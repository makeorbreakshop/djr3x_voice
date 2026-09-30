//! The compact show catalogue Claude sees (port of `cantina_os/show/catalog.py`).
//!
//! Built once at start-up and appended to the *cached* system prompt, so it is byte-stable:
//! sorted by id, nothing time-varying. Tags may name `free`/`cheap` clips and cues; the
//! `perform_show` tool may name `cheap`/`show` sequences.

use std::collections::HashSet;
use std::path::Path;
use std::sync::Arc;

use r3x_performer_core::show::catalog::Catalog;
use r3x_performer_core::show::types::{Kind, ShowItem, Tier};

const TAG_GUIDANCE: &str = "You have a body and lights. You can punctuate what you say with an inline tag written
immediately before the word it goes with: {cue:<id>} for a whole moment (motion, eyes,
chest, lights, sound) or {clip:<id>} for a single gesture. Tags are silent - they are
removed before your words are spoken - and each fires as your voice reaches it.
Use them sparingly: none is often right, never more than two in a reply, and only when
the move adds something the words do not. Only use ids from the lists below.
";
const TOOL_GUIDANCE: &str = "For a full routine (\"do your intro\", \"malfunction!\"), call the perform_show tool with a
routine id from <routines>; keep talking as normal, the routine runs alongside you.
";

#[derive(Debug, Clone, Default)]
pub struct ShowCatalog {
    pub taggable: Arc<HashSet<(String, String)>>,
    /// Sorted routine ids for `perform_show`.
    pub tool_ids: Vec<String>,
    pub prompt_block: String,
    /// Every loaded item id -> kind, for plan `perform`/`sequence` steps.
    pub kinds: std::collections::HashMap<String, Kind>,
}

impl ShowCatalog {
    pub fn is_empty(&self) -> bool {
        self.taggable.is_empty() && self.tool_ids.is_empty()
    }

    /// `SHOW_DIR` layout: `clips/`, `cues/`, `sequences/`, `idle.json`. A missing folder is an
    /// empty catalogue, never an error.
    pub fn load(dir: &Path) -> Self {
        let mut files = Vec::new();
        for sub in ["clips", "cues", "sequences"] {
            let Ok(rd) = std::fs::read_dir(dir.join(sub)) else { continue };
            for e in rd.flatten() {
                let p = e.path();
                if p.extension().is_some_and(|x| x == "json") {
                    if let Ok(text) = std::fs::read_to_string(&p) {
                        files.push((format!("show/{sub}/{}", e.file_name().to_string_lossy()), text));
                    }
                }
            }
        }
        let cat = Catalog::from_files(files.iter().map(|(p, t)| (p.as_str(), t.as_str())));
        for e in &cat.errors {
            tracing::warn!("show catalogue: {e}");
        }
        Self::build(&cat)
    }

    pub fn build(cat: &Catalog) -> Self {
        let pick = |kind: Kind, tiers: &[Tier]| -> Vec<&ShowItem> {
            cat.list(kind).into_iter().filter(|i| tiers.contains(&i.tier)).collect()
        };
        let clips = pick(Kind::Clip, &[Tier::Free, Tier::Cheap]);
        let cues = pick(Kind::Cue, &[Tier::Free, Tier::Cheap]);
        let seqs = pick(Kind::Sequence, &[Tier::Cheap, Tier::Show]);
        let kinds = cat.items.values().map(|i| (i.id.clone(), i.kind())).collect();
        if clips.is_empty() && cues.is_empty() && seqs.is_empty() {
            return Self { kinds, ..Default::default() };
        }
        let line = |i: &ShowItem| {
            let d = if i.description.is_empty() { i.title.clone().unwrap_or_default() } else { i.description.clone() };
            let d = d.split_whitespace().collect::<Vec<_>>().join(" ");
            if d.is_empty() { format!("- {}", i.id) } else { format!("- {}: {d}", i.id) }
        };
        let mut guidance = String::new();
        if !clips.is_empty() || !cues.is_empty() {
            let mut ex = Vec::new();
            if let Some(c) = clips.first() {
                ex.push(format!("{{clip:{}}} You got it, friend.", c.id));
            }
            if let Some(c) = cues.first() {
                ex.push(format!("Now {{cue:{}}} let's MOVE!", c.id));
            }
            guidance += TAG_GUIDANCE;
            guidance += &format!("Example: \"{}\"\n", ex.join(" "));
        }
        if !seqs.is_empty() {
            guidance += TOOL_GUIDANCE;
        }
        let mut parts = vec![format!("<performance>\n{guidance}</performance>")];
        let block = |tag: &str, items: &[&ShowItem]| format!("{tag}\n{}\n", items.iter().map(|i| line(i)).collect::<Vec<_>>().join("\n"));
        if !cues.is_empty() {
            parts.push(block("<cues>", &cues) + "</cues>");
        }
        if !clips.is_empty() {
            parts.push(block("<clips>", &clips) + "</clips>");
        }
        if !seqs.is_empty() {
            parts.push(block("<routines>  (perform_show tool only)", &seqs) + "</routines>");
        }
        let taggable = clips.iter().map(|i| ("clip".to_string(), i.id.clone())).chain(cues.iter().map(|i| ("cue".to_string(), i.id.clone())));
        Self {
            taggable: Arc::new(taggable.collect()),
            tool_ids: seqs.iter().map(|i| i.id.clone()).collect(),
            prompt_block: parts.join("\n"),
            kinds,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn repo_show_folder() {
        let c = ShowCatalog::load(&Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../show"));
        assert!(c.taggable.contains(&("clip".into(), "beat_bop".into())));
        assert!(c.taggable.contains(&("cue".into(), "excited".into())));
        assert!(c.tool_ids.contains(&"crowd_hype".to_string()));
        assert!(c.prompt_block.starts_with("<performance>\nYou have a body"));
        assert_eq!(c.kinds.get("dj_intro"), Some(&Kind::Sequence));
        assert!(ShowCatalog::load(Path::new("/nonexistent")).is_empty());
    }
}
