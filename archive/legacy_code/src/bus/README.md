# Event Bus System

This package provides a robust, thread-safe event bus implementation with proper handling of race conditions and event synchronization.

## Components

### SyncEventBus

The core event bus implementation with synchronous registration guarantees:

```python
from src.bus.sync_event_bus import SyncEventBus

# Create event bus
event_bus = SyncEventBus()

# Subscribe to events (guaranteed completion)
await event_bus.sync_on("topic", handler)

# Emit events
await event_bus.emit("topic", payload)
```

Key features:
- Synchronous event registration with completion guarantees
- Proper handler lifecycle management
- Thread-safe operation
- Support for both sync and async handlers
- Automatic payload conversion for Pydantic models

### EventSynchronizer

Testing utility for managing event timing and synchronization:

```python
from src.bus.event_synchronizer import EventSynchronizer

# Create synchronizer
sync = EventSynchronizer(event_bus)

# Wait for single event
result = await sync.wait_for_event("topic")

# Wait for multiple events
results = await sync.wait_for_events(["topic1", "topic2"])

# Wait for ordered sequence
results = await sync.wait_for_events(
    ["first", "second", "third"],
    in_order=True
)
```

Features:
- Eliminates race conditions in tests
- Per-subscription context tracking
- Support for ordered event sequences
- Configurable grace periods
- Clean resource management

### BaseService

Base class for services using the event bus:

```python
from src.services.base_service import BaseService

class MyService(BaseService):
    def __init__(self, event_bus):
        super().__init__("my_service", event_bus)
        
    async def _start(self):
        # Subscribe to events
        await self.subscribe("topic", self._handle_event)
        
    async def _handle_event(self, payload):
        # Handle event
        pass
```

Features:
- Proper service lifecycle management
- Automatic subscription cleanup
- Status tracking and reporting
- Error handling and recovery

## Best Practices

1. **Event Registration**
   - Always use `sync_on()` for registration
   - Wait for registration to complete before emitting events
   - Store subscriptions for proper cleanup

2. **Event Emission**
   - Use Pydantic models for type-safe payloads
   - Handle both sync and async handlers
   - Set appropriate timeouts for handler completion

3. **Testing**
   - Use EventSynchronizer for timing-sensitive tests
   - Set appropriate grace periods
   - Test ordered event sequences when order matters
   - Verify proper cleanup

4. **Error Handling**
   - Implement proper error handling in handlers
   - Use service status updates for error reporting
   - Clean up resources in error cases

## Example Usage

```python
from src.bus.sync_event_bus import SyncEventBus
from src.services.base_service import BaseService
from pydantic import BaseModel

# Define event payload
class MyEventPayload(BaseModel):
    message: str
    value: int

# Create service
class MyService(BaseService):
    async def _start(self):
        await self.subscribe("my/topic", self._handle_event)
        
    async def _handle_event(self, payload: dict):
        # Handle event
        print(f"Received: {payload['message']}")
        
# Set up system
event_bus = SyncEventBus()
service = MyService(event_bus)

# Start service
await service.start()

# Emit event
await event_bus.emit(
    "my/topic",
    MyEventPayload(message="hello", value=42)
)

# Clean up
await service.stop()
```

## Testing Example

```python
import pytest
from src.bus.event_synchronizer import EventSynchronizer

@pytest.mark.asyncio
async def test_my_service():
    # Set up
    event_bus = SyncEventBus()
    sync = EventSynchronizer(event_bus)
    service = MyService(event_bus)
    await service.start()
    
    # Emit event
    payload = MyEventPayload(message="test", value=42)
    await event_bus.emit("my/topic", payload)
    
    # Wait for and verify event
    result = await sync.wait_for_event("my/topic")
    assert result["message"] == "test"
    assert result["value"] == 42
    
    # Clean up
    await service.stop()
``` 