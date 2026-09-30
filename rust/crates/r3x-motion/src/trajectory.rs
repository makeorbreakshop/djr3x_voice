//! Online jerk-limited trajectory following with soft-limit braking (port of
//! `trajectory.ts`). `no_std`-friendly (core + libm only): the same module runs on the
//! servo controller firmware (D6, `firmware/servo`) and on the host for dumb sinks.
//!
//! Construction (every limit provable, not approximate):
//!   1. an exact trapezoidal follower (v <= vMax, |a| <= aMax, braking v = sqrt(2 a d)
//!      toward the target and toward each soft limit), then
//!   2. a critically damped second-order filter on its output with
//!      omega = e * jMax / (2 aMax), which bounds jerk at jMax (a lag of ~2/omega).

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct MotionLimits {
    /// units/s (deg or mm)
    pub v_max: f64,
    /// units/s^2
    pub a_max: f64,
    /// units/s^3
    pub j_max: f64,
}

fn clamp(x: f64, lo: f64, hi: f64) -> f64 {
    if x < lo {
        lo
    } else if x > hi {
        hi
    } else {
        x
    }
}

fn sign(x: f64) -> f64 {
    if x > 0.0 {
        1.0
    } else if x < 0.0 {
        -1.0
    } else {
        0.0
    }
}

/// Speed from which you can still stop within distance d at deceleration a.
pub fn brake_speed(d: f64, a: f64) -> f64 {
    libm::sqrt(2.0 * a * d.max(0.0))
}

#[derive(Clone, Debug, PartialEq)]
pub struct JerkLimitedFollower {
    pub limits: MotionLimits,
    pub soft_min: f64,
    pub soft_max: f64,
    /// Filtered output: what goes to the output stage.
    pub x: f64,
    pub v: f64,
    pub a: f64,
    pub target: f64,
    // stage 1: trapezoid
    x1: f64,
    v1: f64,
}

impl JerkLimitedFollower {
    pub fn new(limits: MotionLimits, soft_min: f64, soft_max: f64, x0: f64) -> Self {
        let x = clamp(x0, soft_min, soft_max);
        JerkLimitedFollower {
            limits,
            soft_min,
            soft_max,
            x,
            v: 0.0,
            a: 0.0,
            target: x,
            x1: x,
            v1: 0.0,
        }
    }

    pub fn set_target(&mut self, t: f64) {
        self.target = clamp(t, self.soft_min, self.soft_max);
    }

    /// Jump the whole state to a position at rest (e.g. when a script hands back control).
    pub fn reset(&mut self, x: f64) {
        let x = clamp(x, self.soft_min, self.soft_max);
        self.x = x;
        self.x1 = x;
        self.target = x;
        self.v = 0.0;
        self.v1 = 0.0;
        self.a = 0.0;
    }

    /// Ramp to hold: aim at the point where the trapezoid stage stops if it brakes now at
    /// `a_max` (so the joint decelerates without reversing), inside the soft limits.
    /// A target already between here and that point (braking into it) is kept.
    pub fn hold(&mut self) {
        let stop = self.x1 + self.v1 * self.v1.abs() / (2.0 * self.limits.a_max);
        let (to_target, to_stop) = (self.target - self.x1, stop - self.x1);
        if !(to_target * to_stop > 0.0 && to_target.abs() <= to_stop.abs()) {
            self.set_target(stop);
        }
    }

    /// Speed of the trapezoid stage (the filtered output lags it by ~2/omega).
    pub fn stage1_velocity(&self) -> f64 {
        self.v1
    }

    pub fn step(&mut self, dt: f64) {
        let MotionLimits {
            v_max,
            a_max,
            j_max,
        } = self.limits;

        // Stage 1: exact trapezoid, braking for the target and for both soft limits.
        let e = self.target - self.x1;
        let mut v_des = sign(e) * v_max.min(brake_speed(e.abs(), a_max));
        v_des = v_des.min(brake_speed(self.soft_max - self.x1, a_max));
        v_des = v_des.max(-brake_speed(self.x1 - self.soft_min, a_max));
        self.v1 += clamp(v_des - self.v1, -a_max * dt, a_max * dt);
        self.x1 += self.v1 * dt;
        if (self.target - self.x1).abs() < 1e-3 && self.v1.abs() < a_max * dt {
            self.x1 = self.target;
            self.v1 = 0.0;
        }
        self.x1 = clamp(self.x1, self.soft_min, self.soft_max);

        // Stage 2: critically damped filter (exact update, stable at any dt).
        let omega = (core::f64::consts::E * j_max) / (2.0 * a_max);
        let v_prev = self.v;
        let (x, v) = spring_step(self.x, self.v, self.x1, omega, dt);
        self.x = clamp(x, self.soft_min, self.soft_max);
        self.v = v;
        self.a = (self.v - v_prev) / dt;
    }
}

/// Critically damped spring, exact for any dt (Holden, "Spring-It-On"). Returns (x, v).
pub fn spring_step(x: f64, v: f64, goal: f64, omega: f64, dt: f64) -> (f64, f64) {
    let j0 = x - goal;
    let j1 = v + j0 * omega;
    let e = libm::exp(-omega * dt);
    (e * (j0 + j1 * dt) + goal, e * (v - j1 * omega * dt))
}

/// Minimum-jerk (Flash & Hogan) duration that respects all three limits for distance D.
pub fn min_jerk_duration(d: f64, l: &MotionLimits) -> f64 {
    let d = d.abs();
    (1.875 * d / l.v_max)
        .max(libm::sqrt(5.77 * d / l.a_max))
        .max(libm::cbrt(60.0 * d / l.j_max))
}
