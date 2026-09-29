//! Player-level parity with the TypeScript reference: traces written by
//! `sim/web/scripts/gen-performer-parity.mjs` (removed with the TS player in Phase 3; the
//! recorded traces are the frozen reference) (blend, interruption, beat clock, idle, the
//! procedural layers, the actuation pipeline). The Rust side replays the same inputs.

mod common;

use common::{json_eq, read_json, show_catalog};
use r3x_performer_core::actuation::pipeline::{rig_joints, servo_map, Actuation};
use r3x_performer_core::behavior::{Activity, PerformContext, Procedural};
use r3x_performer_core::rng::Rng;
use r3x_performer_core::show::body::{BodyCompositor, PlayRequest, Pose};
use r3x_performer_core::show::catalog::Catalog;
use r3x_performer_core::show::idle::{IdleAction, IdleContext, IdleRunner};
use r3x_performer_core::show::player::{
    DispatchCtx, EndReason, PlayerHost, PlayerOptions, RunInfo, RunLayer, ShowPlayer, StopSel,
};
use r3x_performer_core::show::types::{Action, Params, Source};
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::sync::Arc;

/// Show, body, idle and actuation are bit-identical to the TS reference. The procedural
/// layers go through sin/exp, where libm and V8 may differ in the last ulp.
const EXACT: f64 = 0.0;
const ULPS: f64 = 1e-12;

fn f(v: &Value) -> f64 {
    v.as_f64().unwrap_or_else(|| panic!("not a number: {v}"))
}

fn assert_close(got: &[f64], want: &Value, tol: f64, what: &str) -> f64 {
    let want = want.as_array().unwrap();
    assert_eq!(got.len(), want.len(), "{what}: length");
    let mut worst: f64 = 0.0;
    for (i, (g, w)) in got.iter().zip(want).enumerate() {
        let d = (g - f(w)).abs();
        assert!(d <= tol, "{what}[{i}]: rust {g} vs ts {} (diff {d})", f(w));
        worst = worst.max(d);
    }
    worst
}

/// Player + body + idle wired as the TS harness (and sim/web/src/main.ts) wires them.
struct Harness {
    cat: Arc<Catalog>,
    body: BodyCompositor,
    idle: IdleRunner,
    events: Vec<Value>,
    speech: bool,
    bpm: Option<f64>,
    now: f64,
}

enum Ev {
    D(Action, RunInfo, f64),
    S(RunInfo),
    E(RunInfo, EndReason),
}

struct Rec(Vec<Ev>, bool, Option<f64>);

impl PlayerHost for Rec {
    fn dispatch(&mut self, a: &Action, ctx: DispatchCtx) {
        self.0.push(Ev::D(a.clone(), ctx.run.clone(), ctx.at));
    }
    fn speech_active(&self) -> bool {
        self.1
    }
    fn live_bpm(&self) -> Option<f64> {
        self.2
    }
    fn started(&mut self, run: &RunInfo) {
        self.0.push(Ev::S(run.clone()));
    }
    fn ended(&mut self, run: &RunInfo, reason: EndReason) {
        self.0.push(Ev::E(run.clone(), reason));
    }
}

impl Harness {
    fn rec(&self) -> Rec {
        Rec(vec![], self.speech, self.bpm)
    }

    fn apply(&mut self, r: Rec) {
        let now = self.now;
        for e in r.0 {
            match e {
                Ev::D(a, run, at) => {
                    self.events.push(
                        json!({"t": now, "ev": "dispatch", "run": run.run_id, "at": at, "a": a}),
                    );
                    if let Action::Clip {
                        id,
                        intensity,
                        speed,
                    } = &a
                    {
                        if let Some(c) = self.cat.clip(id) {
                            self.body.play(PlayRequest {
                                run_id: run.run_id.clone(),
                                clip: c.clone(),
                                intensity: *intensity,
                                speed: *speed,
                                layer: run.layer,
                                owns: run.owns.clone(),
                                t0: at,
                            });
                        }
                    }
                }
                Ev::S(run) => {
                    self.events.push(json!({"t": now, "ev": "started", "run": run.run_id, "id": run.id, "layer": run.layer, "owns": run.owns}));
                    if let Some(o) = &run.owns {
                        self.body.own(&run.run_id, run.layer, o, now);
                    }
                }
                Ev::E(run, reason) => {
                    self.events.push(json!({"t": now, "ev": "ended", "run": run.run_id, "id": run.id, "reason": reason}));
                    self.body.release(&run.run_id, now);
                    self.idle.ended(&run.run_id, now);
                }
            }
        }
    }

