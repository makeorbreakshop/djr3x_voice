"""Tests for the mock Deepgram service."""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from cantina_os.tests.mocks.mock_deepgram_service import MockDeepgramService
from cantina_os.core import EventTopics

@pytest.fixture
async def mock_event_bus():
    """Create a mock event bus for testing."""
    event_bus = AsyncMock()
    event_bus.emit = AsyncMock()
    return event_bus

@pytest.fixture
async def mock_deepgram_service(mock_event_bus):
    """Create a MockDeepgramService instance for testing."""
    service = MockDeepgramService(event_bus=mock_event_bus)
    yield service
    await service._cleanup()

@pytest.mark.asyncio
async def test_service_lifecycle(mock_deepgram_service, mock_event_bus):
    """Test service startup and shutdown."""
    # Test startup
    await mock_deepgram_service._start()
    assert mock_deepgram_service.is_connected is True
    mock_event_bus.emit.assert_called_with(
        EventTopics.SERVICE_STATUS,
        {"status": "started", "service": mock_deepgram_service.name}
    )
    
    # Test shutdown
    await mock_deepgram_service._stop()
    assert mock_deepgram_service.is_connected is False
    assert mock_deepgram_service.is_listening is False

@pytest.mark.asyncio
async def test_streaming_lifecycle(mock_deepgram_service):
    """Test streaming start and stop."""
    await mock_deepgram_service._start()
    conversation_id = "test-conversation"
    
    # Test start streaming
    await mock_deepgram_service.start_streaming(conversation_id)
    assert mock_deepgram_service.is_listening is True
    assert mock_deepgram_service.current_conversation_id == conversation_id
    
    # Test stop streaming
    await mock_deepgram_service.stop_streaming()
    assert mock_deepgram_service.is_listening is False
    assert mock_deepgram_service.current_conversation_id is None

@pytest.mark.asyncio
async def test_audio_processing(mock_deepgram_service):
    """Test audio processing and event emission."""
    await mock_deepgram_service._start()
    conversation_id = "test-conversation"
    await mock_deepgram_service.start_streaming(conversation_id)
    
    # Process some mock audio data
    audio_data = bytes([0] * 1024)  # Mock audio data
    await mock_deepgram_service.process_audio(audio_data)
    
    # Verify interim transcription event was emitted
    await asyncio.sleep(0.2)  # Allow for processing delay
    mock_deepgram_service._event_bus.emit.assert_called_with(
        EventTopics.VOICE_TRANSCRIPTION_INTERIM,
        {
            "conversation_id": conversation_id,
            "is_final": False,
            "transcript": "mock interim transcription",
            "confidence": 0.8
        }
    )

@pytest.mark.asyncio
async def test_websocket_events(mock_deepgram_service):
    """Test WebSocket lifecycle event handlers."""
    # Test connection open
    mock_client = MagicMock()
    mock_deepgram_service._on_open(mock_client)
    assert mock_deepgram_service.is_connected is True
    assert mock_deepgram_service._ws_client == mock_client
    
    # Test connection close
    mock_deepgram_service._on_close()
    assert mock_deepgram_service.is_connected is False
    assert mock_deepgram_service._ws_client is None
    assert mock_deepgram_service.is_listening is False
    
    # Test error handling
    mock_error = Exception("Test error")
    mock_deepgram_service._on_error(mock_error)
    assert mock_deepgram_service.is_connected is False
    assert mock_deepgram_service._ws_client is None
    assert mock_deepgram_service.is_listening is False

@pytest.mark.asyncio
async def test_error_handling(mock_deepgram_service):
    """Test error handling scenarios."""
    # Test starting streaming without connection
    with pytest.raises(RuntimeError, match="Service not connected"):
        await mock_deepgram_service.start_streaming("test-conversation")
    
    # Test processing audio when not listening
    audio_data = bytes([0] * 1024)
    await mock_deepgram_service.process_audio(audio_data)
    # Should not raise any errors, but also should not process
    assert mock_deepgram_service._event_bus.emit.call_count == 0 