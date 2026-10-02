//! Port of `sim/web/test/actuation.test.ts`.

use r3x_performer_core::actuation::maestro::{compile_maestro, MaestroHost, MaestroScript};
use r3x_performer_core::actuation::pipeline::{servo_map, Actuation, JointSpec, JointType};
use r3x_performer_core::actuation::trajectory::{
    brake_speed, min_jerk_duration, JerkLimitedFollower, MotionLimits,
};
use r3x_performer_core::rng::Rng;
use std::collections::BTreeMap;

/// Actuation.update caps each call at 0.1 s (background-tab guard), so step in frames.
fn run(a: &mut Actuation, seconds: f64) {
    let mut t = 0.0;
    while t < seconds - 1e-9 {
        a.update(0.02);
        t += 0.02;
    }
}

fn j(name: &str, min: f64, max: f64, kind: JointType) -> JointSpec {
    JointSpec {
        name: name.into(),
        min,
        max,
        kind,
    }
}

fn joints() -> Vec<JointSpec> {
    use JointType::*;
    vec![
        j("head_pan", -70.0, 70.0, Revolute),
        j("head_lift", -20.0, 20.0, Prismatic),
        j("head_tilt", -20.0, 25.0, Revolute),
        j("head_roll", -12.0, 12.0, Revolute),
        j("visor", -15.0, 30.0, Revolute),
        j("hero_shoulder", -35.0, 45.0, Revolute),
        j("hero_wrist", -90.0, 90.0, Revolute),
        j("torso_lower", -60.0, 60.0, Revolute),
        j("torso_top", -45.0, 45.0, Revolute),
    ]
}

fn actuation() -> Actuation {
    Actuation::new(
        &joints(),
        &BTreeMap::new(),
        &servo_map(),
        "r3x_animation",
        Rng::new(7),
    )
    .unwrap()
}

const HEAD: MotionLimits = MotionLimits {
    v_max: 150.0,
    a_max: 600.0,
    j_max: 4000.0,
};

#[test]
fn follower_reaches_the_target_without_exceeding_v_a_limits() {
    let mut f = JerkLimitedFollower::new(HEAD, -66.0, 66.0, 0.0);
    f.set_target(60.0);
    let (mut vp, mut ap, mut xp) = (0f64, 0f64, 0f64);
    for _ in 0..600 {
        f.step(0.005);
        vp = vp.max(f.v.abs());
        ap = ap.max(f.a.abs());
        xp = xp.max(f.x);
    }
    assert!(xp < 60.5); // overshoot stays a fraction of a degree
    assert!((f.x - 60.0).abs() < 0.05);
    assert!(vp <= 150.0 + 1e-9);
    assert!(ap <= 600.0 + 1e-9);
}

#[test]
fn follower_clamps_targets_to_the_soft_range_and_never_passes_a_soft_limit() {
    let mut f = JerkLimitedFollower::new(
        MotionLimits {
            v_max: 300.0,
            a_max: 2000.0,
            j_max: 15000.0,
        },
        -10.0,
        10.0,
        0.0,
    );
    f.set_target(500.0);
    let mut x_max = f64::NEG_INFINITY;
    for _ in 0..400 {
        f.step(0.005);
        x_max = x_max.max(f.x);
    }
    assert_eq!(f.target, 10.0);
    assert!(x_max <= 10.0);
}

#[test]
fn brake_speed_is_zero_at_zero_distance_and_grows_with_distance() {
    assert!(brake_speed(0.0, 600.0).abs() < 1e-9);
    assert!(brake_speed(10.0, 600.0) > brake_speed(1.0, 600.0));
}

#[test]
fn follower_keeps_jerk_within_j_max() {
    let mut f = JerkLimitedFollower::new(HEAD, -66.0, 66.0, 0.0);
    f.set_target(60.0);
    let (mut a_prev, mut jp) = (0f64, 0f64);
    for _ in 0..600 {
        f.step(0.005);
        jp = jp.max((f.a - a_prev).abs() / 0.005);
        a_prev = f.a;
    }
    assert!(jp <= 4000.0 * 1.05); // discrete-time slack
}

#[test]
fn min_jerk_duration_matches_the_worked_example() {
    assert!((min_jerk_duration(90.0, &HEAD) - 1.125).abs() < 0.005); // 90 deg head pan ~1.13 s
}

