//! Pololu Maestro: a dumb sink (D6). The host runs the performer's follower and streams its
//! controller frame here at 50 Hz, send-on-change per channel.
//!
//! Serial commands (quarter-microsecond targets, 7-bit data bytes):
//! - Set Target `0x84 ch lo hi`; Set Multiple Targets `0x9F n first (lo hi)*n` (Mini Maestro
//!   12/18/24 only); Set Speed `0x87`, Set Acceleration `0x89`; Get Errors `0xA1` -> 2 bytes.
//! - Pololu protocol (daisy-chained): `0xAA device (cmd & 0x7F) ...`.
//!
//! Disabled = stop streaming: the Maestro holds the last pulse (it has no heartbeat; set its
//! own serial timeout in the Maestro Control Center if a host crash must go limp).

use std::collections::BTreeMap;
use std::sync::Arc;
use std::time::{Duration, Instant};

use r3x_contracts::profile::DriverKind;
use r3x_contracts::{RobotProfile, ServiceStatus};
use r3x_performer_core::Frames;

use crate::link::{Link, Opener};
use crate::{env_flag, env_str, Driver, Health};

pub const SET_TARGET: u8 = 0x84;
pub const SET_MULTIPLE: u8 = 0x9F;
pub const SET_SPEED: u8 = 0x87;
pub const SET_ACCEL: u8 = 0x89;
pub const GET_ERRORS: u8 = 0xA1;

/// Wraps a compact command for the Pololu protocol when `device` is set.
pub fn frame(device: Option<u8>, cmd: &[u8]) -> Vec<u8> {
    match device {
        None => cmd.to_vec(),
        Some(d) => {
            let mut v = vec![0xAA, d & 0x7F, cmd[0] & 0x7F];
            v.extend_from_slice(&cmd[1..]);
            v
        }
    }
}

fn lohi(v: u16) -> [u8; 2] {
    [(v & 0x7F) as u8, ((v >> 7) & 0x7F) as u8]
}

pub fn set_target(ch: u8, quarter_us: u16) -> Vec<u8> {
    let [lo, hi] = lohi(quarter_us);
    vec![SET_TARGET, ch, lo, hi]
}

pub fn set_multiple(first: u8, quarter_us: &[u16]) -> Vec<u8> {
    let mut v = vec![SET_MULTIPLE, quarter_us.len() as u8, first];
    for q in quarter_us {
        v.extend_from_slice(&lohi(*q));
    }
    v
}

/// Speed in 0.25 us / 10 ms, acceleration in 0.25 us / 10 ms / 80 ms; 0 = unlimited.
pub fn set_speed(ch: u8, speed: u16) -> Vec<u8> {
    let [lo, hi] = lohi(speed);
    vec![SET_SPEED, ch, lo, hi]
}

pub fn set_accel(ch: u8, accel: u16) -> Vec<u8> {
    let [lo, hi] = lohi(accel);
    vec![SET_ACCEL, ch, lo, hi]
}

#[derive(Debug, Clone)]
pub struct MaestroConfig {
    /// `MAESTRO_SERIAL_PORT` (the Maestro's "Command Port").
    pub port: Option<String>,
    pub force_mock: bool,
    pub baud: u32,
    /// Pololu-protocol device number; None = compact protocol.
    pub device: Option<u8>,
    /// Controller channels this sink drives (profile actuators on `maestro`).
    pub channels: Vec<u8>,
    /// Microseconds per unit of the performer's `Frames.servo.targets`.
    pub us_per_unit: f64,
    /// Use Set Multiple Targets for contiguous runs (Mini Maestro only).
    pub multi: bool,
}

impl MaestroConfig {
    pub fn from_profile(profile: &RobotProfile, us_per_unit: f64) -> Self {
        let mut channels: Vec<u8> =
            profile.actuators.iter().filter(|a| a.driver == DriverKind::Maestro).map(|a| a.channel as u8).collect();
        channels.sort_unstable();
        channels.dedup();
        MaestroConfig {
            port: env_str("MAESTRO_SERIAL_PORT"),
            force_mock: env_flag("FORCE_MOCK_SERVO"),
            baud: 115_200,
            device: None,
            channels,
            us_per_unit,
            multi: true,
        }
    }
}

pub struct MaestroDriver {
    cfg: MaestroConfig,
    opener: Arc<dyn Opener>,
    link: Option<Box<dyn Link>>,
    mock_reason: String,
    enabled: bool,
    sent: BTreeMap<u8, u16>,
    last_frame: u64,
}

impl MaestroDriver {
    pub fn new(cfg: MaestroConfig, opener: Arc<dyn Opener>) -> Self {
        MaestroDriver {
            cfg,
            opener,
            link: None,
            mock_reason: "not started".into(),
            enabled: true,
            sent: BTreeMap::new(),
            last_frame: 0,
        }
    }

    fn write(&mut self, cmd: &[u8]) {
        let bytes = frame(self.cfg.device, cmd);
        if let Some(l) = self.link.as_mut() {
            if let Err(e) = l.write_all(&bytes) {
                self.mock_reason = format!("write failed: {e}");
                self.link = None;
            }
        }
    }

