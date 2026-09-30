//! Structural validation of show files (port of `validate.ts`). Returns human-readable
//! errors; an empty list means the document is well formed. Cross-file checks (ids resolve,
//! rig limits, nesting depth) live in `lint.rs`.

use super::types::ACTIONS;
use serde_json::{Map, Value};

type Obj = Map<String, Value>;

fn num(x: Option<&Value>) -> Option<f64> {
    x.and_then(Value::as_f64).filter(|v| v.is_finite())
}
fn is_str(x: Option<&Value>) -> bool {
    matches!(x, Some(Value::String(s)) if !s.is_empty())
}
fn valid_id(s: &str) -> bool {
    let mut c = s.chars();
    matches!(c.next(), Some('a'..='z')) && c.all(|c| matches!(c, 'a'..='z' | '0'..='9' | '_'))
}
fn in_range(x: Option<&Value>, lo: f64, hi: f64) -> bool {
    num(x).is_some_and(|v| v >= lo && v <= hi)
}

fn action_fields(d: &str) -> &'static [&'static str] {
    match d {
        "clip" => &["id", "intensity", "speed"],
        "eyes" => &["pattern", "color", "intensity", "duration"],
        "chest" => &["command", "hold"],
        "lights" => &["cue", "mode", "fade", "hold", "rig"],
        "sfx" => &["id"],
        "speak" => &["text"],
        "wait" => &["for"],
        _ => &[],
    }
}

fn check_params(o: &Obj, w: &str, err: &mut Vec<String>) {
    if o.contains_key("intensity") && !in_range(o.get("intensity"), 0.0, 1.5) {
        err.push(format!("{w}: intensity must be 0..1.5"));
    }
    if o.contains_key("speed") && !in_range(o.get("speed"), 0.5, 2.0) {
        err.push(format!("{w}: speed must be 0.5..2"));
    }
}

/// A department action (`{do: ...}`), with or without `at`.
pub fn validate_action(a: &Value, w: &str, err: &mut Vec<String>) {
    let Some(a) = a.as_object() else {
        return err.push(format!("{w}: not an object"));
    };
    let d = match a.get("do") {
        Some(Value::String(d)) if ACTIONS.contains(&d.as_str()) => d.as_str(),
        other => {
            let shown = match other {
                Some(Value::String(s)) => s.clone(),
                Some(v) => v.to_string(),
                None => "undefined".into(),
            };
            return err.push(format!("{w}: unknown do \"{shown}\""));
        }
    };
    let allowed = action_fields(d);
    for k in a.keys() {
        if k != "do" && k != "at" && !allowed.contains(&k.as_str()) {
            err.push(format!("{w}: \"{d}\" has unknown field \"{k}\""));
        }
    }
    let nonneg = |k: &str| !a.contains_key(k) || num(a.get(k)).is_some_and(|v| v >= 0.0);
    match d {
        "clip" => {
            if !is_str(a.get("id")) {
                err.push(format!("{w}: clip needs id"));
            }
            check_params(a, w, err);
        }
        "eyes" => {
            if !is_str(a.get("pattern")) {
                err.push(format!("{w}: eyes needs pattern"));
            }
            if let Some(c) = a.get("color") {
                let ok = c.as_str().is_some_and(|s| {
                    s.len() == 7
                        && s.starts_with('#')
                        && s[1..].chars().all(|c| c.is_ascii_hexdigit())
                });
                if !ok {
                    err.push(format!("{w}: color must be #rrggbb"));
                }
            }
            if !nonneg("duration") {
                err.push(format!("{w}: duration must be >= 0"));
            }
            if a.contains_key("intensity") && !in_range(a.get("intensity"), 0.0, 1.0) {
                err.push(format!("{w}: eyes intensity must be 0..1"));
            }
        }
        "chest" => {
            if !is_str(a.get("command")) {
                err.push(format!("{w}: chest needs command"));
            }
            if !nonneg("hold") {
                err.push(format!("{w}: hold must be >= 0"));
            }
        }
        "lights" => {
            if !is_str(a.get("cue")) && !is_str(a.get("mode")) && !is_str(a.get("rig")) {
                err.push(format!("{w}: lights needs cue, mode or rig"));
            }
            for k in ["fade", "hold"] {
                if !nonneg(k) {
                    err.push(format!("{w}: {k} must be >= 0"));
                }
            }
        }
        "sfx" if !is_str(a.get("id")) => err.push(format!("{w}: sfx needs id")),
        "speak" if !is_str(a.get("text")) => err.push(format!("{w}: speak needs text")),
        "wait" if a.get("for").and_then(Value::as_str) != Some("speech_end") => {
            err.push(format!("{w}: wait supports only for: \"speech_end\""))
        }
        _ => {}
    }
}

