"""
Tests for DeepgramTranscriptionService

These tests validate that the DeepgramTranscriptionService correctly handles various
response patterns from Deepgram, properly recovers from errors, and maintains the
closed-loop audio processing architecture.
"""

import asyncio
import pytest
import time
from unittest.mock import AsyncMock, MagicMock, patch, call
import numpy as np

from cantina_os.services.deepgram_transcription_service import DeepgramTranscriptionService
from cantina_os.event_topics import EventTopics
from cantina_os.event_payloads import ServiceStatus

# Sample response formats to test robustness
OBJECT_STYLE_RESULT = MagicMock()
OBJECT_STYLE_RESULT.is_final = True
OBJECT_STYLE_RESULT.channel.alternatives = [MagicMock()]
OBJECT_STYLE_RESULT.channel.alternatives[0].transcript = "This is object style"

DICT_STYLE_RESULT = {
    "type": "Results",
    "channel": {
        "alternatives": [
            {"transcript": "This is dictionary style"}
        ]
    },
    "is_final": True
}

ALT_DICT_STYLE_RESULT = {
    "channel": {
        "alternatives": [
            {"transcript": "This is alternative dictionary style"}
        ]
    },
    "is_final": True
}

@pytest.fixture
def event_bus():
    """Create a mock event bus for testing."""
    bus = MagicMock()
    bus.emit = AsyncMock()
    return bus

@pytest.fixture
def config():
    """Create test configuration."""
    return {
        "DEEPGRAM_API_KEY": "test_api_key",
        "DEEPGRAM_MODEL": "test-model",
        "DEEPGRAM_LANGUAGE": "en-US",
        "SAMPLE_RATE": 16000,
        "CHANNELS": 1,
        "MAX_RECONNECT_ATTEMPTS": 2,
        "SILENCE_THRESHOLD": 1.0
    }

@pytest.fixture
def logger():
    """Create a mock logger."""
    return MagicMock()

@pytest.fixture
def mock_client():
    """Create a mock Deepgram client."""
    client = MagicMock()
    connection = MagicMock()
    client.listen.websocket.v.return_value = connection
    return client, connection

@pytest.fixture
def service(event_bus, config, logger, mock_client):
    """Create a DeepgramTranscriptionService with mocks."""
    service = DeepgramTranscriptionService(
        event_bus=event_bus,
        config=config,
        logger=logger
    )
    
    # Setup necessary mocks
    client, connection = mock_client
    service._client = client
    service._connection = connection
    service.emit = AsyncMock()
    service._emit_status = AsyncMock()
    
    return service

@pytest.mark.asyncio
async def test_initialize_success(service, mock_client):
    """Test successful initialization."""
    client, connection = mock_client
    
    with patch('deepgram.DeepgramClient', return_value=client):
        await service._initialize()
    
    # Verify client and connection setup
    assert service._client is not None
    assert service._connection is not None
    
    # Verify event handlers were set up
    assert connection.on.call_count >= 4  # At least 4 event handlers
    
    # Verify connection was started
    assert connection.start.called
    
    # Verify error counter was reset
    assert service._errors == 0

@pytest.mark.asyncio
async def test_initialize_failure(service, mock_client, logger):
    """Test initialization failure handling."""
    client, connection = mock_client
    
    # Simulate failure
    client.listen.websocket.v.side_effect = Exception("Test connection failure")
    
    with patch('deepgram.DeepgramClient', return_value=client):
        with pytest.raises(Exception):
            await service._initialize()
    
    # Verify error counter was incremented
    assert service._errors > 0
    
    # Verify error was logged
    assert any("Failed to initialize" in str(args[0]) for args, _ in logger.error.call_args_list)

@pytest.mark.asyncio
async def test_on_transcript_object_style(service, event_bus):
    """Test transcript handling with object-style results."""
    # Setup
    service._current_conversation_id = "test_conv_id"
    
    # Use the loop trick to allow asyncio.run_coroutine_threadsafe to work in tests
    loop = asyncio.get_event_loop()
    
    with patch('asyncio.get_event_loop', return_value=loop):
        # Call the handler with object-style result
        service._on_transcript(None, OBJECT_STYLE_RESULT)
        
        # Allow the task to complete
        await asyncio.sleep(0.1)
    
    # Verify event was emitted with the correct parameters
    service.emit.assert_called_once()
    topic, payload = service.emit.call_args[0]
    
    assert topic == EventTopics.TRANSCRIPTION_FINAL
    assert payload["text"] == "This is object style"
    assert payload["is_final"] is True
    assert payload["conversation_id"] == "test_conv_id"

