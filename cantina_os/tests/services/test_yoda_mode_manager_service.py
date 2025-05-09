"""
Tests for YodaModeManagerService

This module contains tests for the YodaModeManagerService, which manages system operation modes
and mode transitions in CantinaOS.
"""

import pytest
import asyncio
from unittest.mock import Mock, AsyncMock
from pyee.asyncio import AsyncIOEventEmitter

from cantina_os.services.yoda_mode_manager_service import YodaModeManagerService, SystemMode
from cantina_os.event_topics import EventTopics
from cantina_os.event_payloads import (
    SystemModeChangePayload,
    ServiceStatusPayload,
    ServiceStatus,
    LogLevel
)

@pytest.fixture
async def event_bus() -> AsyncIOEventEmitter:
    """Create a test event bus.
    
    Returns:
        An AsyncIOEventEmitter instance
    """
    bus = AsyncIOEventEmitter(loop=asyncio.get_running_loop())
    yield bus
    # Clean up any remaining listeners
    bus.remove_all_listeners()

@pytest.fixture
def config():
    """Create a test configuration."""
    return {
        'MODE_CHANGE_GRACE_PERIOD_MS': 100
    }

@pytest.fixture
async def yoda_mode_manager(event_bus, config) -> YodaModeManagerService:
    """Create a test YodaModeManagerService instance.
    
    Args:
        event_bus: The test event bus
        config: The test configuration
        
    Returns:
        A YodaModeManagerService instance
    """
    manager = YodaModeManagerService(event_bus, config)
    yield manager
    # Ensure service is stopped
    if manager._current_mode != SystemMode.IDLE:
        await manager.stop()

# Mark all tests to use function-scoped event loop
pytestmark = pytest.mark.asyncio(loop_scope="function")

async def test_initialization(yoda_mode_manager):
    """Test service initialization."""
    assert yoda_mode_manager._current_mode == SystemMode.STARTUP
    assert yoda_mode_manager._mode_change_grace_period_ms == 100

async def test_start_service(yoda_mode_manager, event_bus):
    """Test service startup."""
    status_events = []
    event_bus.on(EventTopics.SERVICE_STATUS_UPDATE, status_events.append)
    
    await yoda_mode_manager.start()
    
    # Should transition to IDLE after startup
    assert yoda_mode_manager._current_mode == SystemMode.IDLE
    
    # Should emit status events
    assert len(status_events) > 0
    assert any(e.get('status') == ServiceStatus.RUNNING for e in status_events)

async def test_stop_service(yoda_mode_manager):
    """Test service shutdown."""
    await yoda_mode_manager.start()
    await yoda_mode_manager.set_mode(SystemMode.INTERACTIVE)
    
    await yoda_mode_manager.stop()
    
    # Should transition back to IDLE on shutdown
    assert yoda_mode_manager._current_mode == SystemMode.IDLE

async def test_valid_mode_transition(yoda_mode_manager, event_bus):
    """Test valid mode transition."""
    mode_change_events = []
    event_bus.on(EventTopics.SYSTEM_MODE_CHANGE, mode_change_events.append)
    
    await yoda_mode_manager.start()
    await yoda_mode_manager.set_mode(SystemMode.INTERACTIVE)
    
    assert yoda_mode_manager._current_mode == SystemMode.INTERACTIVE
    assert len(mode_change_events) == 2  # STARTUP->IDLE, IDLE->INTERACTIVE
    
    last_event = mode_change_events[-1]
    assert last_event['old_mode'] == SystemMode.IDLE.name
    assert last_event['new_mode'] == SystemMode.INTERACTIVE.name

async def test_invalid_mode_transition(yoda_mode_manager, event_bus):
    """Test invalid mode transition."""
    status_events = []
    event_bus.on(EventTopics.SERVICE_STATUS_UPDATE, status_events.append)
    
    await yoda_mode_manager.start()
    await yoda_mode_manager.set_mode("INVALID_MODE")
    
    # Should stay in current mode
    assert yoda_mode_manager._current_mode == SystemMode.IDLE
    
    # Should emit error status
    assert any(
        e.get('status') == ServiceStatus.ERROR and
        e.get('severity') == LogLevel.ERROR
        for e in status_events
    )

async def test_same_mode_transition(yoda_mode_manager, event_bus):
    """Test transition to the same mode."""
    mode_change_events = []
    event_bus.on(EventTopics.SYSTEM_MODE_CHANGE, mode_change_events.append)
    
    await yoda_mode_manager.start()
    initial_events = len(mode_change_events)
    
    await yoda_mode_manager.set_mode(SystemMode.IDLE)
    
    # Should not emit new event for same mode
    assert len(mode_change_events) == initial_events

async def test_mode_change_request_handling(yoda_mode_manager, event_bus):
    """Test handling of mode change requests."""
    await yoda_mode_manager.start()
    
    # Emit a mode change request (don't await since emit returns bool)
    event_bus.emit(
        EventTopics.SYSTEM_SET_MODE_REQUEST,
        {"mode": "INTERACTIVE"}
    )
    
    # Wait for grace period
    await asyncio.sleep(0.2)
    
    assert yoda_mode_manager._current_mode == SystemMode.INTERACTIVE

async def test_error_handling_in_mode_request(yoda_mode_manager, event_bus):
    """Test error handling in mode change request handler."""
    status_events = []
    event_bus.on(EventTopics.SERVICE_STATUS_UPDATE, status_events.append)
    
    await yoda_mode_manager.start()
    
    # Send invalid payload (don't await since emit returns bool)
    event_bus.emit(
        EventTopics.SYSTEM_SET_MODE_REQUEST,
        {"invalid": "payload"}
    )
    
    # Wait for grace period
    await asyncio.sleep(0.2)
    
    # Should stay in current mode
    assert yoda_mode_manager._current_mode == SystemMode.IDLE
    
    # Should emit error status
    assert any(
        e.get('status') == ServiceStatus.ERROR and
        e.get('severity') == LogLevel.ERROR
        for e in status_events
    )

async def test_mode_transition_sequence(yoda_mode_manager):
    """Test a sequence of mode transitions."""
    await yoda_mode_manager.start()
    
    # Test all valid mode transitions
    transitions = [
        SystemMode.INTERACTIVE,
        SystemMode.AMBIENT,
        SystemMode.IDLE,
        SystemMode.INTERACTIVE
    ]
    
    for mode in transitions:
        await yoda_mode_manager.set_mode(mode)
        assert yoda_mode_manager._current_mode == mode

async def test_config_override(event_bus):
    """Test configuration override."""
    custom_config = {
        'MODE_CHANGE_GRACE_PERIOD_MS': 200
    }
    
    manager = YodaModeManagerService(event_bus, custom_config)
    assert manager._mode_change_grace_period_ms == 200

async def test_multiple_rapid_transitions(yoda_mode_manager):
    """Test handling of multiple rapid mode transitions."""
    await yoda_mode_manager.start()
    
    # Attempt rapid transitions
    await asyncio.gather(
        yoda_mode_manager.set_mode(SystemMode.INTERACTIVE),
        yoda_mode_manager.set_mode(SystemMode.AMBIENT),
        yoda_mode_manager.set_mode(SystemMode.IDLE)
    )
    
    # Final mode should be valid
    assert yoda_mode_manager._current_mode in [
        SystemMode.INTERACTIVE,
        SystemMode.AMBIENT,
        SystemMode.IDLE
    ] 