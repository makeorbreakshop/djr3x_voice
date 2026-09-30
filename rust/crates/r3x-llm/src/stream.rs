//! SSE decoding and Messages-stream assembly.
//!
//! Text deltas surface as they arrive; each `tool_use` block surfaces the moment its
//! `content_block_stop` lands - not after the final message (CantinaOS extracted tool calls only
//! after `get_final_message()`, so an action could not start until Claude finished talking).

use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::BTreeMap;

use crate::LlmError;

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ToolUse {
    pub id: String,
    pub name: String,
    pub input: Value,
}

#[derive(Debug, Clone, PartialEq, Default, Serialize, Deserialize)]
pub struct Usage {
    #[serde(default)]
    pub input_tokens: u64,
    #[serde(default)]
    pub output_tokens: u64,
    #[serde(default)]
    pub cache_creation_input_tokens: Option<u64>,
    #[serde(default)]
    pub cache_read_input_tokens: Option<u64>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ContentBlock {
    Text { text: String },
    ToolUse { id: String, name: String, input: Value },
    #[serde(other)]
    Other,
}

/// The whole reply. Same shape as the Messages API non-streaming response.
#[derive(Debug, Clone, PartialEq, Default, Serialize, Deserialize)]
pub struct FinalMessage {
    #[serde(default)]
    pub id: String,
    #[serde(default)]
    pub model: String,
    #[serde(default)]
    pub content: Vec<ContentBlock>,
    #[serde(default)]
    pub stop_reason: Option<String>,
    #[serde(default)]
    pub usage: Usage,
}

impl FinalMessage {
    /// Concatenated `text` blocks only (`_response_text`): never `content[0].text`, which breaks
    /// when a tool_use or thinking block comes first.
    pub fn text(&self) -> String {
        self.content
            .iter()
            .filter_map(|b| match b {
                ContentBlock::Text { text } => Some(text.as_str()),
                _ => None,
            })
            .collect()
    }
    pub fn tool_uses(&self) -> Vec<ToolUse> {
        self.content
            .iter()
            .filter_map(|b| match b {
                ContentBlock::ToolUse { id, name, input } => {
                    Some(ToolUse { id: id.clone(), name: name.clone(), input: input.clone() })
                }
                _ => None,
            })
            .collect()
    }
}

#[derive(Debug, Clone, PartialEq)]
pub enum StreamEvent {
    TextDelta(String),
    /// A complete tool call, emitted as soon as its block closes.
    ToolUse(ToolUse),
    /// Always last on success.
    Done(FinalMessage),
}

/// Splits a byte stream into SSE `(event, data)` frames. Handles frames split across chunks
/// and multi-byte UTF-8 split across chunks.
#[derive(Default)]
pub struct SseDecoder {
    buf: Vec<u8>,
}

impl SseDecoder {
    pub fn push(&mut self, bytes: &[u8]) -> Vec<(String, String)> {
        self.buf.extend_from_slice(bytes);
        let mut out = Vec::new();
        loop {
            let Some((end, sep)) = find_frame_end(&self.buf) else { break };
            let frame: Vec<u8> = self.buf.drain(..end + sep).collect();
            let text = String::from_utf8_lossy(&frame[..end]);
            let (mut event, mut data) = (String::new(), Vec::new());
            for line in text.lines() {
                if let Some(v) = line.strip_prefix("event:") {
                    event = v.trim().to_string();
                } else if let Some(v) = line.strip_prefix("data:") {
                    data.push(v.strip_prefix(' ').unwrap_or(v).to_string());
                }
            }
            if !data.is_empty() || !event.is_empty() {
                out.push((event, data.join("\n")));
            }
        }
        out
    }
}

fn find_frame_end(b: &[u8]) -> Option<(usize, usize)> {
    (0..b.len()).find_map(|i| {
        if b[i..].starts_with(b"\r\n\r\n") {
            Some((i, 4))
        } else if b[i..].starts_with(b"\n\n") {
            Some((i, 2))
        } else {
            None
        }
    })
}

enum Partial {
    Text(String),
    Tool { id: String, name: String, json: String },
    Other,
}

/// Folds Messages stream events into [`StreamEvent`]s and the final message.
#[derive(Default)]
pub struct Assembler {
    msg: FinalMessage,
    blocks: BTreeMap<u64, Partial>,
    done: Vec<(u64, ContentBlock)>,
}

impl Assembler {
    /// Feed one SSE frame's data (the JSON has its own `type`).
    pub fn feed(&mut self, data: &str) -> Result<Vec<StreamEvent>, LlmError> {
        if data.trim().is_empty() {
            return Ok(vec![]);
        }
        let v: Value = serde_json::from_str(data).map_err(|e| LlmError::Protocol(format!("bad SSE json: {e}")))?;
        let idx = v["index"].as_u64().unwrap_or(0);
        let mut out = Vec::new();
        match v["type"].as_str().unwrap_or("") {
            "message_start" => {
                let m = &v["message"];
                self.msg.id = m["id"].as_str().unwrap_or_default().into();
                self.msg.model = m["model"].as_str().unwrap_or_default().into();
                if let Ok(u) = serde_json::from_value::<Usage>(m["usage"].clone()) {
                    self.msg.usage = u;
                }
            }
            "content_block_start" => {
                let b = &v["content_block"];
                let p = match b["type"].as_str() {
                    Some("text") => {
                        let t = b["text"].as_str().unwrap_or_default().to_string();
                        if !t.is_empty() {
                            out.push(StreamEvent::TextDelta(t.clone()));
                        }
                        Partial::Text(t)
                    }
                    Some("tool_use") => Partial::Tool {
                        id: b["id"].as_str().unwrap_or_default().into(),
                        name: b["name"].as_str().unwrap_or_default().into(),
                        json: String::new(),
                    },
                    _ => Partial::Other,
                };
                self.blocks.insert(idx, p);
            }
            "content_block_delta" => {
                let d = &v["delta"];
                match (self.blocks.get_mut(&idx), d["type"].as_str()) {
                    (Some(Partial::Text(t)), Some("text_delta")) => {
                        let s = d["text"].as_str().unwrap_or_default();
                        if !s.is_empty() {
                            t.push_str(s);
                            out.push(StreamEvent::TextDelta(s.to_string()));
                        }
                    }
                    (Some(Partial::Tool { json, .. }), Some("input_json_delta")) => {
                        json.push_str(d["partial_json"].as_str().unwrap_or_default());
                    }
                    _ => {}
                }
            }
            "content_block_stop" => {
                if let Some(p) = self.blocks.remove(&idx) {
                    let block = match p {
                        Partial::Text(text) => ContentBlock::Text { text },
                        Partial::Tool { id, name, json } => {
                            let input = if json.trim().is_empty() {
                                Value::Object(Default::default())
                            } else {
                                serde_json::from_str(&json)
                                    .map_err(|e| LlmError::Protocol(format!("tool input json: {e}")))?
                            };
                            out.push(StreamEvent::ToolUse(ToolUse { id: id.clone(), name: name.clone(), input: input.clone() }));
                            ContentBlock::ToolUse { id, name, input }
                        }
                        Partial::Other => ContentBlock::Other,
                    };
                    self.done.push((idx, block));
                }
            }
            "message_delta" => {
                if let Some(r) = v["delta"]["stop_reason"].as_str() {
                    self.msg.stop_reason = Some(r.into());
                }
                if let Some(o) = v["usage"]["output_tokens"].as_u64() {
                    self.msg.usage.output_tokens = o;
                }
            }
            "message_stop" => out.push(StreamEvent::Done(self.finish())),
            "error" => {
                return Err(LlmError::Api {
                    status: 0,
                    message: v["error"]["message"].as_str().unwrap_or("stream error").to_string(),
                })
            }
            _ => {} // ping, unknown
        }
        Ok(out)
    }

