//! The performer: the single conductor for the body (plan D4), wired as the sim's offline
//! `main.ts` wires its parts. Feed it commands and a monotonic clock; it returns frames
//! (joints + light pixels) and queues outgoing events for real drivers.
//!
//! Per tick, in the sim's order:
//! 1. LED hosts tick (named commands go to the board emulators and, when enabled, out);
//!    the face firmware runs to `t`;
//! 2. procedural pose -> body compositor (background, gesture, show, puppet, freeze) ->
//!    actuation (soft-limit clamp, jerk-limited follower, pulse output);
//! 3. the show system: puppeteer, show tags on the word, player, background loop, idle;
//! 4. the stage-light desk follows the state; servo goals go out at event time.

use crate::actuation::pipeline::{profile_joints, Actuation, Frame, JointDynamics};
use crate::behavior::{Activity, AliveLayers, PerformContext, Procedural};
use crate::leds::chest::{ChestFirmware, ChestHost, ChestLightKind, ChestLightSpec};
use crate::leds::firmware::{FirmwareOptions, RexFaceFirmware, Rgb, NUM_EYE_LEDS, NUM_MOUTH_LEDS};
use crate::leds::host::{CantinaHostEmulator, DualHost, SerialDir, SystemMode};
use crate::rng::Rng;
use crate::show::body::{get, BodyCompositor, PlayRequest, Pose};
use crate::show::catalog::Catalog;
use crate::show::idle::{IdleAction, IdleContext, IdleRunner};
use crate::show::player::{
    DispatchCtx, EndReason, PlayerHost, PlayerOptions, RunInfo, RunLayer, ShowPlayer, StopSel,
};
use crate::show::puppeteer::{PadState, PuppetEvent, PuppetMode, Puppeteer};
use crate::show::take::{TakeRecorder, TakeSample};
use crate::show::types::{Action, Clip, Params, ShowItem, Source};
use crate::show::validate::validate_item;
use crate::stagelights::{LightMode, Output, StageLights, GROUPS};
use indexmap::IndexMap;
use r3x_contracts::messages::{PerfCommand, PerfLayer, StopTarget};
use r3x_contracts::RobotProfile;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::sync::Arc;

/// Fallback pace for show tags when the TTS gave no character timings (chars/s).
pub const TAG_CHARS_PER_SEC: f64 = 13.0;

/// The body run a Studio preview occupies (show layer).
pub const PREVIEW_RUN: &str = "studio#preview";

/// A SPEC clip document (unsaved, no file) -> a validated clip.
pub fn parse_clip(doc: &serde_json::Value) -> Result<Arc<Clip>, String> {
    let errs = validate_item(doc, None);
    if !errs.is_empty() {
        return Err(errs.join("; "));
    }
    let item = ShowItem::from_value(doc)?;
    item.clip().cloned().ok_or_else(|| format!("{} is a {}, not a clip", item.id, item.kind().as_str()))
}

/// Output enables gate drivers, not layers: frames are always computed.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct Enables {
    pub motion: bool,
    pub face: bool,
    pub chest: bool,
    pub stage_lights: bool,
    pub sfx: bool,
}

impl Default for Enables {
    fn default() -> Self {
        Enables {
            motion: true,
            face: true,
            chest: true,
            stage_lights: true,
            sfx: true,
        }
    }
}

/// Runtime settings the Robot Profile does not carry.
#[derive(Clone, Debug)]
pub struct PerformerConfig {
    pub seed: u32,
    /// Per-joint mass/inertia for the servo plant (sim only; empty = unloaded).
    pub dynamics: BTreeMap<String, JointDynamics>,
    /// Stage-light rig preset; None = `disneyland_2019`.
    pub light_rig: Option<String>,
    pub player: PlayerOptions,
    /// Smallest target change that sends a new servo goal (joint units).
    pub goal_deadband: f64,
    /// Plan §7b single mouth rate (the profile's `audio.mouth_hz`); None = the legacy
    /// CantinaOS throttles (host.ts parity).
    pub mouth_hz: Option<f64>,
}

impl Default for PerformerConfig {
    fn default() -> Self {
        PerformerConfig {
            seed: 0x5EED,
            dynamics: BTreeMap::new(),
            light_rig: None,
            player: PlayerOptions::default(),
            goal_deadband: 0.25,
            mouth_hz: None,
        }
    }
}

/// The chest light group's layout as the chest firmware's specs (strip order).
fn chest_layout(p: &RobotProfile) -> Vec<ChestLightSpec> {
    let Some(g) = p.lights.iter().find(|g| g.name == "chest") else {
        return vec![];
    };
    g.layout
        .iter()
        .map(|px| ChestLightSpec {
            kind: if px.kind == "window" {
                ChestLightKind::Window
            } else {
                ChestLightKind::Dot
            },
            pos: px.pos,
            normal: px.normal,
            w: px.w,
            h: px.h,
            // The firmware groups the pixels of the logic-panel mesh; all of them are.
            panel: "MS_P_1_Full".into(),
        })
        .collect()
}

