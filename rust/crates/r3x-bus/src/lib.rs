//! Typed in-process bus (plan D2).
//!
//! - Events: one bounded `broadcast` per [`Domain`]. A lagging receiver gets
//!   [`Received::Lagged`] carrying a fresh [`RetainedState`] snapshot to resync from.
//! - Retained state: one `watch` per state domain ([`StateDomain`]); late joiners see the latest.
//! - Commands: one `mpsc` per [`MessageClass`] with a `oneshot` [`Ack`].
//! - Frames: one small `broadcast` of 50 Hz body snapshots (sim, virtual driver). Unstamped and
//!   kept off the tap and the session log; a lagging receiver just skips to the newest.
//! - Every message is stamped here with `seq`, `t_mono`, `t_wall`, `source`, and optionally
//!   written to a JSONL session log ([`Bus::attach_session_log`]).

mod log;
mod state;

use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex, OnceLock, Weak};
use std::time::{Instant, SystemTime, UNIX_EPOCH};

use r3x_contracts::{
    Ack, Body, Command, Domain, Envelope, Event, Frames, MessageClass, RetainedState, Source,
    PROTOCOL_VERSION,
};
use tokio::sync::{broadcast, mpsc, oneshot, watch};

pub use log::LogRecord;
pub use state::StateDomain;
use state::States;

#[derive(Debug, Clone)]
pub struct BusConfig {
    /// Per-domain event buffer. A receiver further behind than this lags.
    pub event_capacity: usize,
    pub command_capacity: usize,
    /// Session log queue; records beyond it are dropped (and counted), never block the bus.
    pub log_capacity: usize,
}

impl Default for BusConfig {
    fn default() -> Self {
        Self { event_capacity: 256, command_capacity: 64, log_capacity: 8192 }
    }
}

/// Monotonic + wall clock shared by everything the bus stamps.
#[derive(Debug, Clone, Copy)]
pub struct Clock {
    origin: Instant,
}

impl Clock {
    fn new() -> Self {
        Self { origin: Instant::now() }
    }

    /// Seconds since the bus was created.
    pub fn t_mono(&self) -> f64 {
        self.origin.elapsed().as_secs_f64()
    }

    /// Unix seconds.
    pub fn t_wall(&self) -> f64 {
        SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)
    }
}

/// Cheap to clone; all clones share one bus.
#[derive(Clone)]
pub struct Bus {
    inner: Arc<Inner>,
}

struct Inner {
    seq: AtomicU64,
    clock: Clock,
    events: HashMap<Domain, broadcast::Sender<Arc<Envelope>>>,
    /// Every stamped envelope (events, state, commands, acks), for the gateway.
    tap: broadcast::Sender<Arc<Envelope>>,
    frames: broadcast::Sender<Arc<Frames>>,
    states: States,
    /// Present only once a handler has taken the class.
    commands: Mutex<HashMap<MessageClass, mpsc::Sender<CommandRequest>>>,
    command_capacity: usize,
    log: OnceLock<log::LogSender>,
    log_capacity: usize,
}

impl Default for Bus {
    fn default() -> Self {
        Self::new(BusConfig::default())
    }
}

impl Bus {
    pub fn new(cfg: BusConfig) -> Self {
        let events = Domain::ALL.iter().map(|d| (*d, broadcast::channel(cfg.event_capacity).0)).collect();
        Self {
            inner: Arc::new(Inner {
                seq: AtomicU64::new(0),
                clock: Clock::new(),
                events,
                tap: broadcast::channel(cfg.event_capacity * 4).0,
                frames: broadcast::channel(8).0,
                states: States::default(),
                commands: Mutex::default(),
                command_capacity: cfg.command_capacity,
                log: OnceLock::new(),
                log_capacity: cfg.log_capacity,
            }),
        }
    }

    pub fn clock(&self) -> Clock {
        self.inner.clock
    }

