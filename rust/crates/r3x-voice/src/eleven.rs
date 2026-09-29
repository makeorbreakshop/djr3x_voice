//! ElevenLabs TTS (plan §7a Speech). Behavioural reference: `elevenlabs_dialogue_socket.py`
//! and `ElevenLabsService._open_live_audio_stream`.
//!
//! - v4 models go over the text-to-dialogue WebSocket
//!   (`?model_id=..&output_format=pcm_24000&sync_alignment=true`); the first message is
//!   `{"voices":[voice], "voice_settings":{"stability":s}}` (stability only).
//! - One socket per model+voice, opened at start (warm-up), kept alive with
//!   `{"keep_alive":true}` 10 s after the last *send* (received audio does not reset the
//!   server's 20 s idle timer).
//! - Whole replies: `{"inputs":[{text, voice_id, new_turn:true}]}` then `{"flush":true}`.
//! - Stopping mid-turn drops the socket (the rest of the turn would bleed into the next).
//! - Failure before any audio falls back to the HTTP stream; after audio it is an error and is
//!   never replayed ([`open_speech`]).

use std::sync::Arc;
use std::time::Duration;

use anyhow::{bail, Result};
use base64::Engine;
use futures_util::{SinkExt, StreamExt};
use serde::Deserialize;
use serde_json::{json, Value};
use tokio::sync::mpsc;
use tokio::time::Instant;
use tokio_tungstenite::tungstenite::client::IntoClientRequest;
use tokio_tungstenite::tungstenite::http::HeaderValue;
use tokio_tungstenite::tungstenite::Message;

pub const DEFAULT_MODEL: &str = "eleven_v4_turbo";
pub const DEFAULT_VOICE: &str = "P9l1opNa5pWou2X5MwfB";
pub const KEEPALIVE: Duration = Duration::from_secs(10);
const RECV_TIMEOUT: Duration = Duration::from_secs(10);

#[derive(Debug, Clone)]
pub struct ElevenConfig {
    pub api_key: String,
    pub voice_id: String,
    pub model_id: String,
    pub stability: f64,
    pub similarity_boost: f64,
    pub speed: f64,
    /// `wss://api.elevenlabs.io`; tests point it at a mock.
    pub ws_base: String,
    pub http_base: String,
    pub keepalive: Duration,
    /// `ELEVENLABS_DIALOGUE_SOCKET=false` forces every line onto HTTP.
    pub dialogue_socket: bool,
}

impl ElevenConfig {
    pub fn new(api_key: impl Into<String>) -> Self {
        Self {
            api_key: api_key.into(),
            voice_id: DEFAULT_VOICE.into(),
            model_id: DEFAULT_MODEL.into(),
            stability: 0.60,
            similarity_boost: 0.85,
            speed: 1.1,
            ws_base: "wss://api.elevenlabs.io".into(),
            http_base: "https://api.elevenlabs.io".into(),
            keepalive: KEEPALIVE,
            dialogue_socket: true,
        }
    }

    pub fn uses_dialogue_socket(&self) -> bool {
        self.dialogue_socket && supports_dialogue(&self.model_id)
    }
}

/// v4 Turbo is only accepted on the dialogue socket.
pub fn supports_dialogue(model_id: &str) -> bool {
    model_id.starts_with("eleven_v4")
}

/// `style`/`speed` exist only on the v2.5-era models.
fn takes_speed_and_style(model_id: &str) -> bool {
    !(model_id == "eleven_v3" || model_id.starts_with("eleven_v4"))
}

