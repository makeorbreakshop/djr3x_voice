//! R3X performer core: the single conductor for the body (plan D4).
//!
//! No I/O, a seeded RNG, deterministic, WASM-compatible. It holds the show system (format,
//! linter, clock-driven player, idle policy, body compositor, puppeteer, takes), the
//! procedural alive layers, actuation (soft limits, jerk-limited follower, pulse output) and
//! the LED board emulators, and wires them together in [`performer::Performer`].
//!
//! A port of the sim's TypeScript (`sim/web/src/`); parity is tested against it.

pub mod actuation;
pub mod behavior;
pub mod leds;
pub mod performer;
pub mod rng;
pub mod show;
pub mod stagelights;
pub mod tempo;

pub use performer::{Frames, Performer};
