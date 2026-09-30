//! The body compositor (port of `body.ts`): the animation engine between behaviour and
//! actuation. Over the procedural pose it stacks, bottom to top:
//!
//! ```text
//! 0 procedural   alive + gaze + activity + speech (behavior.rs)
//! 1 background   an authored loop per activity
//! 2 gesture      triggered clips and cues
//! 3 show         show elements (sequences)
//! 4 puppeteer    additive offsets on high-level intents (puppeteer.rs)
//! 5 freeze       motion stop: holds the setpoints; releases over 0.5 s
//! ```
//!
//! Per joint, a track is `additive` or `override` (by weight, ramped over the track's
//! blend). An interrupted clip freezes where it is and fades its weight out: it never cuts.
//! A run that `owns` joints holds them at the pose it started over; its override tracks are
//! masked to the owned joints. The output is only a target: actuation follows downstream.

use super::curve::{eval_track, minjerk};
use super::player::{RunLayer, RUN_LAYERS};
use super::puppeteer::Puppeteer;
use super::types::{default_blend, Clip, TrackMode};
use std::collections::{BTreeMap, HashSet};
use std::sync::Arc;

/// Joint name -> value (deg, or mm for `head_lift`). A missing joint reads as 0.
pub type Pose = BTreeMap<String, f64>;

pub(crate) fn get(p: &Pose, j: &str) -> f64 {
    p.get(j).copied().unwrap_or(0.0)
}

fn ramp(x: f64) -> f64 {
    if x <= 0.0 {
        0.0
    } else if x >= 1.0 {
        1.0
    } else {
        minjerk(x)
    }
}

#[derive(Clone, Debug)]
struct Play {
    run_id: String,
    clip: Arc<Clip>,
    intensity: f64,
    speed: f64,
    layer: RunLayer,
    mask: Option<HashSet<String>>,
    t0: f64,
    stop_at: Option<f64>,
    base: Option<BTreeMap<String, f64>>,
    /// Longest fade among the clip's tracks, for the tail.
    tail: f64,
    /// Studio scrub: evaluate at this clip time, fully weighted, until replaced.
    hold: Option<f64>,
}

#[derive(Clone, Debug)]
struct Own {
    run_id: String,
    layer: RunLayer,
    joints: Vec<String>,
    t0: f64,
    stop_at: Option<f64>,
    hold: Option<BTreeMap<String, f64>>,
}

#[derive(Clone, Debug)]
pub struct PlayRequest {
    pub run_id: String,
    pub clip: Arc<Clip>,
    pub intensity: Option<f64>,
    pub speed: Option<f64>,
    pub layer: RunLayer,
    /// Joints the run owns; override tracks outside it are masked.
    pub owns: Option<Vec<String>>,
    /// Clock time the clip starts (s).
    pub t0: f64,
}

#[derive(Clone, Debug, Default)]
pub struct BodyCompositor {
    plays: Vec<Play>,
    owns: Vec<Own>,
    frozen: bool,
    freeze_hold: Option<Pose>,
    freeze_release: f64,
    last: Pose,
}

impl BodyCompositor {
    pub const FREEZE_RELEASE_S: f64 = 0.5;

    pub fn new() -> Self {
        BodyCompositor {
            freeze_release: f64::NEG_INFINITY,
            ..Default::default()
        }
    }

    pub fn play(&mut self, req: PlayRequest) {
        // Longest fade any track needs (overrides use their blend; additive tracks fade over
        // the joint-class blend when interrupted).
        let tail = req
            .clip
            .tracks
            .iter()
            .map(|(j, tr)| {
                if tr.mode == TrackMode::Override {
                    tr.blend.unwrap_or_else(|| default_blend(j))
                } else {
                    default_blend(j)
                }
            })
            .fold(0.0, f64::max);
        self.plays.push(Play {
            run_id: req.run_id,
            clip: req.clip,
            intensity: req.intensity.unwrap_or(1.0),
            speed: req.speed.unwrap_or(1.0),
            layer: req.layer,
            mask: req.owns.map(|o| o.into_iter().collect()),
            t0: req.t0,
            stop_at: None,
            base: None,
            tail,
            hold: None,
        });
    }

    /// Studio preview: replace run `run_id` (cut, not blended) with `clip` at clip time
    /// `at`; `hold` pins it there (scrubbing), else it plays on from `at`. Actuation still
    /// follows downstream, so a scrub moves the body as the servos would.
    pub fn scrub(&mut self, run_id: &str, clip: Arc<Clip>, layer: RunLayer, at: f64, hold: bool, now: f64) {
        self.plays.retain(|p| p.run_id != run_id);
        self.play(PlayRequest {
            run_id: run_id.into(),
            clip,
            intensity: None,
            speed: None,
            layer,
            owns: None,
            t0: now - at,
        });
        if let Some(p) = self.plays.last_mut() {
            p.hold = hold.then_some(at);
        }
    }

    /// A run takes ownership of joints (sequence `owns`).
    pub fn own(&mut self, run_id: &str, layer: RunLayer, joints: &[String], t0: f64) {
        if joints.is_empty() || self.owns.iter().any(|o| o.run_id == run_id) {
            return;
        }
        self.owns.push(Own {
            run_id: run_id.into(),
            layer,
            joints: joints.to_vec(),
            t0,
            stop_at: None,
            hold: None,
        });
    }

    /// Blend out everything a run is doing (interrupt, stop, end). Never a cut.
    pub fn release(&mut self, run_id: &str, now: f64) {
        self.release_where(now, |id, _| id == run_id);
    }

    pub fn release_layer(&mut self, layer: RunLayer, now: f64) {
        self.release_where(now, |_, l| l == layer);
    }

