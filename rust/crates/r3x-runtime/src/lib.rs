//! The `r3x` runtime: bus + gateway + ops + stage manager, optionally bridged to CantinaOS.

pub mod brain;
pub mod bridge;
pub mod music;
pub mod performer;
pub mod studio;
pub mod vision;

use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::Arc;

use r3x_bus::Bus;
use r3x_contracts::{ClientInfo, LogLine, MessageClass, RobotProfile, ServiceStatus, Source};
use r3x_gateway::{tokens, ClientAuth, GatewayConfig};
use tokio::sync::broadcast;

/// The panel's dev-server origins (same list as CantinaOS `tap/auth.py`).
pub const DEFAULT_ORIGINS: &[&str] = &[
    "http://localhost:5391",
    "http://127.0.0.1:5391",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:4173",
    "http://127.0.0.1:4173",
];
pub const DEFAULT_BIND: &str = "127.0.0.1:8780";
pub const DEFAULT_TAP_URL: &str = "ws://127.0.0.1:8766/";

#[derive(Clone)]
pub struct RuntimeConfig {
    pub bind: SocketAddr,
    pub clients: Vec<ClientAuth>,
    pub origins: Vec<String>,
    pub profile: Option<Arc<RobotProfile>>,
    /// `Some` = bridge mode.
    pub bridge: Option<bridge::BridgeConfig>,
    pub session_log: Option<PathBuf>,
    pub logs: Option<broadcast::Sender<LogLine>>,
    /// `Some` = r3x owns voice I/O (Phase 2). In bridge mode CantinaOS must run with
    /// `R3X_EXTERNAL_VOICE=1`.
    pub voice: Option<r3x_voice::VoiceSettings>,
    /// Global left-click push-to-talk (needs the `mouse` feature).
    pub mouse: bool,
    /// The show folder the performer plays (hot-reloaded).
    pub show_dir: PathBuf,
    /// Hardware drivers for the performer (`None` = frames only).
    pub drivers: Option<performer::DriverOptions>,
}

pub fn all_classes() -> Vec<MessageClass> {
    vec![MessageClass::Intent, MessageClass::Perf, MessageClass::Stage, MessageClass::Telemetry]
}

/// Gateway clients from env/token files:
/// - `ui` (panel): `R3X_GATEWAY_TOKEN`, else the shared local token CantinaOS uses
///   (`R3X_TAP_TOKEN` / `~/.config/dj-r3x/tap_token`), so `./r3x`'s `#token=` link opens both.
/// - `cli`: `R3X_CLI_TOKEN` / `~/.config/dj-r3x/cli_token` (read by `r3x-cli`).
/// - `public` (only if `R3X_PUBLIC_TOKEN` is set): `intent` class, `public` tier.
pub fn clients_from_env() -> std::io::Result<Vec<ClientAuth>> {
    let ui = match std::env::var("R3X_GATEWAY_TOKEN").ok().filter(|t| !t.trim().is_empty()) {
        Some(t) => t.trim().to_owned(),
        None => {
            let file = std::env::var_os("R3X_TOKEN_FILE")
                .map(PathBuf::from)
                .unwrap_or_else(|| tokens::config_dir().join("tap_token"));
            tokens::load_or_create("R3X_TAP_TOKEN", file)?
        }
    };
    let cli = tokens::load_or_create("R3X_CLI_TOKEN", tokens::config_dir().join("cli_token"))?;
    let mut clients = vec![
        ClientAuth { token: ui, info: ClientInfo { name: "panel".into(), source: Source::Ui, classes: all_classes() } },
        ClientAuth { token: cli, info: ClientInfo { name: "cli".into(), source: Source::Cli, classes: all_classes() } },
    ];
    if let Some(t) = std::env::var("R3X_PUBLIC_TOKEN").ok().filter(|t| !t.trim().is_empty()) {
        clients.push(ClientAuth {
            token: t.trim().to_owned(),
            info: ClientInfo { name: "public".into(), source: Source::Public, classes: vec![MessageClass::Intent] },
        });
    }
    Ok(clients)
}

pub fn origins_from_env() -> Vec<String> {
    let extra = std::env::var("R3X_ALLOWED_ORIGINS").unwrap_or_default();
    DEFAULT_ORIGINS
        .iter()
        .map(|s| s.to_string())
        .chain(extra.split(',').map(|o| o.trim().trim_end_matches('/').to_owned()).filter(|o| !o.is_empty()))
        .collect()
}

/// Wire everything onto `bus` and serve the gateway until it fails.
pub async fn run(bus: Bus, cfg: RuntimeConfig, level: Option<r3x_ops::LevelControl>) -> anyhow::Result<()> {
    let listener = tokio::net::TcpListener::bind(cfg.bind).await?;
    run_on(bus, cfg, level, listener).await
}

