//! StageManager (plan §4): owns `state.stage` (Show/Bench/Studio, output and alive-layer
//! enables, brain, autonomy, freeze) and `state.engagement` (STARTUP/IDLE/AMBIENT/INTERACTIVE).
//!
//! Mode defaults:
//!
//! | | Show | Bench | Studio |
//! |---|---|---|---|
//! | brain | on | off | off |
//! | alive layers | profile defaults | off | off |
//! | autonomy (idle policy, DJ) | on | off | off |
//! | outputs | all | none (enable one at a time) | none (virtual preview; enabling = "send to robot") |
//!
//! Freeze survives mode changes. While CantinaOS is the brain (bridge mode) engagement belongs
//! to it: requests go to an [`EngagementBackend`] and `state.engagement` follows what CantinaOS
//! reports.

use std::collections::BTreeMap;

use r3x_bus::Bus;
use r3x_contracts::{
    Ack, Command, Engagement, EngagementState, Event, MessageClass, OperatingMode, RobotProfile,
    Source, StageCommand, StageEvent, StageState,
};
use tokio::sync::{mpsc, oneshot};
use tokio::task::JoinHandle;

/// Where engagement requests go when something else owns engagement.
pub type EngagementBackend = mpsc::Sender<(Engagement, oneshot::Sender<Ack>)>;

#[derive(Debug, Clone, Default)]
pub struct StageConfig {
    /// Output names: profile actuators and light groups.
    pub outputs: Vec<String>,
    /// Alive layers and their Show-mode defaults.
    pub alive: BTreeMap<String, bool>,
}

impl StageConfig {
    pub fn from_profile(p: &RobotProfile) -> Self {
        let outputs = p.actuators.iter().map(|a| a.name.clone()).chain(p.lights.iter().map(|l| l.name.clone()));
        Self { outputs: outputs.collect(), alive: p.alive.clone() }
    }

    /// The §4 defaults for `mode`, keeping `frozen`.
    pub fn defaults(&self, mode: OperatingMode, frozen: bool) -> StageState {
        let show = mode == OperatingMode::Show;
        StageState {
            mode,
            outputs: self.outputs.iter().map(|o| (o.clone(), show)).collect(),
            layers: self.alive.iter().map(|(l, on)| (l.clone(), show && *on)).collect(),
            brain: show,
            autonomy: show,
            frozen,
        }
    }
}

/// Take the `stage` command class and seed `state.stage` with Show defaults.
/// Without a backend the manager owns engagement itself and leaves STARTUP for IDLE.
pub fn spawn(bus: &Bus, cfg: StageConfig, backend: Option<EngagementBackend>) -> Option<JoinHandle<()>> {
    let mut rx = bus.take_commands(MessageClass::Stage)?;
    bus.set(Source::System, cfg.defaults(OperatingMode::Show, false));
    if backend.is_none() {
        set_engagement(bus, Engagement::Idle);
    }
    let bus = bus.clone();
    Some(tokio::spawn(async move {
        while let Some(req) = rx.recv().await {
            let Command::Stage(cmd) = &req.command else {
                req.ack(Ack::rejected("not a stage command"));
                continue;
            };
            let cmd = cmd.clone();
            match cmd {
                // Engagement may wait on CantinaOS; don't hold up the other stage commands.
                StageCommand::SetEngagement { engagement } => {
                    let (bus, backend) = (bus.clone(), backend.clone());
                    tokio::spawn(async move { req.ack(engage(&bus, backend, engagement).await) });
                }
                other => {
                    let ack = apply(&bus, &cfg, other);
                    req.ack(ack);
                }
            }
        }
    }))
}

fn apply(bus: &Bus, cfg: &StageConfig, cmd: StageCommand) -> Ack {
    let cur = bus.get::<StageState>();
    let mut next = cur.clone();
    match cmd {
        StageCommand::SetMode { mode } => {
            if mode != cur.mode {
                next = cfg.defaults(mode, cur.frozen);
            }
        }
        StageCommand::SetOutput { output, enabled } => match next.outputs.get_mut(&output) {
            Some(v) => *v = enabled,
            None => return Ack::rejected(format!("unknown output {output:?}")),
        },
        StageCommand::SetLayer { layer, enabled } => match next.layers.get_mut(&layer) {
            Some(v) => *v = enabled,
            None => return Ack::rejected(format!("unknown alive layer {layer:?}")),
        },
        StageCommand::SetBrain { enabled } => next.brain = enabled,
        StageCommand::SetAutonomy { enabled } => next.autonomy = enabled,
        StageCommand::Freeze { on } => next.frozen = on,
        StageCommand::SetEngagement { .. } => unreachable!("handled by the caller"),
    }
    if next.mode != cur.mode {
        tracing::info!(from = ?cur.mode, to = ?next.mode, "operating mode");
    }
    let (from, to) = (cur.mode, next.mode);
    bus.set(Source::System, next);
    if from != to {
        bus.publish(Source::System, None, Event::Stage(StageEvent::ModeChanged { from, to }));
    }
    Ack::Accepted
}

