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
    GazeSource, OperatingMode, PerfState, Rig, VisionEvent, RobotProfile, RunInfo, RunKind, Source, StageCommand, StageState, HOME_TOLERANCE,
};
use r3x_drivers::DriverSet;
use r3x_performer_core::behavior::AliveLayers;
use r3x_performer_core::leds::host::SystemMode;
use r3x_performer_core::performer::{Command as PCmd, Enables, Out, PerformerConfig};
use r3x_performer_core::show::catalog::Catalog;
use r3x_performer_core::show::puppeteer::PadState;
use r3x_performer_core::show::player::{EndReason as PEnd, RunInfo as PRun, RunLayer, StopSel};
use r3x_performer_core::show::types::{Action, Kind, Source as PSource};
use r3x_performer_core::Performer;
use tokio::task::JoinHandle;

mod calibrate;

pub const TICK_HZ: f64 = 50.0;
const RELOAD_EVERY: Duration = Duration::from_secs(1);

#[derive(Clone)]
pub struct PerformerHostConfig {
    pub profile: Arc<RobotProfile>,
    pub show_dir: PathBuf,
    /// Hardware drivers (virtual frames always). `None` = frames only (tests).
    pub drivers: Option<DriverOptions>,
    /// A catalogue shared read-only between performers (the public server's sessions): used
    /// as is, never reloaded. `None` = load `show_dir` and hot-reload it.
    pub catalog: Option<Arc<Catalog>>,
    /// The gamepad (`crate::pad`): the latest standard-layout state, `None` while no pad is
    /// attached. `None` here = no pad input at all.
    pub pad: Option<PadFeed>,
}

/// Latest pad input, `None` while no pad is attached.
pub type PadFeed = tokio::sync::watch::Receiver<Option<PadInput>>;

