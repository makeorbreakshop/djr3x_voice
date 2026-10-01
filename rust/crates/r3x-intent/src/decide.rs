//! The three-read decision rule and risk tiers (`jev_intents.decide`).

use serde::de::{MapAccess, Visitor};
use serde::{Deserialize, Deserializer, Serialize};
use serde_json::{json, Map, Value};

use std::collections::HashMap;

use crate::catalogue::*;

/// Probabilities in the order Jev sent them (ties in `top_two` resolve by that order).
#[derive(Debug, Clone, Default, PartialEq, Serialize)]
pub struct Probabilities(pub Vec<(String, f64)>);

impl<'de> Deserialize<'de> for Probabilities {
    fn deserialize<D: Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        struct V;
        impl<'de> Visitor<'de> for V {
            type Value = Probabilities;
            fn expecting(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
                f.write_str("a map of probabilities or null")
            }
            fn visit_map<A: MapAccess<'de>>(self, mut m: A) -> Result<Self::Value, A::Error> {
                let mut v = Vec::new();
                while let Some((k, p)) = m.next_entry::<String, Option<f64>>()? {
                    v.push((k, p.unwrap_or(0.0)));
                }
                Ok(Probabilities(v))
            }
            fn visit_unit<E>(self) -> Result<Self::Value, E> {
                Ok(Probabilities::default())
            }
        }
        d.deserialize_any(V)
    }
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct JevAnswer {
    #[serde(default, rename = "type")]
    pub kind: String,
    #[serde(default)]
    pub noul: Option<f64>,
    #[serde(default)]
    pub score: Option<i64>,
    #[serde(default)]
    pub choice: Option<String>,
    #[serde(default)]
    pub confidence: Option<f64>,
    #[serde(default)]
    pub probabilities: Probabilities,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct JevResult {
    #[serde(default)]
    pub model: String,
    #[serde(default)]
    pub answers: HashMap<String, JevAnswer>,
    #[serde(skip)]
    pub input_tokens: u64,
    #[serde(skip)]
    pub output_tokens: u64,
    #[serde(skip)]
    pub latency_ms: f64,
}

impl JevResult {
    /// Parse a response body from its text (not a `Value`: probability order must survive).
    pub fn from_json(body: &str, latency_ms: f64) -> Option<Self> {
        #[derive(Deserialize)]
        struct U {
            #[serde(default)]
            input_tokens: u64,
            #[serde(default)]
            output_tokens: u64,
        }
        #[derive(Deserialize)]
        struct B {
            #[serde(default)]
            usage: Option<U>,
        }
        let mut r: JevResult = serde_json::from_str(body).ok()?;
        if let Some(u) = serde_json::from_str::<B>(body).ok().and_then(|b| b.usage) {
            r.input_tokens = u.input_tokens;
            r.output_tokens = u.output_tokens;
        }
        r.latency_ms = latency_ms;
        Some(r)
    }
    /// Probability that question `key` is true, or `default` when unanswered.
    pub fn noul(&self, key: &str, default: f64) -> f64 {
        self.answers.get(key).and_then(|a| a.noul).unwrap_or(default)
    }
    /// A choice question's label and its confidence (`(None, 0.0)` when absent).
    pub fn choice(&self, key: &str) -> (Option<&str>, f64) {
        self.choice_of(key)
    }
    fn choice_of(&self, key: &str) -> (Option<&str>, f64) {
        self.answers.get(key).map_or((None, 0.0), |a| (a.choice.as_deref(), a.confidence.unwrap_or(0.0)))
    }
}

pub const DEFAULT_THRESHOLD: f64 = 0.85;
pub const DEFAULT_CHEAP_NOUL: f64 = 0.7;
pub const FREE_TIER_RELAXATION: f64 = 0.10;
pub const DEFAULT_FREE_NOUL: f64 = 0.5;
pub const DEFAULT_COMMAND_THRESHOLD: f64 = 0.5;
/// Instantly reversible. Fire eagerly.
pub const FREE_TIER: &[&str] = &["set_eye_animation", "next_track"];
/// Reversible but noticeable in the room.
pub const CHEAP_TIER: &[&str] = &["play_music", "stop_music", "dj_mode_on", "dj_mode_off"];
/// Writes shared state; never fired from the router alone. Empty today - a guard rail.
pub const COMMITTING_TIER: &[&str] = &[];

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Gates {
    pub confidence: f64,
    pub noul: f64,
    pub command: f64,
}

/// `None` = committing: never fires from the router alone. The free tier is derived from the
/// cheap threshold so one knob moves both.
pub fn tier_gate(intent: &str, threshold: f64, command: f64) -> Option<Gates> {
    if COMMITTING_TIER.contains(&intent) {
        return None;
    }
    Some(if FREE_TIER.contains(&intent) {
        Gates { confidence: threshold - FREE_TIER_RELAXATION, noul: DEFAULT_FREE_NOUL, command }
    } else {
        Gates { confidence: threshold, noul: DEFAULT_CHEAP_NOUL, command }
    })
}

#[derive(Debug, Clone, Default, PartialEq, Serialize)]
pub struct RouterDecision {
    /// The winning tool, or `None`: nothing dispatched, Claude owns the turn.
    pub intent: Option<String>,
    pub parameters: Map<String, Value>,
    /// `min(choice confidence, own noul)`.
    pub confidence: f64,
    pub choice: Option<String>,
    pub choice_confidence: f64,
    pub noul: f64,
    pub is_command: f64,
    /// "free" | "cheap" | "committing" | "".
    pub tier: String,
    pub probabilities: Probabilities,
    pub reason: String,
}

impl RouterDecision {
    pub fn should_execute(&self) -> bool {
        self.intent.is_some()
    }
    /// Top two choice options as `name (0.62)`: the hint for a mid-confidence decline.
    pub fn top_two(&self) -> Vec<String> {
        let mut ranked = self.probabilities.0.clone();
        ranked.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
        ranked.iter().take(2).map(|(n, p)| format!("{n} ({p:.2})")).collect()
    }
}

/// Parameters the executor needs for `intent`, or `None` to decline (e.g. eyes with no colour).
pub fn extract_parameters(intent: &str, utterance: &str, result: Option<&JevResult>) -> Option<Map<String, Value>> {
    let obj = |v: Value| v.as_object().cloned();
    match intent {
        "play_music" => {
            let fallback = obj(json!({"track": naming_phrase(utterance)}));
            let Some(r) = result else { return fallback };
            let (kind, kind_conf) = r.choice_of("music_request_kind");
            if kind_conf < MUSIC_KIND_MIN_CONFIDENCE {
                return fallback;
            }
            match kind {
                Some("generic_music") => return obj(json!({"track": null})),
                Some("semantic_vibe") => {}
                _ => return fallback,
            }
            let (vibe, vibe_conf) = r.choice_of("music_positive_vibe");
            let vibe_query = vibe.and_then(|v| MUSIC_VIBE_QUERIES.iter().find(|(k, _)| *k == v)).map(|(_, q)| *q);
            let avoid = r.noul("music_avoid_heavy_aggressive", 0.0) >= MUSIC_AVOID_MIN_NOUL;
            let mut query = semantic_query_from_utterance(utterance, avoid);
            if query.is_empty() {
                match vibe_query {
                    Some(q) if vibe_conf >= MUSIC_VIBE_MIN_CONFIDENCE => query = q.into(),
                    _ => return fallback,
                }
            }
            let negative = avoid.then_some(MUSIC_NEGATIVE_HEAVY_QUERY);
            obj(json!({"track": encode_semantic_request(&query, negative)}))
        }
        "stop_music" | "next_track" | "dj_mode_on" | "dj_mode_off" => Some(Map::new()),
        "set_eye_animation" => {
            let color = word_search(utterance, EYE_COLORS.iter().copied())?;
            let pattern = word_search(utterance, EYE_PATTERNS.iter().map(|(k, _)| *k))
                .and_then(|w| EYE_PATTERNS.iter().find(|(k, _)| *k == w))
                .map_or("solid", |(_, p)| *p);
            obj(json!({"color": color, "pattern": pattern}))
        }
        _ => None,
    }
}

/// A tool fires only when: the Choice picked it; it is not committing; `is_a_command` clears the
/// command gate (defaults to 1.0 when unanswered); choice confidence *and* its own noul clear
/// its tier; and its parameters can be extracted. Anything else hands the turn to Claude.
pub fn decide(result: Option<&JevResult>, utterance: &str, threshold: f64, command_threshold: f64) -> RouterDecision {
    let Some(r) = result else {
        return RouterDecision { reason: "no Jev result (classifier unavailable)".into(), ..Default::default() };
    };
    let ia = r.answers.get("intent");
    let choice = ia.and_then(|a| a.choice.clone()).filter(|c| !c.is_empty());
    let choice_confidence = ia.and_then(|a| a.confidence).unwrap_or(0.0);
    let probabilities = ia.map(|a| a.probabilities.clone()).unwrap_or_default();
    let is_command = r.noul("is_a_command", 1.0);
    let base = RouterDecision {
        choice: choice.clone(),
        choice_confidence,
        is_command,
        probabilities,
        ..Default::default()
    };
    let declined = |reason: String, confidence: f64, noul: f64, tier: &str| RouterDecision {
        reason,
        confidence,
        noul,
        tier: tier.into(),
        ..base.clone()
    };

    let Some(choice) = choice else {
        return declined("Jev returned no intent choice".into(), 0.0, 0.0, "");
    };
    if NO_TOOL_INTENTS.contains(&choice.as_str()) {
        return declined(format!("classified as {choice}; no action"), choice_confidence, 0.0, "");
    }
    if !TOOL_INTENTS.contains(&choice.as_str()) {
        return declined(format!("unknown intent '{choice}'; no action"), choice_confidence, 0.0, "");
    }
    let Some(g) = tier_gate(&choice, threshold, command_threshold) else {
        return declined(format!("'{choice}' is a committing action; never fired from the router alone"), 0.0, 0.0, "committing");
    };
    let tier = if FREE_TIER.contains(&choice.as_str()) { "free" } else { "cheap" };
    let own = r.noul(&choice, 0.0);
    let confidence = choice_confidence.min(own);

    if is_command < g.command {
        return declined(
            format!("'{choice}' vetoed by command gate (is_a_command={is_command:.2} < {:.2}); conversation, not an order", g.command),
            confidence, own, tier,
        );
    }
    if choice_confidence < g.confidence || own < g.noul {
        return declined(
            format!(
                "'{choice}' below {tier}-tier gate (choice={choice_confidence:.2} vs {:.2}, noul={own:.2} vs {:.2})",
                g.confidence, g.noul
            ),
            confidence, own, tier,
        );
    }
    let Some(parameters) = extract_parameters(&choice, utterance, Some(r)) else {
        return declined(
            format!("'{choice}' cleared its gate at {confidence:.2} but required parameters are missing from the utterance"),
            confidence, own, tier,
        );
    };
    RouterDecision {
        intent: Some(choice.clone()),
        parameters,
        confidence,
        noul: own,
        tier: tier.into(),
        reason: format!("'{choice}' cleared the {tier}-tier gate (choice={choice_confidence:.2}, noul={own:.2})"),
        ..base
    }
}
