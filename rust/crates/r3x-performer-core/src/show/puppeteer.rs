//! The puppeteer layer (port of `puppeteer.ts`): a FIXED, NAMED command space, not raw
//! joints - the future action space for operator-imitation learning (Disney, arXiv
//! 2504.02724). Renaming or remapping a signal invalidates every recorded take; add new
//! signals at the end instead.
//!
//! Continuous intents in [-1, 1]: gaze_yaw, gaze_pitch, lift, body_yaw, lean, visor,
//! arm_raise, energy, roll, then the hands (2026-10-01): hero_aim / hero_raise / hero_twist /
//! hero_grip and poker_aim / poker_raise / poker_reach / poker_grip (grips 0..1). Discrete: 8
//! cue slots and a mode (idle / engaged / dj).
//!
//! Gamepad (standard mapping): left stick = body_yaw / lean, right stick = gaze, d-pad =
//! lift / visor, RT - LT = arm_raise, LB/RB = energy down/up, A B X Y = cue slots 1-4
//! (hold Back for 5-8), R3 = cycle mode, Start = motion.freeze toggle. The host polls the
//! pad and hands its state to [`Puppeteer::update`]. `roll` reads a fifth axis when there is
//! one (`r3x-pad`'s operator layer: the right stick's X while L1+R1 are held), mirrored like
//! the sticks; with a standard four-axis pad, UI sliders and takes drive it.

use super::body::{get, Pose};
use serde::{Deserialize, Serialize};

pub const CONTINUOUS: [&str; 17] = [
    "gaze_yaw",
    "gaze_pitch",
    "lift",
    "body_yaw",
    "lean",
    "visor",
    "arm_raise",
    "energy",
    "roll",
    "hero_aim",
    "hero_raise",
    "hero_twist",
    "hero_grip",
    "poker_aim",
    "poker_raise",
    "poker_reach",
    "poker_grip",
];
pub const MODES: [PuppetMode; 3] = [PuppetMode::Idle, PuppetMode::Engaged, PuppetMode::Dj];
pub const SLOT_COUNT: usize = 8;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Default, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum PuppetMode {
    #[default]
    Idle,
    Engaged,
    Dj,
}

/// One value per [`CONTINUOUS`] intent, in that order.
pub type Command = [f64; 17];

pub fn intent_index(name: &str) -> Option<usize> {
    CONTINUOUS.iter().position(|k| *k == name)
}

fn clamp1(x: f64) -> f64 {
    x.clamp(-1.0, 1.0)
}
/// Expo curve: fine control near centre, full range at the end of travel.
fn expo(x: f64) -> f64 {
    let k = 0.55;
    let c = clamp1(x);
    c * ((1.0 - k) + k * c * c)
}
const DEAD: f64 = 0.12;
fn dead(x: f64) -> f64 {
    if x.abs() < DEAD {
        0.0
    } else {
        (x - x.signum() * DEAD) / (1.0 - DEAD)
    }
}

/// Deg of pan before the rings take over.
const NECK: f64 = 60.0;
pub const GAZE_YAW: f64 = 85.0;
const GAZE_PITCH: f64 = 14.0;
const LIFT: f64 = 12.0;
const BODY_YAW_LOWER: f64 = 24.0;
const BODY_YAW_TOP: f64 = 14.0;
const VISOR_OPEN: f64 = 11.0;
const VISOR_CLOSE: f64 = 14.0;
const ARM_UP: f64 = 34.0;
const ARM_DOWN: f64 = 20.0;
/// Head roll at full stick, deg (inside the soft range, +/-10).
const ROLL: f64 = 9.0;
/// Ring turn that aims an arm at full stick, deg: the hero arm rides the top ring, the poker
/// arm the lower one (which carries everything above it).
pub const HERO_AIM: f64 = 25.0;
pub const POKER_AIM: f64 = 25.0;
const HERO_TWIST: f64 = 80.0;
/// Hero arm at full stick, deg of shoulder. The arm rests raised, and a + shoulder swings it
/// forward and down (measured in the sim, 2026-10-01: +1 took the claw from 0.68 to 0.55 m),
/// so stick up = - shoulder, inside the soft range (-30..40).
const HERO_UP: f64 = 28.0;
const HERO_DOWN: f64 = 34.0;
/// Poker claw tip at full stick: swung this far around the shoulder (deg); at full d-pad,
/// reached this far in or out (mm; out stops at full stretch, a few mm past rest).
pub const POKER_SWING: f64 = 30.0;
pub const POKER_REACH_MM: f64 = 40.0;
/// Claw fingers at full grip, deg (`claw_snap` closes to 25).
const CLAW_CLOSED: f64 = 25.0;
const HERO_CLAWS: [&str; 3] = ["hero_claw_l", "hero_claw_r", "hero_claw_t"];
const POKER_CLAWS: [&str; 2] = ["poker_claw_upper", "poker_claw_lower"];

