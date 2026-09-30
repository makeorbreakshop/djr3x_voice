//! Settings from the process environment (binaries load the repo-root `.env` into it at
//! startup: `r3x_contracts::dotenv::load`).

use std::time::Duration;

use crate::deepgram::DeepgramConfig;
use crate::eleven::ElevenConfig;

#[derive(Debug, Clone)]
pub struct VoiceSettings {
    pub deepgram: DeepgramConfig,
    pub eleven: ElevenConfig,
    /// Play speech on a local device and open the local mic.
    pub local_audio: bool,
    /// Device name substrings; `None` = system default.
    pub mic_device: Option<String>,
    pub output_device: Option<String>,
    pub mouth_hz: f64,
    /// How far ahead of playback remote clients are sent audio.
    pub client_buffer: Duration,
    /// `R3X_FIXTURES=replay` + `R3X_FIXTURE_DIR`: TTS replays the recorded audio and STT is
    /// scripted (no Deepgram/ElevenLabs, no keys needed).
    pub replay: Option<(std::path::PathBuf, f64)>,
    /// `--audio null` (`R3X_AUDIO=null`): no device; the mixer renders into nothing at this
    /// speed (1.0 = real time) and the mic is silence.
    pub null_audio: Option<f64>,
}

impl VoiceSettings {
    pub fn from_env() -> anyhow::Result<Self> {
        let get = |k: &str| std::env::var(k).ok().filter(|v| !v.trim().is_empty());
        let flag = |k: &str, d: bool| get(k).map_or(d, |v| !matches!(v.to_ascii_lowercase().as_str(), "0" | "false" | "no" | "off"));
        let replay = std::env::var("R3X_FIXTURES")
            .is_ok_and(|m| m == "replay")
            .then(|| std::env::var_os("R3X_FIXTURE_DIR"))
            .flatten()
            .map(|d| (d.into(), std::env::var("R3X_FIXTURE_PACE").ok().and_then(|p| p.parse().ok()).unwrap_or(1.0)));
        let key = |k: &str| match (get(k), &replay) {
            (Some(v), _) => Ok(v),
            (None, Some(_)) => Ok("replay".to_owned()),
            (None, None) => Err(anyhow::anyhow!("{k} is not set")),
        };
        let deepgram = DeepgramConfig::new(key("DEEPGRAM_API_KEY")?);
        let mut eleven = ElevenConfig::new(key("ELEVENLABS_API_KEY")?);
        if let Some(v) = get("ELEVENLABS_VOICE_ID") {
            eleven.voice_id = v;
        }
        if let Some(m) = get("ELEVENLABS_MODEL_ID") {
            eleven.model_id = m;
        }
        eleven.dialogue_socket = flag("ELEVENLABS_DIALOGUE_SOCKET", true);
        Ok(Self {
            deepgram,
            eleven,
            local_audio: flag("R3X_LOCAL_AUDIO", true),
            mic_device: get("R3X_MIC_DEVICE"),
            output_device: get("R3X_AUDIO_OUTPUT"),
            mouth_hz: r3x_audio::DEFAULT_MOUTH_HZ,
            client_buffer: Duration::from_millis(150),
            replay,
            null_audio: get("R3X_AUDIO")
                .filter(|a| a == "null")
                .map(|_| get("R3X_NULL_AUDIO_SPEED").and_then(|s| s.parse().ok()).unwrap_or(1.0)),
        })
    }
}
