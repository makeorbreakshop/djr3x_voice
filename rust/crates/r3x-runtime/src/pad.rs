//! `R3X_PAD` (on by default in the binary, `--no-pad` / `R3X_PAD=0` off): a DualShock 3 on
//! USB is the operator's controller. The reader (`r3x_pad`) streams raw snapshots; a task
//! runs them through the operator layer (`r3x_pad::controls`, mapping in
//! `profiles/<robot>/pad.json`), hands the continuous part to the performer (puppeteer) and
//! turns the discrete part into ordinary bus commands, as the panel would send them
//! (`Source::Ui`). A lost pad clears the feed (the sticks release) and ends push-to-talk.
//!
//! A panel can take the pad (`StageCommand::ClaimPad`, sent while it is in Build): the layer
//! then stands down as if the pad were unplugged, until the claim is returned.
//!
//! Feedback: pad LEDs = the layer while one is active (LED 1 = L1, 2 = R1, 3 = R2, 4 = something
//! latched), else the state (1 idle, 2 engaged, 3 DJ; all four = frozen). Rumble: a tick on
//! every layer change, two for a latch, one long for an unlatch, a short one for pin/unpin and
//! for a command that landed, a long one for a refusal. Reports `pad`: running while a pad
//! streams.

use std::path::Path;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use r3x_bus::Bus;
use r3x_contracts::{
    Ack, Command, DjState, Engagement, EngagementState, Event, GazeSource, IntentCommand, MusicCommand, OperatingMode,
    PadControls, PadMapping, PerfCommand, PerfEvent, PerfLayer, RobotProfile, ServiceStatus, ServicesState, Source, StageCommand,
    StageState, StopTarget,
};
use r3x_pad::controls::{Action, Controls, Cue, MenuItem};
use r3x_pad::{FeedbackHandle, PadEvent, PadReader};
use r3x_performer_core::show::puppeteer::PadState;
use r3x_performer_core::show::types::Kind;

use crate::performer::{PadFeed, PadInput};

pub fn enabled_from_env() -> bool {
    std::env::var("R3X_PAD").is_ok_and(|v| !matches!(v.as_str(), "" | "0" | "false" | "no" | "off"))
}

/// `pad.json` next to the robot profile.
pub fn load_mapping(profile_path: &Path) -> anyhow::Result<PadMapping> {
    let path = profile_path.with_file_name("pad.json");
    let text = std::fs::read_to_string(&path).map_err(|e| anyhow::anyhow!("{}: {e}", path.display()))?;
    serde_json::from_str(&text).map_err(|e| anyhow::anyhow!("{}: {e}", path.display()))
}

/// How long a button's result stays on the overlay.
const LAST_S: f64 = 2.5;
const ENERGY_STEP: f64 = 0.25;

/// Start the reader and the operator layer. Fail-open: no HID or no mapping is a warning.
pub fn start(bus: &Bus, profile: Arc<RobotProfile>, profile_path: &Path, show_dir: &Path) -> Option<(PadReader, PadFeed)> {
    let map = match load_mapping(profile_path) {
        Ok(m) => m,
        Err(e) => {
            tracing::warn!("pad off: no operator mapping ({e})");
            r3x_ops::report(bus, "pad", ServiceStatus::Degraded, Some(format!("no mapping: {e}")));
            return None;
        }
    };
    let (raw_tx, raw_rx) = tokio::sync::watch::channel::<Option<PadState>>(None);
    let bus2 = bus.clone();
    let mut streaming = false;
    let reader = r3x_pad::spawn(move |ev| match ev {
        PadEvent::State(r) => {
            if !streaming {
                streaming = true;
                r3x_ops::report(&bus2, "pad", ServiceStatus::Running, Some(format!("DualShock 3, battery {:?}", r.battery)));
            }
            raw_tx.send_replace(Some(r.pad_state()));
        }
        PadEvent::Connected { name } => {
            r3x_ops::report(&bus2, "pad", ServiceStatus::Degraded, Some(format!("{name} attached; press PS")));
        }
        PadEvent::Lost { reason } => {
            streaming = false;
            raw_tx.send_replace(None);
            r3x_ops::report(&bus2, "pad", ServiceStatus::Degraded, Some(format!("no pad ({reason})")));
        }
    });
    let reader = match reader {
        Ok(r) => r,
        Err(e) => {
            tracing::warn!("pad off: {e}");
            return None;
        }
    };
    r3x_ops::report(bus, "pad", ServiceStatus::Degraded, Some("no pad; plug in a DualShock 3".into()));
    let feed = spawn_operator(bus, profile, map, show_ids(show_dir), reader.feedback(), raw_rx);
    Some((reader, feed))
}