pub fn validate_item(x: &Value, file: Option<&str>) -> Vec<String> {
    let mut err = Vec::new();
    let w = file
        .map(str::to_owned)
        .or_else(|| x.get("id").and_then(Value::as_str).map(str::to_owned))
        .unwrap_or_else(|| "?".into());
    let Some(o) = x.as_object() else {
        return vec![format!("{w}: not an object")];
    };
    if !o.get("id").and_then(Value::as_str).is_some_and(valid_id) {
        err.push(format!("{w}: id must be snake_case"));
    }
    let kind = o.get("kind").and_then(Value::as_str);
    if !matches!(kind, Some("clip" | "cue" | "sequence")) {
        err.push(format!("{w}: kind must be clip | cue | sequence"));
    }
    if !matches!(
        o.get("tier").and_then(Value::as_str),
        Some("free" | "cheap" | "show")
    ) {
        err.push(format!("{w}: tier must be free | cheap | show"));
    }
    match o.get("description") {
        Some(Value::String(d)) if !d.is_empty() => {
            if d.contains('\n') {
                err.push(format!("{w}: description must be one line"));
            }
        }
        _ => err.push(format!("{w}: description is required")),
    }
    if o.contains_key("title") && !is_str(o.get("title")) {
        err.push(format!("{w}: title must be a string"));
    }
    if let Some(t) = o.get("tags") {
        if !t
            .as_array()
            .is_some_and(|a| a.iter().all(|s| is_str(Some(s))))
        {
            err.push(format!("{w}: tags must be strings"));
        }
    }

    match kind {
        Some("clip") => validate_clip(o, &w, &mut err),
        Some("cue") => match o.get("actions").and_then(Value::as_array) {
            Some(acts) if !acts.is_empty() => {
                for (i, a) in acts.iter().enumerate() {
                    let aw = format!("{w}.actions[{i}]");
                    if !num(a.get("at")).is_some_and(|t| t >= 0.0) || !a.is_object() {
                        err.push(format!("{aw}: at must be >= 0"));
                    }
                    validate_action(a, &aw, &mut err);
                }
            }
            _ => err.push(format!("{w}: actions must be a non-empty list")),
        },
        Some("sequence") => validate_sequence(o, &w, &mut err),
        _ => {}
    }
    err
}

fn validate_clip(o: &Obj, w: &str, err: &mut Vec<String>) {
    let dur = num(o.get("duration"));
    if !dur.is_some_and(|d| d > 0.0) {
        err.push(format!("{w}: duration must be > 0"));
    }
    let dur = dur.unwrap_or(f64::INFINITY);
    if o.contains_key("interruptible_after") && !in_range(o.get("interruptible_after"), 0.0, dur) {
        err.push(format!(
            "{w}: interruptible_after must be within 0..duration"
        ));
    }
    if o.get("requires").is_some_and(|r| r != "extended") {
        err.push(format!("{w}: requires must be \"extended\""));
    }
    let tracks = match o.get("tracks").and_then(Value::as_object) {
        Some(t) if !t.is_empty() => t,
        _ => return err.push(format!("{w}: tracks must be a non-empty object")),
    };
    for (joint, tr) in tracks {
        let tw = format!("{w}.{joint}");
        let Some(tr) = tr.as_object() else {
            err.push(format!("{tw}: not an object"));
            continue;
        };
        let mode = tr.get("mode").and_then(Value::as_str);
        if !matches!(mode, Some("additive" | "override")) {
            err.push(format!("{tw}: mode must be additive | override"));
        }
        if tr
            .get("ease")
            .is_some_and(|e| !matches!(e.as_str(), Some("minjerk" | "linear" | "step")))
        {
            err.push(format!("{tw}: ease must be minjerk | linear | step"));
        }
        if tr.contains_key("blend") && !in_range(tr.get("blend"), 0.0, 2.0) {
            err.push(format!("{tw}: blend must be 0..2 s"));
        }
        for k in tr.keys() {
            if !["mode", "keys", "ease", "blend"].contains(&k.as_str()) {
                err.push(format!("{tw}: unknown field \"{k}\""));
            }
        }
        let keys: Option<Vec<[f64; 2]>> = tr.get("keys").and_then(Value::as_array).and_then(|ks| {
            ks.iter()
                .map(|k| match k.as_array().map(Vec::as_slice) {
                    Some([t, v]) => Some([num(Some(t))?, num(Some(v))?]),
                    _ => None,
                })
                .collect()
        });
        let ks = match keys {
            Some(ks) if ks.len() >= 2 => ks,
            _ => {
                err.push(format!("{tw}: keys must be >= 2 [t, value] pairs"));
                continue;
            }
        };
        if ks[0][0] != 0.0 {
            err.push(format!("{tw}: first key must be at t=0"));
        }
        for p in ks.windows(2) {
            if p[1][0] <= p[0][0] {
                err.push(format!("{tw}: key times must increase"));
            }
        }
        let last = ks[ks.len() - 1];
        if last[0] > dur + 1e-9 {
            err.push(format!("{tw}: key past duration"));
        }
        if mode == Some("additive") && (ks[0][1] != 0.0 || last[1] != 0.0) {
            err.push(format!("{tw}: additive tracks must start and end at 0"));
        }
    }
}

