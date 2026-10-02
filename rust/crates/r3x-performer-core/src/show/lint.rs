//! The show linter (port of `lint.ts`): every file validates, every id resolves, nesting
//! stays within 3 levels, tiers never escalate through nesting, and every clip is
//! something the servos can physically perform:
//!
//! - joint limits: the channel's soft limits exactly as the pipeline computes them;
//! - velocity and acceleration: each track's peak against its channel's vMax/aMax
//!   (joint-side limits, i.e. after the gear ratio or rack pinion);
//! - extended joints need `"requires": "extended"`; coupled joints (claw fingers) must be
//!   driven through the channel's primary joint.
//!
//! Which limits: the caller's. The committed library is held to [`library_limits`] (the
//! range both rigs share, at the Physical build's speeds); Studio lints an edit against the
//! active profile ([`joint_limits_from_profile`]).

use super::catalog::{norm, Catalog};
use super::curve::track_peaks;
use super::expand::{children, owns_union, MAX_DEPTH};
use super::types::{is_extended_joint, Action, Body, Clip, Entry, Kind, ShowItem, TrackMode};
use crate::actuation::pipeline::{
    profile_doc, rig_joints, servo_map, Channel, JointSpec, ServoMap,
};
use crate::stagelights::{rig, rigs, LightMode, DEFAULT_RIG};
use r3x_contracts::profile::JointCoupling;
use r3x_contracts::RobotProfile;
use std::collections::{BTreeMap, HashMap, HashSet};
use std::sync::Arc;

/// EyePattern values CantinaOS accepts (eye_light_controller_service.py).
pub const EYE_PATTERNS: [&str; 13] = [
    "idle",
    "startup",
    "engaged",
    "listening",
    "thinking",
    "speaking",
    "flash",
    "happy",
    "sad",
    "angry",
    "surprised",
    "error",
    "custom",
];
/// The ones the face firmware can show (every other pattern renders as IDLE).
pub const RENDERABLE_EYES: [&str; 6] = [
    "idle",
    "engaged",
    "listening",
    "thinking",
    "speaking",
    "flash",
];

/// `^(S[IELTSF]|M\d{3}|B\d{3}|X[0-3]|H[0-9A-F]{3}|R)$`
pub fn is_chest_word(w: &str) -> bool {
    let b = w.as_bytes();
    let digits = |s: &[u8]| s.iter().all(u8::is_ascii_digit);
    match b {
        [b'S', c] => b"IELTSF".contains(c),
        [b'M' | b'B', rest @ ..] if rest.len() == 3 => digits(rest),
        [b'X', c] => (b'0'..=b'3').contains(c),
        [b'H', rest @ ..] if rest.len() == 3 => rest
            .iter()
            .all(|c| c.is_ascii_digit() || (b'A'..=b'F').contains(c)),
        [b'R'] => true,
        _ => false,
    }
}

/// Kit sfx stems (`sim/web/src/show/kit_sfx.json`), keyed by [`norm`].
pub fn sfx_stems() -> BTreeMap<String, String> {
    #[derive(serde::Deserialize)]
    struct Doc {
        stems: Vec<String>,
    }
    let d: Doc = serde_json::from_str(include_str!("../../../../../sim/web/src/show/kit_sfx.json"))
        .expect("kit_sfx.json parses");
    d.stems.into_iter().map(|s| (norm(&s), s)).collect()
}

#[derive(Clone, Debug, PartialEq)]
pub struct JointLimit {
    /// Positions outside `lo..hi` are errors: no rig the library plays on has them.
    pub lo: f64,
    pub hi: f64,
    /// Positions inside `lo..hi` but outside `warn_lo..warn_hi` are warnings: a rig that is
    /// narrower here (the Physical build) clamps them at playback. Equal to `lo..hi` for one rig.
    pub warn_lo: f64,
    pub warn_hi: f64,
    /// This joint's range as a function of another's on the narrower rig (its coupled limit):
    /// exceeding it is a warning, the performer clamps to it.
    pub coupling: Option<JointCoupling>,
    pub v_max: f64,
    pub a_max: f64,
    pub channel: String,
    pub primary: bool,
    pub base: bool,
}

