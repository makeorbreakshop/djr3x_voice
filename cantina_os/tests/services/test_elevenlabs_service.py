"""
Tests for the ElevenLabsService
"""

import asyncio
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from pydantic import ValidationError

from cantina_os.event_payloads import (
    SpeechGenerationRequestPayload,
    SpeechGenerationCompletePayload,
    BaseEventPayload,
    ServiceStatus,
    LLMResponsePayload
)
from cantina_os.core.event_topics import EventTopics
from cantina_os.services.elevenlabs_service import (
    ElevenLabsService,
    SpeechPlaybackMethod,
    ElevenLabsConfig
)


@pytest.fixture
def mock_api_key():
    """Provide a mock API key for testing."""
    return "mock-api-key"


@pytest.fixture
def mock_audio_data():
    """Provide mock audio data for testing."""
    return b"mock-audio-data"


@pytest.fixture
def mock_event_bus():
    """Provide a mock event bus for testing."""
    event_bus = AsyncMock()
    event_bus.emit = AsyncMock()
    event_bus.on = MagicMock()
    return event_bus


@pytest.fixture
def test_config(mock_api_key):
    """Create a test configuration."""
    return {
        "ELEVENLABS_API_KEY": mock_api_key,
        "VOICE_ID": "test-voice-id",
        "MODEL_ID": "test-model-id",
        "PLAYBACK_METHOD": SpeechPlaybackMethod.SYSTEM,  # Use system to avoid sounddevice dependency
        "STABILITY": 0.71,
        "SIMILARITY_BOOST": 0.5,
        "ENABLE_AUDIO_NORMALIZATION": True
    }


@pytest.fixture
async def service(mock_event_bus, test_config):
    """Create an ElevenLabsService instance for testing."""
    service = ElevenLabsService(
        event_bus=mock_event_bus,
        config=test_config,
        name="TestElevenLabsService"
    )
    yield service
    # Ensure service is stopped after test
    if service._status != ServiceStatus.STOPPED:
        await service.stop()


