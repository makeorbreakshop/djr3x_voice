//! Per-turn latency legs (`latency_tracker_service.py`, keyed the way it was meant to be: on
//! the capture's turn id), published as `ops.latency {leg, ms}` with the turn's
//! `conversation_id`, and summarised for `debug latency`.
//!
//! Legs, all from the bus's own stamps (`t_mono`), "stop" = transcript in hand:
//! `listening` (start -> stop), `stop_to_intent`, `stop_to_action` (first music/DJ/eye/show
//! request of the turn), `stop_to_first_chunk`, `stop_to_reply`, `reply_to_audible`,
//! `stop_to_audible` (first audible sample of the reply), `speech` (audible -> ended).

use std::collections::{BTreeMap, VecDeque};
use std::sync::{Arc, Mutex};

use r3x_bus::{Bus, Received};
use r3x_contracts::{Body, Command, ConversationEvent, DjEvent, Envelope, Event, IntentCommand, MusicEvent, OpsEvent, PerfCommand, Source};

pub const LEGS: &[&str] = &[
    "listening",
    "stop_to_intent",
    "stop_to_action",
    "stop_to_first_chunk",
    "stop_to_reply",
    "reply_to_audible",
    "stop_to_audible",
    "speech",
];
const MAX_TURNS: usize = 100;

#[derive(Debug, Default, Clone)]
pub struct Turn {
    pub id: String,
    pub label: String,
    started: Option<f64>,
    stopped: Option<f64>,
    reply: Option<f64>,
    audible: Option<f64>,
    /// leg -> ms
    pub legs: BTreeMap<&'static str, f64>,
}

#[derive(Default)]
pub struct LatencyTracker {
    turns: Mutex<VecDeque<Turn>>,
}

impl LatencyTracker {
    /// Follow every turn on `bus`.
    pub fn spawn(bus: &Bus) -> Arc<Self> {
        let me = Arc::new(Self::default());
        let (t, bus) = (me.clone(), bus.clone());
        let mut rx = bus.subscribe_all();
        tokio::spawn(async move {
            while let Some(m) = rx.recv().await {
                if let Received::Message(env) = m {
                    for (cid, leg, ms) in t.observe(&env) {
                        bus.publish(Source::System, Some(cid), Event::Ops(OpsEvent::Latency { leg: leg.into(), ms }));
                    }
                }
            }
        });
        me
    }