    fn finish(&mut self) -> FinalMessage {
        let mut done = std::mem::take(&mut self.done);
        done.sort_by_key(|(i, _)| *i);
        let mut m = std::mem::take(&mut self.msg);
        m.content = done.into_iter().map(|(_, b)| b).collect();
        m
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const SSE: &str = "event: message_start\ndata: {\"type\":\"message_start\",\"message\":{\"id\":\"m1\",\"model\":\"claude-sonnet-5\",\"usage\":{\"input_tokens\":10,\"output_tokens\":1,\"cache_read_input_tokens\":7}}}\n\n\
event: content_block_start\ndata: {\"type\":\"content_block_start\",\"index\":0,\"content_block\":{\"type\":\"text\",\"text\":\"\"}}\n\n\
event: ping\ndata: {\"type\":\"ping\"}\n\n\
event: content_block_delta\ndata: {\"type\":\"content_block_delta\",\"index\":0,\"delta\":{\"type\":\"text_delta\",\"text\":\"Spinning \u{00e9}\"}}\n\n\
event: content_block_stop\ndata: {\"type\":\"content_block_stop\",\"index\":0}\n\n\
event: content_block_start\ndata: {\"type\":\"content_block_start\",\"index\":1,\"content_block\":{\"type\":\"tool_use\",\"id\":\"tu1\",\"name\":\"play_music\",\"input\":{}}}\n\n\
event: content_block_delta\ndata: {\"type\":\"content_block_delta\",\"index\":1,\"delta\":{\"type\":\"input_json_delta\",\"partial_json\":\"{\\\"track\\\": \\\"Dos\"}}\n\n\
event: content_block_delta\ndata: {\"type\":\"content_block_delta\",\"index\":1,\"delta\":{\"type\":\"input_json_delta\",\"partial_json\":\"hka\\\"}\"}}\n\n\
event: content_block_stop\ndata: {\"type\":\"content_block_stop\",\"index\":1}\n\n\
event: content_block_start\ndata: {\"type\":\"content_block_start\",\"index\":2,\"content_block\":{\"type\":\"text\",\"text\":\"\"}}\n\n\
event: content_block_delta\ndata: {\"type\":\"content_block_delta\",\"index\":2,\"delta\":{\"type\":\"text_delta\",\"text\":\"!\"}}\n\n\
event: content_block_stop\ndata: {\"type\":\"content_block_stop\",\"index\":2}\n\n\
event: message_delta\ndata: {\"type\":\"message_delta\",\"delta\":{\"stop_reason\":\"tool_use\"},\"usage\":{\"output_tokens\":42}}\n\n\
event: message_stop\ndata: {\"type\":\"message_stop\"}\n\n";

    fn run(chunk: usize) -> Vec<StreamEvent> {
        let (mut d, mut a, mut out) = (SseDecoder::default(), Assembler::default(), vec![]);
        for c in SSE.as_bytes().chunks(chunk) {
            for (_, data) in d.push(c) {
                out.extend(a.feed(&data).unwrap());
            }
        }
        out
    }

    #[test]
    fn tool_use_surfaces_before_the_end_and_chunking_does_not_matter() {
        for chunk in [1, 3, 7, 64, 10_000] {
            let ev = run(chunk);
            assert_eq!(ev[0], StreamEvent::TextDelta("Spinning \u{e9}".into()), "chunk {chunk}");
            assert!(matches!(&ev[1], StreamEvent::ToolUse(t) if t.name == "play_music" && t.input["track"] == "Doshka"));
            assert_eq!(ev[2], StreamEvent::TextDelta("!".into()));
            let StreamEvent::Done(m) = &ev[3] else { panic!() };
            assert_eq!(m.text(), "Spinning \u{e9}!");
            assert_eq!(m.tool_uses().len(), 1);
            assert_eq!((m.stop_reason.as_deref(), m.usage.output_tokens, m.usage.cache_read_input_tokens), (Some("tool_use"), 42, Some(7)));
        }
    }
}
