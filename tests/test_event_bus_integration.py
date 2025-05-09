"""
End-to-end integration tests for the event bus system.

Tests the interaction between all components: SyncEventBus, EventSynchronizer,
BaseService, and YodaModeManagerService.
"""

import pytest
import pytest_asyncio
import asyncio
from typing import Dict, Any, List
from pydantic import BaseModel
import logging

from src.bus.sync_event_bus import SyncEventBus
from src.bus.event_synchronizer import EventSynchronizer
from src.services.base_service import BaseService
from src.services.yoda_mode_manager_service import YodaModeManagerService, SystemMode
from src.models.service_status import ServiceStatus
from src.event_topics import EventTopics

# Test Models
class TestEventPayload(BaseModel):
    """Test event payload."""
    message: str
    value: int

class TestService(BaseService):
    """Test service implementation."""
    
    def __init__(self, event_bus: SyncEventBus):
        """Initialize the test service."""
        super().__init__("test_service", event_bus)
        self.received_events: List[Dict[str, Any]] = []
        self.cleanup_called = False
        
    async def _start(self) -> None:
        """Set up test event handlers."""
        await self.subscribe("test/event", self._handle_test_event)
        await self.subscribe("test/command", self._handle_test_command)
        
    async def _handle_test_event(self, payload: Dict[str, Any]) -> None:
        """Handle test events."""
        # Skip test verification payloads
        if payload.get("_test_verification"):
            return
        self.received_events.append(payload)
        
    async def _handle_test_command(self, payload: Dict[str, Any]) -> None:
        """Handle test commands."""
        # Skip test verification payloads
        if payload.get("_test_verification"):
            return
        await self.event_bus.emit("test/event", payload)
        
    async def _stop(self) -> None:
        """Clean up test service."""
        self.cleanup_called = True

class FailingService(BaseService):
    """Service that simulates failures."""
    
    def __init__(self, event_bus: SyncEventBus):
        """Initialize the failing service."""
        super().__init__("failing_service", event_bus)
        
    async def _start(self) -> None:
        """Set up failing handlers."""
        await self.subscribe("test/event", self._handle_test_event)
        
    async def _handle_test_event(self, payload: Dict[str, Any]) -> None:
        """Handle test events with failure."""
        raise RuntimeError("Simulated failure")

class ModeAwareService(BaseService):
    """Service that tracks mode changes."""
    
    def __init__(self, event_bus: SyncEventBus):
        """Initialize the mode-aware service."""
        super().__init__("mode_aware_service", event_bus)
        self.current_mode = SystemMode.STARTUP
        self.mode_change_events_received = 0
        
    async def _start(self) -> None:
        """Set up mode change handler."""
        self.logger.debug(f"ModeAwareService: Subscribing to {EventTopics.SYSTEM_MODE_CHANGE}")
        await self.subscribe(EventTopics.SYSTEM_MODE_CHANGE, self._handle_mode_change)
        self.logger.debug(f"Mode aware service started with initial mode: {self.current_mode.name}")
        
    async def _handle_mode_change(self, payload: Dict[str, Any]) -> None:
        """Handle mode change events."""
        try:
            self.mode_change_events_received += 1
            self.logger.debug(f"ModeAwareService: Received mode change event #{self.mode_change_events_received} with payload: {payload}")
            
            if not isinstance(payload, dict):
                self.logger.error(f"ModeAwareService: Invalid payload type: {type(payload)}")
                return
                
            self.logger.debug(f"ModeAwareService: Payload keys: {list(payload.keys())}")
            
            new_mode = payload.get("new_mode")
            if not new_mode:
                self.logger.error("ModeAwareService: No new_mode in payload")
                return
                
            # Update the mode and log
            old_mode = self.current_mode
            self.logger.debug(f"ModeAwareService: Updating mode from {old_mode.name} to {new_mode}")
            self.current_mode = SystemMode(new_mode)
            self.logger.info(f"ModeAwareService: Mode changed from {old_mode.name} to {self.current_mode.name}")
        except Exception as e:
            self.logger.error(f"ModeAwareService: Error handling mode change: {e}")
            self.logger.exception("Stack trace:")
            raise

@pytest_asyncio.fixture
async def event_bus():
    """Create a test event bus."""
    bus = SyncEventBus(propagate_errors=True)
    yield bus
    bus.remove_all_listeners()

