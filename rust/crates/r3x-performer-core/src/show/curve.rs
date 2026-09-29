//! Keyframe evaluation (port of `curve.ts`). Min-jerk (Flash & Hogan) between keys by
//! default: each segment starts and ends at rest. The actuation follower stays downstream.

use super::types::{Ease, Track};

/// Min-jerk position profile on s in [0,1].
pub fn minjerk(s: f64) -> f64 {
    s * s * s * (10.0 + s * (-15.0 + 6.0 * s))
}

fn ease(e: Ease, s: f64) -> f64 {
    match e {
        Ease::Step => 0.0,
        Ease::Linear => s,
        Ease::Minjerk => minjerk(s),
    }
}

/// Track value at clip time `u` (seconds at speed 1); holds the end keys outside the range.
pub fn eval_track(tr: &Track, u: f64) -> f64 {
    let k = &tr.keys;
    if u <= k[0][0] {
        return k[0][1];
    }
    let last = k[k.len() - 1];
    if u >= last[0] {
        return last[1];
    }
    let mut i = 1;
    while k[i][0] < u {
        i += 1;
    }
    let [t0, v0] = k[i - 1];
    let [t1, v1] = k[i];
    v0 + (v1 - v0) * ease(tr.ease.unwrap_or_default(), (u - t0) / (t1 - t0))
}

/// Peak |velocity| and |acceleration| of a track at speed 1 and intensity 1, with the
/// segment start time of each. Min-jerk segments are exact (v = 1.875 D/T,
/// a = 10/sqrt(3) D/T^2); linear segments have infinite acceleration, step infinite velocity.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct Peaks {
    pub v: f64,
    pub a: f64,
    pub v_at: f64,
    pub a_at: f64,
}

pub fn track_peaks(tr: &Track) -> Peaks {
    let mut p = Peaks::default();
    let e = tr.ease.unwrap_or_default();
    for w in tr.keys.windows(2) {
        let ([t0, v0], [t1, v1]) = (w[0], w[1]);
        let d = (v1 - v0).abs();
        let t = t1 - t0;
        if d == 0.0 {
            continue;
        }
        let (sv, sa) = match e {
            Ease::Minjerk => (1.875 * d / t, (10.0 / 3f64.sqrt()) * d / (t * t)),
            Ease::Linear => (d / t, f64::INFINITY),
            Ease::Step => (f64::INFINITY, f64::INFINITY),
        };
        if sv > p.v {
            p.v = sv;
            p.v_at = t0;
        }
        if sa > p.a {
            p.a = sa;
            p.a_at = t0;
        }
    }
    p
}
