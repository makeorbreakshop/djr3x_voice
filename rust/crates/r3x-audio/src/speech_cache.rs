//! The DJ commentary cache (plan §6 `cached_speech` -> `r3x-audio`): lines synthesised ahead
//! of time, held as PCM under a key, and played on the speech bus on cue, with a completion
//! signal so a plan can wait for the line to end (CantinaOS `CachedSpeechService`).
//!
//! Synthesis is not done here (that is the voice's TTS); callers hand in 24 kHz PCM or a file.

use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use tokio::sync::oneshot;

use crate::mixer::{BusId, MixerHandle, RenderTime, Source};
use crate::resample::Resampler;
use crate::{i16_to_f32, TTS_RATE};

/// Cached lines kept at most (oldest evicted); a DJ set caches current + next.
const MAX_LINES: usize = 16;

/// Lines by key, plus insertion order for eviction.
type Lines = (HashMap<String, Arc<Vec<f32>>>, Vec<String>);

pub struct SpeechCache {
    mixer: MixerHandle,
    lines: Mutex<Lines>,
}

struct CachedLine {
    data: Arc<Vec<f32>>,
    pos: usize,
    gain: f32,
    done: Option<oneshot::Sender<bool>>,
}

impl Source for CachedLine {
    fn mix(&mut self, out: &mut [f32], _: usize, _: &RenderTime) -> bool {
        let n = out.len().min(self.data.len() - self.pos);
        for (o, s) in out[..n].iter_mut().zip(&self.data[self.pos..self.pos + n]) {
            *o += s * self.gain;
        }
        self.pos += n;
        if self.pos < self.data.len() {
            return true;
        }
        if let Some(tx) = self.done.take() {
            let _ = tx.send(true);
        }
        false
    }
}

impl Drop for CachedLine {
    fn drop(&mut self) {
        if let Some(tx) = self.done.take() {
            let _ = tx.send(false); // cleared before it finished
        }
    }
}

impl SpeechCache {
    pub fn new(mixer: MixerHandle) -> Self {
        Self { mixer, lines: Mutex::default() }
    }

    fn store(&self, key: &str, data: Vec<f32>) -> f64 {
        let secs = data.len() as f64 / (self.mixer.sample_rate() as f64 * self.mixer.channels() as f64);
        let mut g = self.lines.lock().unwrap();
        let (map, order) = &mut *g;
        order.retain(|k| k != key);
        order.push(key.to_owned());
        map.insert(key.to_owned(), Arc::new(data));
        while order.len() > MAX_LINES {
            let old = order.remove(0);
            map.remove(&old);
        }
        secs
    }

    /// Hold 24 kHz mono PCM (ElevenLabs `pcm_24000`) under `key`. Returns its duration (s).
    pub fn insert_pcm24(&self, key: &str, pcm: &[i16]) -> f64 {
        let mono: Vec<f32> = pcm.iter().map(|&s| i16_to_f32(s)).collect();
        let mut rs = Resampler::new(TTS_RATE, self.mixer.sample_rate());
        let mut at_rate = Vec::with_capacity(mono.len() * 2);
        rs.process(&mono, &mut at_rate);
        let ch = self.mixer.channels();
        let data: Vec<f32> = at_rate.iter().flat_map(|&s| std::iter::repeat_n(s, ch)).collect();
        self.store(key, data)
    }

    /// Decode an audio file (mp3/wav/...) under `key`. Blocking.
    #[cfg(feature = "decode")]
    pub fn insert_file(&self, key: &str, path: &std::path::Path) -> anyhow::Result<f64> {
        let planar = crate::decode::decode_file(path, self.mixer.sample_rate(), 2)?;
        let mut data = Vec::new();
        crate::decode::interleave_for_device(&planar, self.mixer.channels(), &mut data);
        Ok(self.store(key, data))
    }

    pub fn contains(&self, key: &str) -> bool {
        self.lines.lock().unwrap().0.contains_key(key)
    }

    pub fn duration_s(&self, key: &str) -> Option<f64> {
        let g = self.lines.lock().unwrap();
        g.0.get(key).map(|d| d.len() as f64 / (self.mixer.sample_rate() as f64 * self.mixer.channels() as f64))
    }

    pub fn remove(&self, key: &str) {
        let mut g = self.lines.lock().unwrap();
        g.0.remove(key);
        g.1.retain(|k| k != key);
    }

    /// Start `key` on the speech bus. The receiver resolves `true` when the last sample has
    /// been rendered, `false` if it was cut off; `None` = not cached.
    pub fn play(&self, key: &str, gain: f32) -> Option<oneshot::Receiver<bool>> {
        let data = self.lines.lock().unwrap().0.get(key).cloned()?;
        let (tx, rx) = oneshot::channel();
        self.mixer.add(BusId::Speech, Box::new(CachedLine { data, pos: 0, gain, done: Some(tx) }));
        Some(rx)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::mixer::mixer;

    #[test]
    fn caches_plays_and_signals_the_end() {
        let (mut m, h) = mixer(8000, 2);
        let cache = SpeechCache::new(h.clone());
        let secs = cache.insert_pcm24("intro", &vec![8000i16; 2400]); // 0.1 s at 24 kHz
        assert!((secs - 0.1).abs() < 0.002, "{secs}");
        assert!(cache.play("missing", 1.0).is_none());
        let mut done = cache.play("intro", 1.0).unwrap();
        let mut out = vec![0.0; 2 * 400];
        m.render(&mut out, &RenderTime::now());
        assert!(h.level(BusId::Speech).rms > 0.2);
        assert!(done.try_recv().is_err(), "not finished after 50 ms");
        m.render(&mut out, &RenderTime::now());
        assert_eq!(done.try_recv(), Ok(true));
        assert!(cache.contains("intro"), "stays cached for a replay");
    }
}
