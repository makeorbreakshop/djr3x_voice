//! Turn prompt assembly, as `claude_service.py` does it: session memory, context blocks,
//! `<action_already_taken>`, the main-turn request and the verbal-feedback request.

use serde_json::{Map, Value};
use std::collections::VecDeque;

use crate::pyjson;
use crate::request::{Message, MessagesRequest, Role, Tool, ToolChoice};

/// Backstop on a spoken reply (the persona asks for 2-3 sentences).
pub const SPOKEN_REPLY_MAX_TOKENS: u32 = 160;
pub const TURN_TEMPERATURE: f32 = 0.4;
pub const VERBAL_FEEDBACK_MAX_TOKENS: u32 = 200;
pub const VERBAL_FEEDBACK_TEMPERATURE: f32 = 0.7;
pub const SESSION_MAX_MESSAGES: usize = 50;
pub const SESSION_MAX_TOKENS: usize = 40_000;

/// Tools whose result is not narrated by a second call (the eyes speak for themselves;
/// `perform_show` runs alongside the main reply).
pub const VISUAL_ONLY_TOOLS: &[&str] = &["set_eye_color", "set_eye_pattern", "eye_pattern", "set_eye_animation", "perform_show"];
/// Tools that produce their own follow-up turn.
pub const VISION_TOOLS: &[&str] = &["analyze_scene"];

/// Conversation history for the main turn. Keeps the *tagged* text (show tags included) so
/// Claude keeps seeing its own convention.
#[derive(Debug, Clone)]
pub struct SessionMemory {
    messages: VecDeque<Message>,
    tokens: usize,
    pub max_messages: usize,
    pub max_tokens: usize,
}

impl Default for SessionMemory {
    fn default() -> Self {
        Self::new(SESSION_MAX_MESSAGES, SESSION_MAX_TOKENS)
    }
}

fn estimate(s: &str) -> usize {
    s.split_whitespace().count() + 5
}

impl SessionMemory {
    pub fn new(max_messages: usize, max_tokens: usize) -> Self {
        Self { messages: VecDeque::new(), tokens: 0, max_messages, max_tokens }
    }

    pub fn add(&mut self, role: Role, content: impl Into<String>) {
        let content = content.into();
        self.tokens += estimate(&content);
        self.messages.push_back(Message { role, content });
        while self.messages.len() > self.max_messages
            || (self.tokens > self.max_tokens && self.messages.len() > 1)
        {
            if let Some(m) = self.messages.pop_front() {
                self.tokens -= estimate(&m.content);
            }
        }
    }

    pub fn messages(&self) -> Vec<Message> {
        self.messages.iter().cloned().collect()
    }

    pub fn len(&self) -> usize {
        self.messages.len()
    }

    pub fn is_empty(&self) -> bool {
        self.messages.is_empty()
    }

    pub fn clear(&mut self) {
        self.messages.clear();
        self.tokens = 0;
    }
}

/// Python `repr()` of a JSON value, for `<parameters>k='v'</parameters>`.
fn py_repr(v: &Value) -> String {
    match v {
        Value::Null => "None".into(),
        Value::Bool(b) => if *b { "True" } else { "False" }.into(),
        Value::String(s) => {
            if s.contains('\'') && !s.contains('"') {
                format!("\"{s}\"")
            } else {
                format!("'{}'", s.replace('\\', "\\\\").replace('\'', "\\'"))
            }
        }
        other => pyjson::dumps(other, false, false),
    }
}

/// The block that tells Claude the fast router already did it (then send `ToolChoice::None`).
pub fn action_already_taken(tool: &str, parameters: &Map<String, Value>) -> String {
    let params: Vec<String> = parameters.iter().map(|(k, v)| format!("{k}={}", py_repr(v))).collect();
    let params = if params.is_empty() { "none".to_string() } else { params.join(", ") };
    format!(
        "<action_already_taken>\n  <tool>{tool}</tool>\n  <parameters>{params}</parameters>\n  \
<status>Already executed. This is done and the user can already see or hear the result.</status>\n  \
<your_instructions>Do NOT call any tool for this request - it has already been carried out for you. \
Respond with one short spoken line, in character, reacting to what you just did. Speak about it in \
the past or present tense, never as something you are about to do.</your_instructions>\n\
</action_already_taken>\n\n"
    )
}

/// Context that precedes `<user_input>`, longform first.
#[derive(Debug, Clone, Default)]
pub struct TurnContext {
    /// Inner XML of `<person_memory>` (r3x-memory renders it).
    pub person_memory: Option<String>,
    /// `<system_observation>` lines, e.g. "What you can see - ...", "Speaking with: [...]".
    pub observations: Vec<String>,
    /// Already formatted recent turns.
    pub conversation_history: Option<String>,
}

impl TurnContext {
    fn section(&self) -> Option<String> {
        let mut parts = Vec::new();
        if let Some(pm) = self.person_memory.as_deref().filter(|s| !s.is_empty()) {
            parts.push(format!("<person_memory>\n{pm}\n</person_memory>"));
        }
        if !self.observations.is_empty() {
            parts.push(format!("<system_observation>\n  {}\n</system_observation>", self.observations.join("\n  ")));
        }
        if let Some(h) = self.conversation_history.as_deref().filter(|s| !s.is_empty()) {
            parts.push(format!("<conversation_history>{h}</conversation_history>"));
        }
        (!parts.is_empty()).then(|| parts.join("\n\n"))
    }
}

