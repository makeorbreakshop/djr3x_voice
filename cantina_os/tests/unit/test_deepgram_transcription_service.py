"""
Tests for DeepgramTranscriptionService

These tests verify the functionality of the Deepgram transcription service,
including initialization, websocket connection handling, and transcription events.
"""

import asyncio
import pytest
import json
from unittest.mock import patch, AsyncMock, MagicMock
import aiohttp
from aiohttp import WSMessage, WSMsgType
import time

from cantina_os.services.deepgram_transcription_service import DeepgramTranscriptionService
from cantina_os.event_topics import EventTopics
from cantina_os.event_payloads import ServiceStatus, TranscriptionTextPayload

@pytest.fixture
def mock_deepgram_client():
    """Create a mock Deepgram client."""
    with patch("cantina_os.services.deepgram_transcription_service.DeepgramClient") as mock_client:
         # Create a mock connection
         mock_connection = Mock()
         mock_client.return_value.listen.transcription.live.return_value = mock_connection
         
         yield mock_client

@pytest.mark.asyncio
async def test_deepgram_service_initialization(event_bus, test_config, mock_deepgram_client):
    """Test service initialization."""
    mock_client, _ = mock_deepgram_client
    
    service = DeepgramTranscriptionService(event_bus, test_config)
    
    # Verify initial state
    assert service.service_name == "deepgram_transcription"
    assert not service.is_started
    assert service.status == ServiceStatus.INITIALIZING
    
    # Start service
    await service.start()
    
    # Verify started state
    assert service.is_started
    assert service.status == ServiceStatus.RUNNING
    
    # Verify Deepgram client was created
    mock_client.assert_called_once_with(test_config["DEEPGRAM_API_KEY"])
    
    # Stop service
    await service.stop()
    assert not service.is_started

@pytest.mark.asyncio
async def test_streaming_lifecycle(event_bus, test_config, mock_deepgram_client):
    """Test streaming start/stop cycle."""
    _, mock_connection = mock_deepgram_client
    
    service = DeepgramTranscriptionService(event_bus, test_config)
    await service.start()
    
    # Start streaming
    await service.start_streaming()
    assert service._is_streaming is True
    assert service._connection is mock_connection
    
    # Verify event handlers were registered
    assert mock_connection.on.call_count >= 5  # At least 5 event handlers
    
    # Verify connection was started
    mock_connection.start.assert_called_once()
    
    # Stop streaming
    await service.stop_streaming()
    assert service._is_streaming is False
    mock_connection.finish.assert_called_once()
    
    await service.stop()

@pytest.mark.asyncio
async def test_audio_chunk_handling(event_bus, test_config, mock_deepgram_client):
    """Test handling of audio chunks."""
    _, mock_connection = mock_deepgram_client
    
    service = DeepgramTranscriptionService(event_bus, test_config)
    await service.start()
    
    # Create test audio data
    audio_chunk = {
        "samples": b'test_audio_data',
        "timestamp": time.time(),
        "sample_rate": 16000,
        "channels": 1
    }
    
    # Send audio chunk
    await service._handle_audio_chunk(audio_chunk)
    
    # Verify streaming was started and audio was sent
    assert service._is_streaming is True
    mock_connection.send.assert_called_once_with(audio_chunk["samples"])
    
    await service.stop()

@pytest.mark.asyncio
async def test_transcript_handling(event_bus, test_config, mock_deepgram_client):
    """Test handling of transcript events."""
    _, mock_connection = mock_deepgram_client
    
    # Track emitted events
    transcript_events = []
    
    def collect_event(event):
        transcript_events.append(event)
    
    event_bus.on(EventTopics.AUDIO_TRANSCRIPTION_FINAL, collect_event)
    
    service = DeepgramTranscriptionService(event_bus, test_config)
    await service.start()
    await service.start_streaming()
    
    # Generate mock transcript data
    transcript_data = {
        "channel": {
            "alternatives": [
                {
                    "transcript": "This is a test transcript",
                    "confidence": 0.95
                }
            ]
        },
        "is_final": True,
        "start": time.time() - 0.5  # 500ms ago
    }
    
    # Call transcript handler directly
    service._on_transcript(None, json.dumps(transcript_data))
    
    # Give time for async events to process
    await asyncio.sleep(0.1)
    
    # Verify transcript event was emitted
    assert len(transcript_events) == 1
    event = transcript_events[0]
    assert event["text"] == "This is a test transcript"
    assert event["source"] == "deepgram"
    assert event["is_final"] is True
    assert event["confidence"] == 0.95
    assert event["conversation_id"] is not None
    
    await service.stop()

@pytest.mark.asyncio
async def test_conversation_id_propagation(event_bus, test_config, mock_deepgram_client):
    """Test conversation ID generation and propagation."""
    service = DeepgramTranscriptionService(event_bus, test_config)
    await service.start()
    
    # Initially no conversation ID
    assert service._current_conversation_id is None
    
    # Starting streaming should generate a conversation ID
    await service.start_streaming()
    assert service._current_conversation_id is not None
    first_id = service._current_conversation_id
    
    # Resetting conversation ID should generate a new one
    service.reset_conversation_id()
    assert service._current_conversation_id != first_id
    
    await service.stop()

@pytest.mark.asyncio
async def test_error_handling(event_bus, test_config, mock_deepgram_client):
    """Test handling of connection errors."""
    _, mock_connection = mock_deepgram_client
    
    # Make send() raise an exception
    mock_connection.send.side_effect = Exception("Test connection error")
    
    service = DeepgramTranscriptionService(event_bus, test_config)
    await service.start()
    
    # Patch the reconnection method to avoid actual delays
    with patch.object(service, '_handle_connection_error', 
                     return_value=asyncio.Future()) as mock_reconnect:
        mock_reconnect.return_value.set_result(None)
        
        # Send audio chunk to trigger error
        await service._handle_audio_chunk({
            "samples": b'test_audio_data',
            "timestamp": time.time()
        })
        
        # Verify reconnection was attempted
        mock_reconnect.assert_called_once()
    
    await service.stop()

@pytest.mark.asyncio
async def test_latency_stats(event_bus, test_config, mock_deepgram_client):
    """Test latency statistics tracking."""
    service = DeepgramTranscriptionService(event_bus, test_config)
    await service.start()
    
    # Initially empty
    stats = service.get_latency_stats()
    assert stats["count"] == 0
    
    # Add some test measurements
    now = time.time()
    service._latency_measurements = [
        {"audio_start": now - 0.5, "transcript_time": now, "latency": 0.5, "is_final": True},
        {"audio_start": now - 0.3, "transcript_time": now, "latency": 0.3, "is_final": False},
        {"audio_start": now - 0.7, "transcript_time": now, "latency": 0.7, "is_final": True}
    ]
    
    # Check stats
    stats = service.get_latency_stats()
    assert stats["count"] == 3
    assert stats["min"] == 0.3
    assert stats["max"] == 0.7
    assert stats["avg"] == 0.5
    
    await service.stop() 