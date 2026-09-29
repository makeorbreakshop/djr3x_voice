//! The native performer (plan D4, Phase 3): the only conductor for motion, LED state,
//! stage lights and sfx triggers.
//!
//! One task owns the [`Performer`]. It takes the `perf` command class (tier checks happen in
//! the player), follows retained state (stage switches, engagement) and bus events
//! (conversation, music, DJ, service health), ticks at 50 Hz on the bus clock, and publishes
//! what the performer did: `perf.*` events and `state.perf`, frames (through the virtual
//! driver), named LED lines and servo goals (to `r3x-drivers`). The show folder is reloaded
//! when a file's mtime changes.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::{Duration, SystemTime};

use r3x_bus::{Bus, CommandRequest, Received};
use r3x_contracts::{
    Ack, Body, Command, ConversationEvent, DjEvent, Domain, EndReason, Engagement, EngagementState,
    Event, LightsState, MessageClass, MusicEvent, OpsEvent, PerfCommand, PerfEvent, PerfLayer,
    PerfState, RobotProfile, RunInfo, RunKind, Source, StageCommand, StageState,
};
use r3x_drivers::DriverSet;
use r3x_performer_core::behavior::AliveLayers;
use r3x_performer_core::leds::host::SystemMode;
use r3x_performer_core::performer::{Command as PCmd, Enables, Out, PerformerConfig};
use r3x_performer_core::show::catalog::Catalog;
use r3x_performer_core::show::player::{EndReason as PEnd, RunInfo as PRun, RunLayer};
use r3x_performer_core::show::types::{Action, Kind, Source as PSource};
use r3x_performer_core::Performer;
use tokio::task::JoinHandle;

pub const TICK_HZ: f64 = 50.0;
const RELOAD_EVERY: Duration = Duration::from_secs(1);

#[derive(Clone)]
pub struct PerformerHostConfig {
    pub profile: Arc<RobotProfile>,
    pub show_dir: PathBuf,
    /// Hardware drivers (virtual frames always). `None` = frames only (tests).
    pub drivers: Option<DriverOptions>,
}

#[derive(Clone, Copy, Debug)]
pub struct DriverOptions {
    /// Drive the face and chest boards. Off while CantinaOS still owns them.
    pub leds: bool,
}

