//! `r3x-brain`: the CantinaOS brain in Rust (plan §6: claude, intent_router, brain_service,
//! timeline plans, cached-speech commentary).
//!
//! - [`turn`]: turn id adopted from capture; the Jev router and Claude run in parallel behind
//!   the dedup gate; streaming show-tag stripping; one spoken reply routed through a
//!   one-step foreground plan; verbal-feedback second call; warm-up on engage.
//! - [`tools`]: tool dispatch to typed bus requests (music, eyes, shows, DJ).
//! - [`plan`]: the layered plan executor.
//! - [`dj`]: DJ planner, commentary caching (event-driven + 15 s lookahead loop), transitions.
//! - [`tags`], [`catalog`]: show tags and the catalogue Claude sees.
//!
//! On the bus the brain owns the `intent` command class and talks to the voice, the music
//! engine and the speech cache only through `r3x-contracts` events (`conversation.speak`,
//! `music.play|stop|next|crossfade|duck|unduck`, `conversation.cache_speech|play_cached`),
//! and to the performer through `perf` commands.

pub mod catalog;
pub mod dj;
pub mod plan;
pub mod tags;
pub mod tools;
pub mod turn;

use std::collections::HashMap;
use std::future::Future;
use std::path::PathBuf;
use std::pin::Pin;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use r3x_bus::{Bus, CommandRequest, Received};
use r3x_contracts::{
    Ack, Body, Command, ConversationEvent, Domain, Engagement, EngagementState, Event, IntentCommand, MessageClass, MusicCommand,
    MusicEvent, OpsEvent, PerfCommand, ServiceStatus, Source, StageState,
};
use r3x_intent::{FastRouterGate, IntentRouter};
use r3x_llm::{LlmClient, SessionMemory, Tool};
use r3x_memory::Memory;
use r3x_ops::latency::LatencyTracker;
use tokio::task::JoinHandle;

pub use catalog::ShowCatalog;
pub use plan::{Executor, Plan, Step};

/// Push-to-talk, forwarded to the voice (the brain owns the `intent` class).
pub enum Ptt {
    Start { owner: String },
    Stop,
}
pub type PttHook = Arc<dyn Fn(Ptt) -> Pin<Box<dyn Future<Output = Ack> + Send>> + Send + Sync>;
/// Picks one of `options` for a choice point (`brain.next_track`); `None` = no pick. Random by
/// default; the parity harness replays the recorded picks.
pub type Chooser = Arc<dyn Fn(&str, &[String]) -> Option<String> + Send + Sync>;

#[derive(Debug, Clone)]
pub struct BrainConfig {
    /// `show/` root (`SHOW_DIR`).
    pub show_dir: PathBuf,
    /// Where `dj_r3x-persona.txt` and `dj_r3x-verbal-feedback-persona.txt` live.
    pub persona_dir: PathBuf,
    /// `SHOW_TAG_CHARS_PER_SEC`: tag fallback pace before character timing arrives.
    pub tag_chars_per_sec: f64,
    /// How long a play/next/stop dispatch waits for the engine to confirm the real track.
    pub playback_confirm_wait: Duration,
    pub warmup_cooldown: Duration,
    pub dj: dj::DjConfig,
}

impl Default for BrainConfig {
    fn default() -> Self {
        let repo = PathBuf::from(concat!(env!("CARGO_MANIFEST_DIR"), "/../../.."));
        Self {
            show_dir: repo.join("show"),
            persona_dir: repo.join("cantina_os"),
            tag_chars_per_sec: tags::DEFAULT_CHARS_PER_SEC,
            playback_confirm_wait: Duration::from_millis(800),
            warmup_cooldown: Duration::from_secs(30),
            dj: dj::DjConfig::default(),
        }
    }
}

impl BrainConfig {
    /// `SHOW_DIR`, `R3X_PERSONA_DIR`, `SHOW_TAG_CHARS_PER_SEC`, `PLAYBACK_CONFIRM_WAIT_S`.
    pub fn from_env() -> Self {
        let mut c = Self::default();
        let env = |k: &str| std::env::var(k).ok().filter(|v| !v.trim().is_empty());
        if let Some(d) = env("SHOW_DIR") {
            c.show_dir = d.into();
        }
        if let Some(d) = env("R3X_PERSONA_DIR") {
            c.persona_dir = d.into();
        }
        if let Some(v) = env("SHOW_TAG_CHARS_PER_SEC").and_then(|v| v.parse().ok()) {
            c.tag_chars_per_sec = v;
        }
        if let Some(v) = env("PLAYBACK_CONFIRM_WAIT_S").and_then(|v| v.parse::<f64>().ok()) {
            c.playback_confirm_wait = Duration::from_secs_f64(v.max(0.0));
        }
        c
    }
}

