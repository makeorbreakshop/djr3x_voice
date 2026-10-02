//! Bench calibration wizard, the write side (plan §9 Phase 8): a measured centre pulse,
//! direction and (optionally) the pulses at the two soft limits go into one actuator of
//! `profiles/<name>/robot.json`, marked `calibrated: measured`. The document is edited as
//! JSON (every other field and the key order survive), validated by
//! `RobotProfile::from_json`, and only then written (temp file + rename).

use std::path::Path;

use r3x_contracts::profile::Unit;
use r3x_contracts::RobotProfile;
use r3x_motion::Calibration;
use serde_json::{json, Value};

/// What the wizard measured for one actuator (pulses in us).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Measured {
    /// Pulse at which the primary joint sits at its `center_value`.
    pub center_us: f64,
    /// `true` when a larger pulse moves the joint negative.
    pub invert: bool,
    /// Pulses at the two ends of the soft range (either order); `None` keeps the range.
    pub limits_us: Option<(f64, f64)>,
}

/// The actuator's primary joint: its joint listed first in the profile's `joints` (the
/// performer's rule).
fn primary_joint<'a>(p: &'a RobotProfile, actuator: &str) -> Result<&'a r3x_contracts::profile::Joint, String> {
    let a = p.actuators.iter().find(|a| a.name == actuator).ok_or_else(|| format!("no actuator {actuator:?}"))?;
    p.joints.iter().find(|j| a.joints.contains_key(&j.name)).ok_or_else(|| format!("actuator {actuator} drives no joint"))
}

/// Apply `m` to `actuator` in the profile JSON text; returns the new, validated text.
pub fn apply(text: &str, actuator: &str, m: &Measured) -> Result<String, String> {
    let before = RobotProfile::from_json(text).map_err(|e| format!("current profile invalid: {e}"))?;
    let joint = primary_joint(&before, actuator)?.clone();
    let a = before.actuators.iter().find(|a| a.name == actuator).expect("found above");
    let c = &a.calibration;
    let in_range = |us: f64| us.is_finite() && us >= c.pulse_min_us && us <= c.pulse_max_us;
    if !in_range(m.center_us) {
        return Err(format!("centre {} us outside the pulse range {}..{}", m.center_us, c.pulse_min_us, c.pulse_max_us));
    }
    let cal = Calibration {
        center_us: m.center_us,
        center_value: c.center_value,
        trim_us: 0.0,
        invert: m.invert,
        gear: c.gear,
        mm_per_deg: (joint.unit == Unit::Mm).then(|| c.mm_per_deg.unwrap_or(0.2)),
        pulse_min_us: c.pulse_min_us,
        pulse_max_us: c.pulse_max_us,
        range_deg: c.range_deg,
    };

    let mut doc: Value = serde_json::from_str(text).map_err(|e| e.to_string())?;
    let acts = doc["actuators"].as_array_mut().ok_or("no actuators array")?;
    let act = acts.iter_mut().find(|v| v["name"] == actuator).ok_or("actuator vanished")?;
    let cv = act["calibration"].as_object_mut().ok_or("no calibration object")?;
    cv.insert("center_us".into(), json!(m.center_us));
    cv.insert("trim_us".into(), json!(0.0));
    cv.insert("invert".into(), json!(m.invert));
    cv.insert("status".into(), json!("measured"));

    if let Some((a_us, b_us)) = m.limits_us {
        if !in_range(a_us) || !in_range(b_us) {
            return Err("limit pulses outside the pulse range".into());
        }
        let (va, vb) = (cal.us_to_value(a_us), cal.us_to_value(b_us));
        let (lo, hi) = (va.min(vb).max(joint.hard.min), va.max(vb).min(joint.hard.max));
        if lo >= hi {
            return Err(format!("limits {lo:.2}..{hi:.2} leave no range inside hard {}..{}", joint.hard.min, joint.hard.max));
        }
        let overlaps = lo <= joint.animation.max && hi >= joint.animation.min;
        if !(overlaps && (lo..=hi).contains(&c.center_value)) {
            return Err(format!("limits {lo:.2}..{hi:.2} exclude the centre or the animation range"));
        }
        let (alo, ahi) = (joint.animation.min.max(lo), joint.animation.max.min(hi));
        let round = |x: f64| (x * 100.0).round() / 100.0; // 0.01 unit is far below one pulse
        let jv = doc["joints"]
            .as_array_mut()
            .and_then(|js| js.iter_mut().find(|j| j["name"] == joint.name.as_str()))
            .ok_or("joint vanished")?;
        jv["soft"] = json!({ "min": round(lo), "max": round(hi) });
        jv["animation"] = json!({ "min": round(alo).max(round(lo)), "max": round(ahi).min(round(hi)) });
    }

    let mut out = serde_json::to_string_pretty(&doc).map_err(|e| e.to_string())?;
    if text.ends_with('\n') {
        out.push('\n');
    }
    RobotProfile::from_json(&out).map_err(|e| format!("calibration rejected by the profile validator: {e}"))?;
    Ok(out)
}

/// Read, apply, validate, then replace `path` atomically. Returns the new profile.
pub fn write(path: &Path, actuator: &str, m: &Measured) -> Result<RobotProfile, String> {
    let text = std::fs::read_to_string(path).map_err(|e| format!("{}: {e}", path.display()))?;
    let out = apply(&text, actuator, m)?;
    let tmp = path.with_extension("json.tmp");
    std::fs::write(&tmp, &out).map_err(|e| format!("{}: {e}", tmp.display()))?;
    std::fs::rename(&tmp, path).map_err(|e| format!("{}: {e}", path.display()))?;
    RobotProfile::from_json(&out).map_err(|e| e.to_string())
}

