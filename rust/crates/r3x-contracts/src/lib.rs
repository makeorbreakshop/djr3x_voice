//! Wire and config contracts for the R3X runtime (plan §2 D2/D3, §3).
//!
//! Everything a client, driver or service exchanges is a type here. TypeScript bindings and
//! JSON Schema are generated from these types (`cargo run -p r3x-contracts --bin export`);
//! never hand-mirror them.

pub mod dotenv;
pub mod electronics;
pub mod envelope;
pub mod frames;
pub mod messages;
pub mod profile;
pub mod state;

pub use envelope::{
    AudioDirection, AudioMeta, Body, ClientBody, ClientInfo, ClientMessage, Envelope, Hello, Kind, LogLine, Source, Tier,
    PROTOCOL_VERSION,
};
pub use electronics::ElectronicsPackage;
pub use frames::{Frames, PadBank, PadBanks, PadBinding, PadControls, PadFrame, PadMapping, PadMenuView, Rgb};
pub use messages::{
    Ack, Command, ConversationEvent, DjEvent, Domain, EndReason, Event, IntentCommand,
    MessageClass, MusicCommand, MusicEvent, OpsEvent, PerfCommand, PerfEvent, PerfLayer, RunKind, ServoChannelTelemetry,
    StageCommand,
    StageEvent, StopTarget, TelemetryCommand, VisionEvent,
};
pub use profile::{ProfileError, RobotProfile};
pub use state::{
    CommentaryStatus, ConversationPhase, GazeSource, ConversationState, DjState, Engagement, EngagementState, LightsState,
    MusicState, OperatingMode, PerfState, RetainedState, Rig, HOME_TOLERANCE, RunInfo, ServiceHealth, ServiceStatus,
    ServicesState, StageState, StateUpdate, Track,
};
