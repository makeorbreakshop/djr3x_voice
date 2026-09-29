//! Servo drivers (plan D6). `r3x_servo` gets goals at event time; `maestro` and `pca9685`
//! are dumb sinks fed the host follower's controller frame; Feetech and Dynamixel are stubs.

pub mod maestro;
pub mod pca9685;
pub mod proto;
pub mod r3x;

use crate::{Driver, Health};
use r3x_contracts::ServiceStatus;

/// Placeholder for a profile driver kind with no implementation yet: accepts everything,
/// moves nothing, says so in its health.
pub struct StubDriver(pub &'static str);

impl Driver for StubDriver {
    fn name(&self) -> &str {
        self.0
    }
    fn set_enabled(&mut self, _on: bool, _now: f64) {}
    fn next_poll(&self, now: f64) -> f64 {
        now + 1.0
    }
    fn health(&self) -> Health {
        Health::new(ServiceStatus::Degraded, "not implemented".to_string())
    }
}
