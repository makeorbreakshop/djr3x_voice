"""
Integration tests for YodaModeManagerService

Tests the mode transition functionality and event sequences of the YodaModeManagerService
using the new SyncEventBus and EventSynchronizer.
"""

import pytest
import asyncio
from typing import Dict, Any, List
from unittest.mock import Mock, AsyncMock

from src.bus.sync_event_bus import SyncEventBus
from src.bus.event_synchronizer import EventSynchronizer
from src.services.yoda_mode_manager_service import YodaModeManagerService, SystemMode
from src.models.service_status import ServiceStatus
from src.models.payloads import SystemModeChangePayload, ModeTransitionPayload
from src.event_topics import EventTopics

@pytest.fixture
async def event_bus():
    """Create a SyncEventBus instance."""
    bus = SyncEventBus()
    yield bus
    bus.clear()

@pytest.fixture
async def event_sync(event_bus):
    """Create an EventSynchronizer instance."""
    sync = EventSynchronizer(event_bus)
    return sync

@pytest.fixture
async def yoda_mode_manager(event_bus):
    """Create a YodaModeManagerService instance."""
    service = YodaModeManagerService(event_bus)
    yield service
    await service.stop()

@pytest.mark.asyncio
async def test_startup_sequence(yoda_mode_manager, event_sync):
    """Test the service startup sequence with subscription verification."""
    # Start waiting for events before starting service
    events_task = asyncio.create_task(
        event_sync.wait_for_events([
            EventTopics.SERVICE_STATUS_UPDATE,
            EventTopics.MODE_TRANSITION_STARTED,
            EventTopics.MODE_TRANSITION_COMPLETE,
            EventTopics.SYSTEM_MODE_CHANGE
        ])
    )
    
    # Start the service
    await yoda_mode_manager.start()
    
    # Get events
    events = await events_task
    
    # Verify status transitions
    status_events = events[EventTopics.SERVICE_STATUS_UPDATE]
    assert len(status_events) >= 2  # At least STARTING and RUNNING
    assert status_events[0]["status"] == ServiceStatus.STARTING
    assert status_events[-1]["status"] == ServiceStatus.RUNNING
    
    # Verify mode transition sequence
    assert events[EventTopics.MODE_TRANSITION_STARTED]["old_mode"] == SystemMode.STARTUP.name
    assert events[EventTopics.MODE_TRANSITION_STARTED]["new_mode"] == SystemMode.IDLE.name
    assert events[EventTopics.MODE_TRANSITION_COMPLETE]["status"] == "completed"
    
    # Verify final mode change
    assert events[EventTopics.SYSTEM_MODE_CHANGE]["old_mode"] == SystemMode.STARTUP.name
    assert events[EventTopics.SYSTEM_MODE_CHANGE]["new_mode"] == SystemMode.IDLE.name

@pytest.mark.asyncio
async def test_mode_transition_sequence(yoda_mode_manager, event_sync):
    """Test that mode transitions emit events in the correct sequence."""
    await yoda_mode_manager.start()
    
    # Start waiting for transition events
    events_task = asyncio.create_task(
        event_sync.wait_for_events([
            EventTopics.MODE_TRANSITION_STARTED,
            EventTopics.SYSTEM_MODE_CHANGE,
            EventTopics.MODE_TRANSITION_COMPLETE
        ], in_order=True)
    )
    
    # Trigger mode change
    await yoda_mode_manager.set_mode(SystemMode.INTERACTIVE)
    
    # Get events
    events = await events_task
    
    # Verify event sequence
    assert events[EventTopics.MODE_TRANSITION_STARTED]["status"] == "started"
    assert events[EventTopics.SYSTEM_MODE_CHANGE]["new_mode"] == SystemMode.INTERACTIVE.name
    assert events[EventTopics.MODE_TRANSITION_COMPLETE]["status"] == "completed"

