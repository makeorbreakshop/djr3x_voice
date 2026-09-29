//! Commands (with acks) and events, grouped by message class and domain (plan D2/D3).
//!
//! Wire shape: commands are `{class, type, ...fields}`, events are `{domain, type, ...fields}`.

use std::collections::BTreeMap;

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use ts_rs::TS;

use crate::envelope::Source;
use crate::state::{Engagement, OperatingMode, ServiceStatus, Track};

/// Message class; the gateway maps each authenticated client to an allowed set.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, TS, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum MessageClass {
    Intent,
    Perf,
    Stage,
    Telemetry,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
#[serde(tag = "class", rename_all = "snake_case")]
pub enum Command {
    Intent(IntentCommand),
    Perf(PerfCommand),
    Stage(StageCommand),
    Telemetry(TelemetryCommand),
}

impl Command {
    pub fn class(&self) -> MessageClass {
        match self {
            Command::Intent(_) => MessageClass::Intent,
            Command::Perf(_) => MessageClass::Perf,
            Command::Stage(_) => MessageClass::Stage,
            Command::Telemetry(_) => MessageClass::Telemetry,
        }
    }
}

/// Things you ask the character to do; the brain decides how.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum IntentCommand {
    /// Inject a line as if spoken.
    Say { text: String },
    /// Push-to-talk; ownership lands in `state.conversation.ptt_owner`.
    PttStart,
    PttStop,
    Music(MusicCommand),
    Dj { active: bool },
    /// A line for the legacy command console (CantinaOS `help`, `status`, `eye ...`).
    /// Bridge-only; retired with CantinaOS in Phase 7.
    Console { line: String },
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
#[serde(tag = "action", rename_all = "snake_case")]
pub enum MusicCommand {
    Play {
        #[serde(default, skip_serializing_if = "Option::is_none")]
        #[ts(optional)]
        query: Option<String>,
    },
    Stop,
    Next,
}

/// Performance layer a run occupies.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, TS, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum PerfLayer {
    Gesture,
    Show,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
#[serde(tag = "target", rename_all = "snake_case")]
pub enum StopTarget {
    Id { id: String },
    Layer { layer: PerfLayer },
    All,
}

/// Direct body control. Goes to the performer.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum PerfCommand {
    /// Play a clip, cue or sequence by id.
    Play {
        id: String,
        #[serde(default = "one")]
        intensity: f64,
        #[serde(default = "one")]
        speed: f64,
        /// Defaults to the item's own layer.
        #[serde(default, skip_serializing_if = "Option::is_none")]
        #[ts(optional)]
        layer: Option<PerfLayer>,
    },
    Stop(StopTarget),
    /// Per-channel override: channel (joint or intent) -> value.
    Puppet { channels: BTreeMap<String, f64> },
    /// Release puppeted channels (empty = all); they ease back.
    Release {
        #[serde(default)]
        channels: Vec<String>,
    },
    /// Fire an emote slot from the profile's `emotes`.
    Emote { slot: u8 },
    /// Stop every run, refuse new ones; `on: false` releases. Same switch as
    /// `StageCommand::Freeze` (`state.stage.frozen`); kept here for the puppeteer.
    Freeze { on: bool },
}

fn one() -> f64 {
    1.0
}

/// Stage manager: operating mode, output and layer switches.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum StageCommand {
    SetMode { mode: OperatingMode },
    SetEngagement { engagement: Engagement },
    /// Gate a driver output (profile actuator or light group name).
    SetOutput { output: String, enabled: bool },
    /// Toggle a procedural alive layer (e.g. `breathing`).
    SetLayer { layer: String, enabled: bool },
    SetBrain { enabled: bool },
    SetAutonomy { enabled: bool },
    Freeze { on: bool },
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum TelemetryCommand {
    SetLogLevel { level: String },
    /// Subscribe this client to 50 Hz `frames`.
    Frames { enabled: bool },
}

/// Reply to every command.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, TS, JsonSchema)]
#[serde(tag = "status", rename_all = "snake_case")]
pub enum Ack {
    Accepted,
    Rejected { reason: String },
}

impl Ack {
    pub fn rejected(reason: impl Into<String>) -> Self {
        Ack::Rejected { reason: reason.into() }
    }

    pub fn is_accepted(&self) -> bool {
        matches!(self, Ack::Accepted)
    }
}

/// Event domain; each has its own bounded broadcast channel on the bus.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize, TS, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum Domain {
    Conversation,
    Perf,
    Music,
    Dj,
    Stage,
    Ops,
}

impl Domain {
    pub const ALL: [Domain; 6] = [
        Domain::Conversation,
        Domain::Perf,
        Domain::Music,
        Domain::Dj,
        Domain::Stage,
        Domain::Ops,
    ];

