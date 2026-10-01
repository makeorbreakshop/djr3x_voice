//! The top-level performer, driven from the committed Robot Profile (`profiles/r3x`).

mod common;

use common::{repo, show_catalog};
use r3x_contracts::messages::{PerfCommand, StopTarget};
use r3x_contracts::RobotProfile;
use r3x_performer_core::actuation::pipeline::{rig_joints, servo_map, Actuation};
use r3x_performer_core::behavior::AliveLayers;
use r3x_performer_core::performer::{Command, Enables, Out, PerformerConfig};
use r3x_performer_core::rng::Rng;
use r3x_performer_core::show::lint::{joint_limits, joint_limits_from_profile, lint_catalog};
use r3x_performer_core::show::player::EndReason;
use r3x_performer_core::show::types::{Params, Source};
use r3x_performer_core::Performer;
use std::collections::BTreeMap;
use std::sync::Arc;

fn profile() -> RobotProfile {
    RobotProfile::load(repo("profiles/r3x/robot.json")).unwrap()
}

fn performer() -> Performer {
    Performer::new(
        Arc::new(show_catalog()),
        &profile(),
        PerformerConfig::default(),
    )
    .unwrap()
}

/// Tick at 60 Hz from `from` to `to` (inclusive of `from`).
fn run(p: &mut Performer, from: f64, to: f64) -> Vec<Out> {
    let mut t = from;
    while t <= to + 1e-9 {
        p.tick(t);
        t += 1.0 / 60.0;
    }
    p.take_events()
}

#[test]
fn the_profile_describes_the_same_body_as_the_servo_map() {
    let p = profile();
    let (a, b) = (joint_limits(), joint_limits_from_profile(&p).unwrap());
    assert_eq!(a.keys().collect::<Vec<_>>(), b.keys().collect::<Vec<_>>());
    for (j, x) in &a {
        let y = &b[j];
        // The profile stores soft ranges to 2 decimals.
        assert!(
            (x.lo - y.lo).abs() < 0.01 && (x.hi - y.hi).abs() < 0.01,
            "{j}: {x:?} vs {y:?}"
        );
        assert_eq!(
            (x.v_max, x.a_max, &x.channel, x.primary, x.base),
            (y.v_max, y.a_max, &y.channel, y.primary, y.base),
            "{j}"
        );
    }
    let lint = lint_catalog(&show_catalog(), &b);
    assert_eq!(lint.errors, Vec::<String>::new());

    let ext = Actuation::new(
        &rig_joints(),
        &BTreeMap::new(),
        &servo_map(),
        "extended",
        Rng::new(1),
    )
    .unwrap();
    let prof = Actuation::from_profile(&p, &BTreeMap::new(), Rng::new(1)).unwrap();
    for (c, d) in ext.channels.iter().zip(&prof.channels) {
        assert_eq!(
            (c.cfg.ch, &c.primary_joint, c.us),
            (d.cfg.ch, &d.primary_joint, d.us)
        );
    }
}

#[test]
fn an_emote_performs_its_slot_and_the_body_moves() {
    let mut p = performer();
    run(&mut p, 0.0, 0.5);
    p.command(Command::Emote { slot: 0 }); // "yes"
    let ev = run(&mut p, 0.5 + 1.0 / 60.0, 3.0);
    let started: Vec<_> = ev
        .iter()
        .filter_map(|e| {
            if let Out::Started { run } = e {
                Some(run.id.as_str())
            } else {
                None
            }
        })
        .collect();
    assert_eq!(started, vec!["yes"]);
    assert!(ev.iter().any(|e| matches!(
        e,
        Out::Ended {
            reason: EndReason::Done,
            ..
        }
    )));
    assert!(ev.iter().any(|e| matches!(e, Out::Action { .. })));
    assert!(ev
        .iter()
        .any(|e| matches!(e, Out::ServoGoal { joint, .. } if joint == "head_tilt")));
}