    fn stop(&mut self, p: &mut ShowPlayer, sel: &StopSel) {
        let mut r = self.rec();
        p.stop(&mut r, sel, self.now);
        self.apply(r);
    }

    fn perform(
        &mut self,
        p: &mut ShowPlayer,
        id: &str,
        source: Source,
        params: Params,
        layer: Option<RunLayer>,
    ) -> Option<String> {
        let mut r = self.rec();
        let run = p.perform(&mut r, id, source, params, self.now, layer);
        self.apply(r);
        run
    }

    fn poke(&mut self, p: &mut ShowPlayer) {
        if let Some(run) = self.idle.poke(self.now) {
            self.stop(p, &StopSel::id(run));
        }
    }
}

fn replay_show(name: &str) {
    let doc = read_json(&format!(
        "rust/crates/r3x-performer-core/tests/parity/perf_{name}.json"
    ));
    let cat = Arc::new(show_catalog());
    let joints: Vec<String> = serde_json::from_value(doc["joints"].clone()).unwrap();
    let base: Pose = joints
        .iter()
        .cloned()
        .zip(doc["base"].as_array().unwrap().iter().map(f))
        .collect();
    let use_idle = name == "idle";
    let mut h = Harness {
        idle: IdleRunner::new(if use_idle { cat.idle.clone() } else { None }, 0.0),
        cat: cat.clone(),
        body: BodyCompositor::new(),
        events: vec![],
        speech: false,
        bpm: None,
        now: 0.0,
    };
    let mut rng = Rng::new(if use_idle { 42 } else { 1 });
    let mut p = ShowPlayer::new(cat, PlayerOptions::default());
    let mut music = false;
    let mut frames = doc["frames"].as_array().unwrap().iter();
    let mut worst: f64 = 0.0;
    for op in doc["ops"].as_array().unwrap() {
        h.now = f(&op["t"]);
        match op["op"].as_str().unwrap() {
            "perform" => {
                let source = op
                    .get("source")
                    .and_then(Value::as_str)
                    .map_or(Source::Ui, |s| Source::parse(s).unwrap());
                if source != Source::Idle {
                    h.poke(&mut p);
                }
                let params: Params = op.get("params").map_or(Params::default(), |v| {
                    serde_json::from_value(v.clone()).unwrap()
                });
                let layer = op
                    .get("layer")
                    .map(|v| serde_json::from_value(v.clone()).unwrap());
                h.perform(&mut p, op["id"].as_str().unwrap(), source, params, layer);
            }
            "stop" => {
                let sel: StopSel = serde_json::from_value(op["sel"].clone()).unwrap();
                h.stop(&mut p, &sel);
            }
            "freeze" => {
                let on = op["on"].as_bool().unwrap();
                let mut r = h.rec();
                p.freeze(&mut r, on, h.now);
                h.apply(r);
                h.body.freeze(on, h.now, None);
                if on {
                    h.poke(&mut p);
                }
            }
            "bpm" => h.bpm = op["value"].as_f64(),
            "speech" => h.speech = op["on"].as_bool().unwrap(),
            "music" => music = op["on"].as_bool().unwrap(),
            "poke" => h.poke(&mut p),
            "tick" => {
                let mut r = h.rec();
                p.update(&mut r, h.now);
                h.apply(r);
                if use_idle {
                    let cur = h.idle.current().map(str::to_owned);
                    let eligible = !p.frozen
                        && p.running().iter().all(|r| {
                            Some(&r.run_id) == cur.as_ref() || r.layer == RunLayer::Background
                        });
                    match h
                        .idle
                        .update(h.now, IdleContext { eligible, music }, &mut rng)
                    {
                        Some(IdleAction::Perform(id)) => {
                            let run = h.perform(&mut p, &id, Source::Idle, Params::default(), None);
                            h.idle.started(run);
                        }
                        Some(IdleAction::Stop(run)) => h.stop(&mut p, &StopSel::id(run)),
                        None => {}
                    }
                }
                let mut pose = base.clone();
                h.body.apply(&mut pose, h.now, None);
                let got: Vec<f64> = joints
                    .iter()
                    .map(|j| pose.get(j).copied().unwrap_or(0.0))
                    .collect();
                let want = frames.next().expect("a frame per tick");
                worst = worst.max(assert_close(
                    &got,
                    &want["pose"],
                    EXACT,
                    &format!("{name} pose at t={}", h.now),
                ));
            }
            o => panic!("unknown op {o}"),
        }
    }
    assert!(frames.next().is_none());
    let want = doc["events"].as_array().unwrap();
    for (i, (g, w)) in h.events.iter().zip(want).enumerate() {
        assert!(json_eq(g, w), "{name} event {i}: rust {g} vs ts {w}");
    }
    assert_eq!(h.events.len(), want.len(), "{name}: event count");
    eprintln!("{name}: {} events, worst pose diff {worst:e}", want.len());
}

