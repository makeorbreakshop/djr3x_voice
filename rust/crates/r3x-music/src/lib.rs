//! `r3x-music`: the music side of the runtime (plan §6 music_controller, Phase 4).
//!
//! - [`library`]: scan (`MUSIC_DIR`), artist/title from filenames, duplicate-title keys,
//!   named-track matching (difflib-exact).
//! - [`engine`]: the playback actor on `r3x-audio`'s music bus: play/stop/pause, `next`
//!   (music owns it, DJ or not), sample-accurate equal-power crossfades, ramped ducking,
//!   `TRACK_ENDING_SOON` from the playback position.
//! - [`semantic`]: CLAP search through `ort` (feature `clap`), "next" walks the ranking.
//! - [`service`]: the engine on the r3x bus (state with the beat-clock anchor, events, the
//!   brain's requests) and sfx playback.
//! - [`commentary`]: the DJ commentary cache on the bus (synthesis injected; fixture replay).
//! - [`cantina`]: CantinaOS's MusicController bus language over the tap (`--music rust`).
//! - [`spotify`]: control-only backend interface (not ported yet).
//!
//! Beats come from `r3x-beats` (cache + low-priority background analysis): [`start`] wires
//! them in.

pub mod cantina;
pub mod commentary;
pub mod engine;
pub mod library;
pub mod semantic;
pub mod service;
pub mod spotify;

use std::path::PathBuf;

use r3x_audio::mixer::MixerHandle;

pub use engine::{Engine, EngineConfig, EngineEvent, Status};
pub use library::{LibTrack, Library};

#[derive(Debug, Clone)]
pub struct MusicSettings {
    pub music_dir: PathBuf,
    pub engine: EngineConfig,
    /// Analyse tempo for tracks the cache does not know (`ENABLE_BEAT_ANALYSIS`, default on).
    pub beat_analysis: bool,
    /// Load CLAP and build the semantic index in the background (`R3X_SEMANTIC`, default on).
    pub semantic: bool,
}

impl MusicSettings {
    pub fn from_env() -> Self {
        let off = |k: &str| std::env::var(k).is_ok_and(|v| matches!(v.to_ascii_lowercase().as_str(), "0" | "false" | "no" | "off"));
        Self { music_dir: library::default_music_dir(), engine: EngineConfig::default(), beat_analysis: !off("ENABLE_BEAT_ANALYSIS"), semantic: !off("R3X_SEMANTIC") }
    }
}

/// Scan the library (blocking), attach cached beats, start the engine, and kick off beat
/// analysis and the semantic index in the background. Fail-open throughout.
pub async fn start(mixer: MixerHandle, s: MusicSettings) -> Engine {
    let dir = s.music_dir.clone();
    let cache = r3x_beats::BeatCache::new(r3x_beats::default_cache_dir());
    let (library, cache) = tokio::task::spawn_blocking(move || {
        let mut lib = Library::scan(&dir, true);
        for t in &mut lib.tracks {
            if let Some(b) = cache.get(&t.path) {
                t.bpm = Some(b.bpm);
                t.first_beat_s = Some(b.first_beat_s);
            }
        }
        (lib, cache)
    })
    .await
    .expect("library scan panicked");
    tracing::info!(tracks = library.len(), dir = %s.music_dir.display(), "music library loaded");
    let paths: Vec<PathBuf> = library.tracks.iter().map(|t| t.path.clone()).collect();
    let keyed: Vec<(String, PathBuf)> = library.tracks.iter().map(|t| (t.key.clone(), t.path.clone())).collect();
    let engine = Engine::spawn(mixer, library, s.engine.clone());
    if s.beat_analysis {
        let rx = r3x_beats::Background::spawn(cache, paths);
        let e = engine.clone();
        tokio::task::spawn_blocking(move || {
            for (p, info) in rx {
                e.beats(p, info);
            }
        });
    }
    if s.semantic {
        spawn_semantic(&engine, keyed);
    }
    engine
}

#[cfg(feature = "clap")]
fn spawn_semantic(engine: &Engine, tracks: Vec<(String, PathBuf)>) {
    let e = engine.clone();
    tokio::task::spawn_blocking(move || {
        let dir = semantic::default_model_dir();
        let built = semantic::Clap::load(&dir).and_then(|clap| {
            let index = clap.build_index(&tracks, &dir.join("index-r3x.json"))?;
            let _ = clap.embed_text("warm upbeat music")?; // pay kernel setup now, not mid-turn
            Ok(semantic::ClapSearch { clap, index })
        });
        match built {
            Ok(s) => e.set_search(std::sync::Arc::new(s)),
            Err(err) => tracing::warn!("semantic music search unavailable ({}): {err:#}", dir.display()),
        }
    });
}

#[cfg(not(feature = "clap"))]
fn spawn_semantic(_: &Engine, _: Vec<(String, PathBuf)>) {
    tracing::info!("semantic music search not built (feature `clap`)");
}
