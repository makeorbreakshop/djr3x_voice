//! R3X voice I/O (plan §6: deepgram_direct_mic, mouse_input, elevenlabs -> here).
//!
//! - [`deepgram`]: streaming STT with reconnect, 20 ms audio, `Finalize` on stop.
//! - [`eleven`]: ElevenLabs dialogue-socket TTS with the HTTP fallback rule.
//! - [`speaker`]: the speech FIFO (first-audible-sample timing, mouth, timings, dup-drop).
//! - [`voice`]: turns and push-to-talk ownership; [`voice::spawn_bus_adapter`] publishes.
//! - [`remote`]: gateway audio frames (browser hold-to-talk and playback).
//! - [`cantina`]: bridge mode, CantinaOS as the brain.
//! - [`commentary`]: the DJ commentary cache, played through the FIFO (mouth + timings).
//! - [`fixture`]: replayed TTS for `R3X_FIXTURES=replay` (STT is then [`deepgram::Stt::scripted`]).
//! - [`start`]: build the whole stack from [`config::VoiceSettings`].

pub mod cantina;
pub mod commentary;
pub mod config;
pub mod deepgram;
pub mod eleven;
pub mod fixture;
#[cfg(feature = "mouse")]
pub mod mouse;
pub mod remote;
pub mod speaker;
pub mod voice;

use std::sync::Arc;

use r3x_audio::sink::{RemoteSink, SpeechSink, TeeSink};
use r3x_bus::Bus;

pub use config::VoiceSettings;
pub use voice::{Voice, VoiceEvent, VoiceOptions};

/// A running voice stack. Keep it alive for as long as voice should run.
pub struct VoiceStack {
    pub voice: Voice,
    /// Speech for gateway clients; see [`remote::hooks`].
    pub remote_sink: RemoteSink,
    /// The TTS backend behind the FIFO, with the commentary cache in front
    /// ([`commentary::spawn`]).
    pub tts: Arc<commentary::CachingTts>,
    /// Replay only: what the scripted STT hears next.
    pub script: Option<deepgram::SttScript>,
    #[cfg(feature = "device")]
    pub output: Option<r3x_audio::device::OutputEngine>,
}

/// Build STT, TTS, sinks and the turn manager. STT connects while `state.engagement` is
/// INTERACTIVE. Speech plays locally (if `local_audio`, or on the null device) and is
/// mirrored to remote clients.
pub fn start(bus: &Bus, s: VoiceSettings) -> anyhow::Result<VoiceStack> {
    let (stt, script, backend): (_, _, Arc<dyn eleven::TtsBackend>) = match &s.replay {
        Some((dir, pace)) => {
            let (stt, script) = deepgram::Stt::scripted();
            (stt, Some(script), Arc::new(fixture::FixtureTts::load(dir, *pace)?))
        }
        None => (deepgram::Stt::spawn(s.deepgram.clone(), voice::interactive(bus)), None, eleven::Eleven::start(s.eleven.clone())),
    };
    let tts = commentary::CachingTts::new(backend);
    let remote_sink = RemoteSink::new(s.client_buffer);
    #[cfg(feature = "device")]
    let (output, sink, mic): (_, Arc<dyn SpeechSink>, Option<Arc<dyn voice::MicOpener>>) = if s.local_audio || s.null_audio.is_some() {
        let (engine, mic): (_, Arc<dyn voice::MicOpener>) = match s.null_audio {
            Some(speed) => (r3x_audio::device::OutputEngine::null(speed), Arc::new(voice::SilentMic)),
            None => (r3x_audio::device::OutputEngine::start(s.output_device.as_deref())?, Arc::new(voice::DeviceMic(s.mic_device.clone()))),
        };
        let local = r3x_audio::sink::LocalSink::new(&engine.mixer);
        let tee = TeeSink::new(Box::new(local), vec![Box::new(remote_sink.clone())]);
        (Some(engine), Arc::new(tee), Some(mic))
    } else {
        (None, Arc::new(remote_sink.clone()), None)
    };
    #[cfg(not(feature = "device"))]
    let (sink, mic): (Arc<dyn SpeechSink>, Option<Arc<dyn voice::MicOpener>>) = {
        let _ = TeeSink::new; // local audio needs the `device` feature
        (Arc::new(remote_sink.clone()), None)
    };
    let speaker = speaker::Speaker::spawn(tts.clone(), sink, s.mouth_hz);
    let voice = Voice::new(bus.clone(), stt, speaker, mic, VoiceOptions::default());
    Ok(VoiceStack {
        voice,
        remote_sink,
        tts,
        script,
        #[cfg(feature = "device")]
        output,
    })
}
