"""The turn id must survive from voice capture to LLM_RESPONSE.

LatencyTrackerService keys every metric on `conversation_id`: it opens a record on
VOICE_LISTENING_STARTED and closes it on LLM_RESPONSE / SPEECH_GENERATION_*. Before
2026-09-17 those two ids came from different places -

  * deepgram_direct_mic_service minted a uuid per turn, put it on VOICE_LISTENING_STARTED,
    and then emitted VOICE_LISTENING_STOPPED as `{"transcript": ...}` with no id at all;
  * ClaudeService minted its own id in reset_conversation() and stamped LLM_RESPONSE with it.

So `conversation_id not in self._conversation_metrics` was true for every close event and the
tracker silently dropped 100% of its measurements. CLIService._handle_llm_response has the same
guard, so the CLI also swallowed R3X's replies.

These tests pin the fix: the capture service owns the turn id, and everything downstream adopts
it rather than inventing one.
"""

import asyncio
import time
from typing import Any, Dict, List

import pytest
from pyee.asyncio import AsyncIOEventEmitter

from cantina_os.core.event_topics import EventTopics
from cantina_os.services.claude_service.claude_service import ClaudeService
from cantina_os.services.latency_tracker_service.latency_tracker_service import (
    LatencyTrackerService,
)


class _Probe:
    # NOTE: pyee's AsyncIOEventEmitter.emit() is SYNCHRONOUS - it returns bool and schedules
    # coroutine handlers on the loop. Never `await bus.emit(...)`; it raises
    # "object bool can't be used in 'await' expression".
    def __init__(self) -> None:
        self.payloads: List[Dict[str, Any]] = []

    async def handler(self, payload):
        self.payloads.append(payload)


class _StubMessages:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)

        class _Text:
            type = "text"
            text = "Already spinning, pal!"

        class _Usage:
            input_tokens = 10
            output_tokens = 5
            cache_creation_input_tokens = 0
            cache_read_input_tokens = 0

        class _Response:
            content = [_Text()]
            usage = _Usage()

        return _Response()


class _StubAnthropic:
    def __init__(self):
        self.messages = _StubMessages()


# ---------------------------------------------------------------------------
# The two emitters must put the turn id on VOICE_LISTENING_STOPPED.
# ---------------------------------------------------------------------------


def test_mic_service_stopped_payload_includes_conversation_id():
    """Source-level pin: the emit must name conversation_id, not just transcript."""
    import inspect

    from cantina_os.services import deepgram_direct_mic_service as mod

    src = inspect.getsource(mod.DeepgramDirectMicService._handle_mic_recording_stop)
    assert "VOICE_LISTENING_STOPPED" in src
    assert "conversation_id" in src, (
        "VOICE_LISTENING_STOPPED must carry the turn's conversation_id; without it the "
        "latency tracker cannot close the record it opened on VOICE_LISTENING_STARTED."
    )


def test_cli_service_stopped_payload_includes_conversation_id():
    import inspect

    from cantina_os.services import cli_service as mod

    src = inspect.getsource(mod.CLIService)
    idx = src.index("VOICE_LISTENING_STOPPED")
    window = src[idx : idx + 300]
    assert "conversation_id" in window, (
        "The typed `done` path must carry the conversation_id minted by `record`."
    )


# ---------------------------------------------------------------------------
# ClaudeService must adopt the incoming id rather than minting its own.
# ---------------------------------------------------------------------------


@pytest.fixture
async def claude_rig():
    bus = AsyncIOEventEmitter()
    probe = _Probe()
    bus.on(EventTopics.LLM_RESPONSE.value, probe.handler)

    claude = ClaudeService(
        bus,
        {
            "ANTHROPIC_API_KEY": "test-key-not-used",
            "STREAMING": False,
            "FAST_ROUTER_WAIT_S": 0.1,
        },
    )
    await claude.start()
    claude._client = _StubAnthropic()  # type: ignore[assignment]
    await asyncio.sleep(0.05)

    yield bus, claude, probe

    await asyncio.sleep(0.1)
    try:
        await claude.stop()
    except Exception:
        pass


