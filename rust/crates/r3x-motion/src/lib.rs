//! Motion primitives shared by the host and the servo controller firmware (plan D6,
//! `sim/docs/motion-control.md` §3-4, §8). `no_std`, no alloc: `core` + `libm` only.
//!
//! - [`trajectory`]: the two-stage jerk-limited follower with soft-limit braking
//!   `v <= sqrt(2 a d)`.
//! - [`calib`]: joint value <-> pulse width, as the performer's output stage computes it.
#![no_std]

pub mod calib;
pub mod trajectory;

pub use calib::Calibration;
pub use trajectory::{brake_speed, min_jerk_duration, spring_step, JerkLimitedFollower, MotionLimits};
