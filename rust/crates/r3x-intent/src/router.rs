//! The router service logic minus the bus (`services/jev_intent_service.py`): config from env,
//! speculative classification of partial transcripts, and the per-turn classify + decide.

use serde_json::Value;
use std::collections::VecDeque;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use crate::catalogue::{build_questions, build_state};
use crate::client::{JevClient, DEFAULT_TIMEOUT};
use crate::decide::{decide, JevResult, RouterDecision, DEFAULT_COMMAND_THRESHOLD, DEFAULT_THRESHOLD};

pub const MIN_SPECULATIVE_WORDS: usize = 2;
pub const SPECULATION_DEBOUNCE: Duration = Duration::from_millis(150);
pub const MAX_SPECULATIVE: usize = 8;

/// Env knobs (CLAUDE.md §9).
#[derive(Debug, Clone)]
pub struct RouterConfig {
    pub api_key: String,
    /// `JEV_ROUTER_ENABLED` (true).
    pub enabled: bool,
    /// `JEV_CONFIDENCE_THRESHOLD` (0.85): cheap tier; free = this - 0.10.
    pub threshold: f64,
    /// `JEV_COMMAND_THRESHOLD` (0.5).
    pub command_threshold: f64,
    /// `JEV_TIMEOUT_S` (0.8).
    pub timeout: Duration,
    /// `JEV_SPECULATE` (true).
    pub speculate: bool,
    /// `FAST_ROUTER_WAIT_S` (1.2): Claude side's verdict wait.
    pub verdict_wait: Duration,
    /// `FAST_ROUTER_OUTCOME_WAIT_S` (1.2): Claude side's outcome wait.
    pub outcome_wait: Duration,
}

impl Default for RouterConfig {
    fn default() -> Self {
        Self {
            api_key: String::new(),
            enabled: true,
            threshold: DEFAULT_THRESHOLD,
            command_threshold: DEFAULT_COMMAND_THRESHOLD,
            timeout: DEFAULT_TIMEOUT,
            speculate: true,
            verdict_wait: Duration::from_millis(1200),
            outcome_wait: Duration::from_millis(1200),
        }
    }
}

impl RouterConfig {
    pub fn from_lookup(get: impl Fn(&str) -> Option<String>) -> Self {
        let d = Self::default();
        let f = |k: &str, dv: f64| get(k).and_then(|v| v.trim().parse::<f64>().ok()).unwrap_or(dv);
        let b = |k: &str, dv: bool| {
            get(k).map_or(dv, |v| !matches!(v.trim().to_lowercase().as_str(), "0" | "false" | "no" | "off" | ""))
        };
        let secs = |k: &str, dv: Duration| Duration::from_secs_f64(f(k, dv.as_secs_f64()).max(0.0));
        Self {
            api_key: get("TYPESAFE_API_KEY").unwrap_or_default(),
            enabled: b("JEV_ROUTER_ENABLED", d.enabled),
            threshold: f("JEV_CONFIDENCE_THRESHOLD", d.threshold),
            command_threshold: f("JEV_COMMAND_THRESHOLD", d.command_threshold),
            timeout: secs("JEV_TIMEOUT_S", d.timeout),
            speculate: b("JEV_SPECULATE", d.speculate),
            verdict_wait: secs("FAST_ROUTER_WAIT_S", d.verdict_wait),
            outcome_wait: secs("FAST_ROUTER_OUTCOME_WAIT_S", d.outcome_wait),
        }
    }

    pub fn from_env() -> Self {
        Self::from_lookup(|k| std::env::var(k).ok())
    }
}

/// Speculation cache key: casing and punctuation must not cause a miss.
pub fn normalize(text: &str) -> String {
    text.to_lowercase().chars().filter(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || *c == ' ').collect::<String>().trim().to_string()
}

#[derive(Default)]
struct Spec {
    turn: u64,
    cache: VecDeque<(String, JevResult)>,
    last_at: Option<Instant>,
    last_text: String,
}

#[derive(Debug, Clone)]
pub struct TurnOutcome {
    pub decision: RouterDecision,
    /// Classification was already done off a partial transcript.
    pub speculative_hit: bool,
    /// `None` when Jev was unavailable (timeout, HTTP error, malformed).
    pub result: Option<JevResult>,
    /// Transcript -> decision, this process.
    pub elapsed: Duration,
}

pub struct IntentRouter {
    client: JevClient,
    cfg: RouterConfig,
    questions: Value,
    spec: Mutex<Spec>,
}

impl IntentRouter {
    pub fn new(client: JevClient, cfg: RouterConfig) -> Arc<Self> {
        Arc::new(Self { client, cfg, questions: build_questions(), spec: Mutex::default() })
    }

    pub fn from_env() -> Arc<Self> {
        let cfg = RouterConfig::from_env();
        Self::new(JevClient::new(&cfg.api_key, cfg.timeout), cfg)
    }

