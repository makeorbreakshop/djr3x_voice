//! The compact show catalogue Claude sees (port of `cantina_os/show/catalog.py`).
//!
//! Built once at start-up and appended to the *cached* system prompt, so it is byte-stable:
//! sorted by id, nothing time-varying. Tags may name `free`/`cheap` clips and cues; the
//! `perform_show` tool may name `cheap`/`show` sequences.

use std::collections::HashSet;
use std::path::Path;
use std::sync::Arc;

use r3x_performer_core::show::catalog::Catalog;
use r3x_performer_core::show::intentions::IntentKind;
use r3x_performer_core::show::types::{Kind, ShowItem, Tier};

const TAG_GUIDANCE: &str = "You have a body and lights. You can punctuate what you say with an inline tag written
immediately before the word it goes with. Say what you FEEL or DO and your body finds a
fresh way to show it: {mood:<id>} a feeling, {gesture:<id>} a gesture, {beat:<id>} a DJ
moment, {look:<id>} a glance. For a whole staged moment (motion, eyes, chest, lights,
sound) use {cue:<id>}. Tags are silent - they are removed before your words are spoken -
and each fires as your voice reaches it. Use them sparingly: none is often right, never
more than three in a reply, and only when the move adds something the words do not. Only
use ids from the lists below.
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
        if let Ok(text) = std::fs::read_to_string(dir.join("intentions.json")) {
            files.push(("show/intentions.json".to_string(), text));
        }
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
        // Intentions Claude may speak (not the `listen` backchannel), by kind, sorted.
        let intents: Vec<&r3x_performer_core::show::intentions::Intention> = {
            let mut v: Vec<_> = cat.intentions.values().filter(|i| i.kind != IntentKind::Listen).collect();
            v.sort_by(|a, b| (a.kind.as_str(), &a.id).cmp(&(b.kind.as_str(), &b.id)));
            v
        };
        if clips.is_empty() && cues.is_empty() && seqs.is_empty() && intents.is_empty() {
            return Self { kinds, ..Default::default() };
        }
        let line = |i: &ShowItem| {
            let d = if i.description.is_empty() { i.title.clone().unwrap_or_default() } else { i.description.clone() };
            let d = d.split_whitespace().collect::<Vec<_>>().join(" ");
            if d.is_empty() { format!("- {}", i.id) } else { format!("- {}: {d}", i.id) }
        };
        let mut guidance = String::new();
        if !clips.is_empty() || !cues.is_empty() || !intents.is_empty() {
            let mut ex = Vec::new();
            let first = |k: IntentKind| intents.iter().find(|i| i.kind == k);
            if let Some(i) = first(IntentKind::Mood) {
                ex.push(format!("{{mood:{}}} Ha, that is a new one!", i.id));
            }
            if let Some(i) = first(IntentKind::Gesture) {
                ex.push(format!("{{gesture:{}}} You got it, friend.", i.id));
            } else if let Some(c) = clips.first() {
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
        for kind in [IntentKind::Mood, IntentKind::Gesture, IntentKind::Beat, IntentKind::Look] {
            let of: Vec<String> = intents.iter().filter(|i| i.kind == kind).map(|i| format!("- {}: {}", i.id, i.description)).collect();
            if !of.is_empty() {
                let tag = kind.as_str();
                parts.push(format!("<{tag}s>  ({{{tag}:<id>}})\n{}\n</{tag}s>", of.join("\n")));
            }
        }
        if !cues.is_empty() {
            parts.push(block("<cues>", &cues) + "</cues>");
        }
        // Exact clips stay taggable ({clip:<id>}) but are no longer listed: intentions say it
        // better and keep the prompt short (Threepio, 2026-09-30).
        if !seqs.is_empty() {
            parts.push(block("<routines>  (perform_show tool only)", &seqs) + "</routines>");
        }
        let taggable = clips
            .iter()
            .map(|i| ("clip".to_string(), i.id.clone()))
            .chain(cues.iter().map(|i| ("cue".to_string(), i.id.clone())))
            .chain(intents.iter().map(|i| (i.kind.as_str().to_string(), i.id.clone())));
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
        // Intentions: offered by kind, taggable, the listening backchannel never offered.
        assert!(c.taggable.contains(&("mood".into(), "amused".into())));
        assert!(c.taggable.contains(&("gesture".into(), "nod".into())));
        assert!(c.taggable.contains(&("beat".into(), "drop".into())));
        assert!(!c.taggable.iter().any(|(_, id)| id.starts_with("listen_")));
        assert!(c.prompt_block.contains("<moods>  ({mood:<id>})\n- amused:"));
        assert!(!c.prompt_block.contains("<clips>"), "clips are not listed any more");
        assert_eq!(c.kinds.get("dj_intro"), Some(&Kind::Sequence));
        assert!(ShowCatalog::load(Path::new("/nonexistent")).is_empty());
    }
}