    pub fn release_all(&mut self, now: f64) {
        for l in RUN_LAYERS {
            self.release_layer(l, now);
        }
    }

    fn release_where(&mut self, now: f64, f: impl Fn(&str, RunLayer) -> bool) {
        for p in self
            .plays
            .iter_mut()
            .filter(|p| p.stop_at.is_none() && f(&p.run_id, p.layer))
        {
            p.stop_at = Some(now);
        }
        for o in self
            .owns
            .iter_mut()
            .filter(|o| o.stop_at.is_none() && f(&o.run_id, o.layer))
        {
            o.stop_at = Some(now);
        }
    }

    /// motion.freeze: on holds the setpoints - `hold` if given (the followers' current
    /// positions, so motion stops where the servos are), else the last output; off blends
    /// back over 0.5 s.
    pub fn freeze(&mut self, on: bool, now: f64, hold: Option<&Pose>) {
        if on == self.frozen {
            return;
        }
        self.frozen = on;
        if on {
            self.release_all(now);
            let mut h = self.last.clone();
            if let Some(hold) = hold {
                h.extend(hold.iter().map(|(k, v)| (k.clone(), *v)));
            }
            self.freeze_hold = Some(h);
        } else {
            self.freeze_release = now;
        }
    }

    pub fn is_frozen(&self) -> bool {
        self.frozen
    }

    /// Clip ids still in on a layer (for the UI).
    pub fn active(&self, layer: RunLayer) -> Vec<&str> {
        self.plays
            .iter()
            .filter(|p| p.layer == layer && p.stop_at.is_none())
            .map(|p| p.clip.id.as_str())
            .collect()
    }

    /// Compose the layers over the procedural pose (mutated in place).
    pub fn apply(&mut self, pose: &mut Pose, t: f64, puppet: Option<&Puppeteer>) {
        for layer in RUN_LAYERS {
            for o in self.owns.iter_mut().filter(|o| o.layer == layer) {
                apply_own(o, pose, t);
            }
            for p in self
                .plays
                .iter_mut()
                .filter(|p| p.layer == layer && t >= p.t0)
            {
                apply_play(p, pose, t);
            }
        }
        if let Some(p) = puppet {
            p.apply(pose);
        }
        self.apply_freeze(pose, t);
        self.last = pose.clone();
        // Retire what has fully blended out.
        self.plays.retain(|p| {
            let end = p.stop_at.unwrap_or(p.t0 + p.clip.duration / p.speed);
            (p.hold.is_some() && p.stop_at.is_none()) || t < end + p.tail + 0.05
        });
        self.owns.retain(|o| o.stop_at.is_none_or(|s| t < s + 0.4));
    }

    fn apply_freeze(&mut self, pose: &mut Pose, t: f64) {
        let Some(hold) = &self.freeze_hold else {
            return;
        };
        let w = if self.frozen {
            1.0
        } else {
            1.0 - ramp((t - self.freeze_release) / Self::FREEZE_RELEASE_S)
        };
        if w <= 0.0 {
            self.freeze_hold = None;
            return;
        }
        for (j, h) in hold {
            let v = get(pose, j);
            pose.insert(j.clone(), v + (h - v) * w);
        }
    }
}

fn apply_own(o: &mut Own, pose: &mut Pose, t: f64) {
    let hold = o
        .hold
        .get_or_insert_with(|| o.joints.iter().map(|j| (j.clone(), get(pose, j))).collect());
    for j in &o.joints {
        let b = default_blend(j);
        let w = ramp((t - o.t0) / b) * o.stop_at.map_or(1.0, |s| 1.0 - ramp((t - s) / b));
        let v = get(pose, j);
        pose.insert(j.clone(), v + (hold[j] - v) * w);
    }
}

fn apply_play(p: &mut Play, pose: &mut Pose, t: f64) {
    let t_eval = p.stop_at.map_or(t, |s| t.min(s));
    let u = p.hold.unwrap_or((t_eval - p.t0) * p.speed);
    let held = p.hold.is_some();
    let end = if held { f64::INFINITY } else { p.t0 + p.clip.duration / p.speed };
    let clip = &p.clip;
    let base = p.base.get_or_insert_with(|| {
        clip.tracks
            .iter()
            .filter(|(_, tr)| tr.mode == TrackMode::Override)
            .map(|(j, _)| (j.clone(), get(pose, j)))
            .collect()
    });
    for (j, tr) in &clip.tracks {
        let value = eval_track(tr, u);
        if tr.mode == TrackMode::Additive {
            let w_stop = p
                .stop_at
                .map_or(1.0, |s| 1.0 - ramp((t - s) / default_blend(j)));
            pose.insert(j.clone(), get(pose, j) + value * p.intensity * w_stop);
            continue;
        }
        if p.mask.as_ref().is_some_and(|m| !m.contains(j)) {
            continue;
        }
        let b = tr.blend.unwrap_or_else(|| default_blend(j));
        let base = base.get(j).copied().unwrap_or(0.0);
        let target = base + p.intensity * (value - base);
        let w_in = if b > 0.0 && !held { ramp((t - p.t0) / b) } else { 1.0 };
        let w_out = if t > end {
            if b > 0.0 {
                1.0 - ramp((t - end) / b)
            } else {
                0.0
            }
        } else {
            1.0
        };
        let w_stop = match p.stop_at {
            None => 1.0,
            Some(s) if b > 0.0 => 1.0 - ramp((t - s) / b),
            Some(_) => 0.0,
        };
        let w = w_in * w_out * w_stop;
        let v = get(pose, j);
        pose.insert(j.clone(), v + (target - v) * w);
    }
}
