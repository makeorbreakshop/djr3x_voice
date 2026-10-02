//! Port of `sim/web/test/show.test.ts`, plus the hand-written goldens in `show/tests/`.

mod common;

use common::{approx, assert_json_eq, fixture_catalog, library_limits, original_profile, physical_profile, repo, show_catalog};
use r3x_performer_core::rng::Rng;
use r3x_performer_core::show::body::{BodyCompositor, PlayRequest, Pose};
use r3x_performer_core::show::catalog::{norm, Catalog};
use r3x_performer_core::show::curve::eval_track;
use r3x_performer_core::show::expand::expand;
use r3x_performer_core::show::idle::{pick, IdleAction, IdleContext, IdleRunner};
use r3x_performer_core::show::lint::{joint_limits, lint_catalog, sfx_stems};
use r3x_performer_core::show::player::{
    DispatchCtx, EndReason, PlayerHost, PlayerOptions, RunInfo, RunLayer, ShowPlayer, StopSel,
};
use r3x_performer_core::show::types::{
    tier_allows, Action, Clip, Ease, Kind, Params, Source, Tier, Track, TrackMode, WeightedId,
};
use serde_json::{json, Value};
use std::cell::Cell;
use std::rc::Rc;
use std::sync::Arc;

// ------------------------------------------------------------------ golden parity

/// Run the real player against a fake clock; record what it dispatches, as an expansion.
fn player_expansion(cat: Catalog, root: &str, bpm: f64) -> Value {
    struct H(Vec<(f64, Action)>, f64);
    impl PlayerHost for H {
        fn dispatch(&mut self, a: &Action, ctx: DispatchCtx) {
            self.0.push((ctx.at, a.clone()));
        }
        fn speech_active(&self) -> bool {
            false
        }
        fn live_bpm(&self) -> Option<f64> {
            Some(self.1)
        }
    }
    let mut h = H(vec![], bpm);
    let mut p = ShowPlayer::new(
        Arc::new(cat),
        PlayerOptions {
            wait_grace_s: 0.0,
            ..Default::default()
        },
    );
    let t0 = 10.0137; // an arbitrary anchor: nothing may depend on it
    p.perform(&mut h, root, Source::Timeline, Params::default(), t0, None);
    // Uneven frame times, including a stall: scheduling must not accumulate them.
    let dts = [1.0 / 60.0, 1.0 / 144.0, 0.05, 1.0 / 60.0, 0.2, 1.0 / 30.0];
    let (mut t, mut i) = (t0, 0);
    while t < t0 + 30.0 && !p.running().is_empty() {
        t += dts[i % dts.len()];
        i += 1;
        p.update(&mut h, t);
    }
    let mut out: Vec<(f64, Action)> =
        h.0.into_iter()
            .map(|(at, a)| (((at - t0) * 1000.0 + 0.5).floor() / 1000.0, a))
            .collect();
    out.sort_by(|a, b| a.0.total_cmp(&b.0));
    Value::Array(
        out.into_iter()
            .map(|(t, a)| {
                let mut v = serde_json::to_value(&a).unwrap();
                v.as_object_mut().unwrap().insert("t".into(), json!(t));
                v
            })
            .collect(),
    )
}

fn fixtures() -> Vec<String> {
    let mut v: Vec<String> = std::fs::read_dir(repo("show/tests/fixtures"))
        .unwrap()
        .map(|e| e.unwrap().file_name().into_string().unwrap())
        .filter(|n| n.ends_with(".json"))
        .collect();
    v.sort();
    v
}

fn golden(f: &str) -> Value {
    serde_json::from_str(
        &std::fs::read_to_string(repo(&format!("show/tests/golden/{f}"))).expect("golden exists"),
    )
    .unwrap()
}

#[test]
fn golden_has_fixtures_and_a_golden_file_for_each() {
    let f = fixtures();
    assert!(!f.is_empty());
    for x in f {
        assert!(repo(&format!("show/tests/golden/{x}")).exists(), "{x}");
    }
}

