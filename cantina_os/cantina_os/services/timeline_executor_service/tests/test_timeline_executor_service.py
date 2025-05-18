"""
Test cases for TimelineExecutorService
"""

import asyncio
import pytest
import uuid
from unittest.mock import MagicMock, patch

from cantina_os.services.timeline_executor_service.timeline_executor_service import TimelineExecutorService
from cantina_os.bus import EventTopics
from cantina_os.event_payloads import (
    PlanPayload,
    PlanStep,
    PlanStartedPayload,
    StepReadyPayload,
    StepExecutedPayload,
    PlanEndedPayload
)

@pytest.fixture
def timeline_service():
    """Create a TimelineExecutorService instance for testing."""
    service = TimelineExecutorService()
    # Mock the emit method
    service.emit = MagicMock()
    return service

@pytest.mark.asyncio
async def test_timeline_service_initialization(timeline_service):
    """Test that TimelineExecutorService initializes correctly."""
    # Setup
    timeline_service._emit_dict = MagicMock()
    timeline_service._emit_status = MagicMock()
    
    # Call
    await timeline_service._start()
    
    # Assert
    assert timeline_service._loop is not None
    assert len(timeline_service._layer_events) == 3  # ambient, foreground, override
    timeline_service._emit_status.assert_called_once()
    
    # Cleanup
    await timeline_service._stop()

@pytest.mark.asyncio
async def test_handle_plan_ready_simple(timeline_service):
    """Test handling of a simple foreground plan."""
    # Setup
    timeline_service._emit_dict = MagicMock()
    timeline_service._emit_status = MagicMock()
    timeline_service._run_plan = MagicMock()  # Mock _run_plan to avoid actual execution
    await timeline_service._start()
    
    # Create a simple plan payload
    plan_id = str(uuid.uuid4())
    plan_payload = PlanPayload(
        plan_id=plan_id,
        layer="foreground",
        steps=[
            PlanStep(
                id="speak_step",
                type="speak",
                text="Hello, world!"
            )
        ]
    ).model_dump()
    
    # Call
    await timeline_service._handle_plan_ready(plan_payload)
    
    # Assert
    # Check that plan started event was emitted
    timeline_service._emit_dict.assert_called_once()
    args, kwargs = timeline_service._emit_dict.call_args
    assert args[0] == EventTopics.PLAN_STARTED
    assert args[1]["plan_id"] == plan_id
    assert args[1]["layer"] == "foreground"
    
    # Check that plan execution was started
    assert plan_id in timeline_service._active_plans
    assert "foreground" in timeline_service._layer_tasks
    
    # Cleanup
    timeline_service._layer_tasks["foreground"].cancel()
    await timeline_service._stop()

@pytest.mark.asyncio
async def test_run_plan_speak_step(timeline_service):
    """Test running a plan with a speak step."""
    # Setup
    timeline_service._emit_dict = MagicMock()
    timeline_service._emit_status = MagicMock()
    timeline_service._execute_step = MagicMock(return_value=(True, {"text": "Hello, world!"}))
    await timeline_service._start()
    
    # Create a simple plan with a speak step
    plan_id = str(uuid.uuid4())
    plan = PlanPayload(
        plan_id=plan_id,
        layer="foreground",
        steps=[
            PlanStep(
                id="speak_step",
                type="speak",
                text="Hello, world!"
            )
        ]
    )
    
    # Call
    task = asyncio.create_task(timeline_service._run_plan(plan))
    # Give it a moment to execute
    await asyncio.sleep(0.1)
    
    # Assert
    # Check that step ready and step executed events were emitted
    assert timeline_service._emit_dict.call_count >= 3
    
    # First call should be STEP_READY
    call_args = timeline_service._emit_dict.call_args_list
    assert call_args[0][0][0] == EventTopics.STEP_READY
    assert call_args[0][0][1]["plan_id"] == plan_id
    assert call_args[0][0][1]["step_id"] == "speak_step"
    
    # Second call should be STEP_EXECUTED
    assert call_args[1][0][0] == EventTopics.STEP_EXECUTED
    assert call_args[1][0][1]["plan_id"] == plan_id
    assert call_args[1][0][1]["step_id"] == "speak_step"
    assert call_args[1][0][1]["status"] == "success"
    
    # Third call should be PLAN_ENDED
    assert call_args[2][0][0] == EventTopics.PLAN_ENDED
    assert call_args[2][0][1]["plan_id"] == plan_id
    assert call_args[2][0][1]["layer"] == "foreground"
    assert call_args[2][0][1]["status"] == "completed"
    
    # Cleanup
    if not task.done():
        task.cancel()
    await timeline_service._stop()