/// Per-character timing of one chunk, times relative to *that chunk's* start (as sent).
#[derive(Debug, Clone, Default, PartialEq, Deserialize)]
pub struct Alignment {
    #[serde(default)]
    pub chars: Vec<String>,
    #[serde(default, rename = "char_start_times_ms")]
    pub start_ms: Vec<f64>,
    #[serde(default, rename = "char_durations_ms")]
    pub duration_ms: Vec<f64>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct TtsChunk {
    /// 24 kHz mono.
    pub pcm: Vec<i16>,
    pub alignment: Option<Alignment>,
}

pub type ChunkRx = mpsc::Receiver<Result<TtsChunk>>;

/// Where synthesis comes from; mocked in tests.
pub trait TtsBackend: Send + Sync + 'static {
    /// Dialogue-socket stream, or `None` when this model/voice does not use the socket.
    /// Dropping the receiver mid-turn stops the turn (and drops the socket).
    fn dialogue(&self, text: &str) -> Option<ChunkRx>;
    fn http(&self, text: &str) -> ChunkRx;
}

/// First chunk (if any) and the rest, applying the fallback rule: an error before any audio
/// switches to HTTP; an error after audio is left in the stream for the caller to report.
pub async fn open_speech(backend: &dyn TtsBackend, text: &str) -> Result<(Option<TtsChunk>, ChunkRx)> {
    if let Some(mut rx) = backend.dialogue(text) {
        match rx.recv().await {
            Some(Ok(first)) => return Ok((Some(first), rx)),
            None => return Ok((None, rx)),
            Some(Err(e)) => tracing::warn!(error = %e, "dialogue socket failed before any audio; using the HTTP stream"),
        }
    }
    let mut rx = backend.http(text);
    match rx.recv().await {
        Some(Ok(first)) => Ok((Some(first), rx)),
        None => Ok((None, rx)),
        Some(Err(e)) => Err(e),
    }
}

// --------------------------------------------------------------------------- live backend

/// The real ElevenLabs backend: one warm dialogue socket plus HTTP.
pub struct Eleven {
    cfg: ElevenConfig,
    http: reqwest::Client,
    socket: Option<mpsc::Sender<SocketReq>>,
}

impl Eleven {
    /// Starts (and warms) the dialogue socket when the model uses it.
    pub fn start(cfg: ElevenConfig) -> Arc<Self> {
        let socket = cfg.uses_dialogue_socket().then(|| {
            let (tx, rx) = mpsc::channel(8);
            tokio::spawn(socket_task(cfg.clone(), rx));
            tx
        });
        Arc::new(Self { http: reqwest::Client::new(), cfg, socket })
    }
}

impl TtsBackend for Eleven {
    fn dialogue(&self, text: &str) -> Option<ChunkRx> {
        let sock = self.socket.as_ref()?;
        let (tx, rx) = mpsc::channel(64);
        if sock.try_send(SocketReq { text: text.to_owned(), out: tx }).is_err() {
            return None; // socket task gone or busy: HTTP
        }
        Some(rx)
    }

    fn http(&self, text: &str) -> ChunkRx {
        let (tx, rx) = mpsc::channel(64);
        let cfg = self.cfg.clone();
        let client = self.http.clone();
        let text = text.to_owned();
        tokio::spawn(async move {
            if let Err(e) = http_stream(&client, &cfg, &text, &tx).await {
                let _ = tx.send(Err(e)).await;
            }
        });
        rx
    }
}

async fn http_stream(client: &reqwest::Client, cfg: &ElevenConfig, text: &str, tx: &mpsc::Sender<Result<TtsChunk>>) -> Result<()> {
    let mut settings = json!({"stability": cfg.stability, "similarity_boost": cfg.similarity_boost, "use_speaker_boost": true});
    if takes_speed_and_style(&cfg.model_id) {
        settings["style"] = json!(0.25);
        settings["speed"] = json!(cfg.speed.clamp(0.7, 1.2));
    }
    let url = format!("{}/v1/text-to-speech/{}/stream?output_format=pcm_24000", cfg.http_base, cfg.voice_id);
    let resp = client
        .post(url)
        .header("xi-api-key", &cfg.api_key)
        .json(&json!({"text": text, "model_id": cfg.model_id, "voice_settings": settings}))
        .send()
        .await?;
    if !resp.status().is_success() {
        let status = resp.status();
        bail!("ElevenLabs HTTP {status}: {}", resp.text().await.unwrap_or_default().chars().take(300).collect::<String>());
    }
    let mut body = resp.bytes_stream();
    let mut carry: Option<u8> = None;
    while let Some(b) = body.next().await {
        let pcm = bytes_to_pcm(&b?, &mut carry);
        if !pcm.is_empty() && tx.send(Ok(TtsChunk { pcm, alignment: None })).await.is_err() {
            return Ok(()); // consumer stopped
        }
    }
    Ok(())
}

