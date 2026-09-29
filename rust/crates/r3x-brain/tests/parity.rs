//! Parity harness (plan §8): replay the Phase 0 corpora through the Rust brain with every
//! external stubbed (Jev and Claude from their fixtures, voice / music engine / performer as
//! bus stubs) and compare per-turn normalised traces against the CantinaOS recordings.
//!
//! A turn is compared on what it *did*: the router/Claude intent and its parameters, the
//! actions it caused (music, DJ, eyes, shows started, speech cached/played), the complete
//! reply and the lines spoken. Every recorded Jev and Claude request must be hit, which pins
//! the prompt bytes (`<action_already_taken>` with the real outcome, commentary prompts).
//! Timing: externals replay at their recorded pace on a paused clock, so the Rust brain's legs
//! (stop -> intent, -> first reply chunk, -> reply speech) are recorded external time plus
//! whatever the brain itself adds; each must be no slower than CantinaOS's within
//! max(300 ms, 30 %) (the trace tolerance). TTS is instant here, so Rust is usually faster.
//! Intended differences are listed in `ALLOWED` with their reason.

use std::collections::{HashMap, VecDeque};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use r3x_brain::{Brain, BrainConfig, BrainDeps, Chooser};
use r3x_bus::{Bus, Received};
use r3x_contracts::{
    Ack, Body, Command, ConversationEvent, DjEvent, Domain, EndReason, Envelope, Event, IntentCommand, MessageClass, MusicEvent,
    MusicState, PerfCommand, PerfEvent, RunKind, Source, StageState, StopTarget, Track,
};
use r3x_intent::{IntentRouter, JevClient, JevFixtures, RouterConfig};
use r3x_llm::{ClaudeFixtures, LlmClient};
use serde_json::Value;

/// (regex over a difference line, reason). Each is a §7b-style intended difference.
const ALLOWED: &[(&str, &str)] = &[];

fn repo() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../..")
}

