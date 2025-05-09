"""
Unit tests for SyncEventBus

Tests the core functionality of the synchronous event bus implementation,
focusing on registration guarantees, handler lifecycle, and thread safety.
"""

import pytest
import pytest_asyncio
import asyncio
from typing import Dict, Any, List
from pydantic import BaseModel

from src.bus.sync_event_bus import SyncEventBus

# Test Models
class TestEventPayload(BaseModel):
    """Test event payload model."""
    message: str
    value: int

# Fixtures
@pytest_asyncio.fixture
async def event_bus():
    """Create a fresh event bus for each test."""
    return SyncEventBus(loop=asyncio.get_running_loop())

# Tests
@pytest.mark.asyncio
async def test_sync_registration(event_bus):
    """Test that event registration is properly synchronized."""
    received_events = []
    
    async def test_handler(payload: Dict[str, Any]):
        received_events.append(payload)
        
    # Register handler
    await event_bus.sync_on("test/topic", test_handler)
    
    # Emit event immediately
    test_payload = TestEventPayload(message="test", value=42)
    await event_bus.emit("test/topic", test_payload)
    
    # Verify event was received
    assert len(received_events) == 1
    assert received_events[0]["message"] == "test"
    assert received_events[0]["value"] == 42

@pytest.mark.asyncio
async def test_handler_lifecycle(event_bus):
    """Test proper handler registration and cleanup."""
    received_events = []
    
    async def test_handler(payload: Dict[str, Any]):
        received_events.append(payload)
    
    # Register handler
    await event_bus.sync_on("test/lifecycle", test_handler)
    
    # Verify handler is registered
    assert "test/lifecycle" in event_bus._handler_registry
    assert test_handler in event_bus._handler_registry["test/lifecycle"]
    
    # Remove handler
    event_bus.remove_listener("test/lifecycle", test_handler)
    
    # Verify handler is removed
    assert "test/lifecycle" not in event_bus._handler_registry
    
    # Emit event - should not be received
    await event_bus.emit("test/lifecycle", TestEventPayload(message="test", value=1))
    assert len(received_events) == 0

@pytest.mark.asyncio
async def test_concurrent_handlers(event_bus):
    """Test multiple handlers receiving events concurrently."""
    results: Dict[str, List[Dict[str, Any]]] = {
        "handler1": [],
        "handler2": [],
        "handler3": []
    }
    
    # Create handlers with different processing times
    async def handler1(payload: Dict[str, Any]):
        await asyncio.sleep(0.1)  # Simulate processing
        results["handler1"].append(payload)
        
    async def handler2(payload: Dict[str, Any]):
        await asyncio.sleep(0.05)  # Faster processing
        results["handler2"].append(payload)
        
    async def handler3(payload: Dict[str, Any]):
        results["handler3"].append(payload)  # Immediate processing
    
    # Register all handlers
    await event_bus.sync_on("test/concurrent", handler1)
    await event_bus.sync_on("test/concurrent", handler2)
    await event_bus.sync_on("test/concurrent", handler3)
    
    # Emit test event
    test_payload = TestEventPayload(message="concurrent", value=42)
    await event_bus.emit("test/concurrent", test_payload)
    
    # Verify all handlers received the event
    for handler_results in results.values():
        assert len(handler_results) == 1
        assert handler_results[0]["message"] == "concurrent"
        assert handler_results[0]["value"] == 42

@pytest.mark.asyncio
async def test_error_handling(event_bus):
    """Test error handling in event processing."""
    success_events = []
    
    async def error_handler(payload: Dict[str, Any]):
        raise ValueError("Test error")
        
    async def success_handler(payload: Dict[str, Any]):
        success_events.append(payload)
    
    # Register both handlers
    await event_bus.sync_on("test/error", error_handler)
    await event_bus.sync_on("test/error", success_handler)
    
    # Emit event - error handler should not prevent success handler
    await event_bus.emit("test/error", TestEventPayload(message="test", value=42))
    
    # Verify success handler still received event
    assert len(success_events) == 1
    assert success_events[0]["message"] == "test"

