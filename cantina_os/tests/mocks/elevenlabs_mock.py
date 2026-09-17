"""Mock implementation of ElevenLabs service."""
from typing import Any, Callable, Dict, Optional, Tuple
import asyncio
import tempfile
from .base_mock import BaseMockService
from cantina_os.event_bus import EventBus
from cantina_os.core.event_topics import EventTopics


class ElevenLabsMock(BaseMockService):
    """Mock service for ElevenLabs TTS.

    Provides both:
    - an event-bus-driven mode (``_handle_synthesis_request``, subscribed to
      SPEECH_SYNTHESIS_REQUESTED) for services that talk to it over the event bus, and
    - a direct API (``generate_speech``, ``play_audio``, ``text_to_speech``) for tests
      that exercise the mock's TTS/playback simulation directly.
    """

    def __init__(self, event_bus: EventBus) -> None:
        """Initialize the ElevenLabs mock service."""
        super().__init__()
        self.event_bus = event_bus
        self.simulate_error_flag: bool = False

        self._temp_dir: Optional[tempfile.TemporaryDirectory] = None
        self.is_playing: bool = False
        self._playback_task: Optional[asyncio.Task] = None
        self._playback_started_event: asyncio.Event = asyncio.Event()

        self._on_audio_start: Optional[Callable[[], None]] = None
        self._on_audio_complete: Optional[Callable[[], None]] = None
        self._on_error: Optional[Callable[[str], None]] = None

    async def initialize(self) -> None:
        """Initialize the mock service."""
        await super().initialize()
        self.record_call('initialize')
        self._temp_dir = tempfile.TemporaryDirectory()
        self.event_bus.on(EventTopics.SPEECH_SYNTHESIS_REQUESTED, self._handle_synthesis_request)

    async def shutdown(self) -> None:
        """Shutdown the mock service and cleanup resources."""
        if self.is_playing:
            await self.stop_playback()

        if self._temp_dir is not None:
            self._temp_dir.cleanup()
            self._temp_dir = None

        await super().shutdown()
        self.record_call('shutdown')

    async def _handle_synthesis_request(self, payload: Dict[str, Any]) -> None:
        """Handle a speech synthesis request."""
        self.record_call('_handle_synthesis_request', payload)

        if self.simulate_error_flag:
            self.event_bus.emit(EventTopics.SERVICE_ERROR, {
                "service": "elevenlabs",
                "error": "Simulated error",
                "conversation_id": payload.get("conversation_id")
            })
            return

        # Simulate processing time
        await asyncio.sleep(0.1)

        # Emit synthesis started
        self.event_bus.emit(EventTopics.SPEECH_SYNTHESIS_STARTED, {
            "text": payload.get("text"),
            "conversation_id": payload.get("conversation_id")
        })

        # Simulate synthesis
        await asyncio.sleep(0.2)

        # Emit synthesis completed
        self.event_bus.emit(EventTopics.SPEECH_SYNTHESIS_COMPLETED, {
            "conversation_id": payload.get("conversation_id"),
            "audio_data": b"mock_audio_data"  # Mock audio data
        })

    async def generate_speech(self, text: str, voice_id: Optional[str] = None, **kwargs: Any) -> Tuple[bytes, str]:
        """Simulate generating speech audio for the given text.

        Returns the configured 'audio_data' response (set via
        ``set_response('audio_data', ...)``) and a WAV mime type.
        """
        self.record_call('generate_speech', text, voice_id)

        if self.simulate_error_flag:
            error_msg = "Simulated error"
            if self._on_error:
                self._on_error(error_msg)
            raise RuntimeError(error_msg)

        audio_data = self.get_response('audio_data') or b""
        return audio_data, "audio/wav"

    def on_audio_start(self, callback: Callable[[], None]) -> None:
        """Register a callback invoked when playback starts."""
        self.record_call('on_audio_start', callback)
        self._on_audio_start = callback

    def on_audio_complete(self, callback: Callable[[], None]) -> None:
        """Register a callback invoked when playback completes (or is stopped)."""
        self.record_call('on_audio_complete', callback)
        self._on_audio_complete = callback

    def on_error(self, callback: Callable[[str], None]) -> None:
        """Register an error callback."""
        self.record_call('on_error', callback)
        self._on_error = callback

    async def play_audio(self, audio_data: bytes) -> None:
        """Simulate playing back audio data.

        Duration is proportional to the size of ``audio_data`` (treated as
        44.1kHz 8-bit mono for simulation purposes), capped so tests stay fast.
        """
        self.record_call('play_audio', audio_data)

        self._playback_started_event.clear()
        self._playback_task = asyncio.current_task()
        self.is_playing = True
        self._playback_started_event.set()

        if self._on_audio_start:
            self._on_audio_start()

        try:
            duration = min(len(audio_data) / 44100, 2.0)
            await asyncio.sleep(duration)
        except asyncio.CancelledError:
            pass
        finally:
            self.is_playing = False
            self._playback_task = None
            if self._on_audio_complete:
                self._on_audio_complete()

    async def wait_for_playback_start(self, timeout: float = 1.0) -> bool:
        """Wait until playback has started, returning True if it did within timeout."""
        try:
            await asyncio.wait_for(self._playback_started_event.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def stop_playback(self) -> None:
        """Stop any in-progress playback."""
        self.record_call('stop_playback')

        task = self._playback_task
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        self.is_playing = False
        self._playback_task = None

    async def text_to_speech(self, text: str, voice_id: Optional[str] = None) -> None:
        """Generate speech for text and play it back."""
        self.record_call('text_to_speech', text, voice_id)
        audio_data, _mime_type = await self.generate_speech(text, voice_id)
        await self.play_audio(audio_data)

    def simulate_error(self, error_msg: str) -> None:
        """Simulate an error condition."""
        self.record_call('simulate_error', error_msg)
        self.simulate_error_flag = True
        if self._on_error:
            self._on_error(error_msg)
