"""
A long reply must neither be generated nor mis-timed.

## The defect this pins (observed live 2026-09-17 10:56:45 → 10:57:18)

    10:56:45.036  Emitting LLM_RESPONSE event with 555 chars
    10:56:45.040  Waiting for speech synthesis to complete (timeout: 25.0s)
    10:57:10.041  ERROR Timeout waiting for speech synthesis to complete for step 537b6734…
    10:57:10.292  Plan 14e65ac8… completed successfully on layer foreground
    10:57:18.719  Speech generation complete for text: Ha! CRAZY?! I prefer to think of it as…
    10:57:18.719  WARNING No waiting event found for speech_id: 537b6734…

Three separate wrongs, 33 seconds of audio against a flat 25 second wait:

1. **The reply should not have been 555 characters.** R3X is a voice droid at a cantina; the
   persona already asks for 2-4 sentences and nothing enforced it.
2. **The wait should scale with the text.** Synthesis time is roughly linear in characters, so
   a constant timeout is wrong at both ends - too tight for long text, needlessly slack for
   short.
3. **The plan was marked "completed successfully"** while the audio was still playing, and the
   real completion 8 seconds later was logged as an orphan. The step had already been retired;
   that is expected, not anomalous, and must not read as an error.
"""

import asyncio
import logging
import time
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock

import pytest
from pyee.asyncio import AsyncIOEventEmitter

from cantina_os.core.event_topics import EventTopics
from cantina_os.services.claude_service.claude_service import ClaudeService
from cantina_os.services.timeline_executor_service.timeline_executor_service import (
    TimelineExecutorService,
)

#: The exact reply that broke the timeline, so the numbers in the assertions are the real ones.
THE_555_CHAR_REPLY = "Ha! CRAZY?! I prefer to think of it as ENTHUSIASTIC! " + "x" * 503

#: Measured: ElevenLabs Flash v2.5 streamed the 555-char reply in 33.7 s wall clock
#: (10:56:45.041 → 10:57:18.717), i.e. ~61 ms per character including playback.
MEASURED_555_CHAR_SECONDS = 33.7


@pytest.fixture(autouse=True)
def mock_external_apis():
    """Override conftest's autouse fixture, which raises on deepgram-sdk 5.x."""
    yield {}


# =========================================================================================
# (a) The reply itself is capped
# =========================================================================================

class StubAnthropicMessages:
    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)

        class _Text:
            type = "text"
            text = "Short and snappy!"

        class _Usage:
            input_tokens = 100
            output_tokens = 5
            cache_creation_input_tokens = 0
            cache_read_input_tokens = 0

        class _Response:
            content = [_Text()]
            usage = _Usage()

        return _Response()


class StubAnthropic:
    def __init__(self) -> None:
        self.messages = StubAnthropicMessages()


#: A spoken reply of ~250 characters is 2-3 sentences, which at ~61 ms/char is ~15 s of audio -
#: already long for a single conversational turn. 160 output tokens is ~600 characters, so it
#: is a backstop against a runaway generation, not the primary control; the prompt is.
SPOKEN_REPLY_TOKEN_CEILING = 160

#: What the persona must commit to in writing, so the instruction and the backstop agree.
PERSONA_CHAR_CAP = 250


@pytest.fixture
async def claude():
    bus = AsyncIOEventEmitter()
    svc = ClaudeService(
        bus, {"ANTHROPIC_API_KEY": "test-key-not-used", "STREAMING": False}
    )
    await svc.start()
    stub = StubAnthropic()
    svc._client = stub  # type: ignore[assignment]
    await asyncio.sleep(0.1)
    yield svc, stub, bus
    try:
        await svc.stop()
    except Exception:
        pass


class TestSpokenRepliesAreCapped:
    async def test_max_tokens_is_a_spoken_reply_budget_not_a_chatbot_one(self, claude):
        """1024 output tokens is ~4,000 characters, i.e. four minutes of speech."""
        svc, stub, bus = claude
        bus.emit(
            EventTopics.VOICE_LISTENING_STOPPED.value,
            {"transcript": "why do you sound so crazy?", "conversation_id": "t1"},
        )
        await asyncio.sleep(0.5)

        assert stub.messages.calls, "no request was made"
        max_tokens = stub.messages.calls[0]["max_tokens"]
        assert max_tokens <= SPOKEN_REPLY_TOKEN_CEILING, (
            f"a spoken turn asked for max_tokens={max_tokens}; this is a voice droid, "
            f"not a chatbot"
        )

    async def test_the_persona_states_the_limit_in_characters(self, claude):
        """An instruction the model can follow needs a number, not an adjective."""
        svc, _stub, _bus = claude
        prompt = svc._config["SYSTEM_PROMPT"]
        assert str(PERSONA_CHAR_CAP) in prompt, (
            "the persona does not state a character limit for spoken replies"
        )
        assert "2-3 sentences" in prompt, (
            "the persona does not state a sentence limit for spoken replies"
        )


