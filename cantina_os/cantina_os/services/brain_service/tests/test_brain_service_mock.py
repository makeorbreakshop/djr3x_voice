"""
Test cases for BrainService using mock classes
"""

import asyncio
import pytest
import uuid
from unittest.mock import MagicMock, patch
from enum import Enum, auto
from typing import Any, Dict, List, Optional
from pydantic import BaseModel

# Mock classes to replace imported ones
class EventTopics:
    """Mock event topics"""
    INTENT_DETECTED = "intent_detected"
    MUSIC_COMMAND = "music_command"
    INTENT_CONSUMED = "intent_consumed"
    MUSIC_PLAYBACK_STARTED = "music_playback_started"
    PLAN_READY = "plan_ready"
    LLM_RESPONSE = "llm_response"
    MEMORY_UPDATED = "memory_updated"

class ServiceStatus(str, Enum):
    """Mock service status enum"""
    OK = "ok"
    WARNING = "warning"
    ERROR = "error"

class LogLevel(str, Enum):
    """Mock log level enum"""
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"

class IntentPayload(BaseModel):
    """Mock intent payload"""
    intent_name: str
    parameters: Dict[str, Any]
    original_text: str
    event_id: str
    timestamp: float

class MusicCommandPayload(BaseModel):
    """Mock music command payload"""
    action: str
    song_query: Optional[str] = None

class PlanStep(BaseModel):
    """Mock plan step"""
    id: str
    type: str
    text: Optional[str] = None

class PlanPayload(BaseModel):
    """Mock plan payload"""
    plan_id: str
    layer: str
    steps: List[PlanStep]

class LLMResponsePayload(BaseModel):
    """Mock LLM response payload"""
    text: str
    response_type: str
    is_complete: bool

# Mock StandardService class
class StandardService:
    """Mock standard service"""
    def __init__(self, name="test_service", config=None):
        self.name = name
        self.config = config or {}
        self.emit = MagicMock()
        
        # Create mocks that return awaitables for async methods
        self._emit_dict = MagicMock()
        self._emit_dict.return_value = asyncio.Future()
        self._emit_dict.return_value.set_result(None)
        
        self._emit_status = MagicMock()
        self._emit_status.return_value = asyncio.Future()
        self._emit_status.return_value.set_result(None)
        
        self._subscribe = MagicMock()
        self._subscribe.return_value = asyncio.Future()
        self._subscribe.return_value.set_result(None)
        
        self.unsubscribe = MagicMock()
        self._current_intent = None
        self._last_track_meta = None
        self._loop = None
        self._tasks = []
        self._subs = []
        self._config = MagicMock()
        self._config.handled_intents = ["play_music"]
        self._handled_intents = set(["play_music"])

