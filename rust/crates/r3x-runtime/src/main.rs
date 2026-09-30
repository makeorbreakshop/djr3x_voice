//! `r3x-runtime [--standalone | --bridge] [--headless] [--public] [--bind ADDR] [--audio device|null] [--no-voice] [--no-vision] [--voice] [--mouse] [--leds r3x|cantina] [--brain cantina|rust] [--music cantina|rust] [--vision] [--no-pad] [--show-dir DIR] [--tap-url URL] [--profile PATH] [--session-log DIR]`
//!
//! Standalone (the default): the whole robot in this process - voice, the Rust brain, the
//! Rust music engine, vision (fail-open), performer and drivers - with no CantinaOS.
//! `--headless`: no local audio device, LED/servo drivers, camera or vision (remote clients
//! only). `--public` (implies headless): the public server (plan Phase 10,
//! `r3x_runtime::public`) - one brain per visitor, visitor tokens, `public` tier, rate limits
//! and budget caps (`R3X_PUBLIC_*`), no music; nothing else runs.
//! `--bridge` is the legacy mode: CantinaOS is the brain (and, unless `--music rust` /
//! `--voice`, music and voice), reached through its bus tap.
//!
//! Env: `R3X_GATEWAY_ADDR`, `R3X_TAP_URL`, `R3X_PROFILE`, `R3X_ALLOWED_ORIGINS`,
//! `R3X_GATEWAY_TOKEN` / `R3X_TAP_TOKEN`, `R3X_CLI_TOKEN`, `R3X_PUBLIC_TOKEN`, `RUST_LOG`,
//! `R3X_AUDIO=null` (= `--audio null`: no device, silent mic; `R3X_NULL_AUDIO_SPEED`),
//! `R3X_VOICE` (`--voice`; `0` = `--no-voice`), `R3X_VISION` (`--vision`; `0` = `--no-vision`),
//! `R3X_LEDS=cantina` (= `--leds cantina`: CantinaOS keeps the face/chest boards; otherwise run
//! it with `R3X_EXTERNAL_BODY=1`), `SHOW_DIR` (= `--show-dir`), `ARDUINO_SERIAL_PORT`,
//! `CHEST_SERIAL_PORT`, `R3X_SERVO_PORT`, `FORCE_MOCK_LED_CONTROLLER`, `FORCE_MOCK_CHEST`,
//! `R3X_BRAIN` (= `--brain`; `rust` not with `--bridge`), `R3X_MEMORY_DB`, `R3X_PERSONA_DIR`,
//! `R3X_CANTINA_DIR` (one-time memory import), `R3X_MUSIC` (= `--music`; with `--bridge` run
//! CantinaOS with `R3X_EXTERNAL_MUSIC=1`), `MUSIC_DIR`, `R3X_SFX_DIR`, `R3X_CLAP_DIR`,
//! `R3X_BEAT_CACHE_DIR`, `R3X_FIXTURES=replay` + `R3X_FIXTURE_DIR` (no paid calls: Claude,
//! Jev, TTS and the recorded picks replay; STT is scripted), `R3X_PAD` (`0` = `--no-pad`).
//!
//! A DualShock 3 on USB drives the puppeteer unless `--no-pad` or `--headless` (plug it in,
//! press PS; mapping in `r3x-performer-core` `show::puppeteer`).

use std::sync::Arc;

use anyhow::Context;
use r3x_bus::Bus;
use r3x_contracts::RobotProfile;
use r3x_gateway::tokens;
use r3x_runtime::{bridge::BridgeConfig, RuntimeConfig};

