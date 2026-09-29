//! Bridge mode (plan §9 Phase 1): CantinaOS stays the brain; `r3x` connects to its bus tap,
//! turns tap topics into typed events, state and perf commands ([`translate`]), and turns
//! typed commands into CantinaOS bus emits. Reconnects with backoff; fail-open (a dead tap
//! only means commands are rejected with a reason).
//!
//! Phase 3: the body is r3x's. CantinaOS's show requests (`show.perform` from Claude tags,
//! plan steps, its CLI; `show.stop`; `motion.freeze`; `eye.command`) become perf commands for
//! the native performer, and what the performer does goes back as the topics CantinaOS
//! services still consume ([`Bridge::forward_perf`]).

pub mod translate;

use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use futures_util::{SinkExt, StreamExt};
use r3x_bus::{Bus, CommandRequest, Received};
use r3x_contracts::{
    Ack, Body, Command, ConversationPhase, ConversationState, DjState, Domain, Engagement, EngagementState,
    Event, IntentCommand, MessageClass, MusicCommand, MusicState, PerfEvent, RunKind, ServiceStatus, Source,
    StageState,
};
use serde_json::{json, Value};
use tokio::sync::{mpsc, oneshot, watch};
use tokio_tungstenite::tungstenite::{client::IntoClientRequest, http::HeaderValue, Message};

use translate::{translate, TapState};

/// Legacy console shortcuts (CantinaOS `CLIService.SHORTCUTS` / SimBridge `CLI_SHORTCUTS`).
pub const SHORTCUTS: &[(&str, &str)] = &[
    ("e", "engage"),
    ("a", "ambient"),
    ("d", "disengage"),
    ("h", "help"),
    ("st", "status"),
    ("r", "reset"),
    ("l", "list music"),
    ("p", "play music"),
    ("s", "stop music"),
];

const EMIT_TIMEOUT: Duration = Duration::from_secs(2);
const MODE_TIMEOUT: Duration = Duration::from_secs(3);
/// How long a held push-to-talk keeps retrying `mic.recording.start` (SimBridge: 5 s).
const PTT_START_TIMEOUT: Duration = Duration::from_secs(5);

#[derive(Clone)]
pub struct BridgeConfig {
    pub tap_url: String,
    pub tap_token: String,
    /// A show's `duck`: the profile's ducking level and ramp.
    pub duck_level: f64,
    pub duck_ramp_ms: f64,
    /// CantinaOS still drives the face/chest boards (r3x runs without LED drivers): mirror a
    /// show's eye/chest actions to it as `eye.command` / `chest.override`.
    pub cantina_leds: bool,
    /// `--music rust`: r3x plays the music and answers MusicController's topics.
    pub music: Option<r3x_music::Engine>,
}

impl BridgeConfig {
    pub fn new(tap_url: String, tap_token: String, profile: &r3x_contracts::RobotProfile) -> Self {
        BridgeConfig {
            tap_url,
            tap_token,
            duck_level: profile.audio.ducking.level,
            duck_ramp_ms: profile.audio.ducking.ramp_ms,
            cantina_leds: false,
            music: None,
        }
    }
}

/// The live tap connection, shared by the reader and every command handler.
#[derive(Default)]
struct TapLink {
    tx: Mutex<Option<mpsc::UnboundedSender<String>>>,
    pending: Mutex<HashMap<String, oneshot::Sender<Result<(), String>>>>,
    next: AtomicU64,
}

impl TapLink {
    /// Emit `topic` on the CantinaOS bus; resolves when the tap confirms the emit.
    async fn emit(&self, topic: &str, payload: Value) -> Result<(), String> {
        let id = format!("r{}", self.next.fetch_add(1, Ordering::Relaxed));
        let (tx, rx) = oneshot::channel();
        self.pending.lock().unwrap().insert(id.clone(), tx);
        let msg = json!({ "topic": topic, "payload": payload, "id": id, "source": "r3x" }).to_string();
        let sent = self.tx.lock().unwrap().as_ref().is_some_and(|t| t.send(msg).is_ok());
        if !sent {
            self.pending.lock().unwrap().remove(&id);
            return Err("CantinaOS is not connected (bus tap down)".into());
        }
        match tokio::time::timeout(EMIT_TIMEOUT, rx).await {
            Ok(Ok(r)) => r,
            _ => {
                self.pending.lock().unwrap().remove(&id);
                Err(format!("CantinaOS did not confirm {topic}"))
            }
        }
    }