/// The axes of an operator feed (`r3x-pad` controls): the standard four sticks, then
/// already-shaped intents. A pad with more than [`op_axis::ROLL`] + 1 axes is an operator feed;
/// a browser's standard four-axis pad keeps the triggers and d-pad mapping.
pub mod op_axis {
    /// Head roll, from the operator's d-pad (eased).
    pub const ROLL: usize = 4;
    pub const HERO_AIM: usize = 5;
    pub const HERO_RAISE: usize = 6;
    pub const HERO_TWIST: usize = 7;
    pub const HERO_GRIP: usize = 8;
    pub const POKER_AIM: usize = 9;
    pub const POKER_RAISE: usize = 10;
    pub const POKER_REACH: usize = 11;
    pub const POKER_GRIP: usize = 12;
    /// Lift and visor, eased by the operator layer (its d-pad bindings).
    pub const LIFT: usize = 13;
    pub const VISOR: usize = 14;
    pub const COUNT: usize = 15;
}

/// A standard-mapping gamepad snapshot.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct PadState {
    pub axes: Vec<f64>,
    /// `(pressed, value)` per button.
    pub buttons: Vec<(bool, f64)>,
}

/// Discrete things the operator did; the performer acts on them.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PuppetEvent {
    Slot(usize),
    Mode(PuppetMode),
    ToggleFreeze,
}

#[derive(Clone, Debug)]
pub struct Puppeteer {
    /// The command the operator is giving now (after deadzone and smoothing).
    pub cmd: Command,
    /// Raw targets (UI sliders / gamepad) before smoothing.
    pub target: Command,
    pub mode: PuppetMode,
    /// Discrete triggers since the last take sample.
    pub fired: Vec<usize>,
    pub gamepad: bool,
    pub enabled: bool,
    /// Discrete events for the host, drained by [`Puppeteer::take_events`].
    events: Vec<PuppetEvent>,
    prev_buttons: Vec<bool>,
    dpad_lift: f64,
    dpad_visor: f64,
    /// A release in progress: the command it started from and the seconds since.
    release: Option<(Command, f64)>,
    /// The selected rig's neck (limits, whether the head rides the rings): the gaze solver's.
    pub rig: super::gaze::RigGeometry,
}

impl Default for Puppeteer {
    fn default() -> Self {
        Puppeteer {
            cmd: [0.0; 17],
            target: [0.0; 17],
            mode: PuppetMode::Idle,
            fired: Vec::new(),
            gamepad: false,
            enabled: true,
            events: Vec::new(),
            prev_buttons: Vec::new(),
            dpad_lift: 0.0,
            dpad_visor: 0.0,
            release: None,
            rig: super::gaze::RigGeometry::default(),
        }
    }
}

const ENERGY: usize = 7;
const ROLL_I: usize = 8;
/// Default release fade (min-jerk), plan section 3c.
pub const RELEASE_S: f64 = 0.4;

impl Puppeteer {
    pub fn set(&mut self, name: &str, v: f64) {
        if let Some(i) = intent_index(name).filter(|_| v.is_finite()) {
            self.target[i] = clamp1(v);
            self.release = None;
        }
    }

    /// Let go of the sticks: every intent but `energy` fades to 0 over [`RELEASE_S`]
    /// (min-jerk), so the body settles back onto the layers below without a jump.
    pub fn release(&mut self) {
        self.release = Some((self.cmd, 0.0));
        for (i, t) in self.target.iter_mut().enumerate() {
            if i != ENERGY {
                *t = 0.0;
            }
        }
    }

    pub fn trigger(&mut self, slot: usize) {
        if slot < SLOT_COUNT {
            self.fired.push(slot);
            self.events.push(PuppetEvent::Slot(slot));
        }
    }