const USAGE: &str = "usage: r3x-runtime [--standalone | --bridge] [--headless] [--public] [--bind ADDR] [--audio device|null] [--no-voice] [--no-vision] [--voice] [--mouse] [--leds r3x|cantina] [--brain cantina|rust] [--music cantina|rust] [--vision] [--no-pad] [--show-dir DIR] [--tap-url URL] [--profile PATH] [--session-log DIR]";

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // The repo-root `.env` (API keys, hardware ports), never overriding the shell.
    let dotenv = r3x_contracts::dotenv::load();
    let (hub, level) = r3x_ops::init_tracing("info");
    tracing::info!(vars = dotenv.len(), "loaded .env");
    let env = |k: &str| std::env::var(k).ok().filter(|v| !v.is_empty());

    let mut bind = env("R3X_GATEWAY_ADDR").unwrap_or_else(|| r3x_runtime::DEFAULT_BIND.into());
    let mut tap_url = env("R3X_TAP_URL").unwrap_or_else(|| r3x_runtime::DEFAULT_TAP_URL.into());
    let mut profile_path = r3x_runtime::default_profile_path();
    let (mut bridge, mut session_log) = (false, None);
    let on = |k: &str| env(k).map(|v| !matches!(v.as_str(), "0" | "false" | "no" | "off"));
    // Resolved after the arguments: the defaults depend on --bridge.
    let (mut voice, mut vision) = (on("R3X_VOICE"), on("R3X_VISION"));
    let mut music: Option<r3x_runtime::music::MusicMode> = env("R3X_MUSIC").map(|v| v.parse()).transpose().map_err(anyhow::Error::msg)?;
    let mut mouse = false;
    let mut pad = on("R3X_PAD").unwrap_or(true);
    let (mut headless, mut public) = (false, false);
    // Who drives the face/chest boards: r3x (default; run CantinaOS with R3X_EXTERNAL_BODY=1)
    // or CantinaOS (`--leds cantina` / R3X_LEDS=cantina).
    let mut leds = env("R3X_LEDS").is_none_or(|v| v != "cantina");
    let mut show_dir = r3x_runtime::performer::default_show_dir();
    let mut brain: Option<r3x_runtime::brain::BrainMode> = env("R3X_BRAIN").map(|v| v.parse()).transpose().map_err(anyhow::Error::msg)?;
    let mut args = std::env::args().skip(1);
    while let Some(a) = args.next() {
        let mut val = || args.next().with_context(|| format!("{a} needs a value\n{USAGE}"));
        match a.as_str() {
            "--bind" => bind = val()?,
            "--bridge" => bridge = true,
            "--standalone" => bridge = false,
            "--headless" => headless = true,
            "--public" => public = true,
            "--voice" => voice = Some(true),
            "--no-voice" => voice = Some(false),
            "--vision" => vision = Some(true),
            "--no-vision" => vision = Some(false),
            "--audio" => match val()?.as_str() {
                // Read by the voice settings and the music engine; set before any thread starts.
                "null" => std::env::set_var("R3X_AUDIO", "null"),
                "device" => std::env::remove_var("R3X_AUDIO"),
                o => anyhow::bail!("--audio {o}: expected device or null"),
            },
            "--mouse" => mouse = true,
            "--no-pad" => pad = false,
            "--leds" => leds = val()? != "cantina",
            "--show-dir" => show_dir = val()?.into(),
            "--brain" => brain = Some(val()?.parse().map_err(anyhow::Error::msg)?),
            "--music" => music = Some(val()?.parse().map_err(anyhow::Error::msg)?),
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

    if public {
        anyhow::ensure!(!bridge && !mouse, "--public runs alone: no --bridge or --mouse");
        let profile = RobotProfile::load(&profile_path).with_context(|| format!("profile {}", profile_path.display()))?;
        let cfg = r3x_runtime::public::PublicConfig::from_env(Arc::new(profile), show_dir, voice.unwrap_or(true))?;
        let listener = tokio::net::TcpListener::bind(&bind).await.with_context(|| format!("bind {bind}"))?;
        return tokio::select! {
            r = r3x_runtime::public::PublicServer::new(cfg).serve(listener) => r.map_err(Into::into),
            _ = tokio::signal::ctrl_c() => Ok(()),
        };
    }
    if headless {
        // = --audio null (no device; remote clients still get speech). Read by the voice
        // settings and the music engine; set before any other thread starts.
        std::env::set_var("R3X_AUDIO", "null");
        anyhow::ensure!(!mouse, "--headless has no local mouse");
    }

    use r3x_runtime::{brain::BrainMode, music::MusicMode};
    let standalone = !bridge;
    let brain = brain.unwrap_or(if standalone { BrainMode::Rust } else { BrainMode::Cantina });
    let music = music.unwrap_or(if standalone { MusicMode::Rust } else { MusicMode::Cantina });
    let voice = voice.unwrap_or(standalone);
    let vision = !headless && vision.unwrap_or(standalone);
    // Read by boot(); set before any other thread starts.
    std::env::set_var("R3X_MUSIC", if music == MusicMode::Rust { "rust" } else { "cantina" });
    std::env::set_var("R3X_VISION", if vision { "1" } else { "0" });
    std::env::set_var("R3X_PAD", if pad && !headless { "1" } else { "0" });
    tracing::info!(standalone, ?brain, ?music, voice, vision, pad, "r3x runtime");

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
        drivers: (!headless).then_some(r3x_runtime::performer::DriverOptions { leds }),
    };
    // The camera's ffmpeg child lives on the capture thread, which exit does not unwind: turn
    // it off on every way out (return, error, SIGINT/SIGTERM/SIGHUP, a panic on this thread).
    let prev = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        if std::thread::current().name() == Some("main") {
            r3x_vision::kill_captures();
        }
        prev(info);
    }));
    let bus = Bus::default();
    let r = tokio::select! {
        r = async {
            let listener = tokio::net::TcpListener::bind(cfg.bind).await?;
            r3x_runtime::run_with_brain(bus, cfg, Some(level), listener, brain).await
        } => r,
        s = shutdown_signal() => {
            tracing::info!("{s}: shutting down");
            Ok(())
        }
    };
    r3x_vision::kill_captures();
    r
}

/// SIGINT (Ctrl-C, the launcher's stop), SIGTERM or SIGHUP.
async fn shutdown_signal() -> &'static str {
    use tokio::signal::unix::{signal, SignalKind};
    let (mut term, mut hup) = match (signal(SignalKind::terminate()), signal(SignalKind::hangup())) {
        (Ok(t), Ok(h)) => (t, h),
        _ => {
            let _ = tokio::signal::ctrl_c().await;
            return "SIGINT";
        }
    };
    tokio::select! {
        _ = tokio::signal::ctrl_c() => "SIGINT",
        _ = term.recv() => "SIGTERM",
        _ = hup.recv() => "SIGHUP",
    }
}
