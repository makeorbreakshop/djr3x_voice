//! Summary prompts (verbatim from `memory_service.py`) and applying a structured visit
//! extraction to a profile.

use serde_json::{json, Value};

use crate::model::{PersonProfile, Turn, VisitSummary};

pub const ROLLING_MAX_TOKENS: u32 = 150;
pub const VISIT_MAX_TOKENS: u32 = 500;

fn transcript(turns: &[Turn]) -> String {
    turns
        .iter()
        .filter(|t| t.user.is_some() || t.assistant.is_some())
        .map(|t| format!("User: {}\nAssistant: {}", t.user.as_deref().unwrap_or(""), t.assistant.as_deref().unwrap_or("")))
        .collect::<Vec<_>>()
        .join("\n")
}

pub fn rolling_prompt(name: &str, existing: &str, turns: &[Turn]) -> String {
    let recent = transcript(turns);
    if !existing.is_empty() {
        format!(
            "You are maintaining a memory summary for DJ R3X's interactions with {name}.\n\nEXISTING SUMMARY:\n{existing}\n\n\
NEW CONVERSATION (just now):\n{recent}\n\nUpdate the summary to include new information from this conversation. Keep it \
concise (2-3 sentences).\nFocus on:\n- Music preferences and requests\n- Conversation patterns and interests\n- Any new topics \
or preferences\n- Remove outdated information if necessary\n\nUPDATED SUMMARY:"
        )
    } else {
        format!(
            "You are creating a memory summary for DJ R3X's first interaction with {name}.\n\nCONVERSATION:\n{recent}\n\n\
Create a concise 2-3 sentence summary. Focus on:\n- Music preferences and requests\n- Topics discussed\n- Any notable \
patterns\n\nSUMMARY:"
        )
    }
}

fn py_list(v: &[String]) -> String {
    let items: Vec<String> = v.iter().map(|s| format!("'{}'", s.replace('\'', "\\'"))).collect();
    format!("[{}]", items.join(", "))
}

pub fn visit_prompt(p: &PersonProfile, turns: &[Turn], duration_s: f64, music: &[String]) -> String {
    let music = if music.is_empty() { "['No music']".to_string() } else { py_list(music) };
    format!(
        "You are DJ R3X's memory system. {name} just left after visit #{visits}.\n\nCONVERSATION TRANSCRIPT:\n{conv}\n\n\
VISIT CONTEXT:\n- Duration: {secs}s ({mins}min)\n- Music played: {music}\n- Total lifetime visits: {visits}\n\n\
Extract structured episodic memories in JSON format:\n\n{{\n  \"visit_summary\": \"1-2 sentence overview of what happened this visit\",\n  \
\"memorable_moments\": [\n    \"Specific funny, notable, or important things that happened\",\n    \"Direct quotes worth remembering\",\n    \
\"Corrections or preferences they stated\",\n    \"Projects or topics they mentioned\"\n  ],\n  \"notable_facts\": [\n    \
\"New facts learned about them (job, hobbies, projects, location)\",\n    \"Physical observations (clothing, equipment, surroundings)\",\n    \
\"Context clues about their life\"\n  ],\n  \"personality_observations\": [\n    \"Communication style (formal/casual/humorous/direct)\",\n    \
\"Patience level and preferences (verbose vs concise)\",\n    \"Topics that engage them\",\n    \"Humor style if evident\"\n  ],\n  \
\"music_preferences\": {{\n    \"requests\": [\"specific tracks they asked for\"],\n    \"positive_reactions\": [\"genres/artists they liked\"],\n    \
\"negative_reactions\": [\"anything they disliked\"]\n  }},\n  \"topics_discussed\": [\"topic1\", \"topic2\", \"topic3\"],\n  \
\"mood\": \"brief description of their mood/vibe\",\n  \"follow_up_opportunities\": [\n    \"Things to ask about next time\",\n    \
\"Ongoing projects they mentioned\",\n    \"Promises or commitments R3X made\"\n  ]\n}}\n\nCRITICAL FORMATTING RULES:\n\
1. Return ONLY valid JSON starting with {{ and ending with }}\n2. NO markdown code blocks (no ```)\n3. NO explanatory text before or after the JSON\n\
4. NO comments inside the JSON\n5. Use double quotes for all strings\n6. Ensure all arrays and objects are properly closed\n\n\
Your response must start with {{ and end with }} - nothing else.",
        name = p.name,
        visits = p.visit_count,
        conv = transcript(turns),
        secs = duration_s as i64,
        mins = (duration_s / 60.0) as i64,
    )
}