    pub fn as_str(self) -> &'static str {
        match self {
            Domain::Conversation => "conversation",
            Domain::Perf => "perf",
            Domain::Music => "music",
            Domain::Dj => "dj",
            Domain::Stage => "stage",
            Domain::Ops => "ops",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
#[serde(tag = "domain", rename_all = "snake_case")]
pub enum Event {
    Conversation(ConversationEvent),
    Perf(PerfEvent),
    Music(MusicEvent),
    Dj(DjEvent),
    Stage(StageEvent),
    Ops(OpsEvent),
}

impl Event {
    pub fn domain(&self) -> Domain {
        match self {
            Event::Conversation(_) => Domain::Conversation,
            Event::Perf(_) => Domain::Perf,
            Event::Music(_) => Domain::Music,
            Event::Dj(_) => Domain::Dj,
            Event::Stage(_) => Domain::Stage,
            Event::Ops(_) => Domain::Ops,
        }
    }

    /// `domain.type`, e.g. `perf.started`; the session log's `topic`.
    pub fn topic(&self) -> String {
        let v = serde_json::to_value(self).unwrap_or_default();
        let ty = v.get("type").and_then(|t| t.as_str()).unwrap_or("?");
        format!("{}.{}", self.domain().as_str(), ty)
    }
}

/// Turn lifecycle. The turn id rides on the envelope's `conversation_id`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ConversationEvent {
    ListeningStarted,
    ListeningStopped { transcript: String },
    Transcript { text: String, is_final: bool },
    IntentDetected {
        tool: String,
        #[ts(optional = nullable)]
        confidence: Option<f64>,
    },
    /// Streaming reply text (a delta).
    ReplyDelta { text: String },
    /// The whole reply, show tags stripped.
    Reply { text: String },
    /// First audible sample.
    SpeechStarted,
    SpeechEnded,
    /// Mouth amplitude 0..1 at the profile's `audio.mouth_hz`, in step with playback.
    Mouth { level: f64 },
    /// Character timing of the line being spoken: ms from its first audible sample, which was
    /// heard at `audio_t0` (bus `t_mono` seconds). One event per synthesis chunk.
    SpeechTiming { chars: Vec<String>, start_ms: Vec<f64>, duration_ms: Vec<f64>, audio_t0: f64 },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, TS, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum RunKind {
    Clip,
    Cue,
    Sequence,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, TS, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum EndReason {
    Done,
    Interrupted,
    Rejected,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum PerfEvent {
    Started { id: String, kind: RunKind, source: Source, run_id: u64 },
    Ended { id: String, kind: RunKind, source: Source, run_id: u64, reason: EndReason },
    Sfx { id: String },
    Emote { slot: u8, cue: String },
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum MusicEvent {
    TrackStarted { track: Track },
    TrackStopped,
    TrackEndingSoon { remaining_s: f64 },
    Ducked { level: f64 },
    Unducked,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum DjEvent {
    Started,
    Stopped,
    NextSelected { track: Track },
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum StageEvent {
    ModeChanged { from: OperatingMode, to: OperatingMode },
    EngagementChanged { from: Engagement, to: Engagement },
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum OpsEvent {
    ServiceStatus {
        service: String,
        status: ServiceStatus,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        #[ts(optional)]
        detail: Option<String>,
    },
    /// One measured latency leg of a turn.
    Latency { leg: String, ms: f64 },
    /// Reply to an `intent.console` line.
    Console { message: String, is_error: bool },
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn perf_play_defaults_and_topic() {
        let cmd: Command =
            serde_json::from_str(r#"{"class":"perf","type":"play","id":"yes"}"#).unwrap();
        assert_eq!(
            cmd,
            Command::Perf(PerfCommand::Play { id: "yes".into(), intensity: 1.0, speed: 1.0, layer: None })
        );
        assert_eq!(cmd.class(), MessageClass::Perf);

        let ev = Event::Perf(PerfEvent::Sfx { id: "zap".into() });
        assert_eq!(ev.topic(), "perf.sfx");
        assert_eq!(ev.domain(), Domain::Perf);
    }

    #[test]
    fn nested_tags_round_trip() {
        for cmd in [
            Command::Intent(IntentCommand::Music(MusicCommand::Play { query: Some("cantina".into()) })),
            Command::Perf(PerfCommand::Stop(StopTarget::Layer { layer: PerfLayer::Show })),
            Command::Stage(StageCommand::SetMode { mode: OperatingMode::Bench }),
            Command::Intent(IntentCommand::PttStart),
        ] {
            let s = serde_json::to_string(&cmd).unwrap();
            assert_eq!(serde_json::from_str::<Command>(&s).unwrap(), cmd, "{s}");
        }
        let ack: Ack = serde_json::from_str(r#"{"status":"rejected","reason":"frozen"}"#).unwrap();
        assert_eq!(ack, Ack::rejected("frozen"));
    }
}
