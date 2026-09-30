//! Profile, visit and timeline types, and their prompt rendering
//! (`memory_service.py` models; `claude_service.py` `_build_rich_memory_context`).

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct VisitSummary {
    pub visit_number: u32,
    pub date: String,
    pub duration_seconds: f64,
    pub summary: String,
    pub memorable_moments: Vec<String>,
    pub music_played: Vec<String>,
    pub topics_discussed: Vec<String>,
    pub mood: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct PersonProfile {
    pub name: String,
    pub visit_count: u32,
    /// Unix seconds.
    pub first_seen: f64,
    pub last_seen: f64,
    pub total_interaction_time_seconds: f64,
    pub personality_traits: Vec<String>,
    /// `favorite_genres`, `requests`, ...
    pub music_preferences: Map<String, Value>,
    pub conversation_style: Map<String, Value>,
    pub notable_facts: Vec<String>,
    /// Last 5 visits, oldest first.
    pub recent_visits: Vec<VisitSummary>,
    pub relationship_progression: String,
    pub preferences: Map<String, Value>,
    pub notes: Vec<String>,
    /// `conversation_summary`, `summary_updated_at`, `follow_up_opportunities`, ...
    pub metadata: Map<String, Value>,
}

impl Default for PersonProfile {
    fn default() -> Self {
        Self {
            name: String::new(),
            visit_count: 0,
            first_seen: 0.0,
            last_seen: 0.0,
            total_interaction_time_seconds: 0.0,
            personality_traits: vec![],
            music_preferences: Map::new(),
            conversation_style: Map::new(),
            notable_facts: vec![],
            recent_visits: vec![],
            relationship_progression: "stranger".into(),
            preferences: Map::new(),
            notes: vec![],
            metadata: Map::new(),
        }
    }
}

fn strs(v: Option<&Value>) -> Vec<String> {
    v.and_then(Value::as_array)
        .map(|a| a.iter().map(|x| x.as_str().map_or_else(|| x.to_string(), str::to_string)).collect())
        .unwrap_or_default()
}

impl PersonProfile {
    pub fn new(name: &str, now: f64) -> Self {
        Self { name: name.into(), first_seen: now, last_seen: now, ..Default::default() }
    }

    pub fn time_since_last_seen(&self, now: f64) -> String {
        let s = now - self.last_seen;
        if s < 60.0 {
            "just now".into()
        } else if s < 3600.0 {
            format!("{}m ago", (s / 60.0) as i64)
        } else if s < 86400.0 {
            format!("{}h ago", (s / 3600.0) as i64)
        } else {
            format!("{}d ago", (s / 86400.0) as i64)
        }
    }

    /// `[Name | N visits | Xh ago]` for `<system_observation>` ("Speaking with: ...").
    pub fn minimal_context(&self, now: f64) -> String {
        format!("[{} | {} visits | {}]", self.name, self.visit_count, self.time_since_last_seen(now))
    }

    pub fn add_visit(&mut self, v: VisitSummary) {
        self.recent_visits.push(v);
        let n = self.recent_visits.len();
        if n > 5 {
            self.recent_visits.drain(..n - 5);
        }
    }

    fn rich_memory(&self) -> String {
        let mut parts = Vec::new();
        if let Some(last) = self.recent_visits.last() {
            let mut x = format!("  <last_visit>\n    <summary>{}</summary>", last.summary);
            if !last.memorable_moments.is_empty() {
                let m: Vec<String> = last.memorable_moments.iter().take(2).map(|m| format!("<moment>{m}</moment>")).collect();
                x += &format!("\n    <memorable_moments>\n    {}\n    </memorable_moments>", m.join("\n    "));
            }
            x += "\n  </last_visit>";
            parts.push(x);
        }
        let tail = |v: &[String], n: usize| v[v.len().saturating_sub(n)..].to_vec();
        if !self.notable_facts.is_empty() {
            let f: Vec<String> = tail(&self.notable_facts, 3).iter().map(|f| format!("<fact>{f}</fact>")).collect();
            parts.push(format!("  <notable_facts>\n    {}\n  </notable_facts>", f.join("\n    ")));
        }
        if !self.personality_traits.is_empty() {
            let t: Vec<String> = tail(&self.personality_traits, 2).iter().map(|t| format!("<trait>{t}</trait>")).collect();
            parts.push(format!("  <personality>\n    {}\n  </personality>", t.join("\n    ")));
        }
        let mut music = Vec::new();
        let genres = strs(self.music_preferences.get("favorite_genres"));
        if !genres.is_empty() {
            music.push(format!("    <favorite_genres>{}</favorite_genres>", genres.iter().take(3).cloned().collect::<Vec<_>>().join(", ")));
        }
        if let Some(r) = strs(self.music_preferences.get("requests")).last() {
            music.push(format!("    <recent_request>{r}</recent_request>"));
        }
        if !music.is_empty() {
            parts.push(format!("  <music_preferences>\n{}\n  </music_preferences>", music.join("\n")));
        }
        let follow = strs(self.metadata.get("follow_up_opportunities"));
        if !follow.is_empty() {
            let i: Vec<String> = follow.iter().take(2).map(|f| format!("<item>{f}</item>")).collect();
            parts.push(format!("  <follow_up_opportunities>\n    {}\n  </follow_up_opportunities>", i.join("\n    ")));
        }
        if parts.is_empty() {
            return match self.metadata.get("conversation_summary").and_then(Value::as_str) {
                Some(l) if !l.is_empty() => format!("  <legacy_summary>{l}</legacy_summary>"),
                _ => String::new(),
            };
        }
        parts.join("\n")
    }

    /// Inner XML for `<person_memory>` (`TurnContext::person_memory`); `None` when there is
    /// nothing worth saying yet.
    pub fn person_memory(&self) -> Option<String> {
        let rich = self.rich_memory();
        (!rich.is_empty()).then(|| format!("<name>{}</name>\n<visit_count>{}</visit_count>\n{rich}", self.name, self.visit_count))
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EventKind {
    /// What the person said (final transcript). `data.text`.
    UserSaid,
    /// What R3X said (complete reply, not stream chunks). `data.text`.
    RexSaid,
    IntentResult,
    TrackPlaying,
    ModeChanged,
}

impl EventKind {
    pub fn as_str(self) -> &'static str {
        match self {
            EventKind::UserSaid => "user_said",
            EventKind::RexSaid => "rex_said",
            EventKind::IntentResult => "intent_result",
            EventKind::TrackPlaying => "track_playing",
            EventKind::ModeChanged => "mode_changed",
        }
    }
    /// CantinaOS `events.jsonl` `event_type` -> kind.
    pub fn from_cantina(t: &str) -> Option<Self> {
        Some(match t {
            "transcription.final" => EventKind::UserSaid,
            "llm.response.text" | "llm.response" => EventKind::RexSaid,
            "intent.execution.result" => EventKind::IntentResult,
            "track.playing" => EventKind::TrackPlaying,
            "system.mode.changed" => EventKind::ModeChanged,
            _ => return None,
        })
    }
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct TimelineEvent {
    pub ts: f64,
    pub kind: String,
    pub person: Option<String>,
    pub conversation_id: Option<String>,
    pub data: Value,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize)]
pub struct Turn {
    pub user: Option<String>,
    pub assistant: Option<String>,
    pub ts: f64,
    pub conversation_id: Option<String>,
}

/// `<conversation_history>` body (CantinaOS `_load_conversation_history_for_person`).
pub fn format_history(person: &str, turns: &[Turn]) -> Option<String> {
    let mut lines = Vec::new();
    for t in turns {
        if let Some(u) = t.user.as_deref().filter(|s| !s.is_empty()) {
            lines.push(format!("  User: {u}"));
        }
        if let Some(a) = t.assistant.as_deref().filter(|s| !s.is_empty()) {
            lines.push(format!("  You: {a}"));
        }
    }
    (!lines.is_empty()).then(|| format!("\n\n[Recent conversation history with {person}:\n{}\n]", lines.join("\n")))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn rendering() {
        let mut p = PersonProfile::new("Brandon", 1000.0);
        p.visit_count = 3;
        assert_eq!(p.minimal_context(1000.0 + 7300.0), "[Brandon | 3 visits | 2h ago]");
        assert_eq!(p.person_memory(), None);
        p.metadata.insert("conversation_summary".into(), json!("Likes cantina jazz."));
        assert!(p.person_memory().unwrap().ends_with("  <legacy_summary>Likes cantina jazz.</legacy_summary>"));
        p.add_visit(VisitSummary { summary: "Built a robot.".into(), memorable_moments: vec!["a".into(), "b".into(), "c".into()], ..Default::default() });
        p.notable_facts = vec!["f1".into(), "f2".into(), "f3".into(), "f4".into()];
        p.music_preferences.insert("requests".into(), json!(["Doshka", "Bai Tee Tee"]));
        let m = p.person_memory().unwrap();
        assert!(m.starts_with("<name>Brandon</name>\n<visit_count>3</visit_count>\n  <last_visit>\n    <summary>Built a robot.</summary>\n    <memorable_moments>\n    <moment>a</moment>\n    <moment>b</moment>\n    </memorable_moments>"));
        assert!(m.contains("<fact>f2</fact>") && !m.contains("<fact>f1</fact>"));
        assert!(m.contains("<recent_request>Bai Tee Tee</recent_request>") && !m.contains("legacy"));
        for i in 0..7 {
            p.add_visit(VisitSummary { visit_number: i, ..Default::default() });
        }
        assert_eq!(p.recent_visits.len(), 5);
        assert_eq!(p.recent_visits[0].visit_number, 2);
        let h = format_history("B", &[Turn { user: Some("hi".into()), assistant: Some("yo".into()), ..Default::default() }]).unwrap();
        assert_eq!(h, "\n\n[Recent conversation history with B:\n  User: hi\n  You: yo\n]");
    }
}