#[derive(Debug, Default, Clone, PartialEq)]
struct TurnSummary {
    label: String,
    intent: Option<(String, Value, String)>,
    actions: Vec<String>,
    reply: Vec<String>,
    spoken: Vec<String>,
    /// ms from "transcript in hand" to: first intent, first reply chunk, reply speech start.
    timing: Vec<(&'static str, f64)>,
}

impl TurnSummary {
    fn finish(mut self) -> Self {
        self.actions.sort();
        self
    }
}

// ------------------------------------------------------------------ CantinaOS side

fn load_trace(corpus: &str) -> Vec<Value> {
    std::fs::read_to_string(repo().join("fixtures").join(corpus).join("trace.jsonl"))
        .unwrap()
        .lines()
        .filter(|l| !l.trim().is_empty())
        .map(|l| serde_json::from_str::<Value>(l).unwrap())
        .filter(|r| r.get("kind").is_none())
        .collect()
}

fn cantina_turns(trace: &[Value]) -> Vec<TurnSummary> {
    let mut turns: Vec<TurnSummary> = Vec::new();
    let mut show_lines: Vec<String> = Vec::new();
    let mut stops: Vec<Option<f64>> = Vec::new();
    for r in trace {
        let topic = r["topic"].as_str().unwrap_or("");
        let p = &r["payload"];
        let s = |k: &str| p.get(k).and_then(Value::as_str).map(str::to_owned);
        let top_cli = topic == "cli.command" && p.get("conversation_id").is_none_or(Value::is_null);
        if topic == "voice.listening.started" || top_cli {
            stops.push(None);
            turns.push(TurnSummary { label: s("raw_input").map(|c| format!("cli {c}")).unwrap_or_default(), ..Default::default() });
            continue;
        }
        let Some(t) = turns.last_mut() else { continue };
        let now = r["t_mono"].as_f64().unwrap_or(0.0);
        if let Some(leg) = match topic {
            "intent.detected" => Some("intent"),
            "llm.response" => Some("first_chunk"),
            "speech.generation.started" if p.get("conversation_id").is_some_and(|c| !c.is_null()) => Some("speech"),
            _ => None,
        } {
            if let Some(stop) = stops.last().copied().flatten() {
                if !t.timing.iter().any(|(l, _)| *l == leg) {
                    t.timing.push((leg, (now - stop) * 1000.0));
                }
            }
        }
        match topic {
            "voice.listening.stopped" => {
                t.label = format!("say {}", s("transcript").unwrap_or_default());
                *stops.last_mut().unwrap() = Some(now);
            }
            "intent.detected" => {
                let src = if s("source").as_deref() == Some("jev_fast_router") { "jev" } else { "claude" };
                t.intent = Some((s("intent_name").unwrap(), p["parameters"].clone(), src.into()));
            }
            "music.command" => {
                let act = s("action").or_else(|| s("command")).unwrap_or_default();
                let q = s("song_query").or_else(|| p["args"].get(0).and_then(Value::as_str).map(str::to_owned));
                t.actions.push(match act.as_str() {
                    "play" => format!("music.play({})", q.unwrap_or("-".into())),
                    "crossfade" => format!("music.crossfade({})", q.unwrap_or_default()),
                    a => format!("music.{a}"),
                });
            }
            "dj.mode.changed" => t.actions.push(if p["is_active"] == true { "dj.on" } else { "dj.off" }.into()),
            "eye.command" if !p["color"].is_null() => t.actions.push(format!("eyes({})", s("pattern").unwrap_or_default())),
            "show.started" => t.actions.push(format!("show({},{})", s("id").unwrap(), s("source").unwrap())),
            "speech.cache.request" => t.actions.push("cache_speech".into()),
            "speech.cache.playback.request" => t.actions.push("play_cached".into()),
            "llm.response" if p["is_complete"] == true && !s("text").unwrap_or_default().is_empty() => t.reply.push(s("text").unwrap()),
            "tts.generate.request" if s("clip_id").unwrap_or_default().starts_with("show-") => show_lines.push(s("text").unwrap()),
            "speech.generation.started" => t.spoken.push(s("text").unwrap_or_default()),
            _ => {}
        }
    }
    // Show lines are the performer's, not the brain's.
    for t in &mut turns {
        t.spoken.retain(|x| !show_lines.contains(x));
    }
    turns.into_iter().map(TurnSummary::finish).collect()
}

// ------------------------------------------------------------------ Rust side

type Logged = (tokio::time::Instant, Arc<Envelope>);

fn rust_turns(log: &[Logged], starts: &[(u64, String, Option<tokio::time::Instant>)]) -> Vec<TurnSummary> {
    let mut turns: Vec<TurnSummary> = starts.iter().map(|(_, l, _)| TurnSummary { label: l.clone(), ..Default::default() }).collect();
    let mut intents: HashMap<usize, (String, String)> = HashMap::new();
    for (at, env) in log {
        let Some(i) = starts.iter().rposition(|(seq, ..)| env.seq >= *seq) else { continue };
        let t = &mut turns[i];
        if let (Some(stop), Body::Event(Event::Conversation(c))) = (starts[i].2, &env.body) {
            let leg = match c {
                ConversationEvent::IntentDetected { .. } => Some("intent"),
                ConversationEvent::ReplyDelta { .. } => Some("first_chunk"),
                ConversationEvent::SpeechStarted if env.conversation_id.as_deref().is_some_and(|c| c.starts_with("parity-")) => Some("speech"),
                _ => None,
            };
            if let Some(leg) = leg.filter(|l| !t.timing.iter().any(|(x, _)| x == l)) {
                t.timing.push((leg, at.duration_since(stop).as_secs_f64() * 1000.0));
            }
        }
        let src = format!("{:?}", env.source).to_lowercase();
        match &env.body {
            Body::Event(e) => match e {
                Event::Conversation(c) => match c {
                    ConversationEvent::IntentDetected { tool, .. } => {
                        intents.insert(i, (tool.clone(), src));
                    }
                    ConversationEvent::ToolResult { tool, parameters, .. } if t.intent.is_none() => {
                        if let Some((it, s)) = intents.get(&i).filter(|(it, _)| it == tool) {
                            t.intent = Some((it.clone(), parameters.clone(), s.clone()));
                        }
                    }
                    ConversationEvent::Reply { text } if !text.is_empty() => t.reply.push(text.clone()),
                    ConversationEvent::Speak { text, .. } => t.spoken.push(text.clone()),
                    ConversationEvent::CacheSpeech { .. } => t.actions.push("cache_speech".into()),
                    ConversationEvent::PlayCached { .. } => t.actions.push("play_cached".into()),
                    _ => {}
                },
                Event::Music(MusicEvent::Play { query }) => t.actions.push(format!("music.play({})", query.as_deref().unwrap_or("-"))),
                Event::Music(MusicEvent::Stop) => t.actions.push("music.stop".into()),
                Event::Music(MusicEvent::Next) => t.actions.push("music.next".into()),
                Event::Music(MusicEvent::Crossfade { track, .. }) => t.actions.push(format!("music.crossfade({track})")),
                Event::Dj(DjEvent::Started) => t.actions.push("dj.on".into()),
                Event::Dj(DjEvent::Stopped) => t.actions.push("dj.off".into()),
                Event::Perf(PerfEvent::Started { id, source, .. }) => t.actions.push(format!("show({id},{})", format!("{source:?}").to_lowercase())),
                _ => {}
            },
            Body::Command(Command::Perf(PerfCommand::Eyes { pattern, .. })) if matches!(env.source, Source::Jev | Source::Claude) => {
                t.actions.push(format!("eyes({pattern})"))
            }
            _ => {}
        }
    }
    turns.into_iter().map(TurnSummary::finish).collect()
}

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

fn compare(exp: &[TurnSummary], act: &[TurnSummary]) -> Vec<String> {
    let mut diffs = Vec::new();
    if exp.len() != act.len() {
        diffs.push(format!("turn count: expected {}, got {}", exp.len(), act.len()));
    }
    for (i, (e, a)) in exp.iter().zip(act).enumerate() {
        let name = format!("turn {i} {}", e.label);
        if e.label != a.label {
            diffs.push(format!("{name}: label differs: {}", a.label));
            continue;
        }
        if e.intent != a.intent {
            diffs.push(format!("{name}: intent: expected {:?}, got {:?}", e.intent, a.intent));
        }
        if e.actions != a.actions {
            let missing: Vec<_> = e.actions.iter().filter(|x| !a.actions.contains(x)).collect();
            let extra: Vec<_> = a.actions.iter().filter(|x| !e.actions.contains(x)).collect();
            diffs.push(format!("{name}: actions: expected {:?}, got {:?} (missing {missing:?}, extra {extra:?})", e.actions, a.actions));
        }
        if e.reply != a.reply {
            diffs.push(format!("{name}: reply: expected {:?}, got {:?}", e.reply, a.reply));
        }
        if e.spoken != a.spoken {
            diffs.push(format!("{name}: spoken: expected {:?}, got {:?}", e.spoken, a.spoken));
        }
        for (leg, ms) in &e.timing {
            match a.timing.iter().find(|(l, _)| l == leg) {
                None => diffs.push(format!("{name}: timing {leg}: missing (expected {ms:.0} ms)")),
                Some((_, got)) if *got > ms + (0.3 * ms).max(300.0) => diffs.push(format!("{name}: timing {leg}: {got:.0} ms, slower than {ms:.0} ms")),
                _ => {}
            }
        }
    }
    diffs
}

fn check(corpus: &str, exp: Vec<TurnSummary>, act: Vec<TurnSummary>) {
    let diffs = compare(&exp, &act);
    if std::env::var("PARITY_SHOW").is_ok() {
        for (e, a) in exp.iter().zip(&act) {
            eprintln!("[{corpus}] {a:?}\n    cantina timing {:?}", e.timing);
        }
    }
    let allowed: Vec<(regex::Regex, &str)> = ALLOWED.iter().map(|(r, why)| (regex::Regex::new(r).unwrap(), *why)).collect();
    let mut bad = Vec::new();
    for d in &diffs {
        match allowed.iter().find(|(r, _)| r.is_match(d)) {
            Some((_, why)) => eprintln!("[{corpus}] ALLOWED {d}\n    because: {why}"),
            None => {
                eprintln!("[{corpus}] DIFF    {d}");
                bad.push(d.clone());
            }
        }
    }
    eprintln!("[{corpus}] {} turns compared; {} difference(s), {} not allow-listed", exp.len(), diffs.len(), bad.len());
    assert!(bad.is_empty(), "{corpus}: {bad:#?}");
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
