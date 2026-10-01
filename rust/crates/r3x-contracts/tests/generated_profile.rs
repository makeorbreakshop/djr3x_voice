//! `profiles/r3x/robot.generated.json` (written by `mech/.venv/bin/python -m rigsync` from the
//! mech model) must parse and validate as a Robot Profile, so `R3X_PROFILE` can point at it
//! and `rigsync --apply` can never write a profile the runtime refuses. Its `mech` block is an
//! extension the contract ignores.

use std::path::Path;

use r3x_contracts::profile::RobotProfile;

#[test]
fn generated_profile_validates() {
    let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../profiles/r3x");
    let path = dir.join("robot.generated.json");
    if !path.exists() {
        eprintln!("{} not generated; skipping", path.display());
        return;
    }
    let generated = RobotProfile::load(&path).unwrap_or_else(|e| panic!("{}: {e}", path.display()));
    let live = RobotProfile::load(dir.join("robot.json")).unwrap();
    // Same joints and actuators by name: the generator re-derives values, never the set.
    let names = |p: &RobotProfile| p.joints.iter().map(|j| j.name.clone()).collect::<Vec<_>>();
    assert_eq!(names(&generated), names(&live));
    let acts = |p: &RobotProfile| p.actuators.iter().map(|a| (a.name.clone(), a.channel)).collect::<Vec<_>>();
    assert_eq!(acts(&generated), acts(&live));
    // The mech model stands the head on the base, not the rings: the central column's lift
    // carriage carries the pan (head_lift on the base, head_pan on the lift).
    assert_eq!(generated.joint("head_lift").unwrap().parent, None);
    assert_eq!(generated.joint("head_pan").unwrap().parent.as_deref(), Some("head_lift"));
    let tilt = generated.joint("head_tilt").unwrap().parent.clone();
    assert_eq!(tilt.as_deref(), Some("head_pan"), "the head's gimbal rides the pan");
}
