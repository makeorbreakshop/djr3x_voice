//! The grnwave LED set emulator (`leds/grnwave.rs`, twin of `firmware/grnwave_nano`), in the
//! style of `leds.rs`: the serial words, then each state's look on body, eyes and mouth, then
//! the performer driving it from the `grnwave_full_led` package.

mod common;

use common::{repo, show_catalog};
use r3x_contracts::RobotProfile;
use r3x_performer_core::leds::firmware::Rgb;
use r3x_performer_core::leds::grnwave::{
    from_chest_stream, mouth_rank, GrnwaveFirmware as Fw, BODY_BOARDS, BODY_LEDS, EXPOSED, GROUPS_PER_BOARD,
    IDENTITY, LEDS_PER_GROUP, MAIN_LEDS, MOUTH_LEDS, READY, SMALL_PER_BOARD,
};
use r3x_performer_core::performer::{Command, PerformerConfig};
use r3x_performer_core::rng::Rng;
use r3x_performer_core::Performer;
use std::sync::Arc;

fn on(c: &Rgb) -> bool {
    c.iter().any(|&v| v > 0)
}

/// Booted and told the system is up (X0), in `mode`, at t = 1 s.
fn board(words: &str) -> Fw {
    let mut fw = Fw::new(Rng::Const(0.5));
    assert_eq!(fw.read_lines(), vec![READY]);
    fw.write("X0\n");
    fw.write(words);
    fw.update(1000.0);
    fw
}

fn smalls(fw: &Fw, b: usize) -> Vec<Rgb> {
    (0..SMALL_PER_BOARD).map(|k| fw.main[Fw::small(b, k)]).collect()
}

fn window(fw: &Fw, b: usize, k: usize) -> Rgb {
    let i = Fw::window(b, k);
    let px = &fw.main[i..i + LEDS_PER_GROUP];
    assert!(px.iter().all(|p| p == &px[0]), "a window's 4 LEDs move together");
    px[0]
}

#[test]
fn chain_matches_the_grnwave_sample_sketch() {
    assert_eq!(MAIN_LEDS, 98, "NUM_LEDS 98");
    assert_eq!(Fw::small(1, 0), 32, "PanelBStart");
    assert_eq!(Fw::small(2, 0), 64, "PanelCStart");
    // PanelA1..A3, B1..B3, C1..C3 are the groups that face the windows.
    let starts: Vec<usize> = (0..BODY_BOARDS).flat_map(|b| (0..3).map(move |k| Fw::window(b, k))).collect();
    assert_eq!(starts, [12, 24, 28, 40, 44, 48, 84, 88, 92]);
    assert_eq!(BODY_LEDS, 96, "eyes daisy-chained at 96-97");
}

#[test]
fn acks_state_words_answers_identify_and_resets_silently() {
    let mut fw = Fw::new(Rng::Const(0.5));
    fw.read_lines();
    fw.write("SL\nSQ\nX2\nM100\nB120\nH1fe\n?\nR\n");
    assert_eq!(fw.read_lines(), vec!["+", "-", "+", IDENTITY]);
    assert_eq!((fw.mode, fw.amplitude, fw.bpm, fw.health, fw.sys_state), (b'I', 0, 0, 0x1ff, 0), "R resets");
}

#[test]
fn only_b_x_h_come_from_the_chest_stream() {
    for w in ["B120", "X0", "H1ff"] {
        assert!(from_chest_stream(w));
    }
    for w in ["SS", "SF", "M100", "R"] {
        assert!(!from_chest_stream(w), "{w} comes from the face stream");
    }
}

#[test]
fn boots_into_the_boot_sweep_until_x0() {
    let mut fw = Fw::new(Rng::Const(0.5));
    fw.update(0.0);
    assert_eq!(fw.sys_state, 1, "boots into the sweep until told X0");
    fw.update(700.0); // board A full, B starting, C dark
    assert!(smalls(&fw, 0).iter().all(on));
    assert!(!smalls(&fw, 2).iter().any(on));
    fw.write("X0\n");
    fw.update(1520.0);
    assert_eq!(fw.sys_state, 0);
}

#[test]
fn idle_eyes_are_gold_and_the_hidden_groups_stay_dark() {
    let fw = board("SI\n");
    for e in fw.eyes() {
        assert!(e[0] > 100 && e[1] > 80 && e[2] < e[1], "gold, not {e:?}");
    }
    for (b, exposed) in EXPOSED.iter().enumerate() {
        for g in (0..GROUPS_PER_BOARD).filter(|g| !exposed.contains(g)) {
            let i = Fw::group(b, g);
            assert!(fw.main[i..i + LEDS_PER_GROUP].iter().all(|c| !on(c)), "board {b} group {g}");
        }
        for k in 0..3 {
            assert!(on(&window(&fw, b, k)), "window {b}.{k} breathes");
        }
    }
    let lit: Vec<usize> = (0..MOUTH_LEDS).filter(|&i| on(&fw.mouth[i])).collect();
    assert_eq!(lit, [3, 4], "idle mouth: the V's tip");
}

#[test]
fn speaking_the_bars_are_a_vu_meter_and_the_mouth_opens_from_the_tip() {
    let quiet = board("SS\nM010\n");
    let loud = board("SS\nM255\n");
    let n = |fw: &Fw| smalls(fw, 1).iter().filter(|c| on(c)).count();
    assert_eq!(n(&loud), SMALL_PER_BOARD);
    assert!(n(&quiet) < 3);
    assert!(on(&smalls(&quiet, 0)[0]) && !on(&smalls(&quiet, 0)[7]), "bottom rows first");
    assert!(loud.mouth.iter().all(on));
    let open = |fw: &Fw| (0..MOUTH_LEDS).filter(|&i| fw.mouth[i] == [255, 120, 20]).map(mouth_rank).max();
    assert!(open(&quiet) < open(&loud));
    assert!(loud.eyes()[0][0] > quiet.eyes()[0][0], "eyes brighten with the level");
}

