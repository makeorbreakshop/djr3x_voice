"""Provider resolution for the Anthropic SDK client.

CantinaOS talks to Claude through the official ``anthropic`` Python SDK. That SDK is not
locked to Anthropic's own host: it sends ``x-api-key`` and ``anthropic-version`` to
``{base_url}/v1/messages``, so any gateway that speaks the Anthropic Messages format can be
reached by overriding ``base_url``.

OpenRouter does speak it. That matters because this machine has no Anthropic key with
credit, which left the whole Claude path dead (see ``dev_logs/2026-09-17_dev_log.md`` ->
"Live Claude verification: BLOCKED"). Routing through OpenRouter revives it without
rewriting a single call site.

Two rules:

1. **Direct Anthropic wins when its key exists.** OpenRouter is the fallback, never the
   default. A restored ``ANTHROPIC_API_KEY`` silently takes the wheel back.
2. **Model ids are translated, not guessed at the call site.** Anthropic's ids carry a date
   suffix (``claude-haiku-4-5-20251001``); OpenRouter's are namespaced and dotted
   (``anthropic/claude-haiku-4.5``). The mapping lives here, once, and every service keeps
   configuring itself with the Anthropic id it already used.

Base-URL gotcha worth stating plainly: the SDK appends its own ``/v1``. OpenRouter's
documented endpoint is ``https://openrouter.ai/api/v1/messages``, so the ``base_url`` here is
``https://openrouter.ai/api`` — not ``.../api/v1``, which produces ``/api/v1/v1/messages``
and a 404 HTML page.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

# The SDK appends "/v1" itself. See the module docstring.
OPENROUTER_BASE_URL = "https://openrouter.ai/api"

PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_OPENROUTER = "openrouter"

#: Anthropic model id -> OpenRouter model id. Kept deliberately small: only the models
#: CantinaOS actually configures, plus the two larger ones worth reaching for by hand.
#: An unmapped id is passed through unchanged so a new model is a config edit, not a crash.
OPENROUTER_MODEL_MAP: Dict[str, str] = {
    "claude-haiku-4-5-20251001": "anthropic/claude-haiku-4.5",
    "claude-haiku-4-5": "anthropic/claude-haiku-4.5",
    "claude-sonnet-5": "anthropic/claude-sonnet-5",
    "claude-sonnet-5-5": "anthropic/claude-sonnet-5.5",
    "claude-opus-5": "anthropic/claude-opus-5",
}


@dataclass(frozen=True)
class ProviderConfig:
    """Everything needed to construct an ``Anthropic`` client, already resolved."""

    provider: str
    api_key: str
    #: ``None`` means "leave the SDK default alone" (i.e. Anthropic's own host).
    base_url: Optional[str]

    @property
    def is_openrouter(self) -> bool:
        return self.provider == PROVIDER_OPENROUTER


def map_model(model: str, provider: str) -> str:
    """Translate an Anthropic model id for ``provider``.

    Direct Anthropic is the identity. Unknown ids pass through untouched on every provider.
    """
    if provider != PROVIDER_OPENROUTER:
        return model
    return OPENROUTER_MODEL_MAP.get(model, model)


def resolve_provider(
    config: Optional[Mapping[str, Any]] = None,
    env: Optional[Mapping[str, str]] = None,
) -> Optional[ProviderConfig]:
    """Decide which Anthropic-compatible backend to use.

    Values are read from ``config`` first and fall back to ``env`` (default
    ``os.environ``), so a service config can always override the process environment.

    ``LLM_PROVIDER`` forces the choice:

    * ``anthropic``   — direct only; returns ``None`` if ``ANTHROPIC_API_KEY`` is missing.
    * ``openrouter``  — OpenRouter only; returns ``None`` if ``OPENROUTER_API_KEY`` is missing.
    * ``auto`` / unset — direct when ``ANTHROPIC_API_KEY`` is present, else OpenRouter.

    ``ANTHROPIC_BASE_URL`` overrides the host for whichever provider is chosen, which is the
    escape hatch for a local proxy or a different gateway entirely.

    Returns ``None`` when no usable credential exists. Callers treat that as "the LLM is
    unavailable" — the same condition they already handled when the key was simply absent.
    """
    cfg = config or {}
    environ = env if env is not None else os.environ

    def _get(name: str) -> str:
        value = cfg.get(name)
        if value is None or str(value).strip() == "":
            value = environ.get(name, "")
        return str(value).strip()

    anthropic_key = _get("ANTHROPIC_API_KEY")
    openrouter_key = _get("OPENROUTER_API_KEY")
    base_url_override = _get("ANTHROPIC_BASE_URL") or None
    requested = (_get("LLM_PROVIDER") or "auto").lower()

    if requested == PROVIDER_ANTHROPIC:
        chosen = PROVIDER_ANTHROPIC if anthropic_key else None
    elif requested == PROVIDER_OPENROUTER:
        chosen = PROVIDER_OPENROUTER if openrouter_key else None
    else:
        if anthropic_key:
            chosen = PROVIDER_ANTHROPIC
        elif openrouter_key:
            chosen = PROVIDER_OPENROUTER
        else:
            chosen = None

    if chosen is None:
        return None

    if chosen == PROVIDER_ANTHROPIC:
        return ProviderConfig(PROVIDER_ANTHROPIC, anthropic_key, base_url_override)
    return ProviderConfig(
        PROVIDER_OPENROUTER, openrouter_key, base_url_override or OPENROUTER_BASE_URL
    )


def client_kwargs(provider: ProviderConfig) -> Dict[str, Any]:
    """Keyword arguments for ``Anthropic(**kwargs)``.

    ``base_url`` is omitted rather than passed as ``None`` so the SDK's own default (and the
    ``ANTHROPIC_BASE_URL`` env var it reads itself) keep working untouched on the direct path.
    """
    kwargs: Dict[str, Any] = {"api_key": provider.api_key}
    if provider.base_url:
        kwargs["base_url"] = provider.base_url
    return kwargs
