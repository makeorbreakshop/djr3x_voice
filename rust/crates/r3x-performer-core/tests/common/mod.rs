//! Shared test helpers: repo paths, the show folder, JSON comparison "as parsed JSON"
//! (`2` equals `2.0`, SPEC "Cross-language parity").
#![allow(dead_code)]

use r3x_performer_core::show::catalog::Catalog;
use serde_json::Value;
use std::path::PathBuf;

pub fn repo(rel: &str) -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../..")
        .join(rel)
}

pub fn read_json(rel: &str) -> Value {
    serde_json::from_str(
        &std::fs::read_to_string(repo(rel)).unwrap_or_else(|e| panic!("{rel}: {e}")),
    )
    .unwrap()
}

/// The repo's `show/` folder, loaded the way the sim's loader does.
pub fn show_catalog() -> Catalog {
    let mut files = Vec::new();
    for dir in ["clips", "cues", "sequences"] {
        for e in std::fs::read_dir(repo(&format!("show/{dir}"))).unwrap() {
            let name = e.unwrap().file_name().into_string().unwrap();
            if name.ends_with(".json") {
                let rel = format!("show/{dir}/{name}");
                files.push((rel.clone(), std::fs::read_to_string(repo(&rel)).unwrap()));
            }
        }
    }
    files.push((
        "show/idle.json".into(),
        std::fs::read_to_string(repo("show/idle.json")).unwrap(),
    ));
    files.push((
        "show/intentions.json".into(),
        std::fs::read_to_string(repo("show/intentions.json")).unwrap(),
    ));
    Catalog::from_files(files.iter().map(|(p, t)| (p.as_str(), t.as_str())))
}

/// A parity fixture: its catalogue, root id and bpm.
pub fn fixture_catalog(f: &str) -> (Catalog, String, f64) {
    let doc = read_json(&format!("show/tests/fixtures/{f}"));
    let items = doc["items"].as_array().unwrap().clone();
    let cat = Catalog::new(items, None);
    (
        cat,
        doc["fixture"]["root"].as_str().unwrap().into(),
        doc["fixture"]["bpm"].as_f64().unwrap(),
    )
}

pub fn approx(got: f64, want: f64, tol: f64) {
    assert!(
        (got - want).abs() <= tol,
        "got {got}, want {want} (tol {tol})"
    );
}

/// Structural equality with numbers compared as f64 (within `1e-9`).
pub fn json_eq(a: &Value, b: &Value) -> bool {
    match (a, b) {
        (Value::Number(x), Value::Number(y)) => {
            (x.as_f64().unwrap() - y.as_f64().unwrap()).abs() <= 1e-9
        }
        (Value::Array(x), Value::Array(y)) => {
            x.len() == y.len() && x.iter().zip(y).all(|(a, b)| json_eq(a, b))
        }
        (Value::Object(x), Value::Object(y)) => {
            x.len() == y.len()
                && x.iter()
                    .all(|(k, v)| y.get(k).is_some_and(|w| json_eq(v, w)))
        }
        _ => a == b,
    }
}

pub fn assert_json_eq(got: &Value, want: &Value, what: &str) {
    assert!(
        json_eq(got, want),
        "{what}: got\n{}\nwant\n{}",
        serde_json::to_string_pretty(got).unwrap(),
        serde_json::to_string_pretty(want).unwrap()
    );
}
