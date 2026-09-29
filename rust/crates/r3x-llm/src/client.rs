//! The Messages client. One model (`CLAUDE_MODEL`) for conversation, summaries and scenes.

use futures_util::StreamExt;
use serde_json::Value;
use std::sync::Arc;
use std::time::Duration;
use tokio::sync::mpsc;
use tokio::task::JoinHandle;

use crate::fixture::ClaudeFixtures;
use crate::provider::{map_model, resolve_provider, ProviderConfig, ProviderKind};
use crate::request::MessagesRequest;
use crate::stream::{Assembler, FinalMessage, SseDecoder, StreamEvent};
use crate::LlmError;

/// Live default (`main.py`). No Haiku-specific paths anywhere.
pub const DEFAULT_MODEL: &str = "claude-sonnet-5";
pub const ANTHROPIC_VERSION: &str = "2023-06-01";
/// Per-read ceiling (CantinaOS `TIMEOUT` 30 s). A stream that goes silent this long fails.
pub const READ_TIMEOUT: Duration = Duration::from_secs(30);

#[derive(Debug, Clone)]
pub struct LlmConfig {
    pub provider: ProviderConfig,
    /// Anthropic id as configured; every call site speaks this one.
    pub requested_model: String,
    /// Id actually sent (translated once, here, for OpenRouter).
    pub wire_model: String,
    /// `output_config.effort` for between-tools models (`SPOKEN_EFFORT`, default `low`).
    pub effort: String,
}

impl LlmConfig {
    /// `None` when no credential resolves: treat the LLM as unavailable.
    pub fn from_lookup(get: impl Fn(&str) -> Option<String>) -> Option<Self> {
        let provider = resolve_provider(&get)?;
        let requested = get("CLAUDE_MODEL").filter(|s| !s.trim().is_empty()).unwrap_or_else(|| DEFAULT_MODEL.into());
        let wire = map_model(requested.trim(), provider.kind);
        Some(Self {
            provider,
            requested_model: requested.trim().to_string(),
            wire_model: wire,
            effort: get("SPOKEN_EFFORT").filter(|s| !s.is_empty()).unwrap_or_else(|| "low".into()),
        })
    }

    pub fn from_env() -> Option<Self> {
        Self::from_lookup(|k| std::env::var(k).ok())
    }
}

enum Backend {
    Http { http: reqwest::Client, cfg: LlmConfig },
    Replay(Arc<ClaudeFixtures>),
}

#[derive(Clone)]
pub struct LlmClient {
    backend: Arc<Backend>,
    requested_model: String,
    wire_model: String,
    effort: String,
}

impl LlmClient {
    pub fn new(cfg: LlmConfig) -> Result<Self, LlmError> {
        let http = reqwest::Client::builder()
            .read_timeout(READ_TIMEOUT)
            .connect_timeout(Duration::from_secs(10))
            .pool_idle_timeout(Duration::from_secs(90))
            .build()
            .map_err(|e| LlmError::Http(e.to_string()))?;
        Ok(Self {
            requested_model: cfg.requested_model.clone(),
            wire_model: cfg.wire_model.clone(),
            effort: cfg.effort.clone(),
            backend: Arc::new(Backend::Http { http, cfg }),
        })
    }

    /// `Ok(None)` when no provider key is configured.
    pub fn from_env() -> Result<Option<Self>, LlmError> {
        LlmConfig::from_env().map(Self::new).transpose()
    }

    /// Replays recorded Claude streams; no network.
    pub fn replay(fixtures: Arc<ClaudeFixtures>, model: &str) -> Self {
        Self {
            backend: Arc::new(Backend::Replay(fixtures)),
            requested_model: model.into(),
            wire_model: model.into(),
            effort: "low".into(),
        }
    }

    pub fn model(&self) -> &str {
        &self.requested_model
    }

    pub fn provider(&self) -> Option<ProviderKind> {
        match &*self.backend {
            Backend::Http { cfg, .. } => Some(cfg.provider.kind),
            Backend::Replay(_) => None,
        }
    }

    /// The exact JSON body a request becomes.
    pub fn body(&self, req: &MessagesRequest, stream: bool) -> Value {
        req.to_body(&self.requested_model, &self.wire_model, &self.effort, stream)
    }

    async fn post(&self, http: &reqwest::Client, cfg: &LlmConfig, body: &Value) -> Result<reqwest::Response, LlmError> {
        let resp = http
            .post(format!("{}/v1/messages", cfg.provider.base_url))
            .header("x-api-key", &cfg.provider.api_key)
            .header("anthropic-version", ANTHROPIC_VERSION)
            .json(body)
            .send()
            .await
            .map_err(|e| LlmError::Http(e.to_string()))?;
        let status = resp.status();
        if !status.is_success() {
            let text = resp.text().await.unwrap_or_default();
            return Err(LlmError::Api { status: status.as_u16(), message: text.chars().take(400).collect() });
        }
        Ok(resp)
    }

