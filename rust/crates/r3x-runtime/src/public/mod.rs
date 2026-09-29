//! `r3x-runtime --public` (plan Phase 10): R3X for anonymous web visitors.
//!
//! - **One brain per connection.** Each visitor gets its own bus, stage, performer (frames only,
//!   on the shared read-only show catalogue), public brain (its own `SessionMemory`, no memory
//!   database, no Jev router, eye tools only) and, with voice, its own Deepgram/ElevenLabs stack
//!   whose speech goes to that visitor alone. A session runs on its own thread and tokio
//!   runtime, so ending it (disconnect, idle, token expiry) cancels everything it started.
//! - **No local anything**: no audio device, drivers, camera, vision or music (plan §12 Q5).
//! - **Auth**: visitor tokens ([`token`]) minted by `POST /token` with the admin bearer; the
//!   `Origin` allow-list is `R3X_ALLOWED_ORIGINS` only (no localhost defaults).
//! - **Tier**: the `public` source - say, push-to-talk, cheap perf (the gateway and the bus
//!   refuse everything else; the performer holds plays to `cheap`).
//! - **Limits** ([`budget`]): per-IP connects and sessions, per-visitor turn and command
//!   rates, per-visitor and daily LLM-token / TTS-character caps. A spent cap is answered with
//!   a refusal line shown as R3X's reply, never synthesised.
//! - **TLS** is a reverse proxy's job (Caddy; `rust/README.md`). Bind to loopback behind it and
//!   set `R3X_PUBLIC_TRUST_PROXY=1` so limits key on `X-Forwarded-For`.

pub mod budget;
pub mod token;

