//! The gateway/bus envelope (plan §3b) and message sources with their tier ceilings.

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use ts_rs::TS;

use crate::frames::Frames;
use crate::messages::{Ack, Command, Event, MessageClass};
use crate::profile::RobotProfile;
use crate::state::{RetainedState, StateUpdate};

pub const PROTOCOL_VERSION: u32 = 1;

/// Permission tier of a performance item. Ordered: `Free < Cheap < Show`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize, TS, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum Tier {
    /// Instantly reversible (gestures, eye animation).
    Free,
    /// Reversible but noticeable (music, DJ mode, cheap cues).
    Cheap,
    /// Full routines.
    Show,
}

/// Who originated a message. Stamped by the bus/gateway, never self-declared by a client.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, TS, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum Source {
    /// Jev fast intent router.
    Jev,
    Claude,
    /// Plan/timeline executor.
    Timeline,
    /// Authenticated control panel / Studio.
    Ui,
    Cli,
    /// Idle policy.
    Idle,
    /// Anonymous public-site visitor.
    Public,
    /// The runtime itself (stage manager, drivers, health).
    System,
    /// CantinaOS bridge (Phase 1-6 strangler).
    Bridge,
}

impl Source {
    /// Highest tier this source may perform (plan §3b).
    pub fn max_tier(self) -> Tier {
        match self {
            Source::Idle => Tier::Free,
            Source::Jev | Source::Public => Tier::Cheap,
            Source::Claude
            | Source::Timeline
            | Source::Ui
            | Source::Cli
            | Source::System
            | Source::Bridge => Tier::Show,
        }
    }

    pub fn allows(self, tier: Tier) -> bool {
        tier <= self.max_tier()
    }

    /// Command-level gate. `public` (plan Phase 10): `intent.say`, push-to-talk, and perf
    /// `play` / `stop` / `emote` (the performer holds a play to the `cheap` ceiling), plus
    /// its own frame stream - never stage, music, DJ, console, puppet, freeze or calibration.
    pub fn may_issue(self, cmd: &Command) -> bool {
        use crate::{IntentCommand as I, PerfCommand as P, TelemetryCommand as T};
        match self {
            Source::Public => matches!(
                cmd,
                Command::Intent(I::Say { .. } | I::PttStart | I::PttStop)
                    | Command::Perf(P::Play { .. } | P::Stop(_) | P::Emote { .. })
                    | Command::Telemetry(T::Frames { .. })
            ),
            _ => true,
        }
    }
}

/// Envelope kind. Mirrors the variant of [`Body`].
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, TS, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum Kind {
    Hello,
    State,
    Event,
    Command,
    Ack,
    Result,
    Frames,
    Audio,
    Log,
}

/// `kind` + `body`, adjacently tagged so the wire stays `{kind, body}`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
#[serde(tag = "kind", content = "body", rename_all = "snake_case")]
pub enum Body {
    /// First message on connect.
    Hello(Box<Hello>),
    State(StateUpdate),
    Event(Event),
    Command(Command),
    Ack(Ack),
    Result(serde_json::Value),
    Frames(Frames),
    /// Metadata for a following binary audio frame.
    Audio(AudioMeta),
    Log(LogLine),
}

impl Body {
    pub fn kind(&self) -> Kind {
        match self {
            Body::Hello(_) => Kind::Hello,
            Body::State(_) => Kind::State,
            Body::Event(_) => Kind::Event,
            Body::Command(_) => Kind::Command,
            Body::Ack(_) => Kind::Ack,
            Body::Result(_) => Kind::Result,
            Body::Frames(_) => Kind::Frames,
            Body::Audio(_) => Kind::Audio,
            Body::Log(_) => Kind::Log,
        }
    }
}

/// `hello` body: who the gateway thinks you are, full retained state, the robot profile.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
pub struct Hello {
    pub client: ClientInfo,
    pub state: RetainedState,
    #[ts(optional = nullable)]
    pub profile: Option<RobotProfile>,
}

