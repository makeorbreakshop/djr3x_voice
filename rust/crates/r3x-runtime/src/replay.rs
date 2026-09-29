//! `R3X_FIXTURES=replay`: the recorded choice points (`choice.jsonl`: the engine's random
//! track, the DJ's next track), so a replay makes the same picks as the recording. A key
//! whose recorded picks run out falls back to the first option.

use std::collections::{HashMap, VecDeque};
use std::path::Path;
use std::sync::{Arc, Mutex};

#[derive(Clone, Default)]
pub struct Choices(Arc<Mutex<HashMap<String, VecDeque<String>>>>);

impl Choices {
    /// From `R3X_FIXTURES=replay` + `R3X_FIXTURE_DIR`; `None` when not replaying.
    pub fn from_env() -> Option<Self> {
        let dir = std::env::var_os("R3X_FIXTURE_DIR").filter(|_| std::env::var("R3X_FIXTURES").is_ok_and(|m| m == "replay"))?;
        Some(Self::load(Path::new(&dir)))
    }

    pub fn load(dir: &Path) -> Self {
        let mut picks: HashMap<String, VecDeque<String>> = HashMap::new();
        for l in std::fs::read_to_string(dir.join("choice.jsonl")).unwrap_or_default().lines() {
            let Ok(v) = serde_json::from_str::<serde_json::Value>(l) else { continue };
            if let (Some(k), Some(p)) = (v["key"].as_str(), v["picked"].as_str()) {
                picks.entry(k.into()).or_default().push_back(p.into());
            }
        }
        Self(Arc::new(Mutex::new(picks)))
    }

    pub fn pick(&self, key: &str, options: &[String]) -> Option<String> {
        self.0.lock().unwrap().get_mut(key).and_then(VecDeque::pop_front).or_else(|| options.first().cloned())
    }

    pub fn chooser(&self) -> r3x_brain::Chooser {
        let c = self.clone();
        Arc::new(move |key, options: &[String]| c.pick(key, options))
    }

    /// The engine's pick for a play that names nothing (`music.random_track`).
    pub fn music_picker(&self) -> r3x_music::engine::Picker {
        let c = self.clone();
        Arc::new(move |options: &[String]| c.pick("music.random_track", options))
    }
}