    pub fn set_mode(&mut self, m: PuppetMode) {
        self.mode = m;
        self.events.push(PuppetEvent::Mode(m));
    }

    pub fn take_events(&mut self) -> Vec<PuppetEvent> {
        std::mem::take(&mut self.events)
    }

    /// Read the gamepad (if any) and smooth the command. dt in s.
    pub fn update(&mut self, dt: f64, pad: Option<&PadState>) {
        self.gamepad = pad.is_some();
        if let Some(pad) = pad {
            self.read_pad(pad, dt);
        }
        let k = 1.0 - libm::exp(-dt / 0.08); // ~80 ms smoothing: sticks are noisy, servos are not
        for (c, t) in self.cmd.iter_mut().zip(self.target) {
            *c += (t - *c) * k;
        }
        if let Some((from, since)) = &mut self.release {
            *since += dt;
            let w = 1.0 - super::curve::minjerk((*since / RELEASE_S).min(1.0));
            for (i, (c, f)) in self.cmd.iter_mut().zip(from.iter()).enumerate() {
                if i != ENERGY {
                    *c = f * w;
                }
            }
            if w <= 0.0 {
                self.release = None;
            }
        }
    }

    fn read_pad(&mut self, pad: &PadState, dt: f64) {
        let ax = |i: usize| dead(pad.axes.get(i).copied().unwrap_or(0.0));
        let btn = |i: usize| pad.buttons.get(i).is_some_and(|b| b.0);
        let val = |i: usize| pad.buttons.get(i).map_or(0.0, |b| b.1);
        let prev = self.prev_buttons.clone();
        let edge = |i: usize| btn(i) && !prev.get(i).copied().unwrap_or(false);
        // D-pad holds ease toward +/-1 and back to 0 when released.
        let rate = 1.0 - libm::exp(-dt / 0.15);
        let dir = |up: usize, down: usize| {
            if btn(up) {
                1.0
            } else if btn(down) {
                -1.0
            } else {
                0.0
            }
        };
        if pad.axes.len() >= op_axis::COUNT {
            // An operator feed (`r3x-pad` controls): everything arrives shaped - layers,
            // pickup, easing home and the d-pad are the operator layer's.
            let raw = |i: usize| clamp1(pad.axes[i]);
            let energy = self.target[ENERGY];
            self.target = [
                ax(2),
                -ax(3),
                raw(op_axis::LIFT),
                ax(0),
                -ax(1),
                raw(op_axis::VISOR),
                0.0,
                energy,
                -raw(op_axis::ROLL),
                // The hands arrive shaped (dead zone, pickup, crane), so no dead zone here: a crane
                // holding the arm at 0.1 must stay at 0.1.
                raw(op_axis::HERO_AIM),
                raw(op_axis::HERO_RAISE),
                raw(op_axis::HERO_TWIST),
                raw(op_axis::HERO_GRIP).max(0.0),
                raw(op_axis::POKER_AIM),
                raw(op_axis::POKER_RAISE),
                raw(op_axis::POKER_REACH),
                raw(op_axis::POKER_GRIP).max(0.0),
            ];
            self.prev_buttons = pad.buttons.iter().map(|b| b.0).collect();
            return;
        }
        self.dpad_lift += (dir(12, 13) - self.dpad_lift) * rate;
        self.dpad_visor += (dir(15, 14) - self.dpad_visor) * rate;
        let t = self.target;
        self.target = [
            ax(2),
            -ax(3),
            self.dpad_lift,
            ax(0),
            -ax(1),
            self.dpad_visor,
            clamp1(val(7) - val(6)),
            self.target[ENERGY],
            // A fifth axis is roll (the runtime's operator layer: right X while L1+R1 are
            // held), mirrored like the sticks; a standard four-axis pad leaves roll to the UI.
            if pad.axes.len() > 4 { -ax(4) } else { t[ROLL_I] },
            // A standard pad has no hands: the UI's sliders keep them.
            t[9],
            t[10],
            t[11],
            t[12],
            t[13],
            t[14],
            t[15],
            t[16],
        ];
        if edge(4) {
            self.target[ENERGY] = clamp1(self.target[ENERGY] - 0.25);
        }
        if edge(5) {
            self.target[ENERGY] = clamp1(self.target[ENERGY] + 0.25);
        }
        let shift = if btn(8) { 4 } else { 0 };
        for b in 0..4 {
            if edge(b) {
                self.trigger(b + shift);
            }
        }
        if edge(11) {
            let i = MODES.iter().position(|m| *m == self.mode).unwrap_or(0);
            self.set_mode(MODES[(i + 1) % MODES.len()]);
        }
        if edge(9) {
            self.events.push(PuppetEvent::ToggleFreeze);
        }
        self.prev_buttons = pad.buttons.iter().map(|b| b.0).collect();
    }

