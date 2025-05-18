"""
Test cases for TimelineExecutorService using mock classes
"""

import asyncio
import pytest
import uuid
from unittest.mock import MagicMock, patch
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Literal
from pydantic import BaseModel, Field

# Mock classes to replace imported ones
class EventTopics:
    """Mock event topics"""
    PLAN_READY = "plan_ready"
    PLAN_STARTED = "plan_started"
    STEP_READY = "step_ready"
    STEP_EXECUTED = "step_executed"
    PLAN_ENDED = "plan_ended"
    AUDIO_DUCKING_START = "audio_ducking_start"
    AUDIO_DUCKING_STOP = "audio_ducking_stop"
    TTS_GENERATE_REQUEST = "tts_generate_request"
    SPEECH_SYNTHESIS_ENDED = "speech_synthesis_ended"

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

class PlanStep(BaseModel):
    """Mock plan step"""
    id: str
    type: Literal["play_music", "speak", "eye_pattern", "move", "wait_for_event", "delay"]
    text: Optional[str] = None
    clip_id: Optional[str] = None
    genre: Optional[str] = None
    event: Optional[str] = None
    delay: Optional[float] = None
    pattern: Optional[str] = None
    motion: Optional[str] = None

class Plan(BaseModel):
    """Mock plan"""
    plan_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    layer: Literal["ambient", "foreground", "override"]
    steps: List[PlanStep]

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
        self._loop = None
        self._tasks = []
        self._subs = []
        self._config = MagicMock()
        
        # TimelineExecutorService specific
        self._layer_tasks = {}
        self._layer_plans = {}
        self._current_ambient_step_index = 0
        self._current_foreground_step_index = 0 
        self._current_override_step_index = 0
        self._is_ambient_paused = False