#[test]
fn golden_expand_reproduces_the_golden_files() {
    for f in fixtures() {
        let (cat, root, bpm) = fixture_catalog(&f);
        assert_eq!(cat.errors, Vec::<String>::new());
        assert_json_eq(
            &serde_json::to_value(expand(&root, &cat, Some(bpm)).unwrap()).unwrap(),
            &golden(&f),
            &f,
        );
    }
}

#[test]
fn golden_the_clock_driven_player_dispatches_at_the_golden_times() {
    for f in fixtures() {
        let (cat, root, bpm) = fixture_catalog(&f);
        assert_json_eq(&player_expansion(cat, &root, bpm), &golden(&f), &f);
    }
}

// ------------------------------------------------------------------ linter

#[test]
fn linter_loads_the_show_folder() {
    let cat = show_catalog();
    assert!(cat.items.len() > 30);
    assert!(cat.list(Kind::Clip).len() >= 25);
    assert!(cat.list(Kind::Cue).len() >= 12);
    assert!(cat.list(Kind::Sequence).len() >= 6);
}

/// The library's lint policy (`lint::library_limits`, the 2026-10-01 energy pass): every clip
/// stays inside the range BOTH rigs share, and inside the Physical build's velocity and
/// acceleration (the real servos'), not the Original profile's hand-set, conservative v_max.
#[test]
fn linter_every_file_validates_every_id_resolves_every_clip_is_performable() {
    let res = lint_catalog(&show_catalog(), &library_limits());
    assert_eq!(res.errors, Vec::<String>::new());
    if !res.warnings.is_empty() {
        eprintln!("show lint warnings:\n  {}", res.warnings.join("\n  "));
    }
}

#[test]
fn linter_every_show_item_expands_without_error() {
    let cat = show_catalog();
    for it in cat.items.values().filter(|i| i.kind() != Kind::Clip) {
        expand(&it.id, &cat, Some(120.0)).unwrap();
    }
}

#[test]
fn linter_catches_what_it_claims_to_catch() {
    let items = vec![
        json!({"id": "too_fast", "kind": "clip", "tier": "free", "description": "x", "duration": 0.3,
            "tracks": {"torso_top": {"mode": "additive", "keys": [[0, 0], [0.15, 20], [0.3, 0]]}}}),
        json!({"id": "claw", "kind": "clip", "tier": "free", "description": "x", "duration": 1,
            "tracks": {"hero_claw_l": {"mode": "override", "keys": [[0, 0], [1, 10]]}}}),
        json!({"id": "coupled", "kind": "clip", "tier": "free", "requires": "extended", "description": "x", "duration": 1,
            "tracks": {"hero_claw_r": {"mode": "override", "keys": [[0, 0], [1, 10]]}}}),
        json!({"id": "far", "kind": "clip", "tier": "free", "description": "x", "duration": 2,
            "tracks": {"head_pan": {"mode": "override", "keys": [[0, 0], [2, 69]]}}}),
        json!({"id": "c1", "kind": "cue", "tier": "free", "description": "x", "actions": [
            {"at": 0, "do": "clip", "id": "nope"}, {"at": 0, "do": "sfx", "id": "kazoo"}, {"at": 0, "do": "chest", "command": "Q1"}]}),
        json!({"id": "s1", "kind": "sequence", "tier": "free", "description": "x", "track": [{"at": 0, "sequence": "s2"}]}),
        json!({"id": "s2", "kind": "sequence", "tier": "show", "description": "x", "track": [{"at": 0, "sequence": "s3"}]}),
        json!({"id": "s3", "kind": "sequence", "tier": "show", "description": "x", "track": [{"at": 0, "sequence": "s4"}]}),
        json!({"id": "s4", "kind": "sequence", "tier": "show", "description": "x", "track": [{"at": 0, "do": "unduck"}]}),
    ];
    let bad = Catalog::new(
        items,
        Some(json!({"after_s": 10, "choices": [{"id": "s2", "weight": 1}]})),
    );
    let e = lint_catalog(&bad, &joint_limits()).errors.join("\n");
    for want in [
        "too_fast.torso_top: peak velocity",
        "too_fast.torso_top: peak acceleration",
        "claw.hero_claw_l: extended joint",
        "coupled.hero_claw_r: coupled joint",
        "far.head_pan: 69 at",
        "c1: unknown clip \"nope\"",
        "c1: sfx \"kazoo\"",
        "c1: chest \"Q1\"",
        "s1: nesting deeper than 3",
        "s1 (free) plays sequence s2 (show)",
        "idle.json: s2 is show",
    ] {
        assert!(e.contains(want), "missing {want:?} in\n{e}");
    }
    assert!(e.contains("outside"));
}

