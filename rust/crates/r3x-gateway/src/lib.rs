//! The gateway (plan §3b): one WebSocket, protocol v1.
//!
//! - **Auth on every bind, loopback included.** A token (`Authorization: Bearer`, or `?token=`
//!   for browsers, which cannot set WS headers) selects a [`ClientInfo`]; a request carrying an
//!   `Origin` must match the allow-list. Refusals happen before the upgrade (401 / 403).
//! - **Source is stamped** from the authenticated client, never read from the message.
//! - **Class allow-list + tier gate** per client; a refused command still gets an `ack`.
//! - Out: `hello` (client, full retained state, profile), then `state` deltas and `event`s;
//!   `ack` for each command, `log` lines, `frames` once the client asks, binary audio (Phase 2).

use std::collections::HashMap;
use std::net::SocketAddr;
use std::sync::Arc;

use axum::extract::ws::{Message, Utf8Bytes, WebSocket, WebSocketUpgrade};
use axum::extract::{Query, State};
use axum::http::{header, HeaderMap, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::get;
use axum::Router;
use futures_util::{SinkExt, StreamExt};
use r3x_bus::{Bus, Received};
use r3x_contracts::{
    AudioMeta, Body, ClientBody, ClientInfo, ClientMessage, Command, Envelope, Frames, Hello,
    LogLine, RobotProfile, Source, TelemetryCommand, PROTOCOL_VERSION,
};
use tokio::net::TcpListener;
use tokio::sync::{broadcast, mpsc};

/// Per-client outbound queue. A client this far behind is disconnected; it resyncs on reconnect.
const OUT_QUEUE: usize = 1024;

#[derive(Clone)]
pub struct ClientAuth {
    pub token: String,
    pub info: ClientInfo,
}

/// Phase 2 plugs voice in here.
#[derive(Clone, Default)]
pub struct AudioHooks {
    /// Client mic audio (binary frames after an `audio` meta message).
    pub inbound: Option<mpsc::Sender<AudioIn>>,
    /// Runtime TTS audio for clients.
    pub outbound: Option<broadcast::Sender<Arc<AudioOut>>>,
    /// The runtime's whole output mix, only to clients that ask (`telemetry.mix_audio`). The
    /// producer copies the mix only while someone is subscribed.
    pub mix: Option<broadcast::Sender<Arc<AudioOut>>>,
}

#[derive(Debug)]
pub struct AudioIn {
    pub client: String,
    pub meta: Option<AudioMeta>,
    pub pcm: axum::body::Bytes,
}

#[derive(Debug)]
pub struct AudioOut {
    pub meta: AudioMeta,
    pub pcm: axum::body::Bytes,
}

/// Runs after the class/tier gate, before a command reaches the bus: `Some(ack)` answers the
/// command here (rate limits, budgets - the public server's hooks).
pub type CommandFilter = Arc<dyn Fn(&ClientInfo, &Command) -> Option<r3x_contracts::Ack> + Send + Sync>;

#[derive(Clone, Default)]
pub struct GatewayConfig {
    pub clients: Vec<ClientAuth>,
    /// Exact `Origin` values allowed (scheme://host:port). Requests without `Origin`
    /// (non-browser clients) pass on the token alone.
    pub origins: Vec<String>,
    pub profile: Option<Arc<RobotProfile>>,
    pub logs: Option<broadcast::Sender<LogLine>>,
    pub audio: AudioHooks,
    pub filter: Option<CommandFilter>,
}

#[derive(Clone)]
struct Shared {
    bus: Bus,
    cfg: Arc<GatewayConfig>,
}

pub fn router(bus: Bus, cfg: GatewayConfig) -> Router {
    Router::new().route("/", get(upgrade)).with_state(Shared { bus, cfg: Arc::new(cfg) })
}

/// Serve until the listener fails.
pub async fn serve(listener: TcpListener, bus: Bus, cfg: GatewayConfig) -> std::io::Result<()> {
    let app = router(bus, cfg).into_make_service_with_connect_info::<SocketAddr>();
    axum::serve(listener, app).await
}

/// Why a handshake was refused.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Refusal {
    Origin,
    Token,
}