/// Commands in, as the bus or the wasm wrapper delivers them.
#[derive(Clone, Debug, PartialEq, Deserialize)]
#[serde(tag = "cmd", rename_all = "snake_case")]
pub enum Command {
    Perform {
        id: String,
        #[serde(default = "default_source")]
        source: Source,
        #[serde(default)]
        params: Params,
        #[serde(default)]
        layer: Option<RunLayer>,
    },
    Stop(StopSel),
    Freeze {
        on: bool,
    },
    Puppet {
        intent: String,
        value: f64,
    },
    PuppetRelease,
    PuppetMode {
        mode: PuppetMode,
    },
    Emote {
        slot: usize,
    },
    Pad(PadState),
    Enables(Enables),
    Alive(AliveLayers),
    Mode {
        mode: SystemMode,
    },
    ListeningStarted,
    ListeningStopped,
    LlmChunk,
    /// Speech became audible. `timings[i]` = seconds after start at which character i is
    /// spoken; `tags` = (char offset, show id) to perform on the word.
    SpeechStarted {
        #[serde(default)]
        timings: Option<Vec<f64>>,
        #[serde(default)]
        tags: Vec<(usize, String)>,
    },
    Amplitude {
        value: f64,
    },
    SpeechEnded,
    Music {
        playing: bool,
    },
    Dj {
        on: bool,
    },
    Tempo {
        bpm: f64,
    },
    Look {
        pan_tilt: Option<(f64, f64)>,
    },
    Background {
        activity: Activity,
        id: Option<String>,
    },
    /// A service health report for the chest's status windows. `latched` defaults to the
    /// fault-latch rule on `detail` (only "Failed to start/initialize" latches).
    ServiceStatus {
        service: String,
        status: String,
        #[serde(default)]
        detail: Option<String>,
        #[serde(default)]
        latched: Option<bool>,
    },
    /// Idle policy on/off (StageManager autonomy; off in Bench and Studio).
    Autonomy {
        on: bool,
    },
    /// Direct jog (Bench, sim sliders): hold `joint` at `value` over everything else;
    /// `None` releases it (it follows the composed pose again through the follower).
    Jog {
        joint: String,
        #[serde(default)]
        value: Option<f64>,
    },
    JogRelease,
    /// Bench/Studio Home (`PerfCommand::Home`): no joints = the whole-body reset.
    Home {
        #[serde(default)]
        joints: Vec<String>,
    },
    /// EYE_COMMAND: a named face pattern for `duration` s (0 = until the state changes).
    Eyes {
        pattern: String,
        #[serde(default)]
        duration: f64,
    },
}

fn default_source() -> Source {
    Source::Ui
}

/// Outgoing events: show lifecycle, department actions for real drivers, servo goals.
#[derive(Clone, Debug, PartialEq, Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum Out {
    Started {
        run: RunInfo,
    },
    Ended {
        run: RunInfo,
        reason: EndReason,
    },
    /// Every department action a run dispatched (for logs and takes).
    Action {
        run_id: String,
        at: f64,
        action: Action,
    },
    Sfx {
        id: String,
    },
    Speak {
        text: String,
    },
    Duck,
    Unduck,
    StageLights {
        action: Action,
    },
    /// A named command for the real face board (`SI`, `M128`, ...).
    FaceLine {
        line: String,
    },
    /// A named command for the real chest board.
    ChestLine {
        line: String,
    },
    /// A goal at event time for the servo controller (D6).
    ServoGoal {
        joint: String,
        target: f64,
        v_max: f64,
        a_max: f64,
        j_max: f64,
        seq: u64,
    },
    /// A raw pulse for one actuator (Bench calibration jog). The controller still moves it
    /// through its follower, clamped to the pulse range and soft limits.
    ServoPulse {
        actuator: String,
        us: f64,
    },
    Freeze {
        on: bool,
    },
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct Frames {
    pub t: f64,
    /// Composed targets (before actuation).
    pub targets: Pose,
    /// What the joints do (after follower, pulse output and servo plant).
    pub joints: IndexMap<String, f64>,
    pub eyes: [Rgb; NUM_EYE_LEDS],
    pub mouth: [Rgb; NUM_MOUTH_LEDS],
    pub chest: Vec<Rgb>,
    /// Linear flux per stage-light group (`stagelights::GROUPS` order).
    pub stage: Output,
    /// The last controller frame (pulses per channel).
    pub servo: Frame,
}

impl Frames {
    /// The gateway's `frames` message (plan section 3b): joints as the rig shows them, and
    /// one colour per pixel per light group (stage: linear flux clamped to 0..1).
    pub fn to_contract(&self) -> r3x_contracts::Frames {
        let u8c = |v: f64| (v.clamp(0.0, 1.0) * 255.0).round() as u8;
        let stage: Vec<Rgb> = self.stage.iter().map(|c| c.map(u8c)).collect();
        debug_assert_eq!(stage.len(), GROUPS.len());
        r3x_contracts::Frames {
            t_mono: self.t,
            joints: self.joints.iter().map(|(k, v)| (k.clone(), *v)).collect(),
            lights: [
                ("eyes", self.eyes.to_vec()),
                ("mouth", self.mouth.to_vec()),
                ("chest", self.chest.clone()),
                ("stage", stage),
            ]
            .into_iter()
            .map(|(k, v)| (k.to_string(), v))
            .collect(),
        }
    }
}

