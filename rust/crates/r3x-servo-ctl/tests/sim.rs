//! The controller logic on the host: fake clock, fake PWM (`pulses()`), fake serial. Host
//! messages are encoded with the host driver's own codec (`r3x_drivers::servo::proto`), so
//! these also pin wire compatibility between the two independent implementations.

use std::collections::{BTreeMap, VecDeque};
use std::io;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use r3x_contracts::profile::DriverKind;
use r3x_contracts::RobotProfile;
use r3x_drivers::link::{Link, Opener, PortInfo};
use r3x_drivers::servo::proto::{flag, ChannelConfig, Deframer, Msg, Telemetry};
use r3x_drivers::servo::r3x::{channel_configs, R3xServoConfig, R3xServoDriver};
use r3x_drivers::Driver;
use r3x_performer_core::performer::Out;
use r3x_servo_ctl::controller::{HEARTBEAT_TIMEOUT_US, STAGGER_US};
use r3x_servo_ctl::{Controller, CHANNELS, DT};

const TICK_US: u64 = 5_000;

fn profile() -> RobotProfile {
    RobotProfile::load(concat!(env!("CARGO_MANIFEST_DIR"), "/../../../profiles/r3x/robot.json")).unwrap()
}

/// Every profile channel but 17 (head_roll), which the tests keep as the unconfigured one.
fn configs() -> BTreeMap<String, ChannelConfig> {
    let mut c = channel_configs(&profile(), DriverKind::R3xServo);
    assert_eq!(c.remove("head_roll").map(|r| r.ch), Some(17));
    c
}

/// A host talking to a controller over a fake wire, on a fake clock.
struct Bench {
    c: Controller,
    now: u64,
    seq: u16,
    rx: Deframer,
    from_ctl: Vec<(u16, Msg)>,
    heartbeat: bool,
    last_beat: u64,
}

impl Bench {
    fn new() -> Self {
        Bench { c: Controller::new(), now: 0, seq: 0, rx: Deframer::default(), from_ctl: vec![], heartbeat: false, last_beat: 0 }
    }

    /// Configured from the real profile, heartbeating, powered up.
    fn running() -> Self {
        let mut b = Self::new();
        for c in configs().values() {
            b.send(&Msg::Config(*c));
        }
        b.heartbeat = true;
        b.run(1.2);
        b
    }

    fn send(&mut self, m: &Msg) -> u16 {
        self.seq = self.seq.wrapping_add(1);
        self.send_raw(&m.encode(self.seq));
        self.seq
    }

    fn send_raw(&mut self, bytes: &[u8]) {
        let mut out = vec![];
        self.c.receive(bytes, self.now, &mut |f: &[u8]| out.extend_from_slice(f));
        self.from_ctl.extend(self.rx.push(&out));
    }

    /// Advance one control period; `each` sees the controller after the tick.
    fn step(&mut self) {
        self.now += TICK_US;
        if self.heartbeat && self.now - self.last_beat >= 100_000 {
            self.last_beat = self.now;
            self.send(&Msg::Heartbeat { outputs_enabled: true, mask: u32::MAX });
        }
        self.c.tick(self.now, DT);
        let mut out = vec![];
        self.c.poll_telemetry(self.now, &mut |f: &[u8]| out.extend_from_slice(f));
        self.from_ctl.extend(self.rx.push(&out));
    }

    fn run(&mut self, secs: f64) {
        for _ in 0..(secs * 200.0).round() as u64 {
            self.step();
        }
    }

    fn telemetry(&self) -> Telemetry {
        self.from_ctl
            .iter()
            .rev()
            .find_map(|(_, m)| if let Msg::Telemetry(t) = m { Some(t.clone()) } else { None })
            .expect("telemetry")
    }

    fn naks(&self) -> Vec<(u16, u8)> {
        self.from_ctl.iter().filter_map(|(_, m)| if let Msg::Nak { seq, code } = m { Some((*seq, *code)) } else { None }).collect()
    }
}

fn goal(ch: u8, target: f32) -> Msg {
    Msg::Goal { ch, target, v_max: 0.0, a_max: 0.0, j_max: 0.0 }
}

