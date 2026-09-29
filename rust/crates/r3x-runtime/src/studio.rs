//! Studio (plan Phase 9): the runtime side of `perf.save_show` and `perf.preview`.
//!
//! Saving writes a clip or cue into the show folder only after it validates and lints: the
//! new document may add no lint error to the folder (errors already there do not block it).
//! The write is atomic (temp file + rename) and the performer's folder reload picks it up.
//! Files are formatted like the hand-written ones: one field per line, tracks one per line.

use std::collections::HashSet;
use std::path::{Path, PathBuf};

use r3x_contracts::RobotProfile;
use r3x_performer_core::performer::parse_clip;
use r3x_performer_core::show::catalog::Catalog;
use r3x_performer_core::show::lint::{joint_limits_from_profile, lint_catalog, lint_clip};
use serde_json::Value;

use crate::performer::show_files;

/// `snake_case`, starting with a letter: also rules out any path in the id.
fn valid_id(id: &str) -> bool {
    let mut c = id.chars();
    c.next().is_some_and(|f| f.is_ascii_lowercase())
        && c.all(|x| x.is_ascii_lowercase() || x.is_ascii_digit() || x == '_')
        && id.len() <= 64
}

/// Validate, lint and write `doc`; returns the file written.
pub fn save_show(dir: &Path, profile: &RobotProfile, doc: &Value, overwrite: bool) -> Result<PathBuf, String> {
    let id = doc.get("id").and_then(Value::as_str).ok_or("the document has no id")?;
    if !valid_id(id) {
        return Err(format!("id {id:?} must be snake_case"));
    }
    let kind = doc.get("kind").and_then(Value::as_str).unwrap_or("");
    let sub = match kind {
        "clip" => "clips",
        "cue" => "cues",
        _ => return Err(format!("Studio saves clips and cues, not {kind:?}")),
    };
    let root = dir.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_else(|| "show".into());
    let rel = format!("{root}/{sub}/{id}.json");
    let path = dir.join(sub).join(format!("{id}.json"));
    let files: Vec<(String, String)> = show_files(dir).into_iter().map(|(p, t, _)| (p, t)).collect();
    if let Some((p, _)) = files.iter().find(|(p, _)| p.ends_with(&format!("/{id}.json")) && *p != rel) {
        return Err(format!("id {id} is already taken by {p}"));
    }
    if path.exists() && !overwrite {
        return Err(format!("{rel} exists (save with overwrite to replace it)"));
    }
    let text = format_doc(doc);
    let limits = joint_limits_from_profile(profile)?;
    let lint = |files: &[(String, String)]| -> HashSet<String> {
        let cat = Catalog::from_files(files.iter().map(|(p, t)| (p.as_str(), t.as_str())));
        lint_catalog(&cat, &limits).errors.into_iter().collect()
    };
    let before = lint(&files);
    let mut next: Vec<(String, String)> = files.into_iter().filter(|(p, _)| *p != rel).collect();
    next.push((rel.clone(), text.clone()));
    let mut added: Vec<String> = lint(&next).difference(&before).cloned().collect();
    if !added.is_empty() {
        added.sort();
        return Err(added.join("; "));
    }
    std::fs::create_dir_all(dir.join(sub)).map_err(|e| e.to_string())?;
    let tmp = dir.join(sub).join(format!(".{id}.json.tmp"));
    std::fs::write(&tmp, &text).and_then(|_| std::fs::rename(&tmp, &path)).map_err(|e| {
        let _ = std::fs::remove_file(&tmp);
        format!("write {rel}: {e}")
    })?;
    Ok(path)
}

/// Bench-safe preview check: a clip that validates and lints clean against the profile.
pub fn check_preview(profile: &RobotProfile, clip: &Value) -> Result<(), String> {
    let c = parse_clip(clip)?;
    let mut errors = Vec::new();
    lint_clip(&c, &joint_limits_from_profile(profile)?, &mut errors);
    if errors.is_empty() {
        Ok(())
    } else {
        Err(errors.join("; "))
    }
}

const TOP_ORDER: &[&str] = &[
    "id", "kind", "title", "description", "tags", "tier", "requires", "duration", "interruptible_after", "tracks",
    "actions",
];
const TRACK_ORDER: &[&str] = &["mode", "keys", "ease", "blend"];

fn ordered<'a>(m: &'a serde_json::Map<String, Value>, order: &[&str]) -> Vec<(&'a String, &'a Value)> {
    let rank = |k: &str| order.iter().position(|o| *o == k).unwrap_or(order.len());
    let mut v: Vec<_> = m.iter().collect();
    v.sort_by_key(|(k, _)| rank(k)); // stable: unknown keys keep their order, last
    v
}

