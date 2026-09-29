//! LED emulators: ports of sim/web/test/firmware.test.ts and chest.test.ts, plus byte-exact
//! parity against the TS emulators (fixtures from sim/web/scripts/gen-led-parity.mjs).

use indexmap::IndexMap;
use r3x_performer_core::leds::chest::{
    health_mask, ChestFirmware, ChestHost, ChestLightKind, ChestLightSpec,
};
use r3x_performer_core::leds::firmware::{scale8, FirmwareOptions, RexFaceFirmware, Rgb, State};
use r3x_performer_core::leds::host::{
    CantinaHostEmulator, DualHost, SerialDir, SystemMode, TtsAmplitudeAgc,
};
use r3x_performer_core::rng::Rng;
use serde::Deserialize;
use serde_json::Value;

fn boot_with(oob_aliases_mouth: bool) -> RexFaceFirmware {
    let mut fw = RexFaceFirmware::new(FirmwareOptions {
        oob_aliases_mouth,
        rng: Rng::Const(0.5),
    });
    assert_eq!(fw.read_lines(), vec!["READY"]);
    fw
}

fn boot() -> RexFaceFirmware {
    boot_with(true)
}

fn lit(px: &[Rgb]) -> Vec<bool> {
    px.iter().map(|c| c.iter().any(|&v| v > 0)).collect()
}

// ------------------------------------------------------------------ firmware.test.ts

#[test]
fn acks_valid_state_commands_and_rejects_unknown_ones() {
    let mut fw = boot();
    fw.write("SE\nSX\nR\n");
    fw.advance_to(5.0);
    assert_eq!(fw.read_lines(), vec!["+", "-", "+"]);
    assert_eq!(fw.current_state, State::Idle); // R resets to IDLE
}

#[test]
fn parses_mnnn_as_fire_and_forget_and_clamps_to_255() {
    let mut fw = boot();
    fw.write("M300\nM042\n");
    fw.advance_to(2.0);
    assert!(fw.read_lines().is_empty());
    assert_eq!(fw.mouth_amplitude, 42);
    fw.write("M999\n");
    fw.advance_to(3.0);
    assert_eq!(fw.mouth_amplitude, 255);
}

#[test]
fn scale8_matches_fastled_fixed_scaling() {
    assert_eq!(scale8(255, 255), 255);
    assert_eq!(scale8(255, 128), 128);
    assert_eq!(scale8(100, 0), 0);
}

#[test]
fn boots_dark_then_idle_breathing_paints_orange_eyes_on_the_first_20_ms_tick() {
    let mut fw = boot();
    assert_eq!(fw.eye_leds[0], [0, 0, 0]);
    fw.advance_to(25.0);
    let [r, g, b] = fw.eye_leds[1];
    assert!(r > 200);
    assert!(g > 40);
    assert_eq!(b, 0);
}

#[test]
fn idle_mouth_lights_only_the_v_middle_in_blue() {
    let mut fw = boot();
    fw.advance_to(50.0);
    assert_eq!(
        lit(&fw.mouth_leds),
        [false, true, true, false, false, true, true, false]
    );
    assert!(fw.mouth_leds[1][2] > fw.mouth_leds[1][0]);
}

#[test]
fn speaking_mouth_stages_outward_with_amplitude() {
    let mut fw = boot();
    fw.write("SS\n");
    let mut lit_at = |amp: u32| {
        fw.write(&format!("M{amp:03}\n"));
        fw.advance_to(fw.now + 5.0);
        lit(&fw.mouth_leds)
    };
    assert_eq!(
        lit_at(1),
        [false, true, false, false, false, false, true, false]
    );
    assert_eq!(
        lit_at(10),
        [false, true, true, false, false, true, true, false]
    );
    assert_eq!(
        lit_at(60),
        [true, true, true, false, false, true, true, true]
    );
    assert_eq!(lit_at(255), [true; 8]);
    assert_eq!(lit_at(0), [false; 8]);
}

#[test]
fn flash_pulses_green_then_drops_into_engaged() {
    let mut fw = boot();
    fw.write("SS\n");
    fw.advance_to(10.0);
    fw.write("SF\n");
    fw.advance_to(40.0);
    assert!(fw.flash_active);
    assert!(fw.eye_leds[0][1] > 0);
    assert_eq!(fw.eye_leds[0][0], 0);
    fw.advance_to(400.0);
    assert!(!fw.flash_active);
    assert_eq!(fw.current_state, State::Engaged);
}

const CYAN: Rgb = [0, 255, 255];