class TestElevenLabsService:
    """Tests for the ElevenLabsService."""

    @pytest.mark.asyncio
    async def test_initialization(self, service, test_config):
        """Test that the service initializes correctly."""
        # Verify initial properties from the config
        assert service._config.api_key == "mock-api-key"
        assert service._config.voice_id == "test-voice-id"
        assert service._config.model_id == "test-model-id"
        # ElevenLabsService.__init__ now force-overrides playback_method to
        # STREAMING regardless of config (see elevenlabs_service.py:119-121:
        # "Force streaming playback method regardless of config"), so the
        # SYSTEM value in test_config is intentionally ignored.
        assert service._config.playback_method == SpeechPlaybackMethod.STREAMING

        # Check status enum instead of string
        assert service._status == ServiceStatus.INITIALIZING

    @pytest.mark.asyncio
    @pytest.mark.skip(
        reason=(
            "Production bug, not a stale test: ElevenLabsService defines "
            "_cleanup() (elevenlabs_service.py:325) to close self._client "
            "and remove self._temp_dir, but BaseService.stop() "
            "(base_service.py:97-106) calls self._stop(), never "
            "self._cleanup(). ElevenLabsService does not override _stop(), "
            "so it inherits BaseService's no-op _stop() (base_service.py:"
            "108-110) and _cleanup() is dead code -- stop() never closes "
            "the HTTP client or removes the temp dir. Patching _cleanup() "
            "here (as the original test did) papers over this: the patch "
            "target is simply never invoked by the real lifecycle. Left "
            "failing/skipped per instructions rather than silently "
            "reworking the assertions to match the (buggy) real behavior."
        )
    )
    async def test_start_stop(self, service):
        """Test the service start and stop lifecycle."""
        # Create a mock client
        mock_client = MagicMock()
        mock_client.aclose = AsyncMock()
        
        # Define a patched start function that assigns our mock client
        async def patched_start():
            service._client = mock_client
            service._temp_dir = tempfile.TemporaryDirectory()
            service._status = ServiceStatus.RUNNING
            service._started = True
            
        # Define a patched stop function that cleans up resources
        async def patched_cleanup():
            # Clear client
            if service._client:
                await service._client.aclose()
                service._client = None
                
            # Clean up temp directory
            if service._temp_dir:
                service._temp_dir.cleanup()
                service._temp_dir = None

        # Apply the patches
        with patch.object(service, '_start', side_effect=patched_start), \
             patch.object(service, '_cleanup', side_effect=patched_cleanup):
                
            # Start the service
            await service.start()
            
            # Verify the service state after starting
            assert service._status == ServiceStatus.RUNNING
            assert service._client is mock_client  # Check the exact mock object
            assert service._temp_dir is not None
            
            # Verify the event bus emit was called for status update.
            # NOTE: BaseService._emit_status() emits the literal string
            # "service_status" (base_service.py:155), not
            # EventTopics.SERVICE_STATUS_UPDATE ("service.status.update") --
            # possible drift between the two that's worth reconciling in
            # production, but out of scope for this test fix.
            assert any(call[0][0] == "service_status" for call in service._event_bus.emit.call_args_list)
            
            # Stop the service
            await service.stop()
            
            # Verify the service state after stopping
            assert service._status == ServiceStatus.STOPPED
            assert service._client is None
            
            # Verify client was closed
            mock_client.aclose.assert_called_once()

    @pytest.mark.asyncio
    async def test_missing_api_key(self, mock_event_bus):
        """Test that initialization fails if API key is missing."""
        # Mock os.environ.get to ensure it doesn't return an API key
        with patch('os.environ.get', return_value=None):
            with pytest.raises(ValueError) as excinfo:
                ElevenLabsService(event_bus=mock_event_bus, config={})
            assert "API key is required" in str(excinfo.value)

    @pytest.mark.asyncio
    @patch("httpx.AsyncClient.post")
    async def test_generate_speech_success(self, mock_post, service, mock_audio_data):
        """Test successful speech generation."""
        # Configure the mock response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = mock_audio_data
        mock_post.return_value = mock_response

        # Start the service with mocked _start method
        async def patched_start():
            service._client = httpx.AsyncClient(
                base_url="https://api.elevenlabs.io/v1",
                headers={"xi-api-key": service._config.api_key},
                timeout=30.0
            )
            service._status = ServiceStatus.RUNNING
            service._started = True
            
        with patch.object(service, '_start', side_effect=patched_start):
            await service.start()

        # Generate speech
        result = await service._generate_speech(
            text="Test text",
            voice_id=service._config.voice_id,
            model_id=service._config.model_id,
            stability=0.7,
            similarity_boost=0.5,
            speed=1.1  # _generate_speech now requires `speed` (elevenlabs_service.py:877-884)
        )

        # Verify the result
        assert result == mock_audio_data
        
        # Verify the API call
        mock_post.assert_called_once()
        call_args = mock_post.call_args[0]
        call_kwargs = mock_post.call_args[1]
        
        assert f"/text-to-speech/{service._config.voice_id}" in call_args[0]
        assert call_kwargs["json"]["text"] == "Test text"
        assert call_kwargs["json"]["model_id"] == service._config.model_id
        assert call_kwargs["json"]["voice_settings"]["stability"] == 0.7
        assert call_kwargs["json"]["voice_settings"]["similarity_boost"] == 0.5

    @pytest.mark.asyncio
    @patch("httpx.AsyncClient.post")
    async def test_generate_speech_error(self, mock_post, service):
        """Test error handling in speech generation."""
        # Configure the mock response
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = "Bad Request"
        mock_post.return_value = mock_response

        # Start the service with mocked _start method
        async def patched_start():
            service._client = httpx.AsyncClient(
                base_url="https://api.elevenlabs.io/v1",
                headers={"xi-api-key": service._config.api_key},
                timeout=30.0
            )
            service._status = ServiceStatus.RUNNING
            service._started = True
            
        with patch.object(service, '_start', side_effect=patched_start):
            await service.start()

        # Generate speech (should return None due to error)
        result = await service._generate_speech(
            text="Test text",
            voice_id=service._config.voice_id,
            model_id=service._config.model_id,
            stability=0.7,
            similarity_boost=0.5,
            speed=1.1  # _generate_speech now requires `speed` (elevenlabs_service.py:877-884)
        )

        # Verify the result
        assert result is None

    @pytest.mark.asyncio
    async def test_handle_speech_generation_request(self, service):
        """Test handling a speech generation request.

        NOTE: playback_method is now force-set to STREAMING in __init__
        (elevenlabs_service.py:119-121), so _handle_speech_generation_request
        no longer calls httpx directly or _play_audio -- it enqueues the
        request onto self._speech_request_queue for the background audio
        thread to process (elevenlabs_service.py:648-668). The old
        mock_post/_play_audio-based assertions tested a code path that is now
        dead for this service configuration.
        """
        # Start the service with a mocked _start method (avoid real HTTP client)
        async def patched_start():
            service._status = ServiceStatus.RUNNING
            service._started = True

        with patch.object(service, '_start', side_effect=patched_start):
            await service.start()

        # Create a test payload
        request_payload = SpeechGenerationRequestPayload(
            text="Test speech generation",
            conversation_id="test-conversation-id",
            voice_id=None,  # Use default from service
            model_id=None   # Use default from service
        )

        # Handle the request
        await service._handle_speech_generation_request(request_payload)

        # Verify the request was enqueued for the streaming audio thread
        assert service._speech_request_queue.qsize() == 1
        queued_request = service._speech_request_queue.get_nowait()
        assert queued_request["text"] == "Test speech generation"
        assert queued_request["conversation_id"] == "test-conversation-id"
        assert queued_request["voice_id"] == service._config.voice_id
        assert queued_request["model_id"] == service._config.model_id

        # In streaming mode, SPEECH_GENERATION_COMPLETE is emitted later by
        # the audio thread, not by _handle_speech_generation_request itself
        # (see comment at elevenlabs_service.py:667-668), so no completion
        # event is expected here.

    @pytest.mark.asyncio
    async def test_handle_llm_response(self, service, mock_event_bus):
        """Test handling a complete LLM response.

        NOTE: _handle_llm_response no longer calls
        _handle_speech_generation_request directly. It now buffers the
        response and, once complete, creates a timeline plan and emits
        PLAN_READY for TimelineExecutorService to coordinate playback with
        ducking (elevenlabs_service.py:740-841,
        _create_speech_timeline_plan).
        """
        # Create an LLM response payload
        llm_payload = {
            "text": "This is a test LLM response",
            "conversation_id": "test-llm-conversation",
            "is_complete": True
        }

        # Call the handler
        await service._handle_llm_response(llm_payload)

        # Verify a PLAN_READY event was emitted with a "speak" step containing
        # the buffered text
        emit_calls = [
            call for call in service._event_bus.emit.call_args_list
            if call[0][0] == EventTopics.PLAN_READY
        ]
        assert len(emit_calls) == 1

        plan_payload = emit_calls[-1][0][1]
        steps = plan_payload["plan"]["steps"]
        assert len(steps) == 1
        assert steps[0]["step_type"] == "speak"
        assert steps[0]["text"] == "This is a test LLM response"
        assert steps[0]["id"] == "test-llm-conversation" 