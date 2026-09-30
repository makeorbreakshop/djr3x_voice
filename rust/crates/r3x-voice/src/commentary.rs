//! The DJ commentary cache, voiced (plan §6 `cached_speech`): lines are synthesised ahead of
//! time and played back through the speech FIFO like any live line, so a cached line gets the
//! first-audible-sample `speech_started`, the mouth at the profile rate and character timings
//! (show tags) exactly as a reply does.
//!
//! - [`CachingTts`] wraps the voice's TTS backend: [`CachingTts::precache`] synthesises a
//!   line over HTTP (the dialogue socket stays free for replies) and keeps its chunks
//!   (PCM + alignment); a later request for that text replays them with no network.
//! - [`spawn`] serves the brain's `conversation.cache_speech {key, text}` -> `speech_cached
//!   {key, duration_s}` | `speech_cache_failed`, and `play_cached {key, playback_id}` ->
//!   the line on the FIFO -> exactly one `cached_playback_ended {playback_id, ok}`.

use std::collections::{HashMap, VecDeque};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use anyhow::{anyhow, Result};
use r3x_bus::{Bus, Received};
use r3x_contracts::{Body, ConversationEvent, Domain, Event, Source};
use tokio::sync::mpsc;

use crate::eleven::{ChunkRx, TtsBackend, TtsChunk};
use crate::speaker::{SpeechEvent, SpeechRequest};
use crate::voice::{Voice, VoiceEvent};

/// Cached lines kept (a DJ set needs current + next).
const MAX_LINES: usize = 16;

type Lines = (HashMap<String, Arc<Vec<TtsChunk>>>, VecDeque<String>);

/// A TTS backend with a cache of pre-synthesised lines in front of it.
pub struct CachingTts {
    inner: Arc<dyn TtsBackend>,
    lines: Mutex<Lines>,
}

impl CachingTts {
    pub fn new(inner: Arc<dyn TtsBackend>) -> Arc<Self> {
        Arc::new(Self { inner, lines: Mutex::default() })
    }

    /// Synthesise `text` now and keep it. Returns its duration (s).
    pub async fn precache(&self, text: &str) -> Result<f64> {
        if let Some(c) = self.get(text) {
            return Ok(secs(&c));
        }
        let mut rx = self.inner.http(text);
        let mut chunks = Vec::new();
        while let Some(c) = rx.recv().await {
            chunks.push(c?);
        }
        if chunks.iter().all(|c| c.pcm.is_empty()) {
            return Err(anyhow!("empty audio"));
        }
        let d = secs(&chunks);
        let mut g = self.lines.lock().unwrap();
        let (map, order) = &mut *g;
        order.retain(|t| t != text);
        order.push_back(text.to_owned());
        map.insert(text.to_owned(), Arc::new(chunks));
        while order.len() > MAX_LINES {
            if let Some(old) = order.pop_front() {
                map.remove(&old);
            }
        }
        Ok(d)
    }

    pub fn contains(&self, text: &str) -> bool {
        self.lines.lock().unwrap().0.contains_key(text)
    }

    fn get(&self, text: &str) -> Option<Arc<Vec<TtsChunk>>> {
        self.lines.lock().unwrap().0.get(text).cloned()
    }

    fn replay(chunks: Arc<Vec<TtsChunk>>) -> ChunkRx {
        let (tx, rx) = mpsc::channel(chunks.len().max(1));
        for c in chunks.iter() {
            let _ = tx.try_send(Ok(c.clone()));
        }
        rx
    }
}

fn secs(chunks: &[TtsChunk]) -> f64 {
    chunks.iter().map(|c| c.pcm.len()).sum::<usize>() as f64 / r3x_audio::TTS_RATE as f64
}

impl TtsBackend for CachingTts {
    fn dialogue(&self, text: &str) -> Option<ChunkRx> {
        match self.get(text) {
            Some(c) => Some(Self::replay(c)),
            None => self.inner.dialogue(text),
        }
    }

    fn http(&self, text: &str) -> ChunkRx {
        match self.get(text) {
            Some(c) => Self::replay(c),
            None => self.inner.http(text),
        }
    }
}

/// Serve the commentary cache requests on `bus` with `voice` (whose backend is `tts`).
pub fn spawn(bus: &Bus, voice: Voice, tts: Arc<CachingTts>) {
    let mut rx = bus.subscribe(Domain::Conversation);
    let bus = bus.clone();
    let keys: Arc<Mutex<HashMap<String, String>>> = Arc::default();
    tokio::spawn(async move {
        while let Some(m) = rx.recv().await {
            let Received::Message(env) = m else { continue };
            let Body::Event(Event::Conversation(e)) = &env.body else { continue };
            let (bus, cid) = (bus.clone(), env.conversation_id.clone());
            match e.clone() {
                ConversationEvent::CacheSpeech { key, text } => {
                    keys.lock().unwrap().insert(key.clone(), text.clone());
                    let tts = tts.clone();
                    tokio::spawn(async move {
                        let ev = match tts.precache(&text).await {
                            Ok(duration_s) => {
                                tracing::info!(%key, duration_s, "commentary cached");
                                ConversationEvent::SpeechCached { key, duration_s }
                            }
                            Err(e) => ConversationEvent::SpeechCacheFailed { key, error: e.to_string() },
                        };
                        bus.publish(Source::System, cid, Event::Conversation(ev));
                    });
                }
                ConversationEvent::PlayCached { key, playback_id } => {
                    let text = keys.lock().unwrap().get(&key).cloned().filter(|t| tts.contains(t));
                    let voice = voice.clone();
                    tokio::spawn(async move {
                        let ok = match text {
                            Some(t) => play(&voice, t, &playback_id).await,
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

/// Speak a cached line through the FIFO; true if it played to the end.
async fn play(voice: &Voice, text: String, playback_id: &str) -> bool {
    let cid = format!("cached-{playback_id}");
    let mut events = voice.subscribe();
    let req = SpeechRequest {
        text,
        conversation_id: Some(cid.clone()),
        clip_id: Some(cid.clone()),
        step_id: None,
        plan_id: None,
        source: Source::Timeline,
        reply: false,
    };
    if !voice.say(req) {
        return false;
    }
    let mine = |r: &SpeechRequest| r.conversation_id.as_deref() == Some(cid.as_str());
    let wait = async {
        loop {
            match events.recv().await {
                Ok(VoiceEvent::Speech(SpeechEvent::Ended { req, error, .. })) if mine(&req) => return error.is_none(),
                Ok(VoiceEvent::Speech(SpeechEvent::Dropped { req, .. })) if mine(&req) => return false,
                Ok(_) | Err(tokio::sync::broadcast::error::RecvError::Lagged(_)) => {}
                Err(_) => return false,
            }
        }
    };
    // Bounded: a line queued behind a long reply still ends; a hung backend does not hang a plan.
    tokio::time::timeout(Duration::from_secs(120), wait).await.unwrap_or(false)
}
