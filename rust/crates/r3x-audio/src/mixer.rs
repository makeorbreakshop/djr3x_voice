//! The mixer: named buses, each a set of sources under one ramped gain.
//!
//! [`Mixer::render`] runs on the device callback: it never blocks and never allocates in the
//! steady state. Control arrives through a lock-free queue from [`MixerHandle`].
//!
//! Music is a decoder [`Source`] on [`BusId::Music`] (see [`crate::music`]); ducking ramps
//! that bus's gain. Each source can also carry an equal-power envelope, so a crossfade is one
//! command and both sides of it start on the same frame ([`MixerCommand::Crossfade`]).

use std::sync::atomic::{AtomicU32, AtomicU64, Ordering};
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
    /// Equal-power crossfade, sample-accurate: every source on `bus` fades out over `frames`
    /// (then is dropped) while `src` fades in over the same frames, starting on the same frame.
    Crossfade { bus: BusId, src: Box<dyn Source>, frames: u32 },
    /// Fade every source on `bus` out over `frames`, then drop them (a click-free stop).
    FadeOut { bus: BusId, frames: u32 },
}

#[derive(Debug, Clone, Copy, PartialEq)]
enum Shape {
    /// sin(t·π/2): 0 -> 1
    In,
    /// cos(t·π/2): 1 -> 0, then the source is dropped
    Out,
}

#[derive(Debug, Clone, Copy)]
struct Envelope {
    shape: Shape,
    pos: u32,
    len: u32,
}

impl Envelope {
    fn new(shape: Shape, len: u32) -> Self {
        Self { shape, pos: 0, len: len.max(1) }
    }

    #[inline]
    fn advance(&mut self) -> f32 {
        let t = self.pos as f32 / self.len as f32;
        self.pos = (self.pos + 1).min(self.len);
        let a = t * std::f32::consts::FRAC_PI_2;
        match self.shape {
            Shape::In => a.sin(),
            Shape::Out => a.cos(),
        }
    }

    fn done(&self) -> bool {
        self.pos >= self.len
    }
}

struct Voice {
    src: Box<dyn Source>,
    env: Option<Envelope>,
}

struct Bus {
    gain: GainRamp,
    sources: Vec<Voice>,
}

/// Per-bus output level, written by the render callback (post-gain, pre-clip).
#[derive(Default)]
pub struct Meters {
    rms_bits: [AtomicU32; 3],
    audible: [AtomicU64; 3],
}

/// A bus's last-buffer RMS and how many buffers so far were audible (RMS > -80 dBFS).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct BusLevel {
    pub rms: f32,
    pub audible_buffers: u64,
}

impl Meters {
    fn record(&self, bus: usize, rms: f32) {
        self.rms_bits[bus].store(rms.to_bits(), Ordering::Relaxed);
        if rms > 1e-4 {
            self.audible[bus].fetch_add(1, Ordering::Relaxed);
        }
    }

    pub fn level(&self, bus: BusId) -> BusLevel {
        BusLevel {
            rms: f32::from_bits(self.rms_bits[bus.index()].load(Ordering::Relaxed)),
            audible_buffers: self.audible[bus.index()].load(Ordering::Relaxed),
        }
    }
}

pub struct Mixer {
    channels: usize,
    meters: Arc<Meters>,
    buses: [Bus; 3],
    commands: rtrb::Consumer<MixerCommand>,
    scratch: Vec<f32>,
    voice_buf: Vec<f32>,
}

impl Mixer {
    pub fn channels(&self) -> usize {
        self.channels
    }

