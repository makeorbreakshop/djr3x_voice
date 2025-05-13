"""Tests for the DeepgramDirectMicService."""

import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock, call

from cantina_os.services.deepgram_direct_mic_service import DeepgramDirectMicService
from cantina_os.core.event_bus import EventBus
from cantina_os.core.events import (
    VOICE_LISTENING_STARTED,
    VOICE_LISTENING_STOPPED,
    TRANSCRIPTION_INTERIM,
    TRANSCRIPTION_FINAL,
    TRANSCRIPTION_ERROR,
    TRANSCRIPTION_METRICS
)

@pytest.fixture
def event_bus():
    """Create a test event bus."""
    return EventBus()

@pytest.fixture
def config():
    """Create a test configuration."""
    return {
        "DEEPGRAM_API_KEY": "test_key",
        "AUDIO_SAMPLE_RATE": 16000,
        "AUDIO_CHANNELS": 1,
        "METRICS_INTERVAL": 0.1  # Faster interval for testing
    }

@pytest.fixture
def mock_dg_connection():
    """Create a mock Deepgram connection."""
    mock = MagicMock()
    mock.start = MagicMock()
    mock.finish = MagicMock()
    mock.on = MagicMock()
    mock.send = AsyncMock()
    
    # Store event handlers
    mock._handlers = {}
    def mock_on(event, handler):
        event_key = event if isinstance(event, str) else event.value
        mock._handlers[event_key] = handler
    mock.on.side_effect = mock_on
    
    return mock

@pytest.fixture
def mock_microphone():
    """Create a mock microphone."""
    mock = MagicMock()
    mock.start = MagicMock()
    mock.finish = MagicMock()
    return mock

@pytest.fixture
def mock_live_events():
    """Create a mock for LiveTranscriptionEvents."""
    mock = MagicMock()
    mock.Open = "Open"
    mock.Close = "Close"
    mock.Transcript = "Results"
    mock.Error = "Error"
    mock.UtteranceEnd = "UtteranceEnd"
    return mock

@pytest.fixture
async def service(event_bus, config, mock_dg_connection, mock_microphone, mock_live_events):
    """Create a test service instance with mocked components."""
    # Patch all Deepgram components
    with patch("cantina_os.services.deepgram_direct_mic_service.DeepgramClient") as mock_client, \
         patch("cantina_os.services.deepgram_direct_mic_service.Microphone", return_value=mock_microphone), \
         patch("cantina_os.services.deepgram_direct_mic_service.LiveTranscriptionEvents", mock_live_events):
        
        # Set up mock client
        mock_client.return_value.listen.websocket.v.return_value = mock_dg_connection
        
        # Create service
        service = DeepgramDirectMicService(event_bus, config)
        await service._start()
        
        yield service
        
        # Cleanup
        await service._stop()

@pytest.mark.asyncio
async def test_transcription_flow(service, event_bus, mock_dg_connection, mock_microphone):
    """Test the complete transcription flow."""
    # Set up event handlers
    interim_handler = AsyncMock()
    final_handler = AsyncMock()
    
    event_bus._emitter.on(TRANSCRIPTION_INTERIM, interim_handler)
    event_bus._emitter.on(TRANSCRIPTION_FINAL, final_handler)
    
    # Call the start listening method directly
    await service._start_listening()
    
    # Check that the connection was started and microphone initialized
    assert mock_dg_connection.start.called
    assert mock_microphone.start.called
    assert service.is_active()
    
    # Simulate transcript
    transcript_data = {
        "channel": {
            "alternatives": [{
                "transcript": "test transcript",
                "confidence": 0.95
            }]
        },
        "is_final": False,
        "duration": 0.5
    }
    
    # Get the transcript handler and call it manually
    transcript_handler = mock_dg_connection._handlers.get("Results")
    assert transcript_handler is not None
    transcript_handler(transcript_data)
    
    # Verify interim transcript
    interim_handler.assert_called_once_with({
        "text": "test transcript",
        "confidence": 0.95,
        "is_final": False
    })
    
    # Simulate final transcript
    transcript_data["is_final"] = True
    transcript_handler(transcript_data)
    
    # Verify final transcript
    final_handler.assert_called_once_with({
        "text": "test transcript",
        "confidence": 0.95,
        "is_final": True
    })
    
    # Stop listening
    await service._stop_listening()
    
    # Verify stopped state
    assert not service.is_active()
    assert mock_microphone.finish.called