/// The handshake check, separate from axum so it is easy to test and reuse.
pub fn authenticate<'a>(
    cfg: &'a GatewayConfig,
    origin: Option<&str>,
    bearer: Option<&str>,
    query_token: Option<&str>,
) -> Result<&'a ClientInfo, Refusal> {
    if let Some(o) = origin {
        let o = o.trim_end_matches('/');
        if !cfg.origins.iter().any(|a| a.trim_end_matches('/') == o) {
            return Err(Refusal::Origin);
        }
    }
    let presented = bearer.filter(|t| !t.is_empty()).or(query_token).unwrap_or("");
    if presented.is_empty() {
        return Err(Refusal::Token);
    }
    cfg.clients
        .iter()
        .find(|c| !c.token.is_empty() && ct_eq(c.token.as_bytes(), presented.as_bytes()))
        .map(|c| &c.info)
        .ok_or(Refusal::Token)
}

fn ct_eq(a: &[u8], b: &[u8]) -> bool {
    a.len() == b.len() && a.iter().zip(b).fold(0u8, |acc, (x, y)| acc | (x ^ y)) == 0
}

async fn upgrade(
    State(sh): State<Shared>,
    headers: HeaderMap,
    Query(q): Query<HashMap<String, String>>,
    ws: WebSocketUpgrade,
) -> Response {
    let origin = headers.get(header::ORIGIN).and_then(|v| v.to_str().ok());
    let bearer = headers
        .get(header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.strip_prefix("Bearer ").or_else(|| v.strip_prefix("bearer ")))
        .map(str::trim);
    match authenticate(&sh.cfg, origin, bearer, q.get("token").map(String::as_str)) {
        Ok(info) => {
            let info = info.clone();
            tracing::info!(client = %info.name, source = ?info.source, "gateway client connected");
            ws.on_upgrade(move |socket| async move {
                connection(socket, sh, info.clone()).await;
                tracing::info!(client = %info.name, "gateway client disconnected");
            })
        }
        Err(r) => {
            tracing::warn!(?r, origin, "refused gateway client");
            match r {
                Refusal::Origin => (StatusCode::FORBIDDEN, "origin not allowed\n").into_response(),
                Refusal::Token => (StatusCode::UNAUTHORIZED, "bad or missing token\n").into_response(),
            }
        }
    }
}

fn text(env: &Envelope) -> Message {
    Message::Text(Utf8Bytes::from(serde_json::to_string(env).expect("envelopes serialise")))
}

/// An unsequenced envelope (frames, logs): not part of the bus's ordered stream.
fn loose(bus: &Bus, body: Body) -> Envelope {
    let c = bus.clock();
    Envelope {
        v: PROTOCOL_VERSION,
        seq: 0,
        t_mono: c.t_mono(),
        t_wall: c.t_wall(),
        source: Source::System,
        id: None,
        re: None,
        conversation_id: None,
        body,
    }
}

fn hello(sh: &Shared, info: &ClientInfo) -> Envelope {
    let body = Hello { client: info.clone(), state: sh.bus.snapshot(), profile: sh.cfg.profile.as_deref().cloned() };
    sh.bus.stamp(Source::System, None, Body::Hello(Box::new(body)))
}

/// Why a client may not send `cmd`, if it may not.
pub fn refuse(info: &ClientInfo, cmd: &Command) -> Option<String> {
    let class = cmd.class();
    if !info.classes.contains(&class) {
        return Some(format!("client {:?} may not send {class:?} commands", info.name));
    }
    if !info.source.may_issue(cmd) {
        return Some(format!("{:?} (max tier {:?}) may not issue this command", info.source, info.source.max_tier()));
    }
    None
}

/// Serve one already-authenticated socket on `bus` (the public server gives each visitor its
/// own bus). Returns when the client goes away.
pub async fn serve_socket(socket: WebSocket, bus: Bus, cfg: Arc<GatewayConfig>, info: ClientInfo) {
    connection(socket, Shared { bus, cfg }, info).await;
}