/// Collects what the player does during one call; the performer applies it afterwards.
enum PEv {
    Dispatch(Action, RunInfo, f64),
    Started(RunInfo),
    Ended(RunInfo, EndReason),
}

struct Collect {
    ev: Vec<PEv>,
    speaking: bool,
    bpm: Option<f64>,
}

impl PlayerHost for Collect {
    fn dispatch(&mut self, a: &Action, ctx: DispatchCtx) {
        self.ev
            .push(PEv::Dispatch(a.clone(), ctx.run.clone(), ctx.at));
    }
    fn speech_active(&self) -> bool {
        self.speaking
    }
    fn live_bpm(&self) -> Option<f64> {
        self.bpm
    }
    fn started(&mut self, run: &RunInfo) {
        self.ev.push(PEv::Started(run.clone()));
    }
    fn ended(&mut self, run: &RunInfo, reason: EndReason) {
        self.ev.push(PEv::Ended(run.clone(), reason));
    }
}

struct ShowLight {
    mode: LightMode,
    until: f64,
    natural: LightMode,
}

pub struct Performer {
    pub catalog: Arc<Catalog>,
    pub player: ShowPlayer,
    pub body: BodyCompositor,
    pub idle: IdleRunner,
    pub puppet: Puppeteer,
    pub procedural: Procedural,
    pub actuation: Actuation,
    pub host: DualHost,
    pub chest_fw: ChestFirmware,
    pub lights: StageLights,
    pub take: TakeRecorder,
    pub enables: Enables,
    slots: Vec<String>,
    joints: Vec<String>,
    rng: Rng,
    idle_rng: Rng,
    now: f64,
    ticked: bool,
    speaking: bool,
    music: bool,
    dj: bool,
    bpm: f64,
    amplitude: f64,
    look: Option<(f64, f64)>,
    frozen: bool,
    pad: Option<PadState>,
    show_light: Option<ShowLight>,
    backgrounds: [Option<String>; 2],
    background_run: Option<(String, String)>,
    tags: Vec<(f64, String)>,
    sent_goals: BTreeMap<String, f64>,
    /// Direct jog targets (joint -> value), applied after the compositor.
    jog: BTreeMap<String, f64>,
    /// Every joint's home value (the profile's `home`, else 0).
    home_pose: BTreeMap<String, f64>,
    /// A whole-body Home holds `home_pose` under the jog until something moves the body.
    home_hold: bool,
    goal_seq: u64,
    goal_deadband: f64,
    out: Vec<Out>,
}

fn derive(seed: u32, k: u32) -> Rng {
    Rng::new(seed ^ k.wrapping_mul(0x9E37_79B9))
}

impl Performer {
    /// A performer for the robot a profile describes (joints, actuators, chest layout,
    /// emote slots, alive layers).
    pub fn new(
        catalog: Arc<Catalog>,
        profile: &RobotProfile,
        cfg: PerformerConfig,
    ) -> Result<Performer, String> {
        let joints = profile_joints(profile);
        let actuation = Actuation::from_profile(profile, &cfg.dynamics, derive(cfg.seed, 1))?;
        let fw = RexFaceFirmware::new(FirmwareOptions {
            oob_aliases_mouth: true,
            rng: derive(cfg.seed, 2),
        });
        let mut face = CantinaHostEmulator::new(fw, true);
        face.set_mouth_hz(cfg.mouth_hz);
        let mut host = DualHost::new(face, ChestHost::new(120.0));
        host.chest.boot(0.0); // the boot sweep, as when CantinaOS starts
        let mut procedural = Procedural::default();
        procedural.layers = AliveLayers::from_map(&profile.alive);
        Ok(Performer {
            player: ShowPlayer::new(catalog.clone(), cfg.player),
            body: BodyCompositor::new(),
            idle: IdleRunner::new(catalog.idle.clone(), 0.0),
            puppet: Puppeteer::default(),
            procedural,
            actuation,
            host,
            chest_fw: ChestFirmware::new(chest_layout(profile), derive(cfg.seed, 3)),
            lights: StageLights::new(cfg.light_rig.as_deref(), None, None),
            take: TakeRecorder::default(),
            enables: Enables::default(),
            slots: profile.emotes.clone(),
            joints: joints.iter().map(|j| j.name.clone()).collect(),
            rng: derive(cfg.seed, 4),
            idle_rng: derive(cfg.seed, 5),
            now: 0.0,
            ticked: false,
            speaking: false,
            music: false,
            dj: false,
            bpm: 118.0,
            amplitude: 0.0,
            look: None,
            frozen: false,
            pad: None,
            show_light: None,
            backgrounds: [None, None],
            background_run: None,
            tags: Vec::new(),
            sent_goals: BTreeMap::new(),
            jog: BTreeMap::new(),
            home_pose: joints.iter().map(|j| (j.name.clone(), profile.home_of(&j.name))).collect(),
            home_hold: false,
            goal_seq: 0,
            goal_deadband: cfg.goal_deadband,
            catalog,
            out: Vec::new(),
        })
    }

    /// Outgoing events since the last call.
    pub fn take_events(&mut self) -> Vec<Out> {
        std::mem::take(&mut self.out)
    }

