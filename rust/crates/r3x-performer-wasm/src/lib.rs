//! The performer in the browser: a wasm-bindgen wrapper of `r3x-performer-core`.
//!
//! JSON in, JSON out, so the sim needs no generated types: commands are
//! `r3x_performer_core::performer::Command` (`{"cmd": "perform", "id": "nod"}`), frames are
//! `performer::Frames`, events are `performer::Out`. Build: `npm run build:wasm` in sim/web.

use r3x_contracts::messages::PerfCommand;
use r3x_contracts::{RobotProfile, Source};
use r3x_performer_core::performer::{Command, PerformerConfig};
use r3x_performer_core::show::catalog::Catalog;
use r3x_performer_core::show::expand::expand;
use r3x_performer_core::show::lint::{joint_limits_from_profile, lint_catalog};
use r3x_performer_core::Performer;
use serde_json::{json, Map, Value};
use std::sync::Arc;
use wasm_bindgen::prelude::*;

fn err(e: impl std::fmt::Display) -> JsError {
    JsError::new(&e.to_string())
}

/// `{"show/clips/nod.json": {...} | "<json text>", ...}` -> a catalogue (idle.json by name).
fn catalog(files_json: &str) -> Result<Catalog, JsError> {
    let files: Map<String, Value> = serde_json::from_str(files_json).map_err(err)?;
    let texts: Vec<(String, String)> = files
        .into_iter()
        .map(|(p, v)| {
            (
                p,
                if let Value::String(s) = v {
                    s
                } else {
                    v.to_string()
                },
            )
        })
        .collect();
    Ok(Catalog::from_files(
        texts.iter().map(|(p, t)| (p.as_str(), t.as_str())),
    ))
}

#[wasm_bindgen]
pub struct WasmPerformer {
    inner: Performer,
}

#[wasm_bindgen]
impl WasmPerformer {
    /// `profile_json`: `profiles/<name>/robot.json`; `show_files_json`: see [`catalog`].
    #[wasm_bindgen(constructor)]
    pub fn new(
        profile_json: &str,
        show_files_json: &str,
        seed: u32,
    ) -> Result<WasmPerformer, JsError> {
        let profile = RobotProfile::from_json(profile_json).map_err(err)?;
        let cat = Arc::new(catalog(show_files_json)?);
        let inner = Performer::new(
            cat,
            &profile,
            PerformerConfig {
                seed,
                ..Default::default()
            },
        )
        .map_err(err)?;
        Ok(WasmPerformer { inner })
    }

    /// Apply a performer command (JSON).
    pub fn command(&mut self, cmd_json: &str) -> Result<(), JsError> {
        let c: Command = serde_json::from_str(cmd_json).map_err(err)?;
        self.inner.command(c);
        Ok(())
    }

    /// Apply a bus `PerfCommand` (JSON) from `source` (e.g. "ui", "claude").
    #[wasm_bindgen(js_name = perfCommand)]
    pub fn perf_command(&mut self, cmd_json: &str, source: &str) -> Result<(), JsError> {
        let c: PerfCommand = serde_json::from_str(cmd_json).map_err(err)?;
        let s: Source = serde_json::from_value(Value::String(source.into())).map_err(err)?;
        self.inner.perf_command(&c, s);
        Ok(())
    }

    /// Advance to `t` (monotonic seconds); returns the frame as JSON.
    pub fn tick(&mut self, t: f64) -> String {
        serde_json::to_string(&self.inner.tick(t)).unwrap_or_default()
    }

    /// Outgoing events since the last call, as a JSON array.
    pub fn events(&mut self) -> String {
        serde_json::to_string(&self.inner.take_events()).unwrap_or_default()
    }

    /// Runs on each layer: `[{run_id, id, kind, source, layer, ...}]`.
    pub fn running(&self) -> String {
        serde_json::to_string(&self.inner.player.running()).unwrap_or_default()
    }
}

/// Expand a cue or sequence to the SPEC parity list (JSON).
#[wasm_bindgen(js_name = expandShow)]
pub fn expand_show(show_files_json: &str, root: &str, bpm: Option<f64>) -> Result<String, JsError> {
    let cat = catalog(show_files_json)?;
    let out = expand(root, &cat, bpm).map_err(err)?;
    serde_json::to_string(&out).map_err(err)
}

/// Lint a show folder against a Robot Profile: `{errors, warnings}`.
#[wasm_bindgen(js_name = lintShow)]
pub fn lint_show(profile_json: &str, show_files_json: &str) -> Result<String, JsError> {
    let profile = RobotProfile::from_json(profile_json).map_err(err)?;
    let limits = joint_limits_from_profile(&profile).map_err(err)?;
    let res = lint_catalog(&catalog(show_files_json)?, &limits);
    Ok(json!({"errors": res.errors, "warnings": res.warnings}).to_string())
}