use std::collections::HashMap;
use std::net::{IpAddr, SocketAddr};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use axum::extract::ws::{WebSocket, WebSocketUpgrade};
use axum::extract::{ConnectInfo, Query, State};
use axum::http::{header, HeaderMap, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use r3x_brain::{Brain, BrainConfig, BrainDeps, Ptt};
use r3x_bus::{Bus, Received};
use r3x_contracts::{
    Ack, Body, ClientInfo, Command, ConversationEvent, Domain, Engagement, Event, IntentCommand, MessageClass, RobotProfile,
    Source, StageCommand,
};
use r3x_gateway::GatewayConfig;
use r3x_performer_core::show::catalog::Catalog;
use r3x_voice::speaker::SpeechRequest;

pub use budget::{Budgets, Limits, Spend};
pub use token::{mint, verify, Visitor};

use budget::Bucket;

pub struct PublicConfig {
    /// HMAC key for visitor tokens (`R3X_PUBLIC_SECRET`).
    pub secret: Vec<u8>,
    /// Bearer for `POST /token` (`R3X_PUBLIC_ADMIN_TOKEN`); `None` = no mint endpoint.
    pub admin_token: Option<String>,
    /// Exact `Origin`s allowed (`R3X_ALLOWED_ORIGINS`). Requests without `Origin` pass on the
    /// token alone (non-browser clients), as on the private gateway.
    pub origins: Vec<String>,
    /// Lifetime of a minted token (`R3X_PUBLIC_TOKEN_TTL_S`, default 900).
    pub token_ttl: Duration,
    pub limits: Limits,
    /// Key limits on the first `X-Forwarded-For` hop (`R3X_PUBLIC_TRUST_PROXY=1`); only
    /// behind a proxy that sets it.
    pub trust_proxy: bool,
    pub profile: Arc<RobotProfile>,
    pub show_dir: PathBuf,
    /// Per-session Deepgram + ElevenLabs (`false` = typed turns, text replies).
    pub voice: bool,
}

impl PublicConfig {
    pub fn from_env(profile: Arc<RobotProfile>, show_dir: PathBuf, voice: bool) -> anyhow::Result<Self> {
        let env = |k: &str| std::env::var(k).ok().map(|v| v.trim().to_owned()).filter(|v| !v.is_empty());
        let secret = env("R3X_PUBLIC_SECRET").ok_or_else(|| anyhow::anyhow!("--public needs R3X_PUBLIC_SECRET (the visitor-token HMAC key)"))?;
        anyhow::ensure!(secret.len() >= 32, "R3X_PUBLIC_SECRET must be at least 32 characters");
        let origins: Vec<String> = env("R3X_ALLOWED_ORIGINS")
            .unwrap_or_default()
            .split(',')
            .map(|o| o.trim().trim_end_matches('/').to_owned())
            .filter(|o| !o.is_empty())
            .collect();
        if origins.is_empty() {
            tracing::warn!("R3X_ALLOWED_ORIGINS is empty: no browser page can connect");
        }
        Ok(Self {
            secret: secret.into_bytes(),
            admin_token: env("R3X_PUBLIC_ADMIN_TOKEN"),
            origins,
            token_ttl: Duration::from_secs(env("R3X_PUBLIC_TOKEN_TTL_S").and_then(|v| v.parse().ok()).unwrap_or(900)),
            limits: Limits::from_env(),
            trust_proxy: env("R3X_PUBLIC_TRUST_PROXY").is_some_and(|v| v == "1" || v == "true"),
            profile,
            show_dir,
            voice,
        })
    }
}

#[derive(Default)]
struct IpState {
    connects: Option<Bucket>,
    sessions: usize,
}

/// The public server: shared caps, the catalogue, and the live sessions.
pub struct PublicServer {
    cfg: PublicConfig,
    catalog: Arc<Catalog>,
    budgets: Arc<Mutex<Budgets>>,
    ips: Mutex<HashMap<IpAddr, IpState>>,
    /// visitor id -> that session's brain (introspection: tests, `/healthz` counts).
    sessions: Mutex<HashMap<String, Brain>>,
    live: Mutex<usize>,
}

impl PublicServer {
    pub fn new(cfg: PublicConfig) -> Arc<Self> {
        let catalog = Arc::new(crate::performer::load_catalog(&cfg.show_dir));
        let budgets = Arc::new(Mutex::new(Budgets::new(cfg.limits.clone())));
        Arc::new(Self { cfg, catalog, budgets, ips: Mutex::default(), sessions: Mutex::default(), live: Mutex::new(0) })
    }

    pub fn router(self: &Arc<Self>) -> Router {
        Router::new()
            .route("/", get(upgrade))
            .route("/token", post(mint_token))
            .route("/healthz", get(healthz))
            .with_state(self.clone())
    }

    pub async fn serve(self: Arc<Self>, listener: tokio::net::TcpListener) -> std::io::Result<()> {
        tracing::info!(addr = %listener.local_addr()?, voice = self.cfg.voice, limits = ?self.cfg.limits, "r3x public server listening");
        axum::serve(listener, self.router().into_make_service_with_connect_info::<SocketAddr>()).await
    }

    /// A new visitor token (what `POST /token` returns).
    pub fn mint(&self) -> (String, Visitor) {
        token::mint(&self.cfg.secret, token::now_unix() + self.cfg.token_ttl.as_secs())
    }

    pub fn spend(&self, visitor: &str) -> Spend {
        self.budgets.lock().unwrap().visitor(visitor)
    }

    /// The conversation a live session's brain holds (empty if there is no such session).
    pub fn history(&self, visitor: &str) -> Vec<r3x_llm::Message> {
        self.sessions.lock().unwrap().get(visitor).map(Brain::history).unwrap_or_default()
    }

    pub fn live_sessions(&self) -> usize {
        *self.live.lock().unwrap()
    }

    fn client_ip(&self, peer: SocketAddr, headers: &HeaderMap) -> IpAddr {
        let fwd = self.cfg.trust_proxy.then(|| headers.get("x-forwarded-for")).flatten();
        fwd.and_then(|v| v.to_str().ok()).and_then(|v| v.split(',').next()).and_then(|v| v.trim().parse().ok()).unwrap_or(peer.ip())
    }

    fn connect_allowed(&self, ip: IpAddr) -> bool {
        let per_min = self.cfg.limits.connects_per_min;
        self.ips.lock().unwrap().entry(ip).or_default().connects.get_or_insert_with(|| Bucket::per_min(per_min)).take()
    }

    /// Reserve a session slot for `ip`; `Err` = why not.
    fn admit(self: &Arc<Self>, ip: IpAddr) -> Result<SessionSlot, &'static str> {
        let mut live = self.live.lock().unwrap();
        if *live >= self.cfg.limits.max_sessions {
            return Err("R3X is talking to as many visitors as he can; try again in a minute\n");
        }
        let mut ips = self.ips.lock().unwrap();
        let st = ips.entry(ip).or_default();
        if st.sessions >= self.cfg.limits.sessions_per_ip {
            return Err("too many sessions from this address\n");
        }
        st.sessions += 1;
        *live += 1;
        Ok(SessionSlot { server: self.clone(), ip })
    }
}