@pytest.mark.asyncio
async def test_execute_speak_step(timeline_service):
    """Test executing a speak step including audio ducking."""
    # Setup
    timeline_service._emit_dict = MagicMock()
    timeline_service._emit_status = MagicMock()
    await timeline_service._start()
    
    # Create a speak step
    step = PlanStep(
        id="speak_step",
        type="speak",
        text="Hello, world!"
    )
    
    # Mock handling of speech ended event
    async def mock_speech_ended(delay=0.1):
        await asyncio.sleep(delay)
        await timeline_service._handle_speech_ended({"clip_id": "speak_step"})
    
    # Start the speech ended handler in the background
    speech_task = asyncio.create_task(mock_speech_ended())
    
    # Call
    result, details = await timeline_service._execute_speak_step(step, "test_plan_id")
    
    # Assert
    # Check that the step was successful
    assert result is True
    assert details["text"] == "Hello, world!"
    
    # Check that ducking start and TTS request were emitted
    assert timeline_service._emit_dict.call_count >= 3
    
    # First call should be AUDIO_DUCKING_START
    call_args = timeline_service._emit_dict.call_args_list
    assert call_args[0][0][0] == EventTopics.AUDIO_DUCKING_START
    
    # Second call should be TTS_GENERATE_REQUEST
    assert call_args[1][0][0] == EventTopics.TTS_GENERATE_REQUEST
    assert call_args[1][0][1]["text"] == "Hello, world!"
    
    # Third call should be AUDIO_DUCKING_STOP
    assert call_args[2][0][0] == EventTopics.AUDIO_DUCKING_STOP
    
    # Cleanup
    if not speech_task.done():
        speech_task.cancel()
    await timeline_service._stop()

@pytest.mark.asyncio
async def test_layer_priority_override(timeline_service):
    """Test that override layer cancels lower priority layers."""
    # Setup
    timeline_service._emit_dict = MagicMock()
    timeline_service._emit_status = MagicMock()
    timeline_service._run_plan = MagicMock()  # Mock _run_plan to avoid actual execution
    await timeline_service._start()
    
    # Create mock tasks for ambient and foreground layers
    ambient_task = MagicMock()
    ambient_task.done = MagicMock(return_value=False)
    ambient_task.cancel = MagicMock()
    
    foreground_task = MagicMock()
    foreground_task.done = MagicMock(return_value=False)
    foreground_task.cancel = MagicMock()
    
    timeline_service._layer_tasks = {
        "ambient": ambient_task,
        "foreground": foreground_task
    }
    
    # Create an override plan
    plan_id = str(uuid.uuid4())
    plan_payload = PlanPayload(
        plan_id=plan_id,
        layer="override",
        steps=[
            PlanStep(
                id="override_step",
                type="speak",
                text="Emergency override!"
            )
        ]
    ).model_dump()
    
    # Call
    await timeline_service._handle_plan_ready(plan_payload)
    
    # Assert
    # Check that both lower priority tasks were cancelled
    ambient_task.cancel.assert_called_once()
    foreground_task.cancel.assert_called_once()
    
    # Cleanup
    timeline_service._layer_tasks["override"].cancel()
    await timeline_service._stop() 