/// One line, with the spacing of the hand-written files (`{"a": 1, "b": [0, 1]}`).
fn compact(v: &Value, order: &[&str]) -> String {
    match v {
        Value::Array(a) => format!("[{}]", a.iter().map(|x| compact(x, &[])).collect::<Vec<_>>().join(", ")),
        Value::Object(m) => format!(
            "{{{}}}",
            ordered(m, order)
                .into_iter()
                .map(|(k, x)| format!("{}: {}", Value::String(k.clone()), compact(x, &[])))
                .collect::<Vec<_>>()
                .join(", ")
        ),
        _ => v.to_string(),
    }
}

/// The show-file layout: top-level fields one per line; `tracks` one track per line.
pub fn format_doc(doc: &Value) -> String {
    let Value::Object(m) = doc else { return compact(doc, &[]) + "\n" };
    let lines: Vec<String> = ordered(m, TOP_ORDER)
        .into_iter()
        .map(|(k, v)| {
            let key = Value::String(k.clone());
            match (k.as_str(), v) {
                ("tracks", Value::Object(tr)) if !tr.is_empty() => {
                    let inner: Vec<String> =
                        tr.iter().map(|(j, t)| format!("    {}: {}", Value::String(j.clone()), compact(t, TRACK_ORDER))).collect();
                    format!("  {key}: {{\n{}\n  }}", inner.join(",\n"))
                }
                _ => format!("  {key}: {}", compact(v, &[])),
            }
        })
        .collect();
    format!("{{\n{}\n}}\n", lines.join(",\n"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    /// A scratch copy of the repo's show folder.
    fn scratch() -> PathBuf {
        let src = crate::performer::default_show_dir();
        let dir = std::env::temp_dir().join(format!("r3x-studio-{}", uuid::Uuid::new_v4())).join("show");
        for sub in ["clips", "cues", "sequences"] {
            std::fs::create_dir_all(dir.join(sub)).unwrap();
            for e in std::fs::read_dir(src.join(sub)).unwrap().flatten() {
                std::fs::copy(e.path(), dir.join(sub).join(e.file_name())).unwrap();
            }
        }
        std::fs::copy(src.join("idle.json"), dir.join("idle.json")).unwrap();
        dir
    }

    fn clip(id: &str, peak: f64) -> Value {
        json!({"id": id, "kind": "clip", "title": "T", "description": "d", "tags": [], "tier": "free",
               "duration": 1.0, "tracks": {"head_tilt": {"mode": "additive", "keys": [[0, 0], [0.5, peak], [1.0, 0]]}}})
    }

    #[test]
    fn save_validates_before_writing() {
        let dir = scratch();
        let profile = RobotProfile::load(crate::default_profile_path()).unwrap();
        let path = save_show(&dir, &profile, &clip("studio_nod", 6.0), false).unwrap();
        let cat = crate::performer::load_catalog(&dir);
        assert!(cat.get("studio_nod").is_some(), "{:?}", cat.errors);
        // Round trip: the written text parses back to the same document.
        let back: Value = serde_json::from_str(&std::fs::read_to_string(&path).unwrap()).unwrap();
        assert_eq!(back, clip("studio_nod", 6.0));

        let reject = |doc: Value, overwrite: bool| save_show(&dir, &profile, &doc, overwrite).unwrap_err();
        assert!(reject(clip("studio_nod", 5.0), false).contains("exists"));
        save_show(&dir, &profile, &clip("studio_nod", 5.0), true).unwrap();
        assert!(reject(clip("../escape", 5.0), false).contains("snake_case"));
        assert!(reject(clip("nod_x", 500.0), false).contains("outside"), "limit");
        assert!(reject(json!({"id": "nope", "kind": "sequence"}), false).contains("clips and cues"));
        assert!(reject(clip("yes", 5.0), false).contains("already taken"), "a cue owns the id");
        let bad_cue = json!({"id": "studio_cue", "kind": "cue", "description": "d", "tags": [], "tier": "free",
                             "actions": [{"at": 0, "do": "clip", "id": "no_such_clip"}]});
        assert!(reject(bad_cue, false).contains("no_such_clip"));
        assert!(!dir.join("clips/nod_x.json").exists() && !dir.join("cues/studio_cue.json").exists());
        let _ = std::fs::remove_dir_all(dir.parent().unwrap());
    }

    #[test]
    fn preview_is_bench_safe_and_format_matches_the_files() {
        let profile = RobotProfile::load(crate::default_profile_path()).unwrap();
        assert!(check_preview(&profile, &clip("p", 6.0)).is_ok());
        assert!(check_preview(&profile, &clip("p", 500.0)).is_err());
        let nod = std::fs::read_to_string(crate::performer::default_show_dir().join("clips/nod.json")).unwrap();
        assert_eq!(format_doc(&serde_json::from_str(&nod).unwrap()), nod);
    }
}
