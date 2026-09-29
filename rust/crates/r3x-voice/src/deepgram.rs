//! Deepgram streaming STT (plan §7a Turn, §7b fixes).
//!
//! One persistent socket while engaged, as in `deepgram_direct_mic_service.py`, with the
//! fixes from §7b:
//! - the socket **reconnects with backoff** after any close; a turn is refused only while it
//!   is down ([`Stt::begin`] errors);
//! - audio goes out in 20 ms chunks as it is captured;
//! - stop sends `Finalize` and waits (bounded) for the result flagged `from_finalize`,
//!   instead of a fixed 100 ms sleep.
//!
//! Results that arrive outside a turn are dropped, so a late final can never leak into the
//! next turn.

use std::time::Duration;

use anyhow::{anyhow, Result};
use futures_util::{SinkExt, StreamExt};
use serde_json::{json, Value};
use tokio::sync::{broadcast, mpsc, oneshot, watch};
use tokio::time::{Instant, MissedTickBehavior};
use tokio_tungstenite::tungstenite::client::IntoClientRequest;
use tokio_tungstenite::tungstenite::http::HeaderValue;
use tokio_tungstenite::tungstenite::Message;

#[derive(Debug, Clone)]
pub struct DeepgramConfig {
    pub api_key: String,
    /// `wss://api.deepgram.com/v1/listen`; tests point it at a mock.
    pub url: String,
    /// Query parameters (same set CantinaOS uses).
    pub params: Vec<(String, String)>,
    pub keepalive: Duration,
    pub finalize_timeout: Duration,
    pub backoff_min: Duration,
    pub backoff_max: Duration,
}

impl DeepgramConfig {
    pub fn new(api_key: impl Into<String>) -> Self {
        let params = [
            ("model", "nova-3"),
            ("punctuate", "true"),
            ("language", "en-US"),
            ("encoding", "linear16"),
            ("channels", "1"),
            ("sample_rate", "16000"),
            ("interim_results", "true"),
            ("vad_events", "false"),
            ("smart_format", "true"),
        ];
        Self {
            api_key: api_key.into(),
            url: "wss://api.deepgram.com/v1/listen".into(),
            params: params.iter().map(|(k, v)| (k.to_string(), v.to_string())).collect(),
            keepalive: Duration::from_secs(5),
            finalize_timeout: Duration::from_millis(1500),
            backoff_min: Duration::from_millis(500),
            backoff_max: Duration::from_secs(10),
        }
    }