#[test]
fn pipeline_keeps_the_channel_numbers_from_the_show_script() {
    let a = actuation();
    let by_name: BTreeMap<_, _> = a
        .channels
        .iter()
        .map(|c| (c.cfg.name.as_str(), c.cfg.ch))
        .collect();
    let want: BTreeMap<_, _> = [
        ("neck", 0),
        ("headlift", 1),
        ("headtilt", 2),
        ("visor", 3),
        ("elbow", 4),
        ("hand", 5),
        ("lowarm", 6),
        ("heroarm", 7),
        ("headroll", 17),
    ]
    .into_iter()
    .collect();
    assert_eq!(by_name, want);
}

#[test]
fn pipeline_emits_whole_microsecond_frames_at_50_hz_on_the_custom_controller() {
    let mut a = actuation();
    run(&mut a, 1.0);
    assert!((49..=50).contains(&a.frame_no), "{}", a.frame_no);
    assert_eq!(a.us_per_unit, 1.0);
    // Rest pose: head lift centre pulse 1500 us.
    assert_eq!(a.last_frame.targets[1], 1500);
}

#[test]
fn pipeline_maps_the_head_lift_in_mm_through_the_rack_and_pinion() {
    let mut a = actuation();
    let lift = a.channel_for("head_lift").unwrap();
    // 0.21 mm per servo degree on a 270 deg servo (7.41 us/deg): +10 mm = +352.8 us.
    assert!((lift.value_to_us(10.0) - (1500.0 + (10.0 / 0.21) * (2000.0 / 270.0))).abs() < 1e-3);
    a.command("head_lift", 10.0);
    run(&mut a, 3.0);
    assert!((a.channel_for("head_lift").unwrap().value() - 10.0).abs() < 0.5);
}

#[test]
fn pipeline_a_script_target_overrides_the_follower_and_moves_the_servo() {
    let mut a = actuation();
    a.set_target(1, 8000.0); // 2000 us, as in the sample show
    run(&mut a, 1.5);
    let lift = a.channel(1).unwrap();
    assert_eq!(lift.us, 2000.0);
    assert!(lift.value() > 13.0);
    a.release_all();
    let lift = a.channel(1).unwrap();
    assert!(lift.direct_us.is_none());
    assert!((lift.follower.x - lift.servo_to_value(lift.us_to_servo(2000.0))).abs() < 1e-3);
}

#[test]
fn pipeline_the_servo_plant_lags_the_command() {
    let mut a = actuation();
    a.set_target(0, 8832.0); // neck to one extreme
    run(&mut a, 0.1);
    let neck = a.channel(0).unwrap();
    let settled = neck.servo_to_value(neck.us_to_servo(2208.0));
    assert!(neck.value().abs() < settled.abs());
}

#[derive(Default)]
struct Rec(Vec<(i64, i64)>);
impl MaestroHost for Rec {
    fn set_target(&mut self, ch: i64, v: i64) -> bool {
        self.0.push((ch, v));
        true
    }
    fn set_speed(&mut self, _: i64, _: i64) {}
    fn set_accel(&mut self, _: i64, _: i64) {}
    fn get_position(&mut self, _: i64) -> i64 {
        0
    }
    fn any_moving(&mut self) -> bool {
        false
    }
}

const SHOW: &str = "
  # headlift
  begin
    6400 headlift
    4956 visor
    500 delay
    8000 headlift
    5297 visor
    500 delay
  repeat
  sub headlift
    1 servo
    return
  sub visor
    3 servo
    return";

#[test]
fn maestro_runs_subroutine_calls_servo_and_delay_on_the_sim_clock() {
    let mut s = MaestroScript::new(SHOW).unwrap();
    let mut h = Rec::default();
    s.run(0.0, &mut h).unwrap();
    assert_eq!(h.0, vec![(1, 6400), (3, 4956)]);
    s.run(499.0, &mut h).unwrap();
    assert_eq!(h.0.len(), 2);
    s.run(500.0, &mut h).unwrap();
    assert_eq!(h.0[2..], [(1, 8000), (3, 5297)]);
    s.run(1000.0, &mut h).unwrap();
    assert_eq!(h.0[4..], [(1, 6400), (3, 4956)]); // repeat loops
}