/// Frees a session's slot when the session ends, however it ends.
struct SessionSlot {
    server: Arc<PublicServer>,
    ip: IpAddr,
}

impl Drop for SessionSlot {
    fn drop(&mut self) {
        *self.server.live.lock().unwrap() -= 1;
        let mut ips = self.server.ips.lock().unwrap();
        if let Some(st) = ips.get_mut(&self.ip) {
            st.sessions = st.sessions.saturating_sub(1);
        }
    }
}

fn bearer(headers: &HeaderMap) -> Option<&str> {
    headers
        .get(header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.strip_prefix("Bearer ").or_else(|| v.strip_prefix("bearer ")))
        .map(str::trim)
}

async fn mint_token(State(sv): State<Arc<PublicServer>>, ConnectInfo(peer): ConnectInfo<SocketAddr>, headers: HeaderMap) -> Response {
    let Some(admin) = &sv.cfg.admin_token else { return StatusCode::NOT_FOUND.into_response() };
    if !sv.connect_allowed(sv.client_ip(peer, &headers)) {
        return (StatusCode::TOO_MANY_REQUESTS, "slow down\n").into_response();
    }
    let ok = bearer(&headers).is_some_and(|b| b.len() == admin.len() && b.bytes().zip(admin.bytes()).fold(0u8, |a, (x, y)| a | (x ^ y)) == 0);
    if !ok {
        return (StatusCode::UNAUTHORIZED, "bad admin token\n").into_response();
    }
    let (token, v) = sv.mint();
    Json(serde_json::json!({ "token": token, "expires_at": v.exp, "ttl_s": sv.cfg.token_ttl.as_secs() })).into_response()
}

async fn healthz(State(sv): State<Arc<PublicServer>>) -> Response {
    Json(serde_json::json!({ "ok": true, "sessions": sv.live_sessions(), "max_sessions": sv.cfg.limits.max_sessions })).into_response()
}

async fn upgrade(
    State(sv): State<Arc<PublicServer>>,
    ConnectInfo(peer): ConnectInfo<SocketAddr>,
    headers: HeaderMap,
    Query(q): Query<HashMap<String, String>>,
    ws: WebSocketUpgrade,
) -> Response {
    if let Some(o) = headers.get(header::ORIGIN).and_then(|v| v.to_str().ok()) {
        if !sv.cfg.origins.iter().any(|a| a == o.trim_end_matches('/')) {
            return (StatusCode::FORBIDDEN, "origin not allowed\n").into_response();
        }
    }
    let ip = sv.client_ip(peer, &headers);
    if !sv.connect_allowed(ip) {
        return (StatusCode::TOO_MANY_REQUESTS, "slow down\n").into_response();
    }
    let presented = bearer(&headers).filter(|t| !t.is_empty()).or(q.get("token").map(String::as_str)).unwrap_or("");
    let visitor = match token::verify(&sv.cfg.secret, presented, token::now_unix()) {
        Ok(v) => v,
        Err(e) => return (StatusCode::UNAUTHORIZED, format!("visitor token refused: {e:?}\n")).into_response(),
    };
    let slot = match sv.admit(ip) {
        Ok(s) => s,
        Err(why) => return (StatusCode::SERVICE_UNAVAILABLE, why).into_response(),
    };
    ws.on_upgrade(move |socket| async move {
        let short = visitor.id[..8].to_owned();
        let spawned = std::thread::Builder::new().name(format!("r3x-visitor-{short}")).spawn(move || {
            let rt = match tokio::runtime::Builder::new_current_thread().enable_all().build() {
                Ok(rt) => rt,
                Err(e) => return tracing::error!("visitor runtime: {e}"),
            };
            rt.block_on(session(sv, socket, visitor));
            rt.shutdown_timeout(Duration::from_secs(1));
            drop(slot);
        });
        if let Err(e) = spawned {
            tracing::error!("visitor thread: {e}");
        }
    })
}