#[test]
fn thinking_left_eye_has_two_dots_right_eye_one() {
    let mut fw = boot_with(false);
    fw.write("ST\n");
    fw.advance_to(100.0); // a few 30 ms steps in
    assert_eq!(fw.eye_leds[..7].iter().filter(|&&c| c == CYAN).count(), 2);
    assert_eq!(fw.eye_leds[7..].iter().filter(|&&c| c == CYAN).count(), 1);
}

#[test]
fn thinking_step_0_overruns_eye_leds_14_aliased_it_paints_mouth_led_0_cyan() {
    let mut fw = boot_with(true);
    fw.write("ST\n");
    fw.advance_to(40.0);
    assert_eq!(fw.mouth_leds[0], CYAN);
}

#[test]
fn drops_the_end_of_speech_m000_inside_the_10_hz_window_leaving_the_mouth_lit() {
    let mut host = CantinaHostEmulator::new(boot(), true);
    host.set_mode(SystemMode::Interactive);
    host.speech_started();
    host.tick();
    host.fw.advance_to(20.0);
    for _ in 0..10 {
        host.amplitude(0.9);
        let t = host.fw.now + 17.0;
        host.fw.advance_to(t);
    }
    host.speech_ended(); // < 100 ms after the last mouth command
    for _ in 0..30 {
        host.tick();
        let t = host.fw.now + 17.0;
        host.fw.advance_to(t);
    }
    let sent: Vec<String> = host
        .take_tapped()
        .into_iter()
        .filter(|l| l.dir == SerialDir::Tx)
        .map(|l| l.line)
        .collect();
    assert_eq!(host.dropped_mouth_resets, 1);
    assert!(!sent.iter().any(|l| l == "M000"));
    assert_eq!(host.fw.current_state, State::Engaged);
    assert!(host.fw.mouth_amplitude > 0);
    assert!(lit(&host.fw.mouth_leds).contains(&true));
}

// ------------------------------------------------------------------ chest.test.ts

/// Three panels ~31 deg apart, each 8 dots (vertical) + 3 windows - the detected layout.
fn layout() -> Vec<ChestLightSpec> {
    let mut out = Vec::new();
    for deg in [-88.7f64, -57.7, -26.7] {
        let a = (deg * std::f64::consts::PI) / 180.0;
        let at = |y: f64| [a.sin() * 0.14, y, a.cos() * 0.14];
        let spec = |kind, y: f64, w, h| ChestLightSpec {
            kind,
            pos: at(y),
            normal: [0.0, 0.0, 1.0],
            w,
            h,
            panel: "MS_P_1_Full".into(),
        };
        for k in 0..8 {
            out.push(spec(
                ChestLightKind::Dot,
                0.4336 + f64::from(k) * 0.0055,
                0.0024,
                0.0024,
            ));
        }
        for k in 0..3 {
            out.push(spec(
                ChestLightKind::Window,
                0.44 + f64::from(k) * 0.015,
                0.014,
                0.012,
            ));
        }
    }
    out
}

fn chest() -> ChestFirmware {
    ChestFirmware::new(layout(), Rng::Const(0.5))
}

fn lit_kind(fw: &ChestFirmware, kind: ChestLightKind) -> usize {
    fw.specs
        .iter()
        .zip(&fw.pixels)
        .filter(|(s, p)| s.kind == kind && p.iter().any(|&v| v > 0))
        .count()
}

#[test]
fn speaking_the_dots_are_a_vu_meter_driven_by_mnnn() {
    let mut fw = chest();
    fw.write("X0\nSS\nM255\n");
    fw.update(100.0);
    assert_eq!(lit_kind(&fw, ChestLightKind::Dot), 24);
    fw.write("M000\n");
    fw.update(120.0);
    assert_eq!(lit_kind(&fw, ChestLightKind::Dot), 0);
    assert_eq!(lit_kind(&fw, ChestLightKind::Window), 9); // windows keep a base glow
}

#[test]
fn a_vu_level_lights_the_bottom_rows_first() {
    let mut fw = chest();
    fw.write("X0\nSS\nM064\n"); // sqrt(64/255) ~ 0.5 -> 4 of 8 rows
    fw.update(100.0);
    assert_eq!(
        lit(&fw.pixels[..8]),
        [true, true, true, true, false, false, false, false]
    );
}

#[test]
fn bnnn_starts_a_beat_chase_and_b000_stops_it_sf_does_not_change_the_mode() {
    let mut fw = chest();
    fw.write("B120\nSF\n");
    fw.update(0.0);
    assert_eq!(fw.bpm, 120);
    assert_eq!(fw.mode, 'I');
    fw.write("B000\n");
    fw.update(10.0);
    assert_eq!(fw.bpm, 0);
}