@pytest.mark.asyncio
async def test_error_handling(service, event_bus, mock_dg_connection):
    """Test error handling during transcription."""
    # Set up error handler
    error_handler = AsyncMock()
    event_bus._emitter.on(TRANSCRIPTION_ERROR, error_handler)
    
    # Start listening
    await service._start_listening()
    
    # Get the error handler and call it
    error_handler_dg = mock_dg_connection._handlers.get("Error")
    assert error_handler_dg is not None
    
    # Simulate Deepgram error
    test_error = {"message": "Test error"}
    error_handler_dg(test_error)
    
    # Verify error was handled
    error_handler.assert_called_once_with({"error": test_error})

@pytest.mark.asyncio
async def test_utterance_end(service, event_bus, mock_dg_connection):
    """Test utterance end handling."""
    # Start listening
    await service._start_listening()
    
    # Get handlers
    utterance_handler = mock_dg_connection._handlers.get("UtteranceEnd")
    transcript_handler = mock_dg_connection._handlers.get("Results")
    
    assert utterance_handler is not None
    assert transcript_handler is not None
    
    # Send a transcript
    transcript_data = {
        "channel": {
            "alternatives": [{
                "transcript": "test utterance",
                "confidence": 0.95
            }]
        },
        "is_final": False
    }
    transcript_handler(transcript_data)
    
    # Trigger utterance end
    utterance_handler({})
    
    # Service should still be active
    assert service.is_active()

@pytest.mark.asyncio
async def test_metrics_collection(service, event_bus):
    """Test metrics collection and reporting."""
    metrics_handler = AsyncMock()
    event_bus._emitter.on(TRANSCRIPTION_METRICS, metrics_handler)
    
    # Wait for metrics to be collected
    await asyncio.sleep(0.2)  # Wait for at least one metrics collection
    
    # Verify metrics were emitted
    assert metrics_handler.called
    metrics_data = metrics_handler.call_args[0][0]
    assert "average_latency" in metrics_data
    assert "transcripts_processed" in metrics_data
    assert "errors_count" in metrics_data
    assert "uptime" in metrics_data

# BUGLOG
# ======
#
# 2024-03-19 Test Suite Cleanup
# ----------------------------
#
# Fixed Issues:
# 1. Standardized Mock Usage:
#    - Replaced MagicMock with AsyncMock for async event handlers
#    - Fixed inconsistent mock usage across test functions
#    - Ensured proper async/sync handler usage
#
# 2. Service Lifecycle:
#    - Fixed service fixture initialization
#    - Added proper cleanup in fixture teardown
#    - Corrected event propagation delays
#
# 3. Event Bus Integration:
#    - Updated event emission to use proper sync/async methods
#    - Fixed event handler registration
#    - Standardized event payload structure
#
# Remaining Issues:
# 1. Duplicate Test Functions:
#    - test_service_lifecycle and test_service_lifecycle_with_event_bus have overlapping functionality
#    - test_transcription_flow and test_transcription_flow_with_event_bus test the same features
#    - test_error_handling and test_error_handling_with_event_bus are redundant
#
# 2. Test Structure:
#    - Undefined service variable in test_transcription_flow_with_event_bus
#    - Inconsistent use of service fixture vs direct event bus emission
#    - Some tests mix direct service method calls with event bus events
#
# Next Steps:
# 1. Remove duplicate test functions and consolidate into single, comprehensive tests
# 2. Standardize on either direct service method calls or event bus events
# 3. Fix undefined service variable in remaining tests
# 4. Add missing test cases for microphone initialization and cleanup
# 5. Add performance metrics testing as specified in DeepgramDirectMicService_BUGLOG.md
#
# Test Coverage Status:
# - Service Lifecycle: ✓ (Passing)
# - Event Handling: ✓ (Passing)
# - Error Handling: ✓ (Passing)
# - Transcription Flow: ! (Partially passing, needs consolidation)
# - Microphone Integration: x (Missing tests)
# - Performance Metrics: x (Not implemented)