//! `r3x-intent`: the Jev fast intent router (CLAUDE.md §3b).
//!
//! Brain wiring, per turn:
//! 1. `voice.listening.started` -> `router.turn_started()`
//! 2. interim / final STT segments -> `router.partial(text, is_final)`
//! 3. transcript in hand -> spawn `router.classify_turn(t)`; if `decision.intent` is set: dispatch
//!    the action, `gate.resolve(key, ActionTaken::from_decision(..))`, else `gate.resolve(key, None)`
//! 4. Claude side: `gate.await_router(key, cfg.verdict_wait, cfg.outcome_wait)`; `Some(a)` ->
//!    prepend `a.context_block()` and send `ToolChoice::None`
//! 5. execution side, when the real outcome is known -> `gate.resolve_outcome(key, params)`
//!
//! `gate.register_router()` only when `router.active()`.

pub mod catalogue;
pub mod client;
pub mod decide;
pub mod gate;
pub mod router;

pub use client::{JevClient, JevFixtures, JEV_MODEL};
pub use decide::{decide, JevAnswer, JevResult, RouterDecision};
pub use gate::{ActionTaken, FastRouterGate};
pub use router::{IntentRouter, RouterConfig, TurnOutcome};