pub struct BrainDeps {
    /// `None`: Claude unavailable; the router still acts, nothing is said.
    pub llm: Option<LlmClient>,
    pub router: Arc<IntentRouter>,
    pub memory: Option<Arc<Memory>>,
    pub latency: Option<Arc<LatencyTracker>>,
    pub ptt: Option<PttHook>,
    pub chooser: Chooser,
}

pub fn random_chooser() -> Chooser {
    Arc::new(|_, options: &[String]| {
        if options.is_empty() {
            return None;
        }
        let nanos = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.subsec_nanos()).unwrap_or(0);
        Some(options[nanos as usize % options.len()].clone())
    })
}

pub(crate) struct Inner {
    pub bus: Bus,
    pub cfg: BrainConfig,
    pub llm: Option<LlmClient>,
    pub router: Arc<IntentRouter>,
    pub gate: Arc<FastRouterGate>,
    pub memory: Option<Arc<Memory>>,
    pub latency: Option<Arc<LatencyTracker>>,
    pub ptt: Option<PttHook>,
    pub chooser: Chooser,
    pub catalog: ShowCatalog,
    /// Main persona + show catalogue: the cached system prompt, fixed at start-up.
    pub system: String,
    pub feedback_persona: Option<String>,
    /// DJ commentary persona (the main persona, unstripped, as BrainService reads it).
    pub dj_persona: String,
    pub tools: Vec<Tool>,
    pub session: Mutex<SessionMemory>,
    pub tags: Arc<tags::TagScheduler>,
    pub exec: Executor,
    /// Reply texts already routed to speech, per conversation (duplicates dropped).
    pub spoken: Mutex<HashMap<String, Vec<String>>>,
    pub dj: Mutex<dj::DjState>,
    pub last_warmup: Mutex<Option<tokio::time::Instant>>,
    /// Title of the track the engine last reported started (cleared on stop).
    pub now_playing: Mutex<Option<String>>,
}

/// A running brain. Dropping it stops nothing; call [`Brain::shutdown`].
#[derive(Clone)]
pub struct Brain {
    pub(crate) inner: Arc<Inner>,
    tasks: Arc<Mutex<Vec<JoinHandle<()>>>>,
}

fn read(dir: &std::path::Path, name: &str) -> Option<String> {
    std::fs::read_to_string(dir.join(name)).ok().filter(|s| !s.trim().is_empty())
}

impl Brain {
    /// Take the `intent` command class and start. `None` if another handler owns it.
    pub fn spawn(bus: &Bus, cfg: BrainConfig, deps: BrainDeps) -> Option<Brain> {
        let commands = bus.take_commands(MessageClass::Intent)?;
        let catalog = ShowCatalog::load(&cfg.show_dir);
        let persona = read(&cfg.persona_dir, "dj_r3x-persona.txt");
        let mut system = persona.as_deref().map(str::trim).unwrap_or("You are DJ R3X, a helpful and enthusiastic Star Wars droid DJ assistant.").to_string();
        if !catalog.prompt_block.is_empty() && !system.contains("<performance>") {
            system = format!("{}\n\n{}", system.trim_end(), catalog.prompt_block);
        }
        let mut tools = r3x_llm::request::default_tools();
        if !catalog.tool_ids.is_empty() {
            tools.push(r3x_llm::request::perform_show_tool(&catalog.tool_ids));
        }
        let gate = Arc::new(FastRouterGate::new());
        if deps.router.active() {
            gate.register_router();
        }
        let exec = Executor::new(bus.clone(), catalog.kinds.clone());
        let perform_bus = bus.clone();
        let clock = bus.clock();
        let tags = tags::TagScheduler::new(
            // Perf commands carry no turn id; the tag's turn is only its timing key.
            Arc::new(move |id, _turn| {
                let bus = perform_bus.clone();
                tokio::spawn(async move {
                    let cmd = Command::Perf(PerfCommand::Play { id: id.clone(), intensity: 1.0, speed: 1.0, layer: None });
                    let ack = bus.command(Source::Claude, None, cmd).await;
                    if !ack.is_accepted() {
                        tracing::info!(id, ?ack, "show tag not performed");
                    }
                });
            }),
            Arc::new(move || clock.t_mono()),
            cfg.tag_chars_per_sec,
        );
        let inner = Arc::new(Inner {
            bus: bus.clone(),
            llm: deps.llm,
            router: deps.router,
            gate,
            memory: deps.memory,
            latency: deps.latency,
            ptt: deps.ptt,
            chooser: deps.chooser,
            feedback_persona: read(&cfg.persona_dir, "dj_r3x-verbal-feedback-persona.txt").map(|s| s.trim().to_string()),
            dj_persona: persona.unwrap_or_default(),
            system,
            tools,
            catalog,
            session: Mutex::new(SessionMemory::default()),
            tags,
            exec,
            spoken: Mutex::default(),
            dj: Mutex::default(),
            last_warmup: Mutex::default(),
            now_playing: Mutex::default(),
            cfg,
        });
        tracing::info!(
            llm = inner.llm.as_ref().map(|l| l.model().to_string()),
            router = inner.router.active(),
            taggable = inner.catalog.taggable.len(),
            routines = inner.catalog.tool_ids.len(),
            "brain started"
        );
        let brain = Brain { inner, tasks: Arc::default() };
        let t = vec![
            tokio::spawn(brain.clone().command_loop(commands)),
            // Subscribed here, not in the task: nothing published right after spawn is missed.
            tokio::spawn(brain.clone().event_loop([bus.subscribe(Domain::Conversation), bus.subscribe(Domain::Music), bus.subscribe(Domain::Ops)])),
            tokio::spawn(brain.clone().warmup_loop()),
            tokio::spawn(brain.clone().dj_loop()),
        ];
        brain.tasks.lock().unwrap().extend(t);
        if brain.inner.router.active() {
            let r = brain.inner.router.clone();
            tokio::spawn(async move {
                r.prewarm().await;
            });
        }
        r3x_ops::report(bus, "brain", ServiceStatus::Running, None);
        Some(brain)
    }

