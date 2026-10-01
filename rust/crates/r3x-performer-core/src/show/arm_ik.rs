//! Two-link planar IK for the poker arm: the shoulder and the wrist turn about the same axis
//! (`sim/model/build_r3x.py`: both `axis=(1, 0, 0)`), so the claw tip moves in one plane and
//! two joints can place it anywhere in reach. The operator moves the tip in polar terms around
//! the shoulder - swing it up/down, reach it in/out - because at rest the arm is nearly
//! straight (255 of 268 mm), so "straight up 60 mm" is out of reach while a swing never is.
//!
//! Angles are elevations in that plane (0 = straight forward, + = up). Joint angles are
//! offsets from the rest pose, signed by [`TwoLink::sign`] (how a + joint angle tips the arm).

use libm::{atan2, cos, sin, sqrt};

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct TwoLink {
    /// Shoulder pivot to wrist pivot, mm.
    pub l1: f64,
    /// Wrist pivot to the claw tip, mm.
    pub l2: f64,
    /// Rest elevations of the two links, deg.
    pub rest1: f64,
    pub rest2: f64,
    /// +1 when a + joint angle raises the link, -1 when it lowers it.
    pub sign: f64,
}

/// The poker arm. `l1` and `rest1` from the model's pivots: shoulder (-4.2, 372.0, 200.6) to
/// wrist (-8.0, 465.7, 328.0) mm = 93.7 up, 127.4 forward. The claw's length and rest angle
/// are estimates (the claw is not jointed on the stock build); retune against the robot.
pub const POKER: TwoLink = TwoLink { l1: 158.2, l2: 110.0, rest1: 36.3, rest2: 0.0, sign: -1.0 };

fn rad(d: f64) -> f64 {
    d.to_radians()
}

impl TwoLink {
    /// The claw tip (forward, up) in mm from the shoulder, for joint offsets (deg).
    pub fn tip(&self, shoulder: f64, wrist: f64) -> (f64, f64) {
        let e1 = rad(self.rest1 + self.sign * shoulder);
        let e2 = rad(self.rest2 + self.sign * (shoulder + wrist));
        (self.l1 * cos(e1) + self.l2 * cos(e2), self.l1 * sin(e1) + self.l2 * sin(e2))
    }

    /// Joint offsets (shoulder, wrist) in deg that put the tip `swing` deg higher around the
    /// shoulder than at rest and `reach` mm further from it. Out of reach: as far as it goes
    /// along that line. Keeps the rest pose's bend (the elbow never flips).
    pub fn solve(&self, swing: f64, reach: f64) -> (f64, f64) {
        let (rx, ry) = self.tip(0.0, 0.0);
        let a = atan2(ry, rx) + rad(swing);
        let r = sqrt(rx * rx + ry * ry) + reach;
        let (x, y) = (r * cos(a), r * sin(a));
        let reach = sqrt(x * x + y * y);
        let (lo, hi) = ((self.l1 - self.l2).abs() + 1e-6, self.l1 + self.l2 - 1e-6);
        let d = reach.clamp(lo, hi);
        // The bend between the links (e2 - e1), on the rest pose's side.
        let rest_bend = (self.rest2 - self.rest1).to_radians();
        let c = ((d * d - self.l1 * self.l1 - self.l2 * self.l2) / (2.0 * self.l1 * self.l2)).clamp(-1.0, 1.0);
        let bend = libm::acos(c) * if rest_bend < 0.0 { -1.0 } else { 1.0 };
        let e1 = atan2(y, x) - atan2(self.l2 * sin(bend), self.l1 + self.l2 * cos(bend));
        let e2 = e1 + bend;
        let shoulder = (e1.to_degrees() - self.rest1) / self.sign;
        let wrist = (e2.to_degrees() - self.rest2) / self.sign - shoulder;
        (shoulder, wrist)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rest_is_zero_and_the_tip_lands_where_asked() {
        let (s, w) = POKER.solve(0.0, 0.0);
        assert!(s.abs() < 1e-9 && w.abs() < 1e-9, "({s}, {w})");
        let (rx, ry) = POKER.tip(0.0, 0.0);
        let (a0, r0) = (atan2(ry, rx).to_degrees(), sqrt(rx * rx + ry * ry));
        for (swing, reach) in [(30.0, 0.0), (-30.0, 0.0), (0.0, -40.0), (0.0, 10.0), (20.0, -30.0), (-25.0, 5.0)] {
            let (s, w) = POKER.solve(swing, reach);
            let (x, y) = POKER.tip(s, w);
            let (a, r) = (atan2(y, x).to_degrees(), sqrt(x * x + y * y));
            assert!((a - a0 - swing).abs() < 1e-6 && (r - r0 - reach).abs() < 1e-6, "({swing}, {reach}) -> ({s:.1}, {w:.1}) = ({a:.1} deg, {r:.1} mm)");
        }
    }

    #[test]
    fn a_swing_is_the_shoulder_alone_and_reach_bends_the_wrist() {
        let (s, w) = POKER.solve(30.0, 0.0);
        assert!((s.abs() - 30.0).abs() < 1e-6 && w.abs() < 1e-6, "({s}, {w})");
        let (_, w) = POKER.solve(0.0, -40.0);
        assert!(w.abs() > 5.0, "reaching in bends the wrist: {w}");
    }

    #[test]
    fn out_of_reach_stays_on_the_line_and_never_flips_the_elbow() {
        let (s, w) = POKER.solve(0.0, 500.0);
        assert!(s.is_finite() && w.is_finite());
        let (s2, w2) = POKER.solve(0.0, 20.0);
        assert!(w2.signum() == w.signum() || w2.abs() < 1e-6 || w.abs() < 1e-6, "same bend side");
        let _ = s2;
    }
}