    pub fn now(&self) -> f64 {
        self.now
    }
    /// Emote slot -> cue id (the profile's `emotes`).
    pub fn slots(&self) -> &[String] {
        &self.slots
    }
    pub fn frozen(&self) -> bool {
        self.frozen
    }
    pub fn speaking(&self) -> bool {
        self.speaking
    }

    /// Apply a command at the current clock.
    pub fn command(&mut self, c: Command) {
        match c {
            Command::Perform {
                id,
                source,
                params,
                layer,
            } => {
                self.perform(&id, source, params, layer);
            }
            Command::Stop(sel) => self.stop(&sel),
            Command::Freeze { on } => self.freeze(on),
            Command::Puppet { intent, value } => {
                self.home_hold = false;
                self.puppet.set(&intent, value)
            }
            Command::PuppetRelease => self.puppet.release(),
            Command::PuppetMode { mode } => self.puppet.set_mode(mode),
            Command::Emote { slot } => self.puppet.trigger(slot),
            Command::Pad(p) => self.pad = Some(p),
            Command::Enables(e) => self.enables = e,
            Command::Alive(a) => {
                // A layer switched back on ends the hold (switching them off does not).
                let o = self.procedural.layers;
                if (a.breathing && !o.breathing) || (a.saccades && !o.saccades) || (a.gaze_wander && !o.gaze_wander) || (a.speech_bob && !o.speech_bob) {
                    self.home_hold = false;
                }
                self.procedural.layers = a;
            }
            Command::Mode { mode } => {
                self.host.set_mode(mode);
                if !self.dj {
                    self.set_activity(if mode == SystemMode::Idle {
                        Activity::Idle
                    } else {
                        Activity::Engaged
                    });
                }
            }
            Command::ListeningStarted => {
                if self.host.mode() != SystemMode::Interactive {
                    self.host.set_mode(SystemMode::Interactive);
                }
                self.host.listening_started();
                self.set_activity(Activity::Listening);
            }
            Command::ListeningStopped => {
                self.host.listening_stopped();
                self.set_activity(Activity::Thinking);
            }
            Command::LlmChunk => self.host.llm_chunk(),
            Command::SpeechStarted { timings, tags } => {
                self.speaking = true;
                self.host.speech_started();
                self.set_activity(Activity::Speaking);
                for (off, id) in tags {
                    let dt = timings
                        .as_ref()
                        .and_then(|t| t.get(off).copied())
                        .unwrap_or(off as f64 / TAG_CHARS_PER_SEC);
                    self.tags.push((self.now + dt, id));
                }
            }
            Command::Amplitude { value } => {
                self.amplitude = value;
                self.host.amplitude(value);
            }
            Command::SpeechEnded => {
                self.speaking = false;
                self.amplitude = 0.0;
                self.tags.clear();
                self.host.speech_ended();
                self.set_activity(self.resting());
            }
            Command::Music { playing } => {
                self.music = playing;
                self.host.chest.music(playing, Some(self.bpm));
                if !self.speaking {
                    self.set_activity(self.resting());
                }
            }
            Command::Dj { on } => {
                self.dj = on;
                self.host.chest.dj(on, Some(self.bpm));
                if on && self.host.mode() == SystemMode::Idle {
                    self.host.set_mode(SystemMode::Ambient);
                }
                self.set_activity(self.resting());
            }
            Command::Tempo { bpm } => {
                self.bpm = bpm;
                if self.dj {
                    self.host.chest.dj(true, Some(bpm));
                } else if self.music {
                    self.host.chest.music(true, Some(bpm));
                }
            }
            Command::Look { pan_tilt } => self.look = pan_tilt,
            Command::Background { activity, id } => {
                let i = usize::from(activity == Activity::Dj);
                self.backgrounds[i] = id;
            }
            Command::ServiceStatus {
                service,
                status,
                detail,
                latched,
            } => {
                let latched = latched.unwrap_or_else(|| ChestHost::latches(detail.as_deref()));
                self.host.chest.service_status(&service, &status, latched)
            }
            Command::Autonomy { on } => {
                self.home_hold &= !on;
                self.idle.enabled = on;
                if !on {
                    self.poke_idle();
                }
            }
            Command::Eyes { pattern, duration } => {
                self.host.face.eye_command(&pattern, duration);
            }
            Command::Jog { joint, value } => match value {
                Some(v) if self.joints.contains(&joint) => {
                    self.jog.insert(joint, v);
                }
                _ => {
                    self.jog.remove(&joint);
                }
            },
            Command::JogRelease => self.jog.clear(),
            Command::Home { joints } => {
                let _ = self.home(&joints);
            }
        }
    }

    /// A bus source as the show system's source (tier ceiling): `public` is `jev`-tier,
    /// the runtime and the CantinaOS bridge are `timeline`.
    pub fn show_source(source: r3x_contracts::Source) -> Source {
        use r3x_contracts::Source as S;
        match source {
            S::Jev | S::Public => Source::Jev,
            S::Claude => Source::Claude,
            S::Ui => Source::Ui,
            S::Cli => Source::Cli,
            S::Idle => Source::Idle,
            S::Timeline | S::System | S::Bridge => Source::Timeline,
        }
    }