    pub fn shutdown(&self) {
        for t in self.tasks.lock().unwrap().drain(..) {
            t.abort();
        }
        self.inner.exec.cancel_all();
        self.inner.tags.cancel_all();
    }

    pub fn executor(&self) -> &Executor {
        &self.inner.exec
    }

    pub fn system_prompt(&self) -> &str {
        &self.inner.system
    }

    async fn command_loop(self, mut rx: r3x_bus::CommandReceiver) {
        while let Some(req) = rx.recv().await {
            let me = self.clone();
            tokio::spawn(async move { me.handle_command(req).await });
        }
    }

    async fn handle_command(self, req: CommandRequest) {
        let source = req.source();
        let Command::Intent(cmd) = req.command.clone() else { return req.ack(Ack::rejected("not an intent command")) };
        let brain_on = self.inner.bus.get::<StageState>().brain;
        match cmd {
            IntentCommand::Say { .. } | IntentCommand::PttStart if !brain_on => req.ack(Ack::rejected("the brain is off in this mode")),
            IntentCommand::Say { text } => {
                if text.trim().is_empty() {
                    return req.ack(Ack::rejected("nothing to say"));
                }
                req.ack(Ack::Accepted);
                // A typed turn is captured here: mint the id, then the same events as the mic.
                let turn = format!("typed-{}", uuid::Uuid::new_v4());
                let bus = &self.inner.bus;
                bus.publish(source, Some(turn.clone()), Event::Conversation(ConversationEvent::ListeningStarted));
                bus.publish(source, Some(turn), Event::Conversation(ConversationEvent::ListeningStopped { transcript: text }));
            }
            IntentCommand::PttStart | IntentCommand::PttStop => match &self.inner.ptt {
                None => req.ack(Ack::rejected("no voice input in this runtime")),
                Some(hook) => {
                    let action = match req.command {
                        Command::Intent(IntentCommand::PttStart) => Ptt::Start { owner: format!("{source:?}").to_ascii_lowercase() },
                        _ => Ptt::Stop,
                    };
                    let ack = hook(action).await;
                    req.ack(ack);
                }
            },
            IntentCommand::Music(m) => {
                req.ack(Ack::Accepted);
                let (tool, params) = match m {
                    MusicCommand::Play { query } => ("play_music", serde_json::json!({ "track": query })),
                    MusicCommand::Stop => ("stop_music", serde_json::json!({})),
                    MusicCommand::Next => ("next_track", serde_json::json!({})),
                };
                self.dispatch(tool, params.as_object().cloned().unwrap_or_default(), None, source).await;
            }
            IntentCommand::Dj { active } => {
                req.ack(Ack::Accepted);
                let msg = if active { self.dj_start().await } else { self.dj_stop().await };
                self.console(msg.0, msg.1);
            }
            IntentCommand::Console { line } => {
                req.ack(Ack::Accepted);
                let (msg, err) = self.console_line(&line).await;
                self.console(msg, err);
            }
        }
    }

    pub(crate) fn console(&self, message: String, is_error: bool) {
        self.inner.bus.publish(Source::System, None, Event::Ops(OpsEvent::Console { message, is_error }));
    }

