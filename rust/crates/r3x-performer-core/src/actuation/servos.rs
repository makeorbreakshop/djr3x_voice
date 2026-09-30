//! Hobby servo datasheet values (port of `servos.ts`). Sources in sim/docs/motion-control.md;
//! backlash and tau are estimates until bench-tested.

#[derive(Clone, Debug, PartialEq)]
pub struct ServoModel {
    pub label: &'static str,
    /// Mechanical travel for the full pulse range.
    pub range_deg: f64,
    pub min_us: f64,
    pub max_us: f64,
    /// (volts, s per 60 deg) no load, at the two rated voltages.
    pub speed: [(f64, f64); 2],
    /// (volts, stall kg.cm) at the same voltages.
    pub stall: [(f64, f64); 2],
    pub deadband_us: f64,
    /// Gear-train play at the horn, deg (measure).
    pub backlash_deg: f64,
    /// Small-signal time constant of the internal P loop, s (measure).
    pub tau_s: f64,
}

#[allow(clippy::too_many_arguments)]
const fn m(
    label: &'static str,
    range_deg: f64,
    max_us: f64,
    speed: [(f64, f64); 2],
    stall: [(f64, f64); 2],
    deadband_us: f64,
    backlash_deg: f64,
    tau_s: f64,
) -> ServoModel {
    ServoModel {
        label,
        range_deg,
        min_us: 500.0,
        max_us,
        speed,
        stall,
        deadband_us,
        backlash_deg,
        tau_s,
    }
}

pub fn servo(name: &str) -> Option<ServoModel> {
    Some(match name {
        "MG996R" => m(
            "MG996R",
            180.0,
            2500.0,
            [(4.8, 0.17), (6.0, 0.14)],
            [(4.8, 9.4), (6.0, 11.0)],
            5.0,
            1.0,
            0.05,
        ),
        "DS3218" => m(
            "DS3218 (180)",
            180.0,
            2500.0,
            [(5.0, 0.16), (6.8, 0.14)],
            [(5.0, 19.0), (6.8, 21.5)],
            3.0,
            0.5,
            0.04,
        ),
        "DS3218_270" => m(
            "DS3218 (270)",
            270.0,
            2500.0,
            [(5.0, 0.16), (6.8, 0.14)],
            [(5.0, 19.0), (6.8, 21.5)],
            3.0,
            0.5,
            0.04,
        ),
        "MG90S" => m(
            "MG90S",
            180.0,
            2400.0,
            [(4.8, 0.1), (6.0, 0.08)],
            [(4.8, 1.8), (6.0, 2.2)],
            5.0,
            1.0,
            0.03,
        ),
        // Classes from the R-3X Animation parts list; typical figures for the class.
        "SERVO_35KG_270" => m(
            "35 kg 270deg",
            270.0,
            2500.0,
            [(5.0, 0.16), (7.4, 0.11)],
            [(5.0, 25.0), (7.4, 35.0)],
            3.0,
            0.6,
            0.045,
        ),
        "SERVO_60KG_270" => m(
            "60 kg 270deg",
            270.0,
            2500.0,
            [(6.0, 0.2), (8.4, 0.15)],
            [(6.0, 50.0), (8.4, 60.0)],
            3.0,
            0.8,
            0.06,
        ),
        "DS3218_DUAL" => m(
            "20 kg dual-shaft (DS3218-class)",
            180.0,
            2500.0,
            [(5.0, 0.16), (6.8, 0.14)],
            [(5.0, 19.0), (6.8, 21.5)],
            3.0,
            0.5,
            0.04,
        ),
        "SERVO_7KG" => m(
            "7 kg",
            180.0,
            2500.0,
            [(4.8, 0.12), (6.0, 0.1)],
            [(4.8, 6.0), (6.0, 7.0)],
            4.0,
            1.0,
            0.035,
        ),
        // Hunter's head mech (tilt + roll push-rod pair): 25.2 kg.cm stall at 6 V, 500-2500 us
        // over ~300 deg (goBILDA 2000-0025-0002 catalogue); the 4.8 V point is scaled.
        "GOBILDA_2000_25_2" => m(
            "goBILDA 2000 (25-2 torque)",
            300.0,
            2500.0,
            [(4.8, 0.25), (6.0, 0.2)],
            [(4.8, 20.2), (6.0, 25.2)],
            3.0,
            0.6,
            0.045,
        ),
        // Claimed 0.1 s/60; ServoEasing measured ~450 deg/s.
        "SG90" => m(
            "SG90",
            180.0,
            2400.0,
            [(4.8, 0.133), (6.0, 0.12)],
            [(4.8, 1.8), (6.0, 1.8)],
            10.0,
            1.5,
            0.03,
        ),
        _ => return None,
    })
}

fn interp(t: &[(f64, f64); 2], v: f64) -> f64 {
    let (a, b) = (t[0], t[1]);
    let k = ((v - a.0) / (b.0 - a.0)).clamp(0.0, 1.0);
    a.1 + (b.1 - a.1) * k
}

impl ServoModel {
    /// No-load speed, deg/s, at supply volts.
    pub fn no_load_speed(&self, volts: f64) -> f64 {
        60.0 / interp(&self.speed, volts)
    }
    /// Stall torque, kg.cm, at supply volts.
    pub fn stall_torque(&self, volts: f64) -> f64 {
        interp(&self.stall, volts)
    }
    pub fn us_per_deg(&self) -> f64 {
        (self.max_us - self.min_us) / self.range_deg
    }
}
