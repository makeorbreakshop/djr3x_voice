"""Tests for Anthropic-vs-OpenRouter provider resolution.

Covers the three things that can silently break the LLM path: which credential wins, what
``base_url`` the SDK ends up with, and whether the model id is translated for the host that
receives it.

``env={}`` is passed explicitly throughout so a real ``ANTHROPIC_API_KEY`` or
``OPENROUTER_API_KEY`` in the developer's environment cannot change a result.
"""

import anthropic
import pytest

from cantina_os.llm.anthropic_provider import (
    OPENROUTER_BASE_URL,
    PROVIDER_ANTHROPIC,
    PROVIDER_OPENROUTER,
    client_kwargs,
    map_model,
    resolve_provider,
)


class TestProviderSelection:
    def test_direct_anthropic_is_the_default_when_its_key_exists(self):
        p = resolve_provider({"ANTHROPIC_API_KEY": "sk-ant-x"}, env={})
        assert p.provider == PROVIDER_ANTHROPIC
        assert p.base_url is None  # leave the SDK default alone

    def test_anthropic_wins_even_when_openrouter_is_also_available(self):
        p = resolve_provider(
            {"ANTHROPIC_API_KEY": "sk-ant-x", "OPENROUTER_API_KEY": "sk-or-y"}, env={}
        )
        assert p.provider == PROVIDER_ANTHROPIC
        assert p.api_key == "sk-ant-x"

    def test_openrouter_is_used_when_anthropic_key_is_absent(self):
        p = resolve_provider({"OPENROUTER_API_KEY": "sk-or-y"}, env={})
        assert p.provider == PROVIDER_OPENROUTER
        assert p.api_key == "sk-or-y"
        assert p.base_url == OPENROUTER_BASE_URL

    def test_empty_string_key_counts_as_absent(self):
        """main.py passes "" for unset env vars, so blanks must not win the selection."""
        p = resolve_provider(
            {"ANTHROPIC_API_KEY": "   ", "OPENROUTER_API_KEY": "sk-or-y"}, env={}
        )
        assert p.provider == PROVIDER_OPENROUTER

    def test_no_credential_returns_none(self):
        assert resolve_provider({}, env={}) is None

    def test_environment_is_the_fallback_when_config_is_blank(self):
        p = resolve_provider({"ANTHROPIC_API_KEY": ""}, env={"OPENROUTER_API_KEY": "sk-or-y"})
        assert p.provider == PROVIDER_OPENROUTER

    def test_config_overrides_the_environment(self):
        p = resolve_provider(
            {"ANTHROPIC_API_KEY": "from-config"}, env={"ANTHROPIC_API_KEY": "from-env"}
        )
        assert p.api_key == "from-config"


class TestForcedProvider:
    def test_forcing_openrouter_ignores_a_present_anthropic_key(self):
        p = resolve_provider(
            {
                "LLM_PROVIDER": "openrouter",
                "ANTHROPIC_API_KEY": "sk-ant-x",
                "OPENROUTER_API_KEY": "sk-or-y",
            },
            env={},
        )
        assert p.provider == PROVIDER_OPENROUTER

    def test_forcing_anthropic_does_not_silently_fall_through_to_openrouter(self):
        """A forced provider with no key is unavailable, not a redirect."""
        assert (
            resolve_provider(
                {"LLM_PROVIDER": "anthropic", "OPENROUTER_API_KEY": "sk-or-y"}, env={}
            )
            is None
        )

    def test_forcing_openrouter_without_its_key_is_unavailable(self):
        assert (
            resolve_provider(
                {"LLM_PROVIDER": "openrouter", "ANTHROPIC_API_KEY": "sk-ant-x"}, env={}
            )
            is None
        )

    @pytest.mark.parametrize("value", ["auto", "", "AUTO", "something-else"])
    def test_auto_and_unrecognised_values_use_the_default_preference(self, value):
        p = resolve_provider(
            {"LLM_PROVIDER": value, "OPENROUTER_API_KEY": "sk-or-y"}, env={}
        )
        assert p.provider == PROVIDER_OPENROUTER