@pytest_asyncio.fixture
async def event_synchronizer(event_bus):
    """Create a test event synchronizer."""
    return EventSynchronizer(event_bus)

@pytest_asyncio.fixture
async def test_service(event_bus):
    """Create a test service."""
    service = TestService(event_bus)
    await service.start()
    yield service
    await service.stop()

@pytest_asyncio.fixture
async def failing_service(event_bus):
    """Create a test failing service."""
    service = FailingService(event_bus)
    await service.start()
    yield service
    await service.stop()

@pytest_asyncio.fixture
async def yoda_mode_manager(event_bus):
    """Create a test mode manager."""
    manager = YodaModeManagerService(event_bus)
    yield manager
    await manager.stop()

@pytest_asyncio.fixture
async def mode_aware_service(event_bus):
    """Create a test mode-aware service."""
    service = ModeAwareService(event_bus)
    yield service
    await service.stop()

async def test_complete_system_startup(
    event_bus,
    event_synchronizer,
    yoda_mode_manager,
    mode_aware_service,
    test_service
):
    """Test complete system startup sequence."""
    import logging
    logging.getLogger().setLevel(logging.DEBUG)  # Set root logger to DEBUG level
    
    print("Starting test_complete_system_startup")
    
    # Make sure we track all possible status topics
    status_topics = ["service/status", EventTopics.SERVICE_STATUS_UPDATE]
    
    # Set up all subscriptions first
    for topic in status_topics:
        # Make sure we're subscribed to all status topics
        print(f"Setting up subscription for {topic}")
        async with event_synchronizer._subscription_context(topic):
            pass
    
    # Set up event listener for mode transition events
    print("Setting up event listeners for mode transition events")
    transition_topics = [
        EventTopics.MODE_TRANSITION_STARTED,
        EventTopics.MODE_TRANSITION_COMPLETE,
        EventTopics.SYSTEM_MODE_CHANGE
    ]
    
    # Subscribe to transition topics first
    for topic in transition_topics:
        print(f"Setting up subscription for {topic}")
        async with event_synchronizer._subscription_context(topic):
            pass
    
    # Wait a bit to ensure subscriptions are registered
    print("Waiting for subscriptions to be registered")
    await asyncio.sleep(0.5)  # Increased from 0.1 to 0.5
    
    # Start services in the order that properly captures events
    # Start the mode manager first
    print("Starting yoda_mode_manager")
    await yoda_mode_manager.start()
    
    # Wait for initial mode transitions to complete
    print("Waiting for initial mode transitions")
    await asyncio.sleep(1.0)
    
    # Start other services
    print("Starting test_service")
    await test_service.start()
    print("Starting mode_aware_service")
    await mode_aware_service.start()
    
    # Create startup task
    startup_task = event_synchronizer.wait_for_events(
        transition_topics, 
        in_order=True, 
        timeout=15.0  # Increased from 5.0 to 15.0
    )
    
    # Set the mode again after all services are started
    print("Setting mode to INTERACTIVE")
    await yoda_mode_manager.set_mode(SystemMode.INTERACTIVE)
    
    # Wait for all events to complete
    print("Awaiting startup_task")
    events = await startup_task
    print(f"Received events: {events}")
    
    # Check each status topic and combine results
    all_status_updates = []
    for topic in status_topics:
        updates = event_synchronizer.get_events(topic)
        if updates:
            all_status_updates.extend(updates)
            print(f"Found {len(updates)} status updates on topic: {topic}")
            
    # Debug log the status events we received
    for update in all_status_updates:
        print(f"Status update: {update}")
    
    # Since we're starting multiple services, we should have multiple status updates
    assert len(all_status_updates) >= 2, f"Expected at least 2 status updates, got {len(all_status_updates)}: {all_status_updates}"
    
    # Verify mode transitions
    assert len(events[EventTopics.MODE_TRANSITION_STARTED]) > 0
    assert len(events[EventTopics.MODE_TRANSITION_COMPLETE]) > 0
    assert len(events[EventTopics.SYSTEM_MODE_CHANGE]) > 0
    
    # Verify service states
    assert yoda_mode_manager._status == ServiceStatus.RUNNING
    assert mode_aware_service._status == ServiceStatus.RUNNING
    assert test_service._status == ServiceStatus.RUNNING
    
    # Verify the mode was propagated correctly
    assert mode_aware_service.current_mode == SystemMode.INTERACTIVE

