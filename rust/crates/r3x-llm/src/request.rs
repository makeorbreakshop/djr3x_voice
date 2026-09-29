//! Request building: messages, tools, `cache_control`, `tool_choice`, and the per-model-family
//! sampling rule (`claude_service.py` `_generation_kwargs` / `_temperature_kwargs`).

use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};

/// Models that reject `temperature` *and* think at high effort unless told otherwise: they get
/// `thinking: between_tools` + `output_config.effort` instead. Matched on the Anthropic id.
pub const BETWEEN_TOOLS_MODELS: &[&str] = &["claude-sonnet-5-5"];

/// Families that still accept a non-default `temperature`. Everything newer returns 400 on the
/// direct API (OpenRouter silently drops it). Matched on the Anthropic id and on OpenRouter's
/// `anthropic/claude-haiku-4.5` form alike.
pub const TEMPERATURE_MODEL_PREFIXES: &[&str] =
    &["claude-haiku-", "claude-sonnet-4", "claude-opus-4-5", "claude-opus-4-6", "claude-3"];

pub fn accepts_temperature(model: &str) -> bool {
    let bare = model.rsplit('/').next().unwrap_or(model).replace('.', "-");
    TEMPERATURE_MODEL_PREFIXES.iter().any(|p| bare.starts_with(p))
}

/// Sampling/thinking fields for one request. `requested` is the Anthropic id (before provider
/// mapping), `wire` the id actually sent.
pub fn generation_params(requested: &str, wire: &str, temperature: f32, effort: &str) -> Map<String, Value> {
    let mut m = Map::new();
    if BETWEEN_TOOLS_MODELS.contains(&requested) {
        m.insert("thinking".into(), json!({"type": "between_tools"}));
        m.insert("output_config".into(), json!({"effort": effort}));
    } else if accepts_temperature(wire) {
        m.insert("temperature".into(), json!(temperature));
    }
    m
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Role {
    User,
    Assistant,
}

/// A plain-text turn. Tool results are folded in as user text, as CantinaOS does. `images`
/// (scene description only) go on the wire as image blocks ahead of the text block; they are
/// never kept in session memory or fixture keys.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Message {
    pub role: Role,
    pub content: String,
    #[serde(skip)]
    pub images: Vec<Image>,
}

/// A base64 image for a user message (`vision_service.py` `_analyze_scene`: JPEG q85).
#[derive(Debug, Clone, PartialEq)]
pub struct Image {
    pub media_type: String,
    pub data_b64: String,
}

impl Message {
    pub fn new(role: Role, t: impl Into<String>) -> Self {
        Self { role, content: t.into(), images: Vec::new() }
    }
    pub fn user(t: impl Into<String>) -> Self {
        Self::new(Role::User, t)
    }
    pub fn assistant(t: impl Into<String>) -> Self {
        Self::new(Role::Assistant, t)
    }
    /// A user turn carrying one image and a question about it.
    pub fn user_image(media_type: impl Into<String>, data_b64: impl Into<String>, t: impl Into<String>) -> Self {
        Self { images: vec![Image { media_type: media_type.into(), data_b64: data_b64.into() }], ..Self::user(t) }
    }