/// The operator layer on its own: raw snapshots (`None` = no pad) in, the performer's feed out.
/// `shows` = (sequences, cues) for the menu. Separate from the reader so it runs without HID.
pub fn spawn_operator(
    bus: &Bus,
    profile: Arc<RobotProfile>,
    map: PadMapping,
    shows: (Vec<String>, Vec<String>),
    feedback: FeedbackHandle,
    raw: tokio::sync::watch::Receiver<Option<PadState>>,
) -> PadFeed {
    let (feed_tx, feed_rx) = tokio::sync::watch::channel(None);
    let op = Operator {
        bus: bus.clone(),
        profile,
        controls: Controls::new(map),
        feedback,
        last: Arc::new(Mutex::new(None)),
        saved_alive: None,
        energy: 0.0,
        gaze_before: None,
        shows,
        talking: false,
        epoch: Instant::now(),
    };
    tokio::spawn(op.run(raw, feed_tx));
    feed_rx
}

/// Sequences and cues for the menu's "Shows" lists (read once; the menu is for rare use).
fn show_ids(dir: &Path) -> (Vec<String>, Vec<String>) {
    let cat = crate::performer::load_catalog(dir);
    let of = |k: Kind| cat.items.values().filter(|i| i.kind() == k).map(|i| i.id.clone()).collect::<Vec<_>>();
    (of(Kind::Sequence), of(Kind::Cue))
}

struct Operator {
    bus: Bus,
    profile: Arc<RobotProfile>,
    controls: Controls,
    feedback: FeedbackHandle,
    last: Arc<Mutex<Option<(String, Instant)>>>,
    /// The alive layers and autonomy as they were before L3 stilled them.
    saved_alive: Option<(Vec<(String, bool)>, bool)>,
    energy: f64,
    /// The gaze source before R3 turned "look at guests" on.
    gaze_before: Option<GazeSource>,
    shows: (Vec<String>, Vec<String>),
    talking: bool,
    epoch: Instant,
}

impl Operator {
    async fn run(mut self, mut raw: tokio::sync::watch::Receiver<Option<PadState>>, feed: tokio::sync::watch::Sender<Option<PadInput>>) {
        while raw.changed().await.is_ok() {
            let snap = raw.borrow_and_update().clone();
            let claimed = self.stage().pad_owner.is_some();
            let pad = match snap {
                Some(p) if !claimed => p,
                other => {
                    // No pad, or a panel holds it (Build jogs the workbench with it): no button
                    // actions, talk ended. While claimed the raw pad still goes out in the
                    // frames with a centred puppet (the body stays put): Build's jog reads it
                    // from there when the browser cannot see the DS3 itself (USB on macOS).
                    if self.talking {
                        self.talking = false;
                        self.send(Command::Intent(IntentCommand::PttStop), None);
                    }
                    self.controls.reset();
                    feed.send_replace(other.filter(|_| claimed).map(|raw| {
                        let puppet = PadState { axes: vec![0.0; r3x_performer_core::show::puppeteer::op_axis::COUNT], buttons: vec![(false, 0.0); r3x_pad::ds3::std_btn::COUNT] };
                        PadInput { puppet, raw, controls: Default::default() }
                    }));
                    continue;
                }
            };
            let menu = self.menu();
            let now = self.epoch.elapsed().as_secs_f64();
            let mut step = self.controls.step(now, &pad, &menu);
            for a in std::mem::take(&mut step.actions) {
                self.act(a, now);
            }
            for c in std::mem::take(&mut step.cues) {
                self.cue(c);
            }
            step.view.last = self.last.lock().ok().and_then(|g| g.as_ref().filter(|(_, at)| at.elapsed().as_secs_f64() < LAST_S).map(|(s, _)| s.clone()));
            self.leds(&step.view);
            feed.send_replace(Some(PadInput { puppet: step.puppet, raw: pad, controls: step.view }));
        }
    }