    /// A bus `PerfCommand` (plan D2) from `source`. `Err` says why a play was refused (the
    /// run also ends with `rejected`).
    pub fn perf_command(
        &mut self,
        c: &PerfCommand,
        source: r3x_contracts::Source,
    ) -> Result<(), String> {
        let source = Self::show_source(source);
        let layer = |l: &PerfLayer| match l {
            PerfLayer::Background => RunLayer::Background,
            PerfLayer::Gesture => RunLayer::Gesture,
            PerfLayer::Show => RunLayer::Show,
        };
        match c {
            PerfCommand::Play {
                id,
                intensity,
                speed,
                layer: l,
            } => {
                let run = self.perform(
                    id,
                    source,
                    Params {
                        intensity: Some(*intensity),
                        speed: Some(*speed),
                    },
                    l.as_ref().map(layer),
                );
                if run.is_none() {
                    let item = self.catalog.get(id).or_else(|| self.catalog.resolve(id));
                    return Err(match item {
                        _ if self.frozen => "motion is frozen".to_string(),
                        None => format!("no show item {id:?}"),
                        Some(it) => format!(
                            "{id} is {:?}-tier; {source:?} may not perform it",
                            it.tier
                        )
                        .to_lowercase(),
                    });
                }
            }
            PerfCommand::Stop(t) => self.stop(&match t {
                StopTarget::Id { id } => StopSel::id(id.clone()),
                StopTarget::Layer { layer: l } => StopSel::layer(layer(l)),
                StopTarget::All => StopSel::all(),
            }),
            // A joint name jogs that joint directly; anything else is a puppeteer intent.
            PerfCommand::Puppet { channels } => {
                for (k, v) in channels {
                    if self.joints.contains(k) {
                        self.jog.insert(k.clone(), *v);
                    } else {
                        self.home_hold = false;
                        self.puppet.set(k, *v);
                    }
                }
            }
            PerfCommand::Release { channels } if channels.is_empty() => {
                self.jog.clear();
                self.puppet.release();
            }
            PerfCommand::Release { channels } => {
                let mut intents = false;
                for c in channels {
                    intents |= self.jog.remove(c).is_none();
                }
                if intents {
                    self.puppet.release();
                }
            }
            PerfCommand::CalJog { .. } | PerfCommand::CalSave { .. } => {
                return Err("calibration is the runtime's job".into())
            }
            PerfCommand::Emote { slot } => {
                if usize::from(*slot) >= self.slots.len() {
                    return Err(format!("no emote in slot {slot}"));
                }
                self.puppet.trigger(usize::from(*slot));
            }
            PerfCommand::Freeze { on } => self.freeze(*on),
            PerfCommand::Eyes { pattern, duration } => {
                if !self.host.face.eye_command(pattern, duration.unwrap_or(0.0)) {
                    return Err(format!("no eye pattern {pattern:?}"));
                }
            }
            PerfCommand::Home { joints } => self.home(joints)?,
            PerfCommand::Look { pan, tilt, .. } => self.look = Some((*pan, *tilt)),
            PerfCommand::Preview { clip, at, hold } => self.preview(clip, *at, *hold)?,
            PerfCommand::PreviewStop => self.preview_stop(),
            PerfCommand::SaveShow { .. } => return Err("saving is the runtime's job".into()),
        }
        Ok(())
    }

    /// Home. No joints: stop every run and preview, release jog and puppet, idle policy and
    /// alive layers off (the host mirrors that into the stage state), and hold every joint at
    /// its home value; the followers get there inside each joint's v/a/j limits. The hold ends
    /// when a run starts, a puppet intent moves, or a layer or the idle policy comes back on.
    /// With joints: jog just those to their home values.
    pub fn home(&mut self, joints: &[String]) -> Result<(), String> {
        if self.frozen {
            return Err("motion is frozen; unfreeze first".into());
        }
        if let Some(j) = joints.iter().find(|j| !self.home_pose.contains_key(*j)) {
            return Err(format!("no joint {j:?}"));
        }
        if !joints.is_empty() {
            for j in joints {
                self.jog.insert(j.clone(), self.home_pose[j]);
            }
            return Ok(());
        }
        self.stop(&StopSel::all());
        self.preview_stop();
        self.jog.clear();
        self.puppet.release();
        self.command(Command::Autonomy { on: false });
        self.procedural.layers = AliveLayers { breathing: false, saccades: false, gaze_wander: false, speech_bob: false };
        self.home_hold = true;
        Ok(())
    }

    /// Whether a whole-body Home is holding the home pose.
    pub fn homing(&self) -> bool {
        self.home_hold
    }

    /// Every joint's commanded position (the follower the controller runs, not the simulated
    /// servo, which settles inside its deadband) within `tol` of its home value.
    pub fn at_home(&self, tol: f64) -> bool {
        self.actuation.channels.iter().all(|ch| {
            ch.cfg.joints.iter().all(|(j, k)| {
                self.home_pose.get(j).is_none_or(|h| (ch.follower.x * k - h).abs() <= tol)
            })
        })
    }

    /// Every joint's home value.
    pub fn home_pose(&self) -> &BTreeMap<String, f64> {
        &self.home_pose
    }

