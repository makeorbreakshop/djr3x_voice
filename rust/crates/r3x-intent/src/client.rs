//! Jev (typesafe.ai systemone) client: one warm connection, pinned model, `attempts=1`,
//! 0.8 s ceiling, fails open (`None`) and never raises into the voice loop.

use serde::Deserialize;
use serde_json::value::RawValue;
use serde_json::{json, Value};
use std::collections::{HashMap, VecDeque};
use std::path::Path;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use crate::decide::JevResult;

pub const JEV_URL: &str = "https://api.typesafe.ai/v1/systemone";
/// Pinned: a silent model bump would move every threshold.
pub const JEV_MODEL: &str = "jev-1.13.0";
pub const DEFAULT_TIMEOUT: Duration = Duration::from_millis(800);

#[derive(Debug, Clone, Deserialize)]
pub struct JevRecord {
    pub key: String,
    #[serde(default)]
    pub state: Option<String>,
    pub status: u16,
    pub body: Box<RawValue>,
    #[serde(default)]
    pub latency_ms: f64,
}

/// Recorded Jev exchanges (`fixtures/<name>/jev.jsonl`), keyed exactly like the recorder.
pub struct JevFixtures {
    queues: Mutex<HashMap<String, VecDeque<JevRecord>>>,
    last: Mutex<HashMap<String, JevRecord>>,
    /// 1.0 = recorded latency, 0.0 = instant.
    pub pace: f64,
}

impl JevFixtures {
    pub fn load(dir: impl AsRef<Path>, pace: f64) -> std::io::Result<Self> {
        let text = std::fs::read_to_string(dir.as_ref().join("jev.jsonl"))?;
        let mut queues: HashMap<String, VecDeque<JevRecord>> = HashMap::new();
        for line in text.lines().filter(|l| !l.trim().is_empty()) {
            let rec: JevRecord =
                serde_json::from_str(line).map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
            queues.entry(rec.key.clone()).or_default().push_back(rec);
        }
        Ok(Self { queues: Mutex::new(queues), last: Mutex::new(HashMap::new()), pace })
    }

    /// `sha1(["jev", {"model", "state", "questions"}])`.
    pub fn key_for(model: &str, state: &str, questions: &Value) -> String {
        r3x_llm::pyjson::fixture_key(&[json!("jev"), json!({"model": model, "state": state, "questions": questions})])
    }

    pub fn take(&self, key: &str) -> Option<JevRecord> {
        let popped = self.queues.lock().ok()?.get_mut(key).and_then(VecDeque::pop_front);
        let mut last = self.last.lock().ok()?;
        match popped {
            Some(r) => {
                last.insert(key.into(), r.clone());
                Some(r)
            }
            None => last.get(key).cloned().or_else(|| {
                tracing::warn!(key, "Jev fixture miss");
                None
            }),
        }
    }
}

enum Backend {
    Http(reqwest::Client),
    Replay(Arc<JevFixtures>),
    Off,
}

#[derive(Clone)]
pub struct JevClient {
    backend: Arc<Backend>,
    url: String,
    model: String,
    timeout: Duration,
}

impl JevClient {
    /// Blank key -> an unconfigured client (router inactive, every turn takes the Claude path).
    pub fn new(api_key: &str, timeout: Duration) -> Self {
        let key = api_key.trim();
        let backend = if key.is_empty() {
            Backend::Off
        } else {
            let mut h = reqwest::header::HeaderMap::new();
            if let Ok(v) = format!("Bearer {key}").parse() {
                h.insert(reqwest::header::AUTHORIZATION, v);
            }
            match reqwest::Client::builder()
                .default_headers(h)
                .timeout(timeout)
                .pool_max_idle_per_host(2)
                .pool_idle_timeout(Duration::from_secs(300))
                .tcp_keepalive(Duration::from_secs(30))
                .build()
            {
                Ok(c) => Backend::Http(c),
                Err(e) => {
                    tracing::warn!("Jev http client failed to build: {e}");
                    Backend::Off
                }
            }
        };
        Self { backend: Arc::new(backend), url: JEV_URL.into(), model: JEV_MODEL.into(), timeout }
    }

    pub fn replay(fx: Arc<JevFixtures>) -> Self {
        Self { backend: Arc::new(Backend::Replay(fx)), url: JEV_URL.into(), model: JEV_MODEL.into(), timeout: DEFAULT_TIMEOUT }
    }

    pub fn configured(&self) -> bool {
        !matches!(*self.backend, Backend::Off)
    }

    /// Pay TCP+TLS on engage instead of on the first command. Returns success; failures only log.
    pub async fn prewarm(&self) -> bool {
        let q = json!({"warmup": {"type": "noul", "instructions": "Is this text the single word 'warmup'?",
            "criteria": {"true": "It is the word warmup.", "false": "It is anything else."}}});
        match self.classify(r#"{"utterance": "warmup"}"#, &q).await {
            Some(r) => {
                tracing::info!("Jev connection pre-warmed in {:.0} ms", r.latency_ms);
                true
            }
            None => {
                tracing::warn!("Jev prewarm failed; first real call will pay connection setup");
                false
            }
        }
    }

    /// Ask every question about `state` (already a JSON string) in one round trip.
    /// `None` on any failure; never panics or errors into the caller.
    pub async fn classify(&self, state: &str, questions: &Value) -> Option<JevResult> {
        let started = Instant::now();
        let (status, body) = match &*self.backend {
            Backend::Off => return None,
            Backend::Replay(fx) => {
                let rec = fx.take(&JevFixtures::key_for(&self.model, state, questions))?;
                if fx.pace > 0.0 {
                    tokio::time::sleep(Duration::from_secs_f64(rec.latency_ms / 1000.0 * fx.pace)).await;
                }
                (rec.status, rec.body.get().to_string())
            }
            Backend::Http(http) => {
                let payload = json!({"model": self.model, "state": state, "questions": questions});
                match http.post(&self.url).json(&payload).send().await {
                    Ok(resp) => {
                        let status = resp.status().as_u16();
                        let body = match resp.text().await {
                            Ok(t) => t,
                            Err(e) => {
                                tracing::warn!("Jev request error: body: {e}");
                                return None;
                            }
                        };
                        (status, body)
                    }
                    Err(e) if e.is_timeout() => {
                        tracing::warn!("Jev request timed out after {:.1}s", self.timeout.as_secs_f64());
                        return None;
                    }
                    Err(e) => {
                        tracing::warn!("Jev request error: {e}");
                        return None;
                    }
                }
            }
        };
        let latency_ms = started.elapsed().as_secs_f64() * 1000.0;
        if status != 200 {
            tracing::warn!("Jev request failed (HTTP {status}: {})", body.chars().take(200).collect::<String>());
            return None;
        }
        let r = JevResult::from_json(&body, latency_ms);
        if r.is_none() {
            tracing::warn!("Jev request error: malformed body");
        }
        r
    }
}
