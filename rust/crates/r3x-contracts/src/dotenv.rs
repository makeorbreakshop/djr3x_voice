//! The repo-root `.env`, loaded once into the process environment at startup (every binary
//! calls [`load`] first thing). A variable that is already set (non-empty) is never
//! overridden, so the shell always wins. `R3X_DOTENV` points at another file, or `0`/`off`
//! skips it. Provider choice between the keys it brings in stays with `r3x_llm::provider`
//! (CLAUDE.md §9a).

use std::path::{Path, PathBuf};

/// `KEY=VALUE` lines (optional `export `); quotes stripped; blank and `#` lines skipped.
pub fn parse(text: &str) -> Vec<(String, String)> {
    text.lines()
        .filter_map(|l| {
            let l = l.trim();
            if l.is_empty() || l.starts_with('#') {
                return None;
            }
            let (k, v) = l.strip_prefix("export ").unwrap_or(l).split_once('=')?;
            let v = v.trim();
            let v = v.strip_prefix('"').and_then(|v| v.strip_suffix('"')).or_else(|| v.strip_prefix('\'').and_then(|v| v.strip_suffix('\''))).unwrap_or(v);
            Some((k.trim().to_owned(), v.to_owned()))
        })
        .filter(|(k, _)| !k.is_empty())
        .collect()
}

/// The repo-root `.env` this workspace was built in, unless `R3X_DOTENV` says otherwise.
pub fn path() -> Option<PathBuf> {
    match std::env::var("R3X_DOTENV") {
        Ok(v) if matches!(v.as_str(), "0" | "off" | "false" | "none") => None,
        Ok(v) if !v.is_empty() => Some(v.into()),
        _ => Some(Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../.env")),
    }
}

/// Load the `.env` into the process environment without overriding anything already set.
/// Returns the names it set. Call before spawning threads.
pub fn load() -> Vec<String> {
    let Some(p) = path() else { return Vec::new() };
    let Ok(text) = std::fs::read_to_string(&p) else { return Vec::new() };
    let mut set = Vec::new();
    for (k, v) in parse(&text) {
        if std::env::var_os(&k).is_some_and(|cur| !cur.is_empty()) || v.is_empty() {
            continue;
        }
        std::env::set_var(&k, v);
        set.push(k);
    }
    set
}

#[cfg(test)]
mod tests {
    #[test]
    fn parsing() {
        let m = super::parse("# c\nA=1\nexport B=\"two\"\n\n  # ANTHROPIC_API_KEY=x\nC='x=y'\nbad line\nD=\n");
        let m: Vec<(&str, &str)> = m.iter().map(|(k, v)| (k.as_str(), v.as_str())).collect();
        assert_eq!(m, [("A", "1"), ("B", "two"), ("C", "x=y"), ("D", "")]);
    }
}
