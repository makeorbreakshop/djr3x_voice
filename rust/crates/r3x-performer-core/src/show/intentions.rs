//! Intentions (2026-10-01, mirroring Threepio's `{mood:}`/`{gesture:}` vocabulary): who
//! decides *what* R3X feels or does (Claude, Jev, the listening reactor, the pad, idle) names
//! an intention - `amused`, `nod`, `drop` - and the performer plays a varied pick from that
//! intention's pool, so the same feeling never looks canned.
//!
//! `show/intentions.json`:
//! ```json
//! {"intentions": [{"id": "amused", "kind": "mood", "description": "finds it funny",
//!                  "pool": ["chuckle_bob", "head_shake_laugh"], "cooldown_s": 2.0}]}
//! ```
//! Kinds: `mood`, `gesture`, `beat` (Claude's vocabulary, as Threepio's), `look`, and `listen`
//! (small backchannel reactions fired while the guest talks; never offered to Claude).
//! A pool names clips or cues of tier `free` or `cheap` (intentions fire from Jev and Claude).
//!
//! The pick ([`Picker`]): uniform over the pool minus the last picks (up to two, never the
//! whole pool), intensity x[`PICK_INTENSITY`], speed x[`PICK_SPEED`], and nothing while the
//! intention is inside its cooldown (default [`DEFAULT_COOLDOWN_S`]). The performer also
//! scales the intensity by the expressiveness (the puppeteer's energy, 0.4-1.6x).

use std::collections::{HashMap, VecDeque};

use indexmap::IndexMap;
use serde::{Deserialize, Serialize};

use crate::rng::Rng;

pub const DEFAULT_COOLDOWN_S: f64 = 1.5;
/// How many recent picks a pool avoids (fewer when the pool is small).
pub const AVOID_RECENT: usize = 2;
/// A pick's intensity range (x the requested intensity). 2026-10-01: was 0.8-1.05, which
/// made the average reaction smaller than authored ("the animations feel subdued").
pub const PICK_INTENSITY: (f64, f64) = (0.9, 1.15);
/// A pick's speed range.
pub const PICK_SPEED: (f64, f64) = (0.9, 1.12);

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum IntentKind {
    Mood,
    Gesture,
    Beat,
    Look,
    Listen,
}

impl IntentKind {
    pub fn as_str(self) -> &'static str {
        match self {
            IntentKind::Mood => "mood",
            IntentKind::Gesture => "gesture",
            IntentKind::Beat => "beat",
            IntentKind::Look => "look",
            IntentKind::Listen => "listen",
        }
    }
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Intention {
    pub id: String,
    pub kind: IntentKind,
    pub description: String,
    pub pool: Vec<String>,
    #[serde(default)]
    pub cooldown_s: Option<f64>,
}

#[derive(Deserialize)]
struct File {
    intentions: Vec<Intention>,
}

/// Parse `intentions.json`; problems are returned as messages (the catalogue reports them).
pub fn parse(text: &str, path: &str) -> (IndexMap<String, Intention>, Vec<String>) {
    let mut errs = vec![];
    let mut out = IndexMap::new();
    match serde_json::from_str::<File>(text) {
        Err(e) => errs.push(format!("{path}: {e}")),
        Ok(f) => {
            for i in f.intentions {
                if i.pool.is_empty() {
                    errs.push(format!("{path}: intention {} has an empty pool", i.id));
                    continue;
                }
                if out.contains_key(&i.id) {
                    errs.push(format!("{path}: intention {} defined twice", i.id));
                    continue;
                }
                out.insert(i.id.clone(), i);
            }
        }
    }
    (out, errs)
}

/// What one pick plays.
#[derive(Clone, Debug, PartialEq)]
pub struct Pick {
    pub item: String,
    pub intensity: f64,
    pub speed: f64,
}

#[derive(Debug, Default)]
pub struct Picker {
    recent: HashMap<String, VecDeque<String>>,
    last_at: HashMap<String, f64>,
}

