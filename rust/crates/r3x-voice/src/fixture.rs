//! Replayed TTS (`R3X_FIXTURES=replay`): the ElevenLabs audio CantinaOS recorded
//! (`R3X_FIXTURE_DIR/tts.jsonl` + `tts/<n>.pcm|mp3`), chunk by chunk with the recorded
//! alignment and inter-chunk waits (scaled by `pace`; 0 = instant). No network.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::time::Duration;

use anyhow::{anyhow, Context, Result};
use serde::Deserialize;
use tokio::sync::mpsc;

use crate::eleven::{Alignment, ChunkRx, TtsBackend, TtsChunk};

#[derive(Debug, Clone, Deserialize)]
struct RecChunk {
    #[serde(default)]
    wait: f64,
    /// Bytes of 16-bit PCM.
    len: usize,
    alignment: Option<Alignment>,
}

#[derive(Debug, Clone, Deserialize)]
struct Entry {
    text: String,
    audio: String,
    /// Streamed (socket) lines have chunks; whole-file (HTTP, mp3) lines have one wait.
    #[serde(default)]
    chunks: Vec<RecChunk>,
    #[serde(default)]
    wait: f64,
}

pub struct FixtureTts {
    dir: PathBuf,
    by_text: HashMap<String, Entry>,
    pace: f64,
}

impl FixtureTts {
    pub fn load(dir: &Path, pace: f64) -> Result<Self> {
        let raw = std::fs::read_to_string(dir.join("tts.jsonl")).with_context(|| format!("{}/tts.jsonl", dir.display()))?;
        let mut by_text = HashMap::new();
        for line in raw.lines().filter(|l| !l.trim().is_empty()) {
            let e: Entry = serde_json::from_str(line)?;
            by_text.entry(e.text.clone()).or_insert(e);
        }
        Ok(Self { dir: dir.to_owned(), by_text, pace: pace.max(0.0) })
    }

    fn pcm(&self, e: &Entry) -> Result<Vec<i16>> {
        let path = self.dir.join(&e.audio);
        if path.extension().is_some_and(|x| x == "pcm") {
            let b = std::fs::read(&path)?;
            Ok(b.chunks_exact(2).map(|c| i16::from_le_bytes([c[0], c[1]])).collect())
        } else {
            let m = r3x_audio::decode::decode_file(&path, r3x_audio::TTS_RATE, 1)?.swap_remove(0);
            Ok(m.into_iter().map(r3x_audio::f32_to_i16).collect())
        }
    }

    fn replay(&self, text: &str) -> ChunkRx {
        let (tx, rx) = mpsc::channel(64);
        let found = self.by_text.get(text).cloned().ok_or_else(|| anyhow!("no recorded TTS for {text:?}"));
        let pcm = found.as_ref().map_err(|e| anyhow!("{e}")).and_then(|e| self.pcm(e));
        let pace = self.pace;
        tokio::spawn(async move {
            let (e, pcm) = match (found, pcm) {
                (Ok(e), Ok(p)) => (e, p),
                (Err(err), _) | (_, Err(err)) => {
                    let _ = tx.send(Err(err)).await;
                    return;
                }
            };
            let chunks = if e.chunks.is_empty() {
                vec![RecChunk { wait: e.wait, len: pcm.len() * 2, alignment: None }]
            } else {
                e.chunks
            };
            let mut at = 0;
            for c in chunks {
                if pace > 0.0 && c.wait > 0.0 {
                    tokio::time::sleep(Duration::from_secs_f64(c.wait * pace)).await;
                }
                let end = (at + c.len / 2).min(pcm.len());
                let chunk = TtsChunk { pcm: pcm[at..end].to_vec(), alignment: c.alignment };
                at = end;
                if tx.send(Ok(chunk)).await.is_err() {
                    return;
                }
            }
        });
        rx
    }
}

impl TtsBackend for FixtureTts {
    fn dialogue(&self, text: &str) -> Option<ChunkRx> {
        Some(self.replay(text))
    }

    fn http(&self, text: &str) -> ChunkRx {
        self.replay(text)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn replays_recorded_chunks_with_alignment() {
        let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../fixtures/smoke-voice");
        let tts = FixtureTts::load(&dir, 0.0).unwrap();
        let mut rx = tts.http("Time to boogie!");
        let (mut samples, mut aligned) = (0, 0);
        while let Some(c) = rx.recv().await {
            let c = c.unwrap();
            samples += c.pcm.len();
            aligned += c.alignment.map_or(0, |a| a.chars.len());
        }
        assert_eq!(samples * 2, std::fs::metadata(dir.join("tts/7.pcm")).unwrap().len() as usize);
        assert_eq!(aligned, "Time to boogie!".chars().count());
        assert!(tts.http("never recorded").recv().await.unwrap().is_err());
    }
}
