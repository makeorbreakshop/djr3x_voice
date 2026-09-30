//! Regenerate TypeScript bindings and JSON Schema from the Rust contracts.
//!
//! `cargo run -p r3x-contracts --bin export` (paths are relative to the repo, not the cwd).
//! `--check` fails if the committed output is stale.

use std::path::{Path, PathBuf};

use r3x_contracts::{envelope::Envelope, profile::RobotProfile, ClientMessage};
use ts_rs::{Config, TS};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let check = std::env::args().any(|a| a == "--check");
    let repo = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../..").canonicalize()?;
    let ts_dir = repo.join("sim/web/src/generated");
    let schema_dir = repo.join("rust/contracts-schema");

    // Per process: concurrent checks (parallel test runs, worktrees) must not share a scratch dir.
    let scratch = std::env::temp_dir().join(format!("r3x-contracts-check-{}", std::process::id()));
    let out = if check { scratch.clone() } else { repo.clone() };
    let ts_out = out.join("sim/web/src/generated");
    let schema_out = out.join("rust/contracts-schema");
    for d in [&ts_out, &schema_out] {
        let _ = std::fs::remove_dir_all(d);
        std::fs::create_dir_all(d)?;
    }

    // u64 fields (seq, run_id) never exceed 2^53 in practice; keep them plain numbers in TS.
    let cfg = Config::new().with_out_dir(&ts_out).with_large_int("number");
    Envelope::export_all(&cfg)?;
    RobotProfile::export_all(&cfg)?;
    ClientMessage::export_all(&cfg)?;

    write_schema(&schema_out, "envelope", schemars::schema_for!(Envelope))?;
    write_schema(&schema_out, "robot_profile", schemars::schema_for!(RobotProfile))?;
    write_schema(&schema_out, "client_message", schemars::schema_for!(ClientMessage))?;

    if check {
        for (committed, fresh) in [(&ts_dir, &ts_out), (&schema_dir, &schema_out)] {
            if !same_tree(committed, fresh)? {
                let _ = std::fs::remove_dir_all(&scratch);
                eprintln!("{} is stale; run `cargo run -p r3x-contracts --bin export`", committed.display());
                std::process::exit(1);
            }
        }
        let _ = std::fs::remove_dir_all(&scratch);
        println!("generated contracts are up to date");
    } else {
        println!("wrote {} and {}", ts_dir.display(), schema_dir.display());
    }
    Ok(())
}

/// Object keys sorted, so the output does not depend on serde_json's `preserve_order` feature
/// (which feature unification turns on when this builds alongside the performer).
fn sorted(v: serde_json::Value) -> serde_json::Value {
    match v {
        serde_json::Value::Object(m) => {
            let mut kv: Vec<_> = m.into_iter().collect();
            kv.sort_by(|a, b| a.0.cmp(&b.0));
            serde_json::Value::Object(kv.into_iter().map(|(k, v)| (k, sorted(v))).collect())
        }
        serde_json::Value::Array(a) => serde_json::Value::Array(a.into_iter().map(sorted).collect()),
        other => other,
    }
}

fn write_schema(dir: &Path, name: &str, schema: schemars::Schema) -> std::io::Result<()> {
    let json = serde_json::to_string_pretty(&sorted(schema.to_value())).expect("schema serialises");
    std::fs::write(dir.join(format!("{name}.schema.json")), json + "\n")
}

/// (path relative to `root`, contents) for every file under `dir`, sorted.
fn files(root: &Path, dir: &Path, out: &mut Vec<(PathBuf, Vec<u8>)>) -> std::io::Result<()> {
    for e in std::fs::read_dir(dir)? {
        let p = e?.path();
        if p.is_dir() {
            files(root, &p, out)?;
        } else {
            out.push((p.strip_prefix(root).unwrap().to_path_buf(), std::fs::read(&p)?));
        }
    }
    Ok(())
}

fn same_tree(a: &Path, b: &Path) -> std::io::Result<bool> {
    if !a.exists() {
        return Ok(false);
    }
    let (mut fa, mut fb) = (Vec::new(), Vec::new());
    files(a, a, &mut fa)?;
    files(b, b, &mut fb)?;
    fa.sort();
    fb.sort();
    Ok(fa == fb)
}
