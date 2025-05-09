"""
Unit tests for BaseService

Tests the service lifecycle management and subscription handling functionality
of the base service class.
"""

import pytest
import pytest_asyncio
import asyncio
from typing import Dict, Any, List
from pydantic import BaseModel

from src.bus.sync_event_bus import SyncEventBus
from src.services.base_service import BaseService
from src.models.service_status import ServiceStatus, ServiceStatusPayload

# Test Models
class TestEventPayload(BaseModel):
    """Test event payload model."""
    message: str
    value: int

class TestService(BaseService):
    """Test service implementation."""
    
    def __init__(self, event_bus: SyncEventBus):
        super().__init__("test_service", event_bus)
        self.start_called = False
        self.stop_called = False
        self.received_events: List[Dict[str, Any]] = []
        
    async def _start(self) -> None:
        """Test start implementation."""
        self.start_called = True
        await self.subscribe("test/event", self._handle_event)
        
    async def _stop(self) -> None:
        """Test stop implementation."""
        self.stop_called = True
        
    async def _handle_event(self, payload: Dict[str, Any]) -> None:
        """Store received events."""
        self.received_events.append(payload)

class ErrorService(BaseService):
    """Service that raises errors for testing."""
    
    def __init__(self, event_bus: SyncEventBus, error_in_start: bool = True):
        super().__init__("error_service", event_bus)
        self.error_in_start = error_in_start
        
    async def _start(self) -> None:
        if self.error_in_start:
            raise ValueError("Test start error")
            
    async def _stop(self) -> None:
        if not self.error_in_start:
            raise ValueError("Test stop error")

# Fixtures
@pytest_asyncio.fixture
async def event_bus():
    """Create a fresh event bus for each test."""
    return SyncEventBus(loop=asyncio.get_running_loop())

# Tests
@pytest.mark.asyncio
async def test_service_lifecycle(event_bus):
    """Test normal service lifecycle."""
    service = TestService(event_bus)
    
    # Test initial state
    assert service._status == ServiceStatus.INITIALIZING
    assert not service._started
    
    # Start service
    await service.start()
    
    # Verify start
    assert service.start_called
    assert service._status == ServiceStatus.RUNNING
    assert service._started
    
    # Stop service
    await service.stop()
    
    # Verify stop
    assert service.stop_called
    assert service._status == ServiceStatus.STOPPED
    assert not service._started

@pytest.mark.asyncio
async def test_subscription_management(event_bus):
    """Test subscription handling during service lifecycle."""
    service = TestService(event_bus)
    
    # Start service (creates subscription)
    await service.start()
    
    # Verify subscription
    assert "test/event" in service._subscriptions
    
    # Test event handling
    test_payload = TestEventPayload(message="test", value=42)
    await event_bus.emit("test/event", test_payload)
    
    # Verify event received
    assert len(service.received_events) == 1
    assert service.received_events[0]["message"] == "test"
    
    # Stop service
    await service.stop()
    
    # Verify subscription removed
    assert not service._subscriptions
    
    # Emit event again
    await event_bus.emit("test/event", test_payload)
    
    # Verify no new events received
    assert len(service.received_events) == 1

@pytest.mark.asyncio
async def test_error_handling(event_bus):
    """Test error handling during service lifecycle."""
    # Test start error
    start_error_service = ErrorService(event_bus, error_in_start=True)
    with pytest.raises(ValueError, match="Test start error"):
        await start_error_service.start()
    assert start_error_service._status == ServiceStatus.ERROR
    
    # Test stop error
    stop_error_service = ErrorService(event_bus, error_in_start=False)
    await stop_error_service.start()
    with pytest.raises(ValueError, match="Test stop error"):
        await stop_error_service.stop()
    assert stop_error_service._status == ServiceStatus.ERROR

@pytest.mark.asyncio
async def test_status_events(event_bus):
    """Test service status event emission."""
    received_status_events = []
    
    async def status_handler(payload: Dict[str, Any]):
        received_status_events.append(ServiceStatusPayload(**payload))
    
    # Subscribe to status events
    await event_bus.sync_on("service/status", status_handler)
    
    # Create and start service
    service = TestService(event_bus)
    await service.start()
    
    # Stop service
    await service.stop()
    
    # Verify status event sequence
    assert len(received_status_events) == 4  # STARTING, RUNNING, STOPPING, STOPPED
    assert received_status_events[0].status == ServiceStatus.STARTING
    assert received_status_events[1].status == ServiceStatus.RUNNING
    assert received_status_events[2].status == ServiceStatus.STOPPING
    assert received_status_events[3].status == ServiceStatus.STOPPED

@pytest.mark.asyncio
async def test_multiple_start_stop(event_bus):
    """Test multiple start/stop calls handle correctly."""
    service = TestService(event_bus)
    
    # First start
    await service.start()
    assert service._started
    assert service.start_called
    
    # Second start should be ignored
    service.start_called = False  # Reset flag
    await service.start()
    assert not service.start_called  # Should not have been called again
    
    # First stop
    await service.stop()
    assert not service._started
    assert service.stop_called
    
    # Second stop should be ignored
    service.stop_called = False  # Reset flag
    await service.stop()
    assert not service.stop_called  # Should not have been called again

@pytest.mark.asyncio
async def test_subscription_verification(event_bus):
    """Test subscription verification during startup."""
    class SlowSubscriptionService(BaseService):
        async def _start(self):
            # Add a slow subscription
            await asyncio.sleep(0.2)  # Simulate slow subscription
            await self.subscribe("test/slow", lambda x: None)
    
    service = SlowSubscriptionService("slow_service", event_bus)
    
    # Start service - should wait for subscription
    start_time = asyncio.get_event_loop().time()
    await service.start()
    elapsed = asyncio.get_event_loop().time() - start_time
    
    # Verify proper delay for subscription
    assert elapsed >= 0.2
    assert "test/slow" in service._subscriptions

@pytest.mark.asyncio
async def test_cleanup_on_error(event_bus):
    """Test proper cleanup when error occurs during startup."""
    class CleanupTestService(BaseService):
        async def _start(self):
            await self.subscribe("test/cleanup", lambda x: None)
            raise ValueError("Test error")
    
    service = CleanupTestService("cleanup_service", event_bus)
    
    # Start service - should fail
    with pytest.raises(ValueError):
        await service.start()
    
    # Verify cleanup occurred
    assert not service._subscriptions
    assert service._status == ServiceStatus.ERROR 