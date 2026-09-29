//! One-shot sound effects on the mixer's sfx bus: show `sfx` cues (the kit in
//! `sim/web/public/sfx`) and the mode-change ding. Plan §7b: SFX used to play only in the
//! browser sim; now the host plays them, so they are audible with no sim open.
//!
//! An id matches a file stem ignoring case, spaces and underscores (`"air_horn"` ->
//! `Air Horn.mp3`), the same rule as the sim's loader. Decoded clips are cached.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use anyhow::{anyhow, Result};

use crate::decode::{decode_file, interleave_for_device};
use crate::mixer::{BusId, MixerHandle, RenderTime, Source};

/// A decoded clip, interleaved in the mixer's layout.
pub struct Clip {
    data: Arc<Vec<f32>>,
    pos: usize,
    gain: f32,
}

impl Source for Clip {
    fn mix(&mut self, out: &mut [f32], _: usize, _: &RenderTime) -> bool {
        let n = out.len().min(self.data.len() - self.pos);
        for (o, s) in out[..n].iter_mut().zip(&self.data[self.pos..self.pos + n]) {
            *o += s * self.gain;
        }
        self.pos += n;
        self.pos < self.data.len()
    }
}

pub fn normalise_id(s: &str) -> String {
    s.chars().filter(|c| !c.is_whitespace() && *c != '_').flat_map(char::to_lowercase).collect()
}

pub struct SfxBank {
    dirs: Vec<PathBuf>,
    mixer: MixerHandle,
    cache: Mutex<HashMap<PathBuf, Arc<Vec<f32>>>>,
}

impl SfxBank {
    pub fn new(mixer: MixerHandle, dirs: Vec<PathBuf>) -> Self {
        Self { dirs, mixer, cache: Mutex::default() }
    }

    pub fn dirs(&self) -> &[PathBuf] {
        &self.dirs
    }

    /// The file for `id`: an existing path, else a stem match in the kit folders.
    pub fn resolve(&self, id: &str) -> Option<PathBuf> {
        let p = Path::new(id);
        if p.is_absolute() && p.is_file() {
            return Some(p.to_owned());
        }
        let want = normalise_id(Path::new(id).file_stem().and_then(|s| s.to_str()).unwrap_or(id));
        for d in &self.dirs {
            let Ok(rd) = std::fs::read_dir(d) else { continue };
            let mut hits: Vec<PathBuf> = rd
                .filter_map(|e| e.ok().map(|e| e.path()))
                .filter(|p| {
                    let audio = p.extension().and_then(|e| e.to_str()).is_some_and(|e| matches!(e.to_ascii_lowercase().as_str(), "mp3" | "wav" | "m4a" | "ogg" | "flac"));
                    audio && p.file_stem().and_then(|s| s.to_str()).is_some_and(|s| normalise_id(s) == want)
                })
                .collect();
            hits.sort();
            if let Some(h) = hits.into_iter().next() {
                return Some(h);
            }
        }
        None
    }

    fn clip(&self, path: &Path) -> Result<Arc<Vec<f32>>> {
        if let Some(c) = self.cache.lock().unwrap().get(path) {
            return Ok(c.clone());
        }
        let planar = decode_file(path, self.mixer.sample_rate(), 2)?;
        let mut inter = Vec::new();
        interleave_for_device(&planar, self.mixer.channels(), &mut inter);
        let c = Arc::new(inter);
        self.cache.lock().unwrap().insert(path.to_owned(), c.clone());
        Ok(c)
    }

    /// Decode (first time; blocking) and start `id` on the sfx bus. Returns the file played.
    pub fn play(&self, id: &str, gain: f32) -> Result<PathBuf> {
        let path = self.resolve(id).ok_or_else(|| anyhow!("no sfx {id:?} in {:?}", self.dirs))?;
        let data = self.clip(&path)?;
        if !self.mixer.add(BusId::Sfx, Box::new(Clip { data, pos: 0, gain })) {
            return Err(anyhow!("mixer queue full"));
        }
        Ok(path)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::decode::wav_bytes;
    use crate::mixer::mixer;

    #[test]
    fn resolves_like_the_sim_and_is_audible() {
        let dir = std::env::temp_dir().join(format!("r3x-sfx-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let tone: Vec<f32> = (0..4000).map(|i| (i as f32 * 0.3).sin() * 0.5).collect();
        std::fs::write(dir.join("Air Horn.wav"), wav_bytes(8000, 1, &tone)).unwrap();
        let (mut m, h) = mixer(8000, 2);
        let bank = SfxBank::new(h, vec![dir.clone()]);
        assert!(bank.resolve("air_horn").is_some() && bank.resolve("AIRHORN").is_some());
        assert!(bank.resolve("nope").is_none());
        bank.play("air_horn", 1.0).unwrap();
        let mut out = vec![0.0; 2000];
        m.render(&mut out, &RenderTime::now());
        let rms = (out.iter().map(|x| x * x).sum::<f32>() / out.len() as f32).sqrt();
        assert!(rms > 0.2, "sfx bus output is not silent: {rms}");
        std::fs::remove_dir_all(dir).ok();
    }
}