@pytest.mark.asyncio
async def test_payload_conversion(event_bus):
    """Test proper handling of different payload types."""
    received_payloads = []
    
    async def test_handler(payload: Dict[str, Any]):
        received_payloads.append(payload)
    
    await event_bus.sync_on("test/payload", test_handler)
    
    # Test Pydantic model payload
    model_payload = TestEventPayload(message="model", value=1)
    await event_bus.emit("test/payload", model_payload)
    
    # Test dict payload
    dict_payload = {"message": "dict", "value": 2}
    await event_bus.emit("test/payload", dict_payload)
    
    # Test no payload
    await event_bus.emit("test/payload")
    
    # Verify all payloads were handled correctly
    assert len(received_payloads) == 3
    assert received_payloads[0]["message"] == "model"
    assert received_payloads[1]["message"] == "dict"
    assert received_payloads[2] == {}

@pytest.mark.asyncio
async def test_handler_tracking_and_cleanup(event_bus):
    """Test comprehensive handler tracking and cleanup mechanism."""
    # Track received events
    received_events = []
    
    # Create multiple handlers
    async def handler1(payload: Dict[str, Any]):
        received_events.append(("handler1", payload))
        
    async def handler2(payload: Dict[str, Any]):
        received_events.append(("handler2", payload))
    
    # Register handlers on the same topic
    topic = "test/tracking"
    await event_bus.on(topic, handler1)
    await event_bus.on(topic, handler2)
    
    # Emit test event
    test_payload = {"message": "test tracking"}
    await event_bus.emit(topic, test_payload)
    
    # Verify both handlers were called
    assert len(received_events) == 2
    assert ("handler1", test_payload) in received_events
    assert ("handler2", test_payload) in received_events
    
    # Remove one handler
    event_bus.remove_listener(topic, handler1)
    
    # Reset received events
    received_events.clear()
    
    # Emit another event
    await event_bus.emit(topic, test_payload)
    
    # Verify only remaining handler was called
    assert len(received_events) == 1
    assert ("handler2", test_payload) in received_events
    
    # Remove all remaining listeners for the topic
    event_bus.remove_all_listeners(topic)
    
    # Reset received events
    received_events.clear()
    
    # Emit event after removing all listeners
    await event_bus.emit(topic, test_payload)
    
    # Verify no handlers were called
    assert len(received_events) == 0

@pytest.mark.asyncio
async def test_handler_cleanup_multiple_topics(event_bus):
    """Test handler cleanup across multiple topics."""
    # Track received events
    received_events = []
    
    # Create handlers for multiple topics
    async def handler1(payload: Dict[str, Any]):
        received_events.append(("handler1", payload))
        
    async def handler2(payload: Dict[str, Any]):
        received_events.append(("handler2", payload))
    
    # Register handlers on different topics
    topic1 = "test/topic1"
    topic2 = "test/topic2"
    await event_bus.on(topic1, handler1)
    await event_bus.on(topic2, handler2)
    
    # Emit test events
    await event_bus.emit(topic1, {"message": "topic1 test"})
    await event_bus.emit(topic2, {"message": "topic2 test"})
    
    # Verify both handlers were called
    assert len(received_events) == 2
    
    # Remove all listeners
    event_bus.remove_all_listeners()
    
    # Reset received events
    received_events.clear()
    
    # Emit events after removing all listeners
    await event_bus.emit(topic1, {"message": "topic1 test"})
    await event_bus.emit(topic2, {"message": "topic2 test"})
    
    # Verify no handlers were called
    assert len(received_events) == 0

@pytest.mark.asyncio
async def test_handler_error_handling(event_bus):
    """Test error handling during handler registration and execution."""
    # Create a handler that will raise an error
    error_raised = False
    
    async def error_handler(payload: Dict[str, Any]):
        nonlocal error_raised
        error_raised = True
        raise ValueError("Test error handling")
    
    # Register error-prone handler
    topic = "test/error"
    await event_bus.on(topic, error_handler)
    
    # Emit event and verify error is logged but doesn't break event bus
    await event_bus.emit(topic, {"message": "trigger error"})
    
    # Verify the handler was called and raised an error
    assert error_raised, "Error handler was not called"
    
    # Verify handler can still be removed
    event_bus.remove_listener(topic, error_handler)
    
    # Verify no remaining handlers
    event_bus.remove_all_listeners(topic)

@pytest.mark.asyncio
async def test_invalid_handler_registration(event_bus):
    """Test validation of handler registration."""
    # Attempt to register invalid handlers
    with pytest.raises(ValueError, match="Handler must be an async function"):
        def sync_handler(payload):
            pass
        await event_bus.on("test/invalid", sync_handler)
    
    with pytest.raises(ValueError, match="Handler must accept exactly one parameter"):
        async def multi_param_handler(a, b):
            pass
        await event_bus.on("test/invalid", multi_param_handler) 