    fn stage(&self) -> StageState {
        self.bus.get::<StageState>()
    }

    /// LEDs: the layer while one is active (1 = L1, 2 = R1, 3 = R2, 4 = latched), else all four
    /// = frozen, else 1 idle / 2 engaged / 3 DJ.
    fn leds(&self, view: &PadControls) {
        let layer: u8 = [("l1", 0b0001), ("r1", 0b0010), ("r2", 0b0100)]
            .iter()
            .filter(|(m, _)| view.layer.contains(m))
            .fold(0, |acc, (_, bit)| acc | bit);
        let leds = if layer != 0 {
            layer | if view.latched.is_empty() { 0 } else { 0b1000 }
        } else if self.stage().frozen {
            0b1111
        } else if self.bus.get::<DjState>().active {
            0b0100
        } else if matches!(self.bus.get::<EngagementState>().engagement, Engagement::Ambient | Engagement::Interactive) {
            0b0010
        } else {
            0b0001
        };
        self.feedback.set(|f| f.leds = leds);
    }

    /// Send a command as the panel would; `label` = what the overlay says when it lands.
    fn send(&self, cmd: Command, label: Option<String>) {
        let (bus, last, fb) = (self.bus.clone(), self.last.clone(), self.feedback.clone());
        tokio::spawn(async move {
            let ack = bus.command(Source::Ui, None, cmd).await;
            let (text, rumble) = match &ack {
                Ack::Accepted => (label, Duration::from_millis(90)),
                Ack::Rejected { reason } => (Some(format!("refused: {reason}")), Duration::from_millis(400)),
            };
            if let Some(t) = text {
                if let Ok(mut g) = last.lock() {
                    *g = Some((t, Instant::now()));
                }
                buzz(&fb, rumble).await;
            }
        });
    }

    fn note(&self, text: String) {
        if let Ok(mut g) = self.last.lock() {
            *g = Some((text, Instant::now()));
        }
        let fb = self.feedback.clone();
        tokio::spawn(async move { buzz(&fb, Duration::from_millis(90)).await });
    }

