//! Expansion (SPEC "Cross-language parity", port of `expand.ts`): a cue or sequence
//! flattened to a time-sorted list of department actions. Loops are not unrolled and waits
//! resolve as 0 s.

use super::catalog::Catalog;
use super::types::{Action, Body, Clock, Entry, Expanded, Kind, Sequence, ShowItem};

pub const MAX_DEPTH: usize = 3;
pub const DEFAULT_BPM: f64 = 120.0;

/// `Math.round(t * 1000) / 1000`.
pub(crate) fn round3(t: f64) -> f64 {
    js_round(t * 1000.0) / 1000.0
}

/// JS `Math.round`: halves round up.
pub(crate) fn js_round(x: f64) -> f64 {
    (x + 0.5).floor()
}

/// Fill the parity defaults: clip intensity/speed, lights fade/hold, chest hold. `wait` -> None.
pub fn normalize_action(a: &Action) -> Option<Action> {
    let mut a = a.clone();
    match &mut a {
        Action::Wait { .. } => return None,
        Action::Clip {
            intensity, speed, ..
        } => {
            intensity.get_or_insert(1.0);
            speed.get_or_insert(1.0);
        }
        Action::Lights { fade, hold, .. } => {
            fade.get_or_insert(0.0);
            hold.get_or_insert(0.0);
        }
        Action::Chest { hold, .. } => {
            hold.get_or_insert(0.0);
        }
        _ => {}
    }
    Some(a)
}

/// Seconds per unit of a sequence's clock at the given tempo.
pub fn seconds_per_unit(seq: &Sequence, bpm: Option<f64>) -> f64 {
    match seq.clock {
        Clock::Beat => 60.0 / bpm.or(seq.bpm).unwrap_or(DEFAULT_BPM),
        Clock::Time => 1.0,
    }
}

pub fn expand(root_id: &str, cat: &Catalog, bpm: Option<f64>) -> Result<Vec<Expanded>, String> {
    let root = cat
        .get(root_id)
        .ok_or_else(|| format!("unknown show item \"{root_id}\""))?;
    let mut out = Vec::new();
    walk(root, 0.0, 1, cat, bpm, &mut out)?;
    // Stable sort: ties keep file (walk) order.
    out.sort_by(|x: &Expanded, y| x.t.total_cmp(&y.t));
    Ok(out)
}

fn walk(
    item: &ShowItem,
    offset: f64,
    depth: usize,
    cat: &Catalog,
    bpm: Option<f64>,
    out: &mut Vec<Expanded>,
) -> Result<(), String> {
    if depth > MAX_DEPTH {
        return Err(format!("{}: nesting deeper than {MAX_DEPTH}", item.id));
    }
    fn emit(out: &mut Vec<Expanded>, t: f64, a: &Action) {
        if let Some(action) = normalize_action(a) {
            out.push(Expanded {
                t: round3(t),
                action,
            });
        }
    }
    match &item.body {
        Body::Clip(_) => emit(
            out,
            offset,
            &Action::Clip {
                id: item.id.clone(),
                intensity: None,
                speed: None,
            },
        ),
        Body::Cue(c) => {
            for a in &c.actions {
                if let Entry::Act(act) = &a.entry {
                    emit(out, offset + a.at, act);
                }
            }
        }
        Body::Sequence(s) => {
            let k = seconds_per_unit(s, bpm);
            for it in &s.track {
                let t = offset + it.at * k;
                match &it.entry {
                    Entry::Act(a) => emit(out, t, a),
                    Entry::Ref(kind, id) => {
                        let child = cat.get(id).filter(|c| c.kind() == *kind).ok_or_else(|| {
                            format!("{}: unknown {} \"{id}\"", item.id, kind.as_str())
                        })?;
                        walk(child, t, depth + 1, cat, bpm, out)?;
                    }
                }
            }
        }
    }
    Ok(())
}

/// Direct references of a cue or sequence, in order.
pub fn children(it: &ShowItem) -> Vec<(Kind, &str)> {
    let entries = match &it.body {
        Body::Cue(c) => &c.actions,
        Body::Sequence(s) => &s.track,
        Body::Clip(_) => return vec![],
    };
    entries
        .iter()
        .filter_map(|e| match &e.entry {
            Entry::Act(Action::Clip { id, .. }) => Some((Kind::Clip, id.as_str())),
            Entry::Ref(k, id) if matches!(it.body, Body::Sequence(_)) => Some((*k, id.as_str())),
            _ => None,
        })
        .collect()
}

/// The joints a run of this sequence owns: its own `owns` plus nested ones (as the player does).
pub fn owns_union(it: &ShowItem, cat: &Catalog) -> Option<Vec<String>> {
    owns_at(it, cat, 1)
}

fn owns_at(it: &ShowItem, cat: &Catalog, depth: usize) -> Option<Vec<String>> {
    let seq = it.sequence()?;
    if depth > MAX_DEPTH {
        return None;
    }
    let mut set: Vec<String> = Vec::new();
    let mut add = |j: &String| {
        if !set.contains(j) {
            set.push(j.clone());
        }
    };
    seq.owns.iter().flatten().for_each(&mut add);
    let mut any = seq.owns.is_some();
    for (kind, id) in children(it) {
        let c = if kind == Kind::Sequence {
            cat.get(id).filter(|c| c.kind() == Kind::Sequence)
        } else {
            None
        };
        if let Some(o) = c.and_then(|c| owns_at(c, cat, depth + 1)) {
            any = true;
            o.iter().for_each(&mut add);
        }
    }
    any.then_some(set)
}
