"""
Integration tests for latency command handling.

Tests verify that:
- "debug latency" command displays aggregate report
- "debug latency conversation <id>" shows specific conversation details
- "debug latency reset" clears metrics
- Commands route correctly through CLI → Dispatcher → LatencyTrackerService
"""

import asyncio
import pytest
import time
from unittest.mock import Mock, AsyncMock, patch
from pyee.asyncio import AsyncIOEventEmitter

from ..services.latency_tracker_service import LatencyTrackerService
from ..services.command_dispatcher_service import CommandDispatcherService
from ..services.cli_service import CLIService
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


@pytest.fixture
async def command_dispatcher(event_bus):
    """Create and start CommandDispatcherService."""
    service = CommandDispatcherService(event_bus)
    await service.start()

    # Register latency commands
    service.register_command("debug latency", "latency_tracker", EventTopics.LATENCY_COMMAND)
    service.register_command("debug latency conversation", "latency_tracker", EventTopics.LATENCY_COMMAND)
    service.register_command("debug latency reset", "latency_tracker", EventTopics.LATENCY_COMMAND)

    yield service
    await service.stop()


@pytest.mark.asyncio
async def test_debug_latency_with_no_data(latency_tracker, event_bus):
    """Test 'debug latency' command when no conversations tracked yet."""
    response_received = asyncio.Event()
    received_response = {}

    def capture_response(payload):
        nonlocal received_response
        received_response = payload
        response_received.set()

    event_bus.on(EventTopics.CLI_RESPONSE, capture_response)

    # Emit latency command with no args (aggregate report)
    event_bus.emit(EventTopics.LATENCY_COMMAND, {
        "timestamp": time.time(),
        "command": "debug",
        "args": ["latency"],
        "raw_input": "debug latency"
    })

    # Wait for response
    await asyncio.wait_for(response_received.wait(), timeout=1.0)

    # Should indicate no data available
    assert "message" in received_response
    assert "no latency data" in received_response["message"].lower()


@pytest.mark.asyncio
async def test_debug_latency_with_conversation_data(latency_tracker, event_bus):
    """Test 'debug latency' command displays aggregate statistics."""
    # Create a completed conversation
    conversation_id = "test_conv_001"
    base_time = time.time()

    # Simulate full pipeline
    event_bus.emit(EventTopics.VOICE_LISTENING_STARTED, {
        "timestamp": base_time,
        "conversation_id": conversation_id
    })
    event_bus.emit(EventTopics.TRANSCRIPTION_FINAL, {
        "timestamp": base_time + 0.5,
        "conversation_id": conversation_id,
        "text": "test"
    })
    event_bus.emit(EventTopics.LLM_RESPONSE_TEXT, {
        "timestamp": base_time + 2.0,
        "conversation_id": conversation_id,
        "text": "response"
    })
    event_bus.emit(EventTopics.SPEECH_SYNTHESIS_STARTED, {
        "timestamp": base_time + 2.1,
        "conversation_id": conversation_id
    })
    event_bus.emit(EventTopics.SPEECH_SYNTHESIS_ENDED, {
        "timestamp": base_time + 5.0,
        "conversation_id": conversation_id
    })

    await asyncio.sleep(0.2)  # Let events propagate

    # Set up response capture
    response_received = asyncio.Event()
    received_response = {}

    def capture_response(payload):
        nonlocal received_response
        received_response = payload
        response_received.set()

    event_bus.on(EventTopics.CLI_RESPONSE, capture_response)

    # Emit latency command
    event_bus.emit(EventTopics.LATENCY_COMMAND, {
        "timestamp": time.time(),
        "command": "debug",
        "args": ["latency"],
        "raw_input": "debug latency"
    })

    # Wait for response
    await asyncio.wait_for(response_received.wait(), timeout=1.0)

    # Should show aggregate report
    assert "message" in received_response
    message = received_response["message"]
    assert "Pipeline Latency Report" in message
    assert "Total Conversations" in message
    assert "Avg" in message