    /// Rumble for the layer engine's cues (see the module doc).
    fn cue(&self, c: Cue) {
        let fb = self.feedback.clone();
        let pattern: &'static [u64] = match c {
            Cue::Layer => &[35],
            Cue::Latch(true) => &[60, 80, 60],
            Cue::Latch(false) => &[160],
            Cue::Pin(_) => &[70],
        };
        tokio::spawn(async move {
            for (i, ms) in pattern.iter().enumerate() {
                if i % 2 == 0 {
                    buzz(&fb, Duration::from_millis(*ms)).await;
                } else {
                    tokio::time::sleep(Duration::from_millis(*ms)).await;
                }
            }
        });
    }

    fn act(&mut self, a: Action, now: f64) {
        use Command::{Intent, Perf, Stage};
        match a {
            Action::Talk(on) => {
                self.talking = on;
                let c = if on { IntentCommand::PttStart } else { IntentCommand::PttStop };
                self.send(Intent(c), on.then(|| "listening…".into()));
                if on {
                    // The mic opens after a short hold (a tap does nothing): a buzz says "speak".
                    let fb = self.feedback.clone();
                    tokio::spawn(async move { buzz(&fb, Duration::from_millis(60)).await });
                }
            }
            // An emote is its profile cue played at the button's pressure (a light press, a small one).
            Action::Emote { slot, intensity } => match self.profile.emotes.get(usize::from(slot)).cloned() {
                Some(id) => self.send(Perf(PerfCommand::Play { id: id.clone(), intensity, speed: 1.0, layer: None }), Some(id)),
                None => self.send(Perf(PerfCommand::Emote { slot }), Some(format!("emote {}", slot + 1))),
            },
            Action::Play { id, intensity } => self.send(Perf(PerfCommand::Play { id: id.clone(), intensity, speed: 1.0, layer: None }), Some(id)),
            Action::Sfx(id) => {
                // The sfx player follows `perf.sfx` events (a show's sfx cue is the same event).
                self.bus.publish(Source::Ui, None, Event::Perf(PerfEvent::Sfx { id: id.clone() }));
                self.note(format!("♪ {id}"));
            }
            Action::Cancel => {
                self.send(Perf(PerfCommand::Stop(StopTarget::Layer { layer: PerfLayer::Gesture })), Some("cancelled".into()));
                self.send(Perf(PerfCommand::Stop(StopTarget::Layer { layer: PerfLayer::Show })), None);
            }
            Action::Reset => {
                self.send(Perf(PerfCommand::Stop(StopTarget::Layer { layer: PerfLayer::Gesture })), Some("reset: everything home".into()));
                self.send(Perf(PerfCommand::Stop(StopTarget::Layer { layer: PerfLayer::Show })), None);
            }
            Action::LookToggle => {
                let cur = self.stage().gaze;
                let (source, text) = if cur == GazeSource::Vision {
                    (self.gaze_before.take().unwrap_or(GazeSource::Off), "gaze: free")
                } else {
                    self.gaze_before = Some(cur);
                    (GazeSource::Vision, "looking at guests")
                };
                self.send(Stage(StageCommand::SetGaze { source, owner: None }), Some(text.into()));
            }
            Action::ToggleFreeze => {
                let on = !self.stage().frozen;
                self.send(Stage(StageCommand::Freeze { on }), Some(if on { "frozen" } else { "unfrozen" }.into()));
            }
            Action::ToggleArm => {
                let armed = self.controls.armed;
                for a in &self.profile.actuators {
                    self.send(Stage(StageCommand::SetOutput { output: a.name.clone(), enabled: armed }), None);
                }
                self.note(if armed { "motion ARMED" } else { "motion disarmed" }.into());
            }
            Action::Menu(id) => self.menu_pick(&id, now),
        }
    }

    /// L3: everything alive off (remembering how it was), or back as it was.
    fn toggle_alive(&mut self) {
        let s = self.stage();
        let alive = s.autonomy || s.layers.values().any(|on| *on);
        if alive {
            self.saved_alive = Some((s.layers.iter().map(|(k, v)| (k.clone(), *v)).collect(), s.autonomy));
            self.send(Command::Stage(StageCommand::Still), Some("idle motion off".into()));
        } else {
            let (layers, autonomy) = self
                .saved_alive
                .take()
                .unwrap_or_else(|| (s.layers.keys().map(|k| (k.clone(), true)).collect(), true));
            for (layer, enabled) in layers {
                self.send(Command::Stage(StageCommand::SetLayer { layer, enabled }), None);
            }
            self.send(Command::Stage(StageCommand::SetAutonomy { enabled: autonomy }), Some("idle motion on".into()));
        }
    }

    fn menu(&self) -> Vec<MenuItem> {
        let s = self.stage();
        let on = |b: bool| if b { "on" } else { "off" };
        let dj = self.bus.get::<DjState>().active;
        let eng = self.bus.get::<EngagementState>().engagement;
        let mark = |label: &str, cur: bool| if cur { format!("• {label}") } else { label.to_string() };
        let mut layers: Vec<MenuItem> =
            s.layers.iter().map(|(k, v)| MenuItem::leaf(format!("{}: {}", k.replace('_', " "), on(*v)), format!("layer.{k}"))).collect();
        layers.push(MenuItem::leaf(format!("autonomy: {}", on(s.autonomy)), "autonomy"));
        let list = |ids: &[String]| ids.iter().map(|id| MenuItem::leaf(id.replace('_', " "), format!("play.{id}"))).collect::<Vec<_>>();
        let alive = s.autonomy || s.layers.values().any(|on| *on);
        vec![
            MenuItem::leaf(format!("DJ mode: {}", on(dj)), "dj"),
            MenuItem::leaf(format!("Idle motion: {}", on(alive)), "alive"),
            MenuItem::leaf("Arms home", "arms.home"),
            MenuItem::sub("Music", vec![MenuItem::leaf("Play", "music.play"), MenuItem::leaf("Next track", "music.next"), MenuItem::leaf("Stop", "music.stop")]),
            MenuItem::sub("Shows", list(&self.shows.0)),
            MenuItem::sub("Cues", list(&self.shows.1)),
            MenuItem::sub(
                "Engagement",
                vec![
                    MenuItem::leaf(mark("Idle", eng == Engagement::Idle), "eng.idle"),
                    MenuItem::leaf(mark("Ambient", eng == Engagement::Ambient), "eng.ambient"),
                    MenuItem::leaf(mark("Interactive", eng == Engagement::Interactive), "eng.interactive"),
                ],
            ),
            MenuItem::sub("Idle layers", layers),
            MenuItem::sub(
                format!("Energy ({:+.2})", self.energy),
                vec![MenuItem::leaf("More", "energy.up"), MenuItem::leaf("Less", "energy.down"), MenuItem::leaf("Reset", "energy.reset")],
            ),
            MenuItem::sub(
                "Stage mode",
                vec![
                    MenuItem::leaf(mark("Show", s.mode == OperatingMode::Show), "mode.show"),
                    MenuItem::leaf(mark("Bench", s.mode == OperatingMode::Bench), "mode.bench"),
                    MenuItem::leaf(mark("Studio", s.mode == OperatingMode::Studio), "mode.studio"),
                ],
            ),
            MenuItem::leaf("Stop everything", "stop.all"),
        ]
    }

    fn menu_pick(&mut self, id: &str, now: f64) {
        use Command::{Intent, Perf, Stage};
        let s = self.stage();
        let label = |t: &str| Some(t.to_string());
        match id {
            "dj" => {
                let active = !self.bus.get::<DjState>().active;
                self.send(Intent(IntentCommand::Dj { active }), label(if active { "DJ mode on" } else { "DJ mode off" }));
            }
            "music.play" => self.send(Intent(IntentCommand::Music(MusicCommand::Play { query: None })), label("music: play")),
            "music.next" => self.send(Intent(IntentCommand::Music(MusicCommand::Next)), label("music: next")),
            "music.stop" => self.send(Intent(IntentCommand::Music(MusicCommand::Stop)), label("music: stop")),
            "autonomy" => self.send(Stage(StageCommand::SetAutonomy { enabled: !s.autonomy }), label(if s.autonomy { "autonomy off" } else { "autonomy on" })),
            "stop.all" => self.send(Perf(PerfCommand::Stop(StopTarget::All)), label("stopped everything")),
            "alive" => self.toggle_alive(),
            "arms.home" => {
                self.controls.home_arms(now);
                self.note("arms home".into());
            }
            "vision" => {
                let on = self.bus.get::<ServicesState>().services.get("vision").is_some_and(|h| h.status == ServiceStatus::Running);
                let line = if on { "vision off" } else { "vision on" };
                self.send(Intent(IntentCommand::Console { line: line.into() }), Some(line.into()));
            }
            "energy.up" | "energy.down" | "energy.reset" => {
                self.energy = match id {
                    "energy.up" => (self.energy + ENERGY_STEP).min(1.0),
                    "energy.down" => (self.energy - ENERGY_STEP).max(-1.0),
                    _ => 0.0,
                };
                let channels = [("energy".to_string(), self.energy)].into();
                self.send(Perf(PerfCommand::Puppet { channels }), Some(format!("energy {:+.2}", self.energy)));
            }
            _ => {
                if let Some(layer) = id.strip_prefix("layer.") {
                    let enabled = !s.layers.get(layer).copied().unwrap_or(false);
                    let text = format!("{layer} {}", if enabled { "on" } else { "off" });
                    self.send(Stage(StageCommand::SetLayer { layer: layer.into(), enabled }), Some(text));
                } else if let Some(show) = id.strip_prefix("play.") {
                    self.send(Perf(PerfCommand::Play { id: show.into(), intensity: 1.0, speed: 1.0, layer: None }), Some(show.into()));
                } else if let Some(e) = id.strip_prefix("eng.") {
                    let engagement = match e {
                        "idle" => Engagement::Idle,
                        "ambient" => Engagement::Ambient,
                        _ => Engagement::Interactive,
                    };
                    self.send(Stage(StageCommand::SetEngagement { engagement }), Some(format!("engagement: {e}")));
                } else if let Some(m) = id.strip_prefix("mode.") {
                    let mode = match m {
                        "bench" => OperatingMode::Bench,
                        "studio" => OperatingMode::Studio,
                        _ => OperatingMode::Show,
                    };
                    self.send(Stage(StageCommand::SetMode { mode }), Some(format!("mode: {m}")));
                } else {
                    tracing::warn!(id, "pad: unknown menu item");
                }
            }
        }
    }
}

async fn buzz(fb: &FeedbackHandle, d: Duration) {
    fb.set(|f| f.rumble = 180);
    tokio::time::sleep(d).await;
    fb.set(|f| f.rumble = 0);
}