#[test]
fn a_bus_perf_command_plays_stops_and_freezes() {
    use r3x_contracts::Source as S;
    let mut p = performer();
    p.tick(0.0);
    let play = |id: &str| PerfCommand::Play {
        id: id.into(),
        intensity: 1.0,
        speed: 1.0,
        layer: None,
    };
    let err = p.perf_command(&play("dj_intro"), S::Jev).unwrap_err();
    assert!(err.contains("show-tier"), "{err}");
    let ev = p.take_events();
    assert!(
        ev.iter().any(|e| matches!(
            e,
            Out::Ended {
                reason: EndReason::Rejected,
                ..
            }
        )),
        "jev may not play a show item"
    );
    assert!(!ev.iter().any(|e| matches!(e, Out::Started { .. })));
    assert!(p.perf_command(&play("no_such_item"), S::Ui).is_err());
    p.perf_command(&play("dj_intro"), S::Claude).unwrap();
    assert_eq!(p.player.running().len(), 1);
    p.perf_command(&PerfCommand::Stop(StopTarget::All), S::Ui).unwrap();
    assert!(p.player.running().is_empty());
    p.perf_command(&PerfCommand::Freeze { on: true }, S::Ui).unwrap();
    assert_eq!(p.perf_command(&play("nod"), S::Ui).unwrap_err(), "motion is frozen");
    p.perf_command(&PerfCommand::Freeze { on: false }, S::Ui).unwrap();
    assert!(p
        .perform("nod", Source::Ui, Params::default(), None)
        .is_some());
    assert!(p.perf_command(&PerfCommand::Emote { slot: 99 }, S::Ui).is_err());
}

/// Plan §7b (intended difference from host.ts): one mouth rate from the profile, and the
/// end-of-speech M000 is never dropped. The legacy 10 Hz adapter is kept for parity only.
#[test]
fn live_mouth_runs_at_the_profile_rate_and_always_closes() {
    let mouth_lines = |hz: Option<f64>| {
        let mut p = Performer::new(
            Arc::new(show_catalog()),
            &profile(),
            PerformerConfig {
                mouth_hz: hz,
                ..Default::default()
            },
        )
        .unwrap();
        p.command(Command::Mode {
            mode: r3x_performer_core::leds::host::SystemMode::Interactive,
        });
        p.tick(0.0);
        p.command(Command::SpeechStarted {
            timings: None,
            tags: vec![],
        });
        let mut t = 0.0;
        let mut lines = vec![];
        for i in 0..60 {
            t = i as f64 / 60.0;
            p.command(Command::Amplitude {
                value: 0.5 + 0.4 * (i as f64 * 0.7).sin(),
            });
            p.tick(t);
        }
        p.command(Command::SpeechEnded); // right after an amplitude update
        p.tick(t + 1.0 / 60.0);
        for e in p.take_events() {
            if let Out::FaceLine { line } = e {
                if line.starts_with('M') {
                    lines.push(line);
                }
            }
        }
        lines
    };
    let live = mouth_lines(Some(30.0));
    assert_eq!(live.last().map(String::as_str), Some("M000"), "{live:?}");
    assert!((25..=31).contains(&live.len()), "~30 Hz over 1 s: {}", live.len());
    let legacy = mouth_lines(None);
    assert!(legacy.len() <= 11, "legacy 10 Hz adapter: {}", legacy.len());
}

#[test]
fn only_start_failures_latch_on_the_chest() {
    let mut p = performer();
    p.tick(0.0);
    let status = |svc: &str, detail: Option<&str>| Command::ServiceStatus {
        service: svc.into(),
        status: "error".into(),
        detail: detail.map(Into::into),
        latched: None,
    };
    p.command(status("ClaudeService", Some("request failed: 529")));
    p.command(status("MemoryService", Some("Failed to start: no db")));
    let st = p.host.chest.statuses();
    assert_eq!(st["ClaudeService"], "error");
    // Past the fault hold a runtime error clears; a start failure holds.
    p.host.chest.tick(p.host.chest.fault_hold_ms + 1000.0);
    let st = p.host.chest.statuses();
    assert_eq!(st["ClaudeService"], "running");
    assert_eq!(st["MemoryService"], "error");
}