/// Little-endian int16 from a byte stream whose chunks may split a sample.
pub fn bytes_to_pcm(bytes: &[u8], carry: &mut Option<u8>) -> Vec<i16> {
    let mut out = Vec::with_capacity(bytes.len() / 2 + 1);
    let mut it = bytes.iter().copied();
    if let Some(lo) = carry.take() {
        match it.next() {
            Some(hi) => out.push(i16::from_le_bytes([lo, hi])),
            None => {
                *carry = Some(lo);
                return out;
            }
        }
    }
    loop {
        match (it.next(), it.next()) {
            (Some(lo), Some(hi)) => out.push(i16::from_le_bytes([lo, hi])),
            (Some(lo), None) => {
                *carry = Some(lo);
                break;
            }
            _ => break,
        }
    }
    out
}

// ------------------------------------------------------------------------ dialogue socket

struct SocketReq {
    text: String,
    out: mpsc::Sender<Result<TtsChunk>>,
}

type Ws = tokio_tungstenite::WebSocketStream<tokio_tungstenite::MaybeTlsStream<tokio::net::TcpStream>>;

struct Socket {
    ws: Ws,
    last_send: Instant,
}

impl Socket {
    async fn send(&mut self, v: Value) -> Result<()> {
        self.ws.send(Message::Text(v.to_string().into())).await?;
        self.last_send = Instant::now();
        Ok(())
    }
}

async fn open_socket(cfg: &ElevenConfig) -> Result<Socket> {
    let url = format!(
        "{}/v1/text-to-dialogue/stream-input?model_id={}&output_format=pcm_24000&sync_alignment=true",
        cfg.ws_base, cfg.model_id
    );
    let mut req = url.into_client_request()?;
    req.headers_mut().insert("xi-api-key", HeaderValue::from_str(&cfg.api_key)?);
    let (ws, _) = tokio::time::timeout(Duration::from_secs(5), tokio_tungstenite::connect_async(req)).await??;
    let mut s = Socket { ws, last_send: Instant::now() };
    s.send(json!({"voices": [cfg.voice_id], "voice_settings": {"stability": cfg.stability}})).await?;
    tracing::info!(model = %cfg.model_id, "ElevenLabs dialogue socket open");
    Ok(s)
}

/// Owns the socket. Serialises turns; pings when idle; reconnects lazily.
async fn socket_task(cfg: ElevenConfig, mut reqs: mpsc::Receiver<SocketReq>) {
    let mut sock: Option<Socket> = match open_socket(&cfg).await {
        Ok(s) => Some(s),
        Err(e) => {
            tracing::warn!(error = %e, "ElevenLabs dialogue socket warm-up failed; will connect on the first line");
            None
        }
    };
    loop {
        let ping_at = sock.as_ref().map(|s| s.last_send + cfg.keepalive);
        tokio::select! {
            r = reqs.recv() => {
                let Some(req) = r else { return };
                if sock.is_none() {
                    match open_socket(&cfg).await {
                        Ok(s) => sock = Some(s),
                        Err(e) => { let _ = req.out.send(Err(e)).await; continue; }
                    }
                }
                let s = sock.as_mut().expect("opened above");
                if let Err(e) = turn(s, &cfg, &req).await {
                    // Error or a stop mid-turn: never reuse this socket.
                    sock = None;
                    let _ = req.out.send(Err(e)).await;
                }
            }
            _ = async { tokio::time::sleep_until(ping_at.unwrap_or_else(Instant::now)).await }, if ping_at.is_some() => {
                let s = sock.as_mut().expect("guarded");
                if let Err(e) = s.send(json!({"keep_alive": true})).await {
                    tracing::info!(error = %e, "ElevenLabs keep-alive failed; will reconnect on the next line");
                    sock = None;
                }
            }
        }
    }
}