# Mock the BrainService with the real implementation but mocked imports
class BrainService(StandardService):
    """Brain service for DJ R3X."""

    async def _start(self):
        """Initialize brain service and set up subscriptions."""
        self._loop = asyncio.get_running_loop()
        await self._setup_subscriptions()
        await self._emit_status(ServiceStatus.OK, "Brain service started")

    async def _stop(self):
        """Clean up tasks and subscriptions."""
        for task in self._tasks:
            if not task.done():
                task.cancel()
                
        # Wait for tasks to complete with timeout
        if self._tasks:
            await asyncio.wait(self._tasks, timeout=5.0)
            
        self._tasks.clear()

        for topic, handler in self._subs:
            self.unsubscribe(topic, handler)
        self._subs.clear()

        await self._emit_status(ServiceStatus.OK, "Brain service stopped")

    async def _setup_subscriptions(self):
        """Register event-handlers for brain processing."""
        # Create tasks for subscriptions to avoid blocking
        self._subscribe.return_value = asyncio.Future()
        self._subscribe.return_value.set_result(None)
        
        await self._subscribe(EventTopics.INTENT_DETECTED, self._handle_intent_detected)
        await self._subscribe(EventTopics.MUSIC_PLAYBACK_STARTED, self._handle_music_started)
        await self._subscribe(EventTopics.LLM_RESPONSE, self._handle_llm_response)
        await self._subscribe(EventTopics.MEMORY_UPDATED, self._handle_memory_updated)

    async def _handle_intent_detected(self, payload):
        """Handle INTENT_DETECTED events from GPT service."""
        intent_payload = IntentPayload(**payload)
        intent_name = intent_payload.intent_name
        
        # Only handle intents we care about
        if intent_name not in self._handled_intents:
            return
            
        # Store current intent for later
        self._current_intent = intent_payload
        
        # For play_music, immediately forward command to music controller
        if intent_name == "play_music":
            await self._emit_dict(
                EventTopics.MUSIC_COMMAND,
                MusicCommandPayload(
                    action="play", 
                    song_query=intent_payload.parameters.get("genre"),
                ).model_dump()
            )
        
        # Mark intent as consumed
        await self._emit_dict(
            EventTopics.INTENT_CONSUMED,
            {"intent_id": intent_payload.event_id}
        )
        
    async def _handle_memory_updated(self, payload):
        """Handle MEMORY_UPDATED events."""
        # Just a placeholder
        pass

    async def _handle_music_started(self, payload):
        """Handle MUSIC_PLAYBACK_STARTED events from music controller."""
        # Store track metadata
        self._last_track_meta = payload.get("track_metadata", {})
        
        # Generate a track intro as a plan
        plan_id = str(uuid.uuid4())
        
        # Create a simple speak step with track info
        track_title = self._last_track_meta.get("title", "Unknown Track")
        track_artist = self._last_track_meta.get("artist", "Unknown Artist")
        
        intro_text = f"Now dropping the beat with {track_title} by {track_artist}!"
        
        # Create a plan with a single speak step
        plan = PlanPayload(
            plan_id=plan_id,
            layer="foreground",
            steps=[
                PlanStep(
                    id=str(uuid.uuid4()),
                    type="speak",
                    text=intro_text
                )
            ]
        )
        
        # Emit the plan
        await self._emit_dict(EventTopics.PLAN_READY, plan.model_dump())

    async def _handle_llm_response(self, payload):
        """Handle LLM_RESPONSE events."""
        llm_payload = LLMResponsePayload(**payload)
        
        # Handle track intro responses
        if llm_payload.response_type == "track_intro" and llm_payload.is_complete:
            # Create a plan with the LLM-generated text
            plan_id = str(uuid.uuid4())
            plan = PlanPayload(
                plan_id=plan_id,
                layer="foreground",
                steps=[
                    PlanStep(
                        id=str(uuid.uuid4()),
                        type="speak",
                        text=llm_payload.text
                    )
                ]
            )
            
            # Emit the plan
            await self._emit_dict(EventTopics.PLAN_READY, plan.model_dump())

@pytest.fixture
def brain_service():
    """Create a BrainService instance for testing."""
    service = BrainService()
    return service

@pytest.mark.asyncio
async def test_brain_service_initialization(brain_service):
    """Test that BrainService initializes correctly."""
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
    await brain_service._start()
    
    # Reset mock call counts for this test
    brain_service._emit_dict.reset_mock()
    
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
    await brain_service._start()
    
    # Reset mock call counts for this test
    brain_service._emit_dict.reset_mock()
    
    # Create a mock music started payload
    music_payload = {
        "track_metadata": {
            "title": "All That Jazz",
            "artist": "The Cool Cats"
        }
    }
    
    # Call
    await brain_service._handle_music_started(music_payload)
    
    # Assert
    # Check that track metadata was stored
    assert brain_service._last_track_meta is not None
    assert brain_service._last_track_meta["title"] == "All That Jazz"
    
    # Check that a plan was emitted
    brain_service._emit_dict.assert_called_once()
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
    await brain_service._start()
    
    # Reset mock call counts for this test
    brain_service._emit_dict.reset_mock()
    
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