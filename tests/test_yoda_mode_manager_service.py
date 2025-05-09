"""
Integration tests for YodaModeManagerService

Tests the mode transition functionality and event sequences of the YodaModeManagerService
using the new SyncEventBus and EventSynchronizer.
"""

import pytest
import asyncio
from typing import Dict, Any, List
from pydantic import BaseModel

from src.bus.sync_event_bus import SyncEventBus
from src.bus.event_synchronizer import EventSynchronizer
from src.services.yoda_mode_manager_service import YodaModeManagerService, SystemMode
from src.models.service_status import ServiceStatus

# Test Models
class ModeTransitionPayload(BaseModel):
    """Mode transition event payload."""
    old_mode: str
    new_mode: str
    status: str
    error: Optional[str] = None

class SystemModeChangePayload(BaseModel):
    """System mode change event payload."""
    old_mode: str
    new_mode: str

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
async def yoda_mode_manager(event_bus):
    """Create a test YodaModeManagerService instance."""
    service = YodaModeManagerService(event_bus)
    yield service
    # Ensure service is stopped
    if service._started:
        await service.stop()

# Tests
@pytest.mark.asyncio
async def test_startup_sequence(yoda_mode_manager, event_sync):
    """Test the service startup sequence."""
    # Start waiting for events before starting service
    status_task = asyncio.create_task(
        event_sync.wait_for_events([
            "service/status",
            "mode/transition/started",
            "mode/transition/complete",
            "system/mode/change"
        ])
    )
    
    # Start the service
    await yoda_mode_manager.start()
    
    # Get events
    events = await status_task
    
    # Verify status transitions
    status_events = events["service/status"]
    assert len(status_events) >= 2  # At least STARTING and RUNNING
    assert status_events[0]["status"] == ServiceStatus.STARTING
    assert status_events[-1]["status"] == ServiceStatus.RUNNING
    
    # Verify mode transition sequence
    assert events["mode/transition/started"]["old_mode"] == SystemMode.STARTUP.name
    assert events["mode/transition/started"]["new_mode"] == SystemMode.IDLE.name
    assert events["mode/transition/complete"]["status"] == "completed"
    
    # Verify final mode change
    assert events["system/mode/change"]["old_mode"] == SystemMode.STARTUP.name
    assert events["system/mode/change"]["new_mode"] == SystemMode.IDLE.name

@pytest.mark.asyncio
async def test_mode_transition_sequence(yoda_mode_manager, event_sync):
    """Test that mode transitions emit events in the correct sequence."""
    await yoda_mode_manager.start()
    
    # Start waiting for transition events
    events_task = asyncio.create_task(
        event_sync.wait_for_events([
            "mode/transition/started",
            "mode/transition/complete",
            "system/mode/change"
        ], in_order=True)
    )
    
    # Trigger mode change
    await yoda_mode_manager.set_mode(SystemMode.INTERACTIVE)
    
    # Get events
    events = await events_task
    
    # Verify event sequence
    assert events["mode/transition/started"]["status"] == "started"
    assert events["system/mode/change"]["new_mode"] == SystemMode.INTERACTIVE.name
    assert events["mode/transition/complete"]["status"] == "completed"

@pytest.mark.asyncio
async def test_invalid_mode_transition(yoda_mode_manager, event_sync):
    """Test handling of invalid mode transitions."""
    await yoda_mode_manager.start()
    
    # Start waiting for error events
    events_task = asyncio.create_task(
        event_sync.wait_for_events([
            "mode/transition/started",
            "mode/transition/complete",
            "service/status"
        ])
    )
    
    # Attempt invalid transition
    with pytest.raises(ValueError):
        await yoda_mode_manager.set_mode("INVALID_MODE")
    
    # Get events
    events = await events_task
    
    # Verify error events
    assert events["mode/transition/complete"]["status"] == "failed"
    assert events["service/status"]["status"] == ServiceStatus.ERROR

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
        "mode/transition/started",
        "mode/transition/complete"
    ])
    
    # All transitions that started should have completed
    started_count = len([e for e in transition_events["mode/transition/started"]])
    completed_count = len([e for e in transition_events["mode/transition/complete"]])
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
async def test_mode_transition_reversion(yoda_mode_manager, event_sync):
    """Test that failed transitions properly revert the mode."""
    await yoda_mode_manager.start()
    original_mode = yoda_mode_manager.current_mode
    
    # Create a service that will fail during transition
    class FailingService(BaseService):
        async def _handle_mode_change(self, payload: Dict[str, Any]):
            raise ValueError("Simulated failure")
    
    failing_service = FailingService("failing_service", event_bus)
    await failing_service.subscribe("system/mode/change", failing_service._handle_mode_change)
    
    # Attempt transition that will fail
    with pytest.raises(ValueError):
        await yoda_mode_manager.set_mode(SystemMode.INTERACTIVE)
    
    # Verify mode reverted
    assert yoda_mode_manager.current_mode == original_mode
    
    # Verify error events
    events = await event_sync.wait_for_events([
        "mode/transition/complete",
        "service/status"
    ])
    
    assert events["mode/transition/complete"]["status"] == "failed"
    assert events["service/status"]["status"] == ServiceStatus.ERROR

@pytest.mark.asyncio
async def test_shutdown_sequence(yoda_mode_manager, event_sync):
    """Test the service shutdown sequence."""
    await yoda_mode_manager.start()
    await yoda_mode_manager.set_mode(SystemMode.INTERACTIVE)
    
    # Start waiting for shutdown events
    events_task = asyncio.create_task(
        event_sync.wait_for_events([
            "service/status",
            "mode/transition/started",
            "mode/transition/complete",
            "system/mode/change"
        ])
    )
    
    # Stop the service
    await yoda_mode_manager.stop()
    
    # Get events
    events = await events_task
    
    # Verify status transitions
    assert events["service/status"]["status"] == ServiceStatus.STOPPED
    
    # Verify mode transition to IDLE
    assert events["system/mode/change"]["new_mode"] == SystemMode.IDLE.name
    assert events["mode/transition/complete"]["status"] == "completed" 