"""
The microphone must not record R3X, and silence must not start a turn.

## The defect this pins (observed live 2026-09-17 10:56:21 → 10:56:36)

    10:56:16.431  Speech started  (R3X begins a 311-char reply; it plays until 10:56:33.589)
    10:56:21.163  Mouse click detected - starting recording   <-- mic opens over R3X's voice
    10:56:23.292  Audio capture ended (3 chunks sent)
    10:56:23.533  Final transcript: (empty)
    10:56:23.533  WARNING Received empty transcript in VOICE_LISTENING_STOPPED event
    10:56:36.666  Final transcript: Spin some beats and chant with

"Spin some beats and chant with" is not something Brandon said. It is R3X's own TTS, captured
by the Studio Display microphone during the 10:56:21-23 window, buffered by Deepgram's
persistent WebSocket, and finalized into the *next* turn - where it was concatenated with what
Brandon actually said and sent to Claude as if he had said all of it.

Two fixes, both here:

(a) The mic never opens while R3X is speaking. Of the available options - refuse the start,
    drop transcript segments that arrived during speech, or defer the open until speech ends -
    refusing the start is the least invasive: it needs no new state in the transcript path, and
    it removes the capture rather than trying to clean up after it. The lifecycle events are
    already on the bus (`SPEECH_GENERATION_STARTED` / `SPEECH_GENERATION_COMPLETE`), which is
    how `EyeLightControllerService` already tracks the same thing.

(b) An empty final transcript is not a turn. It is marked as such on the event, so no consumer
    has to infer it from an empty string, and no Claude call is made.
"""

import asyncio
import os
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pyee.asyncio import AsyncIOEventEmitter

from cantina_os.core.event_topics import EventTopics

os.environ.setdefault("DEEPGRAM_API_KEY", "test-key-not-used")

from cantina_os.services.claude_service.claude_service import ClaudeService
from cantina_os.services.deepgram_direct_mic_service import DeepgramDirectMicService

#: R3X's own words, exactly as they came back through the microphone.
R3X_OWN_VOICE = "Spin some beats and chant with"


@pytest.fixture(autouse=True)
def mock_external_apis():
    """Override conftest's autouse fixture, which raises on deepgram-sdk 5.x."""
    yield {}


class Probe:
    def __init__(self) -> None:
        self.events: List[Any] = []

    def handler(self, payload: Any) -> None:
        self.events.append(payload if isinstance(payload, dict) else payload.model_dump())

    @property
    def count(self) -> int:
        return len(self.events)


@pytest.fixture
async def mic():
    """A real DeepgramDirectMicService with PyAudio, the socket and the loop stubbed.

    Async so the service is constructed inside the test's own event loop - its ``__init__``
    captures ``asyncio.get_event_loop()`` for marshalling transcripts off the audio thread.
    """
    bus = AsyncIOEventEmitter()
    with patch(
        "cantina_os.services.deepgram_direct_mic_service.DeepgramClient", MagicMock()
    ), patch("cantina_os.services.deepgram_direct_mic_service.pyaudio", MagicMock()):
        svc = DeepgramDirectMicService(bus, {"DEEPGRAM_API_KEY": "test-key-not-used"})

    svc.emit = AsyncMock()  # type: ignore[assignment]
    svc._connection_open = True
    svc._start_microphone = AsyncMock()  # type: ignore[assignment]
    svc._stop_microphone = AsyncMock()  # type: ignore[assignment]
    yield svc


def _emitted_topics(svc) -> List[str]:
    return [
        c.args[0].value if hasattr(c.args[0], "value") else str(c.args[0])
        for c in svc.emit.await_args_list
    ]


def _payload_for(svc, topic) -> Optional[Dict[str, Any]]:
    for call in svc.emit.await_args_list:
        name = call.args[0].value if hasattr(call.args[0], "value") else str(call.args[0])
        if name == topic.value:
            payload = call.args[1]
            return payload if isinstance(payload, dict) else payload.model_dump()
    return None


# =========================================================================================
# (a) The mic never opens over R3X's own voice
# =========================================================================================

