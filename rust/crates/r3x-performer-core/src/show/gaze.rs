//! The rig layer between intent and joints: what the selected rig's neck can do with a gaze
//! (`RigGeometry`, from the Robot Profile), and the automatic expressive roll that rides on top
//! of every head motion (`ExpressiveRoll`, the alive layer `expressive_roll`).
//!
//! Expressive roll is small and centre-weighted, three parts summed and clamped:
//! - **cant**: looking to the side tips the crown toward the look (smoothstep in |pan|, up to
//!   [`CANT_MAX`]; looking up as well adds half again);
//! - **lag**: on a fast pan the crown trails the turn and overshoots when it stops (a
//!   damped spring driven by the filtered pan rate);
//! - **beat**: while music plays, an alternating accent on each beat, scaled by energy.
//!
//! The manual roll (puppeteer, L1+R1 + right stick) is already in the pose; this adds to it and
//! the sum stays inside the joint's animation range.

use r3x_contracts::RobotProfile;

use super::body::{get, Pose};

/// Degrees: the largest cant (at |pan| >= [`CANT_FULL_PAN`]).
pub const CANT_MAX: f64 = 4.0;
pub const CANT_FULL_PAN: f64 = 35.0;
/// Degrees of trailing roll per deg/s of pan rate, and its cap.
pub const LAG_GAIN: f64 = 0.012;
pub const LAG_MAX: f64 = 2.5;
/// Degrees per beat accent at energy 1.
pub const BEAT: f64 = 1.0;
/// The whole layer's cap.
pub const EXPRESSIVE_MAX: f64 = 6.0;

/// The neck as the selected rig has it.
#[derive(Clone, Debug, PartialEq)]
pub struct RigGeometry {
    /// Animation ranges (deg).
    pub pan: (f64, f64),
    pub tilt: (f64, f64),
    pub roll: (f64, f64),
    /// The head rides the top ring (Original), so gaze past the neck can spill to the rings.
    /// False (Physical: the head column stands on the base) = turning a ring moves no gaze.
    pub head_on_rings: bool,
}

impl Default for RigGeometry {
    fn default() -> Self {
        RigGeometry { pan: (-66.0, 66.0), tilt: (-16.0, 21.0), roll: (-10.0, 10.0), head_on_rings: true }
    }
}

impl RigGeometry {
    pub fn from_profile(p: &RobotProfile) -> Self {
        let d = RigGeometry::default();
        let range = |j: &str, dflt: (f64, f64)| p.joint(j).map_or(dflt, |j| (j.animation.min, j.animation.max));
        let mut on_rings = false;
        let mut cur = p.joint("head_pan").and_then(|j| j.parent.clone());
        for _ in 0..p.joints.len() {
            let Some(n) = cur else { break };
            if n.starts_with("torso_") {
                on_rings = true;
                break;
            }
            cur = p.joint(&n).and_then(|j| j.parent.clone());
        }
        RigGeometry {
            pan: range("head_pan", d.pan),
            tilt: range("head_tilt", d.tilt),
            roll: range("head_roll", d.roll),
            head_on_rings: on_rings,
        }
    }
}

/// The expressive-roll layer's state (one per performer).
#[derive(Clone, Debug, Default)]
pub struct ExpressiveRoll {
    lag: f64,
    lag_v: f64,
    rate: f64,
    prev_pan: Option<f64>,
    /// The last offset it added (deg), for tests and overlays.
    pub last: f64,
}

impl ExpressiveRoll {
    pub fn reset(&mut self) {
        *self = ExpressiveRoll::default();
    }