    /// The layer: intents to additive joint offsets. `pose` is everything below.
    pub fn apply(&self, pose: &mut Pose) {
        if !self.enabled {
            return;
        }
        let [gaze_yaw, gaze_pitch, lift, body_yaw, lean, visor, arm_raise, _, roll, hero_aim, hero_raise, hero_twist, hero_grip, poker_aim, poker_raise, poker_reach, poker_grip] =
            self.cmd;
        fn add(pose: &mut Pose, j: &str, v: f64) {
            if v != 0.0 {
                pose.insert(j.into(), get(pose, j) + v);
            }
        }
        // Gaze: the head leads, inside the rig's neck range. Past it, if the head rides the rings
        // (Original) the rest goes to the rings; if it stands on the base (Physical) turning a
        // ring would not move the gaze, so a little of it becomes a cant toward the look.
        let yaw = expo(gaze_yaw) * GAZE_YAW;
        let pan = get(pose, "head_pan");
        let want = pan + yaw;
        let neck = want.clamp(self.rig.pan.0.max(-NECK), self.rig.pan.1.min(NECK));
        let spill = want - neck;
        add(pose, "head_pan", neck - pan);
        if self.rig.head_on_rings {
            add(pose, "torso_top", spill * 0.6);
            add(pose, "torso_lower", spill * 0.4);
        } else {
            add(pose, "head_roll", (-spill * 0.08).clamp(-4.0, 4.0));
        }
        add(pose, "head_tilt", -expo(gaze_pitch) * GAZE_PITCH);

        add(pose, "head_lift", expo(lift) * LIFT);
        let body = expo(body_yaw);
        add(pose, "torso_lower", body * BODY_YAW_LOWER);
        add(pose, "torso_top", body * BODY_YAW_TOP);
        // The body turns under the head: when the head rides the rings the neck takes the turn
        // back, so the gaze stays where the right stick put it (2026-10-01: turning the body
        // swung the head with it, which read as one piece).
        if self.rig.head_on_rings {
            add(pose, "head_pan", -body * (BODY_YAW_LOWER + BODY_YAW_TOP));
        }

        let lean = expo(lean);
        add(pose, "head_lift", lean * 7.0);
        add(pose, "head_tilt", lean * 5.0);
        add(pose, "visor", -lean * 6.0);

        let v = expo(visor);
        add(
            pose,
            "visor",
            if v >= 0.0 {
                -v * VISOR_OPEN
            } else {
                -v * VISOR_CLOSE
            },
        );
        let arm = expo(arm_raise);
        add(
            pose,
            "hero_shoulder",
            if arm >= 0.0 {
                arm * ARM_UP
            } else {
                arm * ARM_DOWN
            },
        );
        add(pose, "hero_wrist", arm * 20.0);
        // +roll: right-handed about +Z, the crown toward the droid's right (-X).
        add(pose, "head_roll", expo(roll) * ROLL);

        // Hands. Aiming turns only that arm's ring (the real rings turn independently on the
        // static core): on stacked rings (Original) the middle ring takes the lower one's turn
        // back so nothing above moves; when the head rides the top ring, the neck takes the hero
        // ring's turn back so the gaze holds.
        let (hero_ring, poker_ring) = (expo(hero_aim) * HERO_AIM, expo(poker_aim) * POKER_AIM);
        add(pose, "torso_top", hero_ring);
        add(pose, "torso_lower", poker_ring);
        if self.rig.rings_stacked {
            add(pose, "torso_middle", -poker_ring);
        }
        if self.rig.head_on_rings {
            add(pose, "head_pan", -hero_ring);
        }
        let up = expo(hero_raise);
        add(pose, "hero_shoulder", if up >= 0.0 { -up * HERO_UP } else { -up * HERO_DOWN });
        add(pose, "hero_wrist", hero_twist.clamp(-1.0, 1.0) * HERO_TWIST);
        // Linear (no expo): the crane moves the tip in straight lines through these channels.
        let (shoulder, wrist) = super::arm_ik::POKER.solve(poker_raise.clamp(-1.0, 1.0) * POKER_SWING, poker_reach.clamp(-1.0, 1.0) * POKER_REACH_MM);
        add(pose, "poker_shoulder", shoulder);
        add(pose, "poker_wrist", wrist);
        for c in HERO_CLAWS {
            add(pose, c, hero_grip.clamp(0.0, 1.0) * CLAW_CLOSED);
        }
        for c in POKER_CLAWS {
            add(pose, c, poker_grip.clamp(0.0, 1.0) * CLAW_CLOSED);
        }
    }