fn validate_sequence(o: &Obj, w: &str, err: &mut Vec<String>) {
    if o.get("clock")
        .is_some_and(|c| !matches!(c.as_str(), Some("time" | "beat")))
    {
        err.push(format!("{w}: clock must be time | beat"));
    }
    if o.contains_key("bpm") && !in_range(o.get("bpm"), 30.0, 300.0) {
        err.push(format!("{w}: bpm must be 30..300"));
    }
    if o.get("layer")
        .is_some_and(|c| !matches!(c.as_str(), Some("show" | "gesture")))
    {
        err.push(format!("{w}: layer must be show | gesture"));
    }
    if let Some(ow) = o.get("owns") {
        if !ow
            .as_array()
            .is_some_and(|a| a.iter().all(|s| is_str(Some(s))))
        {
            err.push(format!("{w}: owns must be joint names"));
        }
    }
    if o.get("loop").is_some_and(|l| !l.is_boolean()) {
        err.push(format!("{w}: loop must be boolean"));
    }
    let looped = o.get("loop") == Some(&Value::Bool(true));
    let length = num(o.get("length"));
    if looped && !length.is_some_and(|l| l > 0.0) {
        err.push(format!("{w}: loop needs length > 0"));
    }
    let track = match o.get("track").and_then(Value::as_array) {
        Some(t) if !t.is_empty() => t,
        _ => return err.push(format!("{w}: track must be a non-empty list")),
    };
    for (i, it) in track.iter().enumerate() {
        let iw = format!("{w}.track[{i}]");
        let at = num(it.get("at"));
        let Some(obj) = it.as_object().filter(|_| at.is_some_and(|t| t >= 0.0)) else {
            err.push(format!("{iw}: at must be >= 0"));
            continue;
        };
        // A department action first: a lights action also carries a `cue` field.
        if obj.contains_key("do") {
            validate_action(it, &iw, err);
            continue;
        }
        let refs: Vec<&str> = ["cue", "clip", "sequence"]
            .into_iter()
            .filter(|k| obj.contains_key(*k))
            .collect();
        if refs.len() != 1 {
            err.push(format!(
                "{iw}: needs exactly one of cue | clip | sequence | do"
            ));
            continue;
        }
        let r = refs[0];
        if !is_str(obj.get(r)) {
            err.push(format!("{iw}: {r} must be an id"));
        }
        for k in obj.keys() {
            let ok = k == "at" || k == r || (r == "clip" && (k == "intensity" || k == "speed"));
            if !ok {
                err.push(format!("{iw}: unknown field \"{k}\""));
            }
        }
        if r == "clip" {
            check_params(obj, &iw, err);
        }
        if let (true, Some(len), Some(at)) = (looped, length, at) {
            if at >= len {
                err.push(format!("{iw}: at is past the loop length"));
            }
        }
    }
}

pub fn validate_idle(x: &Value) -> Vec<String> {
    let Some(o) = x.as_object() else {
        return vec!["idle.json: not an object".into()];
    };
    let mut err = Vec::new();
    if !num(o.get("after_s")).is_some_and(|v| v > 0.0) {
        err.push("idle.json: after_s must be > 0".into());
    }
    for (k, required) in [("choices", true), ("while_music", false)] {
        let l = o.get(k);
        if l.is_none() && !required {
            continue;
        }
        match l.and_then(Value::as_array) {
            Some(l) if !l.is_empty() => {
                for (i, c) in l.iter().enumerate() {
                    if !(is_str(c.get("id")) && num(c.get("weight")).is_some_and(|w| w > 0.0)) {
                        err.push(format!("idle.json: {k}[{i}] needs id and weight > 0"));
                    }
                }
            }
            _ => err.push(format!("idle.json: {k} must be a non-empty list")),
        }
    }
    err
}
