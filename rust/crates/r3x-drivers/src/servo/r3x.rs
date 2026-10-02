//! `r3x_servo`: the custom motion controller (plan D6, motion-control.md §8). The host sends
//! goals at event time plus a heartbeat; the controller runs the follower, calibration and
//! pulses, and answers with telemetry. Wire format: `PROTOCOL.md`.

use std::collections::BTreeMap;
use std::sync::Arc;
use std::time::{Duration, Instant};

use r3x_contracts::profile::{DriverKind, Unit};
use r3x_contracts::{Event, OpsEvent, RobotProfile, ServoChannelTelemetry, ServiceStatus};
use r3x_performer_core::performer::Out;

use super::proto::{flag, ChannelConfig, Deframer, Msg, Telemetry};
use crate::link::{Backoff, Link, Opener};
use crate::{env_flag, env_str, Driver, Health};

pub const HEARTBEAT_S: f64 = 0.1;
/// No telemetry for this long -> error.
pub const TELEMETRY_STALE_S: f64 = 1.0;

#[derive(Debug, Clone)]
pub struct R3xServoConfig {
    /// `R3X_SERVO_PORT`. Not probed: opening an LED Nano resets it.
    pub port: Option<String>,
    /// `FORCE_MOCK_SERVO`.
    pub force_mock: bool,
    pub baud: u32,
    pub hello_wait: Duration,
    pub retries: u32,
    /// Primary joint -> channel config.
    pub channels: BTreeMap<String, ChannelConfig>,
    /// Actuator name (the stage output) -> channel.
    pub actuators: BTreeMap<String, u8>,
}

impl R3xServoConfig {
    pub fn from_profile(profile: &RobotProfile) -> Self {
        R3xServoConfig {
            port: env_str("R3X_SERVO_PORT"),
            force_mock: env_flag("FORCE_MOCK_SERVO"),
            baud: 115_200,
            hello_wait: Duration::from_secs(1),
            retries: 3,
            channels: channel_configs(profile, DriverKind::R3xServo),
            actuators: profile
                .actuators
                .iter()
                .filter(|a| a.driver == DriverKind::R3xServo)
                .map(|a| (a.name.clone(), a.channel as u8))
                .collect(),
        }
    }
}

/// Channel configs for every actuator on `kind`, keyed by primary joint (the first joint by
/// name, as the performer's pipeline orders them). A joint several servos drive at once
/// (Hunter's visor: `visor_l` and `visor_r`, mirrored by `invert`, each with its own trim) keys
/// its first actuator by the joint and the others `joint@actuator`; a goal for the joint goes to
/// all of them ([`R3xServoConfig::for_joint`]).
pub fn channel_configs(profile: &RobotProfile, kind: DriverKind) -> BTreeMap<String, ChannelConfig> {
    let mut seen = std::collections::HashSet::new();
    profile
        .actuators
        .iter()
        .filter(|a| a.driver == kind)
        .filter_map(|a| {
            let (jname, _) = a.joints.iter().next()?;
            let key = if seen.insert(jname.clone()) { jname.clone() } else { format!("{jname}@{}", a.name) };
            let j = profile.joint(jname)?;
            let c = &a.calibration;
            let cfg = ChannelConfig {
                ch: a.channel as u8,
                center_us: c.center_us as f32,
                center_value: c.center_value as f32,
                trim_us: c.trim_us as f32,
                invert: c.invert,
                gear: c.gear as f32,
                mm_per_deg: if j.unit == Unit::Mm { c.mm_per_deg.unwrap_or(0.0) as f32 } else { 0.0 },
                pulse_min_us: c.pulse_min_us as u16,
                pulse_max_us: c.pulse_max_us as u16,
                range_deg: c.range_deg as f32,
                soft_min: j.soft.min as f32,
                soft_max: j.soft.max as f32,
                v_max: j.v_max as f32,
                a_max: j.a_max as f32,
                j_max: j.j_max as f32,
            };
            Some((key, cfg))
        })
        .collect()
}

impl R3xServoConfig {
    /// Every channel that drives `joint` (one, or a mirrored pair).
    pub fn for_joint<'a>(&'a self, joint: &'a str) -> impl Iterator<Item = &'a ChannelConfig> + 'a {
        self.channels
            .iter()
            .filter(move |(k, _)| k.as_str() == joint || k.strip_prefix(joint).is_some_and(|r| r.starts_with('@')))
            .map(|(_, c)| c)
    }
}