@pytest.mark.asyncio
async def test_failed_mode_transition(yoda_mode_manager, event_sync):
    """Test handling of failed mode transitions with rollback."""
    await yoda_mode_manager.start()
    original_mode = yoda_mode_manager.current_mode
    
    # Create a failing handler
    async def failing_handler(payload: Dict[str, Any]):
        raise ValueError("Simulated failure")
    
    # Subscribe failing handler to mode change
    await event_bus.on(EventTopics.SYSTEM_MODE_CHANGE, failing_handler)
    
    # Start waiting for events
    events_task = asyncio.create_task(
        event_sync.wait_for_events([
            EventTopics.MODE_TRANSITION_STARTED,
            EventTopics.MODE_TRANSITION_COMPLETE,
            EventTopics.SERVICE_STATUS_UPDATE
        ])
    )
    
    # Attempt transition that will fail
    with pytest.raises(RuntimeError):
        await yoda_mode_manager.set_mode(SystemMode.INTERACTIVE)
    
    # Get events
    events = await events_task
    
    # Verify mode reverted
    assert yoda_mode_manager.current_mode == original_mode
    
    # Verify error events
    assert events[EventTopics.MODE_TRANSITION_COMPLETE]["status"] == "failed"
    assert any(
        e["status"] == ServiceStatus.ERROR
        for e in events[EventTopics.SERVICE_STATUS_UPDATE]
    )

@pytest.mark.asyncio
async def test_concurrent_mode_transitions(yoda_mode_manager, event_sync):
    """Test handling of concurrent mode transition requests."""
    await yoda_mode_manager.start()
    
    # Start multiple transitions concurrently
    tasks = [
        yoda_mode_manager.set_mode(mode)
        for mode in [SystemMode.INTERACTIVE, SystemMode.AMBIENT, SystemMode.IDLE]
    ]
    
    # Wait for all transitions
    await asyncio.gather(*tasks, return_exceptions=True)
    
    # Verify we ended up in a valid state
    assert yoda_mode_manager.current_mode in SystemMode
    
    # Verify no lingering transitions
    transition_events = await event_sync.wait_for_events([
        EventTopics.MODE_TRANSITION_STARTED,
        EventTopics.MODE_TRANSITION_COMPLETE
    ])
    
    # All transitions that started should have completed
    started_count = len(transition_events[EventTopics.MODE_TRANSITION_STARTED])
    completed_count = len(transition_events[EventTopics.MODE_TRANSITION_COMPLETE])
    assert started_count == completed_count

@pytest.mark.asyncio
async def test_mode_transition_grace_period(yoda_mode_manager, event_sync):
    """Test that mode transitions respect grace periods."""
    await yoda_mode_manager.start()
    
    # Record start time
    start_time = asyncio.get_event_loop().time()
    
    # Trigger mode change
    await yoda_mode_manager.set_mode(SystemMode.INTERACTIVE)
    
    # Get completion time
    end_time = asyncio.get_event_loop().time()
    
    # Verify grace period was applied
    elapsed = end_time - start_time
    assert elapsed >= (yoda_mode_manager._mode_change_grace_period_ms * 2) / 1000  # Pre and post grace periods

@pytest.mark.asyncio
async def test_subscription_verification(yoda_mode_manager, event_bus):
    """Test subscription verification during startup."""
    # Mock the verify_subscriptions method
    original_verify = event_bus.verify_subscriptions
    verify_called = False
    
    async def mock_verify(*args, **kwargs):
        nonlocal verify_called
        verify_called = True
        await original_verify(*args, **kwargs)
        
    event_bus.verify_subscriptions = mock_verify
    
    # Start service
    await yoda_mode_manager.start()
    
    # Verify subscriptions were verified
    assert verify_called

@pytest.mark.asyncio
async def test_transaction_rollback_on_error(yoda_mode_manager, event_sync):
    """Test that transaction rolls back properly on error."""
    await yoda_mode_manager.start()
    original_mode = yoda_mode_manager.current_mode
    
    # Create a mock compensating action
    compensating_called = False
    
    async def mock_compensate():
        nonlocal compensating_called
        compensating_called = True
    
    # Patch the _revert_mode method to use our mock
    yoda_mode_manager._revert_mode = mock_compensate
    
    # Create a failing handler
    async def failing_handler(payload: Dict[str, Any]):
        raise ValueError("Simulated failure")
    
    # Subscribe failing handler
    await event_bus.on(EventTopics.SYSTEM_MODE_CHANGE, failing_handler)
    
    # Attempt transition that will fail
    with pytest.raises(RuntimeError):
        await yoda_mode_manager.set_mode(SystemMode.INTERACTIVE)
    
    # Verify compensating action was called
    assert compensating_called
    
    # Verify mode reverted
    assert yoda_mode_manager.current_mode == original_mode 