    /// Add the layer to `pose` (after the compositor and the puppeteer). `beat`: the bpm while
    /// music plays. Returns the offset added before the joint clamp.
    pub fn apply(&mut self, pose: &mut Pose, dt: f64, t: f64, beat: Option<f64>, energy: f64, g: &RigGeometry) -> f64 {
        let pan = get(pose, "head_pan");
        let tilt = get(pose, "head_tilt");
        // cant toward the look (+pan looks to the droid's left; +roll leans the crown right)
        let u = (pan.abs() / CANT_FULL_PAN).min(1.0);
        let up = (-tilt / 15.0).clamp(0.0, 1.0);
        let cant = -pan.signum() * CANT_MAX * u * u * (3.0 - 2.0 * u) * (1.0 + 0.5 * up);
        // lag: the crown trails a fast turn, then overshoots (damped spring, zeta 0.35, 2.2 Hz)
        if dt > 0.0 {
            let rate = self.prev_pan.map_or(0.0, |p| (pan - p) / dt);
            self.rate += (rate - self.rate) * (1.0 - (-dt / 0.05).exp());
            let target = (self.rate * LAG_GAIN).clamp(-LAG_MAX, LAG_MAX);
            let w = std::f64::consts::TAU * 2.2;
            let a = w * w * (target - self.lag) - 2.0 * 0.35 * w * self.lag_v;
            self.lag_v += a * dt;
            self.lag += self.lag_v * dt;
        }
        self.prev_pan = Some(pan);
        // beat accents, alternating sides
        let accent = beat.filter(|b| *b > 0.0).map_or(0.0, |bpm| {
            let ph = t * bpm / 60.0;
            let n = ph.floor();
            let side = if (n as i64).rem_euclid(2) == 0 { 1.0 } else { -1.0 };
            BEAT * energy.clamp(0.0, 2.0) * side * (-(ph - n) * 5.0).exp()
        });
        let r = (cant + self.lag + accent).clamp(-EXPRESSIVE_MAX, EXPRESSIVE_MAX);
        self.last = r;
        let total = (get(pose, "head_roll") + r).clamp(g.roll.0, g.roll.1);
        pose.insert("head_roll".into(), total);
        r
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn pose(pan: f64, tilt: f64) -> Pose {
        Pose::from([("head_pan".into(), pan), ("head_tilt".into(), tilt), ("head_roll".into(), 0.0)])
    }

    #[test]
    fn cant_is_centre_weighted_toward_the_look() {
        let g = RigGeometry::default();
        let mut e = ExpressiveRoll::default();
        let at = |pan, tilt, e: &mut ExpressiveRoll| {
            e.reset();
            let mut p = pose(pan, tilt);
            e.apply(&mut p, 0.0, 0.0, None, 1.0, &g);
            p["head_roll"]
        };
        assert_eq!(at(0.0, 0.0, &mut e), 0.0);
        assert!(at(5.0, 0.0, &mut e).abs() < 0.3, "small near the centre");
        assert!((at(40.0, 0.0, &mut e) + CANT_MAX).abs() < 1e-9, "full cant, crown toward the look (left = -roll)");
        assert!((at(-40.0, 0.0, &mut e) - CANT_MAX).abs() < 1e-9);
        assert!(at(40.0, -15.0, &mut e) < at(40.0, 0.0, &mut e), "looking up as well adds more");
    }

    #[test]
    fn a_fast_pan_lags_then_overshoots() {
        let g = RigGeometry::default();
        let mut e = ExpressiveRoll::default();
        let (mut pan, dt) = (0.0, 0.02);
        let mut peak: f64 = 0.0;
        for _ in 0..15 {
            pan += 150.0 * dt; // 150 deg/s to the left
            let mut p = pose(pan, 0.0);
            e.apply(&mut p, dt, 0.0, None, 1.0, &g);
            peak = peak.max(e.lag);
        }
        assert!(peak > 0.8, "trails the turn (+roll while panning left): {peak}");
        let mut low: f64 = 0.0;
        for _ in 0..40 {
            let mut p = pose(pan, 0.0);
            e.apply(&mut p, dt, 0.0, None, 1.0, &g);
            low = low.min(e.lag);
        }
        assert!(low < -0.05, "overshoots past centre after the stop: {low}");
    }

    #[test]
    fn beats_alternate_and_everything_stays_clamped() {
        let g = RigGeometry { roll: (-3.0, 3.0), ..RigGeometry::default() };
        let mut e = ExpressiveRoll::default();
        let mut a = pose(0.0, 0.0);
        e.apply(&mut a, 0.0, 0.0, Some(120.0), 1.0, &g);
        let mut b = pose(0.0, 0.0);
        e.reset();
        e.apply(&mut b, 0.0, 0.5, Some(120.0), 1.0, &g);
        assert!(a["head_roll"] > 0.9 && b["head_roll"] < -0.9, "beat 0 right, beat 1 left");
        let mut c = pose(60.0, -20.0);
        c.insert("head_roll".into(), -2.5); // a manual roll the operator holds
        e.reset();
        e.apply(&mut c, 0.0, 0.0, None, 1.0, &g);
        assert_eq!(c["head_roll"], -3.0, "manual + expressive, clamped to the rig");
    }
}
