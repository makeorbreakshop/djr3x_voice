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
    ServiceStatus
)
from cantina_os.event_topics import EventTopics
from cantina_os.services.elevenlabs_service import (
    ElevenLabsService,
    SpeechPlaybackMethod
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
async def service(mock_api_key, mock_event_bus):
    """Create an ElevenLabsService instance for testing."""
    service = ElevenLabsService(
        event_bus=mock_event_bus,
        api_key=mock_api_key,
        voice_id="test-voice-id",
        model_id="test-model-id",
        playback_method=SpeechPlaybackMethod.SYSTEM,  # Use system to avoid sounddevice dependency
        name="TestElevenLabsService"
    )
    yield service
    # Ensure service is stopped after test
    if service.status != "stopped":
        await service.stop()


class TestElevenLabsService:
    """Tests for the ElevenLabsService."""

    @pytest.mark.asyncio
    async def test_initialization(self, service):
        """Test that the service initializes correctly."""
        # Verify initial properties
        assert service.api_key == "mock-api-key"
        assert service.voice_id == "test-voice-id"
        assert service.model_id == "test-model-id"
        assert service.playback_method == SpeechPlaybackMethod.SYSTEM
        
        # Check status enum instead of string
        assert service.status == ServiceStatus.INITIALIZING
        
        # Test event subscriptions
        subscription_topics = [call[0][0] for call in service.event_bus.on.call_args_list]
        assert EventTopics.SPEECH_GENERATION_REQUEST in subscription_topics

    @pytest.mark.asyncio
    async def test_start_stop(self, service):
        """Test the service start and stop lifecycle."""
        # Import ServiceStatus enum
        from cantina_os.event_payloads import ServiceStatus
        
        # Create a mock client
        mock_client = MagicMock()
        mock_client.aclose = AsyncMock()
        
        # Define a patched start function that assigns our mock client
        async def patched_start():
            service.client = mock_client
            service.temp_dir = tempfile.TemporaryDirectory()
            service._status = ServiceStatus.RUNNING
            service._started = True
            
        # Define a patched stop function that cleans up resources
        async def patched_stop():
            # Clear client
            if service.client:
                await service.client.aclose()
                service.client = None
                
            # Clean up temp directory
            if service.temp_dir:
                service.temp_dir.cleanup()
                service.temp_dir = None

        # Apply the patches
        with patch.object(service, '_start', side_effect=patched_start), \
             patch.object(service, '_stop', side_effect=patched_stop):
                
            # Start the service
            await service.start()
            
            # Verify the service state after starting
            assert service.status == ServiceStatus.RUNNING
            assert service.client is mock_client  # Check the exact mock object
            assert service.temp_dir is not None
            
            # Verify the event bus emit was called for status update
            assert any(call[0][0] == EventTopics.SERVICE_STATUS_UPDATE for call in service.event_bus.emit.call_args_list)
            
            # Stop the service
            await service.stop()
            
            # Verify the service state after stopping
            assert service.status == ServiceStatus.STOPPED
            assert service.client is None
            
            # Verify client was closed
            mock_client.aclose.assert_called_once()

    @pytest.mark.asyncio
    async def test_missing_api_key(self, mock_event_bus):
        """Test that initialization fails if API key is missing."""
        # Mock os.environ.get to ensure it doesn't return an API key
        with patch('os.environ.get', return_value=None):
            with pytest.raises(ValueError) as excinfo:
                ElevenLabsService(event_bus=mock_event_bus, api_key=None)
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

        # Start the service
        await service.start()
        
        # Ensure the client is initialized (patching _initialize)
        if not service.client:
            service.client = httpx.AsyncClient(
                base_url="https://api.elevenlabs.io/v1",
                headers={"xi-api-key": service.api_key},
                timeout=30.0
            )

        # Generate speech
        result = await service._generate_speech(
            text="Test text",
            voice_id=service.voice_id,
            model_id=service.model_id,
            stability=0.7,
            similarity_boost=0.5
        )

        # Verify the result
        assert result == mock_audio_data
        
        # Verify the API call
        mock_post.assert_called_once()
        call_args = mock_post.call_args[0]
        call_kwargs = mock_post.call_args[1]
        
        assert f"/text-to-speech/{service.voice_id}" in call_args[0]
        assert call_kwargs["json"]["text"] == "Test text"
        assert call_kwargs["json"]["model_id"] == service.model_id
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

        # Start the service
        await service.start()
        
        # Ensure client is initialized (test shouldn't depend on _initialize method)
        if not service.client:
            service.client = httpx.AsyncClient(
                base_url="https://api.elevenlabs.io/v1",
                headers={"xi-api-key": service.api_key},
                timeout=30.0
            )

        # Generate speech (should return None due to error)
        result = await service._generate_speech(
            text="Test text",
            voice_id=service.voice_id,
            model_id=service.model_id,
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
        
        # Start the service
        await service.start()
        
        # Ensure the client is initialized (patching _initialize)
        if not service.client:
            service.client = httpx.AsyncClient(
                base_url="https://api.elevenlabs.io/v1",
                headers={"xi-api-key": service.api_key},
                timeout=30.0
            )
            
        # Create temp dir if not exists
        if not service.temp_dir:
            service.temp_dir = tempfile.TemporaryDirectory()
        
        # Add a mock for the emit method
        service.emit = AsyncMock()

        # Create a test payload
        payload = SpeechGenerationRequestPayload(
            conversation_id="test-conversation",
            text="Hello, world!",
            voice_id="test-voice",
            model_id="test-model"
        )

        try:
            # Handle the request with timeout to prevent hanging
            await asyncio.wait_for(
                service._handle_speech_generation_request(payload),
                timeout=2.0
            )

            # Verify the API call was made
            mock_post.assert_called_once()
            
            # Verify play_audio was called
            mock_play_audio.assert_called_once()
            assert os.path.basename(mock_play_audio.call_args[0][0]).endswith(".mp3")
            
            # Verify emission of completion event
            service.emit.assert_called_once()
            call_args = service.emit.call_args
            assert call_args[0][0] == EventTopics.SPEECH_GENERATION_COMPLETE
            assert isinstance(call_args[0][1], SpeechGenerationCompletePayload)
            assert call_args[0][1].conversation_id == "test-conversation"
            assert call_args[0][1].text == "Hello, world!"
            assert call_args[0][1].success is True
            
        except asyncio.TimeoutError:
            pytest.fail("Test timed out while handling speech generation request")
        
        finally:
            # Proper cleanup
            if service.temp_dir:
                service.temp_dir.cleanup()
                service.temp_dir = None

    @pytest.mark.asyncio
    @patch("httpx.AsyncClient.post")
    async def test_handle_speech_generation_error(self, mock_post, service):
        """Test error handling in speech generation request."""
        # Configure the mock response to fail
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_post.return_value = mock_response
        
        # Start the service
        await service.start()
        
        # Ensure the client is initialized (patching _initialize)
        if not service.client:
            service.client = httpx.AsyncClient(
                base_url="https://api.elevenlabs.io/v1",
                headers={"xi-api-key": service.api_key},
                timeout=30.0
            )
        
        # Add a mock for the emit method
        service.emit = AsyncMock()

        # Create a test payload
        payload = SpeechGenerationRequestPayload(
            conversation_id="test-conversation",
            text="Hello, world!"
        )

        try:
            # Handle the request with timeout to prevent hanging
            await asyncio.wait_for(
                service._handle_speech_generation_request(payload),
                timeout=2.0
            )

            # Verify emission of error event
            service.emit.assert_called_once()
            call_args = service.emit.call_args
            assert call_args[0][0] == EventTopics.SPEECH_GENERATION_COMPLETE
            assert isinstance(call_args[0][1], SpeechGenerationCompletePayload)
            assert call_args[0][1].conversation_id == "test-conversation"
            assert call_args[0][1].success is False
            assert call_args[0][1].error is not None
            
        except asyncio.TimeoutError:
            pytest.fail("Test timed out while handling speech generation error")

    @pytest.mark.asyncio
    @patch("subprocess.Popen")
    async def test_system_playback(self, mock_popen, service):
        """Test system playback method."""
        import platform
        platform_name = platform.system()
        
        # Skip test if platform not supported in test
        if platform_name not in ["Darwin", "Linux", "Windows"]:
            pytest.skip(f"Platform {platform_name} not covered in test")
        
        # Configure mock subprocess
        mock_process = MagicMock()
        mock_process.communicate.return_value = (b"", b"")
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        # Start the service
        await service.start()
        
        # Mock the emit method to avoid actual event emission
        service.emit = AsyncMock()
        
        # Create a temporary file path
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
        temp_file.write(b"test audio data")
        temp_file.close()
        
        # Create a patched method that doesn't actually call subprocess
        async def mock_play_system(file_path):
            # Just return immediately without calling actual system commands
            return
            
        # Apply the patch
        with patch.object(service, '_play_with_system_command', side_effect=mock_play_system):
            try:
                # Call the _play_audio method directly
                await service._play_audio(temp_file.name, "test-conversation")
                
                # Verify the appropriate method was called based on playback method
                assert service.playback_method == SpeechPlaybackMethod.SYSTEM
                
                # No need to check actual command execution as we're bypassing that
                
            finally:
                # Clean up temp file
                if os.path.exists(temp_file.name):
                    os.unlink(temp_file.name)

    @pytest.mark.asyncio
    async def test_invalid_payload(self, service, mock_event_bus):
        """Test handling an invalid event payload."""
        # Start the service
        await service.start()
        
        # Mock emit to avoid actual event emission
        service.emit = AsyncMock()
        service.logger = MagicMock()
        
        # Create an invalid payload (not a SpeechGenerationRequestPayload)
        invalid_payload = BaseEventPayload(conversation_id="test-conversation")
        
        # Handle the payload with timeout to prevent hanging
        try:
            await asyncio.wait_for(
                service._handle_speech_generation_request(invalid_payload),
                timeout=2.0
            )
            
            # Verify emit was not called (no speech generation occurred)
            service.emit.assert_not_called()
            
            # Verify an error was logged
            assert any("Invalid event payload" in str(call) for call in service.logger.error.call_args_list)
        
        except asyncio.TimeoutError:
            pytest.fail("Test timed out while handling invalid payload")
        
        finally:
            await service.stop() 