impl Picker {
    /// A varied pick for `intent` at time `now`, scaled by `intensity`; None inside the
    /// cooldown. `playable` filters the pool (items the catalogue has and the source may
    /// play); an empty filtered pool is None.
    pub fn pick(&mut self, intent: &Intention, now: f64, intensity: f64, rng: &mut Rng, playable: impl Fn(&str) -> bool) -> Option<Pick> {
        let cooldown = intent.cooldown_s.unwrap_or(DEFAULT_COOLDOWN_S);
        if self.last_at.get(&intent.id).is_some_and(|t| now - t < cooldown) {
            return None;
        }
        let pool: Vec<&String> = intent.pool.iter().filter(|p| playable(p)).collect();
        if pool.is_empty() {
            return None;
        }
        let recent = self.recent.entry(intent.id.clone()).or_default();
        let avoid = AVOID_RECENT.min(pool.len() - 1);
        let fresh: Vec<&String> = pool.iter().copied().filter(|p| !recent.iter().take(avoid).any(|r| r == *p)).collect();
        let k = ((rng.next_f64() * fresh.len() as f64) as usize).min(fresh.len() - 1);
        let item = fresh[k].clone();
        recent.push_front(item.clone());
        recent.truncate(AVOID_RECENT);
        self.last_at.insert(intent.id.clone(), now);
        let intensity = intensity * (PICK_INTENSITY.0 + (PICK_INTENSITY.1 - PICK_INTENSITY.0) * rng.next_f64());
        let speed = PICK_SPEED.0 + (PICK_SPEED.1 - PICK_SPEED.0) * rng.next_f64();
        Some(Pick { item, intensity, speed })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn intent(pool: &[&str], cooldown: Option<f64>) -> Intention {
        Intention { id: "amused".into(), kind: IntentKind::Mood, description: "".into(), pool: pool.iter().map(|s| s.to_string()).collect(), cooldown_s: cooldown }
    }

    #[test]
    fn a_pool_varies_and_never_repeats_the_last_two() {
        let mut p = Picker::default();
        let mut rng = Rng::new(7);
        let i = intent(&["a", "b", "c", "d"], Some(0.0));
        let picks: Vec<String> = (0..60).map(|k| p.pick(&i, k as f64, 1.0, &mut rng, |_| true).unwrap().item).collect();
        for w in picks.windows(3) {
            assert!(w[2] != w[1] && w[2] != w[0], "{w:?}");
        }
        for id in ["a", "b", "c", "d"] {
            assert!(picks.iter().any(|x| x == id), "{id} never picked");
        }
    }

    #[test]
    fn a_pool_of_two_alternates_and_of_one_repeats() {
        let mut p = Picker::default();
        let mut rng = Rng::new(1);
        let two = intent(&["a", "b"], Some(0.0));
        let picks: Vec<String> = (0..6).map(|k| p.pick(&two, k as f64, 1.0, &mut rng, |_| true).unwrap().item).collect();
        assert!(picks.windows(2).all(|w| w[0] != w[1]), "{picks:?}");
        let mut p = Picker::default();
        let one = intent(&["a"], Some(0.0));
        assert_eq!(p.pick(&one, 0.0, 1.0, &mut rng, |_| true).unwrap().item, "a");
        assert_eq!(p.pick(&one, 1.0, 1.0, &mut rng, |_| true).unwrap().item, "a");
    }

    #[test]
    fn cooldown_intensity_speed_and_the_playable_filter() {
        let mut p = Picker::default();
        let mut rng = Rng::new(3);
        let i = intent(&["a", "b"], None);
        let first = p.pick(&i, 10.0, 0.6, &mut rng, |_| true).unwrap();
        assert!((0.6 * PICK_INTENSITY.0..=0.6 * PICK_INTENSITY.1).contains(&first.intensity) && (PICK_SPEED.0..=PICK_SPEED.1).contains(&first.speed), "{first:?}");
        assert!(p.pick(&i, 10.0 + DEFAULT_COOLDOWN_S - 0.1, 1.0, &mut rng, |_| true).is_none(), "cooling down");
        assert!(p.pick(&i, 10.0 + DEFAULT_COOLDOWN_S + 0.1, 1.0, &mut rng, |_| true).is_some());
        assert_eq!(p.pick(&i, 20.0, 1.0, &mut rng, |x| x == "b").unwrap().item, "b");
        assert!(p.pick(&i, 30.0, 1.0, &mut rng, |_| false).is_none(), "nothing playable");
    }

    #[test]
    fn parse_reports_empty_pools_and_duplicates() {
        let (m, errs) = parse(r#"{"intentions":[{"id":"a","kind":"mood","description":"x","pool":["n"]},{"id":"a","kind":"mood","description":"y","pool":["n"]},{"id":"b","kind":"listen","description":"z","pool":[]}]}"#, "show/intentions.json");
        assert_eq!(m.len(), 1);
        assert_eq!(errs.len(), 2, "{errs:?}");
    }
}