#[test]
fn jog_holds_a_joint_and_claude_shows_never_speak() {
    let mut p = performer();
    let j = "head_tilt";
    p.command(Command::Jog {
        joint: j.into(),
        value: Some(4.0),
    });
    let mut f = p.tick(0.0);
    for i in 1..180 {
        f = p.tick(i as f64 / 60.0);
    }
    assert!((f.targets[j] - 4.0).abs() < 1e-9);
    assert!(f.joints[j] > 3.0, "followed: {}", f.joints[j]);
    p.command(Command::JogRelease);
    assert!((p.tick(3.0).targets[j] - 4.0).abs() > 1e-6);

    // A show's `speak` line goes out, except from a Claude-triggered run.
    let speaks = |p: &mut Performer, src: Source, t0: f64| {
        p.perform("dj_intro", src, Params::default(), None).unwrap();
        let ev = run(p, t0, t0 + 8.0);
        p.stop(&r3x_performer_core::show::player::StopSel::all());
        ev.iter().filter(|e| matches!(e, Out::Speak { .. })).count()
    };
    let has_speak = show_catalog().get("dj_intro").is_some_and(|it| format!("{it:?}").contains("Speak"));
    if has_speak {
        assert!(speaks(&mut p, Source::Timeline, 3.1) > 0);
        assert_eq!(speaks(&mut p, Source::Claude, 12.0), 0);
    }
}

#[test]
fn a_reloaded_catalogue_is_swapped_in() {
    let mut p = performer();
    p.tick(0.0);
    let mut small = r3x_performer_core::show::catalog::Catalog::default();
    small.add(
        &serde_json::json!({"id": "wiggle", "kind": "clip", "tier": "free", "description": "d",
            "duration": 0.5, "tracks": {"head_tilt": {"mode": "override", "keys": [[0, 0], [0.5, 5]]}}}),
        None,
    );
    assert!(small.errors.is_empty(), "{:?}", small.errors);
    p.set_catalog(Arc::new(small));
    assert!(p.perform("nod", Source::Ui, Params::default(), None).is_none());
    assert!(p.perform("wiggle", Source::Ui, Params::default(), None).is_some());
}

#[test]
fn a_conversation_drives_the_face_and_chest_boards_with_named_commands() {
    let mut p = performer();
    let mut ev = run(&mut p, 0.0, 0.2);
    p.command(Command::ListeningStarted);
    ev.extend(run(&mut p, 0.2, 1.0));
    p.command(Command::ListeningStopped { heard: true });
    ev.extend(run(&mut p, 1.0, 1.5));
    p.command(Command::SpeechStarted {
        timings: None,
        tags: vec![],
    });
    for i in 0..30 {
        p.command(Command::Amplitude { value: 0.8 });
        p.tick(1.5 + i as f64 / 60.0);
    }
    p.command(Command::SpeechEnded);
    ev.extend(p.take_events());
    ev.extend(run(&mut p, 2.0, 3.0));
    let face: Vec<_> = ev
        .iter()
        .filter_map(|e| {
            if let Out::FaceLine { line } = e {
                Some(line.as_str())
            } else {
                None
            }
        })
        .collect();
    let states: Vec<_> = face
        .iter()
        .copied()
        .filter(|l| l.starts_with('S'))
        .collect();
    assert_eq!(states, vec!["SI", "SL", "ST", "SS", "SF"]);
    assert!(face.iter().any(|l| l.starts_with('M') && *l != "M000"));
    let chest: Vec<_> = ev
        .iter()
        .filter_map(|e| {
            if let Out::ChestLine { line } = e {
                Some(line.as_str())
            } else {
                None
            }
        })
        .collect();
    assert!(chest.contains(&"SS") && chest.contains(&"SF"), "{chest:?}");
    let f = p.tick(3.1);
    assert_eq!(f.chest.len(), 33);
    assert!(f.eyes.iter().any(|c| c.iter().any(|v| *v > 0)));
    let c = f.to_contract();
    assert_eq!(c.lights["stage"].len(), 11);
    assert!(c.joints.contains_key("head_pan"));
}

