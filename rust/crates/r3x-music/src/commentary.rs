//! The DJ commentary cache on the bus: `conversation.cache_speech {key, text}` -> synthesise
//! -> `speech_cached {key, duration_s}` (or `speech_cache_failed`); `conversation.play_cached
//! {key, playback_id}` -> the line on the speech bus -> exactly one `cached_playback_ended`.
//! Storage and playback are `r3x_audio::speech_cache`; synthesis is injected ([`Synthesize`]):
//! the voice's TTS live, or [`FixtureSynth`] replaying recorded audio (no paid calls).

use std::collections::HashMap;
use std::future::Future;
use std::path::{Path, PathBuf};
use std::pin::Pin;
use std::sync::Arc;

use anyhow::{anyhow, Context, Result};
use r3x_audio::speech_cache::SpeechCache;
use r3x_bus::{Bus, Received};
use r3x_contracts::{Body, ConversationEvent, Domain, Event, Source};

pub type SynthFuture<'a> = Pin<Box<dyn Future<Output = Result<Vec<i16>>> + Send + 'a>>;

/// Text -> 24 kHz mono PCM.
pub trait Synthesize: Send + Sync + 'static {
    fn synthesize<'a>(&'a self, text: &'a str) -> SynthFuture<'a>;
}

/// Replays TTS recorded by CantinaOS fixtures (`R3X_FIXTURE_DIR/tts.jsonl` + `tts/<n>.pcm|mp3`).
pub struct FixtureSynth {
    by_text: HashMap<String, PathBuf>,
}

impl FixtureSynth {
    pub fn load(dir: &Path) -> Result<Self> {
        let raw = std::fs::read_to_string(dir.join("tts.jsonl")).with_context(|| format!("{}/tts.jsonl", dir.display()))?;
        let mut by_text = HashMap::new();
        for line in raw.lines().filter(|l| !l.trim().is_empty()) {
            let v: serde_json::Value = serde_json::from_str(line)?;
            if let (Some(t), Some(a)) = (v["text"].as_str(), v["audio"].as_str()) {
                by_text.entry(t.to_owned()).or_insert_with(|| dir.join(a));
            }
        }
        Ok(Self { by_text })
    }

    pub fn texts(&self) -> impl Iterator<Item = &String> {
        self.by_text.keys()
    }
}

impl Synthesize for FixtureSynth {
    fn synthesize<'a>(&'a self, text: &'a str) -> SynthFuture<'a> {
        Box::pin(async move {
            let path = self.by_text.get(text).cloned().ok_or_else(|| anyhow!("fixture miss for {text:?}"))?;
            tokio::task::spawn_blocking(move || -> Result<Vec<i16>> {
                if path.extension().is_some_and(|e| e == "pcm") {
                    let b = std::fs::read(&path)?;
                    Ok(b.chunks_exact(2).map(|c| i16::from_le_bytes([c[0], c[1]])).collect())
                } else {
                    let m = r3x_audio::decode::decode_file(&path, r3x_audio::TTS_RATE, 1)?.swap_remove(0);
                    Ok(m.into_iter().map(r3x_audio::f32_to_i16).collect())
                }
            })
            .await?
        })
    }
}

/// Serve the cache requests published on `bus`.
pub fn spawn(bus: &Bus, cache: Arc<SpeechCache>, synth: Arc<dyn Synthesize>) {
    let mut rx = bus.subscribe(Domain::Conversation);
    let bus = bus.clone();
    tokio::spawn(async move {
        while let Some(m) = rx.recv().await {
            let Received::Message(env) = m else { continue };
            let Body::Event(Event::Conversation(e)) = &env.body else { continue };
            let (bus, cache, synth, cid) = (bus.clone(), cache.clone(), synth.clone(), env.conversation_id.clone());
            match e.clone() {
                ConversationEvent::CacheSpeech { key, text } => {
                    tokio::spawn(async move {
                        let ev = match synth.synthesize(&text).await {
                            Ok(pcm) if !pcm.is_empty() => {
                                let duration_s = cache.insert_pcm24(&key, &pcm);
                                tracing::info!(%key, duration_s, "commentary cached");
                                ConversationEvent::SpeechCached { key, duration_s }
                            }
                            Ok(_) => ConversationEvent::SpeechCacheFailed { key, error: "empty audio".into() },
                            Err(e) => ConversationEvent::SpeechCacheFailed { key, error: e.to_string() },
                        };
                        bus.publish(Source::System, cid, Event::Conversation(ev));
                    });
                }
                ConversationEvent::PlayCached { key, playback_id } => {
                    tokio::spawn(async move {
                        let ok = match cache.play(&key, 1.0) {
                            Some(done) => done.await.unwrap_or(false),
                            None => {
                                tracing::warn!(%key, "play_cached: not cached");
                                false
                            }
                        };
                        bus.publish(Source::System, cid, Event::Conversation(ConversationEvent::CachedPlaybackEnded { playback_id, ok }));
                    });
                }
                _ => {}
            }
        }
    });
}