async fn connection(socket: WebSocket, sh: Shared, info: ClientInfo) {
    let (mut sink, mut stream) = socket.split();
    let (out, mut out_rx) = mpsc::channel::<Message>(OUT_QUEUE);
    let writer = tokio::spawn(async move {
        while let Some(m) = out_rx.recv().await {
            if sink.send(m).await.is_err() {
                break;
            }
        }
    });
    let push = |m: Message| out.try_send(m).is_ok();

    // Subscribe before the snapshot so no delta falls between them.
    let mut tap = sh.bus.subscribe_all();
    let mut frames: Option<broadcast::Receiver<Arc<Frames>>> = None;
    let mut logs = sh.cfg.logs.as_ref().map(|l| l.subscribe());
    let mut audio_out = sh.cfg.audio.outbound.as_ref().map(|a| a.subscribe());
    let mut mix: Option<broadcast::Receiver<Arc<AudioOut>>> = None;
    let mut audio_meta: Option<AudioMeta> = None;
    push(text(&hello(&sh, &info)));

    loop {
        let ok = tokio::select! {
            m = tap.recv() => match m {
                None => break,
                Some(Received::Message(env)) => match env.body {
                    Body::State(_) | Body::Event(_) => push(text(&env)),
                    _ => true,
                },
                Some(Received::Lagged { .. }) => push(text(&hello(&sh, &info))),
            },
            f = recv_opt(&mut frames) => match f {
                Ok(f) => push(text(&loose(&sh.bus, Body::Frames((*f).clone())))),
                Err(broadcast::error::RecvError::Lagged(_)) => true,
                Err(broadcast::error::RecvError::Closed) => { frames = None; true }
            },
            l = recv_opt(&mut logs) => match l {
                Ok(line) => push(text(&loose(&sh.bus, Body::Log(line)))),
                Err(broadcast::error::RecvError::Lagged(_)) => true,
                Err(broadcast::error::RecvError::Closed) => { logs = None; true }
            },
            a = recv_opt(&mut audio_out) => match a {
                Ok(chunk) => push(text(&loose(&sh.bus, Body::Audio(chunk.meta.clone()))))
                    && push(Message::Binary(chunk.pcm.clone())),
                Err(broadcast::error::RecvError::Lagged(_)) => true,
                Err(broadcast::error::RecvError::Closed) => { audio_out = None; true }
            },
            a = recv_opt(&mut mix) => match a {
                Ok(chunk) => push(text(&loose(&sh.bus, Body::Audio(chunk.meta.clone()))))
                    && push(Message::Binary(chunk.pcm.clone())),
                Err(broadcast::error::RecvError::Lagged(n)) => {
                    tracing::warn!(client = %info.name, chunks = n, "mix audio lagged; recording has a gap");
                    true
                }
                Err(broadcast::error::RecvError::Closed) => { mix = None; true }
            },
            msg = stream.next() => match msg {
                None | Some(Err(_)) | Some(Ok(Message::Close(_))) => break,
                Some(Ok(Message::Text(t))) => {
                    match serde_json::from_str::<ClientMessage>(t.as_str()) {
                        Ok(ClientMessage { id, body: ClientBody::Command(cmd) }) => {
                            on_command(&sh, &info, &out, id, cmd, &mut frames, &mut mix);
                        }
                        Ok(ClientMessage { body: ClientBody::Audio(meta), .. }) => audio_meta = Some(meta),
                        Err(e) => {
                            let ack = r3x_contracts::Ack::rejected(format!("bad message: {e}"));
                            let _ = out.try_send(text(&sh.bus.stamp(Source::System, None, Body::Ack(ack))));
                        }
                    }
                    true
                }
                Some(Ok(Message::Binary(pcm))) => {
                    if let Some(tx) = &sh.cfg.audio.inbound {
                        if info.classes.contains(&r3x_contracts::MessageClass::Intent) {
                            let _ = tx.try_send(AudioIn { client: info.name.clone(), meta: audio_meta.clone(), pcm });
                        }
                    }
                    true
                }
                Some(Ok(_)) => true,
            },
        };
        if !ok {
            tracing::warn!(client = %info.name, "gateway client too slow; disconnecting");
            break;
        }
    }
    drop(out);
    writer.abort();
}

