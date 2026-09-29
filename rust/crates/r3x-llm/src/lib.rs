//! `r3x-llm`: the one Claude client every R3X subsystem uses.
//!
//! ```ignore
//! let llm = LlmClient::from_env()?.expect("no ANTHROPIC_API_KEY / OPENROUTER_API_KEY");
//! let req = prompt::turn_request(&system, memory.messages(), tools, router_acted);
//! let mut s = llm.stream(&req).await?;
//! while let Some(ev) = s.next().await {
//!     match ev? {
//!         StreamEvent::TextDelta(t) => { /* strip show tags, feed TTS */ }
//!         StreamEvent::ToolUse(t) => { /* dispatch now, not at the end */ }
//!         StreamEvent::Done(m) => { /* memory.add(Assistant, m.text()) */ }
//!     }
//! }
//! ```

pub mod client;
pub mod fixture;
pub mod prompt;
pub mod provider;
pub mod pyjson;
pub mod request;
pub mod stream;

pub use client::{LlmClient, LlmConfig, LlmStream, DEFAULT_MODEL};
pub use fixture::ClaudeFixtures;
pub use prompt::{SessionMemory, TurnContext};
pub use provider::{ProviderConfig, ProviderKind};
pub use request::{Image, Message, MessagesRequest, Role, Tool, ToolChoice};
pub use stream::{ContentBlock, FinalMessage, StreamEvent, ToolUse, Usage};

#[derive(Debug, thiserror::Error)]
pub enum LlmError {
    #[error("LLM unavailable: no provider key")]
    Unavailable,
    #[error("http: {0}")]
    Http(String),
    #[error("api {status}: {message}")]
    Api { status: u16, message: String },
    #[error("protocol: {0}")]
    Protocol(String),
    #[error("fixture: {0}")]
    Fixture(String),
}