#[test]
fn boot_is_silent_then_parks_with_staggered_power_up() {
    let mut b = Bench::new();
    b.run(0.2);
    assert_eq!(b.telemetry().flags & (flag::UNCONFIGURED | flag::HOLDING), flag::UNCONFIGURED | flag::HOLDING);
    let cfgs = configs();
    for c in cfgs.values() {
        b.send(&Msg::Config(*c));
    }
    b.send(&goal(0, 20.0)); // before any heartbeat: ignored, not an error
    b.run(0.5);
    assert!(b.c.pulses().iter().all(|&us| us == 0) && !b.c.rail_enabled(), "no pulses before an enabling heartbeat");
    assert!(b.naks().is_empty());

    b.send(&Msg::Heartbeat { outputs_enabled: true, mask: u32::MAX });
    let t0 = b.now;
    b.heartbeat = true;
    b.last_beat = t0;
    let mut first_pulse = [None::<u64>; CHANNELS];
    while b.now < t0 + 1_200_000 {
        b.step();
        for (i, &us) in b.c.pulses().iter().enumerate() {
            if us != 0 && first_pulse[i].is_none() {
                first_pulse[i] = Some(b.now - t0);
                // The first pulse is the park pose: centre (+ trim).
                let c = cfgs.values().find(|c| usize::from(c.ch) == i).unwrap();
                assert_eq!(us, (c.center_us + c.trim_us).round() as u16, "ch {i} powers up at park");
            }
        }
    }
    assert!(b.c.rail_enabled());
    for c in cfgs.values() {
        let i = usize::from(c.ch);
        let group = (i / 4) as u64;
        let t = first_pulse[i].expect("powered");
        assert!(t >= group * STAGGER_US && t <= group * STAGGER_US + 2 * TICK_US, "ch {i} powered at {t} us");
    }
    assert_eq!(first_pulse[17], None, "channel 17 is unconfigured: never pulses");
    let tm = b.telemetry();
    assert_eq!(tm.flags & (flag::HOLDING | flag::UNCONFIGURED), 0);
    assert_ne!(tm.flags & flag::PARKED, 0);
    assert_ne!(tm.channels[17].flags & flag::UNCONFIGURED, 0);
    assert!((b.c.state(0).0).abs() < 1e-9, "the pre-heartbeat goal did nothing");
}

#[test]
fn goals_are_followed_and_limits_never_exceeded() {
    let mut b = Bench::running();
    let cfgs: Vec<ChannelConfig> = configs().into_values().collect();
    // Deterministic pseudo-random goals, a third of them outside the soft range, some with
    // tighter per-goal limits.
    let mut rng = 0x1234_5678u32;
    let mut next = || {
        rng ^= rng << 13;
        rng ^= rng >> 17;
        rng ^= rng << 5;
        f64::from(rng) / f64::from(u32::MAX)
    };
    let mut prev_v = [0.0f64; CHANNELS];
    for step in 0..(8 * 200) {
        if step % 60 == 0 {
            for c in &cfgs {
                let (lo, hi) = (f64::from(c.soft_min), f64::from(c.soft_max));
                let target = lo - (hi - lo) * 0.25 + next() * (hi - lo) * 1.5;
                let slow = next() < 0.3;
                let v = if slow { c.v_max * 0.5 } else { 0.0 };
                b.send(&Msg::Goal { ch: c.ch, target: target as f32, v_max: v, a_max: 0.0, j_max: 0.0 });
            }
        }
        b.step();
        let pulses = b.c.pulses();
        for c in &cfgs {
            let i = usize::from(c.ch);
            let (x, v) = b.c.state(i);
            let (lo, hi) = b.c.soft_range(i);
            assert!(x >= lo && x <= hi, "ch {i}: x {x} outside soft {lo}..{hi}");
            assert!(v.abs() <= f64::from(c.v_max) * (1.0 + 1e-6), "ch {i}: |v| {v} > v_max {}", c.v_max);
            let a = (v - prev_v[i]) / DT;
            assert!(a.abs() <= f64::from(c.a_max) * (1.0 + 1e-6), "ch {i}: |a| {a} > a_max {}", c.a_max);
            prev_v[i] = v;
            assert!(pulses[i] >= c.pulse_min_us && pulses[i] <= c.pulse_max_us, "ch {i}: pulse {}", pulses[i]);
        }
    }
    let clamped = b.from_ctl.iter().filter(|(_, m)| matches!(m, Msg::Telemetry(t) if t.flags & flag::SOFT_LIMIT != 0)).count();
    assert!(clamped > 0, "clamped goals are reported");
    assert!(b.naks().is_empty());

    // Finally every channel settles on an in-range goal, and telemetry reports it.
    for c in &cfgs {
        let mid = (c.soft_min + c.soft_max) / 2.0 + (c.soft_max - c.soft_min) * 0.2;
        b.send(&goal(c.ch, mid));
    }
    b.run(6.0);
    let tm = b.telemetry();
    for c in &cfgs {
        let mid = (c.soft_min + c.soft_max) / 2.0 + (c.soft_max - c.soft_min) * 0.2;
        let x = tm.channels[usize::from(c.ch)].x;
        // Within 0.01 (far below one pulse us): the reference trapezoid can limit-cycle by a
        // few thousandths of a degree around its target.
        assert!((x - mid).abs() < 0.01, "ch {}: settled at {x}, goal {mid}", c.ch);
    }
    assert_eq!(tm.flags & flag::SOFT_LIMIT, 0, "flag clears once reported");
}