/// Set one actuator's trim (us added to its centre pulse), keeping everything else: how a pair of
/// servos on one joint is matched after each is centred (Hunter's visor, `visor_l` / `visor_r`: trim
/// one until the two stop fighting). The trimmed centre must stay inside the pulse range.
pub fn apply_trim(text: &str, actuator: &str, trim_us: f64) -> Result<String, String> {
    let before = RobotProfile::from_json(text).map_err(|e| format!("current profile invalid: {e}"))?;
    let a = before.actuators.iter().find(|a| a.name == actuator).ok_or_else(|| format!("no actuator {actuator:?}"))?;
    let c = &a.calibration;
    let at = c.center_us + trim_us;
    if !trim_us.is_finite() || at < c.pulse_min_us || at > c.pulse_max_us {
        return Err(format!("trim {trim_us} us puts the centre at {at} us, outside {}..{}", c.pulse_min_us, c.pulse_max_us));
    }
    let mut doc: Value = serde_json::from_str(text).map_err(|e| e.to_string())?;
    let act = doc["actuators"]
        .as_array_mut()
        .and_then(|acts| acts.iter_mut().find(|v| v["name"] == actuator))
        .ok_or("actuator vanished")?;
    act["calibration"]["trim_us"] = json!(trim_us);
    let out = serde_json::to_string_pretty(&doc).map_err(|e| e.to_string())? + "\n";
    RobotProfile::from_json(&out).map_err(|e| format!("trimmed profile invalid: {e}"))?;
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;
    use r3x_contracts::profile::CalibrationStatus;

    #[test]
    fn trims_one_servo_of_the_visor_pair() {
        let path = concat!(env!("CARGO_MANIFEST_DIR"), "/../../../profiles/r3x/robot.generated.json");
        let t = std::fs::read_to_string(path).unwrap();
        let out = apply_trim(&t, "visor_r", -6.5).unwrap();
        let p = RobotProfile::from_json(&out).unwrap();
        let get = |n: &str| p.actuators.iter().find(|a| a.name == n).unwrap().calibration.clone();
        assert_eq!((get("visor_r").trim_us, get("visor_l").trim_us), (-6.5, 0.0));
        assert!(apply_trim(&t, "visor_r", 5000.0).unwrap_err().contains("outside"));
        assert!(apply_trim(&t, "nope", 1.0).unwrap_err().contains("no actuator"));
    }

    const PATH: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/../../../profiles/r3x/robot.json");

    fn text() -> String {
        std::fs::read_to_string(PATH).unwrap()
    }

    #[test]
    fn untouched_round_trip_is_byte_identical() {
        // So a calibration write diffs only the fields it changed.
        let t = text();
        let v: Value = serde_json::from_str(&t).unwrap();
        assert_eq!(serde_json::to_string_pretty(&v).unwrap(), t.trim_end_matches('\n'));
    }

    #[test]
    fn writes_measured_centre_direction_and_limits() {
        let dir = std::env::temp_dir().join(format!("r3x-cal-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("robot.json");
        std::fs::write(&path, text()).unwrap();

        // The neck: 1.5 servo deg per joint deg, 2000 us over 270 deg -> ~11.1 us per joint deg.
        let m = Measured { center_us: 1502.0, invert: true, limits_us: Some((1900.0, 1100.0)) };
        let p = write(&path, "neck", &m).unwrap();
        let a = p.actuators.iter().find(|a| a.name == "neck").unwrap();
        assert_eq!(a.calibration.status, CalibrationStatus::Measured);
        assert_eq!((a.calibration.center_us, a.calibration.trim_us, a.calibration.invert), (1502.0, 0.0, true));
        let j = p.joint("head_pan").unwrap();
        // Inverted: 1900 us is the negative end. (1900 - 1502) / (2000/270) / 1.5 = 35.82 deg.
        assert_eq!((j.soft.min, j.soft.max), (-35.82, 36.18));
        assert!(j.animation.min >= j.soft.min && j.animation.max <= j.soft.max);
        // Everything else untouched.
        let before = RobotProfile::from_json(&text()).unwrap();
        assert_eq!(p.actuators.iter().filter(|x| x.calibration != before.actuators.iter().find(|b| b.name == x.name).unwrap().calibration).count(), 1);
        assert_eq!(p.lights, before.lights);
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn invalid_measurements_never_reach_the_file() {
        let t = text();
        let bad = |m: Measured| apply(&t, "neck", &m).unwrap_err();
        assert!(bad(Measured { center_us: 3000.0, invert: false, limits_us: None }).contains("pulse range"));
        assert!(bad(Measured { center_us: 1500.0, invert: false, limits_us: Some((1600.0, 1700.0)) }).contains("exclude"));
        assert!(apply(&t, "nope", &Measured { center_us: 1500.0, invert: false, limits_us: None }).unwrap_err().contains("no actuator"));
        // Centre only: the soft range stays.
        let out = apply(&t, "headlift", &Measured { center_us: 1480.0, invert: false, limits_us: None }).unwrap();
        let (p0, p1) = (RobotProfile::from_json(&t).unwrap(), RobotProfile::from_json(&out).unwrap());
        assert_eq!(p0.joint("head_lift"), p1.joint("head_lift"));
    }
}