    /// The CantinaOS console lines the brain owns.
    async fn console_line(&self, line: &str) -> (String, bool) {
        let words: Vec<&str> = line.split_whitespace().collect();
        let rest = |n: usize| words.get(n..).map(|w| w.join(" ")).filter(|s| !s.is_empty());
        match words.as_slice() {
            ["debug", "latency", ..] => match &self.inner.latency {
                Some(l) => (l.report(), false),
                None => ("latency tracking is off".into(), true),
            },
            ["dj", "start"] => self.dj_start().await,
            ["dj", "stop"] => self.dj_stop().await,
            ["dj", "next"] => self.dj_next().await,
            ["play", "music", ..] => {
                let q = rest(2);
                self.dispatch("play_music", serde_json::json!({ "track": q }).as_object().cloned().unwrap(), None, Source::Cli).await;
                ("Playing music".into(), false)
            }
            ["stop", "music"] => {
                self.dispatch("stop_music", Default::default(), None, Source::Cli).await;
                ("Music stopped".into(), false)
            }
            ["next", "music"] => {
                self.dispatch("next_track", Default::default(), None, Source::Cli).await;
                ("Next track".into(), false)
            }
            ["reset"] | ["conversation", "reset"] => {
                self.inner.session.lock().unwrap().clear();
                ("Conversation reset".into(), false)
            }
            _ => (format!("'{line}' is not handled by the rust brain"), true),
        }
    }

    async fn event_loop(self, [mut conv, mut music, mut ops]: [r3x_bus::EventReceiver; 3]) {
        let bus = self.inner.bus.clone();
        loop {
            let got = tokio::select! {
                m = conv.recv() => m,
                m = music.recv() => m,
                m = ops.recv() => m,
            };
            let Some(Received::Message(env)) = got else {
                if got.is_none() {
                    return;
                }
                continue;
            };
            let Body::Event(e) = &env.body else { continue };
            let cid = env.conversation_id.clone();
            match e {
                Event::Conversation(ConversationEvent::ListeningStarted) => self.inner.router.turn_started(),
                Event::Conversation(ConversationEvent::Transcript { text, is_final }) => self.inner.router.partial(text, *is_final),
                Event::Conversation(ConversationEvent::ListeningStopped { transcript }) => {
                    if !transcript.trim().is_empty() && bus.get::<StageState>().brain {
                        let turn = cid.unwrap_or_else(|| format!("turn-{}", uuid::Uuid::new_v4()));
                        self.start_turn(turn, transcript.trim().to_string());
                    }
                }
                Event::Conversation(ConversationEvent::SpeechStarted) => {
                    if let Some(c) = cid {
                        self.inner.tags.on_speech_started(&c);
                    }
                }
                Event::Conversation(ConversationEvent::SpeechTiming { chars, start_ms, audio_t0, .. }) => {
                    if let Some(c) = cid {
                        self.inner.tags.on_timing(&c, chars.len(), start_ms, *audio_t0);
                    }
                }
                Event::Conversation(ConversationEvent::SpeechCached { key, duration_s }) => self.dj_speech_cached(key, *duration_s),
                Event::Conversation(ConversationEvent::SpeechCacheFailed { key, error }) => self.dj_speech_cache_failed(key, error),
                Event::Music(MusicEvent::TrackEndingSoon { .. }) => {
                    let me = self.clone();
                    tokio::spawn(async move { me.dj_track_ending_soon().await });
                }
                Event::Music(MusicEvent::TrackStarted { track }) => self.dj_track_started(&track.title),
                Event::Music(MusicEvent::TrackStopped) => *self.inner.now_playing.lock().unwrap() = None,
                Event::Ops(OpsEvent::PlanEnded { plan_id, status, .. }) if status == "failed" => {
                    let me = self.clone();
                    let plan_id = plan_id.clone();
                    tokio::spawn(async move { me.dj_plan_failed(&plan_id).await });
                }
                _ => {}
            }
        }
    }

    /// Pre-warm the Claude connection when engagement becomes INTERACTIVE (30 s cooldown).
    async fn warmup_loop(self) {
        let mut eng = self.inner.bus.watch::<EngagementState>();
        loop {
            if eng.changed().await.is_err() {
                return;
            }
            if eng.borrow_and_update().engagement != Engagement::Interactive {
                continue;
            }
            let Some(llm) = self.inner.llm.clone() else { continue };
            {
                let mut last = self.inner.last_warmup.lock().unwrap();
                if last.is_some_and(|t| t.elapsed() < self.inner.cfg.warmup_cooldown) {
                    continue;
                }
                *last = Some(tokio::time::Instant::now());
            }
            tokio::spawn(async move {
                match llm.warm_up().await {
                    Ok(()) => tracing::info!("Claude connection warmed up"),
                    Err(e) => tracing::warn!("Claude warm-up failed (non-critical): {e}"),
                }
            });
        }
    }
}