fn on_command(
    sh: &Shared,
    info: &ClientInfo,
    out: &mpsc::Sender<Message>,
    id: Option<String>,
    cmd: Command,
    frames: &mut Option<broadcast::Receiver<Arc<Frames>>>,
    mix: &mut Option<broadcast::Receiver<Arc<AudioOut>>>,
) {
    let reply = |ack| {
        let mut env = sh.bus.stamp(Source::System, None, Body::Ack(ack));
        env.re = id.clone();
        text(&env)
    };
    if let Some(reason) = refuse(info, &cmd) {
        let _ = out.try_send(reply(r3x_contracts::Ack::rejected(reason)));
        return;
    }
    if let Some(ack) = sh.cfg.filter.as_ref().and_then(|f| f(info, &cmd)) {
        let _ = out.try_send(reply(ack));
        return;
    }
    // Per-connection: answered here, never reaches the bus.
    if let Command::Telemetry(TelemetryCommand::Frames { enabled }) = cmd {
        *frames = enabled.then(|| sh.bus.subscribe_frames());
        let _ = out.try_send(reply(r3x_contracts::Ack::Accepted));
        return;
    }
    if let Command::Telemetry(TelemetryCommand::MixAudio { enabled }) = cmd {
        let ack = match (&sh.cfg.audio.mix, enabled) {
            (Some(tx), true) => {
                *mix = Some(tx.subscribe());
                r3x_contracts::Ack::Accepted
            }
            (None, true) => r3x_contracts::Ack::rejected("this runtime has no audio output to record"),
            (_, false) => {
                *mix = None;
                r3x_contracts::Ack::Accepted
            }
        };
        let _ = out.try_send(reply(ack));
        return;
    }
    let (bus, out, source) = (sh.bus.clone(), out.clone(), info.source);
    tokio::spawn(async move {
        let ack = bus.command(source, id.clone(), cmd).await;
        let mut env = bus.stamp(Source::System, None, Body::Ack(ack));
        env.re = id;
        let _ = out.send(text(&env)).await;
    });
}

async fn recv_opt<T: Clone>(rx: &mut Option<broadcast::Receiver<T>>) -> Result<T, broadcast::error::RecvError> {
    match rx {
        Some(rx) => rx.recv().await,
        None => std::future::pending().await,
    }
}

/// Local token files: `~/.config/dj-r3x/<name>`, created 0600 with a random value on first use
/// (the same scheme and directory as CantinaOS's tap token).
pub mod tokens {
    use std::io::Write;
    use std::path::PathBuf;

    pub fn config_dir() -> PathBuf {
        let home = std::env::var_os("HOME").map(PathBuf::from).unwrap_or_else(|| PathBuf::from("."));
        home.join(".config/dj-r3x")
    }

    /// `env_var` if set and non-empty, else the file (created if missing).
    pub fn load_or_create(env_var: &str, path: PathBuf) -> std::io::Result<String> {
        if let Ok(t) = std::env::var(env_var) {
            if !t.trim().is_empty() {
                return Ok(t.trim().to_string());
            }
        }
        if let Ok(t) = std::fs::read_to_string(&path) {
            if !t.trim().is_empty() {
                return Ok(t.trim().to_string());
            }
        }
        if let Some(dir) = path.parent() {
            std::fs::create_dir_all(dir)?;
        }
        let token = random_token()?;
        let mut f = open_private(&path)?;
        writeln!(f, "{token}")?;
        Ok(token)
    }

    fn random_token() -> std::io::Result<String> {
        use std::io::Read;
        let mut b = [0u8; 32];
        std::fs::File::open("/dev/urandom")?.read_exact(&mut b)?;
        Ok(b.iter().map(|x| format!("{x:02x}")).collect())
    }

    #[cfg(unix)]
    fn open_private(path: &std::path::Path) -> std::io::Result<std::fs::File> {
        use std::os::unix::fs::OpenOptionsExt;
        std::fs::OpenOptions::new().write(true).create(true).truncate(true).mode(0o600).open(path)
    }

    #[cfg(not(unix))]
    fn open_private(path: &std::path::Path) -> std::io::Result<std::fs::File> {
        std::fs::File::create(path)
    }
}