/// One pad snapshot as the operator layer (`crate::pad`) split it.
#[derive(Clone, Debug, PartialEq)]
pub struct PadInput {
    /// What the puppeteer reads (sticks, R2, the d-pad when free).
    pub puppet: PadState,
    /// Every button as pressed, for the overlay.
    pub raw: PadState,
    pub controls: r3x_contracts::PadControls,
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
pub(crate) fn show_files(dir: &Path) -> Vec<(String, String, SystemTime)> {
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

/// A viewport look target older than this lapses to straight ahead.
const LOOK_STALE_S: f64 = 1.0;

struct Host {
    bus: Bus,
    profile: Arc<RobotProfile>,
    p: Performer,
    drivers: Option<DriverSet>,
    stage: StageState,
    show_dir: PathBuf,
    /// Latest viewport target and when it arrived (bus seconds).
    viewport: Option<((f64, f64), f64)>,
    /// Latest face from vision.
    face: Option<(f64, f64)>,
    /// What the performer was last told to look at.
    look: Option<(f64, f64)>,
    pad: Option<PadFeed>,
    pad_input: Option<PadInput>,
    /// A freeze the pad toggled, sent to the stage and not yet reflected in its state.
    freeze_sent: Option<bool>,
    /// The rig the loaded profile belongs to, and a switch in progress (target, give-up time).
    rig: Rig,
    rig_pending: Option<(Rig, f64)>,
    driver_opts: Option<DriverOptions>,
}

impl Host {
    fn now(&self) -> f64 {
        self.bus.clock().t_mono()
    }

    fn apply_stage(&mut self, s: StageState, first: bool) {
        if s.rig != self.rig && self.rig_pending.map(|(r, _)| r) != Some(s.rig) {
            self.begin_rig(s.rig);
        }
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

    /// A rig switch: stop every run and Home; `swap_rig` reloads once home (or after 4 s).
    fn begin_rig(&mut self, rig: Rig) {
        tracing::info!(from = ?self.rig, to = ?rig, "rig switch: stopping runs and homing");
        self.p.command(PCmd::Stop(StopSel::all()));
        self.p.command(PCmd::Home { joints: vec![] });
        self.rig_pending = Some((rig, self.now() + 4.0));
    }

    /// Reload the performer (and hardware drivers) with the pending rig's profile.
    fn swap_rig(&mut self) {
        let Some((rig, give_up)) = self.rig_pending else { return };
        if !(self.p.at_home(HOME_TOLERANCE) || self.p.frozen() || self.now() > give_up) {
            return;
        }
        self.rig_pending = None;
        let path = crate::rig_profile_path(rig);
        let profile = match RobotProfile::load(&path) {
            Ok(p) => Arc::new(p),
            Err(e) => {
                tracing::error!(path = %path.display(), "rig switch failed, keeping {:?}: {e}", self.rig);
                return;
            }
        };
        let cfg = PerformerConfig { mouth_hz: Some(profile.audio.mouth_hz), ..Default::default() };
        let p = match Performer::new(self.p.catalog.clone(), &profile, cfg) {
            Ok(p) => p,
            Err(e) => return tracing::error!("rig switch failed, keeping {:?}: {e}", self.rig),
        };
        self.p = p;
        if let Some(o) = self.driver_opts {
            self.drivers = None; // release the ports before reopening them
            self.drivers = Some(DriverSet::from_profile(&profile, &self.bus, self.p.actuation.us_per_unit, o.leds));
        }
        self.profile = profile;
        self.rig = rig;
        self.look = None;
        let engagement = self.bus.get::<EngagementState>().engagement;
        self.p.command(PCmd::Mode { mode: system_mode(engagement) });
        let s = self.stage.clone();
        self.apply_stage(s, true);
        tracing::info!(?rig, path = %path.display(), "rig switched: performer reloaded");
    }

    /// The gaze target the stage selects (Show only; Bench and Studio hold still), clamped to
    /// the head's animation range so a target behind him parks at the limit.
    fn gaze_target(&self) -> Option<(f64, f64)> {
        if self.stage.mode != OperatingMode::Show {
            return None;
        }
        let raw = match self.stage.gaze {
            GazeSource::Viewport => self.viewport.filter(|(_, at)| self.now() - at < LOOK_STALE_S).map(|(pt, _)| pt),
            GazeSource::Vision => self.face,
            GazeSource::Audio | GazeSource::Off => None,
        }?;
        let clamp = |j: &str, v: f64| self.profile.joint(j).map_or(v, |j| j.animation.clamp(v));
        Some((clamp("head_pan", raw.0), clamp("head_tilt", raw.1)))
    }

    /// Hand the performer the pad's latest state (or its loss) before a tick.
    fn update_pad(&mut self) {
        let Some(rx) = &mut self.pad else { return };
        if !rx.has_changed().unwrap_or(false) {
            return;
        }
        let input = rx.borrow_and_update().clone();
        let c = match &input {
            Some(i) => PCmd::Pad(i.puppet.clone()),
            None => PCmd::PadLost,
        };
        self.p.command(c);
        self.pad_input = input;
    }

    /// The overlay sees every button as pressed and the operator layer's state, not just the
    /// puppeteer's share.
    fn patch_pad(&self, frames: &mut r3x_performer_core::Frames) {
        if let (Some(pf), Some(i)) = (frames.pad.as_mut(), &self.pad_input) {
            pf.axes = i.raw.axes.clone();
            pf.buttons = i.raw.buttons.iter().map(|(down, v)| if *down { v.max(1.0 / 255.0) } else { 0.0 }).collect();
            pf.controls = Some(i.controls.clone());
        }
    }

    /// The pad's Start toggles the performer's freeze directly; `state.stage.frozen` is the
    /// one switch, so make it agree (else the next stage change would undo the pad).
    fn sync_freeze(&mut self) {
        let frozen = self.p.frozen();
        if frozen == self.stage.frozen {
            self.freeze_sent = None;
        } else if self.freeze_sent != Some(frozen) {
            self.freeze_sent = Some(frozen);
            let bus = self.bus.clone();
            tokio::spawn(async move {
                bus.command(Source::System, None, Command::Stage(StageCommand::Freeze { on: frozen })).await;
            });
        }
    }

    fn update_look(&mut self) {
        let want = self.gaze_target();
        if want != self.look {
            self.look = want;
            self.p.command(PCmd::Look { pan_tilt: want });
        }
    }

    fn on_event(&mut self, ev: &Event) {
        let c = match ev {
            Event::Vision(VisionEvent::FaceAt { pan, tilt }) => {
                self.face = Some((*pan, *tilt));
                return;
            }
            Event::Vision(VisionEvent::FaceLost) => {
                self.face = None;
                return;
            }
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
        if calibrate::is_calibration(cmd) {
            let ack = calibrate::handle(cmd, source, self.stage.mode, &self.profile, self.drivers.as_ref());
            req.ack(ack);
            return;
        }
        match cmd {
            // One switch: state.stage.frozen (the stage watcher applies it here).
            PerfCommand::Freeze { on } => {
                let (bus, on) = (self.bus.clone(), *on);
                tokio::spawn(async move {
                    req.ack(bus.command(Source::System, None, Command::Stage(StageCommand::Freeze { on })).await)
                });
            }
            // Studio authoring: the panel/CLI only; saving is file I/O, previews are Bench-safe.
            PerfCommand::SaveShow { .. } | PerfCommand::Preview { .. } if !matches!(source, Source::Ui | Source::Cli) => {
                req.ack(Ack::rejected("Studio commands come from the panel or the CLI"));
            }
            PerfCommand::SaveShow { doc, overwrite } => {
                let ack = match crate::studio::save_show(&self.show_dir, &self.profile, doc, *overwrite) {
                    Ok(path) => {
                        tracing::info!(path = %path.display(), "studio: saved");
                        Ack::Accepted
                    }
                    Err(e) => Ack::rejected(e),
                };
                req.ack(ack);
            }
            PerfCommand::Look { pan, tilt, owner } => {
                // The retained state, not our copy: a claim acked a moment ago must count.
                let s = self.bus.get::<StageState>();
                let ack = if s.gaze != GazeSource::Viewport {
                    Ack::rejected(format!("the gaze follows {:?}, not the viewport", s.gaze).to_lowercase())
                } else if s.gaze_owner.is_some() && s.gaze_owner != *owner {
                    Ack::rejected("another panel owns the viewport gaze")
                } else if !(pan.is_finite() && tilt.is_finite()) {
                    Ack::rejected("look target must be finite")
                } else {
                    self.viewport = Some(((*pan, *tilt), self.now()));
                    Ack::Accepted
                };
                req.ack(ack);
            }
            PerfCommand::Home { .. } if !matches!(source, Source::Ui | Source::Cli) => {
                req.ack(Ack::rejected("home comes from the panel or the CLI"));
            }
            PerfCommand::Home { .. } if self.stage.mode == OperatingMode::Show => {
                req.ack(Ack::rejected("home runs in Bench or Studio; switch out of Show first"));
            }
            PerfCommand::Home { joints } => match self.p.home(joints) {
                Ok(()) => {
                    tracing::info!(?joints, "home");
                    // The performer switched its idle policy and layers off already; make the
                    // stage state agree, so nothing turns them back on under the hold.
                    // One change: layer-by-layer would pass through states that re-enable some.
                    let still = joints.is_empty();
                    let bus = self.bus.clone();
                    tokio::spawn(async move {
                        if still {
                            bus.command(Source::System, None, Command::Stage(StageCommand::Still)).await;
                        }
                        req.ack(Ack::Accepted);
                    });
                    self.flush(None);
                }
                Err(e) => req.ack(Ack::rejected(e)),
            },
            PerfCommand::Preview { .. } if self.stage.mode == OperatingMode::Show => {
                req.ack(Ack::rejected("preview runs in Studio or Bench, not Show"));
            }
            PerfCommand::Preview { clip, .. } if crate::studio::check_preview(&self.profile, clip).is_err() => {
                let e = crate::studio::check_preview(&self.profile, clip).unwrap_err();
                req.ack(Ack::rejected(format!("not Bench-safe: {e}")));
            }
            _ => {
                let ack = match self.p.perf_command(cmd, source) {
                    Ok(()) => {
                        if let PerfCommand::Emote { slot } = cmd {
                            let cue = self.p.slots().get(usize::from(*slot)).cloned().unwrap_or_default();
                            self.bus.publish(Source::System, None, Event::Perf(PerfEvent::Emote { slot: *slot, cue }));
                        }
                        // As a show's eye action: retained, and mirrored to CantinaOS's face board
                        // when it drives the LEDs (bridge `--leds cantina`).
                        if let PerfCommand::Eyes { pattern, duration } = cmd {
                            let p = pattern.to_ascii_lowercase();
                            self.bus.update(Source::System, |l: &mut LightsState| l.eye_pattern = Some(p.clone()));
                            self.bus.publish(Source::System, None, Event::Perf(PerfEvent::Eyes { pattern: p, duration: *duration }));
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
        // Retained state first, so whoever reacts to `perf.started|ended` sees the run list.
        let runs_changed = outs.iter().any(|o| matches!(o, Out::Started { .. } | Out::Ended { .. }));
        let (homing, at_home) = (self.p.homing(), self.p.at_home(HOME_TOLERANCE));
        let cur = self.bus.get::<PerfState>();
        if runs_changed || cur.frozen != self.p.frozen() || cur.homing != homing || cur.at_home != at_home {
            let runs: Vec<RunInfo> = self.p.player.running().into_iter().map(run_info).collect();
            let frozen = self.p.frozen();
            self.bus.update(Source::System, |s: &mut PerfState| {
                s.runs = runs;
                s.frozen = frozen;
                s.homing = homing;
                s.at_home = at_home;
            });
        }
        for o in &outs {
            let ev = match o {
                Out::Started { run } => {
                    let r = run_info(run);
                    PerfEvent::Started { id: r.id, kind: r.kind, source: r.source, run_id: r.run_id }
                }
                Out::Ended { run, reason } => {
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
    let fixed = cfg.catalog.is_some();
    let catalog = cfg.catalog.clone().unwrap_or_else(|| Arc::new(load_catalog(&cfg.show_dir)));
    tracing::info!(dir = %cfg.show_dir.display(), items = catalog.items.len(), "show catalogue");
    let p = Performer::new(
        catalog,
        &cfg.profile,
        PerformerConfig { mouth_hz: Some(cfg.profile.audio.mouth_hz), ..Default::default() },
    )
    .map_err(|e| anyhow::anyhow!("performer: {e}"))?;
    let drivers = cfg.drivers.map(|o| DriverSet::from_profile(&cfg.profile, bus, p.actuation.us_per_unit, o.leds));
    // The profile this host starts with is the Original rig's unless it is the Physical file.
    let rig = if crate::rig_profile_path(Rig::Physical) == crate::default_profile_path() { Rig::Physical } else { Rig::Original };
    let mut host = Host {
        bus: bus.clone(),
        profile: cfg.profile.clone(),
        p,
        drivers,
        stage: StageState::default(),
        show_dir: cfg.show_dir.clone(),
        viewport: None,
        face: None,
        look: None,
        pad: cfg.pad.clone(),
        pad_input: None,
        freeze_sent: None,
        rig,
        rig_pending: None,
        driver_opts: cfg.drivers,
    };
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
                    host.swap_rig();
                    host.update_look();
                    host.update_pad();
                    let t = host.now();
                    let mut frames = host.p.tick(t);
                    host.patch_pad(&mut frames);
                    host.sync_freeze();
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
                _ = reload.tick(), if !fixed => {
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
