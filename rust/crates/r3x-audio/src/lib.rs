//! R3X audio engine (plan D7, §3b, §7b).
//!
//! - [`mixer`]: named buses (`speech`, `music`, `sfx`) each with a ramped gain; ducking is a
//!   ramp on the music bus (default 80 ms); per-source equal-power envelopes make a crossfade
//!   one sample-accurate command. The speech bus is fed by a [`sink::LocalSink`].
//! - [`decode`] (feature `decode`): symphonia + rubato, planar mono/stereo at any rate.
//! - [`music`] (feature `decode`): a streamed file on the music bus with a playback position.
//! - [`sfx`] (feature `decode`): one-shot clips on the sfx bus, kit-id resolution.
//! - [`speech_cache`]: DJ commentary held as PCM and played on the speech bus on cue.
//! - [`sink`]: where speech goes. [`sink::LocalSink`] plays through the mixer on a device and
//!   reports exactly when each line becomes audible (device output latency included);
//!   [`sink::RemoteSink`] paces 24 kHz PCM to a gateway client.
//! - [`amplitude`]: fixed 20 ms analysis windows with the CantinaOS AGC rule, and the single
//!   mouth rate (profile, default 30 Hz).
//! - [`resample`]: small streaming resampler (device rate <-> 16/24 kHz).
//! - [`device`] (feature `device`): cpal output engine and 16 kHz / 20 ms mic capture.

pub mod amplitude;
#[cfg(feature = "decode")]
pub mod decode;
#[cfg(feature = "device")]
pub mod device;
pub mod mixer;
#[cfg(feature = "decode")]
pub mod music;
pub mod ramp;
pub mod resample;
#[cfg(feature = "decode")]
pub mod sfx;
pub mod sink;
pub mod speech_cache;

/// Speech-to-text capture rate.
pub const MIC_RATE: u32 = 16_000;
/// Mic chunk and amplitude window length.
pub const CHUNK_MS: u32 = 20;
/// Samples per 20 ms mic chunk at 16 kHz.
pub const MIC_CHUNK: usize = (MIC_RATE * CHUNK_MS / 1000) as usize;
/// ElevenLabs `pcm_24000`, and the remote-client TTS rate.
pub const TTS_RATE: u32 = 24_000;
/// Default mouth amplitude rate (plan §7b: one rate, in the profile).
pub const DEFAULT_MOUTH_HZ: f64 = 30.0;

pub fn i16_to_f32(s: i16) -> f32 {
    s as f32 / 32768.0
}

pub fn f32_to_i16(s: f32) -> i16 {
    (s.clamp(-1.0, 1.0) * 32767.0).round() as i16
}
