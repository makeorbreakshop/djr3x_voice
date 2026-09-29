//! Parity harness (plan §8): replay the Phase 0 corpora through the Rust brain with every
//! external stubbed (Jev and Claude from their fixtures, voice / music engine / performer as
//! bus stubs) and compare per-turn normalised traces against the CantinaOS recordings.
//!
//! The normaliser, comparison and allow-list are in `common/parity.rs` (shared with the
//! runtime's standalone acceptance test). Every recorded Jev and Claude request must be hit,
//! which pins the prompt bytes (`<action_already_taken>` with the real outcome, commentary prompts).
//! Timing: externals replay at their recorded pace on a paused clock, so the Rust brain's legs
//! (stop -> intent, -> first reply chunk, -> reply speech) are recorded external time plus
//! whatever the brain itself adds; each must be no slower than CantinaOS's within
//! max(300 ms, 30 %) (the trace tolerance). TTS is instant here, so Rust is usually faster.
//! Intended differences are listed in `ALLOWED` with their reason.

mod common;

use std::collections::{HashMap, VecDeque};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use r3x_brain::{Brain, BrainConfig, BrainDeps, Chooser};
use r3x_bus::{Bus, Received};
use common::parity::*;
use r3x_contracts::{
    Ack, Body, Command, ConversationEvent, Domain, EndReason, Event, IntentCommand, MessageClass, MusicEvent, MusicState, PerfCommand,
    PerfEvent, RunKind, Source, StageState, StopTarget, Track,
};
use r3x_intent::{IntentRouter, JevClient, JevFixtures, RouterConfig};
use r3x_llm::{ClaudeFixtures, LlmClient};
use serde_json::Value;

// ------------------------------------------------------------------ stubs

type Picks = Arc<Mutex<HashMap<String, VecDeque<String>>>>;

fn replay_chooser(corpus: &str) -> (Chooser, Picks) {
    let mut picks: HashMap<String, VecDeque<String>> = HashMap::new();
    for l in std::fs::read_to_string(repo().join("fixtures").join(corpus).join("choice.jsonl")).unwrap().lines() {
        let v: Value = serde_json::from_str(l).unwrap();
        picks.entry(v["key"].as_str().unwrap().into()).or_default().push_back(v["picked"].as_str().unwrap().into());
    }
    let picks = Arc::new(Mutex::new(picks));
    let p = picks.clone();
    let chooser: Chooser = Arc::new(move |key, options: &[String]| p.lock().unwrap().get_mut(key).and_then(VecDeque::pop_front).or_else(|| options.first().cloned()));
    (chooser, picks)
}

/// Voice + speech cache: a line plays at 15 chars/s; caching a line takes 4.5 s (recorded:
/// 2.6-4.4 s of ElevenLabs after Claude's commentary).
fn stub_voice(bus: &Bus) {
    let mut rx = bus.subscribe(Domain::Conversation);
    let bus = bus.clone();
    tokio::spawn(async move {
        let mut said: Vec<(Option<String>, String)> = Vec::new();
        while let Some(Received::Message(env)) = rx.recv().await {
            let (bus, cid) = (bus.clone(), env.conversation_id.clone());
            match &env.body {
                Body::Event(Event::Conversation(ConversationEvent::Speak { text, .. })) => {
                    if said.contains(&(cid.clone(), text.clone())) {
                        bus.publish(Source::System, cid, Event::Conversation(ConversationEvent::SpeechEnded));
                        continue;
                    }
                    said.push((cid.clone(), text.clone()));
                    let secs = text.chars().count() as f64 / 15.0;
                    tokio::spawn(async move {
                        bus.publish(Source::System, cid.clone(), Event::Conversation(ConversationEvent::SpeechStarted));
                        tokio::time::sleep(Duration::from_secs_f64(secs)).await;
                        bus.publish(Source::System, cid, Event::Conversation(ConversationEvent::SpeechEnded));
                    });
                }
                Body::Event(Event::Conversation(ConversationEvent::CacheSpeech { key, .. })) => {
                    let key = key.clone();
                    tokio::spawn(async move {
                        tokio::time::sleep(Duration::from_millis(4500)).await;
                        bus.publish(Source::System, None, Event::Conversation(ConversationEvent::SpeechCached { key, duration_s: 10.0 }));
                    });
                }
                Body::Event(Event::Conversation(ConversationEvent::PlayCached { playback_id, .. })) => {
                    let playback_id = playback_id.clone();
                    tokio::spawn(async move {
                        tokio::time::sleep(Duration::from_secs(2)).await;
                        bus.publish(Source::System, None, Event::Conversation(ConversationEvent::CachedPlaybackEnded { playback_id, ok: true }));
                    });
                }
                _ => {}
            }
        }
    });
}

