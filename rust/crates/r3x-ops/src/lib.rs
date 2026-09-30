//! Operations (plan §5 `r3x-ops`): service health folded into `state.services`, runtime log
//! lines fanned out to gateway clients, the debug/trace controls (`telemetry` commands), and
//! per-turn latency legs ([`latency`]).

pub mod latency;

use std::sync::Arc;

use r3x_bus::{Bus, Received};
use r3x_contracts::{
    Ack, Command, Domain, Event, LogLine, MessageClass, OpsEvent, ServiceHealth, ServiceStatus,
    ServicesState, Source, TelemetryCommand,
};
use tokio::sync::broadcast;
use tokio::task::JoinHandle;
use tracing_subscriber::layer::SubscriberExt;
use tracing_subscriber::util::SubscriberInitExt;
use tracing_subscriber::{reload, EnvFilter, Layer};

/// Report a service's health. Folded into `state.services` by [`spawn_health`].
pub fn report(bus: &Bus, service: &str, status: ServiceStatus, detail: Option<String>) {
    bus.publish(
        Source::System,
        None,
        Event::Ops(OpsEvent::ServiceStatus { service: service.into(), status, detail }),
    );
}

/// Fold every `ops.service_status` event into `state.services`.
pub fn spawn_health(bus: &Bus) -> JoinHandle<()> {
    let mut rx = bus.subscribe(Domain::Ops);
    let bus = bus.clone();
    tokio::spawn(async move {
        while let Some(msg) = rx.recv().await {
            // A lag loses nothing that matters: the next report per service carries its state.
            let Received::Message(env) = msg else { continue };
            if let r3x_contracts::Body::Event(Event::Ops(OpsEvent::ServiceStatus { service, status, detail })) =
                &env.body
            {
                let health = ServiceHealth { status: *status, detail: detail.clone() };
                bus.update(env.source, |s: &mut ServicesState| {
                    s.services.insert(service.clone(), health);
                });
            }
        }
    })
}

/// Changes the runtime's log filter (`debug`, `info`, `r3x_gateway=trace`, ...).
pub type LevelControl = Arc<dyn Fn(&str) -> Result<(), String> + Send + Sync>;

/// Runtime log lines for gateway clients.
#[derive(Clone)]
pub struct LogHub {
    tx: broadcast::Sender<LogLine>,
}

impl LogHub {
    pub fn subscribe(&self) -> broadcast::Receiver<LogLine> {
        self.tx.subscribe()
    }

    pub fn sender(&self) -> broadcast::Sender<LogLine> {
        self.tx.clone()
    }
}

/// Install the global subscriber: stderr + a [`LogHub`], both behind one reloadable filter.
pub fn init_tracing(default_filter: &str) -> (LogHub, LevelControl) {
    let filter = EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new(default_filter));
    let (filter, handle) = reload::Layer::new(filter);
    let hub = LogHub { tx: broadcast::channel(512).0 };
    tracing_subscriber::registry()
        .with(filter)
        .with(tracing_subscriber::fmt::layer().with_writer(std::io::stderr))
        .with(HubLayer { tx: hub.tx.clone() })
        .init();
    let control: LevelControl = Arc::new(move |spec: &str| {
        let f = EnvFilter::try_new(spec).map_err(|e| e.to_string())?;
        handle.reload(f).map_err(|e| e.to_string())
    });
    (hub, control)
}

struct HubLayer {
    tx: broadcast::Sender<LogLine>,
}

impl<S: tracing::Subscriber> Layer<S> for HubLayer {
    fn on_event(&self, event: &tracing::Event<'_>, _: tracing_subscriber::layer::Context<'_, S>) {
        if self.tx.receiver_count() == 0 {
            return;
        }
        let mut v = MessageVisitor(String::new());
        event.record(&mut v);
        let meta = event.metadata();
        let _ = self.tx.send(LogLine {
            level: meta.level().to_string(),
            target: meta.target().to_string(),
            message: v.0,
        });
    }
}

struct MessageVisitor(String);

impl tracing::field::Visit for MessageVisitor {
    fn record_debug(&mut self, field: &tracing::field::Field, value: &dyn std::fmt::Debug) {
        use std::fmt::Write;
        if !self.0.is_empty() {
            self.0.push(' ');
        }
        if field.name() == "message" {
            let _ = write!(self.0, "{value:?}");
        } else {
            let _ = write!(self.0, "{}={value:?}", field.name());
        }
    }
}

/// Own the `telemetry` class: log level. (`frames` is per connection; the gateway answers it.)
pub fn spawn_telemetry(bus: &Bus, level: LevelControl) -> Option<JoinHandle<()>> {
    let mut rx = bus.take_commands(MessageClass::Telemetry)?;
    Some(tokio::spawn(async move {
        while let Some(req) = rx.recv().await {
            let ack = match &req.command {
                Command::Telemetry(TelemetryCommand::SetLogLevel { level: spec }) => {
                    let spec = spec.trim().to_ascii_lowercase();
                    match level(&spec) {
                        Ok(()) => {
                            tracing::info!(filter = %spec, "log filter changed");
                            Ack::Accepted
                        }
                        Err(e) => Ack::rejected(format!("bad log filter {spec:?}: {e}")),
                    }
                }
                Command::Telemetry(TelemetryCommand::Frames { .. }) => Ack::Accepted,
                other => Ack::rejected(format!("ops does not handle {:?}", other.class())),
            };
            req.ack(ack);
        }
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn health_reports_land_in_state() {
        let bus = Bus::default();
        let mut watch = bus.watch::<ServicesState>();
        let _h = spawn_health(&bus);
        tokio::task::yield_now().await;
        report(&bus, "gateway", ServiceStatus::Running, None);
        report(&bus, "bridge", ServiceStatus::Degraded, Some("tap down".into()));
        watch.wait_for(|s| s.services.len() == 2).await.unwrap();
        let s = bus.get::<ServicesState>();
        assert_eq!(s.services["bridge"].status, ServiceStatus::Degraded);
        assert_eq!(s.services["gateway"].status, ServiceStatus::Running);
    }

    #[tokio::test]
    async fn log_level_command() {
        let bus = Bus::default();
        let seen = Arc::new(std::sync::Mutex::new(Vec::new()));
        let s2 = seen.clone();
        let _t = spawn_telemetry(
            &bus,
            Arc::new(move |f: &str| {
                if f == "nonsense[" {
                    return Err("parse".into());
                }
                s2.lock().unwrap().push(f.to_string());
                Ok(())
            }),
        );
        let set = |l: &str| Command::Telemetry(TelemetryCommand::SetLogLevel { level: l.into() });
        assert!(bus.command(Source::Ui, None, set("DEBUG")).await.is_accepted());
        assert!(!bus.command(Source::Ui, None, set("nonsense[")).await.is_accepted());
        assert_eq!(*seen.lock().unwrap(), vec!["debug".to_string()]);
    }
}
