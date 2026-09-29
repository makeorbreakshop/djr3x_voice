//! The committed TS bindings and JSON Schema must match the Rust contracts.

#[test]
fn generated_outputs_are_fresh() {
    let out = std::process::Command::new(env!("CARGO_BIN_EXE_export"))
        .arg("--check")
        .output()
        .expect("run export --check");
    assert!(
        out.status.success(),
        "{}\nregenerate with `cargo run -p r3x-contracts --bin export`",
        String::from_utf8_lossy(&out.stderr)
    );
}
