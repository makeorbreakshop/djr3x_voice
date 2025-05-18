"""
Test cases for BrainService
"""

import asyncio
import pytest
import uuid
from unittest.mock import MagicMock, patch

from cantina_os.services.brain_service.brain_service import BrainService
from cantina_os.bus import EventTopics
from cantina_os.event_payloads import IntentPayload, MusicCommandPayload, PlanPayload, LLMResponsePayload

@pytest.fixture
def brain_service():
    """Create a BrainService instance for testing."""
    service = BrainService()
    # Mock the emit method
    service.emit = MagicMock()
    return service

@pytest.mark.asyncio
async def test_brain_service_initialization(brain_service):
    """Test that BrainService initializes correctly."""
    # Setup
    brain_service._emit_dict = MagicMock()
    brain_service._emit_status = MagicMock()
    
    # Call
    await brain_service._start()
    
    # Assert
    assert brain_service._loop is not None
    brain_service._emit_status.assert_called_once()
    
    # Cleanup
    await brain_service._stop()

@pytest.mark.asyncio
async def test_handle_intent_detected_play_music(brain_service):
    """Test handling of play_music intent."""
    # Setup
    brain_service._emit_dict = MagicMock()
    brain_service._emit_status = MagicMock()
    await brain_service._start()
    
    # Create a mock intent payload
    intent_id = str(uuid.uuid4())
    intent_payload = IntentPayload(
        intent_name="play_music",
        parameters={"genre": "jazz"},
        original_text="Play some jazz music",
        event_id=intent_id,
        timestamp=123456.789
    ).model_dump()
    
    # Call
    await brain_service._handle_intent_detected(intent_payload)
    
    # Assert
    # Check that music command was emitted
    assert brain_service._emit_dict.call_count >= 2
    
    # First call should be to MUSIC_COMMAND
    args, kwargs = brain_service._emit_dict.call_args_list[0]
    assert args[0] == EventTopics.MUSIC_COMMAND
    assert isinstance(args[1], dict)
    assert args[1].get("action") == "play"
    assert args[1].get("song_query") == "jazz"
    
    # Second call should be to INTENT_CONSUMED
    args, kwargs = brain_service._emit_dict.call_args_list[1]
    assert args[0] == EventTopics.INTENT_CONSUMED
    assert args[1].get("intent_id") == intent_id
    
    # Check that current intent was stored
    assert brain_service._current_intent is not None
    assert brain_service._current_intent.intent_name == "play_music"
    
    # Cleanup
    await brain_service._stop()

@pytest.mark.asyncio
async def test_handle_music_started(brain_service):
    """Test handling of music started event with track intro generation."""
    # Setup
    brain_service._emit_dict = MagicMock()
    brain_service._emit_status = MagicMock()
    await brain_service._start()
    
    # Create a mock music started payload
    music_payload = {
        "track_metadata": {
            "title": "All That Jazz",
            "artist": "The Cool Cats"
        }
    }
    
    # Call
    await brain_service._handle_music_started(music_payload)
    
    # Wait a moment for the async task to complete
    await asyncio.sleep(0.1)
    
    # Assert
    # Check that track metadata was stored
    assert brain_service._last_track_meta is not None
    assert brain_service._last_track_meta["title"] == "All That Jazz"
    
    # Check that a plan was emitted
    brain_service._emit_dict.assert_called()
    args, kwargs = brain_service._emit_dict.call_args
    assert args[0] == EventTopics.PLAN_READY
    
    # Verify the plan has a speak step
    plan_payload = args[1]
    assert plan_payload["layer"] == "foreground"
    assert len(plan_payload["steps"]) == 1
    assert plan_payload["steps"][0]["type"] == "speak"
    assert "All That Jazz" in plan_payload["steps"][0]["text"]
    assert "The Cool Cats" in plan_payload["steps"][0]["text"]
    
    # Cleanup
    await brain_service._stop()

@pytest.mark.asyncio
async def test_handle_llm_response_track_intro(brain_service):
    """Test handling of LLM response with track intro type."""
    # Setup
    brain_service._emit_dict = MagicMock()
    brain_service._emit_status = MagicMock()
    await brain_service._start()
    
    # Create a mock LLM response payload
    response_payload = LLMResponsePayload(
        text="Now dropping the beat with this awesome track!",
        response_type="track_intro",
        is_complete=True
    ).model_dump()
    
    # Call
    await brain_service._handle_llm_response(response_payload)
    
    # Assert
    # Check that a plan was emitted
    brain_service._emit_dict.assert_called_once()
    args, kwargs = brain_service._emit_dict.call_args
    assert args[0] == EventTopics.PLAN_READY
    
    # Verify the plan has a speak step with the LLM response
    plan_payload = args[1]
    assert plan_payload["layer"] == "foreground"
    assert len(plan_payload["steps"]) == 1
    assert plan_payload["steps"][0]["type"] == "speak"
    assert plan_payload["steps"][0]["text"] == "Now dropping the beat with this awesome track!"
    
    # Cleanup
    await brain_service._stop()

@pytest.mark.asyncio
async def test_handle_non_music_intent(brain_service):
    """Test handling of a non-music intent."""
    # Setup
    brain_service._emit_dict = MagicMock()
    brain_service._emit_status = MagicMock()
    await brain_service._start()
    
    # Create a mock intent payload for a non-handled intent
    intent_payload = IntentPayload(
        intent_name="tell_joke",
        parameters={},
        original_text="Tell me a joke",
        event_id=str(uuid.uuid4()),
        timestamp=123456.789
    ).model_dump()
    
    # Call
    await brain_service._handle_intent_detected(intent_payload)
    
    # Assert
    # Check that nothing was emitted (intent not handled)
    brain_service._emit_dict.assert_not_called()
    
    # Check that current intent was not stored
    assert brain_service._current_intent is None
    
    # Cleanup
    await brain_service._stop() 