#[test]
fn show_tags_fire_on_the_word() {
    let mut p = performer();
    p.tick(0.0);
    // Character 26 is spoken 1.3 s after speech starts.
    let timings: Vec<f64> = (0..40).map(|i| i as f64 * 0.05).collect();
    p.command(Command::SpeechStarted {
        timings: Some(timings),
        tags: vec![(26, "nod".into())],
    });
    run(&mut p, 1.0 / 60.0, 1.25);
    assert!(p.player.running().is_empty());
    run(&mut p, 1.25 + 1.0 / 60.0, 1.4);
    assert_eq!(
        p.player
            .running()
            .iter()
            .map(|r| r.id.as_str())
            .collect::<Vec<_>>(),
        vec!["nod"]
    );
}

#[test]
fn output_enables_gate_drivers_not_frames() {
    let mut p = performer();
    p.command(Command::Enables(Enables {
        motion: false,
        face: false,
        chest: false,
        stage_lights: false,
        sfx: false,
    }));
    p.command(Command::ListeningStarted);
    let ev = run(&mut p, 0.0, 1.0);
    assert!(!ev.iter().any(|e| matches!(
        e,
        Out::FaceLine { .. } | Out::ChestLine { .. } | Out::ServoGoal { .. }
    )));
    let f = p.tick(1.1);
    assert!(
        f.eyes.iter().any(|c| c.iter().any(|v| *v > 0)),
        "the emulated face still renders"
    );
}

#[test]
fn alive_layers_toggle_off() {
    let mut p = performer();
    p.command(Command::Alive(AliveLayers {
        breathing: false,
        saccades: false,
        gaze_wander: false,
        speech_bob: false,
        expressive_roll: false,
    }));
    let a = p.tick(0.0).targets;
    let b = p.tick(1.0).targets;
    assert_eq!(a, b, "with every alive layer off an idle robot holds still");
    p.command(Command::Alive(AliveLayers::default()));
    assert_ne!(p.tick(2.0).targets, b);
}

#[test]
fn puppet_release_fades_out_over_400_ms() {
    let mut p = performer();
    p.command(Command::Alive(AliveLayers {
        breathing: false,
        saccades: false,
        gaze_wander: false,
        speech_bob: false,
        expressive_roll: false,
    }));
    let rest = p.tick(0.0).targets["hero_shoulder"];
    p.command(Command::Puppet {
        intent: "arm_raise".into(),
        value: 1.0,
    });
    run(&mut p, 1.0 / 60.0, 1.0);
    let up = p.tick(1.0).targets["hero_shoulder"];
    assert!(up > rest + 30.0, "{up} vs {rest}");
    p.command(Command::PuppetRelease);
    run(&mut p, 1.0 + 1.0 / 60.0, 1.2);
    let half = p.tick(1.2).targets["hero_shoulder"];
    assert!(half > rest + 1.0 && half < up - 1.0, "{half}");
    run(&mut p, 1.2 + 1.0 / 60.0, 1.5);
    assert!((p.tick(1.5).targets["hero_shoulder"] - rest).abs() < 1e-6);
}

#[test]
fn freeze_holds_the_body_and_idle_stays_away() {
    let mut p = performer();
    p.command(Command::Freeze { on: true });
    let ev = run(&mut p, 0.0, 60.0);
    assert!(
        !ev.iter().any(|e| matches!(e, Out::Started { .. })),
        "idle must not start while frozen"
    );
    let a = p.tick(60.1).targets;
    let b = p.tick(61.0).targets;
    for (j, v) in &a {
        assert!((v - b[j]).abs() < 1e-9, "{j} moved while frozen");
    }
}

#[test]
fn idle_performs_after_quiet() {
    let mut p = performer();
    let ev = run(&mut p, 0.0, 40.0);
    let idle: Vec<_> = ev
        .iter()
        .filter_map(|e| {
            if let Out::Started { run } = e {
                (run.source == Source::Idle).then_some(run.id.clone())
            } else {
                None
            }
        })
        .collect();
    assert!(!idle.is_empty());
}