/// Claude for a visitor's brain: the replay fixtures under `R3X_FIXTURES=replay`, else the
/// configured provider (`None` = no key: the brain cannot reply).
fn llm_from_env() -> anyhow::Result<Option<r3x_llm::LlmClient>> {
    let env = |k: &str| std::env::var(k).ok().filter(|v| !v.trim().is_empty());
    match env("R3X_FIXTURES").filter(|m| m == "replay").and(env("R3X_FIXTURE_DIR")) {
        Some(dir) => {
            let pace = env("R3X_FIXTURE_PACE").and_then(|p| p.parse().ok()).unwrap_or(1.0);
            let model = env("CLAUDE_MODEL").unwrap_or_else(|| r3x_llm::DEFAULT_MODEL.into());
            Ok(Some(r3x_llm::LlmClient::replay(Arc::new(r3x_llm::ClaudeFixtures::load(dir, pace)?), &model)))
        }
        None => Ok(r3x_llm::LlmClient::from_env()?),
    }
}

/// One visitor, start to finish, on the session's own runtime.
async fn session(sv: Arc<PublicServer>, socket: WebSocket, visitor: Visitor) {
    let short = visitor.id[..8].to_owned();
    tracing::info!(visitor = %short, "public session started");
    let cfg = &sv.cfg;
    let bus = Bus::default();
    if r3x_stage::spawn(&bus, r3x_stage::StageConfig::from_profile(&cfg.profile), None).is_none() {
        return;
    }
    let _ = bus.command(Source::System, None, Command::Stage(StageCommand::SetEngagement { engagement: Engagement::Interactive })).await;
    let pc = crate::performer::PerformerHostConfig {
        profile: cfg.profile.clone(),
        show_dir: cfg.show_dir.clone(),
        drivers: None,
        catalog: Some(sv.catalog.clone()),
    };
    if let Err(e) = crate::performer::spawn(&bus, pc) {
        tracing::error!("visitor performer: {e}");
    }
    let voice = if cfg.voice {
        let stack = r3x_voice::VoiceSettings::from_env().and_then(|mut s| {
            s.local_audio = false;
            s.null_audio = None;
            s.mouth_hz = cfg.profile.audio.mouth_hz;
            r3x_voice::start(&bus, s)
        });
        match stack {
            Ok(v) => {
                r3x_voice::voice::spawn_bus_adapter(&v.voice, bus.clone(), true);
                Some(v)
            }
            Err(e) => {
                tracing::warn!("visitor voice unavailable (text only): {e}");
                None
            }
        }
    } else {
        None
    };
    speech_glue(&bus, voice.as_ref().map(|v| v.voice.clone()), sv.budgets.clone(), visitor.clone());

    let llm = match llm_from_env() {
        Ok(l) => l,
        Err(e) => {
            tracing::error!("visitor LLM: {e}");
            None
        }
    };
    let meter = {
        let (budgets, v) = (sv.budgets.clone(), visitor.clone());
        Arc::new(move |u: &r3x_llm::Usage| budgets.lock().unwrap().charge_llm(&v.id, v.exp, budget::weighted_tokens(u), token::now_unix()))
    };
    let router_cfg = r3x_intent::RouterConfig { enabled: false, ..r3x_intent::RouterConfig::default() };
    let deps = BrainDeps {
        llm: llm.map(|l| l.with_meter(meter)),
        router: r3x_intent::IntentRouter::new(r3x_intent::JevClient::new("", router_cfg.timeout), router_cfg),
        memory: None,
        latency: None,
        ptt: voice.as_ref().map(|v| ptt_hook(v.voice.clone())),
        chooser: r3x_brain::random_chooser(),
    };
    let Some(brain) = Brain::spawn(&bus, BrainConfig { public: true, ..BrainConfig::from_env() }, deps) else { return };
    sv.sessions.lock().unwrap().insert(visitor.id.clone(), brain.clone());

    let activity = Arc::new(Mutex::new(tokio::time::Instant::now()));
    let gw = GatewayConfig {
        profile: Some(cfg.profile.clone()),
        audio: voice.as_ref().map(|v| r3x_voice::remote::hooks(&v.voice, &v.remote_sink)).unwrap_or_default(),
        filter: Some(filter(&bus, &sv, &visitor, activity.clone())),
        ..Default::default()
    };
    let info = ClientInfo {
        name: format!("visitor-{short}"),
        source: Source::Public,
        classes: vec![MessageClass::Intent, MessageClass::Perf, MessageClass::Telemetry],
    };
    let idle = cfg.limits.idle;
    let expiry = Duration::from_secs(visitor.exp.saturating_sub(token::now_unix()));
    let why = tokio::select! {
        _ = r3x_gateway::serve_socket(socket, bus.clone(), Arc::new(gw), info) => "disconnected",
        _ = async {
            loop {
                let deadline = *activity.lock().unwrap() + idle;
                if tokio::time::Instant::now() >= deadline { break }
                tokio::time::sleep_until(deadline).await;
            }
        } => "idle",
        _ = tokio::time::sleep(expiry) => "token expired",
    };
    brain.shutdown();
    sv.sessions.lock().unwrap().remove(&visitor.id);
    let spent = sv.spend(&visitor.id);
    tracing::info!(visitor = %short, why, llm_tokens = spent.llm_tokens, tts_chars = spent.tts_chars, "public session ended");
}