@pytest.mark.asyncio
async def test_on_transcript_dict_style(service, event_bus):
    """Test transcript handling with dictionary-style results."""
    # Setup
    service._current_conversation_id = "test_conv_id"
    
    # Use the loop trick to allow asyncio.run_coroutine_threadsafe to work in tests
    loop = asyncio.get_event_loop()
    
    with patch('asyncio.get_event_loop', return_value=loop):
        # Call the handler with dict-style result
        service._on_transcript(None, DICT_STYLE_RESULT)
        
        # Allow the task to complete
        await asyncio.sleep(0.1)
    
    # Verify event was emitted with the correct parameters
    service.emit.assert_called_once()
    topic, payload = service.emit.call_args[0]
    
    assert topic == EventTopics.TRANSCRIPTION_FINAL
    assert payload["text"] == "This is dictionary style"
    assert payload["is_final"] is True

@pytest.mark.asyncio
async def test_on_transcript_alt_dict_style(service, event_bus):
    """Test transcript handling with alternative dictionary-style results."""
    # Setup
    service._current_conversation_id = "test_conv_id"
    
    # Use the loop trick to allow asyncio.run_coroutine_threadsafe to work in tests
    loop = asyncio.get_event_loop()
    
    with patch('asyncio.get_event_loop', return_value=loop):
        # Call the handler with alternative dict-style result
        service._on_transcript(None, ALT_DICT_STYLE_RESULT)
        
        # Allow the task to complete
        await asyncio.sleep(0.1)
    
    # Verify event was emitted with the correct parameters
    service.emit.assert_called_once()
    topic, payload = service.emit.call_args[0]
    
    assert topic == EventTopics.TRANSCRIPTION_FINAL
    assert payload["text"] == "This is alternative dictionary style"
    assert payload["is_final"] is True

@pytest.mark.asyncio
async def test_handle_audio_chunk_success(service):
    """Test successful audio chunk handling."""
    # Setup
    service._is_streaming = True
    service._connection.send = MagicMock()
    
    # Create test payload
    audio_data = np.zeros((1024, 1), dtype=np.int16)
    payload = MagicMock()
    payload.samples = audio_data.tobytes()
    
    # Call handler
    await service._handle_audio_chunk(payload)
    
    # Verify audio was sent to Deepgram
    assert service._connection.send.called
    assert service._connection.send.call_args[0][0] == payload.samples
    
    # Verify error counter reset
    assert service._errors == 0

@pytest.mark.asyncio
async def test_handle_audio_chunk_error(service):
    """Test audio chunk error handling."""
    # Setup
    service._is_streaming = True
    service._connection.send.side_effect = Exception("Test send error")
    service._attempt_reconnect = AsyncMock()
    
    # Create test payload
    audio_data = np.zeros((1024, 1), dtype=np.int16)
    payload = MagicMock()
    payload.samples = audio_data.tobytes()
    
    # Call handler 6 times to exceed error threshold
    for _ in range(6):
        await service._handle_audio_chunk(payload)
    
    # Verify error counter incremented
    assert service._errors > 5
    
    # Verify reconnect was attempted after exceeding threshold
    assert service._attempt_reconnect.called

@pytest.mark.asyncio
async def test_attempt_reconnect_success(service):
    """Test successful reconnection attempt."""
    # Setup mocks
    service._cleanup = AsyncMock()
    service._initialize = AsyncMock()
    service._reconnect_attempts = 0
    service._max_reconnect_attempts = 3
    
    # Call reconnect
    await service._attempt_reconnect()
    
    # Verify cleanup and initialize were called
    assert service._cleanup.called
    assert service._initialize.called
    
    # Verify reconnect counter was incremented then reset
    assert service._reconnect_attempts == 0  # Reset to 0 after successful reconnect
    
    # Verify status was updated
    assert service._emit_status.called
    status_call = service._emit_status.call_args_list[-1]
    assert status_call[0][0] == ServiceStatus.RUNNING

@pytest.mark.asyncio
async def test_attempt_reconnect_max_retries(service):
    """Test reconnection max retries handling."""
    # Setup mocks
    service._cleanup = AsyncMock()
    service._initialize = AsyncMock()
    service._initialize.side_effect = Exception("Test reconnect failure")
    service._reconnect_attempts = service._max_reconnect_attempts
    
    # Call reconnect
    await service._attempt_reconnect()
    
    # Verify status was updated to ERROR
    assert service._emit_status.called
    status_call = service._emit_status.call_args_list[-1]
    assert status_call[0][0] == ServiceStatus.ERROR
    
    # Verify initialize was not called (we exceeded max attempts)
    assert not service._initialize.called

@pytest.mark.asyncio
async def test_reset_conversation_id(service):
    """Test conversation ID reset."""
    # Setup
    service._current_conversation_id = None
    
    # Reset the conversation ID
    service.reset_conversation_id()
    
    # Verify ID was set and is a string
    assert service._current_conversation_id is not None
    assert isinstance(service._current_conversation_id, str)
    assert len(service._current_conversation_id) > 0 