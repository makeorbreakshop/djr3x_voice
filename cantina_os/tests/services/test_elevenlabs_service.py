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
from cantina_os.event_topics import EventTopics
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
        assert service._config.playback_method == SpeechPlaybackMethod.SYSTEM
        
        # Check status enum instead of string
        assert service._status == ServiceStatus.INITIALIZING

    @pytest.mark.asyncio
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
            
            # Verify the event bus emit was called for status update
            assert any(call[0][0] == EventTopics.SERVICE_STATUS_UPDATE for call in service._event_bus.emit.call_args_list)
            
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
            similarity_boost=0.5
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
            similarity_boost=0.5
        )

        # Verify the result
        assert result is None

    @pytest.mark.asyncio
    @patch("httpx.AsyncClient.post")
    @patch.object(ElevenLabsService, "_play_audio")
    async def test_handle_speech_generation_request(self, 
                                                    mock_play_audio, 
                                                    mock_post, 
                                                    service, 
                                                    mock_audio_data):
        """Test handling a speech generation request."""
        # Configure the mock response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = mock_audio_data
        mock_post.return_value = mock_response
        
        # Mock the play_audio method to avoid actual playback
        mock_play_audio.return_value = None
        
        # Start the service with mocked _start method
        async def patched_start():
            service._client = httpx.AsyncClient(
                base_url="https://api.elevenlabs.io/v1",
                headers={"xi-api-key": service._config.api_key},
                timeout=30.0
            )
            service._temp_dir = tempfile.TemporaryDirectory()
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
        
        # Verify API call was made
        mock_post.assert_called_once()
        
        # Verify the temp file was created and passed to _play_audio
        mock_play_audio.assert_called_once()
        file_path_arg = mock_play_audio.call_args[0][0]
        assert "test-conversation-id" in file_path_arg
        
        # Verify completion event was emitted
        emit_calls = [call for call in service._event_bus.emit.call_args_list 
                      if call[0][0] == EventTopics.SPEECH_GENERATION_COMPLETE]
        assert len(emit_calls) > 0
        
        # Check payload content in the emit call
        emit_payload = emit_calls[-1][0][1]
        assert emit_payload["conversation_id"] == "test-conversation-id"
        assert emit_payload["success"] is True

    @pytest.mark.asyncio
    async def test_handle_llm_response(self, service, mock_event_bus):
        """Test handling an LLM response."""
        # Mock the _handle_speech_generation_request method
        service._handle_speech_generation_request = AsyncMock()
        
        # Create an LLM response payload
        llm_payload = {
            "text": "This is a test LLM response",
            "conversation_id": "test-llm-conversation",
            "is_complete": True
        }
        
        # Call the handler
        await service._handle_llm_response(llm_payload)
        
        # Verify that _handle_speech_generation_request was called with correct args
        service._handle_speech_generation_request.assert_called_once()
        call_arg = service._handle_speech_generation_request.call_args[0][0]
        
        # Check the request payload has correct values
        assert call_arg.text == "This is a test LLM response"
        assert call_arg.conversation_id == "test-llm-conversation"
        assert call_arg.voice_id == service._config.voice_id
        assert call_arg.model_id == service._config.model_id 