/// `SHOW_DIR`, else the repo's `show/`.
pub fn default_show_dir() -> PathBuf {
    std::env::var_os("SHOW_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(concat!(env!("CARGO_MANIFEST_DIR"), "/../../../show")))
}

/// Show files as `(path relative to the folder's parent, e.g. "show/clips/nod.json", text)`.
fn show_files(dir: &Path) -> Vec<(String, String, SystemTime)> {
    let mut out = Vec::new();
    let root = dir.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_else(|| "show".into());
    let mut push = |rel: String, p: &Path| {
        if let (Ok(text), Ok(meta)) = (std::fs::read_to_string(p), std::fs::metadata(p)) {
            out.push((format!("{root}/{rel}"), text, meta.modified().unwrap_or(SystemTime::UNIX_EPOCH)));
        }
    };
    push("idle.json".into(), &dir.join("idle.json"));
    for sub in ["clips", "cues", "sequences"] {
        let Ok(rd) = std::fs::read_dir(dir.join(sub)) else { continue };
        for e in rd.flatten() {
            let p = e.path();
            if p.extension().is_some_and(|x| x == "json") {
                push(format!("{sub}/{}", e.file_name().to_string_lossy()), &p);
            }
        }
    }
    out.sort_by(|a, b| a.0.cmp(&b.0));
    out
}

/// Cheap change detector: every file's name, size and mtime.
fn fingerprint(dir: &Path) -> Vec<(PathBuf, u64, SystemTime)> {
    let mut v = Vec::new();
    let mut add = |p: PathBuf| {
        if let Ok(m) = std::fs::metadata(&p) {
            v.push((p, m.len(), m.modified().unwrap_or(SystemTime::UNIX_EPOCH)));
        }
    };
    add(dir.join("idle.json"));
    for sub in ["clips", "cues", "sequences"] {
        if let Ok(rd) = std::fs::read_dir(dir.join(sub)) {
            for e in rd.flatten() {
                add(e.path());
            }
        }
    }
    v.sort();
    v
}

/// Load the show folder (a missing or empty folder is an empty catalogue).
pub fn load_catalog(dir: &Path) -> Catalog {
    let files = show_files(dir);
    let cat = Catalog::from_files(files.iter().map(|(p, t, _)| (p.as_str(), t.as_str())));
    for e in &cat.errors {
        tracing::warn!("show: {e}");
    }
    cat
}

fn run_kind(k: Kind) -> RunKind {
    match k {
        Kind::Clip => RunKind::Clip,
        Kind::Cue => RunKind::Cue,
        Kind::Sequence => RunKind::Sequence,
    }
}

fn bus_source(s: PSource) -> Source {
    match s {
        PSource::Jev => Source::Jev,
        PSource::Claude => Source::Claude,
        PSource::Timeline => Source::Timeline,
        PSource::Ui => Source::Ui,
        PSource::Cli => Source::Cli,
        PSource::Idle => Source::Idle,
    }
}

fn perf_layer(l: RunLayer) -> PerfLayer {
    match l {
        RunLayer::Background => PerfLayer::Background,
        RunLayer::Gesture => PerfLayer::Gesture,
        RunLayer::Show => PerfLayer::Show,
    }
}

/// `nod#12` -> 12 (the player's run ids end in a per-player sequence number).
fn run_number(run_id: &str) -> u64 {
    run_id.rsplit('#').next().and_then(|n| n.parse().ok()).unwrap_or(0)
}

fn run_info(r: &PRun) -> RunInfo {
    RunInfo {
        run_id: run_number(&r.run_id),
        id: r.id.clone(),
        kind: run_kind(r.kind),
        layer: perf_layer(r.layer),
        source: bus_source(r.source),
    }
}

fn system_mode(e: Engagement) -> SystemMode {
    match e {
        Engagement::Startup | Engagement::Idle => SystemMode::Idle,
        Engagement::Ambient => SystemMode::Ambient,
        Engagement::Interactive => SystemMode::Interactive,
    }
}

/// Output enables from `state.stage.outputs` (enables gate drivers, never frames).
pub fn enables(profile: &RobotProfile, outputs: &BTreeMap<String, bool>) -> Enables {
    let on = |k: &str| outputs.get(k).copied().unwrap_or(true);
    Enables {
        motion: profile.actuators.iter().any(|a| on(&a.name)),
        face: on("eyes") || on("mouth"),
        chest: on("chest"),
        stage_lights: on("stage"),
        sfx: true,
    }
}

struct Host {
    bus: Bus,
    profile: Arc<RobotProfile>,
    p: Performer,
    drivers: Option<DriverSet>,
    stage: StageState,
}

impl Host {
    fn now(&self) -> f64 {
        self.bus.clock().t_mono()
    }

    fn apply_stage(&mut self, s: StageState, first: bool) {
        if s.frozen != self.p.frozen() {
            self.p.freeze(s.frozen);
        }
        if first || s.layers != self.stage.layers {
            self.p.command(PCmd::Alive(AliveLayers::from_map(&s.layers)));
        }
        if first || s.autonomy != self.stage.autonomy {
            self.p.command(PCmd::Autonomy { on: s.autonomy });
        }
        if first || s.outputs != self.stage.outputs {
            self.p.command(PCmd::Enables(enables(&self.profile, &s.outputs)));
            if let Some(d) = &self.drivers {
                d.apply_outputs(&s.outputs);
            }
        }
        self.stage = s;
    }

    fn on_event(&mut self, ev: &Event) {
        let c = match ev {
            Event::Conversation(c) => match c {
                ConversationEvent::ListeningStarted => PCmd::ListeningStarted,
                ConversationEvent::ListeningStopped { .. } => PCmd::ListeningStopped,
                ConversationEvent::ReplyDelta { .. } => PCmd::LlmChunk,
                ConversationEvent::SpeechStarted => PCmd::SpeechStarted { timings: None, tags: vec![] },
                ConversationEvent::SpeechEnded => PCmd::SpeechEnded,
                ConversationEvent::Mouth { level } => PCmd::Amplitude { value: *level },
                _ => return,
            },
            Event::Music(MusicEvent::TrackStarted { track }) => {
                if let Some(bpm) = track.bpm.filter(|b| *b > 0.0) {
                    self.p.command(PCmd::Tempo { bpm });
                }
                PCmd::Music { playing: true }
            }
            Event::Music(MusicEvent::TrackStopped) => PCmd::Music { playing: false },
            Event::Dj(DjEvent::Started) => PCmd::Dj { on: true },
            Event::Dj(DjEvent::Stopped) => PCmd::Dj { on: false },
            Event::Ops(OpsEvent::ServiceStatus { service, status, detail }) => PCmd::ServiceStatus {
                // CantinaOS services arrive as `cantina/<ClassName>`; the chest windows key on the class.
                service: service.strip_prefix("cantina/").unwrap_or(service).to_owned(),
                status: format!("{status:?}").to_lowercase(),
                detail: detail.clone(),
                latched: None,
            },
            _ => return,
        };
        self.p.command(c);
    }

    fn on_command(&mut self, req: CommandRequest) {
        let source = req.source();
        let Command::Perf(cmd) = &req.command else {
            req.ack(Ack::rejected("not a perf command"));
            return;
        };
        match cmd {
            // One switch: state.stage.frozen (the stage watcher applies it here).
            PerfCommand::Freeze { on } => {
                let (bus, on) = (self.bus.clone(), *on);
                tokio::spawn(async move {
                    req.ack(bus.command(Source::System, None, Command::Stage(StageCommand::Freeze { on })).await)
                });
            }
            _ => {
                let ack = match self.p.perf_command(cmd, source) {
                    Ok(()) => {
                        if let PerfCommand::Emote { slot } = cmd {
                            let cue = self.p.slots().get(usize::from(*slot)).cloned().unwrap_or_default();
                            self.bus.publish(Source::System, None, Event::Perf(PerfEvent::Emote { slot: *slot, cue }));
                        }
                        Ack::Accepted
                    }
                    Err(e) => Ack::rejected(e),
                };
                req.ack(ack);
                self.flush(None);
            }
        }
    }

    /// Publish what the performer did since the last call; hand drivers their share.
    fn flush(&mut self, frames: Option<r3x_performer_core::Frames>) {
        let outs = self.p.take_events();
        let mut runs_changed = false;
        for o in &outs {
            let ev = match o {
                Out::Started { run } => {
                    runs_changed = true;
                    let r = run_info(run);
                    PerfEvent::Started { id: r.id, kind: r.kind, source: r.source, run_id: r.run_id }
                }
                Out::Ended { run, reason } => {
                    runs_changed = true;
                    let r = run_info(run);
                    let reason = match reason {
                        PEnd::Done => EndReason::Done,
                        PEnd::Interrupted => EndReason::Interrupted,
                        PEnd::Rejected => EndReason::Rejected,
                    };
                    PerfEvent::Ended { id: r.id, kind: r.kind, source: r.source, run_id: r.run_id, reason }
                }
                Out::Sfx { id } => PerfEvent::Sfx { id: id.clone() },
                Out::Speak { text } => PerfEvent::Speak { text: text.clone() },
                Out::Duck => PerfEvent::Duck { on: true },
                Out::Unduck => PerfEvent::Duck { on: false },
                Out::StageLights { action: Action::Lights { cue, mode, fade, hold, rig } } => {
                    if cue.is_some() {
                        let cue = cue.clone();
                        self.bus.update(Source::System, |l: &mut LightsState| l.stage_cue = cue);
                    }
                    PerfEvent::Lights { cue: cue.clone(), mode: mode.clone(), rig: rig.clone(), fade: *fade, hold: *hold }
                }
                Out::Action { action: Action::Eyes { pattern, duration, .. }, .. } => {
                    let pattern = pattern.clone();
                    self.bus.update(Source::System, |l: &mut LightsState| l.eye_pattern = Some(pattern.clone()));
                    PerfEvent::Eyes { pattern, duration: *duration }
                }
                Out::Action { action: Action::Chest { command, hold }, .. } => {
                    PerfEvent::Chest { command: command.clone(), hold: *hold }
                }
                _ => continue,
            };
            self.bus.publish(Source::System, None, Event::Perf(ev));
        }
        if runs_changed || self.bus.get::<PerfState>().frozen != self.p.frozen() {
            let runs: Vec<RunInfo> = self.p.player.running().into_iter().map(run_info).collect();
            let frozen = self.p.frozen();
            self.bus.update(Source::System, |s: &mut PerfState| {
                s.runs = runs;
                s.frozen = frozen;
            });
        }
        match (&self.drivers, frames) {
            (Some(d), Some(f)) => d.tick(&outs, f),
            // Event-time outputs between ticks (a command's face line, a goal) go out now.
            (Some(d), None) => d.outs(&outs),
            _ => {}
        }
    }
}

/// Start the performer: takes the `perf` command class.
pub fn spawn(bus: &Bus, cfg: PerformerHostConfig) -> anyhow::Result<JoinHandle<()>> {
    let mut commands = bus.take_commands(MessageClass::Perf).ok_or_else(|| anyhow::anyhow!("perf class taken"))?;
    let catalog = Arc::new(load_catalog(&cfg.show_dir));
    tracing::info!(dir = %cfg.show_dir.display(), items = catalog.items.len(), "show catalogue");
    let p = Performer::new(
        catalog,
        &cfg.profile,
        PerformerConfig { mouth_hz: Some(cfg.profile.audio.mouth_hz), ..Default::default() },
    )
    .map_err(|e| anyhow::anyhow!("performer: {e}"))?;
    let drivers = cfg.drivers.map(|o| DriverSet::from_profile(&cfg.profile, bus, p.actuation.us_per_unit, o.leds));
    let mut host = Host { bus: bus.clone(), profile: cfg.profile.clone(), p, drivers, stage: StageState::default() };
    let mut events = bus.subscribe_all();
    let mut stage = bus.watch::<StageState>();
    let mut engagement = bus.watch::<EngagementState>();
    let show_dir = cfg.show_dir.clone();
    Ok(tokio::spawn(async move {
        host.apply_stage(stage.borrow_and_update().clone(), true);
        host.p.command(PCmd::Mode { mode: system_mode(engagement.borrow_and_update().engagement) });
        let mut tick = tokio::time::interval(Duration::from_secs_f64(1.0 / TICK_HZ));
        tick.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
        let mut reload = tokio::time::interval(RELOAD_EVERY);
        let mut print = fingerprint(&show_dir);
        r3x_ops::report(&host.bus, "performer", r3x_contracts::ServiceStatus::Running, None);
        loop {
            tokio::select! {
                _ = tick.tick() => {
                    let t = host.now();
                    let frames = host.p.tick(t);
                    host.flush(Some(frames));
                }
                Some(req) = commands.recv() => host.on_command(req),
                r = stage.changed() => {
                    if r.is_err() { break; }
                    let s = stage.borrow_and_update().clone();
                    host.apply_stage(s, false);
                    host.flush(None);
                }
                r = engagement.changed() => {
                    if r.is_err() { break; }
                    let e = engagement.borrow_and_update().engagement;
                    host.p.command(PCmd::Mode { mode: system_mode(e) });
                }
                m = events.recv() => match m {
                    Some(Received::Message(env)) => {
                        if let Body::Event(ev) = &env.body {
                            if ev.domain() != Domain::Perf {
                                host.on_event(ev);
                            }
                        }
                    }
                    Some(Received::Lagged { missed, .. }) => tracing::warn!(missed, "performer lagged behind the bus"),
                    None => break,
                },
                _ = reload.tick() => {
                    let now = fingerprint(&show_dir);
                    if now != print {
                        print = now;
                        let cat = load_catalog(&show_dir);
                        tracing::info!(items = cat.items.len(), errors = cat.errors.len(), "show folder changed; reloaded");
                        host.p.set_catalog(Arc::new(cat));
                    }
                }
            }
        }
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn run_numbers_and_enables() {
        assert_eq!(run_number("nod#12"), 12);
        assert_eq!(run_number("weird"), 0);
        let p = RobotProfile::load(crate::default_profile_path()).unwrap();
        let mut outputs: BTreeMap<String, bool> = BTreeMap::new();
        outputs.insert("eyes".into(), false);
        outputs.insert("mouth".into(), false);
        let e = enables(&p, &outputs);
        assert!(!e.face && e.chest && e.motion);
        let all_off: BTreeMap<String, bool> = p.actuators.iter().map(|a| (a.name.clone(), false)).collect();
        assert!(!enables(&p, &all_off).motion);
    }
}
