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
    let mut p = performer();
    p.tick(0.0);
    p.perf_command(
        &PerfCommand::Play {
            id: "dj_intro".into(),
            intensity: 1.0,
            speed: 1.0,
            layer: None,
        },
        r3x_contracts::Source::Jev,
    );
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
    p.perf_command(
        &PerfCommand::Play {
            id: "dj_intro".into(),
            intensity: 1.0,
            speed: 1.0,
            layer: None,
        },
        r3x_contracts::Source::Claude,
    );
    assert_eq!(p.player.running().len(), 1);
    p.perf_command(
        &PerfCommand::Stop(StopTarget::All),
        r3x_contracts::Source::Ui,
    );
    assert!(p.player.running().is_empty());
    p.perf_command(&PerfCommand::Freeze { on: true }, r3x_contracts::Source::Ui);
    assert!(p
        .perform("nod", Source::Ui, Params::default(), None)
        .is_none());
    p.perf_command(
        &PerfCommand::Freeze { on: false },
        r3x_contracts::Source::Ui,
    );
    assert!(p
        .perform("nod", Source::Ui, Params::default(), None)
        .is_some());
}

#[test]
fn a_conversation_drives_the_face_and_chest_boards_with_named_commands() {
    let mut p = performer();
    let mut ev = run(&mut p, 0.0, 0.2);
    p.command(Command::ListeningStarted);
    ev.extend(run(&mut p, 0.2, 1.0));
    p.command(Command::ListeningStopped);
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
