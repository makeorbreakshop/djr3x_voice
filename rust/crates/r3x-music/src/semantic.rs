//! Semantic music search (plan §7a, D10): CLAP audio vectors per track (10 s segments at
//! 15/50/85 %, mean, L2-normalised), a CLAP text vector per query, score = pos - 0.5·neg.
//! Port of CantinaOS `semantic_music_search.py`; the models are ONNX exports made offline by
//! `scripts/export_clap_onnx.py` (mel front end inside the audio graph).

use std::path::{Path, PathBuf};

use anyhow::Result;
use serde::{Deserialize, Serialize};

pub const SAMPLE_RATE: u32 = 48_000;
pub const SEGMENT_SAMPLES: usize = SAMPLE_RATE as usize * 10;
pub const SEGMENT_OFFSETS: [f64; 3] = [0.15, 0.50, 0.85];
pub const NEGATIVE_WEIGHT: f32 = 0.5;

#[derive(Debug, Clone, PartialEq)]
pub struct Match {
    pub key: String,
    pub score: f32,
    pub positive: f32,
    pub negative: Option<f32>,
}

/// Track vectors (L2-normalised), in library order.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct Index {
    pub names: Vec<String>,
    pub vectors: Vec<Vec<f32>>,
}

fn dot(a: &[f32], b: &[f32]) -> f32 {
    a.iter().zip(b).map(|(x, y)| x * y).sum()
}

/// `rank_semantic_embeddings`: best first, at most `limit`.
pub fn rank(index: &Index, pos: &[f32], neg: Option<&[f32]>, weight: f32, limit: usize) -> Vec<Match> {
    let mut m: Vec<Match> = index
        .names
        .iter()
        .zip(&index.vectors)
        .map(|(n, v)| {
            let p = dot(v, pos);
            let q = neg.map(|n| dot(v, n));
            Match { key: n.clone(), score: p - weight * q.unwrap_or(0.0), positive: p, negative: q }
        })
        .collect();
    m.sort_by(|a, b| b.score.total_cmp(&a.score));
    m.truncate(limit.clamp(1, index.names.len().max(1)));
    m
}

/// What the engine needs from a search backend.
pub trait Search: Send + Sync + 'static {
    fn search(&self, query: &str, negative: Option<&str>, limit: usize) -> Result<Vec<Match>>;
}

/// `@semantic <query> [@avoid <negative>]` (Jev's structured music request).
pub fn parse_semantic_request(value: &str) -> Option<(String, Option<String>)> {
    let text = value.split_whitespace().collect::<Vec<_>>().join(" ");
    let body = text.get(..10).filter(|p| p.eq_ignore_ascii_case("@semantic ")).map(|_| &text[10..])?;
    let lower = body.to_lowercase();
    let (q, n) = match lower.find(" @avoid ") {
        Some(i) => (body[..i].trim(), Some(body[i + 8..].trim())),
        None => (body.trim(), None),
    };
    if q.is_empty() {
        return None;
    }
    Some((q.to_owned(), n.filter(|n| !n.is_empty()).map(str::to_owned)))
}

const SEMANTIC_WORDS: &[&str] = &[
    "aggressive", "ambient", "angry", "atmospheric", "bright", "calm", "celebratory", "cheerful", "chill", "cinematic", "crazy",
    "craziest", "country", "dance", "dark", "dramatic", "dreamy", "electronic", "energetic", "energy", "epic", "funk", "funky", "fun",
    "gentle", "happy", "hard", "heavy", "hopeful", "intense", "jazz", "lively", "melancholy", "mellow", "metal", "optimistic", "party",
    "peaceful", "playful", "quirky", "relaxing", "robotic", "rock", "sad", "scary", "soft", "space", "spooky", "strange", "synth",
    "uplifting", "upbeat", "banger",
];

/// Conservative fallback for typed/Claude requests that did not pass through Jev.
pub fn looks_semantic(value: &str) -> bool {
    value
        .to_lowercase()
        .split(|c: char| !(c.is_ascii_alphanumeric() || c == '\''))
        .any(|w| SEMANTIC_WORDS.contains(&w))
}

