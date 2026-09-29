//! Settings from the environment, with the repo-root `.env` as a fallback (never overriding
//! a variable that is already set).

use std::collections::HashMap;
use std::path::Path;
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
}

/// `KEY=VALUE` lines; quotes stripped; `#` comments skipped.
pub fn read_dotenv(path: &Path) -> HashMap<String, String> {
    let Ok(text) = std::fs::read_to_string(path) else { return HashMap::new() };
    text.lines()
        .filter_map(|l| {
            let l = l.trim();
            if l.starts_with('#') {
                return None;
            }
            let (k, v) = l.strip_prefix("export ").unwrap_or(l).split_once('=')?;
            let v = v.trim().trim_matches('"').trim_matches('\'');
            Some((k.trim().to_owned(), v.to_owned()))
        })
        .collect()
}

/// The repo-root `.env` this crate was built in.
pub fn repo_dotenv() -> std::path::PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../.env")
}

impl VoiceSettings {
    pub fn from_env() -> anyhow::Result<Self> {
        let file = read_dotenv(&repo_dotenv());
        let get = |k: &str| std::env::var(k).ok().or_else(|| file.get(k).cloned()).filter(|v| !v.trim().is_empty());
        let flag = |k: &str, d: bool| get(k).map_or(d, |v| !matches!(v.to_ascii_lowercase().as_str(), "0" | "false" | "no" | "off"));
        let deepgram = DeepgramConfig::new(get("DEEPGRAM_API_KEY").ok_or_else(|| anyhow::anyhow!("DEEPGRAM_API_KEY is not set"))?);
        let mut eleven = ElevenConfig::new(get("ELEVENLABS_API_KEY").ok_or_else(|| anyhow::anyhow!("ELEVENLABS_API_KEY is not set"))?);
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
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dotenv_parsing() {
        let dir = std::env::temp_dir().join(format!("r3x-dotenv-{}", std::process::id()));
        std::fs::write(&dir, "# c\nA=1\nexport B=\"two\"\n\nC='x=y'\n").unwrap();
        let m = read_dotenv(&dir);
        std::fs::remove_file(&dir).ok();
        assert_eq!((m["A"].as_str(), m["B"].as_str(), m["C"].as_str()), ("1", "two", "x=y"));
    }
}
