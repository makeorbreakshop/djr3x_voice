//! Provider resolution (CLAUDE.md §9a, `llm/anthropic_provider.py`).
//!
//! - `ANTHROPIC_API_KEY` present -> direct to Anthropic; always wins under `auto`.
//! - only `OPENROUTER_API_KEY` -> same wire format at `https://openrouter.ai/api`
//!   (NOT `.../api/v1`: the client appends `/v1/messages` itself).
//! - `LLM_PROVIDER=anthropic|openrouter` forces the choice; a forced provider without its key
//!   is *unavailable*, never a silent fallback to the other one.
//! - `ANTHROPIC_BASE_URL` overrides the host of whichever provider was chosen.

pub const ANTHROPIC_BASE_URL: &str = "https://api.anthropic.com";
pub const OPENROUTER_BASE_URL: &str = "https://openrouter.ai/api";

/// Anthropic id -> OpenRouter id. Unmapped ids pass through unchanged.
pub const OPENROUTER_MODEL_MAP: &[(&str, &str)] = &[
    ("claude-haiku-4-5-20251001", "anthropic/claude-haiku-4.5"),
    ("claude-haiku-4-5", "anthropic/claude-haiku-4.5"),
    ("claude-sonnet-5", "anthropic/claude-sonnet-5"),
    ("claude-sonnet-5-5", "anthropic/claude-sonnet-5.5"),
    ("claude-opus-5", "anthropic/claude-opus-5"),
];

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ProviderKind {
    Anthropic,
    OpenRouter,
}

#[derive(Clone, PartialEq, Eq)]
pub struct ProviderConfig {
    pub kind: ProviderKind,
    pub api_key: String,
    /// Host root; `/v1/messages` is appended.
    pub base_url: String,
}

impl std::fmt::Debug for ProviderConfig {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("ProviderConfig")
            .field("kind", &self.kind)
            .field("base_url", &self.base_url)
            .finish_non_exhaustive()
    }
}

pub fn map_model(model: &str, kind: ProviderKind) -> String {
    if kind != ProviderKind::OpenRouter {
        return model.to_string();
    }
    OPENROUTER_MODEL_MAP
        .iter()
        .find(|(a, _)| *a == model)
        .map_or_else(|| model.to_string(), |(_, o)| (*o).to_string())
}

/// Resolve from a key lookup (`std::env::var` in production, a map in tests).
/// Returns `None` when no usable credential exists: the LLM is unavailable.
pub fn resolve_provider(get: impl Fn(&str) -> Option<String>) -> Option<ProviderConfig> {
    let val = |k: &str| get(k).map(|v| v.trim().to_string()).filter(|v| !v.is_empty());
    let anthropic = val("ANTHROPIC_API_KEY");
    let openrouter = val("OPENROUTER_API_KEY");
    let base_override = val("ANTHROPIC_BASE_URL");
    let requested = val("LLM_PROVIDER").unwrap_or_else(|| "auto".into()).to_lowercase();

    let (kind, key) = match requested.as_str() {
        "anthropic" => (ProviderKind::Anthropic, anthropic?),
        "openrouter" => (ProviderKind::OpenRouter, openrouter?),
        _ => match (anthropic, openrouter) {
            (Some(k), _) => (ProviderKind::Anthropic, k),
            (None, Some(k)) => (ProviderKind::OpenRouter, k),
            (None, None) => return None,
        },
    };
    let default_base = match kind {
        ProviderKind::Anthropic => ANTHROPIC_BASE_URL,
        ProviderKind::OpenRouter => OPENROUTER_BASE_URL,
    };
    Some(ProviderConfig {
        kind,
        api_key: key,
        base_url: base_override
            .unwrap_or_else(|| default_base.to_string())
            .trim_end_matches('/')
            .to_string(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    fn env(pairs: &[(&str, &str)]) -> impl Fn(&str) -> Option<String> {
        let m: HashMap<String, String> =
            pairs.iter().map(|(k, v)| (k.to_string(), v.to_string())).collect();
        move |k| m.get(k).cloned()
    }

    #[test]
    fn resolution_rules() {
        let both = resolve_provider(env(&[("ANTHROPIC_API_KEY", "a"), ("OPENROUTER_API_KEY", "o")])).unwrap();
        assert_eq!((both.kind, both.api_key.as_str(), both.base_url.as_str()), (ProviderKind::Anthropic, "a", ANTHROPIC_BASE_URL));

        let or = resolve_provider(env(&[("OPENROUTER_API_KEY", "o"), ("ANTHROPIC_API_KEY", "  ")])).unwrap();
        assert_eq!((or.kind, or.base_url.as_str()), (ProviderKind::OpenRouter, "https://openrouter.ai/api"));

        // forced without its key: unavailable, no cross-fallback
        assert!(resolve_provider(env(&[("LLM_PROVIDER", "anthropic"), ("OPENROUTER_API_KEY", "o")])).is_none());
        assert!(resolve_provider(env(&[("LLM_PROVIDER", "OpenRouter"), ("ANTHROPIC_API_KEY", "a")])).is_none());
        let forced = resolve_provider(env(&[("LLM_PROVIDER", "openrouter"), ("ANTHROPIC_API_KEY", "a"), ("OPENROUTER_API_KEY", "o")])).unwrap();
        assert_eq!(forced.kind, ProviderKind::OpenRouter);

        let over = resolve_provider(env(&[("OPENROUTER_API_KEY", "o"), ("ANTHROPIC_BASE_URL", "http://localhost:9/")])).unwrap();
        assert_eq!(over.base_url, "http://localhost:9");
        assert!(resolve_provider(env(&[])).is_none());
    }

    #[test]
    fn model_mapping() {
        assert_eq!(map_model("claude-haiku-4-5-20251001", ProviderKind::OpenRouter), "anthropic/claude-haiku-4.5");
        assert_eq!(map_model("claude-haiku-4-5-20251001", ProviderKind::Anthropic), "claude-haiku-4-5-20251001");
        assert_eq!(map_model("claude-new-9", ProviderKind::OpenRouter), "claude-new-9");
    }
}