/// Strip code fences and take the outermost `{...}`.
pub fn extract_json(raw: &str) -> Option<&str> {
    let mut text = raw.trim();
    if text.starts_with("```") {
        let parts: Vec<&str> = text.split("```").collect();
        if parts.len() >= 2 {
            let mut c = parts[1].trim();
            if let Some(rest) = c.strip_prefix("json") {
                c = rest;
            }
            text = c.trim();
        }
    }
    let (a, b) = (text.find('{')?, text.rfind('}')?);
    (a < b).then(|| &text[a..=b])
}

fn strs(v: &Value, n: usize) -> Vec<String> {
    v.as_array()
        .map(|a| a.iter().filter_map(Value::as_str).filter(|s| !s.is_empty()).take(n).map(str::to_string).collect())
        .unwrap_or_default()
}

fn push_unique(list: &mut Vec<String>, new: Vec<String>, keep: usize) {
    for x in new {
        if !list.contains(&x) {
            list.push(x);
        }
    }
    let n = list.len();
    if n > keep {
        list.drain(..n - keep);
    }
}

/// Fold one structured extraction into the profile (semantic + episodic memory).
pub fn apply_visit(p: &mut PersonProfile, data: &Value, date: &str, duration_s: f64, music: &[String], now: f64) {
    p.add_visit(VisitSummary {
        visit_number: p.visit_count,
        date: date.into(),
        duration_seconds: duration_s,
        summary: data["visit_summary"].as_str().unwrap_or_default().into(),
        memorable_moments: strs(&data["memorable_moments"], 5),
        music_played: music.iter().take(5).cloned().collect(),
        topics_discussed: strs(&data["topics_discussed"], 5),
        mood: data["mood"].as_str().unwrap_or_default().into(),
    });
    push_unique(&mut p.notable_facts, strs(&data["notable_facts"], 3), 10);
    push_unique(&mut p.personality_traits, strs(&data["personality_observations"], 2), 5);
    let prefs = &data["music_preferences"];
    let requests = strs(&prefs["requests"], 3);
    if !requests.is_empty() {
        let mut cur: Vec<String> = strs(&p.music_preferences.get("requests").cloned().unwrap_or_default(), usize::MAX);
        cur.extend(requests);
        let n = cur.len();
        p.music_preferences.insert("requests".into(), json!(cur[n.saturating_sub(10)..]));
    }
    let liked = strs(&prefs["positive_reactions"], usize::MAX);
    if !liked.is_empty() {
        let mut fav: Vec<String> = strs(&p.music_preferences.get("favorite_genres").cloned().unwrap_or_default(), usize::MAX);
        push_unique(&mut fav, liked, 5);
        p.music_preferences.insert("favorite_genres".into(), json!(fav));
    }
    p.metadata.insert("follow_up_opportunities".into(), json!(strs(&data["follow_up_opportunities"], 3)));
    p.metadata.insert("last_summary_update".into(), json!(now));
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn json_extraction_and_apply() {
        assert_eq!(extract_json("```json\n{\"a\": {\"b\": 1}}\n```"), Some("{\"a\": {\"b\": 1}}"));
        assert_eq!(extract_json("Sure! {\"a\":1} bye"), Some("{\"a\":1}"));
        assert_eq!(extract_json("nothing"), None);

        let mut p = PersonProfile::new("B", 0.0);
        p.visit_count = 2;
        let d: Value = serde_json::from_str(r#"{"visit_summary":"Talked robots.","memorable_moments":["m"],"notable_facts":["builds droids","builds droids"],
            "personality_observations":["dry humour"],"music_preferences":{"requests":["Doshka"],"positive_reactions":["jazz"]},
            "topics_discussed":["robots"],"mood":"upbeat","follow_up_opportunities":["ask about the servo board"]}"#).unwrap();
        apply_visit(&mut p, &d, "2026-09-29", 125.0, &["Doshka".into()], 9.0);
        assert_eq!(p.recent_visits[0].visit_number, 2);
        assert_eq!(p.notable_facts, ["builds droids"]);
        assert_eq!(p.music_preferences["requests"], json!(["Doshka"]));
        assert_eq!(p.metadata["follow_up_opportunities"], json!(["ask about the servo board"]));
        assert!(visit_prompt(&p, &[], 125.0, &[]).contains("- Duration: 125s (2min)\n- Music played: ['No music']"));
        assert!(rolling_prompt("B", "", &[]).ends_with("SUMMARY:"));
    }
}
