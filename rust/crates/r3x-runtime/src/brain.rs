//! `--brain rust`: the Rust brain owns turns (plan Phase 5), instead of CantinaOS.
//!
//! Glue that is the runtime's job, not the brain's:
//! - `conversation.speak` -> the voice's speech FIFO (a refused line ends at once, so no plan
//!   waits on it); the commentary cache is the voice's (`r3x_voice::commentary`), or the music
//!   engine's when there is no voice;
//! - push-to-talk from the `intent` class to the voice;
//! - memory: the one-time CantinaOS import and the start-up summary catch-up.

use std::sync::Arc;

use r3x_brain::{Brain, BrainConfig, BrainDeps, Ptt, PttHook};
use r3x_bus::{Bus, Received};
use r3x_contracts::profile::Ducking;
use r3x_contracts::{Body, ConversationEvent, Domain, Event, MusicEvent, PerfEvent, Source};
use r3x_voice::speaker::SpeechRequest;
use r3x_voice::Voice;

use crate::replay::Choices;

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
/// Also returns the brain's `Memory`, for vision's presence link (same instance, so the turn
/// context sees who is present).
pub fn spawn(
    bus: &Bus,
    voice: Option<Voice>,
    choices: Option<Choices>,
    ducking: Ducking,
    emotes: Vec<String>,
) -> anyhow::Result<(Brain, Arc<r3x_memory::Memory>)> {
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
    // A replay keeps its database clean (the DJ history would change its picks).
    if replay.is_none() {
        import_cantina_once(&memory);
    }
    if let Some(l) = llm.clone() {
        memory = memory.with_llm(l);
    }
    let latency = r3x_ops::latency::LatencyTracker::spawn(bus);
    if let Some(v) = &voice {
        speech_glue(bus, v.clone(), ducking);
    }
    let memory = Arc::new(memory);
    // Summaries are Claude calls: live only (a replay has no fixtures for them).
    if replay.is_none() {
        let m = memory.clone();
        tokio::spawn(async move {
            if let Err(e) = m.catch_up_summaries().await {
                tracing::warn!("memory summary catch-up: {e}");
            }
        });
    }
    spawn_engagement_link(bus, memory.clone());
    let deps = BrainDeps {
        llm,
        router,
        memory: Some(memory.clone()),
        latency: Some(latency),
        ptt: voice.map(ptt_hook),
        chooser: choices.map_or_else(r3x_brain::random_chooser, |c| c.chooser()),
    };
    let brain = Brain::spawn(bus, BrainConfig { emotes, ..BrainConfig::from_env() }, deps).ok_or_else(|| anyhow::anyhow!("intent class taken (is --bridge on?)"))?;
    Ok((brain, memory))
}

fn ptt_hook(voice: Voice) -> PttHook {
    Arc::new(move |p| {
        let v = voice.clone();
        Box::pin(async move {
            match p {
                Ptt::Start { owner } => v.ptt_start(&owner).await,
                Ptt::Stop { owner } => v.ptt_stop(Some(&owner)).await,
            }
        })
    })
}

/// Engagement changes -> `Memory::mode_changed` (leaving INTERACTIVE with someone who talked
/// writes their rolling summary), in CantinaOS's mode names.
fn spawn_engagement_link(bus: &Bus, memory: Arc<r3x_memory::Memory>) {
    let mut eng = bus.watch::<r3x_contracts::EngagementState>();
    tokio::spawn(async move {
        let name = |e: r3x_contracts::Engagement| format!("{e:?}").to_ascii_uppercase();
        let mut last = eng.borrow_and_update().engagement;
        while eng.changed().await.is_ok() {
            let now = eng.borrow_and_update().engagement;
            if now != last {
                if let Err(e) = memory.mode_changed(&name(last), &name(now)).await {
                    tracing::warn!("memory: {e}");
                }
                last = now;
            }
        }
    });
}

/// Import CantinaOS's memory (`memory_data/`, the nervous-system DJ keys) once per database.
fn import_cantina_once(memory: &r3x_memory::Memory) {
    const DONE: &str = "import.cantina_os";
    let root = std::env::var_os("R3X_CANTINA_DIR")
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|| std::path::PathBuf::from(concat!(env!("CARGO_MANIFEST_DIR"), "/../../../cantina_os")));
    if !root.is_dir() || memory.get_state(DONE).ok().flatten().is_some() {
        return;
    }
    match r3x_memory::import::import_cantina(memory, &root) {
        Ok(stats) => {
            let _ = memory.set_state(DONE, &serde_json::json!({ "from": root.display().to_string(), "stats": format!("{stats:?}") }));
        }
        Err(e) => tracing::warn!("CantinaOS memory import failed (will retry next start): {e}"),
    }
}

/// Speech for the Rust brain: its `conversation.speak` lines, and the performer's show lines
/// (`perf.speak`, clip `show-r3x-N`) and show ducking (`perf.duck` -> the music engine), which
/// the CantinaOS bridge carries in bridge mode.
fn speech_glue(bus: &Bus, voice: Voice, d: Ducking) {
    let (mut conv, mut perf) = (bus.subscribe(Domain::Conversation), bus.subscribe(Domain::Perf));
    let bus = bus.clone();
    tokio::spawn(async move {
        let mut show_lines = 0u64;
        loop {
            let m = tokio::select! {
                m = conv.recv() => m,
                m = perf.recv() => m,
            };
            let Some(m) = m else { return };
            let Received::Message(env) = m else { continue };
            let req = match &env.body {
                Body::Event(Event::Conversation(ConversationEvent::Speak { text, clip_id, plan_id, reply })) => SpeechRequest {
                    text: text.clone(),
                    conversation_id: env.conversation_id.clone(),
                    clip_id: clip_id.clone(),
                    step_id: None,
                    plan_id: plan_id.clone(),
                    source: env.source,
                    reply: *reply,
                },
                Body::Event(Event::Perf(PerfEvent::Speak { text })) => {
                    show_lines += 1;
                    let clip = format!("show-r3x-{show_lines}");
                    SpeechRequest {
                        text: text.clone(),
                        conversation_id: None,
                        clip_id: Some(clip.clone()),
                        step_id: Some(clip),
                        plan_id: Some("r3x-performer".into()),
                        source: Source::Timeline,
                        reply: false,
                    }
                }
                Body::Event(Event::Perf(PerfEvent::Duck { on })) => {
                    let e = if *on { MusicEvent::Duck { level: d.level, fade_ms: d.ramp_ms } } else { MusicEvent::Unduck { fade_ms: d.ramp_ms } };
                    bus.publish(Source::Timeline, None, Event::Music(e));
                    continue;
                }
                _ => continue,
            };
            let cid = req.conversation_id.clone();
            if !voice.say(req) {
                bus.publish(Source::System, cid, Event::Conversation(ConversationEvent::SpeechEnded));
            }
        }
    });
}