async def test_mode_transition_propagation(
    event_bus,
    event_synchronizer,
    yoda_mode_manager,
    mode_aware_service
):
    """Test mode transition event propagation."""
    import logging
    logging.getLogger().setLevel(logging.DEBUG)
    
    print("Starting test_mode_transition_propagation")
    
    # Set up subscriptions first to ensure they capture events
    transition_topics = [
        EventTopics.MODE_TRANSITION_STARTED,
        EventTopics.MODE_TRANSITION_COMPLETE,
        EventTopics.SYSTEM_MODE_CHANGE
    ]
    
    for topic in transition_topics:
        print(f"Setting up subscription for {topic}")
        async with event_synchronizer._subscription_context(topic):
            pass
    
    # Wait to ensure subscriptions are registered
    print("Waiting for subscriptions to be registered")
    await asyncio.sleep(0.5)
    
    # Start yoda_mode_manager first
    print("Starting yoda_mode_manager")
    await yoda_mode_manager.start()
    
    # Wait for initial mode transitions to complete
    print("Waiting for initial mode transitions")
    await asyncio.sleep(1.0)
    
    # Start mode_aware_service
    print("Starting mode_aware_service")
    await mode_aware_service.start()
    
    # Set up event listener for mode transition events
    print("Setting up event listener for mode transitions")
    transition_task = event_synchronizer.wait_for_events(
        transition_topics, 
        timeout=15.0
    )
    
    # Request mode change
    print("Setting mode to INTERACTIVE")
    await yoda_mode_manager.set_mode(SystemMode.INTERACTIVE)
    
    # Wait for events to complete
    print("Waiting for events to complete")
    events = await transition_task
    
    # Validate all events were received
    print(f"Received events: {events}")
    assert len(events[EventTopics.MODE_TRANSITION_STARTED]) > 0
    assert len(events[EventTopics.MODE_TRANSITION_COMPLETE]) > 0
    assert len(events[EventTopics.SYSTEM_MODE_CHANGE]) > 0
    
    # Add grace period to ensure the mode change has been processed by all services
    print("Waiting for state propagation")
    await asyncio.sleep(0.5)  # 500ms should be enough for state propagation
    
    # Verify mode change was propagated to the service
    print(f"Mode aware service current mode: {mode_aware_service.current_mode.name}")
    assert mode_aware_service.current_mode == SystemMode.INTERACTIVE

async def test_error_propagation(
    event_bus,
    event_synchronizer
):
    """Test error propagation."""
    # Create a failing handler that only fails for non-test events
    async def failing_handler(payload: Dict[str, Any]) -> None:
        if not payload.get("_test_verification"):
            raise RuntimeError("Simulated failure")
            
    # Register handler directly with event bus
    await event_bus.on("test/error", failing_handler)
    
    # Emit event and expect error
    with pytest.raises(RuntimeError, match="Simulated failure"):
        await event_bus.emit("test/error", {"message": "test"})

async def test_concurrent_operations(
    event_bus,
    event_synchronizer,
    test_service
):
    """Test concurrent event operations."""
    # Send multiple events concurrently
    await asyncio.gather(
        event_bus.emit("test/event", TestEventPayload(message="test1", value=1).model_dump()),
        event_bus.emit("test/event", TestEventPayload(message="test2", value=2).model_dump())
    )
    
    assert len(test_service.received_events) == 2  # Both events received

async def test_system_shutdown(
    event_bus,
    event_synchronizer,
    yoda_mode_manager,
    test_service
):
    """Test system shutdown sequence."""
    import logging
    logging.getLogger().setLevel(logging.DEBUG)
    
    print("Starting test_system_shutdown")
    
    # Set up subscriptions first to ensure they capture events
    shutdown_topics = [
        "service/status",
        EventTopics.MODE_TRANSITION_STARTED,
        EventTopics.MODE_TRANSITION_COMPLETE,
        EventTopics.SYSTEM_MODE_CHANGE
    ]
    
    # Subscribe to shutdown topics first
    for topic in shutdown_topics:
        print(f"Setting up subscription for {topic}")
        async with event_synchronizer._subscription_context(topic):
            pass
    
    # Wait to ensure subscriptions are registered
    print("Waiting for subscriptions to be registered")
    await asyncio.sleep(0.5)
    
    # Start the mode manager
    print("Starting yoda_mode_manager")
    await yoda_mode_manager.start()
    
    # Wait for the manager to initialize
    print("Waiting for mode manager to initialize")
    await asyncio.sleep(1.0)
    
    # Now set up the shutdown task
    print("Setting up shutdown task")
    shutdown_task = event_synchronizer.wait_for_events(
        ["service/status"],  # Just watch for service status during shutdown
        timeout=10.0
    )
    
    # Stop the manager to trigger shutdown events
    print("Stopping yoda_mode_manager")
    await yoda_mode_manager.stop()
    
    # Wait for shutdown events to be captured
    print("Waiting for shutdown events")
    events = await shutdown_task
    
    # Verify events were received
    print(f"Received events: {events}")
    assert len(events["service/status"]) > 0
    
    # Verify status is STOPPED
    for event in events["service/status"]:
        if event.get("status") == "STOPPED":
            print("Found STOPPED status event")
            return
    
    assert False, "No STOPPED status event found"