/// `R3X_CLAP_DIR`, default `~/.cache/dj-r3x/clap`.
pub fn default_model_dir() -> PathBuf {
    std::env::var_os("R3X_CLAP_DIR")
        .filter(|d| !d.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(std::env::var_os("HOME").unwrap_or_default()).join(".cache/dj-r3x/clap"))
}

/// Three 10 s windows at 15/50/85 % of a 48 kHz mono file (one zero-padded window if shorter).
pub fn track_segments(path: &Path) -> Result<Vec<Vec<f32>>> {
    let audio = r3x_audio::decode::decode_file(path, SAMPLE_RATE, 1)?.swap_remove(0);
    Ok(segments(&audio))
}

pub fn segments(audio: &[f32]) -> Vec<Vec<f32>> {
    if audio.len() <= SEGMENT_SAMPLES {
        let mut s = audio.to_vec();
        s.resize(SEGMENT_SAMPLES, 0.0);
        return vec![s];
    }
    let max_start = audio.len() - SEGMENT_SAMPLES;
    SEGMENT_OFFSETS.iter().map(|o| {
        let s = (max_start as f64 * o) as usize;
        audio[s..s + SEGMENT_SAMPLES].to_vec()
    }).collect()
}

pub fn normalise(v: &mut [f32]) {
    let n = dot(v, v).sqrt().max(1e-12);
    v.iter_mut().for_each(|x| *x /= n);
}

#[cfg(feature = "clap")]
pub use clap::{Clap, ClapSearch};

#[cfg(feature = "clap")]
mod clap {
    use std::sync::Mutex;
    use std::time::UNIX_EPOCH;

    use anyhow::{anyhow, Context};
    use ort::session::Session;
    use ort::value::Tensor;
    use tokenizers::Tokenizer;

    use super::*;

    /// The exported CLAP encoders.
    pub struct Clap {
        text: Mutex<Session>,
        audio: Mutex<Session>,
        tok: Tokenizer,
        dir: PathBuf,
    }

    impl Clap {
        pub fn load(dir: &Path) -> Result<Self> {
            let session = |f: &str| -> Result<Session> {
                Session::builder()?.with_intra_threads(2)?.commit_from_file(dir.join(f)).with_context(|| format!("{}", dir.join(f).display()))
            };
            let tok = Tokenizer::from_file(dir.join("tokenizer.json")).map_err(|e| anyhow!("tokenizer: {e}"))?;
            Ok(Self { text: Mutex::new(session("clap_text.onnx")?), audio: Mutex::new(session("clap_audio.onnx")?), tok, dir: dir.to_owned() })
        }

        pub fn token_ids(&self, query: &str) -> Result<Vec<i64>> {
            let enc = self.tok.encode(query, true).map_err(|e| anyhow!("tokenize: {e}"))?;
            Ok(enc.get_ids().iter().map(|&i| i as i64).collect())
        }

        /// Raw (unnormalised) text vector, as the Python ranker uses it.
        pub fn embed_text(&self, query: &str) -> Result<Vec<f32>> {
            let ids = self.token_ids(query)?;
            let n = ids.len();
            let ids = Tensor::from_array(([1usize, n], ids))?;
            let mask = Tensor::from_array(([1usize, n], vec![1i64; n]))?;
            let mut s = self.text.lock().unwrap();
            let out = s.run(ort::inputs!["input_ids" => ids, "attention_mask" => mask])?;
            let (_, v) = out["text_embeds"].try_extract_tensor::<f32>()?;
            Ok(v.to_vec())
        }

        /// Audio vectors for 10 s / 48 kHz windows.
        pub fn embed_audio(&self, segs: &[Vec<f32>]) -> Result<Vec<Vec<f32>>> {
            let flat: Vec<f32> = segs.iter().flat_map(|s| s.iter().copied()).collect();
            let wave = Tensor::from_array(([segs.len(), SEGMENT_SAMPLES], flat))?;
            let mut s = self.audio.lock().unwrap();
            let out = s.run(ort::inputs!["waveform" => wave])?;
            let (shape, v) = out["audio_embeds"].try_extract_tensor::<f32>()?;
            let dim = shape[1] as usize;
            Ok(v.chunks(dim).map(<[f32]>::to_vec).collect())
        }