    /// Swap in a reloaded show folder. Running runs keep the items they started with;
    /// the idle policy restarts when it changed.
    pub fn set_catalog(&mut self, catalog: Arc<Catalog>) {
        if catalog.idle != self.catalog.idle {
            if let Some(run) = self.idle.poke(self.now) {
                self.stop(&StopSel::id(run));
            }
            let enabled = self.idle.enabled;
            self.idle = IdleRunner::new(catalog.idle.clone(), self.now);
            self.idle.enabled = enabled;
        }
        self.player.cat = catalog.clone();
        self.catalog = catalog;
    }

    fn resting(&self) -> Activity {
        if self.music || self.dj {
            Activity::Dj
        } else if self.host.mode() == SystemMode::Idle {
            Activity::Idle
        } else {
            Activity::Engaged
        }
    }

    fn set_activity(&mut self, a: Activity) {
        if matches!(
            a,
            Activity::Listening | Activity::Thinking | Activity::Speaking
        ) {
            // Interaction: idle stops and its timer restarts. A listening turn also blends the
            // gesture layer out (Reachy clears its move queue when the user starts talking).
            self.poke_idle();
            if a == Activity::Listening && self.procedural.activity != Activity::Listening {
                self.stop(&StopSel::layer(RunLayer::Gesture));
            }
        }
        self.procedural.set_activity(a, self.now);
    }

    fn poke_idle(&mut self) {
        if let Some(run) = self.idle.poke(self.now) {
            self.stop(&StopSel::id(run));
        }
    }

    fn collect(&self) -> Collect {
        Collect {
            ev: Vec::new(),
            speaking: self.speaking,
            bpm: (self.music || self.dj).then_some(self.bpm),
        }
    }

    /// SPEC `show.perform`. Returns the run id, or None when rejected.
    pub fn perform(
        &mut self,
        id: &str,
        source: Source,
        params: Params,
        layer: Option<RunLayer>,
    ) -> Option<String> {
        if source != Source::Idle {
            self.poke_idle();
        }
        let mut c = self.collect();
        let run = self
            .player
            .perform(&mut c, id, source, params, self.now, layer);
        self.apply_player(c.ev);
        run
    }

    /// Studio preview (Phase 9): the clip document on the show layer from clip time `at`;
    /// `hold` pins it there. The same compositor and actuation as a performed clip.
    pub fn preview(&mut self, doc: &serde_json::Value, at: f64, hold: bool) -> Result<(), String> {
        if self.frozen {
            return Err("motion is frozen".into());
        }
        let clip = parse_clip(doc)?;
        self.home_hold = false;
        let at = at.clamp(0.0, clip.duration);
        self.body.scrub(PREVIEW_RUN, clip, RunLayer::Show, at, hold, self.now);
        Ok(())
    }

    pub fn preview_stop(&mut self) {
        self.body.release(PREVIEW_RUN, self.now);
    }

    /// SPEC `show.stop`.
    pub fn stop(&mut self, sel: &StopSel) {
        let mut c = self.collect();
        self.player.stop(&mut c, sel, self.now);
        self.apply_player(c.ev);
    }

    /// motion.freeze: stop shows and gestures, stop idle, hold setpoints; off blends back over 0.5 s.
    pub fn freeze(&mut self, on: bool) {
        self.frozen = on;
        let mut c = self.collect();
        self.player.freeze(&mut c, on, self.now);
        self.apply_player(c.ev);
        let hold: Pose = if on {
            self.actuation
                .channels
                .iter()
                .map(|ch| (ch.primary_joint.clone(), ch.follower.x))
                .collect()
        } else {
            Pose::new()
        };
        self.body.freeze(on, self.now, Some(&hold));
        if on {
            self.poke_idle();
        }
        self.out.push(Out::Freeze { on });
        self.take
            .event(self.now, "motion.freeze", serde_json::json!({ "on": on }));
    }

    fn apply_player(&mut self, ev: Vec<PEv>) {
        for e in ev {
            match e {
                PEv::Dispatch(a, run, at) => self.dispatch(a, &run, at),
                PEv::Started(run) => {
                    self.home_hold = false;
                    if let Some(owns) = &run.owns {
                        self.body.own(&run.run_id, run.layer, owns, self.now);
                    }
                    self.take.event(self.now, "show.started", serde_json::json!({"id": run.id, "kind": run.kind, "source": run.source, "run_id": run.run_id}));
                    self.out.push(Out::Started { run });
                }
                PEv::Ended(run, reason) => {
                    self.body.release(&run.run_id, self.now);
                    self.idle.ended(&run.run_id, self.now);
                    self.take.event(
                        self.now,
                        "show.ended",
                        serde_json::json!({"id": run.id, "kind": run.kind, "source": run.source, "run_id": run.run_id, "reason": reason}),
                    );
                    self.out.push(Out::Ended { run, reason });
                }
            }
        }
    }