    fn to_json(&self) -> Value {
        if self.images.is_empty() {
            return serde_json::to_value(self).unwrap_or_default();
        }
        let mut blocks: Vec<Value> = self
            .images
            .iter()
            .map(|i| json!({"type": "image", "source": {"type": "base64", "media_type": i.media_type, "data": i.data_b64}}))
            .collect();
        blocks.push(json!({"type": "text", "text": self.content}));
        json!({"role": self.role, "content": blocks})
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Tool {
    pub name: String,
    pub description: String,
    pub input_schema: Value,
}

#[derive(Debug, Clone, PartialEq)]
pub enum ToolChoice {
    Auto,
    Any,
    /// Tools stay in the request (prompt cache still hits) but Claude can only speak. Used when
    /// the fast router already acted.
    None,
    Tool(String),
}

impl ToolChoice {
    pub fn to_json(&self) -> Value {
        match self {
            ToolChoice::Auto => json!({"type": "auto"}),
            ToolChoice::Any => json!({"type": "any"}),
            ToolChoice::None => json!({"type": "none"}),
            ToolChoice::Tool(n) => json!({"type": "tool", "name": n}),
        }
    }
}

/// One Messages API call, minus the model (the client owns the one `CLAUDE_MODEL`).
#[derive(Debug, Clone, PartialEq)]
pub struct MessagesRequest {
    pub system: Option<String>,
    /// `cache_control: ephemeral` on the system block (main persona: yes; verbal feedback: no).
    pub cache_system: bool,
    pub messages: Vec<Message>,
    /// `cache_control` goes on the last tool only, so the whole tool block is one cached prefix.
    pub tools: Vec<Tool>,
    pub tool_choice: Option<ToolChoice>,
    pub max_tokens: u32,
    /// `None`: no sampling fields at all (memory summaries). `Some(t)`: per-family rule above.
    pub temperature: Option<f32>,
}

impl MessagesRequest {
    pub fn new(max_tokens: u32) -> Self {
        Self {
            system: None,
            cache_system: false,
            messages: Vec::new(),
            tools: Vec::new(),
            tool_choice: None,
            max_tokens,
            temperature: None,
        }
    }
    pub fn system(mut self, s: impl Into<String>, cached: bool) -> Self {
        self.system = Some(s.into());
        self.cache_system = cached;
        self
    }
    pub fn messages(mut self, m: Vec<Message>) -> Self {
        self.messages = m;
        self
    }
    pub fn user(mut self, t: impl Into<String>) -> Self {
        self.messages.push(Message::user(t));
        self
    }
    pub fn tools(mut self, t: Vec<Tool>) -> Self {
        self.tools = t;
        self
    }
    pub fn tool_choice(mut self, c: ToolChoice) -> Self {
        self.tool_choice = Some(c);
        self
    }
    pub fn temperature(mut self, t: f32) -> Self {
        self.temperature = Some(t);
        self
    }

    /// The JSON body. `requested`/`wire` model ids as in [`generation_params`].
    pub fn to_body(&self, requested: &str, wire: &str, effort: &str, stream: bool) -> Value {
        let mut b = Map::new();
        b.insert("model".into(), json!(wire));
        b.insert("max_tokens".into(), json!(self.max_tokens));
        if let Some(s) = &self.system {
            let v = if self.cache_system {
                json!([{"type": "text", "text": s, "cache_control": {"type": "ephemeral"}}])
            } else {
                json!(s)
            };
            b.insert("system".into(), v);
        }
        b.insert("messages".into(), Value::Array(self.messages.iter().map(Message::to_json).collect()));
        if !self.tools.is_empty() {
            let mut tools: Vec<Value> = self.tools.iter().map(|t| serde_json::to_value(t).unwrap_or_default()).collect();
            if let Some(Value::Object(last)) = tools.last_mut() {
                last.insert("cache_control".into(), json!({"type": "ephemeral"}));
            }
            b.insert("tools".into(), Value::Array(tools));
        }
        if let Some(c) = &self.tool_choice {
            b.insert("tool_choice".into(), c.to_json());
        }
        if let Some(t) = self.temperature {
            b.extend(generation_params(requested, wire, t, effort));
        }
        if stream {
            b.insert("stream".into(), json!(true));
        }
        Value::Object(b)
    }
}

/// CantinaOS's Claude tools (`command_functions.py`, schemas byte-identical to pydantic's):
/// `play_music`, `search_music`, `stop_music`, `set_eye_color`, `analyze_scene`.
pub fn default_tools() -> Vec<Tool> {
    tools_file().0
}

/// `perform_show`, registered only when routines exist. `routine_ids` must be sorted - the tool
/// block is part of the cached prefix.
pub fn perform_show_tool(routine_ids: &[String]) -> Tool {
    let mut t = tools_file().1;
    if !routine_ids.is_empty() {
        t.input_schema["properties"]["id"]["enum"] = json!(routine_ids);
    }
    t
}

fn tools_file() -> (Vec<Tool>, Tool) {
    #[derive(Deserialize)]
    struct F {
        tools: Vec<Tool>,
        perform_show: Tool,
    }
    let f: F = serde_json::from_str(include_str!("tools.json")).expect("tools.json is valid");
    let mut ps = f.perform_show;
    // the dump used a placeholder enum; the real one is set per call
    if let Some(p) = ps.input_schema.pointer_mut("/properties/id").and_then(Value::as_object_mut) {
        p.remove("enum");
    }
    (f.tools, ps)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn temperature_rule_per_family() {
        assert!(accepts_temperature("claude-haiku-4-5-20251001"));
        assert!(accepts_temperature("anthropic/claude-haiku-4.5"));
        assert!(accepts_temperature("anthropic/claude-sonnet-4.5"));
        assert!(!accepts_temperature("claude-sonnet-5"));
        assert!(!accepts_temperature("anthropic/claude-opus-5"));
        assert!(!accepts_temperature("claude-opus-4-7"));

        let p = generation_params("claude-sonnet-5-5", "anthropic/claude-sonnet-5.5", 0.4, "low");
        assert_eq!(Value::Object(p), json!({"thinking": {"type": "between_tools"}, "output_config": {"effort": "low"}}));
        assert!(generation_params("claude-sonnet-5", "claude-sonnet-5", 0.4, "low").is_empty());
        assert_eq!(generation_params("claude-haiku-4-5", "claude-haiku-4-5", 0.5, "low")["temperature"], json!(0.5));
    }

    #[test]
    fn body_has_cache_control_and_tool_choice_none() {
        let req = MessagesRequest::new(160)
            .system("persona", true)
            .user("hi")
            .tools(default_tools())
            .tool_choice(ToolChoice::None)
            .temperature(0.4);
        let b = req.to_body("claude-sonnet-5", "claude-sonnet-5", "low", true);
        assert_eq!(b["system"][0]["cache_control"], json!({"type": "ephemeral"}));
        let tools = b["tools"].as_array().unwrap();
        assert_eq!(tools.len(), 5);
        assert!(tools[..4].iter().all(|t| t.get("cache_control").is_none()));
        assert_eq!(tools[4]["cache_control"], json!({"type": "ephemeral"}));
        assert_eq!(b["tool_choice"], json!({"type": "none"}));
        assert!(b.get("temperature").is_none());
        assert_eq!(b["stream"], json!(true));
        assert_eq!(b["messages"], json!([{"role": "user", "content": "hi"}]));

        let plain = MessagesRequest::new(200).system("fb", false).user("x").to_body("m", "m", "low", false);
        assert_eq!(plain["system"], json!("fb"));
        assert!(plain.get("tools").is_none() && plain.get("stream").is_none());

        let ps = perform_show_tool(&["a".into(), "b".into()]);
        assert_eq!(ps.input_schema["properties"]["id"]["enum"], json!(["a", "b"]));
        assert!(perform_show_tool(&[]).input_schema["properties"]["id"].get("enum").is_none());
    }

    #[test]
    fn image_message_is_image_then_text_blocks() {
        let req = MessagesRequest::new(200).messages(vec![Message::user_image("image/jpeg", "QUJD", "Describe")]);
        let b = req.to_body("m", "m", "low", false);
        assert_eq!(
            b["messages"],
            json!([{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "QUJD"}},
                {"type": "text", "text": "Describe"}]}])
        );
    }
}
