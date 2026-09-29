//! `r3x_servo`: the custom motion controller (plan D6, motion-control.md §8). The host sends
//! goals at event time plus a heartbeat; the controller runs the follower, calibration and
//! pulses, and answers with telemetry. Wire format: `PROTOCOL.md`.

use std::collections::BTreeMap;
use std::sync::Arc;
use std::time::{Duration, Instant};

use r3x_contracts::profile::{DriverKind, Unit};
use r3x_contracts::{RobotProfile, ServiceStatus};
use r3x_performer_core::performer::Out;

use super::proto::{flag, ChannelConfig, Deframer, Msg, Telemetry};
use crate::link::{Link, Opener};
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
        }
    }
}

/// Channel configs for every actuator on `kind`, keyed by primary joint (the first joint by
/// name, as the performer's pipeline orders them).
pub fn channel_configs(profile: &RobotProfile, kind: DriverKind) -> BTreeMap<String, ChannelConfig> {
    profile
        .actuators
        .iter()
        .filter(|a| a.driver == kind)
        .filter_map(|a| {
            let (jname, _) = a.joints.iter().next()?;
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
            Some((jname.clone(), cfg))
        })
        .collect()
}

pub struct R3xServoDriver {
    cfg: R3xServoConfig,
    opener: Arc<dyn Opener>,
    link: Option<Box<dyn Link>>,
    mock_reason: String,
    deframer: Deframer,
    seq: u16,
    enabled: bool,
    last_heartbeat: f64,
    connected_at: f64,
    stale: bool,
    pub telemetry: Option<(f64, Telemetry)>,
    pub firmware: Option<String>,
    pub naks: u64,
}

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
            last_heartbeat: f64::NEG_INFINITY,
            connected_at: 0.0,
            stale: false,
            telemetry: None,
            firmware: None,
            naks: 0,
        }
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
                    Msg::Telemetry(t) => self.telemetry = Some((now, t)),
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
        self.cfg.channels.keys().cloned().collect()
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
        for _ in 0..self.cfg.retries {
            if let Some(link) = self.opener.open(&port, self.cfg.baud).ok().and_then(|l| self.hello(l)) {
                self.link = Some(link);
                self.connected_at = now;
                let cfgs: Vec<_> = self.cfg.channels.values().copied().collect();
                for c in cfgs {
                    self.send(&Msg::Config(c));
                }
                self.send(&Msg::Heartbeat { outputs_enabled: self.enabled });
                self.last_heartbeat = now;
                return;
            }
        }
        self.mock_reason = format!("no controller answered on {port}");
    }
    fn set_enabled(&mut self, on: bool, now: f64) {
        if on != self.enabled {
            self.enabled = on;
            // Tell the controller now rather than at the next beat.
            self.send(&Msg::Heartbeat { outputs_enabled: on });
            self.last_heartbeat = now;
        }
    }
    fn on_out(&mut self, out: &Out, _now: f64) {
        let Out::ServoGoal { joint, target, v_max, a_max, j_max, .. } = out else { return };
        if !self.enabled {
            return;
        }
        if let Some(c) = self.cfg.channels.get(joint) {
            let msg = Msg::Goal { ch: c.ch, target: *target as f32, v_max: *v_max as f32, a_max: *a_max as f32, j_max: *j_max as f32 };
            self.send(&msg);
        }
    }
    fn poll(&mut self, now: f64) {
        if now - self.last_heartbeat >= HEARTBEAT_S - 1e-9 {
            self.last_heartbeat = now;
            self.send(&Msg::Heartbeat { outputs_enabled: self.enabled });
        }
        self.read_telemetry(now);
        let last = self.telemetry.as_ref().map_or(self.connected_at, |(t, _)| *t);
        self.stale = now - last > TELEMETRY_STALE_S;
    }
    fn next_poll(&self, _now: f64) -> f64 {
        self.last_heartbeat + HEARTBEAT_S
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
        assert_eq!(msgs.last(), Some(&Msg::Heartbeat { outputs_enabled: true }));
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
        let beats = msgs.iter().filter(|m| matches!(m, Msg::Heartbeat { outputs_enabled: true })).count();
        assert_eq!(beats, 10);
        assert_eq!(
            msgs[msgs.len() - 2..],
            [Msg::Goal { ch, target: 5.0, v_max: 1.0, a_max: 2.0, j_max: 3.0 }, Msg::Heartbeat { outputs_enabled: false }]
        );
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
}
