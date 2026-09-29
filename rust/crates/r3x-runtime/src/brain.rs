//! `--brain rust`: the Rust brain owns turns (plan Phase 5), instead of CantinaOS.
//!
//! Glue that is the runtime's job, not the brain's:
//! - `conversation.speak` -> the voice's speech FIFO (a refused line ends at once, so no plan
//!   waits on it);
//! - the speech cache until the Phase 4 audio cache lands: `cache_speech` keeps the text and
//!   answers `speech_cached` at once; `play_cached` speaks it then (synthesis at play time, so
//!   a cached DJ line starts ~ one TTS first-byte later than it will once pre-synthesised);
//! - push-to-talk from the `intent` class to the voice.

use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use r3x_brain::{Brain, BrainConfig, BrainDeps, Ptt, PttHook};
use r3x_bus::{Bus, Received};
use r3x_contracts::{Body, ConversationEvent, Domain, Event, Source};
use r3x_voice::speaker::SpeechRequest;
use r3x_voice::Voice;

/// Which brain answers turns.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum BrainMode {
    /// CantinaOS through the bridge (until Phase 5 acceptance).
    #[default]
    Cantina,
    Rust,
}

impl std::str::FromStr for BrainMode {
    type Err = String;
    fn from_str(s: &str) -> Result<Self, String> {
        match s {
            "cantina" => Ok(Self::Cantina),
            "rust" => Ok(Self::Rust),
            o => Err(format!("--brain {o}: expected rust or cantina")),
        }
    }
}

/// Start the Rust brain. Claude from `ANTHROPIC_API_KEY`/`OPENROUTER_API_KEY` (none = the
/// router still acts, nothing is said), Jev from `TYPESAFE_API_KEY`, memory at
/// `R3X_MEMORY_DB` (default `~/.config/dj-r3x/memory.sqlite`).
///
/// `R3X_FIXTURES=replay` + `R3X_FIXTURE_DIR` (as in CantinaOS): Claude and Jev replay a
/// recorded corpus instead (`R3X_FIXTURE_PACE`, default 1 = recorded timing); no network.
pub fn spawn(bus: &Bus, voice: Option<Voice>) -> anyhow::Result<Brain> {
    let env = |k: &str| std::env::var(k).ok().filter(|v| !v.trim().is_empty());
    let replay = env("R3X_FIXTURES").filter(|m| m == "replay").and(env("R3X_FIXTURE_DIR"));
    let pace = env("R3X_FIXTURE_PACE").and_then(|p| p.parse().ok()).unwrap_or(1.0);
    let (llm, router) = match &replay {
        Some(dir) => {
            tracing::info!(dir, "brain replaying recorded Claude and Jev fixtures");
            let claude = Arc::new(r3x_llm::ClaudeFixtures::load(dir, pace)?);
            let jev = Arc::new(r3x_intent::JevFixtures::load(dir, pace)?);
            let cfg = r3x_intent::RouterConfig { api_key: "replay".into(), ..r3x_intent::RouterConfig::from_env() };
            let model = env("CLAUDE_MODEL").unwrap_or_else(|| r3x_llm::DEFAULT_MODEL.into());
            (Some(r3x_llm::LlmClient::replay(claude, &model)), r3x_intent::IntentRouter::new(r3x_intent::JevClient::replay(jev), cfg))
        }
        None => (r3x_llm::LlmClient::from_env()?, r3x_intent::IntentRouter::from_env()),
    };
    if llm.is_none() {
        tracing::warn!("no ANTHROPIC_API_KEY / OPENROUTER_API_KEY: the brain acts but cannot reply");
    }
    let db = std::env::var_os("R3X_MEMORY_DB")
        .map(Into::into)
        .unwrap_or_else(|| r3x_gateway::tokens::config_dir().join("memory.sqlite"));
    let mut memory = r3x_memory::Memory::open(&db)?;
    if let Some(l) = llm.clone() {
        memory = memory.with_llm(l);
    }
    let latency = r3x_ops::latency::LatencyTracker::spawn(bus);
    if let Some(v) = &voice {
        speech_glue(bus, v.clone());
    }
    let deps = BrainDeps {
        llm,
        router,
        memory: Some(Arc::new(memory)),
        latency: Some(latency),
        ptt: voice.map(ptt_hook),
        chooser: r3x_brain::random_chooser(),
    };
    Brain::spawn(bus, BrainConfig::from_env(), deps).ok_or_else(|| anyhow::anyhow!("intent class taken (is --bridge on?)"))
}

fn ptt_hook(voice: Voice) -> PttHook {
    Arc::new(move |p| {
        let v = voice.clone();
        Box::pin(async move {
            match p {
                Ptt::Start { owner } => v.ptt_start(&owner).await,
                Ptt::Stop => v.ptt_stop(None).await,
            }
        })
    })
}

fn speech_glue(bus: &Bus, voice: Voice) {
    let mut rx = bus.subscribe(Domain::Conversation);
    let bus = bus.clone();
    let cache: Arc<Mutex<HashMap<String, String>>> = Arc::default();
    tokio::spawn(async move {
        while let Some(m) = rx.recv().await {
            let Received::Message(env) = m else { continue };
            let Body::Event(Event::Conversation(e)) = &env.body else { continue };
            match e {
                ConversationEvent::Speak { text, clip_id, plan_id, reply } => {
                    let req = SpeechRequest {
                        text: text.clone(),
                        conversation_id: env.conversation_id.clone(),
                        clip_id: clip_id.clone(),
                        step_id: None,
                        plan_id: plan_id.clone(),
                        source: env.source,
                        reply: *reply,
                    };
                    if !voice.say(req) {
                        bus.publish(Source::System, env.conversation_id.clone(), Event::Conversation(ConversationEvent::SpeechEnded));
                    }
                }
                ConversationEvent::CacheSpeech { key, text } => {
                    cache.lock().unwrap().insert(key.clone(), text.clone());
                    let duration_s = text.chars().count() as f64 / 15.0;
                    bus.publish(Source::System, None, Event::Conversation(ConversationEvent::SpeechCached { key: key.clone(), duration_s }));
                }
                ConversationEvent::PlayCached { key, playback_id } => {
                    let text = cache.lock().unwrap().get(key).cloned();
                    let (bus, voice, playback_id) = (bus.clone(), voice.clone(), playback_id.clone());
                    tokio::spawn(async move { play_cached(bus, voice, text, playback_id).await });
                }
                _ => {}
            }
        }
    });
}

async fn play_cached(bus: Bus, voice: Voice, text: Option<String>, playback_id: String) {
    let cid = format!("cached-{playback_id}");
    let mut rx = bus.subscribe(Domain::Conversation);
    let said = text.is_some_and(|text| {
        voice.say(SpeechRequest {
            text,
            conversation_id: Some(cid.clone()),
            clip_id: Some(cid.clone()),
            step_id: None,
            plan_id: None,
            source: Source::Timeline,
            reply: false,
        })
    });
    let mut ok = false;
    if said {
        let deadline = tokio::time::Instant::now() + Duration::from_secs(60);
        while let Ok(Some(m)) = tokio::time::timeout_at(deadline, rx.recv()).await {
            if let Received::Message(env) = m {
                if matches!(env.body, Body::Event(Event::Conversation(ConversationEvent::SpeechEnded))) && env.conversation_id.as_deref() == Some(&cid) {
                    ok = true;
                    break;
                }
            }
        }
    }
    bus.publish(Source::System, None, Event::Conversation(ConversationEvent::CachedPlaybackEnded { playback_id, ok }));
}