    pub fn config(&self) -> &RouterConfig {
        &self.cfg
    }

    /// Enabled and keyed. When false: register nothing on the gate; every turn is Claude's.
    pub fn active(&self) -> bool {
        self.cfg.enabled && self.client.configured()
    }

    pub async fn prewarm(&self) -> bool {
        self.active() && self.client.prewarm().await
    }

    fn spec(&self) -> std::sync::MutexGuard<'_, Spec> {
        self.spec.lock().unwrap_or_else(|p| p.into_inner())
    }

    /// New utterance (`voice.listening.started`): drop last turn's speculative answers.
    pub fn turn_started(&self) {
        let mut s = self.spec();
        s.turn += 1;
        s.cache.clear();
        s.last_text.clear();
    }

    /// A partial transcript. `is_final` (a finalised STT segment) skips the 150 ms debounce;
    /// interims are debounced. Classifies in the background; never acts on its own - only a
    /// matching authoritative transcript can dispatch.
    pub fn partial(self: &Arc<Self>, text: &str, is_final: bool) {
        if !self.active() || !self.cfg.speculate {
            return;
        }
        let text = text.trim().to_string();
        let key = normalize(&text);
        if key.is_empty() || key.split_whitespace().count() < MIN_SPECULATIVE_WORDS {
            return;
        }
        let turn = {
            let mut s = self.spec();
            if s.cache.iter().any(|(k, _)| *k == key) || s.last_text == key {
                return;
            }
            let now = Instant::now();
            if !is_final && s.last_at.is_some_and(|t| now.duration_since(t) < SPECULATION_DEBOUNCE) {
                return;
            }
            s.last_at = Some(now);
            s.last_text = key.clone();
            s.turn
        };
        let me = self.clone();
        tokio::spawn(async move {
            let Some(result) = me.client.classify(&build_state(&text), &me.questions).await else { return };
            let mut s = me.spec();
            if s.turn != turn {
                return; // the turn moved on
            }
            if s.cache.len() >= MAX_SPECULATIVE {
                s.cache.pop_front();
            }
            tracing::debug!("Speculative Jev answer cached for '{}' in {:.0} ms", text.chars().take(40).collect::<String>(), result.latency_ms);
            s.cache.push_back((key, result));
        });
    }

    /// The hot path: the authoritative transcript. Uses a speculative answer when one matches,
    /// else one live call. Logs every decline with the full probability map (the eval set).
    pub async fn classify_turn(&self, transcript: &str) -> TurnOutcome {
        let started = Instant::now();
        let cached = {
            let key = normalize(transcript);
            self.spec().cache.iter().find(|(k, _)| *k == key).map(|(_, r)| r.clone())
        };
        let speculative_hit = cached.is_some();
        let result = match cached {
            Some(r) => {
                tracing::info!("Jev speculative cache hit for '{}' - classification already done", transcript.chars().take(48).collect::<String>());
                Some(r)
            }
            None if self.active() => self.client.classify(&build_state(transcript), &self.questions).await,
            None => None,
        };
        let decision = decide(result.as_ref(), transcript, self.cfg.threshold, self.cfg.command_threshold);
        let elapsed = started.elapsed();
        match (&result, decision.intent.as_deref()) {
            (None, _) => tracing::info!("Jev classification unavailable; handing turn to Claude ({} ms elapsed)", elapsed.as_millis()),
            (Some(_), None) => tracing::info!(
                "Jev declined: {} | utterance='{}' | top2={:?} | is_a_command={:.2} | probabilities={:?}",
                decision.reason, transcript, decision.top_two(), decision.is_command, decision.probabilities.0
            ),
            (Some(_), Some(i)) => tracing::info!(
                "Jev dispatched '{}' in {:.1} ms ({} tier, choice={:.2}, noul={:.2}) params={}",
                i, elapsed.as_secs_f64() * 1000.0, decision.tier, decision.choice_confidence, decision.noul, serde_json::Value::Object(decision.parameters.clone())
            ),
        }
        TurnOutcome { decision, speculative_hit, result, elapsed }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    #[test]
    fn config_and_normalize() {
        let m: HashMap<&str, &str> = [("JEV_CONFIDENCE_THRESHOLD", "0.9"), ("JEV_SPECULATE", "false"), ("FAST_ROUTER_WAIT_S", "0.5")].into();
        let c = RouterConfig::from_lookup(|k| m.get(k).map(|v| v.to_string()));
        assert_eq!((c.threshold, c.speculate, c.enabled, c.verdict_wait), (0.9, false, true, Duration::from_millis(500)));
        assert_eq!(c.timeout, Duration::from_millis(800));
        assert_eq!(normalize("  Play, the NEXT track! "), "play the next track");
    }
}