    /// Emit as `r3x` without waiting for the confirmation (in order, fire-and-forget).
    fn post(&self, topic: &str, payload: Value) {
        let msg = json!({ "topic": topic, "payload": payload, "source": "r3x" }).to_string();
        if let Some(t) = self.tx.lock().unwrap().as_ref() {
            let _ = t.send(msg);
        }
    }

    /// Emit without waiting for the tap's confirmation (high-rate voice topics).
    fn send(&self, topic: &str, payload: Value) {
        self.send_as("r3x-voice", topic, payload);
    }

    fn send_as(&self, source: &str, topic: &str, payload: Value) {
        let msg = json!({ "topic": topic, "payload": payload, "source": source }).to_string();
        if let Some(t) = self.tx.lock().unwrap().as_ref() {
            let _ = t.send(msg);
        }
    }

    fn on_ack(&self, msg: &Value) {
        let Some(re) = msg.get("re").and_then(Value::as_str) else { return };
        if let Some(tx) = self.pending.lock().unwrap().remove(re) {
            let ok = msg.get("ok").and_then(Value::as_bool).unwrap_or(false);
            let err = msg.get("error").and_then(Value::as_str).unwrap_or("rejected").to_owned();
            let _ = tx.send(if ok { Ok(()) } else { Err(err) });
        }
    }

    fn set(&self, tx: Option<mpsc::UnboundedSender<String>>) {
        *self.tx.lock().unwrap() = tx;
        if self.tx.lock().unwrap().is_none() {
            for (_, p) in self.pending.lock().unwrap().drain() {
                let _ = p.send(Err("CantinaOS connection lost".into()));
            }
        }
    }
}

struct Bridge {
    bus: Bus,
    cfg: BridgeConfig,
    tap: TapLink,
    tap_state: Mutex<TapState>,
    ptt_held: AtomicBool,
    /// Phase 2: r3x owns mic/STT/TTS (CantinaOS runs with `R3X_EXTERNAL_VOICE=1`).
    voice: Option<r3x_voice::Voice>,
    cantina_voice: std::sync::OnceLock<Arc<r3x_voice::cantina::CantinaVoice>>,
    cantina_music: std::sync::OnceLock<Arc<r3x_music::cantina::CantinaMusic>>,
}

/// Start the bridge. Takes the `intent` command class; returns the engagement backend for the
/// StageManager. (The `perf` class belongs to the performer.)
pub fn spawn(bus: &Bus, cfg: BridgeConfig, voice: Option<r3x_voice::Voice>) -> anyhow::Result<r3x_stage::EngagementBackend> {
    let intent = bus.take_commands(MessageClass::Intent).ok_or_else(|| anyhow::anyhow!("intent class taken"))?;
    let b = Arc::new(Bridge {
        bus: bus.clone(),
        cfg,
        tap: TapLink::default(),
        tap_state: Mutex::default(),
        ptt_held: AtomicBool::new(false),
        voice,
        cantina_voice: Default::default(),
        cantina_music: Default::default(),
    });
    if let Some(engine) = b.cfg.music.clone() {
        let tap = Arc::downgrade(&b);
        let emit = move |topic: &str, payload: Value| {
            if let Some(b) = tap.upgrade() {
                b.tap.send_as(r3x_music::cantina::TAP_SOURCE, topic, payload);
            }
        };
        let _ = b.cantina_music.set(r3x_music::cantina::CantinaMusic::attach(&engine, Arc::new(emit)));
    }
    if let Some(v) = &b.voice {
        let tap = Arc::downgrade(&b);
        let emit = move |topic: &str, payload: Value| {
            if let Some(b) = tap.upgrade() {
                b.tap.send(topic, payload);
            }
        };
        let _ = b.cantina_voice.set(r3x_voice::cantina::CantinaVoice::attach(v, Arc::new(emit)));
    }
    let (eng_tx, mut eng_rx) = mpsc::channel::<(Engagement, oneshot::Sender<Ack>)>(8);

    tokio::spawn(b.clone().run_tap());
    {
        let (b, mut rx) = (b.clone(), intent);
        tokio::spawn(async move {
            while let Some(req) = rx.recv().await {
                // Concurrent: a held push-to-talk must not block a stop.
                let b = b.clone();
                tokio::spawn(async move { b.handle(req).await });
            }
        });
    }
    tokio::spawn(b.clone().forward_perf());
    {
        let b = b.clone();
        tokio::spawn(async move {
            while let Some((e, reply)) = eng_rx.recv().await {
                let _ = reply.send(b.engage(e).await);
            }
        });
    }
    tokio::spawn(b.follow_stage());
    Ok(eng_tx)
}

