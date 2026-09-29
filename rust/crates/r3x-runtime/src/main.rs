//! `r3x-runtime [--bind ADDR] [--bridge] [--tap-url URL] [--profile PATH] [--session-log DIR]`
//!
//! Env: `R3X_GATEWAY_ADDR`, `R3X_TAP_URL`, `R3X_PROFILE`, `R3X_ALLOWED_ORIGINS`,
//! `R3X_GATEWAY_TOKEN` / `R3X_TAP_TOKEN`, `R3X_CLI_TOKEN`, `R3X_PUBLIC_TOKEN`, `RUST_LOG`.

use std::sync::Arc;

use anyhow::Context;
use r3x_bus::Bus;
use r3x_contracts::RobotProfile;
use r3x_gateway::tokens;
use r3x_runtime::{bridge::BridgeConfig, RuntimeConfig};

const USAGE: &str = "usage: r3x-runtime [--bind ADDR] [--bridge] [--tap-url URL] [--profile PATH] [--session-log DIR]";

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let (hub, level) = r3x_ops::init_tracing("info");
    let env = |k: &str| std::env::var(k).ok().filter(|v| !v.is_empty());

    let mut bind = env("R3X_GATEWAY_ADDR").unwrap_or_else(|| r3x_runtime::DEFAULT_BIND.into());
    let mut tap_url = env("R3X_TAP_URL").unwrap_or_else(|| r3x_runtime::DEFAULT_TAP_URL.into());
    let mut profile_path = r3x_runtime::default_profile_path();
    let (mut bridge, mut session_log) = (false, None);
    let mut args = std::env::args().skip(1);
    while let Some(a) = args.next() {
        let mut val = || args.next().with_context(|| format!("{a} needs a value\n{USAGE}"));
        match a.as_str() {
            "--bind" => bind = val()?,
            "--bridge" => bridge = true,
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
        Some(BridgeConfig {
            tap_url,
            tap_token: tokens::load_or_create("R3X_TAP_TOKEN", file)?,
            emotes: profile.emotes.clone(),
        })
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
    };
    let bus = Bus::default();
    tokio::select! {
        r = r3x_runtime::run(bus, cfg, Some(level)) => r,
        _ = tokio::signal::ctrl_c() => Ok(()),
    }
}