#[test]
fn boots_into_the_boot_sweep_and_leaves_it_on_x0() {
    let mut fw = chest();
    assert_eq!(fw.sys_state, 1);
    fw.write("X0\n");
    fw.update(10.0);
    assert_eq!(fw.sys_state, 0);
}

#[test]
fn a_down_subsystem_blinks_its_window_red_fault_turns_every_window_red() {
    let mut fw = chest();
    fw.write("X0\nSE\nH1FE\n"); // window 0 (mic / STT) down
    fw.update(250.0); // blink phase "on"
    let w0 = fw
        .specs
        .iter()
        .position(|s| s.kind == ChestLightKind::Window)
        .unwrap();
    assert!(fw.pixels[w0][0] > 200);
    assert!(fw.pixels[w0][1] < 40);
    fw.write("X3\n");
    fw.update(500.0);
    let windows = fw
        .specs
        .iter()
        .zip(&fw.pixels)
        .filter(|(s, _)| s.kind == ChestLightKind::Window);
    assert!(
        windows.clone().count() == 9 && windows.into_iter().all(|(_, p)| p[0] > 0 && p[2] == 0)
    );
}

#[test]
fn chest_host_produces_the_same_conversation_sequence_as_the_python_service_test() {
    let mut h = ChestHost::default();
    h.set_mode("INTERACTIVE");
    h.tick(0.0);
    let _ = h.take_sent();
    h.listening_started();
    h.tick(1.0);
    h.listening_stopped();
    h.tick(2.0);
    h.speech_started();
    h.tick(3.0);
    for a in [0.8, 0.9, 0.7] {
        h.amplitude_in(a);
        h.tick(4.0);
    }
    h.speech_ended();
    h.tick(5.0);
    let sent = h.take_sent();
    let s: Vec<&str> = sent
        .iter()
        .map(String::as_str)
        .filter(|c| c.starts_with('S'))
        .collect();
    assert_eq!(s, ["SL", "ST", "SS", "SF", "SE"]);
    assert_eq!(
        sent.iter()
            .rfind(|c| c.starts_with('M'))
            .map(String::as_str),
        Some("M000")
    );
    let pos = |w: &str| sent.iter().position(|c| c == w).unwrap();
    assert!(pos("M000") < pos("SF"));
}

#[test]
fn chest_host_llm_down_is_a_fault_music_down_only_blinks_its_window() {
    let mut h = ChestHost::default();
    h.set_mode("IDLE");
    h.service_status("MusicControllerService", "degraded", true);
    h.tick(0.0);
    let mut sent = h.take_sent();
    assert!(sent.contains(&format!("H{:03X}", 0x1ff & !(1 << 4))));
    assert!(!sent.iter().any(|c| c == "X3"));
    h.service_status("ClaudeService", "error", true);
    h.tick(1.0);
    sent.extend(h.take_sent());
    assert!(sent.iter().any(|c| c == "X3"));
    assert_eq!(health_mask(&IndexMap::new()), 0x1ff);
}

// ------------------------------------------------------------------ TS parity

#[derive(Deserialize)]
struct Fixture {
    name: String,
    seed: u32,
    oob_aliases_mouth: bool,
    host: bool,
    end: u32,
    sample_every: u32,
    chest_specs: Vec<ChestLightSpec>,
    events: Vec<Event>,
    frames: Vec<Frame>,
}

#[derive(Deserialize)]
struct Event {
    at: f64,
    ev: String,
    #[serde(default)]
    a: Vec<Value>,
}

#[derive(Deserialize, Debug, PartialEq)]
struct Frame {
    t: u32,
    h: u32,
    st: String,
    eye: String,
    mouth: String,
    chest: String,
    tx: Vec<(String, f64)>,
    rx: Vec<String>,
    ctx: Vec<String>,
    dropped: u32,
}

fn hex(px: &[Rgb]) -> String {
    px.iter().flatten().map(|b| format!("{b:02x}")).collect()
}

fn mode(v: &Value) -> SystemMode {
    match v.as_str().unwrap() {
        "IDLE" => SystemMode::Idle,
        "AMBIENT" => SystemMode::Ambient,
        "INTERACTIVE" => SystemMode::Interactive,
        m => panic!("mode {m}"),
    }
}