pub struct R3xServoDriver {
    cfg: R3xServoConfig,
    opener: Arc<dyn Opener>,
    link: Option<Box<dyn Link>>,
    mock_reason: String,
    deframer: Deframer,
    seq: u16,
    enabled: bool,
    /// Per-channel enables (bit n = channel n), from the stage outputs.
    mask: u32,
    last_heartbeat: f64,
    connected_at: f64,
    stale: bool,
    pub telemetry: Option<(f64, Telemetry)>,
    pub firmware: Option<String>,
    pub naks: u64,
    backoff: Backoff,
    /// Telemetry waiting to go on the bus, and when the last one went.
    pending: Option<Telemetry>,
    last_published: f64,
}

/// Telemetry reaches the bus at most this often (the controller sends 20-50 Hz).
pub const TELEMETRY_PUBLISH_S: f64 = 0.2;

impl R3xServoDriver {
    pub fn new(cfg: R3xServoConfig, opener: Arc<dyn Opener>) -> Self {
        R3xServoDriver {
            cfg,
            opener,
            link: None,
            mock_reason: "not started".into(),
            deframer: Deframer::default(),
            seq: 0,
            enabled: true,
            mask: u32::MAX,
            last_heartbeat: f64::NEG_INFINITY,
            connected_at: 0.0,
            stale: false,
            telemetry: None,
            firmware: None,
            naks: 0,
            backoff: Backoff::default(),
            pending: None,
            last_published: f64::NEG_INFINITY,
        }
    }

    /// Open the port and say hello; on success configure every channel and beat once.
    fn connect(&mut self, port: &str, now: f64) -> bool {
        for _ in 0..self.cfg.retries {
            if let Some(link) = self.opener.open(port, self.cfg.baud).ok().and_then(|l| self.hello(l)) {
                self.link = Some(link);
                self.connected_at = now;
                self.telemetry = None;
                let cfgs: Vec<_> = self.cfg.channels.values().copied().collect();
                for c in cfgs {
                    self.send(&Msg::Config(c));
                }
                self.beat(now);
                return true;
            }
        }
        self.mock_reason = format!("no controller answered on {port}");
        false
    }

    fn beat(&mut self, now: f64) {
        self.send(&Msg::Heartbeat { outputs_enabled: self.enabled, mask: self.mask });
        self.last_heartbeat = now;
    }

    fn channel_on(&self, ch: u8) -> bool {
        self.enabled && self.mask & (1u32 << ch) != 0
    }

    fn send(&mut self, msg: &Msg) {
        let Some(link) = self.link.as_mut() else { return };
        self.seq = self.seq.wrapping_add(1);
        if let Err(e) = link.write_all(&msg.encode(self.seq)) {
            tracing::warn!("servo controller write failed: {e}");
            self.mock_reason = format!("write failed: {e}");
            self.link = None;
        }
    }

    fn hello(&mut self, mut link: Box<dyn Link>) -> Option<Box<dyn Link>> {
        link.write_all(&Msg::Hello.encode(0)).ok()?;
        let deadline = Instant::now() + self.cfg.hello_wait;
        let mut buf = [0u8; 256];
        while Instant::now() < deadline {
            let n = link.read(&mut buf, deadline.saturating_duration_since(Instant::now())).ok()?;
            for (_, m) in self.deframer.push(&buf[..n]) {
                if let Msg::HelloReply { version, channels, firmware } = m {
                    tracing::info!(version, channels, firmware, "servo controller connected");
                    self.firmware = Some(firmware);
                    return Some(link);
                }
            }
        }
        None
    }

    fn read_telemetry(&mut self, now: f64) {
        let Some(link) = self.link.as_mut() else { return };
        let mut buf = [0u8; 512];
        loop {
            let n = match link.read(&mut buf, Duration::ZERO) {
                Ok(0) => return,
                Ok(n) => n,
                Err(e) => {
                    self.mock_reason = format!("read failed: {e}");
                    self.link = None;
                    return;
                }
            };
            for (_, m) in self.deframer.push(&buf[..n]) {
                match m {
                    Msg::Telemetry(t) => {
                        self.pending = Some(t.clone());
                        self.telemetry = Some((now, t));
                    }
                    Msg::Nak { seq, code } => {
                        self.naks += 1;
                        tracing::warn!(seq, code, "servo controller rejected a message");
                    }
                    _ => {}
                }
            }
        }
    }
}

