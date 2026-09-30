//! Retained state: one `watch` per domain on the bus; the full set is the gateway `hello`.

use std::collections::BTreeMap;

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use ts_rs::TS;

use crate::envelope::Source;
use crate::messages::{PerfLayer, RunKind};

/// Operating mode (plan §4). Distinct from [`Engagement`].
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Hash, Serialize, Deserialize, TS, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum OperatingMode {
    #[default]
    Show,
    Bench,
    Studio,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
pub struct StageState {
    pub mode: OperatingMode,
    /// Output name -> enabled. Enables gate drivers, not layers.
    pub outputs: BTreeMap<String, bool>,
    /// Procedural alive layer -> enabled.
    pub layers: BTreeMap<String, bool>,
    /// Voice/LLM brain accepts turns.
    pub brain: bool,
    /// Idle policy and DJ autonomy.
    pub autonomy: bool,
    /// Every run stopped, new ones refused; the chest holds its state.
    pub frozen: bool,
    /// Where the head looks in Show (Bench and Studio ignore it).
    #[serde(default)]
    pub gaze: GazeSource,
    /// With `gaze: viewport`: the panel whose 3D camera is the target (the last to select it);
    /// none = whichever panel sends one.
    #[serde(default)]
    #[ts(optional = nullable)]
    pub gaze_owner: Option<String>,
    /// The panel holding the gamepad (Build mode jogs the workbench with it); while set, the
    /// runtime's pad operator layer stands down. None = the runtime drives with it.
    #[serde(default)]
    #[ts(optional = nullable)]
    pub pad_owner: Option<String>,
}

/// The look target the head attends to in Show.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize, TS, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum GazeSource {
    /// A panel's 3D camera (`perf.look` from the owning panel).
    #[default]
    Viewport,
    /// The largest detected face (r3x-vision); straight ahead when nobody is seen.
    Vision,
    /// Sound-source direction. Not implemented yet.
    Audio,
    /// Straight ahead; idle glances only.
    Off,
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize, TS, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum ConversationPhase {
    #[default]
    Idle,
    Listening,
    Thinking,
    Speaking,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
pub struct ConversationState {
    pub phase: ConversationPhase,
    #[ts(optional = nullable)]
    pub conversation_id: Option<String>,
    /// Client id holding push-to-talk, if any.
    #[ts(optional = nullable)]
    pub ptt_owner: Option<String>,
}

/// Today's STARTUP/IDLE/AMBIENT/INTERACTIVE; gates the STT socket, ducking, mouse trigger.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize, TS, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum Engagement {
    #[default]
    Startup,
    Idle,
    Ambient,
    Interactive,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
pub struct EngagementState {
    pub engagement: Engagement,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
pub struct Track {
    pub title: String,
    #[ts(optional = nullable)]
    pub artist: Option<String>,
    #[ts(optional = nullable)]
    pub path: Option<String>,
    #[ts(optional = nullable)]
    pub duration_s: Option<f64>,
    #[ts(optional = nullable)]
    pub bpm: Option<f64>,
    #[ts(optional = nullable)]
    pub first_beat_s: Option<f64>,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
pub struct MusicState {
    pub playing: bool,
    #[ts(optional = nullable)]
    pub track: Option<Track>,
    /// 0..1
    pub volume: f64,
    pub ducked: bool,
    /// Track titles in the local library, sorted.
    pub library: Vec<String>,
    /// Position (s) in `track` at bus time `position_t` (`t_mono`); extrapolate while
    /// `playing`. With `track.bpm`/`first_beat_s` this is the performer's beat clock anchor.
    #[serde(default)]
    pub position_s: f64,
    #[serde(default)]
    pub position_t: f64,
    #[serde(default)]
    pub paused: bool,
    /// Position (s) in `track` where `music.track_ending_soon` fires (the DJ's transition
    /// point); none for a track shorter than the threshold.
    #[serde(default)]
    #[ts(optional = nullable)]
    pub ending_at_s: Option<f64>,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
pub struct DjState {
    pub active: bool,
    #[ts(optional = nullable)]
    pub current: Option<Track>,
    #[ts(optional = nullable)]
    pub next: Option<Track>,
    /// The transition line into `next`.
    #[serde(default)]
    pub commentary: CommentaryStatus,
    /// The running transition plan's step, e.g. `music_crossfade (5/7)`; none between them.
    #[serde(default)]
    #[ts(optional = nullable)]
    pub step: Option<String>,
}

/// Where the DJ's next transition line is.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize, TS, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum CommentaryStatus {
    #[default]
    None,
    /// Claude is writing it.
    Writing,
    /// Written; the speech cache is synthesising it.
    Synthesizing,
    /// Cached audio, ready to play.
    Ready,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
pub struct RunInfo {
    pub run_id: u64,
    pub id: String,
    pub kind: RunKind,
    pub layer: PerfLayer,
    pub source: Source,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
pub struct PerfState {
    pub frozen: bool,
    pub runs: Vec<RunInfo>,
    /// Puppeted channels and their current values.
    pub puppet: BTreeMap<String, f64>,
    /// A whole-body Home is holding the home pose.
    #[serde(default)]
    pub homing: bool,
    /// Every joint is within [`HOME_TOLERANCE`] of its home value.
    #[serde(default)]
    pub at_home: bool,
}

/// How close (deg or mm) a joint must be to count as home.
pub const HOME_TOLERANCE: f64 = 0.5;

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
pub struct LightsState {
    /// Last named eye pattern sent (e.g. `SPEAKING`).
    #[ts(optional = nullable)]
    pub eye_pattern: Option<String>,
    #[ts(optional = nullable)]
    pub eye_color: Option<String>,
    #[ts(optional = nullable)]
    pub chest_mode: Option<String>,
    #[ts(optional = nullable)]
    pub stage_cue: Option<String>,
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize, TS, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum ServiceStatus {
    #[default]
    Starting,
    Running,
    Degraded,
    Error,
    Stopped,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
pub struct ServiceHealth {
    pub status: ServiceStatus,
    #[ts(optional = nullable)]
    pub detail: Option<String>,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
pub struct ServicesState {
    pub services: BTreeMap<String, ServiceHealth>,
}

/// All retained state; the gateway `hello` body.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
pub struct RetainedState {
    pub stage: StageState,
    pub conversation: ConversationState,
    pub engagement: EngagementState,
    pub music: MusicState,
    pub dj: DjState,
    pub perf: PerfState,
    pub lights: LightsState,
    pub services: ServicesState,
}

/// A whole-domain replacement; the gateway `state` body.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
#[serde(tag = "domain", content = "state", rename_all = "snake_case")]
pub enum StateUpdate {
    Stage(StageState),
    Conversation(ConversationState),
    Engagement(EngagementState),
    Music(MusicState),
    Dj(DjState),
    Perf(PerfState),
    Lights(LightsState),
    Services(ServicesState),
}

impl StateUpdate {
    pub fn apply(self, s: &mut RetainedState) {
        match self {
            StateUpdate::Stage(v) => s.stage = v,
            StateUpdate::Conversation(v) => s.conversation = v,
            StateUpdate::Engagement(v) => s.engagement = v,
            StateUpdate::Music(v) => s.music = v,
            StateUpdate::Dj(v) => s.dj = v,
            StateUpdate::Perf(v) => s.perf = v,
            StateUpdate::Lights(v) => s.lights = v,
            StateUpdate::Services(v) => s.services = v,
        }
    }
}
