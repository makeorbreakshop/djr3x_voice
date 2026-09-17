"""
Unit tests for LatencyTrackerService.

Tests verify that the service correctly:
- Tracks timestamps from pipeline events
- Calculates stage durations
- Reports latency metrics
- Handles conversation lifecycle
"""

import asyncio
import pytest
import time
from unittest.mock import Mock, AsyncMock
from pyee.asyncio import AsyncIOEventEmitter

from ..services.latency_tracker_service import LatencyTrackerService
from ..core.event_topics import EventTopics


@pytest.fixture
def event_bus():
    """Create event bus for testing."""
    return AsyncIOEventEmitter()


@pytest.fixture
async def latency_tracker(event_bus):
    """Create and start LatencyTrackerService."""
    service = LatencyTrackerService(event_bus)
    await service.start()
    yield service
    await service.stop()


@pytest.mark.asyncio
async def test_service_initialization(event_bus):
    """Test that service initializes correctly."""
    service = LatencyTrackerService(event_bus)

    assert service._service_name == "latency_tracker"
    assert service._event_bus is event_bus
    assert service._conversation_metrics == {}
    assert service._completed_metrics == []


@pytest.mark.asyncio
async def test_service_start_stop(event_bus):
    """Test service lifecycle."""
    service = LatencyTrackerService(event_bus)

    # Should start without errors
    await service.start()
    assert service._is_running is True
    assert service._started is True

    # Should stop cleanly
    await service.stop()
    assert service._is_running is False


@pytest.mark.asyncio
async def test_tracks_voice_listening_started(latency_tracker, event_bus):
    """Test tracking when voice listening starts."""
    conversation_id = "test_conv_001"
    timestamp = time.time()

    # Emit voice listening started event
    event_bus.emit(EventTopics.VOICE_LISTENING_STARTED, {
        "timestamp": timestamp,
        "conversation_id": conversation_id
    })

    await asyncio.sleep(0.1)  # Let event propagate

    # Check that timestamp was recorded
    assert conversation_id in latency_tracker._conversation_metrics
    metrics = latency_tracker._conversation_metrics[conversation_id]
    assert metrics["voice_started"] == timestamp


@pytest.mark.asyncio
async def test_tracks_transcription_final(latency_tracker, event_bus):
    """Test tracking when transcription completes."""
    conversation_id = "test_conv_002"
    start_time = time.time()
    transcription_time = start_time + 0.5

    # Simulate voice started
    event_bus.emit(EventTopics.VOICE_LISTENING_STARTED, {
        "timestamp": start_time,
        "conversation_id": conversation_id
    })

    await asyncio.sleep(0.05)

    # Emit transcription final
    event_bus.emit(EventTopics.TRANSCRIPTION_FINAL, {
        "timestamp": transcription_time,
        "conversation_id": conversation_id,
        "text": "test transcription"
    })

    await asyncio.sleep(0.1)

    # Check that both timestamps recorded
    metrics = latency_tracker._conversation_metrics[conversation_id]
    assert metrics["voice_started"] == start_time
    assert metrics["transcription_complete"] == transcription_time


@pytest.mark.asyncio
async def test_tracks_llm_response(latency_tracker, event_bus):
    """Test tracking when LLM responds."""
    conversation_id = "test_conv_003"
    start_time = time.time()
    llm_time = start_time + 1.5

    # Set up conversation
    event_bus.emit(EventTopics.VOICE_LISTENING_STARTED, {
        "timestamp": start_time,
        "conversation_id": conversation_id
    })

    await asyncio.sleep(0.05)

    # Emit LLM response
    event_bus.emit(EventTopics.LLM_RESPONSE_TEXT, {
        "timestamp": llm_time,
        "conversation_id": conversation_id,
        "text": "test response"
    })

    await asyncio.sleep(0.1)

    metrics = latency_tracker._conversation_metrics[conversation_id]
    assert metrics["llm_complete"] == llm_time


@pytest.mark.asyncio
async def test_tracks_speech_synthesis(latency_tracker, event_bus):
    """Test tracking speech synthesis lifecycle."""
    conversation_id = "test_conv_004"
    start_time = time.time()
    tts_start = start_time + 2.0
    tts_end = start_time + 5.0

    # Set up conversation
    event_bus.emit(EventTopics.VOICE_LISTENING_STARTED, {
        "timestamp": start_time,
        "conversation_id": conversation_id
    })

    await asyncio.sleep(0.05)

    # Speech synthesis started
    event_bus.emit(EventTopics.SPEECH_SYNTHESIS_STARTED, {
        "timestamp": tts_start,
        "conversation_id": conversation_id
    })

    await asyncio.sleep(0.05)

    # Speech synthesis ended
    event_bus.emit(EventTopics.SPEECH_SYNTHESIS_ENDED, {
        "timestamp": tts_end,
        "conversation_id": conversation_id
    })

    await asyncio.sleep(0.1)

    metrics = latency_tracker._conversation_metrics[conversation_id]
    assert metrics["tts_started"] == tts_start
    assert metrics["tts_ended"] == tts_end