/// An authenticated gateway client. `source` is stamped on everything it sends.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, TS, JsonSchema)]
pub struct ClientInfo {
    pub name: String,
    pub source: Source,
    pub classes: Vec<MessageClass>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
pub struct AudioMeta {
    /// `in` = client mic to runtime, `out` = runtime TTS to client.
    pub direction: AudioDirection,
    pub sample_rate: u32,
    pub channels: u8,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, TS, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum AudioDirection {
    In,
    Out,
}

/// What a gateway client sends. The gateway stamps `seq`, clocks and `source`; a client never
/// declares them. `id` comes back as `re` on the command's ack.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
pub struct ClientMessage {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub id: Option<String>,
    #[serde(flatten)]
    pub body: ClientBody,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
#[serde(tag = "kind", content = "body", rename_all = "snake_case")]
pub enum ClientBody {
    Command(Command),
    /// Metadata for the binary audio frames that follow (Phase 2).
    Audio(AudioMeta),
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, TS, JsonSchema)]
pub struct LogLine {
    pub level: String,
    pub target: String,
    pub message: String,
}

/// One message on the bus or the wire.
///
/// `t_mono` is seconds since the runtime's monotonic epoch; `t_wall` is Unix seconds.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
pub struct Envelope {
    pub v: u32,
    pub seq: u64,
    pub t_mono: f64,
    pub t_wall: f64,
    pub source: Source,
    /// Client-chosen id on a command; echoed as `re` on its ack/result.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub re: Option<String>,
    /// Turn id minted at capture and adopted downstream.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub conversation_id: Option<String>,
    #[serde(flatten)]
    pub body: Body,
}

impl Envelope {
    pub fn kind(&self) -> Kind {
        self.body.kind()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::messages::{IntentCommand, PerfCommand};

    #[test]
    fn tier_ceilings() {
        assert!(Source::Jev.allows(Tier::Cheap));
        assert!(!Source::Jev.allows(Tier::Show));
        assert!(!Source::Idle.allows(Tier::Cheap));
        assert!(Source::Claude.allows(Tier::Show));
        assert_eq!(Source::Public.max_tier(), Tier::Cheap);
    }

    #[test]
    fn public_command_gate() {
        let say = Command::Intent(IntentCommand::Say { text: "hi".into() });
        let freeze = Command::Perf(PerfCommand::Freeze { on: true });
        assert!(Source::Public.may_issue(&say));
        assert!(Source::Public.may_issue(&Command::Intent(IntentCommand::PttStart)));
        assert!(Source::Public.may_issue(&Command::Perf(PerfCommand::Emote { slot: 0 })));
        assert!(!Source::Public.may_issue(&freeze));
        assert!(!Source::Public.may_issue(&Command::Intent(IntentCommand::Music(crate::MusicCommand::Stop))));
        assert!(!Source::Public.may_issue(&Command::Intent(IntentCommand::Dj { active: true })));
        assert!(!Source::Public.may_issue(&Command::Intent(IntentCommand::Console { line: "reset".into() })));
        assert!(!Source::Public.may_issue(&Command::Telemetry(crate::TelemetryCommand::SetLogLevel { level: "debug".into() })));
        assert!(Source::Ui.may_issue(&freeze));
    }

    #[test]
    fn envelope_wire_shape_round_trips() {
        let env = Envelope {
            v: PROTOCOL_VERSION,
            seq: 7,
            t_mono: 1.5,
            t_wall: 1_790_000_000.0,
            source: Source::Ui,
            id: Some("c1".into()),
            re: None,
            conversation_id: None,
            body: Body::Command(Command::Perf(PerfCommand::Emote { slot: 2 })),
        };
        let json = serde_json::to_value(&env).unwrap();
        assert_eq!(json["kind"], "command");
        assert_eq!(json["body"]["class"], "perf");
        assert_eq!(json["body"]["type"], "emote");
        assert!(json.get("re").is_none());
        let back: Envelope = serde_json::from_value(json).unwrap();
        assert_eq!(back, env);
        assert_eq!(back.kind(), Kind::Command);
    }
}