#[test]
fn linter_committed_kit_sfx_list_resolves_by_norm() {
    assert_eq!(
        sfx_stems().get(&norm("air_horn")).map(String::as_str),
        Some("Air Horn")
    );
}

#[test]
fn linter_soft_limits_are_the_pipelines() {
    let l = joint_limits();
    let hp = &l["head_pan"];
    assert_eq!(
        (hp.lo, hp.hi, hp.v_max, hp.base, hp.primary),
        (-66.0, 66.0, 150.0, true, true)
    );
    assert!(!l["torso_middle"].base);
}

#[test]
fn library_limits_are_the_shared_range_at_the_physical_build_speeds() {
    let (lib, o, p) = (library_limits(), original_profile(), physical_profile());
    let orig = joint_limits(); // the servo map: the Original rig
    assert_eq!(lib.keys().collect::<Vec<_>>(), orig.keys().collect::<Vec<_>>());
    for (j, l) in &lib {
        let (a, b) = (&o.joint(j).unwrap().animation, &p.joint(j).unwrap().animation);
        // errors past the Original rig; warnings (the Physical rig clamps) past the shared range
        assert_eq!((l.lo, l.hi), (a.min, a.max), "{j}: the Original rig's range");
        assert_eq!((l.warn_lo, l.warn_hi), (a.min.max(b.min), a.max.min(b.max)), "{j}: the range both rigs share");
        let pj = p.joint(j).unwrap();
        if l.primary {
            assert_eq!((l.v_max, l.a_max), (pj.v_max, pj.a_max), "{j}: the Physical build's motion limits");
        }
        assert!(l.lo >= orig[j].lo - 0.01 && l.hi <= orig[j].hi + 0.01, "{j}: never past the Original rig");
    }
    // the cases that matter: the neck and lift keep the Original rig's range, the top ring the
    // Physical one's (a warning past it); the tilt runs at the servo's speed through Hunter's cut-down
    // 32 mm horns (~2.4 servo deg per tilt deg: ~87 deg/s), and is coupled to the roll on the Physical rig
    assert_eq!((lib["head_pan"].lo, lib["head_pan"].hi, lib["head_pan"].v_max), (-66.0, 66.0, 210.0));
    assert_eq!((lib["head_lift"].warn_lo, lib["head_lift"].warn_hi), (-19.0, 19.0));
    assert!((lib["torso_top"].warn_hi - 23.46).abs() < 1e-9);
    assert!(lib["head_tilt"].v_max > 80.0 && lib["head_tilt"].v_max < 100.0, "{}", lib["head_tilt"].v_max);
    let c = lib["head_tilt"].coupling.as_ref().expect("the Physical rig's tilt-by-roll limit");
    assert_eq!(c.depends_on, "head_roll");
    let (r0, r12) = (c.range_at(0.0).unwrap().1, c.range_at(12.0).unwrap().1);
    assert!(r0 > r12 && r12 >= 7.5, "{r0} {r12}");
}

#[test]
fn tiers_follow_the_spec_table() {
    assert!(tier_allows(Tier::Free, Source::Idle));
    assert!(!tier_allows(Tier::Cheap, Source::Idle));
    assert!(tier_allows(Tier::Cheap, Source::Jev));
    assert!(!tier_allows(Tier::Show, Source::Jev));
    assert!(!tier_allows(Tier::Show, Source::Idle));
    for s in [Source::Claude, Source::Timeline, Source::Ui, Source::Cli] {
        assert!(tier_allows(Tier::Show, s));
    }
}