#[test]
fn studio_preview_scrubs_on_the_real_body_path() {
    let mut p = performer();
    p.command(Command::Alive(AliveLayers { breathing: false, saccades: false, gaze_wander: false, speech_bob: false, expressive_roll: false }));
    p.command(Command::Autonomy { on: false });
    run(&mut p, 0.0, 0.5);
    let base = p.tick(0.5).targets["head_pan"];
    let clip = serde_json::json!({"id": "s", "kind": "clip", "description": "d", "tags": [], "tier": "free", "duration": 2.0,
        "tracks": {"head_pan": {"mode": "override", "keys": [[0, 0], [1.0, 20], [2.0, 20]], "ease": "linear"}}});
    // Held at 0.5 s: the target is the curve there, and stays until replaced.
    p.perf_command(&PerfCommand::Preview { clip: clip.clone(), at: 0.5, hold: true }, r3x_contracts::Source::Ui).unwrap();
    run(&mut p, 0.52, 3.0);
    let f = p.tick(3.0);
    assert!((f.targets["head_pan"] - 10.0).abs() < 1e-6, "{}", f.targets["head_pan"]);
    assert!((f.joints["head_pan"] - 10.0).abs() < 0.5, "actuation follows the scrub");
    // Playing from 1.5 s: at +0.25 s the curve is at 1.75 s (20); the stop blends out to the base.
    p.perf_command(&PerfCommand::Preview { clip, at: 1.5, hold: false }, r3x_contracts::Source::Ui).unwrap();
    assert!((p.tick(3.25).targets["head_pan"] - 20.0).abs() < 1e-6);
    p.perf_command(&PerfCommand::PreviewStop, r3x_contracts::Source::Ui).unwrap();
    run(&mut p, 3.27, 4.5);
    assert!((p.tick(4.5).targets["head_pan"] - base).abs() < 1e-6);
    let bad = serde_json::json!({"id": "s", "kind": "cue"});
    assert!(p.perf_command(&PerfCommand::Preview { clip: bad, at: 0.0, hold: true }, r3x_contracts::Source::Ui).is_err());
}

#[test]
fn home_eases_every_joint_home_inside_its_limits() {
    let mut prof = profile();
    prof.home.insert("head_pan".into(), 10.0); // a non-centre home is honoured
    let mut p = Performer::new(Arc::new(show_catalog()), &prof, PerformerConfig::default()).unwrap();
    // Busy: alive layers on, a show running, a joint jogged, a puppet intent held.
    assert!(p.perform("nod", Source::Ui, Params::default(), None).is_some());
    p.perf_command(&PerfCommand::Puppet { channels: [("visor".to_string(), 20.0), ("energy".to_string(), 1.0)].into() }, r3x_contracts::Source::Ui).unwrap();
    run(&mut p, 0.0, 1.0);
    assert!(!p.at_home(0.5));

    p.command(Command::Freeze { on: true });
    assert!(p.home(&[]).is_err(), "frozen motion is not homed");
    p.command(Command::Freeze { on: false });
    p.home(&[]).unwrap();
    assert!(p.player.running().is_empty() && p.homing());
    assert_eq!(p.procedural.layers, AliveLayers { breathing: false, saccades: false, gaze_wander: false, speech_bob: false, expressive_roll: false });
    assert!(!p.idle.enabled);

    let dt = 1.0 / 50.0;
    let mut t = 1.0 + dt;
    while t < 8.0 {
        p.tick(t);
        for ch in &p.actuation.channels {
            let l = ch.follower.limits;
            assert!(ch.follower.v.abs() <= l.v_max * 1.001, "{} v {} > {}", ch.primary_joint, ch.follower.v, l.v_max);
            assert!(ch.follower.a.abs() <= l.a_max * 1.001, "{} a {} > {}", ch.primary_joint, ch.follower.a, l.a_max);
        }
        t += dt;
    }
    let joints = p.tick(t).joints;
    assert!(p.at_home(0.5), "{joints:?}");
    assert!((joints["head_pan"] - 10.0).abs() < 0.5);

    // One joint only: a jog to its home value, without ending anything else.
    p.perf_command(&PerfCommand::Puppet { channels: [("head_tilt".to_string(), 12.0)].into() }, r3x_contracts::Source::Ui).unwrap();
    run(&mut p, t, t + 2.0);
    assert!(!p.at_home(0.5));
    p.perf_command(&PerfCommand::Home { joints: vec!["head_tilt".into()] }, r3x_contracts::Source::Ui).unwrap();
    run(&mut p, t + 2.0, t + 4.0);
    assert!(p.at_home(0.5) && p.homing());
    assert!(p.home(&["tail".into()]).is_err());

    // Anything that moves the body again ends the hold.
    p.perform("nod", Source::Ui, Params::default(), None).unwrap();
    assert!(!p.homing());
}

