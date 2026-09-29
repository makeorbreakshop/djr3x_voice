//! Per-channel servo calibration: joint value (deg, or mm for a prismatic joint) <-> pulse
//! width. The arithmetic (and its order) is `r3x-performer-core`'s `Channel::value_to_us`
//! so the controller and the sim produce the same pulse; a performer test pins that.

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Calibration {
    /// Pulse at which the joint sits at `center_value`.
    pub center_us: f64,
    pub center_value: f64,
    pub trim_us: f64,
    pub invert: bool,
    /// Servo degrees per joint degree (revolute).
    pub gear: f64,
    /// Prismatic: mm of travel per servo degree; `None` = revolute.
    pub mm_per_deg: Option<f64>,
    pub pulse_min_us: f64,
    pub pulse_max_us: f64,
    /// Servo travel over the full pulse range.
    pub range_deg: f64,
}

impl Calibration {
    pub fn us_per_deg(&self) -> f64 {
        (self.pulse_max_us - self.pulse_min_us) / self.range_deg
    }

    fn dir(&self) -> f64 {
        if self.invert {
            -1.0
        } else {
            1.0
        }
    }

    /// Joint value -> servo degrees from the centre pulse.
    pub fn value_to_servo(&self, v: f64) -> f64 {
        let d = v - self.center_value;
        self.dir()
            * match self.mm_per_deg {
                Some(mm) => d / mm,
                None => d * self.gear,
            }
    }

    pub fn servo_to_value(&self, servo_deg: f64) -> f64 {
        let s = servo_deg * self.dir();
        self.center_value
            + match self.mm_per_deg {
                Some(mm) => s * mm,
                None => s / self.gear,
            }
    }

    /// Joint value -> pulse width (us), clamped to the servo's pulse range.
    pub fn value_to_us(&self, v: f64) -> f64 {
        let us = self.center_us + self.trim_us + self.value_to_servo(v) * self.us_per_deg();
        us.clamp(self.pulse_min_us, self.pulse_max_us)
    }

    /// Pulse width -> joint value (inverse of [`Self::value_to_us`] inside the pulse range).
    pub fn us_to_value(&self, us: f64) -> f64 {
        self.servo_to_value((us - self.center_us - self.trim_us) / self.us_per_deg())
    }

    /// The joint range the servo can reach, `(lo, hi)`.
    pub fn reach(&self) -> (f64, f64) {
        let (a, b) = (self.us_to_value(self.pulse_min_us), self.us_to_value(self.pulse_max_us));
        (a.min(b), a.max(b))
    }
}

/// Whole-microsecond pulse, rounded like the performer's controller frame (`floor(x + 0.5)`).
pub fn pulse_us(us: f64) -> u16 {
    let r = libm::floor(us + 0.5);
    if r <= 0.0 {
        0
    } else if r >= u16::MAX as f64 {
        u16::MAX
    } else {
        r as u16
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn neck() -> Calibration {
        Calibration {
            center_us: 1490.0,
            center_value: 0.0,
            trim_us: 0.0,
            invert: false,
            gear: 1.5,
            mm_per_deg: None,
            pulse_min_us: 500.0,
            pulse_max_us: 2500.0,
            range_deg: 270.0,
        }
    }

    #[test]
    fn round_trip_invert_prismatic_and_clamp() {
        let c = neck();
        assert!((c.us_to_value(c.value_to_us(20.0)) - 20.0).abs() < 1e-9);
        let inv = Calibration { invert: true, ..c };
        assert!(inv.value_to_us(20.0) < 1490.0 && c.value_to_us(20.0) > 1490.0);
        let lift = Calibration { mm_per_deg: Some(0.21), ..c };
        assert!((lift.us_to_value(lift.value_to_us(-7.0)) + 7.0).abs() < 1e-9);
        assert_eq!(c.value_to_us(1e6), 2500.0);
        let (lo, hi) = c.reach();
        assert!(lo < -80.0 && hi > 80.0);
        assert_eq!((pulse_us(1499.5), pulse_us(1499.49), pulse_us(-3.0)), (1500, 1499, 0));
    }
}