async def test_llm_response_carries_the_capture_services_turn_id(claude_rig):
    bus, claude, probe = claude_rig

    turn_id = "turn-from-capture-service-0001"
    claude._current_conversation_id = "some-unrelated-id-claude-made-up"

    bus.emit(
        EventTopics.VOICE_LISTENING_STOPPED.value,
        {"transcript": "hey Rex play some music", "conversation_id": turn_id},
    )

    for _ in range(80):
        if probe.payloads:
            break
        await asyncio.sleep(0.05)

    assert probe.payloads, "ClaudeService emitted no LLM_RESPONSE"
    assert probe.payloads[0]["conversation_id"] == turn_id, (
        "LLM_RESPONSE must be stamped with the turn id the capture service minted, "
        "otherwise LatencyTrackerService drops the measurement."
    )


async def test_absent_conversation_id_does_not_clobber_the_existing_one(claude_rig):
    """Fail-safe: an id-less VOICE_LISTENING_STOPPED must not blank the turn id."""
    bus, claude, probe = claude_rig

    claude._current_conversation_id = "pre-existing-id"
    bus.emit(
        EventTopics.VOICE_LISTENING_STOPPED.value, {"transcript": "hello there"}
    )

    for _ in range(80):
        if probe.payloads:
            break
        await asyncio.sleep(0.05)

    assert probe.payloads
    assert probe.payloads[0]["conversation_id"] == "pre-existing-id"


# ---------------------------------------------------------------------------
# With the ids unified, the tracker actually records.
# ---------------------------------------------------------------------------


async def test_latency_tracker_records_a_full_turn_when_ids_match():
    bus = AsyncIOEventEmitter()
    tracker = LatencyTrackerService(bus, {})
    await tracker.start()
    await asyncio.sleep(0.05)

    turn_id = "unified-turn-id"
    t0 = time.time()

    bus.emit(
        EventTopics.VOICE_LISTENING_STARTED.value,
        {"conversation_id": turn_id, "timestamp": t0},
    )
    await asyncio.sleep(0.02)
    bus.emit(
        EventTopics.TRANSCRIPTION_FINAL.value,
        {"conversation_id": turn_id, "timestamp": t0 + 0.4, "text": "play some music"},
    )
    await asyncio.sleep(0.02)
    bus.emit(
        EventTopics.LLM_RESPONSE.value,
        {"conversation_id": turn_id, "timestamp": t0 + 1.2, "text": "Spinning it up!"},
    )
    await asyncio.sleep(0.02)
    bus.emit(
        EventTopics.SPEECH_GENERATION_STARTED.value,
        {"conversation_id": turn_id, "timestamp": t0 + 1.3},
    )
    await asyncio.sleep(0.02)
    bus.emit(
        EventTopics.SPEECH_GENERATION_COMPLETE.value,
        {"conversation_id": turn_id, "timestamp": t0 + 2.0},
    )
    await asyncio.sleep(0.1)

    summary = tracker.get_conversation_summary(turn_id)
    assert summary is not None, (
        "The tracker recorded nothing even though every event carried the same "
        "conversation_id - the unification did not take."
    )
    assert summary["conversation_id"] == turn_id

    await tracker.stop()


async def test_latency_tracker_drops_the_turn_when_ids_disagree():
    """Characterises the old behaviour, so a regression is visible rather than silent."""
    bus = AsyncIOEventEmitter()
    tracker = LatencyTrackerService(bus, {})
    await tracker.start()
    await asyncio.sleep(0.05)

    t0 = time.time()
    bus.emit(
        EventTopics.VOICE_LISTENING_STARTED.value,
        {"conversation_id": "id-from-deepgram", "timestamp": t0},
    )
    await asyncio.sleep(0.02)
    bus.emit(
        EventTopics.LLM_RESPONSE.value,
        {"conversation_id": "id-from-claude", "timestamp": t0 + 1.2, "text": "hi"},
    )
    await asyncio.sleep(0.1)

    assert tracker.get_conversation_summary("id-from-claude") is None
    started = tracker.get_conversation_summary("id-from-deepgram")
    assert started is None or started.get("llm_complete") in (None, 0), (
        "A mismatched LLM_RESPONSE must not be attributed to the started turn."
    )

    await tracker.stop()
