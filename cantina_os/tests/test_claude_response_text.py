"""Tests for claude_service._response_text.

Regression cover for a latent crash found by Pyright on 2026-09-17: all four Anthropic
response-reading sites in claude_service used `response.content[0].text`, which raises
AttributeError whenever the first content block is not a text block. The main voice turn is
sent `tools=`, so a leading `tool_use` block is the normal case on exactly the path that
matters most.
"""

from types import SimpleNamespace

import pytest

from cantina_os.services.claude_service.claude_service import _response_text


def _block(type_, **kw):
    return SimpleNamespace(type=type_, **kw)


def test_plain_text_response():
    r = SimpleNamespace(content=[_block("text", text="Hey there.")])
    assert _response_text(r) == "Hey there."


def test_tool_use_block_first_does_not_raise():
    """The old `content[0].text` raised AttributeError here."""
    r = SimpleNamespace(
        content=[
            _block("tool_use", id="tu_1", name="play_music", input={"track": "cantina"}),
            _block("text", text="Spinning it up."),
        ]
    )
    assert _response_text(r) == "Spinning it up."


def test_thinking_block_first_does_not_raise():
    r = SimpleNamespace(
        content=[_block("thinking", thinking="hmm"), _block("text", text="Sure.")]
    )
    assert _response_text(r) == "Sure."


def test_tool_use_only_response_returns_empty_string():
    r = SimpleNamespace(content=[_block("tool_use", id="tu_1", name="stop_music", input={})])
    assert _response_text(r) == ""


def test_text_after_the_first_block_is_not_discarded():
    """The old expression returned only content[0], silently dropping the rest."""
    r = SimpleNamespace(
        content=[_block("text", text="One. "), _block("text", text="Two.")]
    )
    assert _response_text(r) == "One. Two."


@pytest.mark.parametrize("content", [None, [], ()])
def test_empty_content_returns_empty_string(content):
    assert _response_text(SimpleNamespace(content=content)) == ""


def test_missing_content_attribute_returns_empty_string():
    assert _response_text(SimpleNamespace()) == ""
