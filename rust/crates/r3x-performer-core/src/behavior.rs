//! Procedural performance (port of `behavior.ts`'s `Performer`): a layered stack after
//! Disney's gaze-controller subsumption layers and van Breemen's iCat engine, producing
//! *targets only* - actuation decides how fast anything gets there.
//!
//! ```text
//! L1 alive     breathing on lift/tilt (0.25 Hz), slow micro-motion >= 2 deg
//! L2 gaze      saccades with +/-20% timing jitter; the head leads and the rings follow
//!              200-350 ms later; anticipation (an 8% counter-move) before big turns
//! L3 activity  listening / thinking / speaking / DJ poses
//! L4 speech    envelope-driven bob (~3 Hz low-pass) plus accents on stressed syllables,
//!              and a small wobble driven by the same amplitude as the mouth LEDs
//! ```
//!
//! Every alive layer is individually switchable ([`AliveLayers`]); with all on, the output
//! is the TS reference's. The host resolves the look target to (pan, tilt) degrees in the
//! head_pan parent frame (the TS `aimAt`, which needs the 3D rig).

use crate::rng::Rng;
use crate::show::body::{get, Pose};
use libm::{exp, floor, sin};
use serde::{Deserialize, Serialize};
use std::f64::consts::PI;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Default, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Activity {
    #[default]
    Idle,
    Engaged,
    Listening,
    Thinking,
    Speaking,
    Dj,
}

/// A centre-weighted draw in `-max..max` (one uniform, cubed): most mass near 0, rare
/// excursions to the ends. Uses exactly one draw, like the uniform it replaced.
pub fn centred(rng: &mut Rng, max: f64) -> f64 {
    let s = 1.0 - 2.0 * rng.next_f64();
    max * s * s * s
}

/// The procedural layers that can be switched off (the Robot Profile's `alive` map; Bench
/// toggles them). Output enables gate drivers; these gate motion layers.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct AliveLayers {
    /// L1: breathing on lift and tilt.
    pub breathing: bool,
    /// L2: saccades - gaze retargeting (off: the gaze holds where it is).
    pub saccades: bool,
    /// L1/L2: slow idle micro-motion, and the rings following the head's pan.
    pub gaze_wander: bool,
    /// L4: speech bob, accents and wobble.
    pub speech_bob: bool,
}

impl Default for AliveLayers {
    fn default() -> Self {
        AliveLayers {
            breathing: true,
            saccades: true,
            gaze_wander: true,
            speech_bob: true,
        }
    }
}