# =========================================================================================
# (b) The timeline's wait scales, and a late completion is not an error
# =========================================================================================

def _completion(text: str, clip_id: str) -> Dict[str, Any]:
    """A valid SPEECH_GENERATION_COMPLETE payload, as ElevenLabsService emits it."""
    return {
        "success": True,
        "text": text,
        "audio_length_seconds": len(text) * 0.06,
        "clip_id": clip_id,
    }


@pytest.fixture
def timeline():
    """A real TimelineExecutorService with the bus and status reporting stubbed."""
    svc = TimelineExecutorService(AsyncIOEventEmitter())
    svc.emit = AsyncMock()  # type: ignore[assignment]
    svc._emit_status = AsyncMock()  # type: ignore[assignment]
    svc._current_music_playing = False
    svc._audio_ducked = False
    return svc


class TestSpeechTimeoutScalesWithLength:
    def test_the_555_char_reply_gets_more_than_its_measured_33_seconds(self, timeline):
        budget = timeline._speech_timeout_for(THE_555_CHAR_REPLY)
        assert budget > MEASURED_555_CHAR_SECONDS, (
            f"555 chars took {MEASURED_555_CHAR_SECONDS}s live but the budget is {budget}s; "
            "the plan would be declared complete mid-sentence again"
        )

    def test_a_short_reply_does_not_get_25_seconds(self, timeline):
        """The 55-char confirmation streamed in 3.3 s. A flat 25 s is slack, not safety."""
        budget = timeline._speech_timeout_for("Now spinning up Cantina Band for you.")
        assert budget < 25.0

    def test_the_budget_is_monotonic_in_length(self, timeline):
        budgets = [timeline._speech_timeout_for("x" * n) for n in (10, 100, 555, 2000)]
        assert budgets == sorted(budgets)
        assert len(set(budgets)) == 4

    def test_empty_text_still_gets_a_usable_budget(self, timeline):
        assert timeline._speech_timeout_for("") >= 5.0

    async def test_the_speak_step_reports_the_budget_it_used(self, timeline):
        """Observable contract: the step says what it waited for, so it can be asserted."""
        step = {"id": "step-555", "text": THE_555_CHAR_REPLY}

        async def complete_it():
            await asyncio.sleep(0.05)
            await timeline._handle_speech_generation_complete(
                _completion(THE_555_CHAR_REPLY, "step-555")
            )

        asyncio.create_task(complete_it())
        success, details = await timeline._execute_speak_step(step, "plan-1")

        assert success
        assert details["timeout_s"] == pytest.approx(
            timeline._speech_timeout_for(THE_555_CHAR_REPLY)
        )
        assert details["timeout_s"] > MEASURED_555_CHAR_SECONDS


class TestLateCompletionIsNotAnOrphan:
    async def test_a_retired_step_does_not_warn(self, timeline, caplog):
        """The 10:57:18.719 warning. The step had timed out and been cleaned up 8 s earlier;
        the audio finishing afterwards is late, not lost."""
        step = {"id": "step-late", "text": "hello"}
        timeline._config.speech_wait_base_s = 0.05
        timeline._config.speech_wait_per_char_s = 0.0

        await timeline._execute_speak_step(step, "plan-1")  # times out, retires the step

        caplog.clear()
        with caplog.at_level(logging.DEBUG, logger="cantina_os.timeline_executor_service"):
            await timeline._handle_speech_generation_complete(
                _completion("hello", "step-late")
            )

        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert not warnings, (
            "a late completion for a retired step still logs as a problem: "
            f"{[r.getMessage() for r in warnings]}"
        )
        assert any(
            "step-late" in r.getMessage() for r in caplog.records
        ), "the late completion is not recorded at all; it should still be traceable"

    async def test_a_genuinely_unknown_speech_id_still_warns(self, timeline, caplog):
        """Don't trade one silent failure for another: an id nobody ever waited on is a real
        anomaly and must stay visible."""
        with caplog.at_level(logging.DEBUG, logger="cantina_os.timeline_executor_service"):
            await timeline._handle_speech_generation_complete(
                _completion("hello", "never-seen-before")
            )

        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warnings, "an unknown speech_id must still be reported"
