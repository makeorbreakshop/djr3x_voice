//! The mixer: named buses, each a set of sources under one ramped gain.
//!
//! [`Mixer::render`] runs on the device callback: it never blocks and never allocates in the
//! steady state. Control arrives through a lock-free queue from [`MixerHandle`].
//!
//! Phase 4 adds music by handing a decoder [`Source`] to [`MixerHandle::add`] on
//! [`BusId::Music`]; ducking already ramps that bus.

use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use crate::ramp::{ms_to_frames, GainRamp};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum BusId {
    Speech,
    Music,
    Sfx,
}

impl BusId {
    pub const ALL: [BusId; 3] = [BusId::Speech, BusId::Music, BusId::Sfx];

    fn index(self) -> usize {
        self as usize
    }
}

/// When the buffer being rendered will be heard.
#[derive(Debug, Clone, Copy)]
pub struct RenderTime {
    /// When the callback ran.
    pub at: Instant,
    /// Device-reported delay from `at` until the buffer's first frame is audible.
    pub latency: Duration,
}

impl RenderTime {
    pub fn now() -> Self {
        Self { at: Instant::now(), latency: Duration::ZERO }
    }
}

/// Something that produces audio at the mixer's rate and channel count.
pub trait Source: Send {
    /// Add up to `out.len() / channels` interleaved frames into `out` (which already holds
    /// other sources' audio). Return `false` once finished; the source is then dropped.
    fn mix(&mut self, out: &mut [f32], channels: usize, when: &RenderTime) -> bool;
}

pub enum MixerCommand {
    Add(BusId, Box<dyn Source>),
    Gain { bus: BusId, gain: f32, ramp_frames: u32 },
    Clear(BusId),
}

struct Bus {
    gain: GainRamp,
    sources: Vec<Box<dyn Source>>,
}

pub struct Mixer {
    channels: usize,
    buses: [Bus; 3],
    commands: rtrb::Consumer<MixerCommand>,
    scratch: Vec<f32>,
}

impl Mixer {
    pub fn channels(&self) -> usize {
        self.channels
    }

    /// Fill `out` (interleaved, `channels` wide). Called once per device buffer.
    pub fn render(&mut self, out: &mut [f32], when: &RenderTime) {
        while let Ok(cmd) = self.commands.pop() {
            match cmd {
                MixerCommand::Add(bus, src) => self.buses[bus.index()].sources.push(src),
                MixerCommand::Gain { bus, gain, ramp_frames } => self.buses[bus.index()].gain.set(gain, ramp_frames),
                MixerCommand::Clear(bus) => self.buses[bus.index()].sources.clear(),
            }
        }
        out.fill(0.0);
        if self.scratch.len() < out.len() {
            self.scratch.resize(out.len(), 0.0); // only when the device grows its buffer
        }
        let ch = self.channels;
        for bus in &mut self.buses {
            if bus.sources.is_empty() {
                // Keep the ramp moving so a duck issued while silent has landed by the time
                // music starts.
                for _ in 0..out.len() / ch {
                    bus.gain.advance();
                }
                continue;
            }
            let scratch = &mut self.scratch[..out.len()];
            scratch.fill(0.0);
            bus.sources.retain_mut(|s| s.mix(scratch, ch, when));
            for (frame_out, frame_in) in out.chunks_mut(ch).zip(scratch.chunks(ch)) {
                let g = bus.gain.advance();
                for (o, i) in frame_out.iter_mut().zip(frame_in) {
                    *o += i * g;
                }
            }
        }
        for s in out.iter_mut() {
            *s = s.clamp(-1.0, 1.0);
        }
    }
}

/// Control side of a [`Mixer`]. Cheap to clone.
#[derive(Clone)]
pub struct MixerHandle {
    tx: Arc<Mutex<rtrb::Producer<MixerCommand>>>,
    sample_rate: u32,
    channels: usize,
}

impl MixerHandle {
    pub fn sample_rate(&self) -> u32 {
        self.sample_rate
    }

    pub fn channels(&self) -> usize {
        self.channels
    }

    pub fn send(&self, cmd: MixerCommand) -> bool {
        self.tx.lock().unwrap().push(cmd).is_ok()
    }

    pub fn add(&self, bus: BusId, src: Box<dyn Source>) -> bool {
        self.send(MixerCommand::Add(bus, src))
    }

    pub fn set_gain(&self, bus: BusId, gain: f32, ramp_ms: f64) -> bool {
        self.send(MixerCommand::Gain { bus, gain, ramp_frames: ms_to_frames(ramp_ms, self.sample_rate) })
    }

    /// Duck music to `level` (profile `audio.ducking`).
    pub fn duck(&self, level: f32, ramp_ms: f64) -> bool {
        self.set_gain(BusId::Music, level, ramp_ms)
    }

    pub fn unduck(&self, ramp_ms: f64) -> bool {
        self.set_gain(BusId::Music, 1.0, ramp_ms)
    }
}

pub fn mixer(sample_rate: u32, channels: usize) -> (Mixer, MixerHandle) {
    let (tx, rx) = rtrb::RingBuffer::new(256);
    let bus = || Bus { gain: GainRamp::new(1.0), sources: Vec::new() };
    let m = Mixer { channels, buses: [bus(), bus(), bus()], commands: rx, scratch: vec![0.0; 4096] };
    (m, MixerHandle { tx: Arc::new(Mutex::new(tx)), sample_rate, channels })
}

#[cfg(test)]
mod tests {
    use super::*;

    struct Dc(f32, usize);
    impl Source for Dc {
        fn mix(&mut self, out: &mut [f32], ch: usize, _: &RenderTime) -> bool {
            let n = (out.len() / ch).min(self.1);
            for s in &mut out[..n * ch] {
                *s += self.0;
            }
            self.1 -= n;
            self.1 > 0
        }
    }

    #[test]
    fn buses_mix_and_duck_ramps() {
        let (mut m, h) = mixer(1000, 2);
        h.add(BusId::Music, Box::new(Dc(0.4, usize::MAX)));
        h.add(BusId::Speech, Box::new(Dc(0.2, 10)));
        let mut out = vec![0.0; 40]; // 20 frames
        m.render(&mut out, &RenderTime::now());
        assert!((out[0] - 0.6).abs() < 1e-6 && (out[1] - 0.6).abs() < 1e-6);
        assert!((out[20] - 0.4).abs() < 1e-6, "speech source ended after 10 frames");

        h.duck(0.5, 80.0); // 80 frames at 1 kHz
        let mut out = vec![0.0; 160];
        m.render(&mut out, &RenderTime::now());
        assert!(out[0] > 0.39, "ramped, not stepped: {}", out[0]);
        assert!((out[158] - 0.2).abs() < 1e-3, "lands on 0.5 × 0.4: {}", out[158]);
        assert_eq!(m.buses[BusId::Speech.index()].sources.len(), 0);
    }
}