impl AliveLayers {
    /// From a profile's `alive` map; a layer it does not name stays on.
    pub fn from_map(m: &std::collections::BTreeMap<String, bool>) -> Self {
        let on = |k: &str| m.get(k).copied().unwrap_or(true);
        AliveLayers {
            breathing: on("breathing"),
            saccades: on("saccades"),
            gaze_wander: on("gaze_wander"),
            speech_bob: on("speech_bob"),
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct PerformContext {
    /// Speech amplitude 0..1 as the host sees it (post-AGC).
    pub amplitude: f64,
    /// (pan, tilt) degrees that would point the face at the listener, or None.
    pub look: Option<(f64, f64)>,
    pub bpm: f64,
    /// Mood gain from the puppeteer's `energy` intent (1 = neutral).
    pub energy: f64,
}

#[derive(Clone, Copy, Debug, Default, PartialEq)]
struct Gaze {
    pan: f64,
    tilt: f64,
}

#[derive(Clone, Debug)]
pub struct Procedural {
    pub activity: Activity,
    pub layers: AliveLayers,
    since: f64,
    gaze: Gaze,
    gaze_from: Gaze,
    gaze_at: f64,
    anticipate: f64,
    next_saccade: f64,
    pan_history: std::collections::VecDeque<(f64, f64)>,
    env_fast: f64,
    env_slow: f64,
    bob: f64,
    accent_at: f64,
    accent_gain: f64,
    still: f64,
    anchor: Gaze,
    glance_back: bool,
    wobble_phase: f64,
    /// While set and a look target exists: the gaze tracks the target plus this (pan, tilt)
    /// offset every frame, so a moving listener is followed between saccades.
    follow: Option<(f64, f64)>,
}

impl Default for Procedural {
    fn default() -> Self {
        Procedural {
            activity: Activity::Idle,
            layers: AliveLayers::default(),
            since: 0.0,
            gaze: Gaze::default(),
            gaze_from: Gaze::default(),
            gaze_at: 0.0,
            anticipate: 0.0,
            next_saccade: 0.0,
            pan_history: Default::default(),
            env_fast: 0.0,
            env_slow: 0.0,
            bob: 0.0,
            accent_at: -10.0,
            accent_gain: 1.0,
            still: 0.0,
            anchor: Gaze::default(),
            glance_back: false,
            wobble_phase: 0.0,
            follow: None,
        }
    }
}

impl Procedural {
    pub fn set_activity(&mut self, a: Activity, t: f64) {
        if a == self.activity {
            return;
        }
        // Speaking hands off from listening: keep looking where the listener was.
        if a == Activity::Speaking {
            self.anchor = self.gaze;
        }
        self.activity = a;
        self.since = t;
        self.next_saccade = t;
        self.glance_back = false;
        self.follow = None;
    }

    /// 0..1: how frozen the listening hold is right now.
    pub fn listening_hold(&self) -> f64 {
        self.still
    }

    /// New gaze target, with anticipation on big moves.
    fn look(&mut self, t: f64, pan: f64, tilt: f64) {
        let jump = (pan - self.gaze.pan).abs();
        self.gaze_from = self.gaze;
        self.gaze = Gaze { pan, tilt };
        self.gaze_at = t;
        self.anticipate = if jump > 25.0 {
            -0.08 * (pan - self.gaze_from.pan)
        } else {
            0.0
        };
    }

    fn gaze_pan(&self, t: f64) -> f64 {
        // 150 ms counter-move, then commit to the target.
        if t - self.gaze_at < 0.15 {
            self.gaze_from.pan + self.anticipate
        } else {
            self.gaze.pan
        }
    }

    fn delayed(&self, t: f64, delay: f64) -> f64 {
        self.pan_history
            .iter()
            .rev()
            .find(|h| h.0 <= t - delay)
            .or(self.pan_history.front())
            .map_or(0.0, |h| h.1)
    }

    /// The procedural pose for every joint in `joints` (missing ones at 0).
    pub fn update(
        &mut self,
        t: f64,
        dt: f64,
        ctx: &PerformContext,
        rng: &mut Rng,
        joints: &[String],
    ) -> Pose {
        let since = t - self.since;
        let energy = ctx.energy;
        let jitter = |rng: &mut Rng| rng.range(0.8, 1.2) / (0.5 + 0.5 * energy);
        let mut pose = Pose::new();
        let set = |p: &mut Pose, j: &str, v: f64| {
            p.insert(j.to_owned(), v);
        };
        let look = ctx.look.unwrap_or((0.0, 0.0));
        let lay = self.layers;

        // ---------------------------------------------------------------- L4 speech envelope
        let a = ctx.amplitude;
        let k_fast = 1.0 - exp(-dt / if a > self.env_fast { 0.015 } else { 0.11 }); // 15 ms attack, 110 ms release
        self.env_fast += (a - self.env_fast) * k_fast;
        self.env_slow += (self.env_fast - self.env_slow) * (1.0 - exp(-dt / 1.5));
        self.bob += (self.env_fast - self.bob) * (1.0 - exp(-dt * 2.0 * PI * 3.0)); // ~3 Hz low-pass
        if self.activity == Activity::Speaking
            && self.env_fast > 0.5
            && self.env_fast - self.env_slow > 0.22
            && t - self.accent_at > 0.35
        {
            self.accent_at = t;
            self.accent_gain = rng.range(0.7, 1.2);
        }
        let (accent_at, accent_gain) = (self.accent_at, self.accent_gain);
        let bob_on = if lay.speech_bob { 1.0 } else { 0.0 };
        let accent = |dur: f64| {
            let u = (t - accent_at) / dur;
            if (0.0..=1.0).contains(&u) {
                sin(PI * u) * accent_gain * bob_on
            } else {
                0.0
            }
        };
        let bob = self.bob * bob_on;
        let saccades = lay.saccades;

        // ---------------------------------------------------------------- L2 gaze + L3 activity
        match self.activity {
            Activity::Idle | Activity::Engaged => {
                let calm = self.activity == Activity::Idle;
                if saccades && t >= self.next_saccade {
                    let attend = !calm && rng.next_f64() < 0.6;
                    // Centre-weighted around the target (straight ahead without one): ~80% of
                    // glances within +/-15 deg of it, ~6% past 25. The TS reference drew
                    // uniformly over +/-45, which read as staring off to one side.
                    let pan = look.0 + if attend { 0.0 } else { centred(rng, if calm { 30.0 } else { 25.0 }) };
                    let tilt = look.1 + if attend { 0.0 } else { -2.0 + centred(rng, 8.0) };
                    self.follow = ctx.look.map(|l| (pan - l.0, tilt - l.1));
                    self.look(t, pan, tilt);
                    let j = jitter(rng);
                    self.next_saccade =
                        t + (if calm { 3.5 } else { 2.4 }) * j * rng.range(0.7, 1.4);
                }
                set(&mut pose, "visor", if calm { 0.0 } else { -3.0 });
            }
            Activity::Listening => {
                // Orient to the listener once, then hold (Reachy's listening freeze).
                if t >= self.next_saccade {
                    self.look(t, look.0, look.1 - 4.0);
                    self.follow = ctx.look.map(|_| (0.0, -4.0));
                    self.next_saccade = f64::INFINITY;
                }
                set(&mut pose, "visor", -8.0); // brow up: attentive
                set(&mut pose, "head_lift", 6.0);
                set(&mut pose, "hero_shoulder", 6.0);
            }
            Activity::Thinking => {
                if saccades && t >= self.next_saccade {
                    let mag = rng.range(15.0, 30.0);
                    let side = if rng.next_f64() < 0.5 { -1.0 } else { 1.0 };
                    self.look(t, mag * side, -14.0);
                    self.follow = None;
                    self.next_saccade = t + 1.1 * jitter(rng);
                }
                set(&mut pose, "visor", 6.0); // brow down: concentrating
                set(&mut pose, "hero_wrist", sin(since * PI * 1.6) * 25.0);
                let claw = 3.0 + ((sin(since * PI * 5.0) + 1.0) / 2.0) * 12.0;
                for j in ["hero_claw_l", "hero_claw_r", "hero_claw_t"] {
                    set(&mut pose, j, claw);
                }
            }
            Activity::Speaking => {
                // Handoff: the gaze stays anchored on the listener (the camera when we have one).
                if ctx.look.is_some() {
                    self.anchor = Gaze {
                        pan: look.0,
                        tilt: look.1,
                    };
                }
                if saccades && t >= self.next_saccade {
                    if self.glance_back {
                        let p = self.anchor.pan + rng.spread(4.0);
                        self.look(t, p, self.anchor.tilt);
                        self.glance_back = false;
                        self.next_saccade = t + 1.3 * jitter(rng);
                    } else if rng.next_f64() < 0.2 {
                        let side = if rng.next_f64() < 0.5 { -1.0 } else { 1.0 };
                        let p = self.anchor.pan + side * rng.range(8.0, 14.0);
                        let tl = self.anchor.tilt + rng.spread(4.0);
                        self.look(t, p, tl);
                        self.glance_back = true;
                        self.next_saccade = t + rng.range(0.5, 0.9);
                    } else {
                        let p = self.anchor.pan + rng.spread(4.0);
                        self.look(t, p, self.anchor.tilt);
                        self.next_saccade = t + 1.3 * jitter(rng);
                    }
                    self.follow = ctx.look.map(|l| (self.gaze.pan - l.0, self.gaze.tilt - l.1));
                }
                set(&mut pose, "head_lift", 3.0 + bob * 5.0 + accent(0.3) * 6.0);
                set(&mut pose, "visor", -4.0 - bob * 4.0 - accent(0.25) * 9.0);
                set(
                    &mut pose,
                    "hero_shoulder",
                    4.0 + bob * 10.0 + accent(0.5) * 14.0,
                );
                set(&mut pose, "hero_wrist", sin(t * 1.7) * 30.0);
                for j in ["hero_claw_l", "hero_claw_r", "hero_claw_t"] {
                    set(&mut pose, j, 4.0 + bob * 20.0);
                }
                set(&mut pose, "throttle_shoulder", sin(t * 1.1) * 12.0);
                set(&mut pose, "throttle_elbow", -10.0 + sin(t * 1.4) * 12.0);
                set(&mut pose, "poker_shoulder", -4.0 + bob * 8.0);
            }
            Activity::Dj => {
                // R-3X Animation show: alternate lift/visor every beat.
                let beat = t * ctx.bpm / 60.0;
                let n = floor(beat) as i64;
                let up = n.rem_euclid(2) == 0;
                let idx = |x: i64, m: i64| (x.rem_euclid(m)) as usize;
                set(&mut pose, "head_lift", if up { 14.0 } else { 3.0 });
                set(&mut pose, "visor", if up { -11.0 } else { 0.0 });
                set(
                    &mut pose,
                    "hero_wrist",
                    [60.0, 0.0, -60.0, 0.0][idx(n.div_euclid(2), 4)],
                );
                set(
                    &mut pose,
                    "hero_shoulder",
                    [0.0, 14.0][idx(n.div_euclid(4), 2)],
                );
                let bar = idx(n.div_euclid(8), 4);
                set(&mut pose, "torso_lower", [0.0, 18.0, 0.0, -18.0][bar]);
                set(&mut pose, "torso_top", [14.0, 0.0, -14.0, 0.0][bar]);
                set(&mut pose, "torso_middle", [0.0, -12.0, 0.0, 12.0][bar]);
                self.gaze = Gaze {
                    pan: [20.0, -20.0, 0.0, 10.0][bar],
                    tilt: if up { -2.0 } else { 4.0 },
                };
                self.gaze_at = -10.0;
                for j in ["hero_claw_l", "hero_claw_r", "hero_claw_t"] {
                    set(&mut pose, j, if up { 22.0 } else { 4.0 });
                }
                set(
                    &mut pose,
                    "throttle_shoulder",
                    if up { 14.0 } else { -14.0 },
                );
                set(&mut pose, "throttle_wrist", if up { 20.0 } else { -20.0 });
                set(&mut pose, "poker_wrist", if up { -18.0 } else { 18.0 });
                set(&mut pose, "poker_claw_upper", if up { 4.0 } else { 20.0 });
                set(&mut pose, "poker_claw_lower", if up { 4.0 } else { 20.0 });
            }
        }

        // ---------------------------------------------------------------- follow a live target
        if let (Some((dp, dt_)), Some((lp, lt))) = (self.follow, ctx.look) {
            if self.activity != Activity::Dj {
                self.gaze = Gaze { pan: lp + dp, tilt: lt + dt_ };
            }
        }

        // ---------------------------------------------------------------- head leads, body follows
        let pan = self.gaze_pan(t);
        self.pan_history.push_back((t, pan));
        while self.pan_history.len() > 2 && self.pan_history[0].0 < t - 1.0 {
            self.pan_history.pop_front();
        }
        set(&mut pose, "head_pan", pan);
        let speak_tilt = if self.activity == Activity::Speaking {
            bob * 5.0 + accent(0.22) * 5.0
        } else {
            0.0
        };
        let tilt = get(&pose, "head_tilt") + self.gaze.tilt + speak_tilt;
        set(&mut pose, "head_tilt", tilt);
        if self.activity != Activity::Dj && lay.gaze_wander {
            let top = get(&pose, "torso_top") + self.delayed(t, 0.2) * 0.3;
            let lower = get(&pose, "torso_lower") + self.delayed(t, 0.35) * 0.12;
            set(&mut pose, "torso_top", top);
            set(&mut pose, "torso_lower", lower);
        }

        // ---------------------------------------------------------------- speech wobble
        // Small, incommensurate sines whose rate and depth follow the speech envelope.
        self.wobble_phase += dt * 2.0 * PI * (1.6 + 2.2 * self.env_fast);
        let w = self.env_fast;
        if w > 0.01 && lay.speech_bob {
            let ph = self.wobble_phase;
            let v = get(&pose, "head_pan") + w * 1.8 * sin(ph);
            set(&mut pose, "head_pan", v);
            let v = get(&pose, "head_tilt") + w * 1.2 * sin(1.37 * ph + 1.1);
            set(&mut pose, "head_tilt", v);
            let v = get(&pose, "head_lift") + w * sin(0.71 * ph + 2.3);
            set(&mut pose, "head_lift", v);
        }

        // ---------------------------------------------------------------- L1 alive
        // Damped while the listening freeze holds; eases back over ~1 s when it lets go.
        let listening = if self.activity == Activity::Listening {
            1.0
        } else {
            0.0
        };
        self.still += (listening - self.still) * (1.0 - exp(-dt / 0.35));
        let alive = (1.0 - 0.85 * self.still) * energy;
        if lay.breathing {
            let breath = sin(2.0 * PI * 0.25 * t);
            let v = get(&pose, "head_lift") + breath * 1.5 * alive;
            set(&mut pose, "head_lift", v);
            let v = get(&pose, "head_tilt") + sin(2.0 * PI * 0.25 * t + 1.2) * 1.2 * alive;
            set(&mut pose, "head_tilt", v);
        }
        if lay.gaze_wander && matches!(self.activity, Activity::Idle | Activity::Engaged) {
            for (j, f, ph, amp) in [
                ("torso_middle", 0.11, 0.0, 3.0),
                ("hero_shoulder", 0.17, 2.0, 2.5),
                ("throttle_elbow", 0.13, 4.0, 2.5),
            ] {
                let v = get(&pose, j) + sin(2.0 * PI * f * t + ph) * amp * alive;
                set(&mut pose, j, v);
            }
        }

        joints.iter().map(|j| (j.clone(), get(&pose, j))).collect()
    }
}
