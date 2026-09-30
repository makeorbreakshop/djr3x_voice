//! Replay of Phase 0 Claude fixtures (`fixtures/<name>/claude.jsonl`, written by
//! `cantina_os/tap/fixtures.py`).
//!
//! A record is keyed like the recorder: `sha1(["claude", method, tool_choice, last_user])`.
//! Streams keep their recorded chunk boundaries and per-chunk `wait`; identical requests replay
//! in recorded order and the last one repeats once the queue is drained.

use serde::Deserialize;
use serde_json::Value;
use std::collections::{HashMap, VecDeque};
use std::path::Path;
use std::sync::Mutex;

use crate::pyjson::fixture_key;
use crate::request::{MessagesRequest, Role};
use crate::stream::FinalMessage;

#[derive(Debug, Clone, Deserialize)]
pub struct Chunk {
    pub wait: f64,
    pub text: String,
}

#[derive(Debug, Clone, Deserialize)]
pub struct ClaudeRecord {
    pub key: String,
    #[serde(default)]
    pub client: String,
    pub method: String,
    #[serde(default)]
    pub chunks: Vec<Chunk>,
    #[serde(default)]
    pub wait: f64,
    #[serde(rename = "final")]
    pub final_message: FinalMessage,
}

pub struct ClaudeFixtures {
    queues: Mutex<HashMap<String, VecDeque<ClaudeRecord>>>,
    last: Mutex<HashMap<String, ClaudeRecord>>,
    /// Multiplies recorded waits: 1.0 = recorded pacing, 0.0 = as fast as possible.
    pub pace: f64,
}

impl ClaudeFixtures {
    pub fn load(dir: impl AsRef<Path>, pace: f64) -> std::io::Result<Self> {
        let path = dir.as_ref().join("claude.jsonl");
        let text = std::fs::read_to_string(&path)?;
        let mut queues: HashMap<String, VecDeque<ClaudeRecord>> = HashMap::new();
        for line in text.lines().filter(|l| !l.trim().is_empty()) {
            let rec: ClaudeRecord = serde_json::from_str(line)
                .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, format!("{}: {e}", path.display())))?;
            queues.entry(rec.key.clone()).or_default().push_back(rec);
        }
        Ok(Self { queues: Mutex::new(queues), last: Mutex::new(HashMap::new()), pace })
    }

    /// The recorder's key for a request made through `method` ("stream" | "create").
    pub fn key_for(method: &str, req: &MessagesRequest) -> String {
        let last_user = req
            .messages
            .iter()
            .rev()
            .find(|m| m.role == Role::User)
            .map_or(Value::Null, |m| Value::String(m.content.clone()));
        let tool_choice = req.tool_choice.as_ref().map_or(Value::Null, |c| c.to_json());
        fixture_key(&[Value::from("claude"), Value::from(method), tool_choice, last_user])
    }

    pub fn take(&self, method: &str, req: &MessagesRequest) -> Option<ClaudeRecord> {
        let key = Self::key_for(method, req);
        let popped = self.queues.lock().ok()?.get_mut(&key).and_then(VecDeque::pop_front);
        let mut last = self.last.lock().ok()?;
        match popped {
            Some(rec) => {
                last.insert(key, rec.clone());
                Some(rec)
            }
            None => {
                let rec = last.get(&key).cloned();
                if rec.is_none() {
                    tracing::warn!(key, method, "Claude fixture miss");
                }
                rec
            }
        }
    }

    pub fn remaining(&self) -> usize {
        self.queues.lock().map(|q| q.values().map(VecDeque::len).sum()).unwrap_or(0)
    }
}
