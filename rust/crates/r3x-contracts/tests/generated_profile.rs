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
    // Same actuators by name and channel, except a joint the mech model drives with several servos
    // at once (Hunter's visor, one each side): its actuator becomes `<name>_<side>` per servo, the
    // first on the original channel, the others on new ones (rigsync's _split_actuators).
    let (mut split, mut bases) = (Vec::new(), 0);
    for a in &live.actuators {
        match generated.actuators.iter().find(|g| g.name == a.name) {
            Some(g) => assert_eq!(g.channel, a.channel, "{}", a.name),
            None => {
                let parts: Vec<_> = generated.actuators.iter().filter(|g| g.name.starts_with(&format!("{}_", a.name))).collect();
                assert!(parts.len() >= 2, "{}: neither kept nor split", a.name);
                assert!(parts.iter().all(|g| g.joints == a.joints), "{}: the split drives the same joint", a.name);
                assert_eq!(parts[0].channel, a.channel, "{}: the first keeps the channel", a.name);
                split.extend(parts.iter().map(|g| g.name.clone()));
                bases += 1;
            }
        }
    }
    assert_eq!(generated.actuators.len(), live.actuators.len() - bases + split.len());
    // The mech model stands the head on the base, not the rings: the central column's lift
    // carriage carries the pan (head_lift on the base, head_pan on the lift).
    assert_eq!(generated.joint("head_lift").unwrap().parent, None);
    assert_eq!(generated.joint("head_pan").unwrap().parent.as_deref(), Some("head_lift"));
    let tilt = generated.joint("head_tilt").unwrap().parent.clone();
    assert_eq!(tilt.as_deref(), Some("head_pan"), "the head's gimbal rides the pan");
}