/// Per-joint soft limits and motion limits, from the servo map + rig joint table.
pub fn joint_limits_for(
    map: &ServoMap,
    joints: &[JointSpec],
) -> Result<BTreeMap<String, JointLimit>, String> {
    let specs: HashMap<String, JointSpec> =
        joints.iter().map(|j| (j.name.clone(), j.clone())).collect();
    let base: HashSet<String> = map
        .resolve("r3x_animation")?
        .channels
        .iter()
        .flat_map(|c| c.joints.keys().cloned())
        .collect();
    let ext = map.resolve("extended")?;
    let mut out = BTreeMap::new();
    for cfg in &ext.channels {
        let ch = Channel::new(cfg.clone(), &specs, &BTreeMap::new(), ext.supply_volts)?;
        for j in cfg.joints.keys() {
            out.insert(
                j.clone(),
                JointLimit {
                    lo: ch.follower.soft_min,
                    hi: ch.follower.soft_max,
                    warn_lo: ch.follower.soft_min,
                    warn_hi: ch.follower.soft_max,
                    coupling: None,
                    v_max: cfg.v_max,
                    a_max: cfg.a_max,
                    channel: cfg.name.clone(),
                    primary: *j == ch.primary_joint,
                    base: base.contains(j),
                },
            );
        }
    }
    Ok(out)
}

/// Per-joint limits from a Robot Profile: its soft ranges and motion limits; `base` is
/// every joint not marked `extended`.
pub fn joint_limits_from_profile(p: &RobotProfile) -> Result<BTreeMap<String, JointLimit>, String> {
    let doc = profile_doc(p)?;
    let mut out = BTreeMap::new();
    for cfg in &doc.channels {
        for (i, j) in cfg.joints.keys().enumerate() {
            let joint = p.joint(j).ok_or_else(|| format!("unknown joint {j}"))?;
            out.insert(
                j.clone(),
                JointLimit {
                    lo: joint.soft.min,
                    hi: joint.soft.max,
                    warn_lo: joint.soft.min,
                    warn_hi: joint.soft.max,
                    coupling: p
                        .mech
                        .as_ref()
                        .and_then(|m| m.couplings.iter().find(|c| &c.joint == j).cloned()),
                    v_max: cfg.v_max,
                    a_max: cfg.a_max,
                    channel: cfg.name.clone(),
                    primary: i == 0,
                    base: !joint.extended,
                },
            );
        }
    }
    Ok(out)
}

/// The limits the committed animation library (`show/`) is authored and tested against:
/// the policy since the 2026-10-01 energy pass. Two rigs play the same files, so
///
/// - **positions**: an error past the Original rig's animation range (`lo..hi`), a warning past
///   the range both rigs share (`warn_lo..warn_hi`) or the Physical build's coupled limits
///   (Hunter's tilt by roll): the Physical rig's performer clamps those at playback (Brandon,
///   2026-10-02: clamp on the physical rig rather than edit the clips). Before 2026-10-02 the
///   shared range was the error bound; Hunter's cut-down horns took the Physical tilt to +13.
/// - **velocity and acceleration** are `physical`'s: the real build, whose limits rigsync
///   derives from the servo specs (`profiles/r3x/robot.generated.json`). The Original
///   profile's v_max are hand-set and conservative (the head tilt 100 deg/s vs the
///   servo's 225); a clip faster than them still plays safely there, because the Original
///   rig's servo follower rate-limits every channel to its own v_max.
/// - channel, primary and base come from `original`, which carries the full channel table.
///
/// Not [`joint_limits`] (the servo map, i.e. the Original rig) and not one profile's
/// [`joint_limits_from_profile`]: each of those alone would either hold the library to
/// speeds the hardware does not have, or let it use range the other rig does not have.
/// The runtime's Studio still lints an edit against the ACTIVE profile, which is right for
/// a bench preview.
pub fn library_limits(
    original: &RobotProfile,
    physical: &RobotProfile,
) -> Result<BTreeMap<String, JointLimit>, String> {
    let mut out = joint_limits_from_profile(original)?;
    let phys = joint_limits_from_profile(physical)?;
    for (j, lim) in out.iter_mut() {
        let p = phys
            .get(j)
            .ok_or_else(|| format!("{j}: not in the physical profile"))?;
        // both joints exist: joint_limits_from_profile resolved them above
        let (o, q) = (&original.joint(j).unwrap().animation, &physical.joint(j).unwrap().animation);
        lim.lo = o.min;
        lim.hi = o.max;
        lim.warn_lo = o.min.max(q.min);
        lim.warn_hi = o.max.min(q.max);
        lim.coupling = p.coupling.clone();
        lim.v_max = p.v_max;
        lim.a_max = p.a_max;
    }
    Ok(out)
}

