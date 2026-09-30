//! One-shot import of CantinaOS memory: `memory_data/events.jsonl`, `memory_data/profiles/*.json`
//! and the `dj_*` keys of `nervous_system_state.json`. Each import is marked in `ops_state`
//! and skipped if run again.

use serde_json::{json, Value};
use std::path::Path;

use crate::model::{EventKind, PersonProfile};
use crate::{Memory, Result};

#[derive(Debug, Default, Clone, PartialEq, Eq)]
pub struct ImportStats {
    pub events: usize,
    /// Stream chunks (`is_complete: false`), empty transcripts, unknown types.
    pub skipped: usize,
    pub profiles: usize,
    pub state_keys: usize,
    pub dj_tracks: usize,
}

fn marker(mem: &Memory, what: &str) -> Result<bool> {
    let key = format!("import:{what}");
    if mem.get_state(&key)?.is_some() {
        return Ok(true);
    }
    mem.set_state(&key, &json!(mem.now()))?;
    Ok(false)
}

/// Timeline events. Only *complete* replies become `rex_said` (CantinaOS logged every stream
/// chunk too); empty transcripts are dropped.
pub fn import_events(mem: &Memory, path: &Path, stats: &mut ImportStats) -> Result<()> {
    if !path.exists() || marker(mem, "events.jsonl")? {
        return Ok(());
    }
    let text = std::fs::read_to_string(path)?;
    let mut db = mem.db();
    let tx = db.transaction()?;
    for line in text.lines().filter(|l| !l.trim().is_empty()) {
        let Ok(e) = serde_json::from_str::<Value>(line) else {
            stats.skipped += 1;
            continue;
        };
        let d = &e["event_data"];
        let kind = EventKind::from_cantina(e["event_type"].as_str().unwrap_or_default());
        let data = match kind {
            Some(EventKind::UserSaid) | Some(EventKind::RexSaid) => {
                let t = d["text"].as_str().unwrap_or_default().trim();
                if t.is_empty() || (kind == Some(EventKind::RexSaid) && d["is_complete"] == json!(false)) {
                    stats.skipped += 1;
                    continue;
                }
                json!({"text": t, "imported": true})
            }
            Some(_) => d.clone(),
            None => {
                stats.skipped += 1;
                continue;
            }
        };
        let conv = e["conversation_id"].as_str().or_else(|| d["conversation_id"].as_str());
        tx.execute(
            "INSERT INTO timeline(ts, kind, person, conversation_id, data) VALUES (?1,?2,?3,?4,?5)",
            rusqlite::params![e["timestamp"].as_f64().unwrap_or(0.0), kind.map(EventKind::as_str), e["person"].as_str(), conv, data.to_string()],
        )?;
        stats.events += 1;
    }
    tx.commit()?;
    Ok(())
}

/// Profile JSONs (`<name>.json`, the pydantic `PersonProfile` shape). Upserts; safe to repeat.
pub fn import_profiles(mem: &Memory, dir: &Path, stats: &mut ImportStats) -> Result<()> {
    let Ok(entries) = std::fs::read_dir(dir) else { return Ok(()) };
    for e in entries.flatten() {
        let p = e.path();
        if p.extension().is_some_and(|x| x == "json") {
            match serde_json::from_str::<PersonProfile>(&std::fs::read_to_string(&p)?) {
                Ok(profile) if !profile.name.is_empty() => {
                    mem.save_profile(&profile)?;
                    stats.profiles += 1;
                }
                _ => tracing::warn!("skipping unreadable profile {}", p.display()),
            }
        }
    }
    Ok(())
}

/// `dj_*` keys -> `ops_state`; `dj_track_history` also -> `dj_history`.
pub fn import_nervous_state(mem: &Memory, path: &Path, stats: &mut ImportStats) -> Result<()> {
    if !path.exists() || marker(mem, "nervous_system_state.json")? {
        return Ok(());
    }
    let state: Value = serde_json::from_str(&std::fs::read_to_string(path)?)?;
    let Some(obj) = state.as_object() else { return Ok(()) };
    let now = mem.now();
    for (k, v) in obj.iter().filter(|(k, _)| k.starts_with("dj_")) {
        mem.set_state(k, v)?;
        stats.state_keys += 1;
    }
    for t in obj.get("dj_track_history").and_then(Value::as_array).into_iter().flatten().filter_map(Value::as_str) {
        mem.db().execute("INSERT INTO dj_history(ts, track) VALUES (?1, ?2)", rusqlite::params![now, t])?;
        stats.dj_tracks += 1;
    }
    Ok(())
}

/// Everything, from a checkout's `cantina_os/` folder.
pub fn import_cantina(mem: &Memory, cantina_root: &Path) -> Result<ImportStats> {
    let mut s = ImportStats::default();
    import_profiles(mem, &cantina_root.join("memory_data/profiles"), &mut s)?;
    import_events(mem, &cantina_root.join("memory_data/events.jsonl"), &mut s)?;
    import_nervous_state(mem, &cantina_root.join("cantina_os/services/nervous_system_service/data/nervous_system_state.json"), &mut s)?;
    tracing::info!("CantinaOS memory import: {s:?}");
    Ok(s)
}
