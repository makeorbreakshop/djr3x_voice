//! `r3x-runtime [--bind ADDR] [--bridge] [--voice] [--mouse] [--leds r3x|cantina] [--brain cantina|rust] [--music cantina|rust] [--show-dir DIR] [--tap-url URL] [--profile PATH] [--session-log DIR]`
//!
//! Env: `R3X_GATEWAY_ADDR`, `R3X_TAP_URL`, `R3X_PROFILE`, `R3X_ALLOWED_ORIGINS`,
//! `R3X_GATEWAY_TOKEN` / `R3X_TAP_TOKEN`, `R3X_CLI_TOKEN`, `R3X_PUBLIC_TOKEN`, `RUST_LOG`,
//! `R3X_VOICE=1` (= `--voice`: mic/STT/TTS in r3x; run CantinaOS with `R3X_EXTERNAL_VOICE=1`),
//! `R3X_LEDS=cantina` (= `--leds cantina`: CantinaOS keeps the face/chest boards; otherwise run
//! it with `R3X_EXTERNAL_BODY=1`), `SHOW_DIR` (= `--show-dir`), `ARDUINO_SERIAL_PORT`,
//! `CHEST_SERIAL_PORT`, `R3X_SERVO_PORT`, `FORCE_MOCK_LED_CONTROLLER`, `FORCE_MOCK_CHEST`,
//! `R3X_BRAIN=rust` (= `--brain rust`: r3x-brain answers turns instead of CantinaOS; not with
//! `--bridge`), `R3X_MEMORY_DB`, `R3X_PERSONA_DIR`, `R3X_MUSIC=rust` (= `--music rust`: the r3x
//! music engine plays music/sfx; run CantinaOS with `R3X_EXTERNAL_MUSIC=1`), `MUSIC_DIR`,
//! `R3X_SFX_DIR`, `R3X_CLAP_DIR`, `R3X_BEAT_CACHE_DIR`.

use std::sync::Arc;

use anyhow::Context;
use r3x_bus::Bus;
use r3x_contracts::RobotProfile;
use r3x_gateway::tokens;
use r3x_runtime::{bridge::BridgeConfig, RuntimeConfig};

const USAGE: &str = "usage: r3x-runtime [--bind ADDR] [--bridge] [--voice] [--mouse] [--leds r3x|cantina] [--brain cantina|rust] [--music cantina|rust] [--show-dir DIR] [--tap-url URL] [--profile PATH] [--session-log DIR]";

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let (hub, level) = r3x_ops::init_tracing("info");
    let env = |k: &str| std::env::var(k).ok().filter(|v| !v.is_empty());

    let mut bind = env("R3X_GATEWAY_ADDR").unwrap_or_else(|| r3x_runtime::DEFAULT_BIND.into());
    let mut tap_url = env("R3X_TAP_URL").unwrap_or_else(|| r3x_runtime::DEFAULT_TAP_URL.into());
    let mut profile_path = r3x_runtime::default_profile_path();
    let (mut bridge, mut session_log) = (false, None);
    let mut voice = env("R3X_VOICE").is_some_and(|v| !matches!(v.as_str(), "0" | "false" | "no" | "off"));
    let mut mouse = false;
    // Who drives the face/chest boards: r3x (default; run CantinaOS with R3X_EXTERNAL_BODY=1)
    // or CantinaOS (`--leds cantina` / R3X_LEDS=cantina).
    let mut leds = env("R3X_LEDS").is_none_or(|v| v != "cantina");
    let mut show_dir = r3x_runtime::performer::default_show_dir();
    let mut brain: r3x_runtime::brain::BrainMode = env("R3X_BRAIN").map_or(Ok(Default::default()), |v| v.parse()).map_err(anyhow::Error::msg)?;
    let mut args = std::env::args().skip(1);
    while let Some(a) = args.next() {
        let mut val = || args.next().with_context(|| format!("{a} needs a value\n{USAGE}"));
        match a.as_str() {
            "--bind" => bind = val()?,
            "--bridge" => bridge = true,
            "--voice" => voice = true,
            "--mouse" => mouse = true,
            "--leds" => leds = val()? != "cantina",
            "--show-dir" => show_dir = val()?.into(),
            "--brain" => brain = val()?.parse().map_err(anyhow::Error::msg)?,
            "--music" => {
                let m: r3x_runtime::music::MusicMode = val()?.parse().map_err(anyhow::Error::msg)?;
                // Read by run(); set before any other thread starts.
                std::env::set_var("R3X_MUSIC", if m == r3x_runtime::music::MusicMode::Rust { "rust" } else { "cantina" });
            }
            "--tap-url" => tap_url = val()?,
            "--profile" => profile_path = val()?.into(),
            "--session-log" => session_log = Some(val()?.into()),
            "-h" | "--help" => {
                println!("{USAGE}");
                return Ok(());
            }
            other => anyhow::bail!("unknown argument {other}\n{USAGE}"),
        }
    }

    let profile = RobotProfile::load(&profile_path).with_context(|| format!("profile {}", profile_path.display()))?;
    let bridge = if bridge {
        let file = std::env::var_os("R3X_TOKEN_FILE")
            .map(Into::into)
            .unwrap_or_else(|| tokens::config_dir().join("tap_token"));
        let mut b = BridgeConfig::new(tap_url, tokens::load_or_create("R3X_TAP_TOKEN", file)?, &profile);
        b.cantina_leds = !leds;
        Some(b)
    } else {
        None
    };
    let cfg = RuntimeConfig {
        bind: bind.parse().with_context(|| format!("bad --bind {bind}"))?,
        clients: r3x_runtime::clients_from_env()?,
        origins: r3x_runtime::origins_from_env(),
        profile: Some(Arc::new(profile)),
        bridge,
        session_log,
        logs: Some(hub.sender()),
        voice: if voice { Some(r3x_voice::VoiceSettings::from_env()?) } else { None },
        mouse,
        show_dir,
        drivers: Some(r3x_runtime::performer::DriverOptions { leds }),
    };
    let bus = Bus::default();
    tokio::select! {
        r = async {
            let listener = tokio::net::TcpListener::bind(cfg.bind).await?;
            r3x_runtime::run_with_brain(bus, cfg, Some(level), listener, brain).await
        } => r,
        _ = tokio::signal::ctrl_c() => Ok(()),
    }
}