    /// Stamp a body into an envelope. `seq` is strictly increasing across the whole bus.
    pub fn stamp(&self, source: Source, conversation_id: Option<String>, body: Body) -> Envelope {
        let c = self.inner.clock;
        Envelope {
            v: PROTOCOL_VERSION,
            seq: self.inner.seq.fetch_add(1, Ordering::Relaxed) + 1,
            t_mono: c.t_mono(),
            t_wall: c.t_wall(),
            source,
            id: None,
            re: None,
            conversation_id,
            body,
        }
    }

    /// Stamp, record and fan out an already-built envelope.
    fn dispatch(&self, env: Envelope, topic: String) -> Arc<Envelope> {
        let env = Arc::new(env);
        if let Some(log) = self.inner.log.get() {
            log.record(&env, topic);
        }
        let _ = self.inner.tap.send(env.clone());
        env
    }

    // ---- events ----

    pub fn publish(&self, source: Source, conversation_id: Option<String>, event: Event) -> Arc<Envelope> {
        let domain = event.domain();
        let topic = event.topic();
        let env = self.dispatch(self.stamp(source, conversation_id, Body::Event(event)), topic);
        // No subscribers is fine.
        let _ = self.inner.events[&domain].send(env.clone());
        env
    }

    pub fn subscribe(&self, domain: Domain) -> EventReceiver {
        EventReceiver { rx: self.inner.events[&domain].subscribe(), bus: Arc::downgrade(&self.inner), what: domain.as_str() }
    }

    /// Every stamped envelope on the bus (events, state updates, commands, acks).
    pub fn subscribe_all(&self) -> EventReceiver {
        EventReceiver { rx: self.inner.tap.subscribe(), bus: Arc::downgrade(&self.inner), what: "all" }
    }

    // ---- frames ----

    pub fn publish_frames(&self, frames: Frames) {
        let _ = self.inner.frames.send(Arc::new(frames));
    }

    /// `Lagged` on this receiver is harmless: skip to the newest frame.
    pub fn subscribe_frames(&self) -> broadcast::Receiver<Arc<Frames>> {
        self.inner.frames.subscribe()
    }

    // ---- retained state ----

    pub fn watch<T: StateDomain>(&self) -> watch::Receiver<T> {
        T::cell(&self.inner.states).subscribe()
    }

    pub fn get<T: StateDomain>(&self) -> T {
        T::cell(&self.inner.states).borrow().clone()
    }

    pub fn snapshot(&self) -> RetainedState {
        self.inner.states.snapshot()
    }

    /// Apply `f`; if the value changed, notify watchers and stamp a `state` message.
    /// Returns whether it changed.
    pub fn update<T: StateDomain>(&self, source: Source, f: impl FnOnce(&mut T)) -> bool {
        let mut new = None;
        T::cell(&self.inner.states).send_if_modified(|cur| {
            let before = cur.clone();
            f(cur);
            let changed = *cur != before;
            if changed {
                new = Some(cur.clone());
            }
            changed
        });
        match new {
            Some(v) => {
                let topic = format!("state.{}", T::NAME);
                self.dispatch(self.stamp(source, None, Body::State(v.into_update())), topic);
                true
            }
            None => false,
        }
    }

    pub fn set<T: StateDomain>(&self, source: Source, value: T) -> bool {
        self.update(source, |s: &mut T| *s = value)
    }

    // ---- commands ----

    /// Become the handler for a message class. One live owner per class: `None` while another
    /// receiver is alive; a dropped receiver frees the class.
    pub fn take_commands(&self, class: MessageClass) -> Option<CommandReceiver> {
        let mut commands = self.inner.commands.lock().unwrap();
        if commands.get(&class).is_some_and(|tx| !tx.is_closed()) {
            return None;
        }
        let (tx, rx) = mpsc::channel(self.inner.command_capacity);
        commands.insert(class, tx);
        Some(CommandReceiver { rx })
    }