impl Bridge {
    // ------------------------------------------------------------------ tap connection

    async fn run_tap(self: Arc<Self>) {
        let mut backoff = Duration::from_millis(500);
        loop {
            match self.connect_once().await {
                Ok(()) => backoff = Duration::from_millis(500),
                Err(e) => tracing::debug!("tap: {e}"),
            }
            self.tap.set(None);
            r3x_ops::report(&self.bus, "bridge", ServiceStatus::Degraded, Some("CantinaOS bus tap unreachable".into()));
            tokio::time::sleep(backoff).await;
            backoff = (backoff * 2).min(Duration::from_secs(10));
        }
    }

    async fn connect_once(&self) -> anyhow::Result<()> {
        let mut req = self.cfg.tap_url.as_str().into_client_request()?;
        req.headers_mut().insert("Authorization", HeaderValue::from_str(&format!("Bearer {}", self.cfg.tap_token))?);
        let (ws, _) = tokio_tungstenite::connect_async(req).await?;
        let (mut sink, mut stream) = ws.split();
        let (tx, mut rx) = mpsc::unbounded_channel::<String>();
        self.tap.set(Some(tx));
        tracing::info!(url = %self.cfg.tap_url, "bridge connected to the CantinaOS bus tap");
        r3x_ops::report(&self.bus, "bridge", ServiceStatus::Running, None);
        if let Some(cm) = self.cantina_music.get() {
            cm.on_connect(); // BrainService needs the library for DJ mode
        } else if self.bus.get::<MusicState>().library.is_empty() {
            // The library event fired at boot; ask again (the reply is parsed in translate).
            let _ = self.tap.tx.lock().unwrap().as_ref().map(|t| t.send(cli_payload_msg("list music")));
        }
        loop {
            tokio::select! {
                out = rx.recv() => match out {
                    Some(m) => sink.send(Message::Text(m.into())).await?,
                    None => return Ok(()),
                },
                inbound = stream.next() => match inbound {
                    None => return Ok(()),
                    Some(m) => match m? {
                        Message::Text(t) => self.on_tap(t.as_str()),
                        Message::Close(_) => return Ok(()),
                        _ => {}
                    },
                },
            }
        }
    }

    fn on_tap(&self, raw: &str) {
        let Ok(msg) = serde_json::from_str::<Value>(raw) else { return };
        match msg.get("kind").and_then(Value::as_str) {
            Some("event") => {
                let topic = msg.get("topic").and_then(Value::as_str).unwrap_or("");
                let payload = msg.get("payload").unwrap_or(&Value::Null);
                // Our own emits come back through the tap as `tap:r3x...`.
                let src = msg.get("source").and_then(Value::as_str).unwrap_or("");
                if src.strip_prefix("tap:") == Some(r3x_music::cantina::TAP_SOURCE) {
                    return; // the engine already published its own state and events
                }
                let ours = src.starts_with("tap:r3x");
                if let Some(cm) = self.cantina_music.get() {
                    cm.on_tap(topic, payload);
                }
                translate(&self.bus, &mut self.tap_state.lock().unwrap(), topic, payload, ours);
                if let Some(cv) = self.cantina_voice.get() {
                    cv.on_tap(topic, payload);
                }
            }
            Some("ack") => self.tap.on_ack(&msg),
            _ => {}
        }
    }

    async fn emit(&self, topic: &str, payload: Value) -> Result<(), String> {
        self.tap.emit(topic, payload).await
    }