impl Driver for R3xServoDriver {
    fn name(&self) -> &str {
        "driver.servo"
    }
    fn outputs(&self) -> Vec<String> {
        self.cfg.actuators.keys().cloned().collect()
    }
    fn start(&mut self, now: f64) {
        if self.cfg.force_mock {
            self.mock_reason = "FORCE_MOCK_SERVO".into();
            return;
        }
        let Some(port) = self.cfg.port.clone() else {
            self.mock_reason = "R3X_SERVO_PORT not set".into();
            return;
        };
        if !self.connect(&port, now) {
            self.backoff.failed(now);
        }
    }
    fn set_enabled(&mut self, on: bool, now: f64) {
        if on != self.enabled {
            self.enabled = on;
            // Tell the controller now rather than at the next beat.
            self.beat(now);
        }
    }
    /// Per channel: an actuator whose output is off holds; the rest keep moving.
    fn set_outputs(&mut self, outputs: &BTreeMap<String, bool>, now: f64) {
        let mask = self
            .cfg
            .actuators
            .iter()
            .filter(|(name, _)| outputs.get(*name) != Some(&false))
            .fold(0u32, |m, (_, ch)| m | 1 << ch);
        let on = mask != 0;
        if mask != self.mask || on != self.enabled {
            self.mask = mask;
            self.enabled = on;
            self.beat(now);
        }
    }
    fn on_out(&mut self, out: &Out, _now: f64) {
        let msg = match out {
            Out::ServoGoal { joint, target, v_max, a_max, j_max, .. } => {
                // every servo on the joint (a mirrored pair gets the same goal; each channel's own
                // calibration turns it into its pulse: invert and trim)
                let goals: Vec<Msg> = self
                    .cfg
                    .for_joint(joint)
                    .map(|c| Msg::Goal { ch: c.ch, target: *target as f32, v_max: *v_max as f32, a_max: *a_max as f32, j_max: *j_max as f32 })
                    .collect();
                for m in &goals {
                    if let Msg::Goal { ch, .. } = m {
                        if self.channel_on(*ch) {
                            self.send(m);
                        }
                    }
                }
                return;
            }
            Out::ServoTrim { actuator, trim_us } => {
                // the channel's config again with the new trim: the controller recomputes its pulses
                let Some(&ch) = self.cfg.actuators.get(actuator) else { return };
                let Some(c) = self.cfg.channels.values_mut().find(|c| c.ch == ch) else { return };
                c.trim_us = *trim_us as f32;
                let msg = Msg::Config(*c);
                self.send(&msg);
                return;
            }
            Out::ServoPulse { actuator, us } => {
                let Some(&ch) = self.cfg.actuators.get(actuator) else { return };
                Msg::Direct { ch, us: us.round().clamp(0.0, f64::from(u16::MAX)) as u16 }
            }
            _ => return,
        };
        let (Msg::Goal { ch, .. } | Msg::Direct { ch, .. }) = msg else { return };
        if self.channel_on(ch) {
            self.send(&msg);
        }
    }
    fn poll(&mut self, now: f64) {
        if self.link.is_none() && !self.cfg.force_mock && self.backoff.due(now) {
            if let Some(port) = self.cfg.port.clone() {
                if self.connect(&port, now) {
                    tracing::info!(port, "servo controller reconnected");
                    self.backoff.reset();
                } else {
                    self.backoff.failed(now);
                }
            }
        }
        if now - self.last_heartbeat >= HEARTBEAT_S - 1e-9 {
            self.beat(now);
        }
        self.read_telemetry(now);
        let last = self.telemetry.as_ref().map_or(self.connected_at, |(t, _)| *t);
        self.stale = now - last > TELEMETRY_STALE_S;
    }
    fn next_poll(&self, now: f64) -> f64 {
        if self.link.is_none() {
            return now + 0.5;
        }
        self.last_heartbeat + HEARTBEAT_S
    }
    fn take_event(&mut self) -> Option<Event> {
        let now = self.telemetry.as_ref().map(|(t, _)| *t)?;
        if now - self.last_published < TELEMETRY_PUBLISH_S - 1e-9 {
            return None;
        }
        let t = self.pending.take()?;
        self.last_published = now;
        let channels = self
            .cfg
            .channels
            .iter()
            .filter_map(|(joint, c)| {
                let ch = t.channels.get(usize::from(c.ch))?;
                Some((joint.clone(), ServoChannelTelemetry { us: f64::from(ch.us), x: f64::from(ch.x), flags: ch.flags }))
            })
            .collect();
        Some(Event::Ops(OpsEvent::ServoTelemetry { flags: t.flags, rail_ma: f64::from(t.rail_ma), channels }))
    }
    fn health(&self) -> Health {
        let Some(_) = self.link else {
            let s = if self.cfg.force_mock { ServiceStatus::Running } else { ServiceStatus::Degraded };
            return Health::new(s, format!("mock: {}", self.mock_reason));
        };
        if self.stale {
            return Health::new(ServiceStatus::Error, "no telemetry".to_string());
        }
        match &self.telemetry {
            Some((_, t)) if t.flags & flag::OVERCURRENT != 0 => Health::new(ServiceStatus::Error, "overcurrent".to_string()),
            Some((_, t)) if t.flags & flag::HOLDING != 0 && self.enabled => {
                Health::new(ServiceStatus::Degraded, "controller holding (heartbeat lost)".to_string())
            }
            Some((_, t)) if t.flags & flag::UNCONFIGURED != 0 => Health::new(ServiceStatus::Degraded, "unconfigured".to_string()),
            _ => Health::new(ServiceStatus::Running, self.firmware.clone()),
        }
    }
    fn stop(&mut self, now: f64) {
        self.set_enabled(false, now);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::link::fake::{FakeLink, FakeOpener};

    fn profile() -> RobotProfile {
        RobotProfile::load(concat!(env!("CARGO_MANIFEST_DIR"), "/../../../profiles/r3x/robot.json")).unwrap()
    }

    fn hello_reply() -> Vec<u8> {
        Msg::HelloReply { version: 1, channels: 18, firmware: "fake".into() }.encode(0)
    }

    fn start(answer: bool) -> (R3xServoDriver, Arc<FakeOpener>) {
        let o = Arc::new(FakeOpener::default().board("/dev/servo", move || {
            FakeLink::new("", move |d| if answer && d.get(1) == Some(&super::super::proto::t::HELLO) { hello_reply() } else { vec![] })
        }));
        let mut cfg = R3xServoConfig::from_profile(&profile());
        cfg.port = Some("/dev/servo".into());
        cfg.force_mock = false;
        cfg.hello_wait = Duration::from_millis(20);
        let mut d = R3xServoDriver::new(cfg, o.clone());
        d.start(0.0);
        (d, o)
    }

    fn sent(o: &FakeOpener) -> Vec<Msg> {
        let mut d = Deframer::default();
        d.push(&o.last("/dev/servo").unwrap().bytes()).into_iter().map(|(_, m)| m).collect()
    }

    #[test]
    fn connect_sends_config_per_channel_then_heartbeat() {
        let (d, o) = start(true);
        let msgs = sent(&o);
        assert_eq!(msgs[0], Msg::Hello);
        let configs = msgs.iter().filter(|m| matches!(m, Msg::Config(_))).count();
        assert_eq!(configs, d.cfg.channels.len());
        assert!(configs > 10);
        assert_eq!(msgs.last(), Some(&Msg::Heartbeat { outputs_enabled: true, mask: u32::MAX }));
    }

    #[test]
    fn head_roll_rides_the_spare_pio_channel() {
        let cfg = R3xServoConfig::from_profile(&profile());
        let roll = &cfg.channels["head_roll"];
        assert_eq!((roll.ch, roll.gear, roll.soft_min, roll.soft_max), (17, 2.4, -10.0, 10.0));
        assert_eq!(cfg.actuators["headroll"], 17);
    }

    /// The Physical rig's visor (`robot.generated.json`): one joint, two channels, mirrored by
    /// `invert`, each with its own trim; one goal reaches both.
    #[test]
    fn the_visor_goal_reaches_both_mirrored_servos() {
        let path = concat!(env!("CARGO_MANIFEST_DIR"), "/../../../profiles/r3x/robot.generated.json");
        let mut p = RobotProfile::load(path).unwrap();
        for (n, t) in [("visor_l", 8.0), ("visor_r", -5.0)] {
            p.actuators.iter_mut().find(|a| a.name == n).unwrap().calibration.trim_us = t;
        }
        let cfg = R3xServoConfig::from_profile(&p);
        let v: Vec<_> = cfg.for_joint("visor").copied().collect();
        assert_eq!(v.len(), 2);
        assert_eq!((v[0].ch, v[1].ch), (cfg.actuators["visor_l"], cfg.actuators["visor_r"]));
        assert_ne!(v[0].invert, v[1].invert, "mirrored");
        assert_eq!((v[0].trim_us, v[1].trim_us), (8.0, -5.0));
        // the controller turns one goal into equal and opposite pulses about centre + trim
        let cal = |c: &ChannelConfig| r3x_motion::Calibration {
            center_us: f64::from(c.center_us), center_value: f64::from(c.center_value), trim_us: f64::from(c.trim_us),
            invert: c.invert, gear: f64::from(c.gear), mm_per_deg: None, pulse_min_us: f64::from(c.pulse_min_us),
            pulse_max_us: f64::from(c.pulse_max_us), range_deg: f64::from(c.range_deg),
        };
        let (l, r) = (cal(&v[0]), cal(&v[1]));
        for x in [-15.0, 0.0, 12.0, 30.0] {
            let dl = l.value_to_us(x) - (l.center_us + 8.0);
            let dr = r.value_to_us(x) - (r.center_us - 5.0);
            assert!((dl + dr).abs() < 1e-6, "{x}: {dl} {dr}");
        }
        assert!(cfg.for_joint("visor_l").next().is_none() && cfg.for_joint("vis").next().is_none());
    }

    #[test]
    fn silent_controller_fails_open_after_retries() {
        let (d, o) = start(false);
        assert_eq!(o.open_count("/dev/servo"), 3);
        assert_eq!(d.health().status, ServiceStatus::Degraded);
    }

    #[test]
    fn heartbeat_cadence_goals_and_disable() {
        let (mut d, o) = start(true);
        o.last("/dev/servo").unwrap().clear();
        for i in 1..=100 {
            d.poll(i as f64 * 0.01); // 1 s at 100 Hz
        }
        let (joint, ch) = d.cfg.channels.iter().next().map(|(j, c)| (j.clone(), c.ch)).unwrap();
        let goal = |target| Out::ServoGoal { joint: joint.clone(), target, v_max: 1.0, a_max: 2.0, j_max: 3.0, seq: 1 };
        d.on_out(&goal(5.0), 1.0);
        d.set_enabled(false, 1.0);
        d.on_out(&goal(6.0), 1.0); // dropped while disabled
        let msgs = sent(&o);
        let beats = msgs.iter().filter(|m| matches!(m, Msg::Heartbeat { outputs_enabled: true, .. })).count();
        assert_eq!(beats, 10);
        assert_eq!(
            msgs[msgs.len() - 2..],
            [Msg::Goal { ch, target: 5.0, v_max: 1.0, a_max: 2.0, j_max: 3.0 }, Msg::Heartbeat { outputs_enabled: false, mask: u32::MAX }]
        );
    }

    /// A Bench trim goes to the controller as the channel's CONFIG with the new trim_us: the
    /// controller's pulses (the same `r3x_motion::Calibration`) move by exactly the trim.
    #[test]
    fn a_trim_reconfigures_the_channel_and_shifts_its_pulses() {
        let (mut d, o) = start(true);
        o.last("/dev/servo").unwrap().clear();
        let ch = d.cfg.actuators["neck"];
        d.on_out(&Out::ServoTrim { actuator: "neck".into(), trim_us: 12.0 }, 0.5);
        let msgs = sent(&o);
        let Some(Msg::Config(c)) = msgs.last() else { panic!("no config: {msgs:?}") };
        assert_eq!((c.ch, c.trim_us), (ch, 12.0));
        let cal = |trim: f32| r3x_motion::Calibration {
            center_us: f64::from(c.center_us), center_value: f64::from(c.center_value), trim_us: f64::from(trim),
            invert: c.invert, gear: f64::from(c.gear), mm_per_deg: None, pulse_min_us: f64::from(c.pulse_min_us),
            pulse_max_us: f64::from(c.pulse_max_us), range_deg: f64::from(c.range_deg),
        };
        for v in [-20.0, 0.0, 15.0] {
            assert!((cal(12.0).value_to_us(v) - cal(0.0).value_to_us(v) - 12.0).abs() < 1e-9);
        }
        // kept for the next connect, and an unknown actuator sends nothing
        assert_eq!(d.cfg.channels.values().find(|x| x.ch == ch).unwrap().trim_us, 12.0);
        let n = sent(&o).len();
        d.on_out(&Out::ServoTrim { actuator: "nope".into(), trim_us: 1.0 }, 0.6);
        assert_eq!(sent(&o).len(), n);
    }

    #[test]
    fn telemetry_drives_health() {
        let (mut d, o) = start(true);
        let wire = o.last("/dev/servo").unwrap();
        d.poll(0.5);
        assert_eq!(d.health().status, ServiceStatus::Running);
        d.poll(1.5);
        assert_eq!(d.health().status, ServiceStatus::Error, "stale");
        wire.push(&Msg::Telemetry(Telemetry { flags: flag::HOLDING, ..Default::default() }).encode(3));
        d.poll(1.6);
        assert_eq!(d.health().status, ServiceStatus::Degraded);
    }

    #[test]
    fn telemetry_reaches_the_bus_rate_limited_by_joint() {
        use super::super::proto::ChannelTelemetry;
        let (mut d, o) = start(true);
        let wire = o.last("/dev/servo").unwrap();
        let (joint, ch) = d.cfg.channels.iter().next().map(|(j, c)| (j.clone(), c.ch)).unwrap();
        let mut chans = vec![ChannelTelemetry::default(); 18];
        chans[usize::from(ch)] = ChannelTelemetry { us: 1600, x: 4.5, flags: 0 };
        let tm = |seq| Msg::Telemetry(Telemetry { flags: 0, rail_ma: 250, last_seq: seq, channels: chans.clone() }).encode(seq);
        wire.push(&tm(1));
        d.poll(0.05);
        let Some(Event::Ops(OpsEvent::ServoTelemetry { rail_ma, channels, .. })) = d.take_event() else { panic!("no telemetry") };
        assert_eq!(rail_ma, 250.0);
        assert_eq!(channels[&joint], ServoChannelTelemetry { us: 1600.0, x: 4.5, flags: 0 });
        wire.push(&tm(2));
        d.poll(0.10);
        assert!(d.take_event().is_none(), "5 Hz at most");
        d.poll(0.30);
        assert!(d.take_event().is_none(), "nothing new since");
    }

    #[test]
    fn lost_controller_reconnects() {
        let (mut d, o) = start(true);
        o.last("/dev/servo").unwrap().0.lock().unwrap().fail_writes = true;
        d.poll(0.2); // heartbeat write fails
        assert!(d.link.is_none());
        d.poll(0.3);
        assert!(d.link.is_some());
        assert_eq!(o.open_count("/dev/servo"), 2);
    }

    #[test]
    fn stage_outputs_gate_per_channel_and_jog_goes_direct() {
        let (mut d, o) = start(true);
        o.last("/dev/servo").unwrap().clear();
        assert!(d.outputs().contains(&"neck".to_string()), "outputs are actuator names");
        // Bench: everything off but the head lift (channel 1).
        let outputs: BTreeMap<String, bool> = d.outputs().into_iter().map(|a| (a.clone(), a == "headlift")).collect();
        d.set_outputs(&outputs, 0.0);
        let pan = Out::ServoGoal { joint: "head_pan".into(), target: 5.0, v_max: 0.0, a_max: 0.0, j_max: 0.0, seq: 1 };
        d.on_out(&pan, 0.0); // neck is off: dropped
        d.on_out(&Out::ServoPulse { actuator: "headlift".into(), us: 1612.4 }, 0.0);
        d.on_out(&Out::ServoPulse { actuator: "neck".into(), us: 1600.0 }, 0.0); // off: dropped
        assert_eq!(sent(&o), [Msg::Heartbeat { outputs_enabled: true, mask: 0b10 }, Msg::Direct { ch: 1, us: 1612 }]);
        let all_off: BTreeMap<String, bool> = d.outputs().into_iter().map(|a| (a, false)).collect();
        d.set_outputs(&all_off, 0.0);
        assert_eq!(sent(&o).last(), Some(&Msg::Heartbeat { outputs_enabled: false, mask: 0 }));
    }
}