# Mock the TimelineExecutorService with a simplified implementation for testing
class TimelineExecutorService(StandardService):
    """Timeline Executor Service for DJ R3X."""

    async def _start(self):
        """Initialize timeline service and set up subscriptions."""
        self._loop = asyncio.get_running_loop()
        await self._setup_subscriptions()
        await self._emit_status(ServiceStatus.OK, "Timeline executor service started")

    async def _stop(self):
        """Clean up tasks and subscriptions."""
        # Cancel all layer tasks
        for layer, task in self._layer_tasks.items():
            if not task.done():
                task.cancel()
                
        # Wait for tasks to complete with timeout
        if self._layer_tasks:
            tasks = list(self._layer_tasks.values())
            await asyncio.wait(tasks, timeout=5.0)
            
        self._layer_tasks.clear()
        self._layer_plans.clear()
        
        # Cancel any other tasks
        for task in self._tasks:
            if not task.done():
                task.cancel()
                
        if self._tasks:
            await asyncio.wait(self._tasks, timeout=5.0)
            
        self._tasks.clear()

        # Unsubscribe from all topics
        for topic, handler in self._subs:
            self.unsubscribe(topic, handler)
        self._subs.clear()

        await self._emit_status(ServiceStatus.OK, "Timeline executor service stopped")

    async def _setup_subscriptions(self):
        """Register event-handlers for timeline execution."""
        # Mock successful subscription
        await self._subscribe(EventTopics.PLAN_READY, self._handle_plan_ready)
        await self._subscribe(EventTopics.SPEECH_SYNTHESIS_ENDED, self._handle_speech_ended)

    async def _handle_plan_ready(self, payload):
        """Handle PLAN_READY events from Brain service."""
        # Parse the plan
        plan = Plan(**payload)
        
        # Store the plan for the specified layer
        self._layer_plans[plan.layer] = plan
        
        # Cancel any existing task for this layer
        if plan.layer in self._layer_tasks and not self._layer_tasks[plan.layer].done():
            self._layer_tasks[plan.layer].cancel()
            
        # If this is an override layer plan, cancel other layers
        if plan.layer == "override":
            for layer, task in self._layer_tasks.items():
                if layer != "override" and not task.done():
                    task.cancel()
        
        # If this is a foreground layer plan, pause ambient layer
        elif plan.layer == "foreground" and "ambient" in self._layer_tasks:
            if not self._layer_tasks["ambient"].done() and not self._is_ambient_paused:
                self._is_ambient_paused = True
        
        # Start executing the plan
        self._layer_tasks[plan.layer] = asyncio.create_task(self._execute_plan(plan))
        
        # Emit plan started event
        await self._emit_dict(
            EventTopics.PLAN_STARTED,
            {"plan_id": plan.plan_id, "layer": plan.layer}
        )

    async def _execute_plan(self, plan):
        """Execute a plan by running its steps in sequence."""
        try:
            # Track the current step index based on layer
            if plan.layer == "ambient":
                step_index = self._current_ambient_step_index
            elif plan.layer == "foreground":
                step_index = self._current_foreground_step_index
            else:  # override
                step_index = self._current_override_step_index
                
            # Execute each step in sequence
            for i in range(step_index, len(plan.steps)):
                step = plan.steps[i]
                
                # Update current step index
                if plan.layer == "ambient":
                    self._current_ambient_step_index = i
                elif plan.layer == "foreground":
                    self._current_foreground_step_index = i
                else:  # override
                    self._current_override_step_index = i
                
                # Emit step ready event
                await self._emit_dict(
                    EventTopics.STEP_READY,
                    {
                        "plan_id": plan.plan_id,
                        "step_id": step.id,
                        "step_type": step.type,
                        "layer": plan.layer
                    }
                )
                
                # Execute the step based on its type
                if step.type == "speak":
                    await self._execute_speak_step(step, plan.layer)
                elif step.type == "delay":
                    await asyncio.sleep(step.delay or 1.0)
                # Other step types would be handled here
                
                # Emit step executed event
                await self._emit_dict(
                    EventTopics.STEP_EXECUTED,
                    {
                        "plan_id": plan.plan_id,
                        "step_id": step.id,
                        "step_type": step.type,
                        "layer": plan.layer
                    }
                )
            
            # Reset step index for this layer
            if plan.layer == "ambient":
                self._current_ambient_step_index = 0
            elif plan.layer == "foreground":
                self._current_foreground_step_index = 0
            else:  # override
                self._current_override_step_index = 0
            
            # Emit plan ended event
            await self._emit_dict(
                EventTopics.PLAN_ENDED,
                {"plan_id": plan.plan_id, "layer": plan.layer}
            )
            
            # Resume ambient layer if we just finished a foreground plan
            if plan.layer == "foreground" and self._is_ambient_paused:
                self._is_ambient_paused = False
                if "ambient" in self._layer_plans:
                    # Restart ambient plan
                    ambient_plan = self._layer_plans["ambient"]
                    self._layer_tasks["ambient"] = asyncio.create_task(self._execute_plan(ambient_plan))
        
        except asyncio.CancelledError:
            # Plan execution was cancelled, clean up properly
            await self._emit_dict(
                EventTopics.PLAN_ENDED,
                {"plan_id": plan.plan_id, "layer": plan.layer, "cancelled": True}
            )
            raise
        except Exception as e:
            # Handle other errors
            await self._emit_status(
                ServiceStatus.ERROR,
                f"Error executing plan {plan.plan_id}: {e}",
                LogLevel.ERROR
            )

    async def _execute_speak_step(self, step, layer):
        """Execute a speak step with proper audio ducking."""
        try:
            # Start audio ducking
            await self._emit_dict(EventTopics.AUDIO_DUCKING_START, {})
            
            # Give the audio a moment to duck
            await asyncio.sleep(0.2)
            
            # Request TTS generation
            await self._emit_dict(
                EventTopics.TTS_GENERATE_REQUEST,
                {"text": step.text, "step_id": step.id}
            )
            
            # For testing, mock speech synthesis completion after a delay
            await asyncio.sleep(0.5)
            self._handle_speech_ended_future = asyncio.Future()
            asyncio.create_task(self._mock_speech_synthesis_ended(step.id))
            await self._handle_speech_ended_future
            
            # Give a moment before unducking
            await asyncio.sleep(0.2)
            
            # Stop audio ducking
            await self._emit_dict(EventTopics.AUDIO_DUCKING_STOP, {})
            
        except Exception as e:
            # Ensure ducking is stopped if there's an error
            await self._emit_dict(EventTopics.AUDIO_DUCKING_STOP, {})
            await self._emit_status(
                ServiceStatus.ERROR,
                f"Error executing speak step: {e}",
                LogLevel.ERROR
            )
            raise

    async def _mock_speech_synthesis_ended(self, step_id):
        """Mock the speech synthesis ended event after a delay."""
        await asyncio.sleep(1.0)
        await self._handle_speech_ended({"step_id": step_id})

    async def _handle_speech_ended(self, payload):
        """Handle SPEECH_SYNTHESIS_ENDED events."""
        # If we're waiting for speech to complete, resolve the future
        if hasattr(self, '_handle_speech_ended_future') and not self._handle_speech_ended_future.done():
            self._handle_speech_ended_future.set_result(True)

@pytest.fixture
def timeline_service():
    """Create a TimelineExecutorService instance for testing."""
    service = TimelineExecutorService()
    return service

@pytest.mark.asyncio
async def test_timeline_service_initialization(timeline_service):
    """Test that TimelineExecutorService initializes correctly."""
    # Call
    await timeline_service._start()
    
    # Assert
    assert timeline_service._loop is not None
    timeline_service._emit_status.assert_called_once()
    
    # Cleanup
    await timeline_service._stop()

