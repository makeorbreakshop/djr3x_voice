//! An in-memory set of show items plus the idle policy, indexed by id (port of
//! `catalog.ts` and `loader.ts`). File I/O lives in callers: they hand over `(path, json)`.

use super::types::{Clip, IdlePolicy, Kind, ShowItem};
use super::validate::{validate_idle, validate_item};
use indexmap::IndexMap;
use serde_json::Value;
use std::sync::Arc;

#[derive(Clone, Debug, Default)]
pub struct Catalog {
    pub items: IndexMap<String, ShowItem>,
    /// Validation and naming problems found while building (the linter asserts none).
    pub errors: Vec<String>,
    pub idle: Option<IdlePolicy>,
    /// `intentions.json`: intention id -> its pool (see `show::intentions`).
    pub intentions: IndexMap<String, super::intentions::Intention>,
}

/// Lowercase, alphanumerics only: the fuzzy-match key.
pub fn norm(s: &str) -> String {
    s.to_lowercase()
        .chars()
        .filter(|c| c.is_ascii_lowercase() || c.is_ascii_digit())
        .collect()
}

impl Catalog {
    pub fn new(items: impl IntoIterator<Item = Value>, idle: Option<Value>) -> Self {
        let mut c = Catalog::default();
        for x in items {
            c.add(&x, None);
        }
        if let Some(i) = idle {
            c.set_idle(&i);
        }
        c
    }

    /// The repo's `show/` folder as `(repo-relative path, file contents)`, e.g.
    /// `("show/clips/nod.json", "{...}")`; `show/idle.json` is recognised by name.
    pub fn from_files<'a>(files: impl IntoIterator<Item = (&'a str, &'a str)>) -> Self {
        let mut files: Vec<_> = files.into_iter().collect();
        files.sort_by(|a, b| a.0.cmp(b.0));
        let mut c = Catalog::default();
        for (path, text) in files {
            match serde_json::from_str::<Value>(text) {
                Err(e) => c.errors.push(format!("{path}: {e}")),
                Ok(_) if path.ends_with("intentions.json") => {
                    let (m, errs) = super::intentions::parse(text, path);
                    c.intentions = m;
                    c.errors.extend(errs);
                }
                Ok(v) if path.ends_with("idle.json") => c.set_idle(&v),
                Ok(v) => c.add(&v, Some(path)),
            }
        }
        c.check_intentions();
        c
    }

    /// Every pool entry is a loaded clip or cue of tier free/cheap (intentions fire from Jev
    /// and Claude). Bad entries are reported and dropped; an intention left empty goes too.
    fn check_intentions(&mut self) {
        let mut errs = vec![];
        for (id, i) in self.intentions.iter_mut() {
            i.pool.retain(|p| match self.items.get(p) {
                None => {
                    errs.push(format!("intentions.json: {id}: unknown item {p}"));
                    false
                }
                Some(it) if it.kind() == super::types::Kind::Sequence => {
                    errs.push(format!("intentions.json: {id}: {p} is a sequence (pools take clips and cues)"));
                    false
                }
                Some(it) if !matches!(it.tier, super::types::Tier::Free | super::types::Tier::Cheap) => {
                    errs.push(format!("intentions.json: {id}: {p} is tier {:?} (pools take free or cheap)", it.tier));
                    false
                }
                Some(_) => true,
            });
        }
        self.intentions.retain(|_, i| !i.pool.is_empty());
        self.errors.extend(errs);
    }

    pub fn add(&mut self, x: &Value, file: Option<&str>) {
        let mut errs = validate_item(x, file);
        let id = x.get("id").and_then(Value::as_str);
        if let (Some(file), Some(id)) = (file, id) {
            let name = file.rsplit('/').next().unwrap_or(file);
            let stem = name.strip_suffix(".json").unwrap_or(name);
            if stem != id {
                errs.push(format!("{file}: file name must equal id \"{id}\""));
            }
            let mut parts = file.rsplit('/');
            let dir = parts.nth(1).unwrap_or("");
            let want = match dir {
                "clips" => Some("clip"),
                "cues" => Some("cue"),
                "sequences" => Some("sequence"),
                _ => None,
            };
            let kind = x.get("kind").and_then(Value::as_str).unwrap_or("undefined");
            if want.is_some_and(|w| w != kind) {
                errs.push(format!("{file}: a {kind} in {dir}/"));
            }
        }
        let valid = errs.is_empty();
        self.errors.extend(errs);
        let Some(id) = id else { return };
        // Invalid documents are reported and skipped: only well-formed items are typed.
        if !valid {
            return;
        }
        let item = match ShowItem::from_value(x) {
            Ok(it) => it,
            Err(e) => return self.errors.push(format!("{}: {e}", file.unwrap_or(id))),
        };
        if self.items.contains_key(id) {
            self.errors.push(format!(
                "{}: duplicate id \"{id}\" (ids are unique across kinds)",
                file.unwrap_or(id)
            ));
        }
        self.items.insert(id.to_owned(), item);
    }

    pub fn set_idle(&mut self, x: &Value) {
        let errs = validate_idle(x);
        if errs.is_empty() {
            self.idle = serde_json::from_value(x.clone()).ok();
        }
        self.errors.extend(errs);
    }

    pub fn get(&self, id: &str) -> Option<&ShowItem> {
        self.items.get(id)
    }
    pub fn clip(&self, id: &str) -> Option<&Arc<Clip>> {
        self.items.get(id).and_then(ShowItem::clip)
    }

    /// Items of one kind, sorted by id.
    pub fn list(&self, kind: Kind) -> Vec<&ShowItem> {
        let mut v: Vec<_> = self.items.values().filter(|x| x.kind() == kind).collect();
        v.sort_by(|a, b| a.id.cmp(&b.id));
        v
    }

    /// One row per item, sorted by kind then id, for pickers (the sim's show list):
    /// `{id, kind, tier, description, tags, title?, clock?, loop?, layer?, duration?, requires?}`.
    pub fn summary(&self) -> Value {
        let mut rows = Vec::new();
        for kind in [Kind::Sequence, Kind::Cue, Kind::Clip] {
            for it in self.list(kind) {
                let mut r = serde_json::json!({
                    "id": it.id, "kind": kind, "tier": it.tier,
                    "description": it.description, "tags": it.tags,
                });
                if let Some(t) = &it.title {
                    r["title"] = t.clone().into();
                }
                if let Some(s) = it.sequence() {
                    r["clock"] = serde_json::to_value(s.clock).unwrap_or_default();
                    r["loop"] = s.looped.into();
                    r["layer"] = serde_json::to_value(s.layer).unwrap_or_default();
                }
                if let Some(c) = it.clip() {
                    r["duration"] = c.duration.into();
                    if let Some(q) = &c.requires {
                        r["requires"] = q.clone().into();
                    }
                }
                rows.push(r);
            }
        }
        Value::Array(rows)
    }

    /// Fuzzy resolve a name the way Reachy Mini resolves emotion names: exact id, then an
    /// id/title/tag match ignoring case and separators.
    pub fn resolve(&self, name: &str) -> Option<&ShowItem> {
        if let Some(x) = self.items.get(name) {
            return Some(x);
        }
        let n = norm(name);
        self.items
            .values()
            .find(|it| norm(&it.id) == n || it.title.as_deref().is_some_and(|t| norm(t) == n))
            .or_else(|| {
                self.items
                    .values()
                    .find(|it| it.tags.iter().any(|t| norm(t) == n))
            })
    }
}