class TestBaseUrlOverride:
    def test_override_replaces_the_openrouter_host(self):
        p = resolve_provider(
            {"OPENROUTER_API_KEY": "sk-or-y", "ANTHROPIC_BASE_URL": "http://localhost:9"},
            env={},
        )
        assert p.base_url == "http://localhost:9"

    def test_override_also_applies_on_the_direct_path(self):
        p = resolve_provider(
            {"ANTHROPIC_API_KEY": "sk-ant-x", "ANTHROPIC_BASE_URL": "http://localhost:9"},
            env={},
        )
        assert p.provider == PROVIDER_ANTHROPIC
        assert p.base_url == "http://localhost:9"


class TestModelMapping:
    @pytest.mark.parametrize(
        "anthropic_id,openrouter_id",
        [
            ("claude-haiku-4-5-20251001", "anthropic/claude-haiku-4.5"),
            ("claude-haiku-4-5", "anthropic/claude-haiku-4.5"),
            ("claude-sonnet-5", "anthropic/claude-sonnet-5"),
            ("claude-opus-5", "anthropic/claude-opus-5"),
        ],
    )
    def test_mapped_for_openrouter(self, anthropic_id, openrouter_id):
        assert map_model(anthropic_id, PROVIDER_OPENROUTER) == openrouter_id

    @pytest.mark.parametrize(
        "model", ["claude-haiku-4-5-20251001", "claude-opus-5", "anything-else"]
    )
    def test_direct_anthropic_is_the_identity(self, model):
        assert map_model(model, PROVIDER_ANTHROPIC) == model

    def test_unknown_ids_pass_through_rather_than_raising(self):
        """A new model should be a config edit, not a crash."""
        assert map_model("claude-future-9", PROVIDER_OPENROUTER) == "claude-future-9"

    def test_an_already_namespaced_id_is_left_alone(self):
        assert (
            map_model("anthropic/claude-haiku-4.5", PROVIDER_OPENROUTER)
            == "anthropic/claude-haiku-4.5"
        )


class TestClientConstruction:
    """The contract the SDK has to honour: x-api-key, and /v1/messages under the base_url.

    This is the part that was documented-but-unmeasured. Requests are built, not sent.
    """

    def _built_request(self, provider_config):
        client = anthropic.Anthropic(**client_kwargs(provider_config))
        from anthropic._models import FinalRequestOptions

        return client._build_request(
            FinalRequestOptions.construct(
                method="post", url="/v1/messages", json_data={"probe": True}
            )
        )

    def test_openrouter_request_hits_the_documented_messages_endpoint(self):
        p = resolve_provider({"OPENROUTER_API_KEY": "sk-or-y"}, env={})
        req = self._built_request(p)
        # Not /api/v1/v1/messages: the SDK appends "/v1" itself, so the base_url stops at
        # /api. Getting this wrong returns OpenRouter's 404 HTML page.
        assert str(req.url) == "https://openrouter.ai/api/v1/messages"

    def test_openrouter_request_authenticates_with_x_api_key(self):
        p = resolve_provider({"OPENROUTER_API_KEY": "sk-or-y"}, env={})
        headers = {k.lower(): v for k, v in self._built_request(p).headers.items()}
        assert headers["x-api-key"] == "sk-or-y"
        assert "authorization" not in headers
        assert headers["anthropic-version"] == "2023-06-01"

    def test_base_url_is_omitted_on_the_direct_path(self):
        """Passing base_url=None would clobber the SDK's own default."""
        p = resolve_provider({"ANTHROPIC_API_KEY": "sk-ant-x"}, env={})
        assert "base_url" not in client_kwargs(p)
        assert str(self._built_request(p).url).startswith("https://api.anthropic.com/")
