//! Bench calibration wizard (plan §9 Phase 8): jog one `r3x_servo` actuator by raw pulse,
//! then write the measured centre, direction and limits into the Robot Profile
//! (`calibrated: measured`; the file write and validation are `r3x_drivers::servo::calibrate`).
//! A saved calibration reaches the controller on the next start (its CONFIG is sent on
//! connect).

use r3x_contracts::profile::DriverKind;
use r3x_contracts::{Ack, OperatingMode, PerfCommand, RobotProfile, Source};
use r3x_drivers::servo::calibrate::{self, Measured};
use r3x_drivers::DriverSet;
use r3x_performer_core::performer::Out;

pub(super) fn is_calibration(cmd: &PerfCommand) -> bool {
    matches!(cmd, PerfCommand::CalJog { .. } | PerfCommand::CalSave { .. } | PerfCommand::CalTrim { .. })
}

pub(super) fn handle(
    cmd: &PerfCommand,
    source: Source,
    mode: OperatingMode,
    profile: &RobotProfile,
    drivers: Option<&DriverSet>,
) -> Ack {
    if !matches!(source, Source::Ui | Source::Cli) {
        return Ack::rejected("calibration comes from the panel or the CLI");
    }
    if mode != OperatingMode::Bench {
        return Ack::rejected("calibration runs in Bench mode");
    }
    let servo = |name: &str| profile.actuators.iter().find(|a| a.name == name && a.driver == DriverKind::R3xServo);
    match cmd {
        PerfCommand::CalJog { actuator, us } => {
            let Some(a) = servo(actuator) else {
                return Ack::rejected(format!("{actuator} is not an r3x_servo actuator"));
            };
            let c = &a.calibration;
            if !(us.is_finite() && *us >= c.pulse_min_us && *us <= c.pulse_max_us) {
                return Ack::rejected(format!("{us} us outside {}..{}", c.pulse_min_us, c.pulse_max_us));
            }
            let Some(d) = drivers else {
                return Ack::rejected("no hardware drivers in this runtime");
            };
            d.outs(&[Out::ServoPulse { actuator: actuator.clone(), us: *us }]);
            Ack::Accepted
        }
        PerfCommand::CalSave { actuator, center_us, invert, limits_us } => {
            if servo(actuator).is_none() {
                return Ack::rejected(format!("{actuator} is not an r3x_servo actuator"));
            }
            // Only ever the profile this runtime runs (R3X_PROFILE, else the repo's).
            let path = crate::default_profile_path();
            match RobotProfile::load(&path) {
                Ok(p) if p.name == profile.name => {}
                _ => return Ack::rejected(format!("{} is not the running profile; set R3X_PROFILE", path.display())),
            }
            let m = Measured { center_us: *center_us, invert: *invert, limits_us: limits_us.map(|[a, b]| (a, b)) };
            match calibrate::write(&path, actuator, &m) {
                Ok(_) => {
                    tracing::info!(actuator, center_us, invert, ?limits_us, path = %path.display(), "calibration saved");
                    Ack::Accepted
                }
                Err(e) => Ack::rejected(e),
            }
        }
        PerfCommand::CalTrim { actuator, trim_us, save } => {
            let Some(a) = servo(actuator) else {
                return Ack::rejected(format!("{actuator} is not an r3x_servo actuator"));
            };
            let c = &a.calibration;
            let at = c.center_us + trim_us;
            if !(trim_us.is_finite() && at >= c.pulse_min_us && at <= c.pulse_max_us) {
                return Ack::rejected(format!("trim {trim_us} us puts the centre at {at} us, outside {}..{}", c.pulse_min_us, c.pulse_max_us));
            }
            if !save && drivers.is_none() {
                return Ack::rejected("no hardware drivers in this runtime");
            }
            if let Some(d) = drivers {
                d.outs(&[Out::ServoTrim { actuator: actuator.clone(), trim_us: *trim_us }]);
            }
            if *save {
                let path = crate::default_profile_path();
                match RobotProfile::load(&path) {
                    Ok(p) if p.name == profile.name => {}
                    _ => return Ack::rejected(format!("{} is not the running profile; set R3X_PROFILE", path.display())),
                }
                if let Err(e) = calibrate::write_trim(&path, actuator, *trim_us) {
                    return Ack::rejected(e);
                }
                tracing::info!(actuator, trim_us, path = %path.display(), "trim saved");
            }
            Ack::Accepted
        }
        _ => Ack::rejected("not a calibration command"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn profile() -> RobotProfile {
        RobotProfile::load(crate::default_profile_path()).unwrap()
    }

    #[test]
    fn gates_source_mode_actuator_and_range() {
        let p = profile();
        let jog = |a: &str, us| PerfCommand::CalJog { actuator: a.into(), us };
        let reason = |ack: Ack| match ack {
            Ack::Rejected { reason } => reason,
            Ack::Accepted => "accepted".into(),
        };
        assert!(reason(handle(&jog("neck", 1500.0), Source::Claude, OperatingMode::Bench, &p, None)).contains("panel"));
        assert!(reason(handle(&jog("neck", 1500.0), Source::Ui, OperatingMode::Show, &p, None)).contains("Bench"));
        assert!(reason(handle(&jog("eyes", 1500.0), Source::Ui, OperatingMode::Bench, &p, None)).contains("not an r3x_servo"));
        assert!(reason(handle(&jog("neck", 9000.0), Source::Ui, OperatingMode::Bench, &p, None)).contains("outside"));
        assert!(reason(handle(&jog("neck", 1500.0), Source::Cli, OperatingMode::Bench, &p, None)).contains("no hardware"));
        // A bad save is rejected before anything is written.
        let save = PerfCommand::CalSave { actuator: "neck".into(), center_us: 99.0, invert: false, limits_us: None };
        assert!(reason(handle(&save, Source::Ui, OperatingMode::Bench, &p, None)).contains("pulse range"));
        // trims: the same gates, and the trimmed centre inside the pulse range
        let trim = |a: &str, t, save| PerfCommand::CalTrim { actuator: a.into(), trim_us: t, save };
        assert!(reason(handle(&trim("neck", 5.0, false), Source::Claude, OperatingMode::Bench, &p, None)).contains("panel"));
        assert!(reason(handle(&trim("neck", 5.0, false), Source::Ui, OperatingMode::Show, &p, None)).contains("Bench"));
        assert!(reason(handle(&trim("eyes", 5.0, false), Source::Ui, OperatingMode::Bench, &p, None)).contains("not an r3x_servo"));
        assert!(reason(handle(&trim("neck", 5000.0, false), Source::Ui, OperatingMode::Bench, &p, None)).contains("outside"));
        assert!(reason(handle(&trim("neck", 5.0, false), Source::Ui, OperatingMode::Bench, &p, None)).contains("no hardware"));
    }
}