    /// Send a command and wait for its ack. Never hangs on a missing or dead handler.
    pub async fn command(&self, source: Source, id: Option<String>, cmd: Command) -> Ack {
        let class = cmd.class();
        let mut env = self.stamp(source, None, Body::Command(cmd.clone()));
        env.id = id;
        let topic = command_topic(&cmd);
        let env = self.dispatch(env, topic);

        let ack = if !source.may_issue(&cmd) {
            Ack::rejected(format!("{source:?} may not issue {class:?} commands"))
        } else {
            let (reply, rx) = oneshot::channel();
            let req = CommandRequest { envelope: env.clone(), command: cmd, reply };
            let handler = self.inner.commands.lock().unwrap().get(&class).cloned();
            match handler {
                Some(tx) if tx.send(req).await.is_ok() => {
                    rx.await.unwrap_or_else(|_| Ack::rejected("handler dropped the command"))
                }
                _ => Ack::rejected(format!("no handler for {class:?}")),
            }
        };

        let mut ack_env = self.stamp(Source::System, env.conversation_id.clone(), Body::Ack(ack.clone()));
        ack_env.re = env.id.clone().or_else(|| Some(env.seq.to_string()));
        self.dispatch(ack_env, "ack".into());
        ack
    }

    // ---- session log ----

    /// Start writing every stamped message to `<dir>/session-<utc>.jsonl`.
    /// The writer finishes (and flushes) once every `Bus` clone is dropped.
    pub async fn attach_session_log(
        &self,
        dir: impl AsRef<std::path::Path>,
    ) -> std::io::Result<(std::path::PathBuf, tokio::task::JoinHandle<std::io::Result<()>>)> {
        let (sender, path, task) = log::start(dir.as_ref(), self.inner.log_capacity).await?;
        self.inner
            .log
            .set(sender)
            .map_err(|_| std::io::Error::other("session log already attached"))?;
        Ok((path, task))
    }
}

fn command_topic(cmd: &Command) -> String {
    let v = serde_json::to_value(cmd).unwrap_or_default();
    let class = v.get("class").and_then(|c| c.as_str()).unwrap_or("?");
    let ty = v.get("type").and_then(|t| t.as_str()).unwrap_or("?");
    format!("command.{class}.{ty}")
}

/// A command awaiting its ack. Dropping it without [`ack`](Self::ack) rejects it.
#[derive(Debug)]
pub struct CommandRequest {
    pub envelope: Arc<Envelope>,
    pub command: Command,
    reply: oneshot::Sender<Ack>,
}

impl CommandRequest {
    pub fn source(&self) -> Source {
        self.envelope.source
    }

    pub fn ack(self, ack: Ack) {
        let _ = self.reply.send(ack);
    }
}

pub struct CommandReceiver {
    rx: mpsc::Receiver<CommandRequest>,
}

impl CommandReceiver {
    pub async fn recv(&mut self) -> Option<CommandRequest> {
        self.rx.recv().await
    }
}

#[derive(Debug)]
pub enum Received {
    Message(Arc<Envelope>),
    /// This receiver fell `missed` messages behind; resync from `state`.
    Lagged { missed: u64, state: Box<RetainedState> },
}

pub struct EventReceiver {
    rx: broadcast::Receiver<Arc<Envelope>>,
    /// Weak, so a receiver never keeps the bus (or its session log) alive.
    bus: Weak<Inner>,
    what: &'static str,
}

impl EventReceiver {
    /// `None` once the bus is gone.
    pub async fn recv(&mut self) -> Option<Received> {
        match self.rx.recv().await {
            Ok(env) => Some(Received::Message(env)),
            Err(broadcast::error::RecvError::Lagged(missed)) => {
                tracing::warn!(channel = self.what, missed, "bus receiver lagged; resyncing from state");
                let inner = self.bus.upgrade()?;
                Some(Received::Lagged { missed, state: Box::new(inner.states.snapshot()) })
            }
            Err(broadcast::error::RecvError::Closed) => None,
        }
    }
}