    /// `cli.command`, exactly as CantinaOS's `CLIService` emits it for a typed line.
    async fn console(&self, line: &str) -> Ack {
        let line = expand_shortcut(line);
        let first = line.split_whitespace().next().unwrap_or("");
        if first.is_empty() {
            return Ack::rejected("empty command");
        }
        if matches!(first, "quit" | "exit" | "q") {
            return Ack::rejected("stop CantinaOS from its terminal");
        }
        to_ack(self.emit("cli.command", cli_payload(&line)).await)
    }

    // ------------------------------------------------------------------ commands

    async fn handle(&self, req: CommandRequest) {
        let source = req.source();
        let ack = match req.command.clone() {
            Command::Intent(i) => self.intent(source, i).await,
            other => Ack::rejected(format!("bridge does not handle {:?}", other.class())),
        };
        req.ack(ack);
    }

    async fn intent(&self, source: Source, cmd: IntentCommand) -> Ack {
        let stage = self.bus.get::<StageState>();
        match cmd {
            IntentCommand::Say { .. } | IntentCommand::PttStart if !stage.brain => {
                Ack::rejected(format!("the brain is off in {:?} mode", stage.mode))
            }
            IntentCommand::Say { text } => self.say(text.trim()).await,
            IntentCommand::PttStart if self.voice.is_some() => self.voice_ptt_start(source).await,
            IntentCommand::PttStop if self.voice.is_some() => self.voice_ptt_stop().await,
            IntentCommand::PttStart => self.ptt_start(source).await,
            IntentCommand::PttStop => self.ptt_stop().await,
            IntentCommand::Music(m) => {
                let line = match m {
                    MusicCommand::Play { query: Some(q) } if !q.trim().is_empty() => format!("play music {}", q.trim()),
                    MusicCommand::Play { .. } => "play music".into(),
                    MusicCommand::Stop => "stop music".into(),
                    // `next music` does nothing outside DJ mode (CLAUDE.md 3b); `dj next` does.
                    MusicCommand::Next if self.bus.get::<DjState>().active => "dj next".into(),
                    MusicCommand::Next => "next music".into(),
                };
                self.console(&line).await
            }
            IntentCommand::Dj { active: true } if !stage.autonomy => {
                Ack::rejected(format!("DJ autonomy is off in {:?} mode", stage.mode))
            }
            IntentCommand::Dj { active } => self.console(if active { "dj start" } else { "dj stop" }).await,
            IntentCommand::Console { line } => self.console(&line).await,
        }
    }

    /// Wait until `state.engagement` is `to`, requesting it if needed.
    async fn engage(&self, to: Engagement) -> Ack {
        let mut w = self.bus.watch::<EngagementState>();
        if w.borrow().engagement == to {
            return Ack::Accepted;
        }
        let mode = format!("{to:?}").to_ascii_uppercase();
        if let Err(e) = self.emit("system.set.mode.request", json!({ "mode": mode })).await {
            return Ack::rejected(e);
        }
        let switched = tokio::time::timeout(MODE_TIMEOUT, w.wait_for(|s| s.engagement == to)).await.is_ok_and(|r| r.is_ok());
        if switched {
            Ack::Accepted
        } else {
            Ack::rejected(format!("CantinaOS did not switch to {mode}"))
        }
    }

    /// A typed utterance through the voice loop exactly as a spoken one (SimBridge `say`).
    async fn say(&self, text: &str) -> Ack {
        if text.is_empty() {
            return Ack::rejected("nothing to say");
        }
        if self.bus.get::<ConversationState>().phase == ConversationPhase::Listening {
            return Ack::rejected("the mic is live - release push-to-talk first");
        }
        let _ = self.engage(Engagement::Interactive).await; // proceed regardless, as SimBridge does
        let id = uuid::Uuid::new_v4().to_string();
        let now = self.bus.clock().t_wall();
        let started = json!({ "conversation_id": id, "timestamp": now, "source": "panel" });
        if let Err(e) = self.emit("voice.listening.started", started).await {
            return Ack::rejected(e);
        }
        tokio::time::sleep(Duration::from_millis(50)).await;
        let stopped = json!({ "transcript": text, "conversation_id": id, "has_transcript": true, "source": "panel" });
        to_ack(self.emit("voice.listening.stopped", stopped).await)
    }