    fn dispatch(&mut self, a: Action, run: &RunInfo, at: f64) {
        match &a {
            Action::Clip {
                id,
                intensity,
                speed,
            } => {
                if let Some(clip) = self.catalog.clip(id) {
                    self.body.play(PlayRequest {
                        run_id: run.run_id.clone(),
                        clip: clip.clone(),
                        intensity: *intensity,
                        speed: *speed,
                        layer: run.layer,
                        owns: run.owns.clone(),
                        t0: at,
                    });
                }
            }
            // EYE_COMMAND through the host's pattern path, so the firmware renders it.
            Action::Eyes {
                pattern, duration, ..
            } => {
                self.host.face.eye_command(pattern, duration.unwrap_or(0.0));
            }
            Action::Chest { command, hold } => {
                let now = self.host.face.fw.now;
                self.host
                    .chest
                    .override_command(command, hold.unwrap_or(0.0), now);
            }
            Action::Lights {
                cue,
                mode,
                fade,
                hold,
                rig,
            } => {
                let (fade, hold) = (fade.unwrap_or(0.0), hold.unwrap_or(0.0));
                if let Some(r) = rig {
                    self.lights.set_rig(r, if fade != 0.0 { fade } else { 1.0 });
                }
                if let Some(m) = mode.as_deref().and_then(LightMode::parse) {
                    let until = if hold > 0.0 {
                        self.now + hold
                    } else {
                        f64::INFINITY
                    };
                    self.show_light = Some(ShowLight {
                        mode: m,
                        until,
                        natural: self.natural_light_mode(),
                    });
                    self.lights.set_mode(m, (fade != 0.0).then_some(fade));
                }
                if let Some(c) = cue {
                    self.lights.show_cue(c, fade, hold);
                }
                if self.enables.stage_lights {
                    self.out.push(Out::StageLights { action: a.clone() });
                }
            }
            Action::Sfx { id } if self.enables.sfx => self.out.push(Out::Sfx { id: id.clone() }),
            // A Claude-triggered show never talks over the reply that triggered it (§7a).
            Action::Speak { text } if run.source != Source::Claude => {
                self.out.push(Out::Speak { text: text.clone() })
            }
            Action::Duck => self.out.push(Out::Duck),
            Action::Unduck => self.out.push(Out::Unduck),
            _ => {}
        }
        self.take.event(
            self.now,
            "show.action",
            serde_json::json!({"run_id": run.run_id, "action": &a}),
        );
        self.out.push(Out::Action {
            run_id: run.run_id.clone(),
            at,
            action: a,
        });
    }

    fn natural_light_mode(&self) -> LightMode {
        if self.dj {
            LightMode::Dj
        } else if self.music {
            LightMode::Music
        } else {
            LightMode::Idle
        }
    }

    fn idle_eligible(&self) -> bool {
        let a = self.procedural.activity;
        !self.frozen
            && !self.speaking
            && matches!(a, Activity::Idle | Activity::Engaged | Activity::Dj)
            && self.player.running().iter().all(|r| {
                Some(r.run_id.as_str()) == self.idle.current() || r.layer == RunLayer::Background
            })
    }

    fn update_background(&mut self) {
        let a = self.procedural.activity;
        let want = if self.frozen || self.home_hold {
            None
        } else {
            match a {
                Activity::Dj => self.backgrounds[1].clone(),
                Activity::Idle | Activity::Engaged => self.backgrounds[0].clone(),
                _ => None,
            }
        };
        let cur = self
            .player
            .running()
            .into_iter()
            .find(|r| r.layer == RunLayer::Background)
            .map(|r| r.run_id.clone());
        if let Some((id, run)) = &self.background_run {
            if Some(id) != want.as_ref() || cur.as_ref() != Some(run) {
                if cur.is_some() {
                    self.stop(&StopSel::layer(RunLayer::Background));
                }
                self.background_run = None;
            }
        }
        if let (Some(w), None) = (want, &self.background_run) {
            if let Some(run) = self.perform(
                &w,
                Source::Timeline,
                Params::default(),
                Some(RunLayer::Background),
            ) {
                self.background_run = Some((w, run));
            }
        }
    }