pub async fn run_on(
    bus: Bus,
    cfg: RuntimeConfig,
    level: Option<r3x_ops::LevelControl>,
    listener: tokio::net::TcpListener,
) -> anyhow::Result<()> {
    run_with_brain(bus, cfg, level, listener, brain::BrainMode::Cantina).await
}

/// [`run_on`] with a choice of brain (`--brain rust|cantina`).
pub async fn run_with_brain(
    bus: Bus,
    cfg: RuntimeConfig,
    level: Option<r3x_ops::LevelControl>,
    listener: tokio::net::TcpListener,
    brain_mode: brain::BrainMode,
) -> anyhow::Result<()> {
    if let Some(dir) = &cfg.session_log {
        let (path, _task) = bus.attach_session_log(dir).await?;
        tracing::info!(path = %path.display(), "session log");
    }
    r3x_ops::spawn_health(&bus);
    tokio::task::yield_now().await; // health subscribed before the first report
    if let Some(level) = level {
        r3x_ops::spawn_telemetry(&bus, level);
    }
    let voice = match cfg.voice.clone() {
        Some(mut v) => {
            if let Some(p) = &cfg.profile {
                v.mouth_hz = p.audio.mouth_hz;
                if v.output_device.is_none() {
                    v.output_device = p.audio.outputs.first().and_then(|o| o.device.clone());
                }
            }
            let stack = r3x_voice::start(&bus, v)?;
            // Bridge mode: the turn/speech lifecycle comes back from CantinaOS via the tap.
            r3x_voice::voice::spawn_bus_adapter(&stack.voice, bus.clone(), cfg.bridge.is_none());
            r3x_ops::report(&bus, "voice", ServiceStatus::Running, None);
            Some(stack)
        }
        None => None,
    };
    if cfg.mouse {
        #[cfg(feature = "mouse")]
        if let Some(v) = &voice {
            r3x_voice::mouse::spawn(bus.clone(), v.voice.clone())?;
        }
        #[cfg(not(feature = "mouse"))]
        anyhow::bail!("--mouse needs r3x-runtime built with --features mouse");
    }
    if let Some(profile) = cfg.profile.clone() {
        let pc = performer::PerformerHostConfig { profile, show_dir: cfg.show_dir.clone(), drivers: cfg.drivers };
        performer::spawn(&bus, pc)?;
    }
    // Before the bridge: the brain takes the `intent` class.
    let (_brain, memory) = match brain_mode {
        brain::BrainMode::Rust => {
            anyhow::ensure!(cfg.bridge.is_none(), "--brain rust replaces the CantinaOS bridge; drop --bridge");
            let (b, m) = brain::spawn(&bus, voice.as_ref().map(|v| v.voice.clone()))?;
            (Some(b), Some(m))
        }
        brain::BrainMode::Cantina => (None, None),
    };
    // `--vision` (R3X_VISION): fail-open; presence goes to the brain's memory.
    let _vision = if vision::enabled_from_env() { vision::start(&bus, memory) } else { None };
    // `--music rust` (R3X_MUSIC): the r3x engine plays; in bridge mode it speaks
    // MusicController's topics over the tap (CantinaOS runs with R3X_EXTERNAL_MUSIC=1).
    let music = match music::MusicMode::from_env() {
        music::MusicMode::Rust => {
            let mixer = voice.as_ref().and_then(|v| v.output.as_ref()).map(|o| o.mixer.clone());
            Some(music::start(&bus, mixer, None, cfg.profile.as_deref()).await?)
        }
        music::MusicMode::Cantina => None,
    };
    let backend = match cfg.bridge.clone() {
        Some(mut b) => {
            b.music = music.as_ref().map(|m| m.engine.clone());
            Some(bridge::spawn(&bus, b, voice.as_ref().map(|v| v.voice.clone()))?)
        }
        None => None,
    };
    let stage_cfg = cfg.profile.as_deref().map(r3x_stage::StageConfig::from_profile).unwrap_or_default();
    r3x_stage::spawn(&bus, stage_cfg, backend).ok_or_else(|| anyhow::anyhow!("stage class taken"))?;

    let gw = GatewayConfig {
        clients: cfg.clients,
        origins: cfg.origins,
        profile: cfg.profile,
        logs: cfg.logs,
        audio: voice.as_ref().map(|v| r3x_voice::remote::hooks(&v.voice, &v.remote_sink)).unwrap_or_default(),
    };
    tracing::info!(addr = %listener.local_addr()?, bridge = cfg.bridge.is_some(), "r3x gateway listening");
    r3x_ops::report(&bus, "gateway", ServiceStatus::Running, None);
    r3x_gateway::serve(listener, bus, gw).await?;
    drop((music, voice)); // the stacks (and their audio devices) live as long as the gateway
    Ok(())
}

/// `R3X_PROFILE`, else `profiles/r3x/robot.json` in the repo this binary was built from.
pub fn default_profile_path() -> PathBuf {
    std::env::var_os("R3X_PROFILE")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(concat!(env!("CARGO_MANIFEST_DIR"), "/../../../profiles/r3x/robot.json")))
}
