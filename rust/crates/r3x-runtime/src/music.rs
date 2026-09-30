//! `--music rust|cantina` (plan §9 Phase 4). `cantina` (the default until acceptance) leaves
//! music to CantinaOS's MusicController. `rust`: the r3x music engine plays on the host mixer
//! (the voice's output device when `--voice`, else its own), publishes `state.music` with the
//! beat-clock anchor, serves the brain's music requests and the commentary cache, and plays
//! sfx. In bridge mode run CantinaOS with `R3X_EXTERNAL_MUSIC=1` (its MusicController and
//! mode-change sound stay down) and the bridge speaks MusicController's topics over the tap.

use std::str::FromStr;
use std::sync::Arc;

use r3x_audio::mixer::MixerHandle;
use r3x_bus::Bus;
use r3x_contracts::{RobotProfile, ServiceStatus};
use r3x_music::commentary::{FixtureSynth, SynthFuture, Synthesize};

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub enum MusicMode {
    #[default]
    Cantina,
    Rust,
}

impl FromStr for MusicMode {
    type Err = String;
    fn from_str(s: &str) -> Result<Self, String> {
        match s.to_ascii_lowercase().as_str() {
            "cantina" | "" => Ok(MusicMode::Cantina),
            "rust" | "r3x" => Ok(MusicMode::Rust),
            o => Err(format!("--music {o}: expected rust or cantina")),
        }
    }
}

impl MusicMode {
    /// `R3X_MUSIC` (`--music` sets it).
    pub fn from_env() -> Self {
        std::env::var("R3X_MUSIC").ok().and_then(|v| v.parse().ok()).unwrap_or_default()
    }
}

/// Keeps the music side alive (its own output device when the voice has none).
pub struct MusicStack {
    pub engine: r3x_music::Engine,
    pub mixer: MixerHandle,
    _output: Option<r3x_audio::device::OutputEngine>,
}

/// Start the engine, its bus service, sfx and (`commentary`, when there is no voice to do it
/// with mouth and timings) the mixer-level commentary cache. `mixer` = the voice's output
/// mixer when there is one; otherwise its own device, or the null device (`R3X_AUDIO=null`).
/// `choices`: a replay's recorded picks.
pub async fn start(
    bus: &Bus,
    mixer: Option<MixerHandle>,
    profile: Option<&RobotProfile>,
    commentary: bool,
    choices: Option<crate::replay::Choices>,
) -> anyhow::Result<MusicStack> {
    let (mixer, output) = match mixer {
        Some(m) => (m, None),
        None if std::env::var("R3X_AUDIO").is_ok_and(|a| a == "null") => {
            let speed = std::env::var("R3X_NULL_AUDIO_SPEED").ok().and_then(|s| s.parse().ok()).unwrap_or(1.0);
            let out = r3x_audio::device::OutputEngine::null(speed);
            (out.mixer.clone(), Some(out))
        }
        None => {
            let dev = profile.and_then(|p| p.audio.outputs.first()).and_then(|o| o.device.clone());
            let out = r3x_audio::device::OutputEngine::start(dev.as_deref())?;
            (out.mixer.clone(), Some(out))
        }
    };

    let mut settings = r3x_music::MusicSettings::from_env();
    if let Some(p) = profile {
        settings.engine.duck_level = p.audio.ducking.level as f32;
        settings.engine.duck_ramp_ms = p.audio.ducking.ramp_ms;
    }
    let engine = r3x_music::start(mixer.clone(), settings).await;
    if let Some(c) = choices {
        engine.set_picker(c.music_picker());
    }
    r3x_music::service::spawn(bus, &engine);

    let bank = Arc::new(r3x_audio::sfx::SfxBank::new(mixer.clone(), r3x_music::service::default_sfx_dirs()));
    r3x_music::service::spawn_sfx(bus, bank, Some(r3x_music::service::default_mode_sound()));

    // Commentary without a voice (bridge mode): replayed fixtures (R3X_FIXTURES=replay, no
    // paid calls) or nothing, in which case cache requests fail fast and the DJ falls back to
    // a plain fade.
    if commentary {
        let replay = std::env::var("R3X_FIXTURES").is_ok_and(|v| v == "replay");
        let synth: Arc<dyn Synthesize> = match (replay, std::env::var_os("R3X_FIXTURE_DIR")) {
            (true, Some(dir)) => match FixtureSynth::load(std::path::Path::new(&dir)) {
                Ok(f) => Arc::new(f),
                Err(e) => {
                    tracing::warn!("commentary fixtures: {e}");
                    Arc::new(NoSynth)
                }
            },
            _ => Arc::new(NoSynth),
        };
        let cache = Arc::new(r3x_audio::speech_cache::SpeechCache::new(mixer.clone()));
        r3x_music::commentary::spawn(bus, cache, synth);
    }
    r3x_ops::report(bus, "music", ServiceStatus::Running, None);
    Ok(MusicStack {
        engine,
        mixer,
        _output: output,
    })
}

struct NoSynth;

impl Synthesize for NoSynth {
    fn synthesize<'a>(&'a self, _: &'a str) -> SynthFuture<'a> {
        Box::pin(async { Err(anyhow::anyhow!("no TTS for commentary (the voice caches it; or R3X_FIXTURES=replay)")) })
    }
}