/// Music engine with CantinaOS's semantics at recording time: a bare play is the engine's
/// (recorded) random pick; `next` outside DJ mode starts nothing.
fn stub_music(bus: &Bus, picks: Picks) {
    let mut rx = bus.subscribe(Domain::Music);
    let bus = bus.clone();
    tokio::spawn(async move {
        while let Some(Received::Message(env)) = rx.recv().await {
            let start = |title: String| {
                let track = Track { title, ..Default::default() };
                bus.update(Source::System, |m: &mut MusicState| {
                    m.playing = true;
                    m.track = Some(track.clone());
                });
                bus.publish(Source::System, None, Event::Music(MusicEvent::TrackStarted { track }));
            };
            match &env.body {
                Body::Event(Event::Music(MusicEvent::Play { query })) => {
                    let lib = bus.get::<MusicState>().library;
                    let pick = query.clone().filter(|q| lib.contains(q)).or_else(|| picks.lock().unwrap().get_mut("music.random_track").and_then(VecDeque::pop_front));
                    start(pick.unwrap_or_else(|| lib[0].clone()));
                }
                Body::Event(Event::Music(MusicEvent::Crossfade { track, id, .. })) => {
                    start(track.clone());
                    bus.publish(Source::System, None, Event::Music(MusicEvent::CrossfadeComplete { id: id.clone() }));
                }
                Body::Event(Event::Music(MusicEvent::Stop)) => {
                    bus.update(Source::System, |m: &mut MusicState| {
                        m.playing = false;
                        m.track = None;
                    });
                    bus.publish(Source::System, None, Event::Music(MusicEvent::TrackStopped));
                }
                _ => {}
            }
        }
    });
}

/// Performer: every known play starts at once and ends 0.5 s later (show-layer runs are
/// interrupted by a layer stop).
fn stub_performer(bus: &Bus, known: Vec<String>) {
    let mut rx = bus.take_commands(MessageClass::Perf).unwrap();
    let bus = bus.clone();
    tokio::spawn(async move {
        let mut run = 0u64;
        while let Some(req) = rx.recv().await {
            let source = req.source();
            match req.command.clone() {
                Command::Perf(PerfCommand::Play { id, .. }) if known.contains(&id) => {
                    run += 1;
                    req.ack(Ack::Accepted);
                    let kind = RunKind::Cue; // not compared
                    bus.publish(source, None, Event::Perf(PerfEvent::Started { id: id.clone(), kind, source, run_id: run }));
                    let bus = bus.clone();
                    tokio::spawn(async move {
                        tokio::time::sleep(Duration::from_millis(500)).await;
                        bus.publish(source, None, Event::Perf(PerfEvent::Ended { id, kind, source, run_id: run, reason: EndReason::Done }));
                    });
                }
                Command::Perf(PerfCommand::Play { id, .. }) => req.ack(Ack::rejected(format!("unknown show item {id}"))),
                Command::Perf(PerfCommand::Stop(StopTarget::Layer { .. })) | Command::Perf(PerfCommand::Eyes { .. }) => req.ack(Ack::Accepted),
                _ => req.ack(Ack::Accepted),
            }
        }
    });
}

// ------------------------------------------------------------------ driver

enum Input {
    Say(String),
    Cli(String),
}

/// The recorded inputs with their times (s after the first), so the replay keeps the
/// recording's pacing: what was still in flight when the next input came matters.
fn inputs(trace: &[Value]) -> Vec<(f64, Input)> {
    let v: Vec<(f64, Input)> = trace
        .iter()
        .filter_map(|r| {
            let p = &r["payload"];
            let t = r["t_mono"].as_f64()?;
            match r["topic"].as_str()? {
                "voice.listening.stopped" => Some((t, Input::Say(p["transcript"].as_str()?.into()))),
                "cli.command" if p.get("conversation_id").is_none_or(Value::is_null) => Some((t, Input::Cli(p["raw_input"].as_str()?.into()))),
                _ => None,
            }
        })
        .collect();
    let t0 = v.first().map_or(0.0, |x| x.0);
    v.into_iter().map(|(t, i)| (t - t0, i)).collect()
}