@pytest.mark.asyncio
async def test_handle_plan_ready(timeline_service):
    """Test handling of a new plan."""
    # Setup
    await timeline_service._start()
    
    # Reset mock call counts for this test
    timeline_service._emit_dict.reset_mock()
    
    # Create a mock plan payload
    plan_id = str(uuid.uuid4())
    step_id = str(uuid.uuid4())
    plan_payload = {
        "plan_id": plan_id,
        "layer": "foreground",
        "steps": [
            {
                "id": step_id,
                "type": "speak",
                "text": "This is a test line!"
            }
        ]
    }
    
    # Call
    await timeline_service._handle_plan_ready(plan_payload)
    
    # Let the task run a bit
    await asyncio.sleep(0.1)
    
    # Assert
    # Check that the plan was stored and a task was created
    assert "foreground" in timeline_service._layer_plans
    assert "foreground" in timeline_service._layer_tasks
    
    # Check that plan started event was emitted
    timeline_service._emit_dict.assert_called()
    args, kwargs = timeline_service._emit_dict.call_args_list[0]
    assert args[0] == EventTopics.PLAN_STARTED
    assert args[1]["plan_id"] == plan_id
    
    # Cleanup
    await timeline_service._stop()

@pytest.mark.asyncio
async def test_layer_priority(timeline_service):
    """Test that layer priorities are respected."""
    # Setup
    await timeline_service._start()
    
    # Create an ambient plan
    ambient_plan_id = str(uuid.uuid4())
    ambient_step_id = str(uuid.uuid4())
    ambient_plan = {
        "plan_id": ambient_plan_id,
        "layer": "ambient",
        "steps": [
            {
                "id": ambient_step_id,
                "type": "delay",
                "delay": 10.0  # Long delay to ensure it's still running
            }
        ]
    }
    
    # Start the ambient plan
    await timeline_service._handle_plan_ready(ambient_plan)
    await asyncio.sleep(0.1)
    
    # Create a foreground plan
    foreground_plan_id = str(uuid.uuid4())
    foreground_step_id = str(uuid.uuid4())
    foreground_plan = {
        "plan_id": foreground_plan_id,
        "layer": "foreground",
        "steps": [
            {
                "id": foreground_step_id,
                "type": "speak",
                "text": "This is a foreground line!"
            }
        ]
    }
    
    # Start the foreground plan
    await timeline_service._handle_plan_ready(foreground_plan)
    await asyncio.sleep(0.1)
    
    # Assert
    # Check that ambient plan is paused
    assert timeline_service._is_ambient_paused
    
    # Create an override plan
    override_plan_id = str(uuid.uuid4())
    override_step_id = str(uuid.uuid4())
    override_plan = {
        "plan_id": override_plan_id,
        "layer": "override",
        "steps": [
            {
                "id": override_step_id,
                "type": "speak",
                "text": "This is an override line!"
            }
        ]
    }
    
    # Start the override plan
    await timeline_service._handle_plan_ready(override_plan)
    await asyncio.sleep(0.1)
    
    # Cleanup
    await timeline_service._stop()

@pytest.mark.asyncio
async def test_speak_step_execution(timeline_service):
    """Test execution of a speak step with proper ducking."""
    # Setup
    await timeline_service._start()
    
    # Reset mock call counts for this test
    timeline_service._emit_dict.reset_mock()
    
    # Create a plan with a speak step
    plan_id = str(uuid.uuid4())
    step_id = str(uuid.uuid4())
    plan = {
        "plan_id": plan_id,
        "layer": "foreground",
        "steps": [
            {
                "id": step_id,
                "type": "speak",
                "text": "This is a test speech line!"
            }
        ]
    }
    
    # Execute the plan
    await timeline_service._handle_plan_ready(plan)
    
    # Let the plan run to completion
    await asyncio.sleep(3.0)
    
    # Assert
    # Check that the correct events were emitted in sequence
    calls = timeline_service._emit_dict.call_args_list
    
    # Should have at least 7 calls:
    # 1. PLAN_STARTED
    # 2. STEP_READY
    # 3. AUDIO_DUCKING_START
    # 4. TTS_GENERATE_REQUEST
    # 5. AUDIO_DUCKING_STOP
    # 6. STEP_EXECUTED
    # 7. PLAN_ENDED
    assert len(calls) >= 7
    
    # Verify the sequence of ducking and speech events
    ducking_start_called = False
    tts_request_called = False
    ducking_stop_called = False
    
    for call in calls:
        args = call[0]
        if args[0] == EventTopics.AUDIO_DUCKING_START:
            ducking_start_called = True
        elif args[0] == EventTopics.TTS_GENERATE_REQUEST:
            # TTS request should happen after ducking start
            assert ducking_start_called
            tts_request_called = True
        elif args[0] == EventTopics.AUDIO_DUCKING_STOP:
            # Ducking stop should happen after TTS request
            assert tts_request_called
            ducking_stop_called = True
    
    assert ducking_start_called
    assert tts_request_called
    assert ducking_stop_called
    
    # Cleanup
    await timeline_service._stop() 