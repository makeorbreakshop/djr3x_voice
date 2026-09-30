//! Spotify as a *control-only* backend (plan D7): its audio never passes through the host, so
//! ducking is a volume command and crossfades/beat alignment do not apply. Off by default and
//! ported last - this is the interface only; every call reports that it is not ported yet.

use anyhow::{bail, Result};

/// A playback backend r3x controls but does not mix.
pub trait ControlBackend: Send + Sync {
    fn name(&self) -> &'static str;
    /// Search the provider catalogue; `(uri, "Artist - Title")` best first.
    fn search(&self, query: &str, limit: usize) -> Result<Vec<(String, String)>>;
    fn play(&self, uri: &str) -> Result<()>;
    fn stop(&self) -> Result<()>;
    /// 0..1; how ducking reaches a backend the mixer cannot touch.
    fn set_volume(&self, level: f32) -> Result<()>;
}

pub struct Spotify {
    pub client_id: String,
    pub device_name: Option<String>,
}

impl Spotify {
    /// `ENABLE_SPOTIFY=true` plus `SPOTIFY_CLIENT_ID`; otherwise `None` (the default).
    pub fn from_env() -> Option<Self> {
        let on = std::env::var("ENABLE_SPOTIFY").is_ok_and(|v| matches!(v.to_ascii_lowercase().as_str(), "1" | "true" | "yes" | "on"));
        let client_id = std::env::var("SPOTIFY_CLIENT_ID").ok().filter(|v| !v.is_empty())?;
        on.then(|| Self { client_id, device_name: std::env::var("SPOTIFY_DEVICE_NAME").ok() })
    }
}

const NOT_PORTED: &str = "Spotify control is not ported to r3x yet (plan D7: ported last)";

impl ControlBackend for Spotify {
    fn name(&self) -> &'static str {
        "spotify"
    }
    fn search(&self, _: &str, _: usize) -> Result<Vec<(String, String)>> {
        bail!(NOT_PORTED)
    }
    fn play(&self, _: &str) -> Result<()> {
        bail!(NOT_PORTED)
    }
    fn stop(&self) -> Result<()> {
        bail!(NOT_PORTED)
    }
    fn set_volume(&self, _: f32) -> Result<()> {
        bail!(NOT_PORTED)
    }
}