#[test]
fn maestro_supports_if_else_and_arithmetic() {
    let mut s =
        MaestroScript::new("3 4 plus 7 equals if 1000 else 2000 endif 0 servo quit").unwrap();
    let mut h = Rec::default();
    s.run(0.0, &mut h).unwrap();
    assert_eq!(h.0.iter().map(|x| x.1).collect::<Vec<_>>(), vec![1000]);
    assert!(!s.running);
}

#[test]
fn maestro_reports_unsupported_commands_with_their_line() {
    let e = compile_maestro("1 2 servo\nfrobnicate").unwrap_err();
    assert!(e.0.contains("line 2"), "{e}");
}

#[test]
fn maestro_drives_the_pipeline_directly() {
    let mut a = actuation();
    let mut s = MaestroScript::new(SHOW).unwrap();
    s.run(0.0, &mut a).unwrap();
    assert_eq!(a.channel(1).unwrap().direct_us, Some(1600.0));
}

/// The servo controller firmware computes pulses with `r3x_motion::Calibration`; it must
/// agree bit for bit with the performer's output stage for every profile channel.
#[test]
fn firmware_calibration_matches_the_output_stage() {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/../../../profiles/r3x/robot.json");
    let profile = r3x_contracts::RobotProfile::load(path).unwrap();
    let act = Actuation::from_profile(&profile, &BTreeMap::new(), Rng::new(1)).unwrap();
    for ch in &act.channels {
        let a = profile.actuators.iter().find(|a| a.name == ch.cfg.name).unwrap();
        let c = &a.calibration;
        let cal = r3x_motion::Calibration {
            center_us: c.center_us,
            center_value: c.center_value,
            trim_us: c.trim_us,
            invert: c.invert,
            gear: c.gear,
            mm_per_deg: ch.prismatic.then(|| c.mm_per_deg.unwrap_or(0.2)),
            pulse_min_us: c.pulse_min_us,
            pulse_max_us: c.pulse_max_us,
            range_deg: c.range_deg,
        };
        let (lo, hi) = (ch.follower.soft_min, ch.follower.soft_max);
        for i in 0..=100 {
            let v = lo - 5.0 + (hi - lo + 10.0) * f64::from(i) / 100.0;
            assert_eq!(cal.value_to_us(v).to_bits(), ch.value_to_us(v).to_bits(), "{} at {v}", ch.cfg.name);
        }
    }
}

/// The Physical rig's coupled limits (`mech.couplings` in `robot.generated.json`) are the safety
/// layer's: Hunter's head tilts less far the more it rolls (the horn arm meets the head top), so a
/// tilt command is clamped at the current roll, and a roll that narrows the tilt pulls it back.
#[test]
fn coupled_limits_clamp_commands_on_the_physical_rig() {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/../../../profiles/r3x/robot.generated.json");
    let profile = r3x_contracts::RobotProfile::load(path).unwrap();
    let mut act = Actuation::from_profile(&profile, &BTreeMap::new(), Rng::new(1)).unwrap();
    let c = act.couplings.iter().find(|c| c.joint == "head_tilt" && c.depends_on == "head_roll").unwrap().clone();
    let at0 = c.range_at(0.0).unwrap().1;
    assert!(c.range_at(12.0).unwrap().1 < at0);
    let soft = act.channel_for("head_tilt").unwrap().follower.soft_max;
    act.command("head_roll", 12.0);
    act.command("head_tilt", 20.0);
    let roll = act.target_of("head_roll").unwrap(); // the roll's own soft limit may stop it short of 12
    let at_roll = c.range_at(roll).unwrap().1;
    assert!(at_roll < at0 && (act.target_of("head_tilt").unwrap() - at_roll.min(soft)).abs() < 1e-9);
    // level again: the tilt may go to its own (soft) limit
    act.command("head_roll", 0.0);
    act.command("head_tilt", 20.0);
    assert!((act.target_of("head_tilt").unwrap() - at0.min(soft)).abs() < 1e-9);
    // rolling back over narrows the tilt with it
    act.command("head_roll", -12.0);
    let lim = c.range_at(act.target_of("head_roll").unwrap()).unwrap().1;
    assert!(act.target_of("head_tilt").unwrap() <= lim + 1e-9);
}
