"""
Test that the LatencyTrackerService receives events correctly after enum fix.

This test verifies that EventTopics enums are properly converted to their string
values when emitting and subscribing to events, fixing the issue where the
LatencyTrackerService wasn't receiving any events.
"""

import pytest
import asyncio
import time
from pyee.asyncio import AsyncIOEventEmitter

from cantina_os.core.event_topics import EventTopics
from cantina_os.services.latency_tracker_service.latency_tracker_service import LatencyTrackerService


@pytest.mark.asyncio
async def test_latency_tracker_receives_enum_events():
    """Test that LatencyTrackerService receives events when using EventTopics enums."""

    # Create event bus
    event_bus = AsyncIOEventEmitter()

    # Create latency tracker service
    latency_service = LatencyTrackerService(event_bus=event_bus)
    await latency_service.start()

    # Give subscriptions time to register
    await asyncio.sleep(0.1)

    # Simulate a voice interaction pipeline
    conversation_id = "test-conversation-123"
    base_time = time.time()

    # Step 1: Voice listening started
    await latency_service.emit(
        EventTopics.VOICE_LISTENING_STARTED,
        {
            "conversation_id": conversation_id,
            "timestamp": base_time
        }
    )
    await asyncio.sleep(0.05)

    # Step 2: Transcription complete
    await latency_service.emit(
        EventTopics.TRANSCRIPTION_FINAL,
        {
            "conversation_id": conversation_id,
            "timestamp": base_time + 0.5,
            "text": "test transcription"
        }
    )
    await asyncio.sleep(0.05)

    # Step 3: LLM response
    await latency_service.emit(
        EventTopics.LLM_RESPONSE_TEXT,
        {
            "conversation_id": conversation_id,
            "timestamp": base_time + 2.0,
            "response_text": "test response"
        }
    )
    await asyncio.sleep(0.05)

    # Step 4: TTS started
    await latency_service.emit(
        EventTopics.SPEECH_SYNTHESIS_STARTED,
        {
            "conversation_id": conversation_id,
            "timestamp": base_time + 2.2
        }
    )
    await asyncio.sleep(0.05)

    # Step 5: TTS ended
    await latency_service.emit(
        EventTopics.SPEECH_SYNTHESIS_ENDED,
        {
            "conversation_id": conversation_id,
            "timestamp": base_time + 10.0
        }
    )
    await asyncio.sleep(0.1)

    # Verify that latency data was captured
    summary = latency_service.get_conversation_summary(conversation_id)

    assert summary is not None, "Latency tracker should have captured conversation data"
    assert summary["conversation_id"] == conversation_id
    assert summary["transcription_latency"] is not None
    assert summary["llm_latency"] is not None
    assert summary["tts_generation_latency"] is not None
    assert summary["total_latency"] is not None

    # Verify latency calculations are reasonable
    assert abs(summary["transcription_latency"] - 0.5) < 0.1
    assert abs(summary["llm_latency"] - 1.5) < 0.1
    assert abs(summary["tts_generation_latency"] - 0.2) < 0.1
    assert abs(summary["total_latency"] - 10.0) < 0.1

    # Cleanup
    await latency_service.stop()


@pytest.mark.asyncio
async def test_latency_tracker_aggregate_report():
    """Test that aggregate latency report works with multiple conversations."""

    event_bus = AsyncIOEventEmitter()
    latency_service = LatencyTrackerService(event_bus=event_bus)
    await latency_service.start()
    await asyncio.sleep(0.1)

    # Simulate 3 conversations
    for i in range(3):
        conversation_id = f"test-conv-{i}"
        base_time = time.time()

        await latency_service.emit(EventTopics.VOICE_LISTENING_STARTED, {
            "conversation_id": conversation_id,
            "timestamp": base_time
        })
        await asyncio.sleep(0.02)

        await latency_service.emit(EventTopics.TRANSCRIPTION_FINAL, {
            "conversation_id": conversation_id,
            "timestamp": base_time + 0.5
        })
        await asyncio.sleep(0.02)

        await latency_service.emit(EventTopics.LLM_RESPONSE_TEXT, {
            "conversation_id": conversation_id,
            "timestamp": base_time + 2.0
        })
        await asyncio.sleep(0.02)

        await latency_service.emit(EventTopics.SPEECH_SYNTHESIS_STARTED, {
            "conversation_id": conversation_id,
            "timestamp": base_time + 2.2
        })
        await asyncio.sleep(0.02)

        await latency_service.emit(EventTopics.SPEECH_SYNTHESIS_ENDED, {
            "conversation_id": conversation_id,
            "timestamp": base_time + 10.0
        })
        await asyncio.sleep(0.02)

    # Get aggregate report
    report = latency_service.get_latency_report()

    assert report["total_conversations"] == 3
    assert report["avg_transcription_latency"] is not None
    assert report["avg_llm_latency"] is not None
    assert report["avg_tts_generation_latency"] is not None
    assert report["avg_total_latency"] is not None

    await latency_service.stop()


if __name__ == "__main__":
    # Run tests directly
    asyncio.run(test_latency_tracker_receives_enum_events())
    print("✓ test_latency_tracker_receives_enum_events PASSED")

    asyncio.run(test_latency_tracker_aggregate_report())
    print("✓ test_latency_tracker_aggregate_report PASSED")

    print("\nAll tests passed!")
