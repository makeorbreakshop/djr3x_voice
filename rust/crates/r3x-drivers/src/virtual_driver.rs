//! Virtual driver: the performer's frames onto the bus frames channel at 50 Hz (plan D6:
//! frames exist for the sim, this driver and logs only). The gateway forwards them to
//! clients that asked (`telemetry.frames`).

use r3x_bus::Bus;
use r3x_performer_core::Frames;

use crate::{Driver, Health};

pub const FRAMES_HZ: f64 = 50.0;

pub struct VirtualDriver {
    sink: Box<dyn FnMut(r3x_contracts::Frames) + Send>,
    enabled: bool,
    last: f64,
    pub published: u64,
}

impl VirtualDriver {
    pub fn new(sink: impl FnMut(r3x_contracts::Frames) + Send + 'static) -> Self {
        VirtualDriver { sink: Box::new(sink), enabled: true, last: f64::NEG_INFINITY, published: 0 }
    }

    pub fn to_bus(bus: Bus) -> Self {
        Self::new(move |f| bus.publish_frames(f))
    }
}

impl Driver for VirtualDriver {
    fn name(&self) -> &str {
        "driver.virtual"
    }
    fn set_enabled(&mut self, on: bool, _now: f64) {
        self.enabled = on;
    }
    fn on_frames(&mut self, frames: &Frames, now: f64) {
        // 20% slack so a 50 Hz performer is never decimated by tick jitter.
        if self.enabled && now - self.last >= 0.8 / FRAMES_HZ {
            self.last = now;
            self.published += 1;
            (self.sink)(frames.to_contract());
        }
    }
    fn next_poll(&self, now: f64) -> f64 {
        now + 1.0
    }
    fn health(&self) -> Health {
        Health::running()
    }
}
