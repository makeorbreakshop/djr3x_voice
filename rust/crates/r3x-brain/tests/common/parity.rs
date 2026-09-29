//! The parity normaliser and comparison (plan §8), shared by `r3x-brain`'s replay harness
//! (`tests/parity.rs`) and the runtime's standalone acceptance test
//! (`r3x-runtime/tests/standalone_acceptance.rs`, which includes this file by path).
//!
//! A turn is compared on what it *did*: the router/Claude intent and its parameters, the
//! actions it caused (music, DJ, eyes, shows started, speech cached/played), the complete
//! reply and the lines spoken, plus latency legs from "transcript in hand" (stop -> intent,
//! -> first reply chunk, -> the reply's first audible sample), each no slower than
//! CantinaOS's within max(300 ms, 30 %).
#![allow(dead_code)]

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;

use r3x_contracts::{Body, Command, ConversationEvent, DjEvent, Envelope, Event, MusicEvent, PerfCommand, PerfEvent, Source};
use serde_json::Value;

pub fn repo() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../..")
}

/// (regex over a difference line, reason). Each is a §7b-style intended difference.
pub const ALLOWED: &[(&str, &str)] = &[(
    r#"make your eyes red: actions: expected \["eyes\(solid\)"\], got \["eyes\(flash\)"\]"#,
    "the face firmware has no colour and no `solid` word (the board refused it); a colour request flashes the eyes",
)];


#[derive(Debug, Default, Clone, PartialEq)]
pub struct TurnSummary {
    pub label: String,
    pub intent: Option<(String, Value, String)>,
    pub actions: Vec<String>,
    pub reply: Vec<String>,
    pub spoken: Vec<String>,
    /// ms from "transcript in hand" to: first intent, first reply chunk, reply speech start.
    pub timing: Vec<(&'static str, f64)>,
}

impl TurnSummary {
    fn finish(mut self) -> Self {
        self.actions.sort();
        self
    }
}

// ------------------------------------------------------------------ CantinaOS side

pub fn load_trace(corpus: &str) -> Vec<Value> {
    std::fs::read_to_string(repo().join("fixtures").join(corpus).join("trace.jsonl"))
        .unwrap()
        .lines()
        .filter(|l| !l.trim().is_empty())
        .map(|l| serde_json::from_str::<Value>(l).unwrap())
        .filter(|r| r.get("kind").is_none())
        .collect()
}

pub fn cantina_turns(trace: &[Value]) -> Vec<TurnSummary> {
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

pub type Logged = (tokio::time::Instant, Arc<Envelope>);

pub fn rust_turns(log: &[Logged], starts: &[(u64, String, Option<tokio::time::Instant>)]) -> Vec<TurnSummary> {
    let mut turns: Vec<TurnSummary> = starts.iter().map(|(_, l, _)| TurnSummary { label: l.clone(), ..Default::default() }).collect();
    let mut intents: HashMap<usize, (String, String)> = HashMap::new();
    let mut ids: HashMap<usize, String> = HashMap::new();
    for (at, env) in log {
        let Some(i) = starts.iter().rposition(|(seq, ..)| env.seq >= *seq) else { continue };
        if let (Body::Event(Event::Conversation(ConversationEvent::ListeningStopped { .. })), Some(c)) = (&env.body, &env.conversation_id) {
            ids.entry(i).or_insert_with(|| c.clone());
        }
        let t = &mut turns[i];
        if let (Some(stop), Body::Event(Event::Conversation(c))) = (starts[i].2, &env.body) {
            let leg = match c {
                ConversationEvent::IntentDetected { .. } => Some("intent"),
                ConversationEvent::ReplyDelta { .. } => Some("first_chunk"),
                ConversationEvent::SpeechStarted if env.conversation_id.is_some() && env.conversation_id == ids.get(&i).cloned() => Some("speech"),
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


pub fn compare(exp: &[TurnSummary], act: &[TurnSummary]) -> Vec<String> {
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

pub fn check(corpus: &str, exp: Vec<TurnSummary>, act: Vec<TurnSummary>) {
    check_allowing(corpus, exp, act, &[]);
}

/// [`check`] with further intended differences (a harness's own, beside [`ALLOWED`]).
pub fn check_allowing(corpus: &str, exp: Vec<TurnSummary>, act: Vec<TurnSummary>, extra: &[(&str, &str)]) {
    let diffs = compare(&exp, &act);
    if std::env::var("PARITY_SHOW").is_ok() {
        for (e, a) in exp.iter().zip(&act) {
            eprintln!("[{corpus}] {a:?}\n    cantina timing {:?}", e.timing);
        }
    }
    let allowed: Vec<(regex::Regex, &str)> = ALLOWED.iter().chain(extra).map(|(r, why)| (regex::Regex::new(r).unwrap(), *why)).collect();
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