@pytest.mark.asyncio
async def test_calculates_full_pipeline_latency(latency_tracker, event_bus):
    """Test end-to-end pipeline latency calculation."""
    conversation_id = "test_conv_005"
    base_time = time.time()

    # Simulate full pipeline
    timestamps = {
        "voice_started": base_time,
        "transcription_complete": base_time + 0.5,
        "llm_complete": base_time + 2.0,
        "tts_started": base_time + 2.1,
        "tts_ended": base_time + 5.0
    }

    for stage, timestamp in timestamps.items():
        event_map = {
            "voice_started": EventTopics.VOICE_LISTENING_STARTED,
            "transcription_complete": EventTopics.TRANSCRIPTION_FINAL,
            "llm_complete": EventTopics.LLM_RESPONSE_TEXT,
            "tts_started": EventTopics.SPEECH_SYNTHESIS_STARTED,
            "tts_ended": EventTopics.SPEECH_SYNTHESIS_ENDED
        }

        event_bus.emit(event_map[stage], {
            "timestamp": timestamp,
            "conversation_id": conversation_id,
            "text": "test"
        })
        await asyncio.sleep(0.05)

    # Calculate metrics
    await asyncio.sleep(0.1)
    summary = latency_tracker.get_conversation_summary(conversation_id)

    # Verify calculations
    assert summary is not None
    assert abs(summary["transcription_latency"] - 0.5) < 0.01
    assert abs(summary["llm_latency"] - 1.5) < 0.01
    assert abs(summary["tts_generation_latency"] - 0.1) < 0.01
    assert abs(summary["tts_playback_duration"] - 2.9) < 0.01
    assert abs(summary["total_latency"] - 5.0) < 0.01


@pytest.mark.asyncio
async def test_handles_multiple_conversations(latency_tracker, event_bus):
    """Test tracking multiple concurrent conversations."""
    conv_ids = ["conv_a", "conv_b", "conv_c"]
    base_time = time.time()

    # Start all conversations
    for i, conv_id in enumerate(conv_ids):
        event_bus.emit(EventTopics.VOICE_LISTENING_STARTED, {
            "timestamp": base_time + i * 0.1,
            "conversation_id": conv_id
        })
        await asyncio.sleep(0.05)

    # Each should be tracked separately
    assert len(latency_tracker._conversation_metrics) == 3
    for conv_id in conv_ids:
        assert conv_id in latency_tracker._conversation_metrics


@pytest.mark.asyncio
async def test_get_latency_report(latency_tracker, event_bus):
    """Test generating latency report."""
    # Create some completed conversations
    for i in range(3):
        conv_id = f"test_conv_{i}"
        base_time = time.time()

        event_bus.emit(EventTopics.VOICE_LISTENING_STARTED, {
            "timestamp": base_time,
            "conversation_id": conv_id
        })
        event_bus.emit(EventTopics.SPEECH_SYNTHESIS_ENDED, {
            "timestamp": base_time + 2.0,
            "conversation_id": conv_id
        })
        await asyncio.sleep(0.1)

    # Get report
    report = latency_tracker.get_latency_report()

    assert report is not None
    assert "total_conversations" in report
    assert report["total_conversations"] >= 3


@pytest.mark.asyncio
async def test_cleanup_old_conversations(latency_tracker, event_bus):
    """Test that old conversations are cleaned up."""
    # Create many conversations
    for i in range(150):
        conv_id = f"old_conv_{i}"
        event_bus.emit(EventTopics.VOICE_LISTENING_STARTED, {
            "timestamp": time.time(),
            "conversation_id": conv_id
        })
        await asyncio.sleep(0.01)

    # Should not exceed max limit
    assert len(latency_tracker._conversation_metrics) <= 100


@pytest.mark.asyncio
async def test_handles_missing_stages(latency_tracker, event_bus):
    """Test handling conversations with missing stages."""
    conversation_id = "incomplete_conv"
    base_time = time.time()

    # Only voice started, no completion
    event_bus.emit(EventTopics.VOICE_LISTENING_STARTED, {
        "timestamp": base_time,
        "conversation_id": conversation_id
    })

    await asyncio.sleep(0.1)

    # Should still track what we have
    assert conversation_id in latency_tracker._conversation_metrics

    # Summary should handle missing data gracefully
    summary = latency_tracker.get_conversation_summary(conversation_id)
    assert summary["transcription_latency"] is None


@pytest.mark.asyncio
async def test_emits_performance_metrics(latency_tracker, event_bus):
    """Test that service emits DEBUG_PERFORMANCE events."""
    conversation_id = "test_metrics_emit"
    base_time = time.time()

    # Track if DEBUG_PERFORMANCE event was emitted
    performance_events = []

    def capture_performance(payload):
        performance_events.append(payload)

    event_bus.on(EventTopics.DEBUG_PERFORMANCE, capture_performance)

    # Complete a conversation
    event_bus.emit(EventTopics.VOICE_LISTENING_STARTED, {
        "timestamp": base_time,
        "conversation_id": conversation_id
    })
    event_bus.emit(EventTopics.SPEECH_SYNTHESIS_ENDED, {
        "timestamp": base_time + 3.0,
        "conversation_id": conversation_id
    })

    await asyncio.sleep(0.2)

    # Should have emitted performance metrics
    assert len(performance_events) > 0


@pytest.mark.asyncio
async def test_configuration_options(event_bus):
    """Test service respects configuration."""
    config = {
        "max_conversations": 50,
        "enable_auto_emit": False
    }

    service = LatencyTrackerService(event_bus, config)
    assert service._max_conversations == 50
    assert service._enable_auto_emit is False