/// [`joint_limits_for`] over the committed servo map and rig table.
pub fn joint_limits() -> BTreeMap<String, JointLimit> {
    joint_limits_for(&servo_map(), &rig_joints()).expect("committed servo map is consistent")
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct LintResult {
    pub errors: Vec<String>,
    pub warnings: Vec<String>,
}

/// A track's value at `t` (keys linear in between, held past the ends; the ease is ignored: the
/// keys are the extremes a track reaches).
pub fn track_at(tr: &super::types::Track, t: f64) -> f64 {
    let k = &tr.keys;
    match k.iter().position(|x| x[0] >= t) {
        None => k.last().map_or(0.0, |x| x[1]),
        Some(0) => k[0][1],
        Some(i) => {
            let (a, b) = (k[i - 1], k[i]);
            if b[0] == a[0] { b[1] } else { a[1] + (b[1] - a[1]) * (t - a[0]) / (b[0] - a[0]) }
        }
    }
}

/// [`lint_clip`] and its warnings: positions a narrower rig clamps (outside `warn_lo..warn_hi`,
/// or past a coupled limit at the depending joint's value at that key, 0 where the clip has
/// no track for it).
pub fn lint_clip_warn(
    c: &Clip,
    limits: &BTreeMap<String, JointLimit>,
    errors: &mut Vec<String>,
    warnings: &mut Vec<String>,
) {
    lint_clip(c, limits, errors);
    for (j, tr) in &c.tracks {
        let Some(lim) = limits.get(j) else { continue };
        for [t, v] in &tr.keys {
            if *v < lim.lo - 1e-9 || *v > lim.hi + 1e-9 {
                continue; // an error already
            }
            if *v < lim.warn_lo - 1e-9 || *v > lim.warn_hi + 1e-9 {
                warnings.push(format!(
                    "{}.{j}: {v} at t={t} outside the Physical rig's {:.1}..{:.1} (clamped there)",
                    c.id, lim.warn_lo, lim.warn_hi
                ));
                continue;
            }
            if let Some(cp) = &lim.coupling {
                let a = c.tracks.get(&cp.depends_on).map_or(0.0, |d| track_at(d, *t));
                if let Some((lo, hi)) = cp.range_at(a) {
                    if *v < lo - 1e-9 || *v > hi + 1e-9 {
                        warnings.push(format!(
                            "{}.{j}: {v} at t={t} past the Physical rig's coupled limit {lo:.1}..{hi:.1} at {} {a:+.1} (clamped there)",
                            c.id, cp.depends_on
                        ));
                    }
                }
            }
        }
    }
}

pub fn lint_catalog(cat: &Catalog, limits: &BTreeMap<String, JointLimit>) -> LintResult {
    let mut errors = cat.errors.clone();
    let mut warnings = Vec::new();

    for it in cat.items.values() {
        if it.description.chars().count() > 120 {
            warnings.push(format!(
                "{}: description over 120 chars (it goes in an LLM catalogue)",
                it.id
            ));
        }
        match &it.body {
            Body::Clip(c) => lint_clip_warn(c, limits, &mut errors, &mut warnings),
            _ => lint_refs(it, cat, &mut errors, &mut warnings),
        }
    }

    // Nesting: depth <= 3, no cycles; tiers never escalate through nesting; owns covers overrides.
    for it in cat.items.values() {
        if it.kind() == Kind::Clip {
            continue;
        }
        walk(it, it, 1, &mut vec![it.id.clone()], cat, &mut errors);
        if let Some(union) = owns_union(it, cat) {
            let owned: HashSet<&String> = union.iter().collect();
            for clip in reachable_clips(it, cat, 1) {
                for (j, tr) in &clip.tracks {
                    if tr.mode == TrackMode::Override && !owned.contains(j) {
                        errors.push(format!("{}: clip {} overrides {j}, which the sequence does not own (it would be masked)", it.id, clip.id));
                    }
                }
            }
        }
    }

    match &cat.idle {
        None => errors.push("show/idle.json missing or invalid".into()),
        Some(idle) => {
            for c in idle.choices.iter().chain(idle.while_music.iter().flatten()) {
                match cat.get(&c.id) {
                    None => errors.push(format!("idle.json: unknown id \"{}\"", c.id)),
                    Some(x) if x.tier != super::types::Tier::Free => errors.push(format!(
                        "idle.json: {} is {}; the idle source may only trigger free items",
                        c.id,
                        x.tier.as_str()
                    )),
                    _ => {}
                }
            }
        }
    }
    LintResult { errors, warnings }
}

fn walk(
    root: &ShowItem,
    x: &ShowItem,
    depth: usize,
    path: &mut Vec<String>,
    cat: &Catalog,
    errors: &mut Vec<String>,
) {
    if depth > MAX_DEPTH {
        return errors.push(format!(
            "{}: nesting deeper than {MAX_DEPTH} ({})",
            root.id,
            path.join(" > ")
        ));
    }
    for (kind, id) in children(x) {
        let Some(c) = cat.get(id).filter(|c| c.kind() == kind) else {
            continue;
        }; // reported by lint_refs
        if path.iter().any(|p| p == id) {
            let mut p = path.clone();
            p.push(id.into());
            return errors.push(format!("{}: cycle {}", root.id, p.join(" > ")));
        }
        if std::ptr::eq(x, root) && c.tier > root.tier {
            errors.push(format!(
                "{} ({}) plays {} {id} ({}): a lower tier would let {} content through",
                root.id,
                root.tier.as_str(),
                c.kind().as_str(),
                c.tier.as_str(),
                c.tier.as_str()
            ));
        }
        if kind != Kind::Clip {
            path.push(id.into());
            walk(root, c, depth + 1, path, cat, errors);
            path.pop();
        }
    }
}

fn fmt_num(x: f64) -> String {
    if x.is_infinite() {
        "inf".into()
    } else {
        format!("{x:.0}")
    }
}

/// One clip against the limits: joints, extended/coupled rules, key range, peak v/a.
pub fn lint_clip(c: &Clip, limits: &BTreeMap<String, JointLimit>, errors: &mut Vec<String>) {
    for (j, tr) in &c.tracks {
        let w = format!("{}.{j}", c.id);
        let Some(lim) = limits.get(j) else {
            errors.push(format!("{w}: no such joint"));
            continue;
        };
        if (is_extended_joint(j) || !lim.base) && c.requires.as_deref() != Some("extended") {
            errors.push(format!(
                "{w}: extended joint; the clip needs \"requires\": \"extended\""
            ));
        }
        if !lim.primary {
            errors.push(format!(
                "{w}: coupled joint; drive channel {} through its primary joint",
                lim.channel
            ));
        }
        for [t, v] in &tr.keys {
            if *v < lim.lo - 1e-9 || *v > lim.hi + 1e-9 {
                errors.push(format!(
                    "{w}: {v} at t={t} outside {:.1}..{:.1}",
                    lim.lo, lim.hi
                ));
            }
        }
        let pk = track_peaks(tr);
        if pk.v > lim.v_max + 1e-6 {
            errors.push(format!(
                "{w}: peak velocity {} > vMax {} (segment at t={})",
                fmt_num(pk.v),
                lim.v_max,
                pk.v_at
            ));
        }
        if pk.a > lim.a_max + 1e-6 {
            errors.push(format!(
                "{w}: peak acceleration {} > aMax {} (segment at t={})",
                fmt_num(pk.a),
                lim.a_max,
                pk.a_at
            ));
        }
    }
}

fn lint_refs(it: &ShowItem, cat: &Catalog, errors: &mut Vec<String>, warnings: &mut Vec<String>) {
    for (kind, id) in children(it) {
        match cat.get(id) {
            None => errors.push(format!("{}: unknown {} \"{id}\"", it.id, kind.as_str())),
            Some(c) if c.kind() != kind => errors.push(format!(
                "{}: \"{id}\" is a {}, not a {}",
                it.id,
                c.kind().as_str(),
                kind.as_str()
            )),
            _ => {}
        }
    }
    let entries = match &it.body {
        Body::Cue(c) => &c.actions,
        Body::Sequence(s) => &s.track,
        Body::Clip(_) => return,
    };
    let sfx = sfx_stems();
    let default_rig = rig(DEFAULT_RIG).expect("default rig");
    for e in entries {
        let Entry::Act(a) = &e.entry else { continue };
        let w = format!("{}: {}", it.id, a.name());
        match a {
            Action::Sfx { id } if !sfx.contains_key(&norm(id)) => errors.push(format!(
                "{w} \"{id}\" matches no kit sound ({})",
                sfx.values().cloned().collect::<Vec<_>>().join(", ")
            )),
            Action::Eyes { pattern, .. } => {
                if !EYE_PATTERNS.contains(&pattern.as_str()) {
                    errors.push(format!(
                        "{w} pattern \"{pattern}\" is not an EyePattern ({})",
                        EYE_PATTERNS.join(", ")
                    ));
                } else if !RENDERABLE_EYES.contains(&pattern.as_str()) {
                    warnings.push(format!(
                        "{w} \"{pattern}\" renders as IDLE on the current firmware"
                    ));
                }
            }
            Action::Chest { command, .. } if !is_chest_word(command) => {
                errors.push(format!("{w} \"{command}\" is not a chest serial word"))
            }
            Action::Lights {
                cue,
                mode,
                rig: rig_name,
                ..
            } => {
                if let Some(cue) = cue {
                    if !rigs().iter().any(|r| r.cue(cue).is_some()) {
                        errors.push(format!("{w} cue \"{cue}\" exists in no rig"));
                    } else if default_rig.cue(cue).is_none() {
                        warnings.push(format!("{w} cue \"{cue}\" is not in the default rig"));
                    }
                }
                if let Some(m) = mode.as_deref().filter(|m| LightMode::parse(m).is_none()) {
                    errors.push(format!("{w} mode \"{m}\" (idle, music, dj, speaking, off)"));
                }
                if let Some(r) = rig_name.as_deref().filter(|r| rig(r).is_none()) {
                    let names: Vec<_> = rigs().iter().map(|r| r.name).collect();
                    errors.push(format!("{w} rig \"{r}\" ({})", names.join(", ")));
                }
            }
            _ => {}
        }
    }
}

fn reachable_clips<'a>(it: &'a ShowItem, cat: &'a Catalog, depth: usize) -> Vec<&'a Arc<Clip>> {
    if depth > MAX_DEPTH {
        return vec![];
    }
    let mut out = Vec::new();
    for (_, id) in children(it) {
        let Some(c) = cat.get(id) else { continue };
        match &c.body {
            Body::Clip(clip) => out.push(clip),
            _ => out.extend(reachable_clips(c, cat, depth + 1)),
        }
    }
    out
}