    /// Push-to-talk on r3x-voice: engage, then open the turn (local mic or the client's
    /// streamed audio). Ownership lives in `state.conversation.ptt_owner`.
    async fn voice_ptt_start(&self, source: Source) -> Ack {
        let ack = self.engage(Engagement::Interactive).await;
        if !ack.is_accepted() {
            return ack;
        }
        let owner = format!("{source:?}").to_ascii_lowercase();
        self.voice.as_ref().expect("guarded").ptt_start(&owner).await
    }

    async fn voice_ptt_stop(&self) -> Ack {
        self.voice.as_ref().expect("guarded").ptt_stop(None).await
    }

    async fn ptt_start(&self, source: Source) -> Ack {
        self.ptt_held.store(true, Ordering::SeqCst);
        let mut conv = self.bus.watch::<ConversationState>();
        if conv.borrow().phase == ConversationPhase::Listening {
            return Ack::Accepted;
        }
        // Tells MouseInputService a panel owns push-to-talk (SimBridge's handshake).
        let panel = json!({ "service_name": "control_panel", "status": "RUNNING", "message": "r3x gateway client", "severity": "INFO" });
        let _ = self.emit("service.status.update", panel).await;
        let ack = self.engage(Engagement::Interactive).await;
        if !ack.is_accepted() {
            return ack;
        }
        let owner = format!("{source:?}").to_ascii_lowercase();
        self.bus.update(Source::Bridge, |c: &mut ConversationState| c.ptt_owner = Some(owner));
        // The mic service silently refuses a start while R3X is speaking or before its socket
        // is up, so retry while the button is held until listening is confirmed.
        let deadline = tokio::time::Instant::now() + PTT_START_TIMEOUT;
        while self.ptt_held.load(Ordering::SeqCst) && tokio::time::Instant::now() < deadline {
            if let Err(e) = self.emit("mic.recording.start", json!({ "source": "panel" })).await {
                return Ack::rejected(e);
            }
            let listening = conv.wait_for(|c| c.phase == ConversationPhase::Listening);
            if tokio::time::timeout(Duration::from_millis(800), listening).await.is_ok_and(|r| r.is_ok()) {
                return Ack::Accepted;
            }
        }
        self.bus.update(Source::Bridge, |c: &mut ConversationState| c.ptt_owner = None);
        if !self.ptt_held.load(Ordering::SeqCst) {
            return Ack::rejected("released before the mic started");
        }
        Ack::rejected("mic did not start - R3X may still be speaking, or Deepgram is not connected")
    }

    async fn ptt_stop(&self) -> Ack {
        self.ptt_held.store(false, Ordering::SeqCst);
        self.bus.update(Source::Bridge, |c: &mut ConversationState| c.ptt_owner = None);
        if self.bus.get::<ConversationState>().phase != ConversationPhase::Listening {
            return Ack::Accepted;
        }
        // Same pair MouseInputService emits: the first flips the eyes to thinking at once.
        let _ = self.emit("mouse.recording.stopped", json!({})).await;
        to_ack(self.emit("mic.recording.stop", json!({})).await)
    }

    // ------------------------------------------------------------------ performer -> CantinaOS

