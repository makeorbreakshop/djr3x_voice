//! Drivers: the performer's outputs to hardware (plan D4-D6, §7a "Hardware and ops").
//!
//! The performer computes everything; a driver only transports. Each driver is a synchronous
//! state machine driven with an explicit monotonic clock (`now`, seconds), so its timing rules
//! are unit-testable, and runs on its own thread ([`runner::spawn`]) because serial I/O
//! blocks: a stalled USB port never stalls the performer.
//!
//! | Driver | Consumes | Wire |
//! |---|---|---|
//! | [`face::FaceDriver`] | `Out::FaceLine` | named_v1 (`SI SE SL ST SS SF`, `Mnnn`) |
//! | [`chest::ChestDriver`] | `Out::ChestLine`, `Out::Freeze` | named_v1 (+ `Bnnn Xn Hxxx`), 20 Hz send-on-change |
//! | [`virtual_driver::VirtualDriver`] | frames | bus frames channel, 50 Hz |
//! | [`servo::r3x::R3xServoDriver`] | `Out::ServoGoal` | framed binary, see `PROTOCOL.md` |
//! | [`servo::maestro::MaestroDriver`] | frames (host follower) | Pololu compact/Pololu protocol |
//! | [`servo::pca9685::Pca9685Driver`] | frames (host follower) | registers over a [`servo::pca9685::RegisterBus`] |
//!
//! Output enables gate drivers, not layers: a disabled driver drops (or holds) output while
//! the performer keeps computing frames.

pub mod chest;
pub mod face;
pub mod link;
pub mod runner;
pub mod servo;
pub mod virtual_driver;

use r3x_contracts::ServiceStatus;
use r3x_performer_core::performer::Out;
use r3x_performer_core::Frames;

pub use runner::{spawn, DriverHandle, DriverSet};

/// A driver's health as reported on the bus (`ops.service_status` -> `state.services`).
#[derive(Debug, Clone, PartialEq)]
pub struct Health {
    pub status: ServiceStatus,
    pub detail: Option<String>,
}

impl Health {
    pub fn new(status: ServiceStatus, detail: impl Into<Option<String>>) -> Self {
        Health { status, detail: detail.into() }
    }
    pub fn running() -> Self {
        Self::new(ServiceStatus::Running, None)
    }
}

/// One output of the performer to one piece of hardware.
pub trait Driver: Send {
    /// Service name for health (`driver.face`, ...).
    fn name(&self) -> &str;
    /// Profile outputs this driver serves (light group or actuator names), for stage enables.
    fn outputs(&self) -> Vec<String> {
        vec![]
    }
    /// Connect (may block for seconds; runs on the driver's thread). Must fail open.
    fn start(&mut self, _now: f64) {}
    /// Output enable gate.
    fn set_enabled(&mut self, on: bool, now: f64);
    /// An event-time output of the performer. Drivers ignore what is not theirs.
    fn on_out(&mut self, _out: &Out, _now: f64) {}
    /// A performer frame (dumb sinks and the virtual driver).
    fn on_frames(&mut self, _frames: &Frames, _now: f64) {}
    /// Timers: rate gates, heartbeats, telemetry reads.
    fn poll(&mut self, _now: f64) {}
    /// When `poll` next has something to do (monotonic seconds).
    fn next_poll(&self, now: f64) -> f64 {
        now + 0.1
    }
    fn health(&self) -> Health;
    /// A bus event to publish (telemetry), drained after every `poll`.
    fn take_event(&mut self) -> Option<r3x_contracts::Event> {
        None
    }
    /// Leave the hardware in a sane state.
    fn stop(&mut self, _now: f64) {}
}

/// `true`/`1`/`yes`/`on` (case-insensitive).
pub(crate) fn env_flag(name: &str) -> bool {
    std::env::var(name).is_ok_and(|v| env_flag_value(&v))
}

pub(crate) fn env_flag_value(v: &str) -> bool {
    matches!(v.trim().to_lowercase().as_str(), "1" | "true" | "yes" | "on")
}

pub(crate) fn env_str(name: &str) -> Option<String> {
    std::env::var(name).ok().map(|s| s.trim().to_string()).filter(|s| !s.is_empty())
}