#[test]
fn heartbeat_loss_ramps_to_hold_within_250ms() {
    let mut b = Bench::running();
    let pan = configs()["head_pan"];
    // A slow move (20 deg/s), so it is still under way when the host dies.
    b.send(&Msg::Goal { ch: pan.ch, target: pan.soft_max - 1.0, v_max: 20.0, a_max: 0.0, j_max: 0.0 });
    b.run(0.5);
    let (_, v0) = b.c.state(0);
    assert!(v0 > 15.0, "moving: {v0}");

    b.heartbeat = false; // host killed
    let last_beat = b.last_beat;
    let mut holding_at = None;
    let mut max_x = f64::MIN;
    while b.now < last_beat + 3_000_000 {
        b.step();
        if holding_at.is_none() && b.telemetry().flags & flag::HOLDING != 0 {
            holding_at = Some(b.now - last_beat);
            b.send(&goal(pan.ch, pan.soft_min)); // ignored while holding
        }
        let (x, v) = b.c.state(0);
        max_x = max_x.max(x);
        assert!(v > -1e-6, "no reversal while ramping to hold (v {v})");
    }
    let held = holding_at.expect("HOLDING reported");
    // The controller holds from 250 ms of silence; telemetry (40 ms) reports it.
    assert!(held <= HEARTBEAT_TIMEOUT_US + 45_000, "HOLDING after {held} us");
    let (x, v) = b.c.state(0);
    assert!(v.abs() < 1e-6, "at rest");
    assert!(x < f64::from(pan.soft_max - 1.0) && x == max_x, "stopped short of the goal, no overshoot");
    assert!(b.naks().is_empty(), "goals while holding are ignored, not rejected");
    let pulse = b.c.pulses()[0];
    assert_ne!(pulse, 0, "holding keeps the pulse (the servo holds its pose)");

    // Heartbeat back: goals apply again.
    b.heartbeat = true;
    b.run(0.2);
    b.send(&goal(pan.ch, 0.0));
    b.run(3.0);
    assert!(b.c.state(0).0.abs() < 1e-3);
    assert_eq!(b.telemetry().flags & flag::HOLDING, 0);
}

#[test]
fn outputs_disabled_or_masked_holds() {
    let mut b = Bench::running();
    let pan = configs()["head_pan"];
    let lift = configs()["head_lift"];
    // Only the lift enabled.
    b.heartbeat = false;
    b.send(&Msg::Heartbeat { outputs_enabled: true, mask: 1 << lift.ch });
    b.send(&goal(pan.ch, 20.0));
    b.send(&goal(lift.ch, 5.0));
    for _ in 0..4 {
        b.run(0.1);
        b.send(&Msg::Heartbeat { outputs_enabled: true, mask: 1 << lift.ch });
    }
    assert!(b.c.state(usize::from(pan.ch)).0.abs() < 1e-9, "masked channel holds");
    assert!(b.c.state(usize::from(lift.ch)).0 > 1.0, "enabled channel moves");
    let tm = b.telemetry();
    assert_ne!(tm.channels[usize::from(pan.ch)].flags & flag::HOLDING, 0);
    assert_eq!(tm.channels[usize::from(lift.ch)].flags & flag::HOLDING, 0);
    // outputs_enabled = 0 holds everything at once.
    b.send(&Msg::Heartbeat { outputs_enabled: false, mask: u32::MAX });
    b.step();
    assert!(b.c.holding(b.now));
}