// ------------------------------------------------------------------ player behaviour

fn clip(id: &str, duration: f64, extra: Value) -> Value {
    let mut v = json!({"id": id, "kind": "clip", "tier": "free", "description": id, "duration": duration,
        "tracks": {"head_tilt": {"mode": "additive", "keys": [[0, 0], [duration / 2.0, 5], [duration, 0]]}}});
    v.as_object_mut()
        .unwrap()
        .extend(extra.as_object().cloned().unwrap_or_default());
    v
}

#[derive(Default)]
struct Rig {
    log: Vec<(f64, Action, RunInfo)>,
    events: Vec<(&'static str, RunInfo, Option<EndReason>)>,
    bpm: Rc<Cell<Option<f64>>>,
    speech: Rc<Cell<bool>>,
}

impl PlayerHost for Rig {
    fn dispatch(&mut self, a: &Action, ctx: DispatchCtx) {
        self.log.push((ctx.at, a.clone(), ctx.run.clone()));
    }
    fn speech_active(&self) -> bool {
        self.speech.get()
    }
    fn live_bpm(&self) -> Option<f64> {
        self.bpm.get()
    }
    fn started(&mut self, run: &RunInfo) {
        self.events.push(("started", run.clone(), None));
    }
    fn ended(&mut self, run: &RunInfo, reason: EndReason) {
        self.events.push(("ended", run.clone(), Some(reason)));
    }
}

fn rig(items: Vec<Value>) -> (ShowPlayer, Rig, Arc<Catalog>) {
    let cat = Arc::new(Catalog::new(items, None));
    assert_eq!(cat.errors, Vec::<String>::new());
    (
        ShowPlayer::new(cat.clone(), PlayerOptions::default()),
        Rig::default(),
        cat,
    )
}

fn run_for(p: &mut ShowPlayer, h: &mut Rig, from: f64, to: f64, dt: f64) {
    let mut t = from;
    while t <= to + 1e-9 {
        p.update(h, t);
        t += dt;
    }
}

fn perform(p: &mut ShowPlayer, h: &mut Rig, id: &str, source: Source, now: f64) -> Option<String> {
    p.perform(h, id, source, Params::default(), now, None)
}

#[test]
fn player_rejects_tier_violations_with_ended_rejected_and_nothing_else() {
    let (mut p, mut h, _) = rig(vec![
        json!({"id": "big", "kind": "sequence", "tier": "show", "description": "x", "track": [{"at": 0, "do": "unduck"}]}),
    ]);
    assert!(perform(&mut p, &mut h, "big", Source::Jev, 0.0).is_none());
    assert_eq!(
        h.events.iter().map(|e| (e.0, e.2)).collect::<Vec<_>>(),
        vec![("ended", Some(EndReason::Rejected))]
    );
    assert!(h.log.is_empty());
    assert!(perform(&mut p, &mut h, "big", Source::Claude, 0.0).is_some());
}

#[test]
fn player_a_wait_for_speech_end_slides_every_later_item_by_the_real_speech_duration() {
    let (mut p, mut h, _) = rig(vec![
        json!({"id": "talk", "kind": "sequence", "tier": "show", "description": "x", "track": [
        {"at": 0, "do": "speak", "text": "hi"}, {"at": 0, "do": "wait", "for": "speech_end"}, {"at": 1, "do": "unduck"}]}),
    ]);
    perform(&mut p, &mut h, "talk", Source::Ui, 0.0);
    h.speech.set(true);
    run_for(&mut p, &mut h, 0.0, 2.5, 1.0 / 60.0);
    h.speech.set(false); // 2.5 s of speech
    run_for(&mut p, &mut h, 2.5 + 1.0 / 60.0, 5.0, 1.0 / 60.0);
    let un = h.log.iter().find(|l| l.1 == Action::Unduck).unwrap();
    assert!(un.0 > 3.49 && un.0 < 3.55, "{}", un.0);
}

#[test]
fn player_a_beat_clock_chases_a_tempo_change_without_losing_its_place() {
    let (mut p, mut h, _) = rig(vec![
        json!({"id": "beats", "kind": "sequence", "tier": "free", "description": "x", "clock": "beat", "track": [
        {"at": 0, "do": "unduck"}, {"at": 4, "do": "unduck"}, {"at": 8, "do": "unduck"}]}),
    ]);
    h.bpm.set(Some(120.0));
    perform(&mut p, &mut h, "beats", Source::Ui, 0.0);
    run_for(&mut p, &mut h, 0.0, 1.0, 1.0 / 60.0); // 2 beats at 120
    h.bpm.set(Some(60.0));
    run_for(&mut p, &mut h, 1.0 + 1.0 / 60.0, 12.0, 1.0 / 60.0);
    let ts: Vec<f64> = h.log.iter().map(|l| l.0).collect();
    approx(ts[0], 0.0, 1e-6);
    approx(ts[1], 3.0, 0.05); // 2 beats left at 60 bpm
    approx(ts[2], 7.0, 0.05);
}

#[test]
fn player_loops_are_anchored_lap_n_starts_at_exactly_n_times_length() {
    let (mut p, mut h, _) = rig(vec![
        json!({"id": "lp", "kind": "sequence", "tier": "free", "description": "x", "loop": true, "length": 0.7, "track": [{"at": 0.1, "do": "unduck"}]}),
    ]);
    perform(&mut p, &mut h, "lp", Source::Ui, 0.0);
    run_for(&mut p, &mut h, 0.0, 10.0, 1.0 / 37.0);
    assert_eq!(h.log.len(), 15);
    for (i, l) in h.log.iter().enumerate() {
        approx(l.0, 0.1 + i as f64 * 0.7, 1e-9);
    }
    p.stop(&mut h, &StopSel::id("lp"), 10.0);
    assert!(p.running().is_empty());
}

#[test]
fn player_a_new_sequence_on_the_same_layer_interrupts_other_layers_keep_running() {
    let (mut p, mut h, _) = rig(vec![
        clip("nod", 1.0, json!({})),
        json!({"id": "a", "kind": "sequence", "tier": "show", "description": "x", "track": [{"at": 5, "do": "unduck"}]}),
        json!({"id": "b", "kind": "sequence", "tier": "show", "description": "x", "track": [{"at": 5, "do": "unduck"}]}),
    ]);
    perform(&mut p, &mut h, "a", Source::Ui, 0.0);
    perform(&mut p, &mut h, "nod", Source::Ui, 0.1);
    perform(&mut p, &mut h, "b", Source::Ui, 1.0);
    let ended: Vec<_> = h
        .events
        .iter()
        .filter(|e| e.0 == "ended")
        .map(|e| (e.1.id.clone(), e.2))
        .collect();
    assert_eq!(ended, vec![("a".to_string(), Some(EndReason::Interrupted))]);
    let running: Vec<_> = p
        .running()
        .iter()
        .map(|r| {
            format!(
                "{}:{}",
                serde_json::to_value(r.layer).unwrap().as_str().unwrap(),
                r.id
            )
        })
        .collect();
    assert_eq!(running, vec!["gesture:nod", "show:b"]);
}

#[test]
fn player_a_gesture_inside_interruptible_after_queues_the_next_request_latest_wins() {
    let (mut p, mut h, _) = rig(vec![
        clip("nod", 1.0, json!({"interruptible_after": 0.4})),
        clip("shake", 1.0, json!({})),
        clip("tilt", 1.0, json!({})),
    ]);
    perform(&mut p, &mut h, "nod", Source::Ui, 0.0);
    perform(&mut p, &mut h, "shake", Source::Ui, 0.1);
    perform(&mut p, &mut h, "tilt", Source::Ui, 0.2);
    assert_eq!(p.queued(RunLayer::Gesture), Some("tilt"));
    run_for(&mut p, &mut h, 0.2, 0.45, 1.0 / 60.0);
    assert_eq!(
        p.running()
            .iter()
            .map(|r| r.id.as_str())
            .collect::<Vec<_>>(),
        vec!["tilt"]
    );
    let started: Vec<_> = h
        .events
        .iter()
        .filter(|e| e.0 == "started")
        .map(|e| e.1.id.as_str())
        .collect();
    assert_eq!(started, vec!["nod", "tilt"]);
}

#[test]
fn player_freeze_stops_everything_and_rejects_until_released() {
    let (mut p, mut h, _) = rig(vec![clip("nod", 1.0, json!({}))]);
    perform(&mut p, &mut h, "nod", Source::Ui, 0.0);
    p.freeze(&mut h, true, 0.1);
    assert!(p.running().is_empty());
    assert!(perform(&mut p, &mut h, "nod", Source::Ui, 0.2).is_none());
    p.freeze(&mut h, false, 0.3);
    assert!(perform(&mut p, &mut h, "nod", Source::Ui, 0.4).is_some());
    let reasons: Vec<_> = h
        .events
        .iter()
        .filter(|e| e.0 == "ended")
        .map(|e| e.2.unwrap())
        .collect();
    assert_eq!(reasons, vec![EndReason::Interrupted, EndReason::Rejected]);
}

#[test]
fn player_a_lights_action_in_a_track_is_an_action_not_a_cue_reference() {
    let (mut p, mut h, cat) = rig(vec![
        json!({"id": "lt", "kind": "sequence", "tier": "free", "description": "x", "track": [{"at": 0, "do": "lights", "cue": "red_flash", "hold": 1}]}),
    ]);
    let want = json!([{"t": 0, "do": "lights", "cue": "red_flash", "fade": 0, "hold": 1}]);
    assert_json_eq(
        &serde_json::to_value(expand("lt", &cat, None).unwrap()).unwrap(),
        &want,
        "expand",
    );
    perform(&mut p, &mut h, "lt", Source::Ui, 0.0);
    assert_json_eq(
        &serde_json::to_value(&h.log[0].1).unwrap(),
        &json!({"do": "lights", "cue": "red_flash", "fade": 0, "hold": 1}),
        "dispatch",
    );
}

#[test]
fn player_params_scale_clip_intensity_and_speed_clamped_to_the_spec_ranges() {
    let (mut p, mut h, _) = rig(vec![
        clip("nod", 1.0, json!({})),
        json!({"id": "c", "kind": "cue", "tier": "free", "description": "x", "actions": [{"at": 0, "do": "clip", "id": "nod", "speed": 1.5, "intensity": 1.2}]}),
    ]);
    p.perform(
        &mut h,
        "c",
        Source::Ui,
        Params {
            intensity: Some(1.5),
            speed: Some(2.0),
        },
        0.0,
        None,
    );
    match &h.log[0].1 {
        Action::Clip {
            intensity, speed, ..
        } => assert_eq!((*intensity, *speed), (Some(1.5), Some(2.0))),
        a => panic!("{a:?}"),
    }
}

// ------------------------------------------------------------------ body compositor

fn over() -> Arc<Clip> {
    let tracks = [
        (
            "head_pan".to_string(),
            Track {
                mode: TrackMode::Override,
                keys: vec![[0.0, 30.0], [1.0, 30.0]],
                ease: None,
                blend: None,
            },
        ),
        (
            "visor".to_string(),
            Track {
                mode: TrackMode::Additive,
                keys: vec![[0.0, 0.0], [0.5, -6.0], [1.0, 0.0]],
                ease: None,
                blend: None,
            },
        ),
    ];
    Arc::new(Clip {
        id: "o".into(),
        duration: 1.0,
        interruptible_after: None,
        requires: None,
        tracks: tracks.into_iter().collect(),
    })
}

fn req(run: &str, clip: Arc<Clip>, layer: RunLayer, t0: f64) -> PlayRequest {
    PlayRequest {
        run_id: run.into(),
        clip,
        intensity: None,
        speed: None,
        layer,
        owns: None,
        t0,
    }
}

fn pose(kv: &[(&str, f64)]) -> Pose {
    kv.iter().map(|(k, v)| (k.to_string(), *v)).collect()
}

fn at(b: &mut BodyCompositor, kv: &[(&str, f64)], t: f64) -> Pose {
    let mut p = pose(kv);
    b.apply(&mut p, t, None);
    p
}

#[test]
fn body_override_blends_in_over_the_joint_class_blend_and_back_out_after_the_clip() {
    let mut b = BodyCompositor::new();
    b.play(req("r", over(), RunLayer::Gesture, 0.0));
    for (t, want) in [
        (0.0, 10.0),
        (0.1, 20.0),
        (0.5, 30.0),
        (1.1, 20.0),
        (1.25, 10.0),
    ] {
        approx(at(&mut b, &[("head_pan", 10.0)], t)["head_pan"], want, 5e-3); // head blend 0.2 s
    }
}

#[test]
fn body_intensity_scales_the_offset_from_the_pose_the_override_started_over() {
    let mut b = BodyCompositor::new();
    b.play(PlayRequest {
        intensity: Some(0.5),
        ..req("r", over(), RunLayer::Gesture, 0.0)
    });
    at(&mut b, &[("head_pan", 10.0)], 0.0);
    approx(
        at(&mut b, &[("head_pan", 10.0)], 0.5)["head_pan"],
        20.0,
        5e-3,
    );
}

#[test]
fn body_an_interrupt_freezes_the_clip_and_fades_its_weight_no_cut() {
    let mut b = BodyCompositor::new();
    b.play(req("r", over(), RunLayer::Gesture, 0.0));
    at(&mut b, &[("head_pan", 0.0), ("visor", 0.0)], 0.25);
    b.release("r", 0.25);
    let v1 = at(&mut b, &[("head_pan", 0.0), ("visor", 0.0)], 0.26);
    assert!(v1["head_pan"] > 25.0); // not cut
    assert!(v1["visor"] < -2.5);
    let v2 = at(&mut b, &[("head_pan", 0.0), ("visor", 0.0)], 0.6);
    approx(v2["head_pan"], 0.0, 5e-3);
    approx(v2["visor"], 0.0, 5e-3);
}

#[test]
fn body_an_interrupted_additive_body_clip_fades_over_the_body_blend_not_a_cut() {
    let mut b = BodyCompositor::new();
    let tr = Track {
        mode: TrackMode::Additive,
        keys: vec![[0.0, 0.0], [1.0, 10.0], [2.0, 0.0]],
        ease: None,
        blend: None,
    };
    let ring = Arc::new(Clip {
        id: "r".into(),
        duration: 2.0,
        interruptible_after: None,
        requires: None,
        tracks: [("torso_top".to_string(), tr)].into_iter().collect(),
    });
    b.play(req("r", ring, RunLayer::Gesture, 0.0));
    at(&mut b, &[("torso_top", 0.0)], 1.0);
    b.release("r", 1.0);
    approx(
        at(&mut b, &[("torso_top", 0.0)], 1.175)["torso_top"],
        5.0,
        0.5,
    ); // half of the 0.35 s body blend
    approx(
        at(&mut b, &[("torso_top", 0.0)], 1.4)["torso_top"],
        0.0,
        5e-3,
    );
}

#[test]
fn body_owned_joints_hold_unowned_joints_stay_live_overrides_outside_owns_are_masked() {
    let mut b = BodyCompositor::new();
    b.own("s", RunLayer::Show, &["visor".to_string()], 0.0);
    b.play(PlayRequest {
        owns: Some(vec!["visor".into()]),
        ..req("s", over(), RunLayer::Show, 0.0)
    });
    at(&mut b, &[("head_pan", 5.0), ("visor", 2.0)], 0.0);
    let p = at(&mut b, &[("head_pan", 12.0), ("visor", 9.0)], 0.5);
    approx(p["head_pan"], 12.0, 5e-3); // override masked: gaze stays live
    approx(p["visor"], 2.0 - 6.0, 5e-3); // held at 2, plus the additive
}

#[test]
fn body_the_show_layer_composes_above_the_gesture_layer() {
    let mut b = BodyCompositor::new();
    b.play(req("g", over(), RunLayer::Gesture, 0.0));
    let tr = Track {
        mode: TrackMode::Override,
        keys: vec![[0.0, -20.0], [1.0, -20.0]],
        ease: None,
        blend: None,
    };
    let mut c = (*over()).clone();
    c.tracks = [("head_pan".to_string(), tr)].into_iter().collect();
    b.play(req("s", Arc::new(c), RunLayer::Show, 0.0));
    at(&mut b, &[("head_pan", 0.0)], 0.0);
    approx(
        at(&mut b, &[("head_pan", 0.0)], 0.5)["head_pan"],
        -20.0,
        5e-3,
    );
}

#[test]
fn body_freeze_holds_the_output_and_releases_over_half_a_second() {
    let mut b = BodyCompositor::new();
    at(&mut b, &[("head_pan", 10.0)], 0.0);
    b.freeze(true, 0.0, None);
    approx(
        at(&mut b, &[("head_pan", 40.0)], 0.2)["head_pan"],
        10.0,
        5e-3,
    );
    b.freeze(false, 1.0, None);
    approx(
        at(&mut b, &[("head_pan", 40.0)], 1.25)["head_pan"],
        25.0,
        5e-3,
    );
    approx(
        at(&mut b, &[("head_pan", 40.0)], 1.6)["head_pan"],
        40.0,
        5e-3,
    );
}

#[test]
fn body_min_jerk_keys_by_default() {
    let tr = Track {
        mode: TrackMode::Override,
        keys: vec![[0.0, 0.0], [1.0, 10.0]],
        ease: None,
        blend: None,
    };
    approx(eval_track(&tr, 0.5), 5.0, 5e-3);
    let s: f64 = 0.25;
    approx(
        eval_track(&tr, 0.25),
        10.0 * (10.0 * s.powi(3) - 15.0 * s.powi(4) + 6.0 * s.powi(5)),
        5e-3,
    );
    assert_eq!(
        eval_track(
            &Track {
                ease: Some(Ease::Step),
                ..tr
            },
            0.9
        ),
        0.0
    );
}

// ------------------------------------------------------------------ idle policy

#[test]
fn idle_waits_after_s_of_quiet_then_performs_a_weighted_choice_music_switches_lists() {
    let policy = serde_json::from_value(json!({"after_s": 10, "choices": [{"id": "a", "weight": 1}], "while_music": [{"id": "m", "weight": 1}]})).unwrap();
    let mut idle = IdleRunner::new(Some(policy), 0.0);
    let mut rng = Rng::Const(0.5);
    let quiet = IdleContext {
        eligible: true,
        music: false,
    };
    let music = IdleContext {
        eligible: true,
        music: true,
    };
    assert_eq!(idle.update(5.0, quiet, &mut rng), None);
    assert_eq!(
        idle.update(10.0, quiet, &mut rng),
        Some(IdleAction::Perform("a".into()))
    );
    idle.started(Some("a#1".into()));
    assert_eq!(
        idle.update(11.0, music, &mut rng),
        Some(IdleAction::Stop("a#1".into()))
    );
    assert_eq!(
        idle.update(21.0, music, &mut rng),
        Some(IdleAction::Perform("m".into()))
    );
}

#[test]
fn idle_pick_respects_weights() {
    let list = vec![
        WeightedId {
            id: "x".into(),
            weight: 3.0,
        },
        WeightedId {
            id: "y".into(),
            weight: 1.0,
        },
    ];
    assert_eq!(pick(&list, &mut Rng::Const(0.7)).as_deref(), Some("x"));
    assert_eq!(pick(&list, &mut Rng::Const(0.8)).as_deref(), Some("y"));
}