async fn replay(corpus: &str) -> (Vec<TurnSummary>, Vec<TurnSummary>, usize, usize) {
    let dir = repo().join("fixtures").join(corpus);
    let trace = load_trace(corpus);
    let bus = Bus::default();
    let library: Vec<String> = trace
        .iter()
        .find(|r| r["topic"] == "music.library.updated")
        .map(|r| r["payload"]["tracks"].as_object().unwrap().keys().cloned().collect())
        .unwrap();
    bus.update(Source::System, |m: &mut MusicState| m.library = library.clone());
    bus.update(Source::System, |s: &mut StageState| s.brain = true);

    let log: Arc<Mutex<Vec<Logged>>> = Arc::default();
    let mut all = bus.subscribe_all();
    let l = log.clone();
    tokio::spawn(async move {
        while let Some(Received::Message(env)) = all.recv().await {
            l.lock().unwrap().push((tokio::time::Instant::now(), env));
        }
    });

    let (chooser, picks) = replay_chooser(corpus);
    stub_voice(&bus);
    stub_music(&bus, picks);
    let catalog = r3x_brain::ShowCatalog::load(&repo().join("show"));
    stub_performer(&bus, catalog.kinds.keys().cloned().collect());

    let claude = Arc::new(ClaudeFixtures::load(&dir, 1.0).unwrap());
    let jev = Arc::new(JevFixtures::load(&dir, 1.0).unwrap());
    let claude_total = claude.remaining();
    let router = IntentRouter::new(JevClient::replay(jev), RouterConfig { api_key: "replay".into(), ..Default::default() });
    let deps = BrainDeps {
        llm: Some(LlmClient::replay(claude.clone(), "claude-sonnet-5-5")),
        router,
        memory: Some(Arc::new(r3x_memory::Memory::open_in_memory().unwrap())),
        latency: None,
        ptt: None,
        chooser,
    };
    let brain = Brain::spawn(&bus, BrainConfig::default(), deps).expect("intent class free");

    let mut starts = Vec::new();
    let t0 = tokio::time::Instant::now();
    for (n, (at, input)) in inputs(&trace).into_iter().enumerate() {
        tokio::time::sleep_until(t0 + Duration::from_secs_f64(at)).await;
        let marker = bus.stamp(Source::System, None, Body::Ack(Ack::Accepted)).seq;
        match input {
            Input::Say(t) => {
                starts.push((marker, format!("say {t}"), Some(tokio::time::Instant::now())));
                let turn = format!("parity-{n}");
                bus.publish(Source::System, Some(turn.clone()), Event::Conversation(ConversationEvent::ListeningStarted));
                bus.publish(Source::System, Some(turn), Event::Conversation(ConversationEvent::ListeningStopped { transcript: t }));
            }
            Input::Cli(c) => {
                starts.push((marker, format!("cli {c}"), None));
                let cmd = match c.split_whitespace().collect::<Vec<_>>().as_slice() {
                    ["show", id] => Command::Perf(PerfCommand::Play { id: id.to_string(), intensity: 1.0, speed: 1.0, layer: None }),
                    _ => Command::Intent(IntentCommand::Console { line: c.clone() }),
                };
                assert!(bus.command(Source::Cli, None, cmd).await.is_accepted());
            }
        }
    }
    tokio::time::sleep(Duration::from_secs(20)).await;
    brain.shutdown();
    let used = claude_total - claude.remaining();
    let log = log.lock().unwrap().clone();
    (cantina_turns(&trace), rust_turns(&log, &starts), used, claude_total)
}

#[tokio::test(start_paused = true)]
async fn smoke_voice_matches_cantina() {
    let (exp, act, used, total) = replay("smoke-voice").await;
    check("smoke-voice", exp, act);
    assert_eq!(used, total, "every recorded Claude request was made, byte for byte");
}

#[tokio::test(start_paused = true)]
async fn smoke_show_matches_cantina() {
    let (exp, act, used, total) = replay("smoke-show").await;
    check("smoke-show", exp, act);
    assert_eq!(used, total, "every recorded Claude request was made, byte for byte");
}

#[test]
fn normaliser_reads_the_recording() {
    let t = cantina_turns(&load_trace("smoke-voice"));
    assert_eq!(t.len(), 7);
    assert_eq!(t[0].intent.as_ref().map(|i| (i.0.as_str(), i.2.as_str())), Some(("play_music", "jev")));
    assert_eq!(t[0].actions, ["music.play(-)", "show(beat_bop,claude)"]);
}