#[test]
fn seq_echo_and_naks() {
    let mut b = Bench::running();
    let s = b.send(&goal(0, 1.0));
    b.run(0.05);
    // The heartbeat after it is the last applied packet.
    assert_eq!(b.telemetry().last_seq, b.seq);
    assert!(b.seq >= s);
    let applied = b.seq;

    let bad_ch = b.send(&goal(99, 1.0));
    let unconf = b.send(&goal(17, 1.0));
    let unknown = {
        b.seq += 1;
        // A valid frame of an unknown type, built with the host's framing.
        let mut raw = vec![0x7E, (b.seq & 0xFF) as u8, (b.seq >> 8) as u8];
        raw.extend_from_slice(&r3x_drivers::servo::proto::crc16(&raw).to_le_bytes());
        let mut f = r3x_drivers::servo::proto::cobs_encode(&raw);
        f.push(0);
        b.send_raw(&f);
        b.seq
    };
    let short = {
        let mut enc = goal(0, 1.0).encode(b.seq.wrapping_add(1));
        b.seq += 1;
        // Re-frame the packet with its last payload byte dropped (CRC recomputed).
        let raw = r3x_drivers::servo::proto::cobs_decode(&enc[..enc.len() - 1]).unwrap();
        let mut body = raw[..raw.len() - 3].to_vec();
        body.extend_from_slice(&r3x_drivers::servo::proto::crc16(&body).to_le_bytes());
        enc = r3x_drivers::servo::proto::cobs_encode(&body);
        enc.push(0);
        b.send_raw(&enc);
        b.seq
    };
    let nan = b.send(&goal(0, f32::NAN));
    let mut corrupt = goal(0, 1.0).encode(b.seq + 1);
    corrupt[3] ^= 0x10;
    b.send_raw(&corrupt); // dropped silently: no NAK, no seq
    assert_eq!(b.naks(), [(bad_ch, 1), (unconf, 2), (unknown, 4), (short, 3), (nan, 5)]);
    b.heartbeat = false;
    b.run(0.05);
    assert_eq!(b.telemetry().last_seq, applied, "rejected packets do not advance last_seq");
}

#[test]
fn rail_stall_holds_everything_until_it_clears() {
    let mut b = Bench::running();
    b.send(&Msg::Goal { ch: 0, target: 30.0, v_max: 10.0, a_max: 0.0, j_max: 0.0 });
    b.run(0.2);
    for _ in 0..70 {
        b.c.set_rail_ma(7000, b.now);
        b.step();
    }
    let tm = b.telemetry();
    assert_ne!(tm.flags & (flag::OVERCURRENT | flag::HOLDING), 0);
    assert_eq!(tm.rail_ma, 7000);
    b.run(2.0);
    let x_stalled = b.c.state(0).0;
    assert!(x_stalled < 30.0 - 1.0, "stopped");
    for _ in 0..210 {
        b.c.set_rail_ma(1500, b.now);
        b.step();
    }
    assert_eq!(b.telemetry().flags & flag::OVERCURRENT, 0, "cleared after 1 s below the clear level");
    b.send(&goal(0, 30.0));
    b.run(3.0);
    assert!((b.c.state(0).0 - 30.0).abs() < 1e-3);
}

// ---- end to end: the real host driver over an in-memory serial pair -----------------

struct Shared {
    c: Controller,
    now: u64,
    to_host: VecDeque<u8>,
}

#[derive(Clone)]
struct SimLink(Arc<Mutex<Shared>>);