/// The rig layer: a puppeted gaze to the side cants the head toward the look (the automatic
/// expressive roll), and on the Physical rig (head on the base) the gaze never spills into
/// the rings and stops at the neck gear's range.
#[test]
fn gaze_rolls_toward_the_look_within_the_selected_rig() {
    let quiet = |p: &mut Performer| {
        p.command(Command::Alive(AliveLayers {
            breathing: false,
            saccades: false,
            gaze_wander: false,
            speech_bob: false,
            expressive_roll: true,
        }))
    };
    let mut p = performer();
    quiet(&mut p);
    p.command(Command::Puppet { intent: "gaze_yaw".into(), value: 0.6 });
    let mut f = p.tick(0.0);
    for i in 1..=120 {
        f = p.tick(i as f64 / 60.0);
    }
    let (pan, roll) = (f.targets["head_pan"], f.targets["head_roll"]);
    assert!(pan > 20.0, "looks to the left ({pan})");
    assert!(roll < -1.5, "cants toward the look ({roll})");
    p.command(Command::Alive(AliveLayers { expressive_roll: false, ..AliveLayers::default() }));
    quiet(&mut p);
    p.command(Command::Alive(AliveLayers { breathing: false, saccades: false, gaze_wander: false, speech_bob: false, expressive_roll: false }));
    let f = p.tick(2.1);
    assert!(f.targets["head_roll"].abs() < 1e-9, "the layer switches off");

    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/../../../profiles/r3x/robot.generated.json");
    let physical = RobotProfile::from_json(&std::fs::read_to_string(path).unwrap()).unwrap();
    let mut q = Performer::new(Arc::new(show_catalog()), &physical, PerformerConfig::default()).unwrap();
    quiet(&mut q);
    assert!(!q.rig.head_on_rings);
    q.command(Command::Puppet { intent: "gaze_yaw".into(), value: 1.0 });
    let mut f = q.tick(0.0);
    for i in 1..=180 {
        f = q.tick(i as f64 / 60.0);
    }
    // the head leads up to the puppeteer's 60 deg neck lead, or the rig's pan range if narrower
    let lim = physical.joint("head_pan").unwrap().animation.max.min(60.0);
    assert!((f.targets["head_pan"] - lim).abs() < 1e-6, "stops at the neck's range ({})", f.targets["head_pan"]);
    assert!(f.targets.get("torso_top").copied().unwrap_or(0.0).abs() < 1e-6, "no spill into the rings");
    assert!(f.targets["head_roll"] < -3.0, "cant toward the look instead ({})", f.targets["head_roll"]);

    // a rig whose pan range is narrower than the look clamps the head at it (the old 4:1 neck gear's 31)
    let mut narrow = physical.clone();
    for j in narrow.joints.iter_mut().filter(|j| j.name == "head_pan") {
        j.animation.min = -31.0;
        j.animation.max = 31.0;
    }
    let mut r = Performer::new(Arc::new(show_catalog()), &narrow, PerformerConfig::default()).unwrap();
    quiet(&mut r);
    r.command(Command::Puppet { intent: "gaze_yaw".into(), value: 1.0 });
    let mut f = r.tick(0.0);
    for i in 1..=180 {
        f = r.tick(i as f64 / 60.0);
    }
    assert!((f.targets["head_pan"] - 31.0).abs() < 1e-6, "stops at a narrow rig's pan range ({})", f.targets["head_pan"]);
    assert!(f.targets.get("torso_top").copied().unwrap_or(0.0).abs() < 1e-6, "no spill into the rings");
}

