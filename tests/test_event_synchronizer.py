"""
Unit tests for EventSynchronizer

Tests the event synchronization functionality, focusing on race condition prevention
and proper context tracking for concurrent events.
"""

import pytest
import asyncio
from typing import Dict, Any, List
from pydantic import BaseModel

from src.bus.sync_event_bus import SyncEventBus
from src.bus.event_synchronizer import EventSynchronizer

# Test Models
class TestEventPayload(BaseModel):
    """Test event payload model."""
    message: str
    value: int

# Fixtures
@pytest.fixture
def event_bus():
    """Create a fresh event bus for each test."""
    return SyncEventBus()

@pytest.fixture
def event_sync(event_bus):
    """Create an event synchronizer."""
    return EventSynchronizer(event_bus)

@pytest.fixture
def event_loop():
    """Create a new event loop for each test."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    loop.close()

# Tests
@pytest.mark.asyncio
async def test_race_condition_prevention(event_bus, event_sync):
    """Test that race conditions between subscription and future creation are prevented."""
    # Start waiting for event before it's emitted
    wait_task = asyncio.create_task(
        event_sync.wait_for_event("test/race", timeout=1.0)
    )
    
    # Small delay to ensure wait_for_event has started
    await asyncio.sleep(0.1)
    
    # Emit event
    test_payload = TestEventPayload(message="test", value=42)
    await event_bus.emit("test/race", test_payload)
    
    # Wait for result
    result = await wait_task
    
    # Verify event was caught
    assert result["message"] == "test"
    assert result["value"] == 42

@pytest.mark.asyncio
async def test_concurrent_event_tracking(event_bus, event_sync):
    """Test that multiple concurrent events are tracked properly."""
    # Create multiple wait tasks
    wait_tasks = [
        asyncio.create_task(
            event_sync.wait_for_event(f"test/concurrent/{i}")
        )
        for i in range(3)
    ]
    
    # Small delay for setup
    await asyncio.sleep(0.1)
    
    # Emit events with different payloads
    for i in range(3):
        await event_bus.emit(
            f"test/concurrent/{i}",
            TestEventPayload(message=f"test{i}", value=i)
        )
    
    # Wait for all results
    results = await asyncio.gather(*wait_tasks)
    
    # Verify each event was tracked separately
    for i, result in enumerate(results):
        assert result["message"] == f"test{i}"
        assert result["value"] == i

@pytest.mark.asyncio
async def test_ordered_event_sequence(event_bus, event_sync):
    """Test waiting for events in a specific order."""
    # Define event sequence
    sequence = ["first", "second", "third"]
    
    # Start waiting for ordered sequence
    wait_task = asyncio.create_task(
        event_sync.wait_for_events(
            [f"test/sequence/{evt}" for evt in sequence],
            timeout=1.0,
            in_order=True
        )
    )
    
    # Emit events in order
    for i, evt in enumerate(sequence):
        await event_bus.emit(
            f"test/sequence/{evt}",
            TestEventPayload(message=evt, value=i)
        )
        await asyncio.sleep(0.1)  # Ensure order
    
    # Get results
    results = await wait_task
    
    # Verify events were received in order
    for i, evt in enumerate(sequence):
        result = results[f"test/sequence/{evt}"]
        assert result["message"] == evt
        assert result["value"] == i

@pytest.mark.asyncio
async def test_condition_filtering(event_bus, event_sync):
    """Test event filtering with conditions."""
    # Define condition
    def value_greater_than_5(payload: Dict[str, Any]) -> bool:
        return payload["value"] > 5
    
    # Start waiting for event with condition
    wait_task = asyncio.create_task(
        event_sync.wait_for_event(
            "test/filter",
            condition=value_greater_than_5
        )
    )
    
    # Emit events that don't match condition
    for i in range(5):
        await event_bus.emit(
            "test/filter",
            TestEventPayload(message=f"test{i}", value=i)
        )
    
    # Emit matching event
    await event_bus.emit(
        "test/filter",
        TestEventPayload(message="match", value=10)
    )
    
    # Get result
    result = await wait_task
    
    # Verify only matching event was returned
    assert result["message"] == "match"
    assert result["value"] == 10

@pytest.mark.asyncio
async def test_timeout_handling(event_bus, event_sync):
    """Test proper timeout handling."""
    with pytest.raises(asyncio.TimeoutError):
        await event_sync.wait_for_event("test/timeout", timeout=0.1)

@pytest.mark.asyncio
async def test_cleanup(event_bus, event_sync):
    """Test proper resource cleanup."""
    # Create some test subscriptions
    wait_tasks = [
        asyncio.create_task(
            event_sync.wait_for_event(f"test/cleanup/{i}")
        )
        for i in range(3)
    ]
    
    # Cancel tasks
    for task in wait_tasks:
        task.cancel()
    
    # Clean up
    await event_sync.cleanup()
    
    # Verify all contexts are cleared
    assert len(event_sync._contexts) == 0
    
    # Verify no memory leaks
    for task in wait_tasks:
        assert task.cancelled()

@pytest.mark.asyncio
async def test_grace_period(event_bus, event_sync):
    """Test grace period behavior."""
    start_time = asyncio.get_event_loop().time()
    
    # Start waiting for event
    wait_task = asyncio.create_task(
        event_sync.wait_for_event("test/grace")
    )
    
    # Emit event immediately
    await event_bus.emit(
        "test/grace",
        TestEventPayload(message="test", value=42)
    )
    
    # Wait for result
    await wait_task
    
    # Verify grace period was applied
    elapsed = asyncio.get_event_loop().time() - start_time
    assert elapsed >= event_sync.grace_period_ms / 1000 