impl Link for SimLink {
    fn write_all(&mut self, data: &[u8]) -> io::Result<()> {
        let mut s = self.0.lock().unwrap();
        let now = s.now;
        let mut out = vec![];
        s.c.receive(data, now, &mut |f: &[u8]| out.extend_from_slice(f));
        s.to_host.extend(out);
        Ok(())
    }
    fn read(&mut self, buf: &mut [u8], _timeout: Duration) -> io::Result<usize> {
        let mut s = self.0.lock().unwrap();
        let n = buf.len().min(s.to_host.len());
        for b in buf.iter_mut().take(n) {
            *b = s.to_host.pop_front().unwrap();
        }
        Ok(n)
    }
    fn clear_input(&mut self) -> io::Result<()> {
        Ok(())
    }
}

struct SimOpener(SimLink);

impl Opener for SimOpener {
    fn list(&self) -> Vec<PortInfo> {
        vec![]
    }
    fn open(&self, _port: &str, _baud: u32) -> io::Result<Box<dyn Link>> {
        Ok(Box::new(self.0.clone()))
    }
}

#[test]
fn host_driver_end_to_end_then_host_killed() {
    let link = SimLink(Arc::new(Mutex::new(Shared { c: Controller::new(), now: 0, to_host: VecDeque::new() })));
    let mut cfg = R3xServoConfig::from_profile(&profile());
    cfg.port = Some("/dev/sim".into());
    cfg.force_mock = false;
    cfg.hello_wait = Duration::from_millis(50);
    let mut d = R3xServoDriver::new(cfg, Arc::new(SimOpener(link.clone())));
    d.start(0.0);
    assert!(d.firmware.as_deref().unwrap().starts_with("r3x-servo"));

    let tick = |until: u64, driver: Option<&mut R3xServoDriver>| {
        let mut driver = driver;
        loop {
            let now = {
                let mut s = link.0.lock().unwrap();
                if s.now >= until {
                    break;
                }
                s.now += TICK_US;
                let now = s.now;
                s.c.tick(now, DT);
                let mut out = vec![];
                s.c.poll_telemetry(now, &mut |f: &[u8]| out.extend_from_slice(f));
                s.to_host.extend(out);
                now
            };
            if let Some(d) = driver.as_deref_mut() {
                if now % 10_000 == 0 {
                    d.poll(now as f64 / 1e6);
                }
            }
        }
    };
    tick(1_500_000, Some(&mut d)); // power-up
    d.on_out(&Out::ServoGoal { joint: "head_pan".into(), target: 30.0, v_max: 0.0, a_max: 0.0, j_max: 0.0, seq: 1 }, 1.5);
    tick(4_000_000, Some(&mut d));
    let (_, tm) = d.telemetry.clone().expect("telemetry");
    assert_eq!(tm.flags & (flag::HOLDING | flag::UNCONFIGURED | flag::OVERCURRENT), 0, "flags {:#x}", tm.flags);
    assert!((tm.channels[0].x - 30.0).abs() < 1e-3, "followed the goal: {}", tm.channels[0].x);
    assert_eq!(d.health().status, r3x_contracts::ServiceStatus::Running);
    assert_eq!(d.naks, 0);

    // Host killed mid-move: no more heartbeats.
    d.on_out(&Out::ServoGoal { joint: "head_pan".into(), target: -30.0, v_max: 20.0, a_max: 0.0, j_max: 0.0, seq: 2 }, 4.0);
    tick(4_300_000, Some(&mut d));
    let killed_at = link.0.lock().unwrap().now;
    let last_beat = killed_at - (killed_at % 100_000); // the driver beats every 100 ms
    drop(d);
    let mut held_at = None;
    while held_at.is_none() {
        let next = link.0.lock().unwrap().now + TICK_US;
        tick(next, None);
        let s = link.0.lock().unwrap();
        if s.c.holding(s.now) {
            held_at = Some(s.now);
        }
    }
    let held_at = held_at.unwrap();
    assert!(held_at - last_beat <= HEARTBEAT_TIMEOUT_US + TICK_US, "held {} us after the last heartbeat", held_at - last_beat);
    tick(held_at + 2_000_000, None);
    let s = link.0.lock().unwrap();
    let (x, v) = s.c.state(0);
    assert!(v.abs() < 1e-3 && x > -30.0 + 1.0, "ramped to hold short of the goal: x {x} v {v}");
}