class TestMicRefusesToRecordOverSpeech:
    async def test_recording_start_is_refused_while_r3x_is_speaking(self, mic):
        """The 10:56:21 click. R3X had been talking since 10:56:16 and had 12 s left."""
        await mic._handle_speech_started({"text": "Hey there! I'm just hanging out..."})
        await mic._handle_mic_recording_start({})

        mic._start_microphone.assert_not_awaited()
        assert EventTopics.VOICE_LISTENING_STARTED.value not in _emitted_topics(mic), (
            "a turn was opened for audio that is R3X's own voice"
        )

    async def test_recording_start_works_once_speech_has_finished(self, mic):
        await mic._handle_speech_started({"text": "..."})
        await mic._handle_speech_ended({"success": True, "text": "..."})
        await mic._handle_mic_recording_start({})

        mic._start_microphone.assert_awaited()
        assert EventTopics.VOICE_LISTENING_STARTED.value in _emitted_topics(mic)

    async def test_recording_start_works_when_nothing_is_speaking(self, mic):
        await mic._handle_mic_recording_start({})
        mic._start_microphone.assert_awaited()

    async def test_a_completion_without_a_start_cannot_wedge_the_mic(self, mic):
        """Fail open: an unpaired COMPLETE must leave the mic usable, not latched shut."""
        await mic._handle_speech_ended({"success": True, "text": "..."})
        await mic._handle_mic_recording_start({})
        mic._start_microphone.assert_awaited()

    async def test_a_failed_generation_also_releases_the_mic(self, mic):
        """SPEECH_GENERATION_COMPLETE with success=False still means no more audio."""
        await mic._handle_speech_started({"text": "..."})
        await mic._handle_speech_ended({"success": False, "error": "boom"})
        await mic._handle_mic_recording_start({})
        mic._start_microphone.assert_awaited()

    async def test_the_full_overlap_replayed(self, mic):
        """The live sequence end to end: no turn is opened, so R3X's voice is never captured."""
        await mic._handle_speech_started({"text": "Hey there! I'm just hanging out..."})
        await mic._handle_mic_recording_start({})   # 10:56:21.163
        await mic._handle_mic_recording_stop({})    # 10:56:23.279

        topics = _emitted_topics(mic)
        assert EventTopics.VOICE_LISTENING_STARTED.value not in topics
        assert EventTopics.VOICE_LISTENING_STOPPED.value not in topics, (
            "a stop was published for a recording that never started"
        )


# =========================================================================================
# (b) An empty final transcript is not a turn
# =========================================================================================

class TestEmptyTranscriptIsNotATurn:
    async def test_the_stop_event_says_there_is_no_transcript(self, mic):
        """A consumer must not have to infer "no turn" from an empty string."""
        await mic._handle_mic_recording_start({})
        mic.emit.reset_mock()
        mic._is_listening = True
        mic._current_transcription = "   "

        await mic._handle_mic_recording_stop({})

        payload = _payload_for(mic, EventTopics.VOICE_LISTENING_STOPPED)
        assert payload is not None, (
            "VOICE_LISTENING_STOPPED must still be published - the ducking and eye state "
            "opened on VOICE_LISTENING_STARTED have to be closed"
        )
        assert payload["transcript"] == ""
        assert payload["has_transcript"] is False

    async def test_a_real_transcript_is_marked_as_one(self, mic):
        await mic._handle_mic_recording_start({})
        mic.emit.reset_mock()
        mic._is_listening = True
        mic._current_transcription = "play some music"

        await mic._handle_mic_recording_stop({})

        payload = _payload_for(mic, EventTopics.VOICE_LISTENING_STOPPED)
        assert payload["transcript"] == "play some music"
        assert payload["has_transcript"] is True


class StubAnthropicMessages:
    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)

        class _Text:
            type = "text"
            text = "hello"

        class _Usage:
            input_tokens = 1
            output_tokens = 1
            cache_creation_input_tokens = 0
            cache_read_input_tokens = 0

        class _Response:
            content = [_Text()]
            usage = _Usage()

        return _Response()


class StubAnthropic:
    def __init__(self) -> None:
        self.messages = StubAnthropicMessages()


@pytest.fixture
async def claude():
    bus = AsyncIOEventEmitter()
    svc = ClaudeService(bus, {"ANTHROPIC_API_KEY": "test-key", "STREAMING": False})
    await svc.start()
    stub = StubAnthropic()
    svc._client = stub  # type: ignore[assignment]
    await asyncio.sleep(0.1)
    yield svc, stub, bus
    try:
        await svc.stop()
    except Exception:
        pass


class TestEmptyTranscriptReachesNoLLM:
    async def test_no_claude_call_and_no_reply(self, claude):
        svc, stub, bus = claude
        llm = Probe()
        bus.on(EventTopics.LLM_RESPONSE.value, llm.handler)

        bus.emit(
            EventTopics.VOICE_LISTENING_STOPPED.value,
            {"transcript": "", "has_transcript": False, "conversation_id": "t1"},
        )
        await asyncio.sleep(0.4)

        assert stub.messages.calls == [], "an empty transcript reached the Claude API"
        assert llm.count == 0, "an empty transcript produced a spoken reply"

    async def test_a_real_transcript_still_gets_through(self, claude):
        """Don't trade the empty turn for a deaf one."""
        svc, stub, bus = claude
        bus.emit(
            EventTopics.VOICE_LISTENING_STOPPED.value,
            {"transcript": "hello there", "has_transcript": True, "conversation_id": "t2"},
        )
        await asyncio.sleep(0.4)
        assert len(stub.messages.calls) == 1