#[test]
fn thinking_one_cyan_dot_scans_and_the_eyes_alternate() {
    let mut fw = board("ST\n");
    let cyan = |fw: &Fw| fw.body().iter().filter(|c| **c == [0, 255, 255]).count();
    assert_eq!(cyan(&fw), 1);
    let before = fw.eyes().to_vec();
    fw.update(1250.0);
    assert_ne!(fw.eyes(), &before[..], "the lit eye hops at 4 Hz");
    assert!(fw.eyes().contains(&[0, 255, 255]));
}

#[test]
fn a_tempo_chases_every_bar_and_b000_stops_it() {
    let mut fw = board("SI\nB120\n");
    let lit = |fw: &Fw, b| smalls(fw, b).iter().filter(|c| c.iter().any(|&v| v > 30)).count();
    for b in 0..BODY_BOARDS {
        assert!((1..=2).contains(&lit(&fw, b)), "board {b}: one two-LED bar");
    }
    fw.write("B000\n");
    fw.update(1020.0);
    assert_eq!(fw.bpm, 0);
}

#[test]
fn a_down_subsystem_blinks_its_window_red_and_fault_reddens_everything() {
    let mut fw = board("SI\nH1fd\n"); // window 1 (board A, second window) down
    fw.update(1250.0); // blink phase on
    assert_eq!(window(&fw, 0, 1), [229, 17, 0]);
    assert_ne!(window(&fw, 0, 0)[0..2], [229, 17]);
    fw.write("X3\n");
    fw.update(1500.0);
    for b in 0..BODY_BOARDS {
        for k in 0..3 {
            let w = window(&fw, b, k);
            assert!(w[0] > 0 && w[2] == 0 && w[1] < w[0] / 4, "red, not {w:?}");
        }
    }
    assert!(!on(&fw.mouth[3]));
}

#[test]
fn sf_flashes_the_eyes_green_then_drops_into_engaged() {
    let mut fw = board("SS\n");
    fw.write("SF\n");
    fw.update(1100.0);
    assert_eq!(fw.eyes(), &[[0, 255, 0], [0, 255, 0]]);
    fw.update(1400.0);
    assert_eq!((fw.mode, fw.eyes()[0]), (b'E', [100, 180, 255]));
}

// ------------------------------------------------------------------ the performer

fn grnwave_profile() -> RobotProfile {
    let raw = std::fs::read_to_string(repo("profiles/r3x/robot.json")).unwrap();
    let mut v: serde_json::Value = serde_json::from_str(&raw).unwrap();
    v["electronics"] = "grnwave_full_led".into();
    RobotProfile::from_json(&v.to_string()).unwrap()
}

#[test]
fn the_performer_drives_the_grnwave_emulator_from_the_face_and_chest_streams() {
    let mut p = Performer::new(Arc::new(show_catalog()), &grnwave_profile(), PerformerConfig::default()).unwrap();
    let mut t = 0.0;
    let mut step = |p: &mut Performer, to: f64| {
        let mut f = None;
        while t <= to {
            f = Some(p.tick(t));
            t += 1.0 / 60.0;
        }
        f.unwrap()
    };
    p.command(Command::ServiceStatus {
        service: "ClaudeService".into(),
        status: "running".into(),
        detail: None,
        latched: None,
    });
    step(&mut p, 3.5); // the chest host's 3 s boot sweep (X1) ends with X0
    assert_eq!(p.grnwave.as_ref().unwrap().sys_state, 0);
    p.command(Command::ListeningStarted);
    p.command(Command::ListeningStopped);
    let f = step(&mut p, 4.0);
    let g = p.grnwave.as_ref().unwrap();
    assert_eq!(g.mode, b'T', "listening stopped -> thinking, via the face's ST");
    let lights = f.to_contract().lights;
    let sizes: Vec<(&str, usize)> = lights.iter().map(|(k, v)| (k.as_str(), v.len())).collect();
    assert_eq!(sizes, [("body", 96), ("eyes", 2), ("mouth", 8), ("stage", 11)]);
    p.command(Command::SpeechStarted { timings: None, tags: vec![] });
    p.command(Command::Amplitude { value: 0.9 });
    let f = step(&mut p, 4.5);
    assert_eq!(p.grnwave.as_ref().unwrap().mode, b'S');
    assert!(f.package["mouth"].iter().any(on), "the mouth moves with speech");
    p.command(Command::Tempo { bpm: 120.0 });
    p.command(Command::Music { playing: true });
    step(&mut p, 5.0);
    assert_eq!(p.grnwave.as_ref().unwrap().bpm, 120, "Bnnn from the chest stream");
}

#[test]
fn the_native_package_has_no_package_frames() {
    let profile = RobotProfile::load(repo("profiles/r3x/robot.json")).unwrap();
    let mut p = Performer::new(Arc::new(show_catalog()), &profile, PerformerConfig::default()).unwrap();
    let f = p.tick(0.0);
    assert!(p.grnwave.is_none() && f.package.is_empty());
    assert_eq!(f.to_contract().lights["chest"].len(), 33);
}