/// One whole reply. Err means drop the socket (including a consumer that went away).
async fn turn(s: &mut Socket, cfg: &ElevenConfig, req: &SocketReq) -> Result<()> {
    s.send(json!({"inputs": [{"text": req.text, "voice_id": cfg.voice_id, "new_turn": true}]})).await?;
    s.send(json!({"flush": true})).await?;
    loop {
        let msg = tokio::select! {
            m = tokio::time::timeout(RECV_TIMEOUT, s.ws.next()) => m,
            _ = req.out.closed() => bail!("stopped mid-turn"),
        };
        let raw = match msg {
            Err(_) => bail!("ElevenLabs dialogue socket: no message for {RECV_TIMEOUT:?}"),
            Ok(None) => bail!("ElevenLabs dialogue socket closed"),
            Ok(Some(m)) => m?,
        };
        let text = match raw {
            Message::Text(t) => t,
            Message::Close(f) => bail!("ElevenLabs dialogue socket closed: {f:?}"),
            _ => continue,
        };
        let v: Value = serde_json::from_str(text.as_str())?;
        if v.get("error").is_some_and(|e| !e.is_null()) || v.get("code").is_some_and(|e| !e.is_null()) {
            bail!("ElevenLabs dialogue socket error: {v}");
        }
        if let Some(audio) = v.get("audio").and_then(Value::as_str).filter(|a| !a.is_empty()) {
            let bytes = base64::engine::general_purpose::STANDARD.decode(audio)?;
            let pcm = bytes_to_pcm(&bytes, &mut None);
            let alignment = v.get("alignment").filter(|a| !a.is_null()).and_then(|a| serde_json::from_value(a.clone()).ok());
            if req.out.send(Ok(TtsChunk { pcm, alignment })).await.is_err() {
                bail!("stopped mid-turn");
            }
        }
        let flag = |k: &str| v.get(k).and_then(Value::as_bool).unwrap_or(false);
        if flag("is_final_audio_for_turn") || flag("is_final") {
            return Ok(());
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use anyhow::anyhow;
    use tokio::net::TcpListener;
    use tokio_tungstenite::accept_async;

    struct Fake {
        dialogue: Vec<Result<Vec<i16>>>,
        http_calls: std::sync::atomic::AtomicUsize,
    }

    impl TtsBackend for Fake {
        fn dialogue(&self, _: &str) -> Option<ChunkRx> {
            let (tx, rx) = mpsc::channel(8);
            for item in &self.dialogue {
                let v = match item {
                    Ok(p) => Ok(TtsChunk { pcm: p.clone(), alignment: None }),
                    Err(e) => Err(anyhow!("{e}")),
                };
                tx.try_send(v).unwrap();
            }
            Some(rx)
        }
        fn http(&self, _: &str) -> ChunkRx {
            self.http_calls.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
            let (tx, rx) = mpsc::channel(8);
            tx.try_send(Ok(TtsChunk { pcm: vec![9], alignment: None })).unwrap();
            rx
        }
    }

    #[tokio::test]
    async fn fallback_only_before_audio() {
        let before = Fake { dialogue: vec![Err(anyhow!("boom"))], http_calls: Default::default() };
        let (first, _) = open_speech(&before, "hi").await.unwrap();
        assert_eq!(first.unwrap().pcm, vec![9], "HTTP served the line");
        assert_eq!(before.http_calls.load(std::sync::atomic::Ordering::SeqCst), 1);

        let after = Fake { dialogue: vec![Ok(vec![1, 2]), Err(anyhow!("mid-turn"))], http_calls: Default::default() };
        let (first, mut rest) = open_speech(&after, "hi").await.unwrap();
        assert_eq!(first.unwrap().pcm, vec![1, 2]);
        assert!(rest.recv().await.unwrap().is_err(), "error surfaces; no replay");
        assert_eq!(after.http_calls.load(std::sync::atomic::Ordering::SeqCst), 0);
    }

    #[test]
    fn pcm_survives_odd_byte_splits() {
        let mut carry = None;
        let mut out = bytes_to_pcm(&[0x01, 0x00, 0xff], &mut carry);
        out.extend(bytes_to_pcm(&[0x7f], &mut carry));
        assert_eq!(out, vec![1, 0x7fff]);
    }

    /// Mock dialogue server: records every client message with its arrival time.
    async fn mock_server() -> (u16, mpsc::UnboundedReceiver<(Instant, Value)>) {
        let l = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = l.local_addr().unwrap().port();
        let (tx, rx) = mpsc::unbounded_channel();
        tokio::spawn(async move {
            while let Ok((s, _)) = l.accept().await {
                let tx = tx.clone();
                tokio::spawn(async move {
                    let Ok(mut ws) = accept_async(s).await else { return };
                    while let Some(Ok(Message::Text(t))) = ws.next().await {
                        let v: Value = serde_json::from_str(t.as_str()).unwrap();
                        let _ = tx.send((Instant::now(), v.clone()));
                        if v.get("flush").is_some() {
                            let audio = base64::engine::general_purpose::STANDARD.encode([1u8, 0, 2, 0]);
                            let align = json!({"chars": ["h", "i"], "char_start_times_ms": [0, 50], "char_durations_ms": [50, 50]});
                            let _ = ws.send(Message::Text(json!({"audio": audio, "alignment": align}).to_string().into())).await;
                            let _ = ws.send(Message::Text(json!({"is_final_audio_for_turn": true}).to_string().into())).await;
                        }
                    }
                });
            }
        });
        (port, rx)
    }

    #[tokio::test]
    async fn dialogue_protocol_and_keepalive_from_last_send() {
        let (port, mut seen) = mock_server().await;
        let mut cfg = ElevenConfig::new("k");
        cfg.ws_base = format!("ws://127.0.0.1:{port}");
        cfg.keepalive = Duration::from_millis(400);
        let eleven = Eleven::start(cfg);

        let (t_init, init) = seen.recv().await.unwrap(); // warm-up connect, before any line
        assert_eq!(init, json!({"voices": [DEFAULT_VOICE], "voice_settings": {"stability": 0.6}}));

        tokio::time::sleep(Duration::from_millis(250)).await;
        let (first, mut rest) = open_speech(eleven.as_ref(), "hi").await.unwrap();
        let chunk = first.unwrap();
        assert_eq!(chunk.pcm, vec![1, 2]);
        assert_eq!(chunk.alignment.unwrap().start_ms, vec![0.0, 50.0]);
        assert!(rest.recv().await.is_none());
        let (t_input, input) = seen.recv().await.unwrap();
        assert_eq!(input, json!({"inputs": [{"text": "hi", "voice_id": DEFAULT_VOICE, "new_turn": true}]}));
        assert_eq!(seen.recv().await.unwrap().1, json!({"flush": true}));
        assert!(t_input - t_init >= Duration::from_millis(250));

        // The next client message is the keep-alive, timed from the flush (our last send),
        // not from the warm-up.
        let (t_ka, ka) = seen.recv().await.unwrap();
        assert_eq!(ka, json!({"keep_alive": true}));
        let since = t_ka - t_input;
        assert!(since >= Duration::from_millis(390) && since < Duration::from_millis(600), "{since:?}");
    }
}