    /// Feed one envelope; returns the legs it completed.
    pub fn observe(&self, env: &Envelope) -> Vec<(String, &'static str, f64)> {
        let Some(cid) = env.conversation_id.as_deref() else { return vec![] };
        let t = env.t_mono;
        let mut turns = self.turns.lock().unwrap();
        if matches!(&env.body, Body::Event(Event::Conversation(ConversationEvent::ListeningStarted))) {
            turns.retain(|x| x.id != cid);
            turns.push_back(Turn { id: cid.into(), started: Some(t), ..Default::default() });
            while turns.len() > MAX_TURNS {
                turns.pop_front();
            }
            return vec![];
        }
        let Some(turn) = turns.iter_mut().rev().find(|x| x.id == cid) else { return vec![] };
        let mut out = Vec::new();
        let (started, stopped) = (turn.started, turn.stopped);
        let mut leg = |turn: &mut Turn, name: &'static str, from: Option<f64>| {
            if let (Some(f), false) = (from, turn.legs.contains_key(name)) {
                let ms = ((t - f) * 1000.0 * 10.0).round() / 10.0;
                turn.legs.insert(name, ms);
                out.push((cid.to_string(), name, ms));
            }
        };
        match &env.body {
            Body::Event(Event::Conversation(e)) => match e {
                ConversationEvent::ListeningStopped { transcript } => {
                    if turn.stopped.is_none() {
                        turn.stopped = Some(t);
                        turn.label = transcript.clone();
                        leg(turn, "listening", started);
                    }
                }
                ConversationEvent::IntentDetected { .. } => leg(turn, "stop_to_intent", stopped),
                ConversationEvent::ReplyDelta { .. } => leg(turn, "stop_to_first_chunk", stopped),
                ConversationEvent::Reply { .. } => {
                    turn.reply.get_or_insert(t);
                    leg(turn, "stop_to_reply", stopped);
                }
                ConversationEvent::SpeechStarted => {
                    let reply = turn.reply;
                    if reply.is_some() && turn.audible.is_none() {
                        turn.audible = Some(t);
                        leg(turn, "reply_to_audible", reply);
                        leg(turn, "stop_to_audible", stopped);
                    }
                }
                ConversationEvent::SpeechEnded => {
                    let audible = turn.audible;
                    leg(turn, "speech", audible)
                }
                _ => {}
            },
            Body::Event(Event::Music(MusicEvent::Play { .. } | MusicEvent::Stop | MusicEvent::Next))
            | Body::Event(Event::Dj(DjEvent::Started | DjEvent::Stopped))
            | Body::Command(Command::Perf(PerfCommand::Eyes { .. } | PerfCommand::Play { .. }))
            | Body::Command(Command::Intent(IntentCommand::Music(_) | IntentCommand::Dj { .. })) => leg(turn, "stop_to_action", stopped),
            _ => {}
        }
        out
    }

    pub fn turns(&self) -> Vec<Turn> {
        self.turns.lock().unwrap().iter().cloned().collect()
    }

    /// Medians per leg over the recorded turns, then the last few turns.
    pub fn report(&self) -> String {
        let turns = self.turns();
        let done: Vec<&Turn> = turns.iter().filter(|t| t.stopped.is_some()).collect();
        if done.is_empty() {
            return "No latency data available yet. Interact with the system to collect metrics.".into();
        }
        let mut lines = vec![format!("=== Pipeline Latency ({} turns, ms) ===", done.len())];
        for leg in LEGS {
            let mut v: Vec<f64> = done.iter().filter_map(|t| t.legs.get(leg).copied()).collect();
            if v.is_empty() {
                continue;
            }
            v.sort_by(f64::total_cmp);
            let med = if v.len() % 2 == 1 { v[v.len() / 2] } else { (v[v.len() / 2 - 1] + v[v.len() / 2]) / 2.0 };
            lines.push(format!("{leg:<20} median {med:>7.0}  (n={})", v.len()));
        }
        lines.push(String::new());
        for t in done.iter().rev().take(5) {
            let legs: Vec<String> = LEGS.iter().filter_map(|l| t.legs.get(l).map(|ms| format!("{l}={ms:.0}"))).collect();
            lines.push(format!("\"{}\": {}", t.label, legs.join(" ")));
        }
        lines.join("\n")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn legs_per_turn_on_the_bus_clock() {
        let bus = Bus::default();
        let tr = LatencyTracker::default();
        let conv = |e| Event::Conversation(e);
        let feed = |cid: Option<&str>, e: Event| {
            let env = bus.stamp(Source::System, cid.map(str::to_owned), Body::Event(e));
            tr.observe(&env)
        };
        feed(Some("t1"), conv(ConversationEvent::ListeningStarted));
        feed(Some("other"), conv(ConversationEvent::ReplyDelta { text: "x".into() }));
        feed(Some("t1"), conv(ConversationEvent::ListeningStopped { transcript: "play music".into() }));
        feed(Some("t1"), Event::Music(MusicEvent::Play { query: None }));
        feed(Some("t1"), conv(ConversationEvent::ReplyDelta { text: "Oh".into() }));
        feed(Some("t1"), conv(ConversationEvent::ReplyDelta { text: " yeah".into() }));
        feed(Some("t1"), conv(ConversationEvent::Reply { text: "Oh yeah".into() }));
        let out = feed(Some("t1"), conv(ConversationEvent::SpeechStarted));
        assert_eq!(out.iter().map(|(_, l, _)| *l).collect::<Vec<_>>(), ["reply_to_audible", "stop_to_audible"]);
        feed(Some("t1"), conv(ConversationEvent::SpeechEnded));
        let t = &tr.turns()[0];
        for leg in ["listening", "stop_to_action", "stop_to_first_chunk", "stop_to_reply", "stop_to_audible", "speech"] {
            assert!(t.legs.get(leg).is_some_and(|ms| *ms >= 0.0), "{leg}");
        }
        assert!(!t.legs.contains_key("stop_to_intent"));
        assert!(tr.report().contains("\"play music\""));
        assert!(LatencyTracker::default().report().starts_with("No latency data"));
    }
}
