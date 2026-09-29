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
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
pub struct DjState {
    pub active: bool,
    #[ts(optional = nullable)]
    pub current: Option<Track>,
    #[ts(optional = nullable)]
    pub next: Option<Track>,
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
}

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