    fn full_url(&self) -> String {
        let q: Vec<String> = self.params.iter().map(|(k, v)| format!("{k}={v}")).collect();
        format!("{}?{}", self.url, q.join("&"))
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Transcript {
    pub turn: String,
    pub text: String,
    pub is_final: bool,
    pub confidence: f64,
}

enum Cmd {
    Begin(String, oneshot::Sender<Result<()>>),
    Audio(Vec<i16>),
    Finish(oneshot::Sender<String>),
    Cancel,
}

/// Handle to the STT task. Cheap to clone.
#[derive(Clone)]
pub struct Stt {
    cmd: mpsc::Sender<Cmd>,
    connected: watch::Receiver<bool>,
    events: broadcast::Sender<Transcript>,
}

impl Stt {
    /// Run the socket while `engaged` is true (INTERACTIVE; plan §4 `state.engagement`).
    pub fn spawn(cfg: DeepgramConfig, engaged: watch::Receiver<bool>) -> Self {
        let (cmd, rx) = mpsc::channel(512);
        let (conn_tx, connected) = watch::channel(false);
        let events = broadcast::channel(256).0;
        tokio::spawn(run(cfg, engaged, rx, conn_tx, events.clone()));
        Self { cmd, connected, events }
    }

    pub fn is_up(&self) -> bool {
        *self.connected.borrow()
    }

    pub fn connected(&self) -> watch::Receiver<bool> {
        self.connected.clone()
    }

    pub fn subscribe(&self) -> broadcast::Receiver<Transcript> {
        self.events.subscribe()
    }

    /// Start a turn. Refused while the socket is down.
    pub async fn begin(&self, turn: &str) -> Result<()> {
        let (tx, rx) = oneshot::channel();
        self.cmd.send(Cmd::Begin(turn.to_owned(), tx)).await.map_err(|_| anyhow!("stt task gone"))?;
        rx.await.map_err(|_| anyhow!("stt task gone"))?
    }

    /// 16 kHz mono PCM for the current turn. Never blocks; drops if the task is swamped.
    pub fn audio(&self, chunk: Vec<i16>) {
        if self.cmd.try_send(Cmd::Audio(chunk)).is_err() {
            tracing::warn!("stt: audio chunk dropped");
        }
    }

    /// `Finalize`, wait (bounded) for the last result, return the turn's final transcript.
    pub async fn finish(&self) -> String {
        let (tx, rx) = oneshot::channel();
        if self.cmd.send(Cmd::Finish(tx)).await.is_err() {
            return String::new();
        }
        rx.await.unwrap_or_default()
    }

    /// Abandon the turn without waiting.
    pub async fn cancel(&self) {
        let _ = self.cmd.send(Cmd::Cancel).await;
    }
}

struct Turn {
    id: String,
    finals: Vec<String>,
    finish: Option<(oneshot::Sender<String>, Instant)>,
}

impl Turn {
    fn text(&self) -> String {
        self.finals.iter().map(|s| s.trim()).filter(|s| !s.is_empty()).collect::<Vec<_>>().join(" ")
    }

    fn done(mut self) {
        let text = self.text();
        if let Some((tx, _)) = self.finish.take() {
            let _ = tx.send(text);
        }
    }
}

async fn run(
    cfg: DeepgramConfig,
    mut engaged: watch::Receiver<bool>,
    mut cmds: mpsc::Receiver<Cmd>,
    connected: watch::Sender<bool>,
    events: broadcast::Sender<Transcript>,
) {
    let mut backoff = cfg.backoff_min;
    let mut turn: Option<Turn> = None;
    loop {
        // Idle (not engaged or backing off): answer commands, refuse turns.
        if !*engaged.borrow() {
            tokio::select! {
                r = engaged.changed() => if r.is_err() { return },
                c = cmds.recv() => match c {
                    None => return,
                    Some(c) => refuse(c, &mut turn),
                },
            }
            continue;
        }
        let ws = match connect(&cfg).await {
            Ok(ws) => ws,
            Err(e) => {
                tracing::warn!(error = %e, retry_in = ?backoff, "deepgram connect failed");
                let until = Instant::now() + backoff;
                backoff = (backoff * 2).min(cfg.backoff_max);
                while Instant::now() < until {
                    tokio::select! {
                        _ = tokio::time::sleep_until(until) => {}
                        c = cmds.recv() => match c { None => return, Some(c) => refuse(c, &mut turn) },
                    }
                }
                continue;
            }
        };
        tracing::info!("deepgram connected");
        backoff = cfg.backoff_min;
        let _ = connected.send(true);
        let result = session(&cfg, ws, &mut engaged, &mut cmds, &mut turn, &events).await;
        let _ = connected.send(false);
        if let Some(t) = turn.take() {
            // The socket died mid-turn: hand back whatever was final so far.
            t.done();
        }
        match result {
            Ok(true) => return,
            Ok(false) => tracing::info!("deepgram disconnected (disengaged)"),
            Err(e) => {
                tracing::warn!(error = %e, "deepgram socket closed; reconnecting");
                tokio::time::sleep(backoff).await;
                backoff = (backoff * 2).min(cfg.backoff_max);
            }
        }
    }
}

fn refuse(c: Cmd, turn: &mut Option<Turn>) {
    match c {
        Cmd::Begin(_, tx) => {
            let _ = tx.send(Err(anyhow!("speech recognition is not connected")));
        }
        Cmd::Finish(tx) => {
            let text = turn.take().map(|t| t.text()).unwrap_or_default();
            let _ = tx.send(text);
        }
        Cmd::Audio(_) => {}
        Cmd::Cancel => *turn = None,
    }
}

type Ws = tokio_tungstenite::WebSocketStream<tokio_tungstenite::MaybeTlsStream<tokio::net::TcpStream>>;

async fn connect(cfg: &DeepgramConfig) -> Result<Ws> {
    let mut req = cfg.full_url().into_client_request()?;
    req.headers_mut().insert("Authorization", HeaderValue::from_str(&format!("Token {}", cfg.api_key))?);
    let (ws, _) = tokio::time::timeout(Duration::from_secs(5), tokio_tungstenite::connect_async(req)).await??;
    Ok(ws)
}

/// Returns Ok(true) when the handle is gone, Ok(false) when disengaged, Err on socket loss.
async fn session(
    cfg: &DeepgramConfig,
    ws: Ws,
    engaged: &mut watch::Receiver<bool>,
    cmds: &mut mpsc::Receiver<Cmd>,
    turn: &mut Option<Turn>,
    events: &broadcast::Sender<Transcript>,
) -> Result<bool> {
    let (mut tx, mut rx) = ws.split();
    let mut keepalive = tokio::time::interval(cfg.keepalive);
    keepalive.set_missed_tick_behavior(MissedTickBehavior::Delay);
    keepalive.tick().await;
    loop {
        let deadline = turn.as_ref().and_then(|t| t.finish.as_ref().map(|f| f.1));
        tokio::select! {
            r = engaged.changed() => {
                if r.is_err() || !*engaged.borrow() {
                    let _ = tx.send(Message::Text(json!({"type": "CloseStream"}).to_string().into())).await;
                    return Ok(r.is_err());
                }
            }
            c = cmds.recv() => match c {
                None => return Ok(true),
                Some(Cmd::Begin(id, reply)) => {
                    if let Some(old) = turn.take() {
                        old.done();
                    }
                    *turn = Some(Turn { id, finals: Vec::new(), finish: None });
                    let _ = reply.send(Ok(()));
                }
                Some(Cmd::Audio(chunk)) => {
                    if turn.as_ref().is_some_and(|t| t.finish.is_none()) {
                        let bytes: Vec<u8> = chunk.iter().flat_map(|s| s.to_le_bytes()).collect();
                        tx.send(Message::Binary(bytes.into())).await?;
                    }
                }
                Some(Cmd::Finish(reply)) => match turn.as_mut() {
                    Some(t) => {
                        t.finish = Some((reply, Instant::now() + cfg.finalize_timeout));
                        tx.send(Message::Text(json!({"type": "Finalize"}).to_string().into())).await?;
                    }
                    None => { let _ = reply.send(String::new()); }
                },
                Some(Cmd::Cancel) => *turn = None,
            },
            _ = keepalive.tick() => {
                tx.send(Message::Text(json!({"type": "KeepAlive"}).to_string().into())).await?;
            }
            _ = async { tokio::time::sleep_until(deadline.unwrap_or_else(Instant::now)).await }, if deadline.is_some() => {
                tracing::info!("deepgram: no finalize result within the bound; using what we have");
                if let Some(t) = turn.take() { t.done(); }
            }
            m = rx.next() => match m {
                None => return Err(anyhow!("socket closed")),
                Some(Err(e)) => return Err(e.into()),
                Some(Ok(Message::Text(t))) => {
                    if on_message(t.as_str(), turn, events) {
                        if let Some(t) = turn.take() { t.done(); }
                    }
                }
                Some(Ok(Message::Close(f))) => return Err(anyhow!("closed by server: {f:?}")),
                Some(Ok(_)) => {}
            },
        }
    }
}

/// Handle one server message; true when it completes a pending `Finalize`.
fn on_message(raw: &str, turn: &mut Option<Turn>, events: &broadcast::Sender<Transcript>) -> bool {
    let Ok(v) = serde_json::from_str::<Value>(raw) else { return false };
    if v.get("type").and_then(Value::as_str) != Some("Results") {
        return false;
    }
    let Some(t) = turn.as_mut() else { return false };
    let alt = &v["channel"]["alternatives"][0];
    let text = alt["transcript"].as_str().unwrap_or("").to_owned();
    let is_final = v["is_final"].as_bool().unwrap_or(false);
    if is_final && !text.trim().is_empty() {
        t.finals.push(text.clone());
    }
    if !text.is_empty() {
        let _ = events.send(Transcript { turn: t.id.clone(), text, is_final, confidence: alt["confidence"].as_f64().unwrap_or(0.0) });
    }
    t.finish.is_some() && v["from_finalize"].as_bool().unwrap_or(false)
}

/// What the scripted STT will "hear" on the next turn(s) (replay/tests; see [`Stt::scripted`]).
#[derive(Clone)]
pub struct SttScript(mpsc::UnboundedSender<String>);

impl SttScript {
    /// Queue the final transcript of the next turn that finishes.
    pub fn hear(&self, text: impl Into<String>) {
        let _ = self.0.send(text.into());
    }
}

impl Stt {
    /// An STT with no socket (`R3X_FIXTURES=replay`, tests): always up; each finished turn's
    /// transcript is the next line queued on the returned [`SttScript`] (empty if none). Mic
    /// audio is accepted and ignored, so the capture path runs exactly as it does live.
    pub fn scripted() -> (Self, SttScript) {
        let (cmd, mut rx) = mpsc::channel::<Cmd>(512);
        let (script_tx, mut script) = mpsc::unbounded_channel::<String>();
        let (_conn_tx, connected) = watch::channel(true);
        let events = broadcast::channel(256).0;
        let ev = events.clone();
        tokio::spawn(async move {
            let _keep_up = _conn_tx;
            let mut turn: Option<String> = None;
            while let Some(c) = rx.recv().await {
                match c {
                    Cmd::Begin(id, tx) => {
                        turn = Some(id);
                        let _ = tx.send(Ok(()));
                    }
                    Cmd::Audio(_) => {}
                    Cmd::Finish(tx) => {
                        let text = script.try_recv().unwrap_or_default();
                        if let Some(id) = turn.take().filter(|_| !text.is_empty()) {
                            let _ = ev.send(Transcript { turn: id, text: text.clone(), is_final: true, confidence: 1.0 });
                        }
                        let _ = tx.send(text);
                    }
                    Cmd::Cancel => turn = None,
                }
            }
        });
        (Self { cmd, connected, events }, SttScript(script_tx))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::net::TcpListener;
    use tokio_tungstenite::accept_async;

    fn results(text: &str, is_final: bool, from_finalize: bool) -> Message {
        Message::Text(
            json!({"type": "Results", "is_final": is_final, "from_finalize": from_finalize,
                   "channel": {"alternatives": [{"transcript": text, "confidence": 0.9}]}})
            .to_string()
            .into(),
        )
    }

    fn cfg(port: u16) -> DeepgramConfig {
        let mut c = DeepgramConfig::new("k");
        c.url = format!("ws://127.0.0.1:{port}/v1/listen");
        c.backoff_min = Duration::from_millis(20);
        c.backoff_max = Duration::from_millis(40);
        c.finalize_timeout = Duration::from_millis(300);
        c
    }

    #[tokio::test]
    async fn turn_streams_audio_and_waits_for_finalize() {
        let l = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = l.local_addr().unwrap().port();
        let server = tokio::spawn(async move {
            let (s, _) = l.accept().await.unwrap();
            let mut ws = accept_async(s).await.unwrap();
            let mut audio_bytes = 0;
            while let Some(Ok(m)) = ws.next().await {
                match m {
                    Message::Binary(b) => {
                        audio_bytes += b.len();
                        if audio_bytes == 640 {
                            ws.send(results("play some", false, false)).await.unwrap();
                            ws.send(results("play some music", true, false)).await.unwrap();
                        }
                    }
                    Message::Text(t) if t.contains("Finalize") => {
                        tokio::time::sleep(Duration::from_millis(50)).await;
                        ws.send(results("please", true, true)).await.unwrap();
                    }
                    _ => {}
                }
            }
            audio_bytes
        });
        let (_e, engaged) = watch::channel(true);
        let stt = Stt::spawn(cfg(port), engaged);
        let mut ev = stt.subscribe();
        let mut up = stt.connected();
        up.wait_for(|u| *u).await.unwrap();
        stt.begin("turn-1").await.unwrap();
        stt.audio(vec![0; 320]);
        stt.audio(vec![0; 320]);
        let first = ev.recv().await.unwrap();
        assert_eq!((first.turn.as_str(), first.is_final), ("turn-1", false));
        let t0 = std::time::Instant::now();
        assert_eq!(stt.finish().await, "play some music please");
        assert!(t0.elapsed() < Duration::from_millis(250), "returned on the finalize result, not the bound");
        drop(stt);
        assert_eq!(server.await.unwrap(), 1280);
    }

    #[tokio::test]
    async fn reconnects_and_refuses_only_while_down() {
        let l = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = l.local_addr().unwrap().port();
        let (go_tx, go_rx) = oneshot::channel::<()>();
        let server = tokio::spawn(async move {
            // First connection: close immediately. Then stay down until told.
            let (s, _) = l.accept().await.unwrap();
            let mut ws = accept_async(s).await.unwrap();
            ws.close(None).await.unwrap();
            drop(ws);
            drop(l);
            go_rx.await.unwrap();
            let l = TcpListener::bind(("127.0.0.1", port)).await.unwrap();
            let (s, _) = l.accept().await.unwrap();
            let mut ws = accept_async(s).await.unwrap();
            while let Some(Ok(m)) = ws.next().await {
                if matches!(m, Message::Text(ref t) if t.contains("Finalize")) {
                    // Never answers: the bounded wait must return anyway.
                }
            }
        });
        let (_e, engaged) = watch::channel(true);
        let stt = Stt::spawn(cfg(port), engaged);
        let mut up = stt.connected();
        up.wait_for(|u| *u).await.unwrap();
        up.wait_for(|u| !*u).await.unwrap();
        assert!(stt.begin("t").await.is_err(), "refused while down");
        go_tx.send(()).unwrap();
        tokio::time::timeout(Duration::from_secs(3), up.wait_for(|u| *u)).await.unwrap().unwrap();
        stt.begin("t2").await.unwrap();
        let t0 = std::time::Instant::now();
        assert_eq!(stt.finish().await, "");
        assert!(t0.elapsed() >= Duration::from_millis(290), "bounded wait");
        server.abort();
    }

    #[tokio::test]
    async fn late_results_never_leak_into_the_next_turn() {
        let (tx, _) = broadcast::channel(8);
        let mut turn = None;
        assert!(!on_message(&results("stale", true, false).into_text().unwrap(), &mut turn, &tx));
        turn = Some(Turn { id: "a".into(), finals: vec![], finish: None });
        on_message(&results("hi", true, false).into_text().unwrap(), &mut turn, &tx);
        assert_eq!(turn.unwrap().text(), "hi");
    }
}
