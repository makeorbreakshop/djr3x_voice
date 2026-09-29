//! PCA9685 16-channel PWM: a dumb sink (D6), host runs the follower. The chip is I2C; a
//! USB host needs a bridge (MCP2221, CH341, FT232H...), so the transport is a trait and no
//! bridge is bundled. The register protocol here is complete; plug a [`RegisterBus`] in.

use std::collections::BTreeMap;

use r3x_contracts::ServiceStatus;
use r3x_performer_core::Frames;

use crate::{Driver, Health};

pub const MODE1: u8 = 0x00;
pub const MODE2: u8 = 0x01;
pub const LED0_ON_L: u8 = 0x06;
pub const PRESCALE: u8 = 0xFE;
const SLEEP: u8 = 0x10;
const AI: u8 = 0x20;
const RESTART: u8 = 0x80;
const OUTDRV: u8 = 0x04;

/// Register writes to one PCA9685 (address fixed by the implementation).
pub trait RegisterBus: Send {
    fn write(&mut self, reg: u8, data: &[u8]) -> std::io::Result<()>;
}

/// Prescale for `hz` (datasheet 7.3.5): round(osc / (4096 * hz)) - 1.
pub fn prescale(osc_hz: f64, hz: f64) -> u8 {
    ((osc_hz / (4096.0 * hz)).round() - 1.0).clamp(3.0, 255.0) as u8
}

/// Wake-up sequence: sleep, set prescale, wake with auto-increment, restart; totem-pole out.
pub fn init_writes(osc_hz: f64, hz: f64) -> Vec<(u8, Vec<u8>)> {
    vec![
        (MODE1, vec![SLEEP]),
        (PRESCALE, vec![prescale(osc_hz, hz)]),
        (MODE1, vec![AI]),
        (MODE2, vec![OUTDRV]),
        (MODE1, vec![AI | RESTART]),
    ]
}

/// One channel's ON/OFF counts: on at 0, off at `counts` (0 = full off).
pub fn channel_write(ch: u8, counts: u16) -> (u8, Vec<u8>) {
    let off = counts.min(4095);
    let full_off = if counts == 0 { 0x10 } else { 0 };
    (LED0_ON_L + 4 * ch, vec![0, 0, (off & 0xFF) as u8, ((off >> 8) as u8) | full_off])
}

pub struct Pca9685Driver {
    bus: Option<Box<dyn RegisterBus>>,
    osc_hz: f64,
    channels: Vec<u8>,
    /// Microseconds per unit of `Frames.servo.targets`.
    us_per_unit: f64,
    enabled: bool,
    sent: BTreeMap<u8, u16>,
    error: Option<String>,
}

impl Pca9685Driver {
    pub fn new(bus: Option<Box<dyn RegisterBus>>, channels: Vec<u8>, us_per_unit: f64) -> Self {
        Pca9685Driver { bus, osc_hz: 25_000_000.0, channels, us_per_unit, enabled: true, sent: BTreeMap::new(), error: None }
    }

    fn write(&mut self, (reg, data): (u8, Vec<u8>)) {
        if let Some(b) = self.bus.as_mut() {
            if let Err(e) = b.write(reg, &data) {
                self.error = Some(e.to_string());
                self.bus = None;
            }
        }
    }

    fn counts(&self, target: i64) -> u16 {
        let hz = self.osc_hz / (4096.0 * (prescale(self.osc_hz, 50.0) as f64 + 1.0));
        let us_per_count = 1e6 / hz / 4096.0;
        (target as f64 * self.us_per_unit / us_per_count).round().clamp(0.0, 4095.0) as u16
    }
}

impl Driver for Pca9685Driver {
    fn name(&self) -> &str {
        "driver.pca9685"
    }
    fn start(&mut self, _now: f64) {
        for w in init_writes(self.osc_hz, 50.0) {
            self.write(w);
        }
    }
    fn set_enabled(&mut self, on: bool, _now: f64) {
        self.enabled = on;
        if on {
            self.sent.clear();
        }
    }
    fn on_frames(&mut self, frames: &Frames, _now: f64) {
        if !self.enabled {
            return;
        }
        for ch in self.channels.clone() {
            let Some(&t) = frames.servo.targets.get(ch as usize) else { continue };
            let c = self.counts(t);
            if self.sent.insert(ch, c) != Some(c) {
                self.write(channel_write(ch, c));
            }
        }
    }
    fn next_poll(&self, now: f64) -> f64 {
        now + 1.0
    }
    fn health(&self) -> Health {
        match (&self.bus, &self.error) {
            (Some(_), _) => Health::running(),
            (None, Some(e)) => Health::new(ServiceStatus::Degraded, format!("mock: write failed: {e}")),
            (None, None) => Health::new(ServiceStatus::Degraded, "mock: no I2C bridge (transport not implemented)".to_string()),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn registers() {
        assert_eq!(prescale(25_000_000.0, 50.0), 121);
        assert_eq!(channel_write(2, 307), (0x0E, vec![0, 0, 0x33, 0x01]));
        assert_eq!(channel_write(0, 0), (0x06, vec![0, 0, 0, 0x10]));
        // 1500 us at the prescale's real ~50.03 Hz is 307 counts.
        let d = Pca9685Driver::new(None, vec![0], 1.0);
        assert_eq!(d.counts(1500), 307);
    }
}