/// Rate limits and budgets, answered before a command reaches the session's bus.
fn filter(bus: &Bus, sv: &Arc<PublicServer>, visitor: &Visitor, activity: Arc<Mutex<tokio::time::Instant>>) -> r3x_gateway::CommandFilter {
    let (bus, budgets, v, limits) = (bus.clone(), sv.budgets.clone(), visitor.clone(), sv.cfg.limits.clone());
    let commands = Mutex::new(Bucket::per_min(limits.commands_per_min));
    let turns = Mutex::new(Bucket::per_min(limits.turns_per_min));
    let holds = Arc::new(std::sync::atomic::AtomicU64::new(0));
    Arc::new(move |_info, cmd| {
        *activity.lock().unwrap() = tokio::time::Instant::now();
        if !commands.lock().unwrap().take() {
            return Some(Ack::rejected("rate limited: too many commands"));
        }
        let turn = matches!(cmd, Command::Intent(IntentCommand::Say { .. } | IntentCommand::PttStart));
        if !turn {
            return None;
        }
        if let Some(line) = budgets.lock().unwrap().refusal(&v.id, token::now_unix()) {
            bus.publish(Source::System, None, Event::Conversation(ConversationEvent::Reply { text: line.into() }));
            return Some(Ack::rejected(line));
        }
        if !turns.lock().unwrap().take() {
            return Some(Ack::rejected("rate limited: give R3X a moment between questions"));
        }
        if matches!(cmd, Command::Intent(IntentCommand::PttStart)) {
            // Deepgram bills streamed audio: a held button is let go after `max_ptt`.
            use std::sync::atomic::Ordering::SeqCst;
            let (bus, max, holds) = (bus.clone(), limits.max_ptt, holds.clone());
            let hold = holds.fetch_add(1, SeqCst) + 1;
            tokio::spawn(async move {
                tokio::time::sleep(max).await;
                if holds.load(SeqCst) == hold {
                    let _ = bus.command(Source::Public, None, Command::Intent(IntentCommand::PttStop)).await;
                }
            });
        }
        None
    })
}

fn ptt_hook(voice: r3x_voice::Voice) -> r3x_brain::PttHook {
    Arc::new(move |p| {
        let v = voice.clone();
        Box::pin(async move {
            match p {
                Ptt::Start { owner } => v.ptt_start(&owner).await,
                Ptt::Stop { owner } => v.ptt_stop(Some(&owner)).await,
            }
        })
    })
}

/// The brain's spoken lines -> this visitor's voice, within the TTS caps. A line over the cap
/// (or with no voice) is not synthesised: its text is already on the page as the reply.
fn speech_glue(bus: &Bus, voice: Option<r3x_voice::Voice>, budgets: Arc<Mutex<Budgets>>, visitor: Visitor) {
    let mut conv = bus.subscribe(Domain::Conversation);
    let bus = bus.clone();
    tokio::spawn(async move {
        while let Some(m) = conv.recv().await {
            let Received::Message(env) = m else { continue };
            let Body::Event(Event::Conversation(ConversationEvent::Speak { text, clip_id, plan_id, reply })) = &env.body else { continue };
            let chars = text.chars().count() as u64;
            let said = voice.as_ref().is_some_and(|v| {
                budgets.lock().unwrap().try_tts(&visitor.id, visitor.exp, chars, token::now_unix())
                    && v.say(SpeechRequest {
                        text: text.clone(),
                        conversation_id: env.conversation_id.clone(),
                        clip_id: clip_id.clone(),
                        step_id: None,
                        plan_id: plan_id.clone(),
                        source: env.source,
                        reply: *reply,
                    })
            });
            if !said {
                bus.publish(Source::System, env.conversation_id.clone(), Event::Conversation(ConversationEvent::SpeechEnded));
            }
        }
    });
}