    /// Energy as a gain: 0.4x at -1, 1x at 0, 1.6x at +1.
    pub fn energy_gain(&self) -> f64 {
        1.0 + 0.6 * self.cmd[ENERGY]
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn operator(set: &[(usize, f64)]) -> PadState {
        let mut axes = vec![0.0; op_axis::COUNT];
        for &(i, v) in set {
            axes[i] = v;
        }
        PadState { axes, buttons: vec![(false, 0.0); 17] }
    }

    fn settle(p: &mut Puppeteer, pad: &PadState) -> Pose {
        for _ in 0..200 {
            p.update(0.01, Some(pad));
        }
        let mut pose = Pose::new();
        p.apply(&mut pose);
        pose
    }

    /// The operator feed drives the hands (2026-10-01): aim turns the arm's ring, raise lifts
    /// it, grip closes the claw; the neck takes back what the rings turn when the head rides them.
    #[test]
    fn an_operator_feed_drives_the_hands() {
        let mut p = Puppeteer::default();
        p.rig.head_on_rings = true;
        let pose = settle(&mut p, &operator(&[(op_axis::HERO_AIM, 1.0), (op_axis::HERO_RAISE, 1.0), (op_axis::HERO_GRIP, 1.0)]));
        assert!((get(&pose, "torso_top") - HERO_AIM).abs() < 0.1);
        assert!((get(&pose, "head_pan") + HERO_AIM).abs() < 0.1, "the gaze holds");
        assert!((get(&pose, "hero_shoulder") + HERO_UP).abs() < 0.1, "stick up = - shoulder (the claw rises)");
        for c in HERO_CLAWS {
            assert!((get(&pose, c) - CLAW_CLOSED).abs() < 0.1, "{c} closed");
        }
        assert_eq!(get(&pose, "poker_claw_upper"), 0.0, "only the hero claw");
        // The head on the base (the column rig): turning a ring does not move it, no counter-turn.
        let mut p = Puppeteer::default();
        p.rig.head_on_rings = false;
        p.rig.rings_stacked = false;
        let pose = settle(&mut p, &operator(&[(op_axis::POKER_AIM, -1.0)]));
        assert!((get(&pose, "torso_lower") + POKER_AIM).abs() < 0.1);
        assert_eq!(get(&pose, "head_pan"), 0.0);
        assert_eq!(get(&pose, "torso_middle"), 0.0, "independent rings: nothing to take back");
    }

    /// Aiming one arm moves only its ring (2026-10-01: "if I'm using the modifier, I just want
    /// to move just that ring"). On stacked rings the middle ring cancels the lower one's turn,
    /// so the throttle arm, the top ring, the hero arm and the head stay where they were.
    #[test]
    fn aiming_the_poker_arm_on_stacked_rings_moves_only_its_ring() {
        let mut p = Puppeteer::default();
        assert!(p.rig.rings_stacked && p.rig.head_on_rings);
        let pose = settle(&mut p, &operator(&[(op_axis::POKER_AIM, 1.0)]));
        assert!((get(&pose, "torso_lower") - POKER_AIM).abs() < 0.1);
        assert!((get(&pose, "torso_middle") + POKER_AIM).abs() < 0.1, "the middle ring takes it back");
        assert_eq!(get(&pose, "torso_top"), 0.0);
        assert_eq!(get(&pose, "head_pan"), 0.0, "the head never saw it");
    }

    /// Turning the body (left stick, no modifier) no longer swings the gaze: when the head
    /// rides the rings the neck takes the turn back, so the head stays the right stick's.
    #[test]
    fn turning_the_body_holds_the_gaze() {
        let mut p = Puppeteer::default();
        let pose = settle(&mut p, &operator(&[(0, 1.0)]));
        let rings = get(&pose, "torso_lower") + get(&pose, "torso_top");
        assert!((rings - (BODY_YAW_LOWER + BODY_YAW_TOP)).abs() < 0.5, "the body turned: {rings}");
        assert!((get(&pose, "head_pan") + rings).abs() < 0.5, "the neck took it back: {}", get(&pose, "head_pan"));
        let mut p = Puppeteer::default();
        p.rig.head_on_rings = false;
        let pose = settle(&mut p, &operator(&[(0, 1.0)]));
        assert_eq!(get(&pose, "head_pan"), 0.0, "head on the column: nothing to take back");
    }

    #[test]
    fn poker_raise_moves_the_claw_tip_through_the_ik() {
        let mut p = Puppeteer::default();
        let pose = settle(&mut p, &operator(&[(op_axis::POKER_RAISE, 1.0)]));
        let (s, w) = (get(&pose, "poker_shoulder"), get(&pose, "poker_wrist"));
        let ik = super::super::arm_ik::POKER;
        let ((x0, y0), (x, y)) = (ik.tip(0.0, 0.0), ik.tip(s, w));
        let swing = libm::atan2(y, x).to_degrees() - libm::atan2(y0, x0).to_degrees();
        assert!((swing - POKER_SWING).abs() < 0.5, "tip swung up {POKER_SWING} deg: {swing:.1}");
    }

    /// A browser's standard pad (four axes) never touches the hands: the UI's sliders keep them.
    #[test]
    fn a_standard_pad_leaves_the_hands_to_the_sliders() {
        let mut p = Puppeteer::default();
        p.set("hero_raise", 0.5);
        let pad = PadState { axes: vec![0.0; 4], buttons: vec![(false, 0.0); 17] };
        settle(&mut p, &pad);
        assert!((p.cmd[10] - 0.5).abs() < 1e-3);
    }

    #[test]
    fn roll_is_an_additive_head_roll_that_a_pad_leaves_alone_and_release_fades() {
        let mut p = Puppeteer::default();
        p.set("roll", 1.0);
        p.set("energy", 0.5);
        let pad = PadState { axes: vec![0.0; 4], buttons: vec![(false, 0.0); 17] };
        for _ in 0..100 {
            p.update(0.01, Some(&pad));
        }
        assert!(p.cmd[ROLL_I] > 0.99, "{}", p.cmd[ROLL_I]);
        let mut pose = Pose::new();
        p.apply(&mut pose);
        assert!((get(&pose, "head_roll") - ROLL * expo(p.cmd[ROLL_I])).abs() < 1e-12);
        p.release();
        for _ in 0..100 {
            p.update(0.01, None);
        }
        assert_eq!(p.cmd[ROLL_I], 0.0);
        assert!((p.cmd[ENERGY] - 0.5).abs() < 1e-3);
    }

    /// A fifth axis (beyond the standard four; the runtime's operator layer puts the right
    /// stick's X there while L1+R1 are held) is roll, mirrored like the sticks: stick right
    /// tips the crown to the operator's right, the droid's left (-roll).
    #[test]
    fn a_fifth_axis_is_roll_mirrored_like_the_sticks() {
        let mut p = Puppeteer::default();
        let pad = PadState { axes: vec![0.0, 0.0, 0.0, 0.0, 1.0], buttons: vec![(false, 0.0); 17] };
        for _ in 0..100 {
            p.update(0.01, Some(&pad));
        }
        assert!(p.cmd[ROLL_I] < -0.99, "{}", p.cmd[ROLL_I]);
        let mut pose = Pose::new();
        p.apply(&mut pose);
        assert!(get(&pose, "head_roll") < -ROLL * 0.99);
        let centred = PadState { axes: vec![0.0; 5], buttons: vec![(false, 0.0); 17] };
        for _ in 0..100 {
            p.update(0.01, Some(&centred));
        }
        assert!(p.cmd[ROLL_I].abs() < 1e-3, "a centred fifth axis rolls back: {}", p.cmd[ROLL_I]);
    }
}