#[test]
fn parity_blend() {
    replay_show("blend");
}

#[test]
fn parity_interruption() {
    replay_show("interruption");
}

#[test]
fn parity_beat_clock() {
    replay_show("beat");
}

#[test]
fn parity_idle_policy() {
    replay_show("idle");
}

#[test]
fn parity_procedural_layers() {
    let doc = read_json("rust/crates/r3x-performer-core/tests/parity/perf_behavior.json");
    let joints: Vec<String> = serde_json::from_value(doc["joints"].clone()).unwrap();
    let mut acts: Vec<(f64, Activity)> = doc["activities"]
        .as_array()
        .unwrap()
        .iter()
        .map(|a| {
            (
                f(&a["t"]),
                serde_json::from_value(a["act"].clone()).unwrap(),
            )
        })
        .collect();
    let mut rng = Rng::new(doc["seed"].as_u64().unwrap() as u32);
    let mut p = Procedural::default();
    let mut worst: f64 = 0.0;
    for fr in doc["frames"].as_array().unwrap() {
        let t = f(&fr["t"]);
        for (at, a) in acts.iter_mut() {
            if *at <= t && !at.is_nan() {
                p.set_activity(*a, t);
                *at = f64::NAN;
            }
        }
        let ctx = PerformContext {
            amplitude: f(&fr["amplitude"]),
            look: None,
            bpm: f(&doc["bpm"]),
            energy: f(&fr["energy"]),
        };
        let pose = p.update(t, f(&fr["dt"]), &ctx, &mut rng, &joints);
        if !fr["pose"].is_null() {
            let got: Vec<f64> = joints.iter().map(|j| pose[j]).collect();
            worst = worst.max(assert_close(
                &got,
                &fr["pose"],
                ULPS,
                &format!("behavior at t={t}"),
            ));
        }
    }
    eprintln!("behavior: worst diff {worst:e}");
}

#[test]
fn parity_actuation_pipeline() {
    let doc = read_json("rust/crates/r3x-performer-core/tests/parity/perf_actuation.json");
    let mut a = Actuation::new(
        &rig_joints(),
        &BTreeMap::new(),
        &servo_map(),
        "r3x_animation",
        Rng::new(doc["seed"].as_u64().unwrap() as u32),
    )
    .unwrap();
    let names: Vec<String> = serde_json::from_value(doc["joints"].clone()).unwrap();
    let target = |j: &str, t: f64| -> f64 {
        match j {
            "head_pan" => 70.0 * libm::sin(t * 1.3),
            "head_tilt" => {
                if t > 2.0 {
                    18.0
                } else {
                    -5.0
                }
            }
            "head_lift" => 15.0 * libm::sin(t * 0.7),
            "visor" => {
                if t % 1.0 < 0.5 {
                    -12.0
                } else {
                    20.0
                }
            }
            "hero_shoulder" => 40.0,
            "hero_wrist" => -80.0 * libm::cos(t),
            "torso_lower" => {
                if t > 1.0 {
                    30.0
                } else {
                    0.0
                }
            }
            "torso_top" => -25.0,
            _ => 0.0,
        }
    };
    let all: Vec<String> = rig_joints().into_iter().map(|j| j.name).collect();
    let mut worst: f64 = 0.0;
    for fr in doc["frames"].as_array().unwrap() {
        let (t, dt) = (f(&fr["t"]), f(&fr["dt"]));
        for j in &all {
            a.command(j, target(j, t));
        }
        a.update(dt);
        assert_eq!(
            a.last_frame.frame,
            fr["frame"].as_u64().unwrap(),
            "frame no at t={t}"
        );
        let want_targets: Vec<i64> = serde_json::from_value(fr["targets"].clone()).unwrap();
        assert_eq!(a.last_frame.targets, want_targets, "pulses at t={t}");
        let v = a.joint_values();
        let got: Vec<f64> = names.iter().map(|j| v[j]).collect();
        worst = worst.max(assert_close(
            &got,
            &fr["values"],
            EXACT,
            &format!("joint values at t={t}"),
        ));
    }
    eprintln!("actuation: worst diff {worst:e}");
}