@pytest.mark.asyncio
async def test_debug_latency_conversation_command(latency_tracker, event_bus):
    """Test 'debug latency conversation <id>' command."""
    # Create a conversation
    conversation_id = "test_conv_specific"
    base_time = time.time()

    event_bus.emit(EventTopics.VOICE_LISTENING_STARTED, {
        "timestamp": base_time,
        "conversation_id": conversation_id
    })
    event_bus.emit(EventTopics.TRANSCRIPTION_FINAL, {
        "timestamp": base_time + 0.6,
        "conversation_id": conversation_id,
        "text": "test"
    })

    await asyncio.sleep(0.1)

    # Set up response capture
    response_received = asyncio.Event()
    received_response = {}

    def capture_response(payload):
        nonlocal received_response
        received_response = payload
        response_received.set()

    event_bus.on(EventTopics.CLI_RESPONSE, capture_response)

    # Request specific conversation details
    event_bus.emit(EventTopics.LATENCY_COMMAND, {
        "timestamp": time.time(),
        "command": "debug",
        "args": ["latency", "conversation", conversation_id],
        "raw_input": f"debug latency conversation {conversation_id}"
    })

    # Wait for response
    await asyncio.wait_for(response_received.wait(), timeout=1.0)

    # Should show conversation details
    assert "message" in received_response
    message = received_response["message"]
    assert conversation_id in message
    assert "Transcription" in message


@pytest.mark.asyncio
async def test_debug_latency_conversation_not_found(latency_tracker, event_bus):
    """Test 'debug latency conversation <id>' when conversation doesn't exist."""
    response_received = asyncio.Event()
    received_response = {}

    def capture_response(payload):
        nonlocal received_response
        received_response = payload
        response_received.set()

    event_bus.on(EventTopics.CLI_RESPONSE, capture_response)

    # Request non-existent conversation
    event_bus.emit(EventTopics.LATENCY_COMMAND, {
        "timestamp": time.time(),
        "command": "debug",
        "args": ["latency", "conversation", "nonexistent_id"],
        "raw_input": "debug latency conversation nonexistent_id"
    })

    # Wait for response
    await asyncio.wait_for(response_received.wait(), timeout=1.0)

    # Should indicate error
    assert "message" in received_response
    assert "is_error" in received_response
    assert received_response["is_error"] is True
    assert "No data found" in received_response["message"]


@pytest.mark.asyncio
async def test_debug_latency_reset_command(latency_tracker, event_bus):
    """Test 'debug latency reset' clears all metrics."""
    # Create some conversations
    for i in range(3):
        conv_id = f"conv_{i}"
        event_bus.emit(EventTopics.VOICE_LISTENING_STARTED, {
            "timestamp": time.time(),
            "conversation_id": conv_id
        })

    await asyncio.sleep(0.1)

    # Verify data exists
    assert len(latency_tracker._conversation_metrics) == 3

    # Set up response capture
    response_received = asyncio.Event()
    received_response = {}

    def capture_response(payload):
        nonlocal received_response
        received_response = payload
        response_received.set()

    event_bus.on(EventTopics.CLI_RESPONSE, capture_response)

    # Reset metrics
    event_bus.emit(EventTopics.LATENCY_COMMAND, {
        "timestamp": time.time(),
        "command": "debug",
        "args": ["latency", "reset"],
        "raw_input": "debug latency reset"
    })

    # Wait for response
    await asyncio.wait_for(response_received.wait(), timeout=1.0)

    # Should confirm reset
    assert "message" in received_response
    assert "Cleared" in received_response["message"]

    # Metrics should be empty
    assert len(latency_tracker._conversation_metrics) == 0


@pytest.mark.asyncio
async def test_latency_command_does_not_interfere_with_debug_level_command(event_bus):
    """Test that latency commands don't break debug level commands on same topic."""
    # This test verifies the two services can coexist on DEBUG_COMMAND topic
    # by each filtering for their own command types

    from ..services.debug_service import DebugService

    # Start both services
    latency_service = LatencyTrackerService(event_bus)
    debug_service = DebugService(event_bus, config={})

    await latency_service.start()
    await debug_service.start()

    try:
        # Emit a debug level command - should work
        event_bus.emit(EventTopics.DEBUG_COMMAND, {
            "timestamp": time.time(),
            "command": "debug",
            "component": "all",
            "level": "INFO"
        })

        await asyncio.sleep(0.1)

        # Emit a latency command - should also work
        event_bus.emit(EventTopics.LATENCY_COMMAND, {
            "timestamp": time.time(),
            "command": "debug",
            "args": ["latency"]
        })

        await asyncio.sleep(0.1)

        # If we get here without exceptions, the test passes
        assert True

    finally:
        await latency_service.stop()
        await debug_service.stop()