#[test]
fn a_heard_phrase_nods_only_while_listening() {
    let mut p = performer();
    run(&mut p, 0.0, 0.5);
    p.command(Command::Heard);
    assert_eq!(p.procedural.nod_started(), None, "not listening: no nod");
    p.command(Command::ListeningStarted);
    run(&mut p, 0.5, 1.0);
    p.command(Command::Heard);
    let at = p.procedural.nod_started().expect("listening: nods at the phrase");
    assert!((at - 1.0).abs() < 1e-9, "{at}");
}

/// A stray tap on talk (a kid on the pad, 2026-10-01) caught no words: R3X goes back to rest,
/// eyes and body, instead of sitting in "thinking" waiting for a reply that never comes.
#[test]
fn a_turn_with_no_words_returns_to_rest_not_thinking() {
    let mut p = performer();
    let mut ev = run(&mut p, 0.0, 0.2);
    p.command(Command::ListeningStarted);
    ev.extend(run(&mut p, 0.2, 0.5));
    p.command(Command::ListeningStopped { heard: false });
    ev.extend(run(&mut p, 0.5, 1.5));
    let states: Vec<String> = ev
        .iter()
        .filter_map(|e| match e {
            Out::FaceLine { line } if line.starts_with('S') => Some(line.clone()),
            _ => None,
        })
        .collect();
    assert_eq!(states, vec!["SI", "SL", "SE"], "listening, then straight back to engaged");
    assert_eq!(p.procedural.activity, r3x_performer_core::behavior::Activity::Engaged);
}

#[test]
fn listening_stopped_without_heard_still_parses_as_heard() {
    let c: Command = serde_json::from_str(r#"{"cmd":"listening_stopped"}"#).unwrap();
    assert!(matches!(c, Command::ListeningStopped { heard: true }));
}

/// Intentions (show/intentions.json): every pool entry is a real, low-tier item, and an
/// intention plays a varied pick, never the same one twice running, and waits out its cooldown.
#[test]
fn intentions_play_a_varied_pick_and_respect_their_cooldown() {
    let cat = show_catalog();
    assert!(cat.errors.is_empty(), "{:?}", cat.errors);
    assert!(cat.intentions.len() >= 30, "{}", cat.intentions.len());
    let mut p = performer();
    let mut picks = vec![];
    let mut t = 0.0;
    for _ in 0..8 {
        run(&mut p, t, t + 5.0);
        t += 5.0;
        p.command(Command::Intend { id: "nod".into(), source: Source::Claude, intensity: 1.0 });
        let started = p.take_events().into_iter().find_map(|o| match o {
            Out::Started { run } => Some(run.id.clone()),
            _ => None,
        });
        picks.push(started.expect("a nod plays"));
    }
    assert!(picks.windows(2).all(|w| w[0] != w[1]), "never the same twice running: {picks:?}");
    p.command(Command::Intend { id: "nod".into(), source: Source::Claude, intensity: 1.0 });
    assert!(p.take_events().iter().all(|o| !matches!(o, Out::Started { .. })), "cooling down");
}

/// While listening, the guest's voice level bobs the head a little (the ears, 2026-10-01);
/// silence leaves it still, and it stops when listening does.
#[test]
fn the_guests_voice_bobs_the_head_while_listening() {
    let range = |p: &mut Performer, from: f64, level: f64| {
        let (mut lo, mut hi) = (f64::MAX, f64::MIN);
        for i in 0..120 {
            p.command(Command::ListenLevel { value: if i % 6 < 3 { level } else { 0.0 } });
            let f = p.tick(from + i as f64 / 60.0);
            let v = f.targets.get("head_tilt").copied().unwrap_or(0.0);
            lo = lo.min(v);
            hi = hi.max(v);
        }
        hi - lo
    };
    let mut quiet = performer();
    quiet.command(Command::ListeningStarted);
    let mut talking = performer();
    talking.command(Command::ListeningStarted);
    let (q, t) = (range(&mut quiet, 0.0, 0.0), range(&mut talking, 0.0, 1.0));
    assert!(t > q + 0.3, "the guest's voice moves the head: {t:.2} vs {q:.2}");
}