async fn engage(bus: &Bus, backend: Option<EngagementBackend>, to: Engagement) -> Ack {
    if to == Engagement::Startup {
        return Ack::rejected("STARTUP is not requestable");
    }
    if to == Engagement::Interactive && !bus.get::<StageState>().brain {
        return Ack::rejected("the brain is off in this mode; turn it on first");
    }
    match backend {
        None => {
            set_engagement(bus, to);
            Ack::Accepted
        }
        Some(tx) => {
            let (reply, rx) = oneshot::channel();
            if tx.send((to, reply)).await.is_err() {
                return Ack::rejected("engagement backend is gone");
            }
            rx.await.unwrap_or_else(|_| Ack::rejected("engagement backend dropped the request"))
        }
    }
}

/// Set `state.engagement` and announce the change. Also used by the bridge.
pub fn set_engagement(bus: &Bus, to: Engagement) {
    let from = bus.get::<EngagementState>().engagement;
    if bus.set(Source::System, EngagementState { engagement: to }) {
        bus.publish(Source::System, None, Event::Stage(StageEvent::EngagementChanged { from, to }));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn cfg() -> StageConfig {
        StageConfig {
            outputs: vec!["neck".into(), "eyes".into()],
            alive: [("breathing".to_string(), true), ("saccades".to_string(), false)].into(),
        }
    }

    fn stage(c: StageCommand) -> Command {
        Command::Stage(c)
    }

    #[tokio::test]
    async fn mode_defaults_and_switches() {
        let bus = Bus::default();
        spawn(&bus, cfg(), None).unwrap();
        let s = bus.get::<StageState>();
        assert!(s.brain && s.autonomy && s.outputs.values().all(|v| *v));
        assert_eq!(s.layers, [("breathing".into(), true), ("saccades".into(), false)].into());
        assert_eq!(bus.get::<EngagementState>().engagement, Engagement::Idle);

        let freeze = stage(StageCommand::Freeze { on: true });
        assert!(bus.command(Source::Ui, None, freeze).await.is_accepted());
        let bench = stage(StageCommand::SetMode { mode: OperatingMode::Bench });
        assert!(bus.command(Source::Ui, None, bench).await.is_accepted());
        let s = bus.get::<StageState>();
        assert_eq!(s.mode, OperatingMode::Bench);
        assert!(!s.brain && !s.autonomy && s.frozen, "freeze survives the mode change");
        assert!(s.outputs.values().chain(s.layers.values()).all(|v| !*v));

        let one = stage(StageCommand::SetOutput { output: "neck".into(), enabled: true });
        assert!(bus.command(Source::Ui, None, one).await.is_accepted());
        assert!(bus.get::<StageState>().outputs["neck"]);
        let bogus = stage(StageCommand::SetOutput { output: "tail".into(), enabled: true });
        assert!(!bus.command(Source::Ui, None, bogus).await.is_accepted());

        let talk = stage(StageCommand::SetEngagement { engagement: Engagement::Interactive });
        assert!(!bus.command(Source::Ui, None, talk.clone()).await.is_accepted(), "brain off in Bench");
        assert!(bus.command(Source::Ui, None, stage(StageCommand::SetBrain { enabled: true })).await.is_accepted());
        assert!(bus.command(Source::Ui, None, talk).await.is_accepted());
        assert_eq!(bus.get::<EngagementState>().engagement, Engagement::Interactive);
    }

    #[tokio::test]
    async fn engagement_goes_to_the_backend() {
        let bus = Bus::default();
        let (tx, mut rx) = mpsc::channel(4);
        spawn(&bus, cfg(), Some(tx)).unwrap();
        assert_eq!(bus.get::<EngagementState>().engagement, Engagement::Startup, "backend owns it");
        tokio::spawn(async move {
            let (e, reply): (Engagement, oneshot::Sender<Ack>) = rx.recv().await.unwrap();
            let _ = reply.send(if e == Engagement::Ambient { Ack::Accepted } else { Ack::rejected("no") });
        });
        let amb = stage(StageCommand::SetEngagement { engagement: Engagement::Ambient });
        assert!(bus.command(Source::Cli, None, amb).await.is_accepted());
    }
}