/// The user message for a turn: `action? + context? + <user_input>`; the raw transcript when
/// there is neither.
pub fn user_message(user_input: &str, action_block: Option<&str>, ctx: &TurnContext) -> String {
    let action = action_block.unwrap_or("");
    match ctx.section() {
        Some(sec) => format!("{action}{sec}\n\n<user_input>\n{user_input}\n</user_input>"),
        None if !action.is_empty() => format!("{action}<user_input>\n{user_input}\n</user_input>"),
        None => user_input.to_string(),
    }
}

/// The main spoken turn. `router_acted` -> `tool_choice: none`, tools kept for the cache.
pub fn turn_request(system: &str, history: Vec<Message>, tools: Vec<Tool>, router_acted: bool) -> MessagesRequest {
    let mut r = MessagesRequest::new(SPOKEN_REPLY_MAX_TOKENS)
        .system(system, true)
        .messages(history)
        .tools(tools)
        .temperature(TURN_TEMPERATURE);
    if router_acted {
        r = r.tool_choice(ToolChoice::None);
    }
    r
}

/// Whether a tool result gets a second, verbal-feedback call. Never when the fast router acted
/// (the main turn owns the one spoken reply), never for visual-only or vision tools.
pub fn needs_verbal_feedback(tool: &str, router_acted: bool) -> bool {
    !router_acted && !VISUAL_ONLY_TOOLS.contains(&tool) && !VISION_TOOLS.contains(&tool)
}

pub fn verbal_feedback_request(persona: Option<&str>, tool: &str, parameters: &Value, result: &Value, success: bool) -> MessagesRequest {
    let fallback;
    let persona = match persona.filter(|p| !p.trim().is_empty()) {
        Some(p) => p,
        None => {
            fallback = format!(
                "You are DJ R-3X, a Star Wars droid DJ. Generate a brief verbal response about the {tool} action that was just \
performed. Be natural, conversational, and specific about what was done. Keep your response short and enthusiastic as if \
you're DJ R3X speaking to a guest."
            );
            &fallback
        }
    };
    let details = format!(
        "Intent executed: {tool}\nParameters: {}\nResult: {}\nSuccess: {}",
        pyjson::dumps(parameters, false, false),
        pyjson::dumps(result, false, false),
        if success { "True" } else { "False" }
    );
    MessagesRequest::new(VERBAL_FEEDBACK_MAX_TOKENS)
        .system(persona, false)
        .user(details)
        .temperature(VERBAL_FEEDBACK_TEMPERATURE)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn session_prunes_by_count_and_tokens() {
        let mut s = SessionMemory::new(3, 1000);
        for i in 0..5 {
            s.add(Role::User, format!("m{i}"));
        }
        assert_eq!(s.messages().iter().map(|m| m.content.as_str()).collect::<Vec<_>>(), ["m2", "m3", "m4"]);
        let mut s = SessionMemory::new(50, 20); // each "a b c" = 8 tokens
        for _ in 0..4 {
            s.add(Role::Assistant, "a b c");
        }
        assert_eq!(s.len(), 2);
    }

    #[test]
    fn user_message_composition() {
        let action = action_already_taken("play_music", json!({"track": null, "name": "Doshka"}).as_object().unwrap());
        assert!(action.contains("<parameters>track=None, name='Doshka'</parameters>") || action.contains("<parameters>name='Doshka', track=None</parameters>"));
        assert_eq!(action_already_taken("stop_music", &Map::new()).lines().nth(2).unwrap(), "  <parameters>none</parameters>");
        let empty = TurnContext::default();
        assert_eq!(user_message("hi", None, &empty), "hi");
        assert_eq!(user_message("hi", Some("A\n\n"), &empty), "A\n\n<user_input>\nhi\n</user_input>");
        let ctx = TurnContext { observations: vec!["Speaking with: [B | 2 visits | 1h ago]".into()], ..Default::default() };
        assert_eq!(
            user_message("hi", None, &ctx),
            "<system_observation>\n  Speaking with: [B | 2 visits | 1h ago]\n</system_observation>\n\n<user_input>\nhi\n</user_input>"
        );
    }

    #[test]
    fn verbal_feedback_rules() {
        assert!(needs_verbal_feedback("play_music", false));
        assert!(!needs_verbal_feedback("play_music", true));
        assert!(!needs_verbal_feedback("set_eye_color", false));
        assert!(!needs_verbal_feedback("analyze_scene", false));
        let r = verbal_feedback_request(None, "stop_music", &json!({}), &json!({"ok": true}), true);
        assert_eq!(r.messages[0].content, "Intent executed: stop_music\nParameters: {}\nResult: {\"ok\": true}\nSuccess: True");
        assert!(!r.cache_system && r.max_tokens == 200);
        let t = turn_request("sys", vec![Message::user("x")], crate::request::default_tools(), true);
        assert_eq!(t.tool_choice, Some(ToolChoice::None));
    }
}
