"""
Test suite for IntentRouterService.

This test suite tests the functionality of the IntentRouterService which routes intents
detected by the GPT service to appropriate hardware commands.

NOTE ON PRODUCTION CONTRACT (as of this branch):
`_handle_intent` (intent_router_service.py) no longer emits domain-specific
`MUSIC_COMMAND` / `EYE_COMMAND` events directly. Every intent handler builds a
generic CLI command dict and emits it on `EventTopics.CLI_COMMAND` (routed
through the CommandDispatcher, matching how CLI/text commands are handled).
Additionally, `_handle_intent` always emits a second event,
`EventTopics.INTENT_EXECUTION_RESULT`, carrying the handler's structured
result (used by GPTService for verbal feedback) — except for `analyze_scene`,
which is explicitly skipped since it produces its own response via
VISION_SCENE_CAPTURED. So for every successful intent, `event_bus.emit` is
called twice: once with the CLI_COMMAND payload, once with the
INTENT_EXECUTION_RESULT payload. For unknown intents / missing required
parameters, only the INTENT_EXECUTION_RESULT (failure) event is emitted — no
event is silently dropped anymore.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
import asyncio

from cantina_os.services.intent_router_service import IntentRouterService
from cantina_os.core.event_topics import EventTopics
from cantina_os.event_payloads import IntentPayload, MusicCommandPayload, EyeCommandPayload
from cantina_os.base_service import BaseService

@pytest.fixture
def mock_event_bus():
    return MagicMock()

@pytest.fixture
def router_service(mock_event_bus):
    service = IntentRouterService(mock_event_bus)
    service._config = {}
    return service

def _emitted_payloads(mock_event_bus, topic_enum):
    """Find all payloads emitted for a given EventTopics member (compares against
    .value, since BaseService.emit converts Enum topics to their string value
    before calling the underlying event bus)."""
    return [
        payload
        for topic, payload in (c[0] for c in mock_event_bus.emit.call_args_list)
        if topic == topic_enum.value
    ]

def _emitted_payload(mock_event_bus, topic_enum):
    """Return the last payload emitted for a given EventTopics member, or None."""
    payloads = _emitted_payloads(mock_event_bus, topic_enum)
    return payloads[-1] if payloads else None

@pytest.mark.asyncio
async def test_play_music_intent_routing(router_service, mock_event_bus):
    """Test that play_music intent is routed correctly to a CLI_COMMAND."""
    intent_payload = IntentPayload(
        intent_name="play_music",
        parameters={"track": "cantina"},
        original_text="Play the cantina song",
        conversation_id="test_conv_123"
    )

    await router_service._handle_intent(intent_payload.dict())

    # CHANGED 2026-09-17: `_select_smart_track` no longer emits a `list music` CLI_COMMAND as
    # a side effect (it dumped the library to the console mid-turn), so there are two emits:
    # the `play music` command and the INTENT_EXECUTION_RESULT.
    assert mock_event_bus.emit.call_count == 2
    cli_payloads = _emitted_payloads(mock_event_bus, EventTopics.CLI_COMMAND)
    assert len(cli_payloads) == 1

    cli_payload = next(p for p in cli_payloads if p.get("command") == "play")
    assert cli_payload is not None
    assert cli_payload.get("command") == "play"
    assert cli_payload.get("subcommand") == "music"
    # The naming word is handed to MusicControllerService, which owns the only matcher that
    # knows the real library. It used to be mapped here to "cantina_band", a track id that
    # matches no file - see tests/test_music_confirmation_names_the_real_track.py.
    assert cli_payload.get("args") == ["cantina"]
    assert cli_payload.get("conversation_id") == "test_conv_123"

    result_payload = _emitted_payload(mock_event_bus, EventTopics.INTENT_EXECUTION_RESULT)
    assert result_payload is not None
    assert result_payload.get("success") is True
    assert result_payload.get("intent_name") == "play_music"

@pytest.mark.asyncio
async def test_stop_music_intent_routing(router_service, mock_event_bus):
    """Test that stop_music intent is routed correctly to a CLI_COMMAND."""
    intent_payload = IntentPayload(
        intent_name="stop_music",
        parameters={},
        original_text="Stop the music",
        conversation_id="test_conv_456"
    )

    await router_service._handle_intent(intent_payload.dict())

    assert mock_event_bus.emit.call_count == 2

    cli_payload = _emitted_payload(mock_event_bus, EventTopics.CLI_COMMAND)
    assert cli_payload is not None
    assert cli_payload.get("command") == "stop"
    assert cli_payload.get("subcommand") == "music"
    assert cli_payload.get("conversation_id") == "test_conv_456"

    result_payload = _emitted_payload(mock_event_bus, EventTopics.INTENT_EXECUTION_RESULT)
    assert result_payload is not None
    assert result_payload.get("success") is True


@pytest.mark.asyncio
async def test_stop_music_waits_for_real_playback_stop(router_service):
    async def emit_and_confirm(topic, payload):
        if topic == EventTopics.CLI_COMMAND:
            await router_service._handle_music_playback_stopped(
                {"track_name": "Modal Notes"}
            )

    router_service.emit = AsyncMock(side_effect=emit_and_confirm)

    result = await router_service._handle_stop_music_intent({}, "turn-stop")

    assert result == {
        "success": True,
        "action": "stop",
        "track": "Modal Notes",
        "message": "Music stopped: Modal Notes",
    }

@pytest.mark.asyncio
async def test_set_eye_color_intent_routing(router_service, mock_event_bus):
    """set_eye_color must emit EYE_COMMAND directly, carrying the colour.

    UPDATED 2026-09-17. This used to assert a CLI_COMMAND of
    `eye pattern <pattern> <color>`. That route was broken two ways and was proved broken by
    a full-system run: the compound command "eye pattern" is registered with max_args=1
    (main.py:349), so command_decorators.py:368 rejected the two-arg form outright, and
    EyeCliCommandPayload.from_cli_payload has no colour slot at all so the colour was
    discarded regardless. The router now emits EYE_COMMAND, which
    EyeLightControllerService._handle_eye_command already accepts as a dict of
    pattern/color/intensity/duration and passes straight to set_pattern().
    """
    intent_payload = IntentPayload(
        intent_name="set_eye_color",
        parameters={"color": "blue", "pattern": "pulse", "intensity": 0.8},
        original_text="Change your eyes to blue",
        conversation_id="test_conv_789"
    )

    await router_service._handle_intent(intent_payload.dict())

    assert mock_event_bus.emit.call_count == 2

    eye_payload = _emitted_payload(mock_event_bus, EventTopics.EYE_COMMAND)
    assert eye_payload is not None, "expected EYE_COMMAND, not CLI_COMMAND"
    assert eye_payload.get("pattern") == "pulse"
    assert eye_payload.get("color") == "blue"
    assert eye_payload.get("intensity") == 0.8
    assert eye_payload.get("conversation_id") == "test_conv_789"

    assert _emitted_payload(mock_event_bus, EventTopics.CLI_COMMAND) is None

    result_payload = _emitted_payload(mock_event_bus, EventTopics.INTENT_EXECUTION_RESULT)
    assert result_payload is not None
    assert result_payload.get("success") is True
    result = result_payload.get("result", {})
    assert result.get("color") == "blue"
    assert result.get("pattern") == "pulse"
    assert result.get("intensity") == 0.8

@pytest.mark.asyncio
async def test_set_eye_color_with_missing_pattern(router_service, mock_event_bus):
    """Test that set_eye_color uses default pattern when not provided."""
    intent_payload = IntentPayload(
        intent_name="set_eye_color",
        parameters={"color": "red", "intensity": 0.5},
        original_text="Make your eyes red",
        conversation_id="test_conv_101"
    )

    await router_service._handle_intent(intent_payload.dict())

    eye_payload = _emitted_payload(mock_event_bus, EventTopics.EYE_COMMAND)
    assert eye_payload is not None
    # UPDATED 2026-09-17: the default was "solid", which is not a member of EyePattern
    # (eye_light_controller_service.py:48), so the eye service raised ValueError and logged
    # "Invalid eye pattern: solid" for every colour request that omitted a pattern. CUSTOM is
    # the member documented at :62 as being for "custom patterns with specific colors".
    assert eye_payload.get("pattern") == "custom"
    assert eye_payload.get("color") == "red"
    from cantina_os.services.eye_light_controller_service import EyePattern
    assert EyePattern(eye_payload["pattern"]) is EyePattern.CUSTOM

    result_payload = _emitted_payload(mock_event_bus, EventTopics.INTENT_EXECUTION_RESULT)
    result = result_payload.get("result", {})
    assert result.get("color") == "red"
    assert result.get("pattern") == "custom"  # UPDATED 2026-09-17: "solid" is not an EyePattern
    assert result.get("intensity") == 0.5

@pytest.mark.asyncio
async def test_unknown_intent_handling(router_service, mock_event_bus):
    """Test that unknown intents are handled gracefully.

    No CLI_COMMAND is emitted, but an INTENT_EXECUTION_RESULT reporting the
    failure IS emitted (see `_handle_intent`'s `else` branch for unknown
    intents) - the event bus is not left silent.
    """
    intent_payload = IntentPayload(
        intent_name="unknown_function",
        parameters={"param1": "value1"},
        original_text="Do something weird",
        conversation_id="test_conv_202"
    )

    await router_service._handle_intent(intent_payload.dict())

    assert mock_event_bus.emit.call_count == 1
    result_payload = _emitted_payload(mock_event_bus, EventTopics.INTENT_EXECUTION_RESULT)
    assert result_payload is not None
    assert result_payload.get("success") is False
    assert "No handler for intent" in result_payload.get("error_message", "")

@pytest.mark.asyncio
async def test_play_music_without_a_track_still_plays(router_service, mock_event_bus):
    """CHANGED 2026-09-17: a missing `track` is not a missing parameter.

    "Play some music" names no track on purpose, and the fast router now sends `track: None`
    for exactly that case. This used to return {"success": False, "message": "No track
    specified"} and play nothing. The command is dispatched with no arguments and
    MusicControllerService - the only component that knows the real library - picks.
    """
    intent_payload = IntentPayload(
        intent_name="play_music",
        parameters={},  # No track named
        original_text="Play some music",
        conversation_id="test_conv_303"
    )

    await router_service._handle_intent(intent_payload.dict())

    cli_payloads = _emitted_payloads(mock_event_bus, EventTopics.CLI_COMMAND)
    assert len(cli_payloads) == 1
    assert cli_payloads[0].get("command") == "play"
    assert cli_payloads[0].get("args") == []

    result_payload = _emitted_payload(mock_event_bus, EventTopics.INTENT_EXECUTION_RESULT)
    assert result_payload is not None
    assert result_payload.get("success") is True

@pytest.mark.asyncio
async def test_set_eye_color_missing_color(router_service, mock_event_bus):
    """Test that set_eye_color with missing color is handled gracefully."""
    intent_payload = IntentPayload(
        intent_name="set_eye_color",
        parameters={"pattern": "blink", "intensity": 0.9},  # Missing color parameter
        original_text="Make your eyes blink",
        conversation_id="test_conv_404"
    )

    await router_service._handle_intent(intent_payload.dict())

    assert mock_event_bus.emit.call_count == 1
    result_payload = _emitted_payload(mock_event_bus, EventTopics.INTENT_EXECUTION_RESULT)
    assert result_payload is not None
    assert result_payload.get("success") is False

@pytest.mark.asyncio
async def test_event_subscription(router_service, mock_event_bus):
    """Test that the service subscribes to the events it needs.

    `_setup_subscriptions` fires the subscribe calls via `asyncio.create_task`
    rather than awaiting them directly, so the test must yield control back to
    the event loop before asserting.

    MUSIC_PLAYBACK_STARTED was added 2026-09-17: the play_music execution result has to
    report the track that actually started, not the one that was requested.
    """
    with patch.object(BaseService, 'subscribe', autospec=True) as mock_subscribe:
        await router_service._setup_subscriptions()
        # Let the scheduled tasks run
        await asyncio.sleep(0)

        subscribed_topics = [c.args[1] for c in mock_subscribe.call_args_list]
        assert EventTopics.MUSIC_PLAYBACK_STARTED in subscribed_topics

        mock_subscribe.assert_any_call(
            router_service,
            EventTopics.INTENT_DETECTED,
            router_service._handle_intent
        )