    /// What the performer did, as the topics CantinaOS services consume: run lifecycle (plan
    /// steps wait on `show.ended`, the CLI reports refusals), a show's spoken line (the
    /// voice's FIFO), ducking, sfx and stage lights (SimBridge), and eye/chest actions while
    /// CantinaOS owns the LED boards.
    async fn forward_perf(self: Arc<Self>) {
        let mut rx = self.bus.subscribe(Domain::Perf);
        let mut speech = 0u64;
        while let Some(m) = rx.recv().await {
            let Received::Message(env) = m else { continue };
            let Body::Event(Event::Perf(ev)) = &env.body else { continue };
            if env.source == Source::Bridge {
                continue;
            }
            let run = |id: &str, kind: RunKind, source: Source, run_id: u64| {
                json!({ "id": id, "kind": kind, "source": source, "run_id": run_id.to_string(),
                        "conversation_id": env.conversation_id })
            };
            let (topic, payload) = match ev {
                PerfEvent::Started { id, kind, source, run_id } => ("show.started", run(id, *kind, *source, *run_id)),
                PerfEvent::Ended { id, kind, source, run_id, reason } => {
                    let mut p = run(id, *kind, *source, *run_id);
                    p["reason"] = json!(reason);
                    ("show.ended", p)
                }
                PerfEvent::Sfx { id } => ("show.sfx", json!({ "id": id })),
                PerfEvent::Speak { text } => {
                    speech += 1;
                    let clip = format!("show-r3x-{speech}");
                    ("tts.generate.request", json!({ "text": text, "clip_id": clip, "step_id": clip, "plan_id": "r3x-performer", "conversation_id": null }))
                }
                PerfEvent::Duck { on: true } => {
                    ("audio.ducking.start", json!({ "level": self.cfg.duck_level, "fade_ms": self.cfg.duck_ramp_ms }))
                }
                PerfEvent::Duck { on: false } => ("audio.ducking.stop", json!({ "fade_ms": self.cfg.duck_ramp_ms })),
                PerfEvent::Lights { cue, mode, rig, fade, hold } => {
                    ("stage.lights", json!({ "cue": cue, "mode": mode, "rig": rig, "fade": fade, "hold": hold }))
                }
                PerfEvent::Eyes { pattern, duration } if self.cfg.cantina_leds => {
                    ("eye.command", json!({ "pattern": pattern, "duration": duration }))
                }
                PerfEvent::Chest { command, hold } if self.cfg.cantina_leds => {
                    ("chest.override", json!({ "command": command, "hold": hold.unwrap_or(0.0) }))
                }
                _ => continue,
            };
            self.tap.post(topic, payload);
        }
    }

    // ------------------------------------------------------------------ stage -> CantinaOS

    /// Carry stage switches CantinaOS understands: freeze, brain off (disengage), autonomy
    /// off (DJ stop). Outputs and alive layers belong to the performer.
    async fn follow_stage(self: Arc<Self>) {
        let mut w: watch::Receiver<StageState> = self.bus.watch();
        let mut prev = w.borrow_and_update().clone();
        while w.changed().await.is_ok() {
            let cur = w.borrow_and_update().clone();
            let cantina_frozen = self.tap_state.lock().unwrap().cantina_frozen;
            if cur.frozen != cantina_frozen {
                if let Err(e) = self.emit("motion.freeze", json!({ "on": cur.frozen })).await {
                    tracing::warn!("freeze not delivered: {e}");
                }
            }
            if prev.brain && !cur.brain && self.bus.get::<EngagementState>().engagement == Engagement::Interactive {
                let _ = self.engage(Engagement::Idle).await;
            }
            if prev.autonomy && !cur.autonomy && self.bus.get::<DjState>().active {
                let _ = self.console("dj stop").await;
            }
            prev = cur;
        }
    }
}

fn to_ack(r: Result<(), String>) -> Ack {
    r.map_or_else(Ack::rejected, |_| Ack::Accepted)
}

pub fn expand_shortcut(line: &str) -> String {
    let line = line.trim();
    let (first, rest) = line.split_once(char::is_whitespace).unwrap_or((line, ""));
    let first = first.to_ascii_lowercase();
    match SHORTCUTS.iter().find(|(k, _)| *k == first) {
        Some((_, full)) if rest.is_empty() => (*full).to_owned(),
        Some((_, full)) => format!("{full} {}", rest.trim()),
        None if rest.is_empty() => first,
        None => format!("{first} {}", rest.trim()),
    }
}

fn cli_payload(line: &str) -> Value {
    let mut parts = line.split_whitespace();
    let command = parts.next().unwrap_or("").to_owned();
    let args: Vec<&str> = parts.collect();
    json!({ "command": command, "args": args, "raw_input": line, "conversation_id": null })
}

fn cli_payload_msg(line: &str) -> String {
    json!({ "topic": "cli.command", "payload": cli_payload(line), "source": "r3x" }).to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn shortcuts_expand_like_cantina() {
        assert_eq!(expand_shortcut("p cantina band"), "play music cantina band");
        assert_eq!(expand_shortcut("st"), "status");
        assert_eq!(expand_shortcut("EYE pattern happy"), "eye pattern happy");
        let p = cli_payload("play music cantina band");
        assert_eq!(p["command"], "play");
        assert_eq!(p["args"], json!(["music", "cantina", "band"]));
    }
}