    /// Advance to `t` (monotonic seconds) and return the frame.
    pub fn tick(&mut self, t: f64) -> Frames {
        let dt = if self.ticked {
            (t - self.now).clamp(0.0, 0.05)
        } else {
            0.0
        };
        self.ticked = true;
        self.now = t;
        let now_ms = t * 1000.0;

        // ---- LED hosts and boards (the host's 60 Hz loop rides on the firmware clock)
        let fw_now = self.host.face.fw.now;
        self.host.tick(fw_now);
        for line in self.host.chest.take_sent() {
            self.chest_fw.write(&format!("{line}\n"));
            if self.enables.chest {
                self.out.push(Out::ChestLine { line });
            }
        }
        for l in self.host.face.take_tapped() {
            if l.dir == SerialDir::Tx && self.enables.face {
                self.out.push(Out::FaceLine { line: l.line });
            }
        }
        self.host.face.fw.advance_to(now_ms);
        self.host.face.fw.read_lines();

        // ---- body: procedural -> compositor -> actuation
        let ctx = PerformContext {
            amplitude: self.amplitude,
            look: self.look,
            bpm: self.bpm,
            energy: self.puppet.energy_gain(),
        };
        let mut pose = self
            .procedural
            .update(t, dt, &ctx, &mut self.rng, &self.joints);
        self.body.apply(&mut pose, t, Some(&self.puppet));
        if self.home_hold && !self.frozen {
            pose.extend(self.home_pose.iter().map(|(j, v)| (j.clone(), *v)));
        }
        if !self.frozen {
            for (j, v) in &self.jog {
                pose.insert(j.clone(), *v);
            }
        }
        for j in &self.joints {
            self.actuation.command(j, get(&pose, j));
        }
        self.actuation.update(dt);
        self.chest_fw.update(self.host.face.fw.now);

        // ---- show system
        self.puppet.update(dt, self.pad.as_ref());
        for e in self.puppet.take_events() {
            match e {
                PuppetEvent::Slot(i) => {
                    if let Some(id) = self.slots.get(i).cloned() {
                        let energy = self.puppet.cmd[7];
                        let params = Params {
                            intensity: Some(1.0 + 0.3 * energy),
                            speed: Some(1.0 + 0.15 * energy),
                        };
                        self.perform(&id, Source::Ui, params, None);
                    }
                }
                PuppetEvent::Mode(m) => self.puppet_mode(m),
                PuppetEvent::ToggleFreeze => self.freeze(!self.frozen),
            }
        }
        let due: Vec<String> = {
            let (due, rest): (Vec<_>, Vec<_>) = std::mem::take(&mut self.tags)
                .into_iter()
                .partition(|(at, _)| *at <= t);
            self.tags = rest;
            due.into_iter().map(|(_, id)| id).collect()
        };
        for id in due {
            self.perform(&id, Source::Claude, Params::default(), None);
        }
        let mut c = self.collect();
        self.player.update(&mut c, t);
        self.apply_player(c.ev);
        self.update_background();
        let ictx = IdleContext {
            eligible: self.idle_eligible(),
            music: self.music || self.dj,
        };
        match self.idle.update(t, ictx, &mut self.idle_rng) {
            Some(IdleAction::Perform(id)) => {
                let run = self.perform(&id, Source::Idle, Params::default(), None);
                self.idle.started(run);
            }
            Some(IdleAction::Stop(run)) => self.stop(&StopSel::id(run)),
            None => {}
        }
        if self.take.recording {
            let sample = self.take_sample();
            self.take.sample(t, || sample);
        }
        self.puppet.fired.clear();

        // ---- stage lights follow the show state
        let natural = self.natural_light_mode();
        if self
            .show_light
            .as_ref()
            .is_some_and(|s| natural != s.natural || t >= s.until)
        {
            self.show_light = None;
        }
        let mode = if self.speaking {
            LightMode::Speaking
        } else {
            self.show_light.as_ref().map_or(natural, |s| s.mode)
        };
        self.lights.set_mode(mode, None);
        self.lights.set_bpm(self.bpm);
        self.lights.update(dt);

        self.send_goals();

        Frames {
            t,
            targets: pose,
            joints: self.actuation.joint_values(),
            eyes: self.host.face.fw.eye_leds,
            mouth: self.host.face.fw.mouth_leds,
            chest: self.chest_fw.pixels.clone(),
            stage: self.lights.out,
            servo: self.actuation.last_frame.clone(),
        }
    }

    fn puppet_mode(&mut self, m: PuppetMode) {
        match m {
            PuppetMode::Dj => {
                if !self.dj {
                    self.command(Command::Dj { on: true });
                }
            }
            _ => {
                if self.dj {
                    self.command(Command::Dj { on: false });
                }
                self.host.set_mode(if m == PuppetMode::Idle {
                    SystemMode::Idle
                } else {
                    SystemMode::Interactive
                });
                self.set_activity(if m == PuppetMode::Idle {
                    Activity::Idle
                } else {
                    Activity::Engaged
                });
            }
        }
    }

    /// Goals at event time (D6): a joint's goal goes out when its target moves by more than
    /// the deadband, with the channel's motion limits. Not a 50 Hz stream.
    fn send_goals(&mut self) {
        if !self.enables.motion {
            return;
        }
        for ch in &self.actuation.channels {
            let target = ch.follower.target;
            let j = &ch.primary_joint;
            if self
                .sent_goals
                .get(j)
                .is_some_and(|v| (v - target).abs() < self.goal_deadband)
            {
                continue;
            }
            self.sent_goals.insert(j.clone(), target);
            self.goal_seq += 1;
            let l = ch.follower.limits;
            self.out.push(Out::ServoGoal {
                joint: j.clone(),
                target,
                v_max: l.v_max,
                a_max: l.a_max,
                j_max: l.j_max,
                seq: self.goal_seq,
            });
        }
    }

    // ------------------------------------------------------------------ takes

    pub fn take_start(&mut self, started_iso: &str) {
        self.take.start(self.now, &self.slots, started_iso);
    }

    fn take_sample(&self) -> TakeSample {
        let mut layers = serde_json::Map::new();
        for l in ["background", "gesture", "show"] {
            layers.insert(l.into(), serde_json::Value::Null);
        }
        for r in self.player.running() {
            let key = serde_json::to_value(r.layer)
                .ok()
                .and_then(|v| v.as_str().map(String::from))
                .unwrap_or_default();
            layers.insert(key, r.id.clone().into());
        }
        TakeSample {
            cmd: self.puppet.cmd,
            slots: self.puppet.fired.clone(),
            mode: self.puppet.mode,
            frozen: self.frozen,
            activity: serde_json::to_value(self.procedural.activity)
                .ok()
                .and_then(|v| v.as_str().map(String::from))
                .unwrap_or_default(),
            speaking: self.speaking,
            layers,
            live: false,
        }
    }
}
