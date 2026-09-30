//! R3X Show Format v1 (`show/SPEC.md`) as Rust types. Port of `sim/web/src/show/types.ts`.
//!
//! Documents are validated structurally as JSON first (`validate.rs`, which reports every
//! problem with its path); only valid documents become typed items.

use indexmap::IndexMap;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::sync::Arc;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Kind {
    Clip,
    Cue,
    Sequence,
}

impl Kind {
    pub fn as_str(self) -> &'static str {
        match self {
            Kind::Clip => "clip",
            Kind::Cue => "cue",
            Kind::Sequence => "sequence",
        }
    }
}

/// Ordered: `free < cheap < show`.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Tier {
    Free,
    Cheap,
    Show,
}

impl Tier {
    pub fn as_str(self) -> &'static str {
        match self {
            Tier::Free => "free",
            Tier::Cheap => "cheap",
            Tier::Show => "show",
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Default, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Ease {
    #[default]
    Minjerk,
    Linear,
    Step,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum TrackMode {
    Additive,
    Override,
}

/// Who asked for a performance (SPEC "Tiers").
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Source {
    Jev,
    Claude,
    Timeline,
    Idle,
    Ui,
    Cli,
}

impl Source {
    pub fn parse(s: &str) -> Option<Source> {
        serde_json::from_value(Value::String(s.into())).ok()
    }
}

/// The nine joints the base build drives: the 8-servo `r3x_animation` mechanics plus the
/// head roll of Hunter's head mech.
pub const BASE_JOINTS: [&str; 9] = [
    "head_pan",
    "head_tilt",
    "head_roll",
    "head_lift",
    "visor",
    "hero_shoulder",
    "hero_wrist",
    "torso_lower",
    "torso_top",
];

/// Joints that need `"requires": "extended"`.
pub fn is_extended_joint(j: &str) -> bool {
    j == "torso_middle"
        || ["hero_claw_", "throttle_", "poker_"]
            .iter()
            .any(|p| j.len() > p.len() && j.starts_with(p))
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Track {
    pub mode: TrackMode,
    /// `[t_seconds, value]`; the first key is at t=0.
    pub keys: Vec<[f64; 2]>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub ease: Option<Ease>,
    /// Override blend in/out (s); default by joint class, see [`default_blend`].
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub blend: Option<f64>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Clip {
    pub id: String,
    pub duration: f64,
    #[serde(default)]
    pub interruptible_after: Option<f64>,
    #[serde(default)]
    pub requires: Option<String>,
    pub tracks: IndexMap<String, Track>,
}

/// A department action (SPEC "Cue", the `do` table).
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(tag = "do", rename_all = "lowercase")]
pub enum Action {
    Clip {
        id: String,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        intensity: Option<f64>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        speed: Option<f64>,
    },
    Eyes {
        pattern: String,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        color: Option<String>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        intensity: Option<f64>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        duration: Option<f64>,
    },
    Chest {
        command: String,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        hold: Option<f64>,
    },
    Lights {
        #[serde(default, skip_serializing_if = "Option::is_none")]
        cue: Option<String>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        mode: Option<String>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        fade: Option<f64>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        hold: Option<f64>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        rig: Option<String>,
    },
    Sfx {
        id: String,
    },
    Speak {
        text: String,
    },
    Duck,
    Unduck,
    Wait {
        #[serde(rename = "for")]
        until: String,
    },
}

impl Action {
    pub fn name(&self) -> &'static str {
        match self {
            Action::Clip { .. } => "clip",
            Action::Eyes { .. } => "eyes",
            Action::Chest { .. } => "chest",
            Action::Lights { .. } => "lights",
            Action::Sfx { .. } => "sfx",
            Action::Speak { .. } => "speak",
            Action::Duck => "duck",
            Action::Unduck => "unduck",
            Action::Wait { .. } => "wait",
        }
    }
}

pub const ACTIONS: [&str; 9] = [
    "clip", "eyes", "chest", "lights", "sfx", "speak", "duck", "unduck", "wait",
];

/// A timed entry of a cue or sequence: a department action, or a nested cue/sequence.
#[derive(Clone, Debug, PartialEq)]
pub enum Entry {
    Ref(Kind, String),
    Act(Action),
}

#[derive(Clone, Debug, PartialEq)]
pub struct TimedEntry {
    pub at: f64,
    pub entry: Entry,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Cue {
    pub actions: Vec<TimedEntry>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Default, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Clock {
    #[default]
    Time,
    Beat,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum SeqLayer {
    Gesture,
    Show,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Sequence {
    pub clock: Clock,
    pub bpm: Option<f64>,
    pub layer: Option<SeqLayer>,
    pub owns: Option<Vec<String>>,
    pub looped: bool,
    pub length: Option<f64>,
    pub track: Vec<TimedEntry>,
}

#[derive(Clone, Debug, PartialEq)]
pub enum Body {
    Clip(Arc<Clip>),
    Cue(Cue),
    Sequence(Sequence),
}

#[derive(Clone, Debug, PartialEq)]
pub struct ShowItem {
    pub id: String,
    pub title: Option<String>,
    /// One line: what Claude and Jev see in their catalogue.
    pub description: String,
    pub tags: Vec<String>,
    pub tier: Tier,
    pub body: Body,
}

impl ShowItem {
    pub fn kind(&self) -> Kind {
        match self.body {
            Body::Clip(_) => Kind::Clip,
            Body::Cue(_) => Kind::Cue,
            Body::Sequence(_) => Kind::Sequence,
        }
    }
    pub fn clip(&self) -> Option<&Arc<Clip>> {
        match &self.body {
            Body::Clip(c) => Some(c),
            _ => None,
        }
    }
    pub fn sequence(&self) -> Option<&Sequence> {
        match &self.body {
            Body::Sequence(s) => Some(s),
            _ => None,
        }
    }

    /// Build from a document that already passed [`crate::show::validate::validate_item`].
    pub fn from_value(x: &Value) -> Result<ShowItem, String> {
        let s = |k: &str| x.get(k).and_then(Value::as_str).map(str::to_owned);
        let id = s("id").ok_or("no id")?;
        let tier: Tier = de(x.get("tier").cloned().unwrap_or(Value::Null))?;
        let body = match x.get("kind").and_then(Value::as_str) {
            Some("clip") => Body::Clip(Arc::new(de(x.clone())?)),
            Some("cue") => Body::Cue(Cue {
                actions: x["actions"]
                    .as_array()
                    .ok_or("no actions")?
                    .iter()
                    .map(entry)
                    .collect::<Result<_, _>>()?,
            }),
            Some("sequence") => Body::Sequence(Sequence {
                clock: x
                    .get("clock")
                    .map(|v| de(v.clone()))
                    .transpose()?
                    .unwrap_or_default(),
                bpm: x.get("bpm").and_then(Value::as_f64),
                layer: x.get("layer").map(|v| de(v.clone())).transpose()?,
                owns: x.get("owns").map(|v| de(v.clone())).transpose()?,
                looped: x.get("loop").and_then(Value::as_bool).unwrap_or(false),
                length: x.get("length").and_then(Value::as_f64),
                track: x["track"]
                    .as_array()
                    .ok_or("no track")?
                    .iter()
                    .map(entry)
                    .collect::<Result<_, _>>()?,
            }),
            _ => return Err("bad kind".into()),
        };
        Ok(ShowItem {
            id,
            title: s("title"),
            description: s("description").unwrap_or_default(),
            tags: x
                .get("tags")
                .map(|v| de(v.clone()))
                .transpose()?
                .unwrap_or_default(),
            tier,
            body,
        })
    }
}

fn de<T: serde::de::DeserializeOwned>(v: Value) -> Result<T, String> {
    serde_json::from_value(v).map_err(|e| e.to_string())
}

/// A cue action or sequence track item. `do` first: a lights action also has a `cue` field.
fn entry(it: &Value) -> Result<TimedEntry, String> {
    let at = it.get("at").and_then(Value::as_f64).ok_or("no at")?;
    let id = |k: &str| it[k].as_str().unwrap_or_default().to_owned();
    let entry = if it.get("do").is_some() {
        Entry::Act(serde_json::from_value(it.clone()).map_err(|e| e.to_string())?)
    } else if it.get("cue").is_some() {
        Entry::Ref(Kind::Cue, id("cue"))
    } else if it.get("sequence").is_some() {
        Entry::Ref(Kind::Sequence, id("sequence"))
    } else if it.get("clip").is_some() {
        Entry::Act(Action::Clip {
            id: id("clip"),
            intensity: it.get("intensity").and_then(Value::as_f64),
            speed: it.get("speed").and_then(Value::as_f64),
        })
    } else {
        return Err(format!(
            "track item at {at} has no cue | clip | sequence | do"
        ));
    };
    Ok(TimedEntry { at, entry })
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct WeightedId {
    pub id: String,
    pub weight: f64,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct IdlePolicy {
    pub after_s: f64,
    pub choices: Vec<WeightedId>,
    #[serde(default)]
    pub while_music: Option<Vec<WeightedId>>,
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct Params {
    #[serde(default)]
    pub intensity: Option<f64>,
    #[serde(default)]
    pub speed: Option<f64>,
}

/// One entry of an expansion: the flat, time-sorted parity format.
#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct Expanded {
    pub t: f64,
    #[serde(flatten)]
    pub action: Action,
}

/// Default override blend by joint class (SPEC Clip "Keys", after Disney's BD-X engine:
/// light "show function" parts blend faster than the body).
pub fn default_blend(joint: &str) -> f64 {
    if joint == "visor" {
        0.1
    } else if joint.starts_with("head_") {
        0.2
    } else {
        0.35
    }
}

pub fn clamp_intensity(x: f64) -> f64 {
    x.clamp(0.0, 1.5)
}
pub fn clamp_speed(x: f64) -> f64 {
    x.clamp(0.5, 2.0)
}

/// Who may trigger what (SPEC "Tiers").
pub fn tier_allows(tier: Tier, source: Source) -> bool {
    match tier {
        Tier::Free => true,
        Tier::Cheap => source != Source::Idle,
        Tier::Show => matches!(
            source,
            Source::Claude | Source::Timeline | Source::Ui | Source::Cli
        ),
    }
}