async def test_service_lifecycle(event_bus, test_service):
    """Test service lifecycle events."""
    # Send test event
    await event_bus.emit("test/event", TestEventPayload(message="test", value=42).model_dump())
    assert len(test_service.received_events) == 1
    
    # Stop service
    await test_service.stop()
    assert test_service.cleanup_called

async def test_handler_cleanup_on_error(event_bus, test_service):
    """Test handler cleanup on error."""
    # Try to subscribe invalid handler
    def invalid_handler(payload: Dict[str, Any]):
        """Non-async handler."""
        pass
        
    with pytest.raises(ValueError, match="Handler must be an async function"):
        await test_service.subscribe("test/topic", invalid_handler)

async def test_concurrent_events(event_bus, test_service):
    """Test concurrent event handling."""
    # Send events concurrently
    await asyncio.gather(
        event_bus.emit("test/command", {"message": "test1", "value": 1}),
        event_bus.emit("test/command", {"message": "test2", "value": 2})
    )
    
    # Wait for events to be processed
    await asyncio.sleep(0.1)
    assert len(test_service.received_events) == 2 

@pytest.mark.asyncio
async def test_alternative_startup_sequence(
    event_bus,
    event_synchronizer,
    yoda_mode_manager,
    mode_aware_service,
    test_service
):
    """Test alternative startup sequence (starting YodaModeManager first)."""
    import logging
    logging.getLogger().setLevel(logging.DEBUG)
    
    print("Starting test_alternative_startup_sequence")
    
    # Make sure we track all possible status topics
    status_topics = ["service/status", EventTopics.SERVICE_STATUS_UPDATE]
    transition_topics = [
        EventTopics.MODE_TRANSITION_STARTED,
        EventTopics.MODE_TRANSITION_COMPLETE,
        EventTopics.SYSTEM_MODE_CHANGE
    ]
    
    # Set up subscriptions for all topics
    for topic in status_topics + transition_topics:
        print(f"Setting up subscription for {topic}")
        async with event_synchronizer._subscription_context(topic):
            pass
    
    # Wait to ensure subscriptions are registered
    print("Waiting for subscriptions to be registered")
    await asyncio.sleep(0.5)
    
    # Start mode manager first
    print("Starting yoda_mode_manager")
    await yoda_mode_manager.start()
    
    # Wait for initial mode transitions to complete
    print("Waiting for initial mode transitions")
    await asyncio.sleep(1.0)
    
    # Start other services
    print("Starting test_service")
    await test_service.start()
    print("Starting mode_aware_service")
    await mode_aware_service.start()
    
    # Check events received by EventSynchronizer
    for topic in transition_topics:
        events = event_synchronizer.get_events(topic)
        print(f"Events for {topic}: {len(events)}")
        assert len(events) > 0, f"No events received for {topic}"
    
    # Verify service states
    assert yoda_mode_manager._status == ServiceStatus.RUNNING
    assert mode_aware_service._status == ServiceStatus.RUNNING
    assert test_service._status == ServiceStatus.RUNNING
    
    # Now explicitly set the mode again to make sure ModeAwareService receives it
    print("Setting mode to INTERACTIVE")
    await yoda_mode_manager.set_mode(SystemMode.INTERACTIVE)
    
    # Wait for the mode change to propagate
    print("Waiting for mode change to propagate")
    await asyncio.sleep(1.0)
    
    # Now check the mode in ModeAwareService
    print(f"Current mode in ModeAwareService: {mode_aware_service.current_mode.name}")
    
    # Verify mode is propagated
    assert mode_aware_service.current_mode == SystemMode.INTERACTIVE 