/// The driver loop of gen-led-parity.mjs, step for step.
fn replay(f: &Fixture) -> Vec<Frame> {
    let face = RexFaceFirmware::new(FirmwareOptions {
        oob_aliases_mouth: f.oob_aliases_mouth,
        rng: Rng::new(f.seed),
    });
    let mut chest_fw = ChestFirmware::new(f.chest_specs.clone(), Rng::new(f.seed + 1));
    let mut dual = DualHost::new(CantinaHostEmulator::new(face, true), ChestHost::default());
    let mut agc = TtsAmplitudeAgc::new();
    let (mut rx, mut ctx, mut frames) = (Vec::new(), Vec::new(), Vec::new());
    let mut h: u32 = 0x811c_9dc5;
    let mut next = 0;
    for step in 0..=f.end {
        let t = f64::from(step);
        dual.face.fw.advance_to(t);
        while next < f.events.len() && f.events[next].at <= t {
            let Event { ev, a, .. } = &f.events[next];
            let num = |i: usize| a.get(i).and_then(Value::as_f64);
            let s = |i: usize| a[i].as_str().unwrap();
            let b = |i: usize| a[i].as_bool().unwrap();
            let ch = &mut dual.chest;
            match ev.as_str() {
                "setMode" => dual.set_mode(mode(&a[0])),
                "listeningStarted" => dual.listening_started(),
                "listeningStopped" => dual.listening_stopped(),
                "llmChunk" => dual.llm_chunk(),
                "speechStarted" => dual.speech_started(),
                "rms" => dual.amplitude(agc.next(num(0).unwrap())),
                "speechEnded" => {
                    dual.speech_ended();
                    agc.reset();
                }
                "eyeCommand" => {
                    dual.face.eye_command(s(0), num(1).unwrap_or(0.0));
                }
                "serviceStatus" => {
                    ch.service_status(s(0), s(1), a.get(2).is_none_or(|v| v.as_bool().unwrap()))
                }
                "faultHold" => ch.fault_hold_ms = num(0).unwrap(),
                "music" => ch.music(b(0), num(1)),
                "dj" => ch.dj(b(0), num(1)),
                "sleep" => ch.sleeping = b(0),
                "chestBoot" => ch.boot(t),
                "override" => ch.override_command(s(0), num(1).unwrap(), t),
                "faceWrite" => dual.face.fw.write(s(0)),
                "chestWrite" => chest_fw.write(s(0)),
                "blinkNow" => dual.face.fw.blink_now(),
                other => panic!("unknown event {other}"),
            }
            next += 1;
        }
        if f.host {
            dual.tick(t);
            for c in dual.chest.take_sent() {
                chest_fw.write(&format!("{c}\n"));
                ctx.push(c);
            }
        }
        chest_fw.update(t);
        rx.extend(dual.face.fw.read_lines());
        let fw = &dual.face.fw;
        for &byte in fw
            .eye_leds
            .iter()
            .chain(&fw.mouth_leds)
            .chain(&chest_fw.pixels)
            .flatten()
        {
            h ^= u32::from(byte);
            h = h.wrapping_mul(16_777_619);
        }
        if step % f.sample_every == 0 {
            frames.push(Frame {
                t: step,
                h,
                st: fw.current_state.as_str().into(),
                eye: hex(&fw.eye_leds),
                mouth: hex(&fw.mouth_leds),
                chest: hex(&chest_fw.pixels),
                tx: dual
                    .face
                    .take_tapped()
                    .into_iter()
                    .map(|l| (l.line, l.at_ms))
                    .collect(),
                rx: std::mem::take(&mut rx),
                ctx: std::mem::take(&mut ctx),
                dropped: dual.dropped_mouth_resets(),
            });
        }
    }
    frames
}

fn check(json: &str) {
    let f: Fixture = serde_json::from_str(json).unwrap();
    let got = replay(&f);
    assert_eq!(got.len(), f.frames.len(), "{}: frame count", f.name);
    for (g, want) in got.iter().zip(&f.frames) {
        assert_eq!(g, want, "{}: first divergence at t={} ms", f.name, want.t);
    }
}

#[test]
fn parity_conversation() {
    check(include_str!("parity/leds_conversation.json"));
}

#[test]
fn parity_idle() {
    check(include_str!("parity/leds_idle.json"));
}

#[test]
fn parity_raw_serial() {
    check(include_str!("parity/leds_raw_serial.json"));
}

#[test]
fn parity_raw_serial_nooob() {
    check(include_str!("parity/leds_raw_serial_nooob.json"));
}

#[test]
fn parity_chest() {
    check(include_str!("parity/leds_chest.json"));
}

#[test]
fn parity_chest_synthetic() {
    check(include_str!("parity/leds_chest_synthetic.json"));
}