    /// Fill `out` (interleaved, `channels` wide). Called once per device buffer.
    pub fn render(&mut self, out: &mut [f32], when: &RenderTime) {
        while let Ok(cmd) = self.commands.pop() {
            match cmd {
                MixerCommand::Add(bus, src) => self.buses[bus.index()].sources.push(Voice { src, env: None }),
                MixerCommand::Gain { bus, gain, ramp_frames } => self.buses[bus.index()].gain.set(gain, ramp_frames),
                MixerCommand::Clear(bus) => self.buses[bus.index()].sources.clear(),
                MixerCommand::Crossfade { bus, src, frames } => {
                    let b = &mut self.buses[bus.index()];
                    fade_out_all(b, frames);
                    b.sources.push(Voice { src, env: Some(Envelope::new(Shape::In, frames)) });
                }
                MixerCommand::FadeOut { bus, frames } => fade_out_all(&mut self.buses[bus.index()], frames),
            }
        }
        out.fill(0.0);
        if self.scratch.len() < out.len() {
            self.scratch.resize(out.len(), 0.0); // only when the device grows its buffer
            self.voice_buf.resize(out.len(), 0.0);
        }
        let ch = self.channels;
        for (bi, bus) in self.buses.iter_mut().enumerate() {
            if bus.sources.is_empty() {
                self.meters.record(bi, 0.0);
                // Keep the ramp moving so a duck issued while silent has landed by the time
                // music starts.
                for _ in 0..out.len() / ch {
                    bus.gain.advance();
                }
                continue;
            }
            let scratch = &mut self.scratch[..out.len()];
            scratch.fill(0.0);
            let vbuf = &mut self.voice_buf[..out.len()];
            bus.sources.retain_mut(|v| match &mut v.env {
                None => v.src.mix(scratch, ch, when),
                Some(env) => {
                    vbuf.fill(0.0);
                    let alive = v.src.mix(vbuf, ch, when);
                    for (o, i) in scratch.chunks_mut(ch).zip(vbuf.chunks(ch)) {
                        let g = env.advance();
                        for (o, i) in o.iter_mut().zip(i) {
                            *o += i * g;
                        }
                    }
                    let faded_out = env.shape == Shape::Out && env.done();
                    if env.shape == Shape::In && env.done() {
                        v.env = None;
                    }
                    alive && !faded_out
                }
            });
            let mut energy = 0.0f32;
            for (frame_out, frame_in) in out.chunks_mut(ch).zip(scratch.chunks(ch)) {
                let g = bus.gain.advance();
                for (o, i) in frame_out.iter_mut().zip(frame_in) {
                    let v = i * g;
                    energy += v * v;
                    *o += v;
                }
            }
            self.meters.record(bi, (energy / out.len().max(1) as f32).sqrt());
        }
        for s in out.iter_mut() {
            *s = s.clamp(-1.0, 1.0);
        }
    }
}

fn fade_out_all(bus: &mut Bus, frames: u32) {
    for v in &mut bus.sources {
        match v.env {
            Some(Envelope { shape: Shape::Out, .. }) => {} // already leaving
            Some(Envelope { shape: Shape::In, pos, len }) => {
                // Mid fade-in: leave from the current level, not from full
                // (sin(t·π/2) = cos((1-t)·π/2)).
                let t = pos as f32 / len as f32;
                let start = ((1.0 - t.clamp(0.0, 1.0)) * frames as f32) as u32;
                v.env = Some(Envelope { shape: Shape::Out, pos: start.min(frames), len: frames.max(1) });
            }
            None => v.env = Some(Envelope::new(Shape::Out, frames)),
        }
    }
}

/// Control side of a [`Mixer`]. Cheap to clone.
#[derive(Clone)]
pub struct MixerHandle {
    tx: Arc<Mutex<rtrb::Producer<MixerCommand>>>,
    meters: Arc<Meters>,
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

    /// Output level per bus (e.g. to verify sfx are audible without a listener).
    pub fn level(&self, bus: BusId) -> BusLevel {
        self.meters.level(bus)
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

    /// Equal-power crossfade on `bus` to `src` over `secs`.
    pub fn crossfade(&self, bus: BusId, src: Box<dyn Source>, secs: f64) -> bool {
        self.send(MixerCommand::Crossfade { bus, src, frames: ms_to_frames(secs * 1000.0, self.sample_rate) })
    }

    /// Fade everything on `bus` out over `ms`, then drop it.
    pub fn fade_out(&self, bus: BusId, ms: f64) -> bool {
        self.send(MixerCommand::FadeOut { bus, frames: ms_to_frames(ms, self.sample_rate) })
    }
}

pub fn mixer(sample_rate: u32, channels: usize) -> (Mixer, MixerHandle) {
    let (tx, rx) = rtrb::RingBuffer::new(256);
    let bus = || Bus { gain: GainRamp::new(1.0), sources: Vec::new() };
    let meters = Arc::new(Meters::default());
    let m = Mixer { channels, meters: meters.clone(), buses: [bus(), bus(), bus()], commands: rx, scratch: vec![0.0; 4096], voice_buf: vec![0.0; 4096] };
    (m, MixerHandle { tx: Arc::new(Mutex::new(tx)), meters, sample_rate, channels })
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
        assert!((h.level(BusId::Music).rms - 0.4).abs() < 1e-5);
        assert!((h.level(BusId::Speech).rms - 0.2 / 2f32.sqrt()).abs() < 1e-5, "half the buffer");

        h.duck(0.5, 80.0); // 80 frames at 1 kHz
        let mut out = vec![0.0; 160];
        m.render(&mut out, &RenderTime::now());
        assert!(out[0] > 0.39, "ramped, not stepped: {}", out[0]);
        assert!((out[158] - 0.2).abs() < 1e-3, "lands on 0.5 × 0.4: {}", out[158]);
        assert_eq!(m.buses[BusId::Speech.index()].sources.len(), 0);
        assert_eq!(h.level(BusId::Speech).rms, 0.0, "silent bus meters zero");
        assert_eq!(h.level(BusId::Speech).audible_buffers, 1);
        assert_eq!(h.level(BusId::Sfx).audible_buffers, 0);
    }
}
