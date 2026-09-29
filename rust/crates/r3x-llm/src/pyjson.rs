//! Python `json.dumps`-compatible serialisation, so Phase 0 fixture keys (sha1 of a Python dump)
//! and Jev's `state` string are reproduced byte for byte.

use serde_json::Value;
use sha1::{Digest, Sha1};

/// `json.dumps(v, sort_keys=sort, separators=(",",":") if compact else (", ",": "))`,
/// with Python's default `ensure_ascii=True`.
pub fn dumps(v: &Value, sort_keys: bool, compact: bool) -> String {
    let mut s = String::new();
    write(&mut s, v, sort_keys, if compact { (",", ":") } else { (", ", ": ") });
    s
}

fn write(s: &mut String, v: &Value, sort: bool, sep: (&str, &str)) {
    match v {
        Value::Null => s.push_str("null"),
        Value::Bool(b) => s.push_str(if *b { "true" } else { "false" }),
        Value::Number(n) => s.push_str(&n.to_string()),
        Value::String(t) => write_str(s, t),
        Value::Array(a) => {
            s.push('[');
            for (i, x) in a.iter().enumerate() {
                if i > 0 {
                    s.push_str(sep.0);
                }
                write(s, x, sort, sep);
            }
            s.push(']');
        }
        Value::Object(m) => {
            let mut keys: Vec<&String> = m.keys().collect();
            if sort {
                keys.sort();
            }
            s.push('{');
            for (i, k) in keys.into_iter().enumerate() {
                if i > 0 {
                    s.push_str(sep.0);
                }
                write_str(s, k);
                s.push_str(sep.1);
                write(s, &m[k], sort, sep);
            }
            s.push('}');
        }
    }
}

pub fn write_str(s: &mut String, t: &str) {
    s.push('"');
    for c in t.chars() {
        match c {
            '"' => s.push_str("\\\""),
            '\\' => s.push_str("\\\\"),
            '\n' => s.push_str("\\n"),
            '\r' => s.push_str("\\r"),
            '\t' => s.push_str("\\t"),
            '\u{08}' => s.push_str("\\b"),
            '\u{0c}' => s.push_str("\\f"),
            c if (c as u32) < 0x20 || (c as u32) > 0x7e => {
                let mut buf = [0u16; 2];
                for u in c.encode_utf16(&mut buf) {
                    s.push_str(&format!("\\u{:04x}", u));
                }
            }
            c => s.push(c),
        }
    }
    s.push('"');
}

/// `tap/fixtures.py::_key(*parts)`: sha1 of the sorted compact dump, first 16 hex chars.
pub fn fixture_key(parts: &[Value]) -> String {
    let blob = dumps(&Value::Array(parts.to_vec()), true, true);
    let digest = Sha1::digest(blob.as_bytes());
    digest.iter().map(|b| format!("{b:02x}")).collect::<String>()[..16].to_string()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn matches_python() {
        // python: json.dumps({"b":[1,"é\n"],"a":None}, sort_keys=True)
        assert_eq!(dumps(&json!({"b": [1, "é\n"], "a": null}), true, false), r#"{"a": null, "b": [1, "\u00e9\n"]}"#);
        assert_eq!(dumps(&json!(["x", {"t": "🎵"}]), true, true), r#"["x",{"t":"\ud83c\udfb5"}]"#);
        // python: _key("claude","stream",None,"hi")
        assert_eq!(fixture_key(&[json!("claude"), json!("stream"), Value::Null, json!("hi")]), "86101d9f5470ba71");
    }
}