    /// Commands for the channels whose target changed (contiguous runs batched when `multi`).
    pub fn commands(&mut self, targets: &[i64]) -> Vec<Vec<u8>> {
        let q = |t: i64| ((t as f64 * self.cfg.us_per_unit * 4.0).round().clamp(0.0, 16383.0)) as u16;
        let changed: Vec<(u8, u16)> = self
            .cfg
            .channels
            .iter()
            .filter_map(|&ch| targets.get(ch as usize).map(|&t| (ch, q(t))))
            .filter(|(ch, v)| self.sent.get(ch) != Some(v))
            .collect();
        let mut out = Vec::new();
        let mut i = 0;
        while i < changed.len() {
            let mut j = i + 1;
            while self.cfg.multi && j < changed.len() && changed[j].0 == changed[j - 1].0 + 1 {
                j += 1;
            }
            let run = &changed[i..j];
            out.push(if run.len() == 1 {
                set_target(run[0].0, run[0].1)
            } else {
                set_multiple(run[0].0, &run.iter().map(|r| r.1).collect::<Vec<_>>())
            });
            i = j;
        }
        for (ch, v) in changed {
            self.sent.insert(ch, v);
        }
        out
    }
}

impl Driver for MaestroDriver {
    fn name(&self) -> &str {
        "driver.maestro"
    }
    fn start(&mut self, _now: f64) {
        if self.cfg.force_mock {
            self.mock_reason = "FORCE_MOCK_SERVO".into();
            return;
        }
        let Some(port) = self.cfg.port.clone() else {
            self.mock_reason = "MAESTRO_SERIAL_PORT not set".into();
            return;
        };
        for _ in 0..3 {
            let Ok(mut link) = self.opener.open(&port, self.cfg.baud) else { continue };
            // Get Errors answers two bytes: proof something Maestro-shaped is listening.
            if link.write_all(&frame(self.cfg.device, &[GET_ERRORS])).is_err() {
                continue;
            }
            let (mut buf, mut got) = ([0u8; 2], 0);
            let deadline = Instant::now() + Duration::from_millis(500);
            while got < 2 && Instant::now() < deadline {
                got += link.read(&mut buf[got..], Duration::from_millis(50)).unwrap_or(0);
            }
            if got == 2 {
                let errors = u16::from_le_bytes(buf);
                if errors != 0 {
                    tracing::warn!(errors, "maestro reports errors");
                }
                self.link = Some(link);
                return;
            }
        }
        self.mock_reason = format!("no Maestro answered on {port}");
    }
    fn set_enabled(&mut self, on: bool, _now: f64) {
        self.enabled = on;
        if on {
            self.sent.clear(); // re-assert every channel
        }
    }
    fn on_frames(&mut self, frames: &Frames, _now: f64) {
        if !self.enabled || frames.servo.frame == self.last_frame {
            return;
        }
        self.last_frame = frames.servo.frame;
        for c in self.commands(&frames.servo.targets) {
            self.write(&c);
        }
    }
    fn next_poll(&self, now: f64) -> f64 {
        now + 1.0
    }
    fn health(&self) -> Health {
        match self.link {
            Some(_) => Health::running(),
            None if self.cfg.force_mock => Health::new(ServiceStatus::Running, format!("mock: {}", self.mock_reason)),
            None => Health::new(ServiceStatus::Degraded, format!("mock: {}", self.mock_reason)),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::link::fake::FakeOpener;

    #[test]
    fn byte_encoding_matches_the_pololu_manual() {
        // Manual example: channel 0 to 1500 us (6000 quarter-us) -> 0x84 0x00 0x70 0x2E.
        assert_eq!(set_target(0, 6000), [0x84, 0x00, 0x70, 0x2E]);
        assert_eq!(frame(Some(12), &set_target(0, 6000)), [0xAA, 0x0C, 0x04, 0x00, 0x70, 0x2E]);
        assert_eq!(set_multiple(3, &[6000, 4000]), [0x9F, 2, 3, 0x70, 0x2E, 0x20, 0x1F]);
        assert_eq!(set_speed(1, 140), [0x87, 1, 0x0C, 0x01]);
        assert_eq!(set_accel(1, 4), [0x89, 1, 4, 0]);
    }

    #[test]
    fn streams_changed_channels_in_quarter_us() {
        let cfg = MaestroConfig {
            port: None,
            force_mock: false,
            baud: 115_200,
            device: None,
            channels: vec![0, 1, 2, 5],
            us_per_unit: 1.0,
            multi: true,
        };
        let mut d = MaestroDriver::new(cfg, Arc::new(FakeOpener::default()));
        let t = [1500, 1500, 1600, 0, 0, 1000];
        assert_eq!(d.commands(&t), vec![set_multiple(0, &[6000, 6000, 6400]), set_target(5, 4000)]);
        assert!(d.commands(&t).is_empty(), "send on change");
        assert_eq!(d.commands(&[1500, 1501, 1600, 0, 0, 1000]), vec![set_target(1, 6004)]);
    }
}