    /// Stream a reply. Events: `TextDelta`s and `ToolUse`s as they complete, then `Done`.
    /// Dropping the returned stream cancels the request.
    pub async fn stream(&self, req: &MessagesRequest) -> Result<LlmStream, LlmError> {
        let (tx, rx) = mpsc::channel(64);
        let task = match &*self.backend {
            Backend::Http { http, cfg } => {
                let resp = self.post(http, cfg, &self.body(req, true)).await?;
                tokio::spawn(async move {
                    let (mut dec, mut asm) = (SseDecoder::default(), Assembler::default());
                    let mut bytes = resp.bytes_stream();
                    while let Some(chunk) = bytes.next().await {
                        let chunk = match chunk {
                            Ok(c) => c,
                            Err(e) => {
                                let _ = tx.send(Err(LlmError::Http(e.to_string()))).await;
                                return;
                            }
                        };
                        for (_, data) in dec.push(&chunk) {
                            match asm.feed(&data) {
                                Ok(evs) => {
                                    for ev in evs {
                                        let done = matches!(ev, StreamEvent::Done(_));
                                        if tx.send(Ok(ev)).await.is_err() || done {
                                            return;
                                        }
                                    }
                                }
                                Err(e) => {
                                    let _ = tx.send(Err(e)).await;
                                    return;
                                }
                            }
                        }
                    }
                    let _ = tx.send(Err(LlmError::Protocol("stream ended before message_stop".into()))).await;
                })
            }
            Backend::Replay(fx) => {
                let rec = fx.take("stream", req).ok_or_else(|| LlmError::Fixture("no Claude fixture for this request".into()))?;
                let pace = fx.pace;
                tokio::spawn(async move {
                    for c in rec.chunks {
                        if pace > 0.0 && c.wait > 0.0 {
                            tokio::time::sleep(Duration::from_secs_f64(c.wait * pace)).await;
                        }
                        if tx.send(Ok(StreamEvent::TextDelta(c.text))).await.is_err() {
                            return;
                        }
                    }
                    for t in rec.final_message.tool_uses() {
                        if tx.send(Ok(StreamEvent::ToolUse(t))).await.is_err() {
                            return;
                        }
                    }
                    let _ = tx.send(Ok(StreamEvent::Done(rec.final_message))).await;
                })
            }
        };
        Ok(LlmStream { rx, task })
    }

    /// Non-streaming call (verbal feedback, summaries, scene description).
    pub async fn create(&self, req: &MessagesRequest) -> Result<FinalMessage, LlmError> {
        match &*self.backend {
            Backend::Http { http, cfg } => {
                let resp = self.post(http, cfg, &self.body(req, false)).await?;
                resp.json::<FinalMessage>().await.map_err(|e| LlmError::Protocol(e.to_string()))
            }
            Backend::Replay(fx) => {
                let rec = fx.take("create", req).ok_or_else(|| LlmError::Fixture("no Claude fixture for this request".into()))?;
                if fx.pace > 0.0 && rec.wait > 0.0 {
                    tokio::time::sleep(Duration::from_secs_f64(rec.wait * fx.pace)).await;
                }
                Ok(rec.final_message)
            }
        }
    }

    /// Open the connection ahead of the first turn (CantinaOS does this on engage, with a 30 s
    /// cooldown the caller owns). Errors are for logging only.
    pub async fn warm_up(&self) -> Result<(), LlmError> {
        let req = MessagesRequest::new(10).system("You are a helpful assistant.", false).user("hi").temperature(0.1);
        tokio::time::timeout(Duration::from_secs(5), self.create(&req))
            .await
            .map_err(|_| LlmError::Http("warm-up timed out".into()))?
            .map(|_| ())
    }
}

pub struct LlmStream {
    rx: mpsc::Receiver<Result<StreamEvent, LlmError>>,
    task: JoinHandle<()>,
}

impl LlmStream {
    pub async fn next(&mut self) -> Option<Result<StreamEvent, LlmError>> {
        self.rx.recv().await
    }

    /// Drain to the final message.
    pub async fn finish(mut self) -> Result<FinalMessage, LlmError> {
        while let Some(ev) = self.next().await {
            if let StreamEvent::Done(m) = ev? {
                return Ok(m);
            }
        }
        Err(LlmError::Protocol("stream closed without a final message".into()))
    }
}

impl Drop for LlmStream {
    fn drop(&mut self) {
        self.task.abort();
    }
}
