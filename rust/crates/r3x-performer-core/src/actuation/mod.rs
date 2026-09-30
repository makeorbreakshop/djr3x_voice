//! Actuation: jerk-limited trajectory following, servo models, the pulse output pipeline
//! and the Maestro script interpreter.

pub mod maestro;
pub mod pipeline;
pub mod servos;
/// The follower lives in `r3x-motion` (`no_std`), shared with the servo controller firmware.
pub use r3x_motion::trajectory;