        /// One track vector: mean of its windows, L2-normalised.
        pub fn embed_track(&self, path: &Path) -> Result<Vec<f32>> {
            let e = self.embed_audio(&track_segments(path)?)?;
            let mut mean = vec![0.0f32; e[0].len()];
            for v in &e {
                mean.iter_mut().zip(v).for_each(|(m, x)| *m += x / e.len() as f32);
            }
            normalise(&mut mean);
            Ok(mean)
        }

        /// Index `tracks` (key, path), reusing `cache` when nothing changed. Slow (seconds per
        /// track on CPU) the first time: run it off the async runtime.
        pub fn build_index(&self, tracks: &[(String, PathBuf)], cache: &Path) -> Result<Index> {
            let fp = self.fingerprint(tracks);
            if let Ok(bytes) = std::fs::read(cache) {
                if let Ok(c) = serde_json::from_slice::<CachedIndex>(&bytes) {
                    if c.fingerprint == fp {
                        tracing::info!("loaded semantic music index for {} tracks", c.index.names.len());
                        return Ok(c.index);
                    }
                }
            }
            let t0 = std::time::Instant::now();
            let mut index = Index::default();
            for (k, p) in tracks {
                match self.embed_track(p) {
                    Ok(v) => {
                        index.names.push(k.clone());
                        index.vectors.push(v);
                    }
                    Err(e) => tracing::warn!("semantic index: skipping {k}: {e}"),
                }
            }
            tracing::info!("built semantic music index for {} tracks in {:.1} s", index.names.len(), t0.elapsed().as_secs_f64());
            let tmp = cache.with_extension("tmp");
            if std::fs::write(&tmp, serde_json::to_vec(&CachedIndex { fingerprint: fp, index: index.clone() })?).is_ok() {
                let _ = std::fs::rename(tmp, cache);
            }
            Ok(index)
        }

        fn fingerprint(&self, tracks: &[(String, PathBuf)]) -> String {
            let stat = |p: &Path| {
                std::fs::metadata(p)
                    .map(|m| (m.len(), m.modified().ok().and_then(|t| t.duration_since(UNIX_EPOCH).ok()).map_or(0, |d| d.as_nanos())))
                    .unwrap_or((0, 0))
            };
            let mut s = format!("clap-r3x-1|{:?}|{:?}", SEGMENT_OFFSETS, stat(&self.dir.join("clap_audio.onnx")));
            for (k, p) in tracks {
                s.push_str(&format!("|{k}|{}|{:?}", p.display(), stat(p)));
            }
            s
        }
    }

    #[derive(Serialize, Deserialize)]
    struct CachedIndex {
        fingerprint: String,
        index: Index,
    }

    /// CLAP + a built index.
    pub struct ClapSearch {
        pub clap: Clap,
        pub index: Index,
    }

    impl Search for ClapSearch {
        fn search(&self, query: &str, negative: Option<&str>, limit: usize) -> Result<Vec<Match>> {
            let pos = self.clap.embed_text(query)?;
            let neg = negative.map(|n| self.clap.embed_text(n)).transpose()?;
            Ok(rank(&self.index, &pos, neg.as_deref(), NEGATIVE_WEIGHT, limit))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ranking_weights_the_negative_and_requests_parse() {
        let index = Index { names: vec!["a".into(), "b".into()], vectors: vec![vec![1.0, 0.0], vec![0.6, 0.8]] };
        let r = rank(&index, &[1.0, 0.4], None, NEGATIVE_WEIGHT, 5);
        assert_eq!(r[0].key, "a");
        let r = rank(&index, &[1.0, 0.4], Some(&[1.0, 0.0]), NEGATIVE_WEIGHT, 5);
        assert_eq!((r[0].key.as_str(), r.len()), ("b", 2), "avoiding a's quality flips it");
        assert_eq!(parse_semantic_request("@semantic  upbeat  dance @AVOID slow"), Some(("upbeat dance".into(), Some("slow".into()))));
        assert_eq!(parse_semantic_request("upbeat"), None);
        assert!(looks_semantic("something upbeat please") && !looks_semantic("play doshka"));
        assert_eq!(segments(&vec![0.0; 100]).len(), 1);
        assert_eq!(segments(&vec![0.0; SEGMENT_SAMPLES * 3]).len(), 3);
    }
}
