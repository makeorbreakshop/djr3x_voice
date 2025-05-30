# Service Creation Guidelines

> **Use this document when creating a new service in CantinaOS**

## Overview

This document provides a step-by-step guide for creating new services that:
- Start without errors
- Communicate properly with the event bus
- Handle cleanup gracefully
- Follow all architectural standards

## Service Creation Checklist

### 1. File Structure Setup

- [ ] Create service directory: `cantina_os/cantina_os/services/<service_name>/`
- [ ] Create `__init__.py` in service directory
- [ ] Copy `service_template.py` to `<service_name>.py` (DO NOT import from template)
- [ ] Create `tests/` subdirectory with `test_<service_name>.py`

### 2. Class Implementation

- [ ] Rename class from `ServiceTemplate` to `<ServiceName>` 
- [ ] Ensure class inherits from `StandardService` or `BaseService`
- [ ] Set unique service name in constructor: `name="<service_name>"`

### 3. Constructor Requirements

```python
# REQUIRED: This exact signature
def __init__(self, event_bus, config=None, name="<service_name>"):
    super().__init__(event_bus, config, name=name)
    # Your initialization here
```

- [ ] First parameter MUST be `event_bus` (positional)
- [ ] Second parameter MUST be `config` (positional)
- [ ] DO NOT use `*,` keyword-only syntax
- [ ] Pass both parameters to `super().__init__()`

### 4. Configuration

- [ ] Create Pydantic `_Config` model inside your service class
- [ ] Define all configuration fields with defaults
- [ ] Validate config in `__init__` if needed

### 5. Event Subscriptions

- [ ] Implement `_setup_subscriptions()` method
- [ ] Use `await asyncio.gather()` for all initial subscriptions
- [ ] NEVER use `asyncio.create_task()` for initial subscriptions

```python
async def _setup_subscriptions(self):
    # CORRECT: Wait for all subscriptions
    await asyncio.gather(
        self.subscribe(EventTopics.TOPIC1, self._handle_topic1),
        self.subscribe(EventTopics.TOPIC2, self._handle_topic2)
    )
```

### 6. Event Emission

- [ ] Use `self._emit_dict()` for all events (converts Pydantic to dict)
- [ ] NEVER await `emit()` - it's synchronous
- [ ] Define event topics in `EventTopics` enum

### 7. Lifecycle Methods

- [ ] Implement `_start()` method (NOT `start()`)
- [ ] Implement `_stop()` method (NOT `stop()`)
- [ ] Call `_setup_subscriptions()` from `_start()`
- [ ] Handle all resource cleanup in `_stop()`

### 8. Task Management

- [ ] Store all background tasks in `self._tasks`
- [ ] Use `asyncio.create_task()` for background tasks
- [ ] Cancel all tasks in `_stop()` method
- [ ] Wait for task completion with `asyncio.gather()`

### 9. Thread Safety (if using hardware)

- [ ] Store event loop: `self._event_loop = asyncio.get_running_loop()`
- [ ] Use `run_threadsafe()` for thread-to-asyncio communication
- [ ] Handle thread cleanup in `_stop()`

### 10. Error Handling

- [ ] Wrap all operations in try/except blocks
- [ ] Use `self._emit_status()` for error reporting
- [ ] Log errors with appropriate severity
- [ ] Emit error responses for user-facing operations

### 11. Service Registration

- [ ] Import service in `main.py`
- [ ] Add to `service_class_map` dictionary
- [ ] Ensure key matches service name
- [ ] Add to service initialization order if needed

### 12. Command Registration (if applicable)

- [ ] Register commands with CommandDispatcher in `main.py`
- [ ] Use full command string for multi-word commands
- [ ] Specify correct EventTopic
- [ ] Test command from CLI

### 13. Testing

- [ ] Write test for service initialization
- [ ] Test event subscription setup
- [ ] Test event emission
- [ ] Test error handling
- [ ] Test cleanup/shutdown

## Common Pitfalls to Avoid

### Race Conditions
**Problem**: Events emitted before subscriptions are ready
```python
# WRONG
async def _start(self):
    asyncio.create_task(self.subscribe(EventTopics.RESPONSE, self._handler))
    await self.emit(EventTopics.REQUEST, {})  # Handler might not be ready!

# CORRECT
async def _start(self):
    await self.subscribe(EventTopics.RESPONSE, self._handler)
    await self.emit(EventTopics.REQUEST, {})
```

### Import Errors
**Problem**: Relative imports or missing module paths
```python
# WRONG
from .event_topics import EventTopics
from event_topics import EventTopics

# CORRECT
from core.event_topics import EventTopics
```

### Event Bus Methods
**Problem**: Trying to await synchronous methods
```python
# WRONG
await self._event_bus.emit(topic, payload)  # TypeError!

# CORRECT
self._event_bus.emit(topic, payload)  # No await
```

### Multi-Word Commands
**Problem**: Commands not parsing correctly
```python
# WRONG
dispatcher.register_command("dj", "brain_service", EventTopics.DJ_COMMAND)

# CORRECT
dispatcher.register_command("dj start", "brain_service", EventTopics.DJ_COMMAND)
dispatcher.register_command("dj stop", "brain_service", EventTopics.DJ_COMMAND)
```

## Example Service Structure

```python
from typing import Dict, Any
from pydantic import BaseModel
import asyncio

from core.base_service import StandardService
from core.event_topics import EventTopics
from core.event_payloads import BaseEventPayload


class MyService(StandardService):
    """Example service following all guidelines."""
    
    class _Config(BaseModel):
        """Service configuration."""
        timeout: int = 30
        retry_count: int = 3
    
    def __init__(self, event_bus, config=None, name="my_service"):
        super().__init__(event_bus, config, name=name)
        self._config = self._Config(**(config or {}))
        self._tasks = []
        
    async def _start(self):
        """Start the service."""
        await self._setup_subscriptions()
        
        # Start background tasks
        task = asyncio.create_task(self._background_worker())
        self._tasks.append(task)
        
    async def _stop(self):
        """Stop the service."""
        # Cancel tasks
        for task in self._tasks:
            if not task.done():
                task.cancel()
                
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
            
        self._tasks.clear()
        
    async def _setup_subscriptions(self):
        """Set up event subscriptions."""
        await asyncio.gather(
            self.subscribe(EventTopics.MY_EVENT, self._handle_my_event)
        )
        
    async def _handle_my_event(self, event_name: str, payload: Dict[str, Any]):
        """Handle incoming event."""
        try:
            # Process event
            result = await self._process_event(payload)
            
            # Emit response
            self._emit_dict(EventTopics.MY_RESPONSE, {
                "result": result,
                "success": True
            })
        except Exception as e:
            self._logger.error(f"Error handling event: {e}")
            await self._emit_status(ServiceStatus.ERROR, str(e))
```

### 14. Event Payload Standards

- [ ] Define all event payloads as Pydantic models in `event_payloads.py`
- [ ] ALWAYS use `.model_dump()` before emitting Pydantic models
- [ ] Include `timestamp` field in payload models
- [ ] Handle both dict and Pydantic payloads in receivers

```python
# WRONG - Causes "object has no attribute 'get'" errors
self.emit(EventTopics.MY_EVENT, my_pydantic_model)

# CORRECT
self.emit(EventTopics.MY_EVENT, my_pydantic_model.model_dump())
```

### 15. Service Dependencies

- [ ] Identify all services your service depends on
- [ ] Document dependencies in class docstring
- [ ] Add your service AFTER its dependencies in `service_order`
- [ ] Handle missing dependencies gracefully

```python
# In main.py service_order
service_order = [
    "memory_service",      # Base services first
    "command_dispatcher",  # Core services
    "brain_service",       # Depends on memory_service
    "your_service",        # Add after dependencies
]
```

### 16. Path Resolution for File Access

- [ ] Use multi-level path resolution for files/directories
- [ ] Never use hardcoded absolute paths
- [ ] Check multiple locations (cwd, app root, system root)
- [ ] Log clear errors when paths not found

```python
def _resolve_path(self, base_path: str) -> Optional[str]:
    """Try multiple path resolution strategies."""
    # Check relative to cwd
    if os.path.exists(base_path):
        return os.path.abspath(base_path)
    
    # Check relative to app root
    app_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    app_path = os.path.join(app_root, base_path)
    if os.path.exists(app_path):
        return app_path
    
    self._logger.error(f"Could not resolve path: {base_path}")
    return None
```

### 17. Memory Service Integration

- [ ] For stateful services, use MemoryService for persistence
- [ ] Wait for MEMORY_VALUE responses before proceeding
- [ ] Handle memory request timeouts gracefully
- [ ] Save state periodically, not just on shutdown

```python
async def _get_memory_value(self, key: str, timeout: float = 5.0) -> Any:
    """Get value from memory service with timeout."""
    response_event = asyncio.Event()
    response_value = None
    
    async def handle_response(event_name: str, payload: Dict[str, Any]):
        nonlocal response_value
        if payload.get("key") == key:
            response_value = payload.get("value")
            response_event.set()
    
    # Temporarily subscribe
    await self.subscribe(EventTopics.MEMORY_VALUE, handle_response)
    
    # Request value
    self._emit_dict(EventTopics.MEMORY_GET, {"key": key})
    
    # Wait with timeout
    try:
        await asyncio.wait_for(response_event.wait(), timeout)
    except asyncio.TimeoutError:
        self._logger.error(f"Timeout getting memory value for key: {key}")
    
    return response_value
```

### 18. Streaming API Response Handling

- [ ] For streaming APIs, accumulate responses properly
- [ ] Validate JSON completeness for tool calls
- [ ] Handle partial responses on timeout
- [ ] Process complete responses, not fragments

```python
async def _handle_streaming_response(self, stream):
    """Accumulate streaming response properly."""
    accumulated_text = ""
    
    async for chunk in stream:
        if chunk.choices[0].delta.content:
            accumulated_text += chunk.choices[0].delta.content
            # Emit progress if needed
            self._emit_dict(EventTopics.RESPONSE_CHUNK, {
                "text": chunk.choices[0].delta.content
            })
    
    # Process complete response
    if accumulated_text:
        self._emit_dict(EventTopics.RESPONSE_COMPLETE, {
            "text": accumulated_text
        })
```

### 19. Hardware Communication Patterns

- [ ] Use dedicated thread for hardware I/O
- [ ] Implement reconnection logic for hardware
- [ ] Add timeout protection for all hardware ops
- [ ] Provide mock mode for testing without hardware

```python
class HardwareService(StandardService):
    def __init__(self, event_bus, config=None, name="hardware_service"):
        super().__init__(event_bus, config, name=name)
        self._mock_mode = config.get("mock_mode", False)
        self._reconnect_attempts = 0
        self._max_reconnects = 3
        
    async def _connect_hardware(self):
        """Connect with retry logic."""
        while self._reconnect_attempts < self._max_reconnects:
            try:
                if self._mock_mode:
                    self._logger.info("Running in mock mode")
                    return True
                    
                # Actual hardware connection
                await self._establish_connection()
                self._reconnect_attempts = 0
                return True
                
            except Exception as e:
                self._reconnect_attempts += 1
                self._logger.error(f"Connection attempt {self._reconnect_attempts} failed: {e}")
                await asyncio.sleep(2 ** self._reconnect_attempts)  # Exponential backoff
                
        return False
```

### 20. Service State Management

- [ ] Track service state (INITIALIZING, RUNNING, ERROR, STOPPED)
- [ ] Emit status updates for monitoring
- [ ] Implement health check methods
- [ ] Handle graceful degradation

```python
async def _emit_status_update(self, status: str, details: str = ""):
    """Emit standardized status update."""
    self._emit_dict(EventTopics.SERVICE_STATUS, {
        "service_name": self._name,
        "status": status,
        "details": details,
        "timestamp": datetime.now().isoformat()
    })
```

## Final Steps

1. Run tests: `pytest tests/test_<service_name>.py`
2. Check linting: `make lint`
3. Verify service starts: `python main.py`
4. Test commands if applicable
5. Test with missing dependencies
6. Verify proper cleanup on shutdown
7. Check for memory leaks (long-running test)
8. Create PR with completed checklist

## Reference Documents

- `ARCHITECTURE_STANDARDS.md` - Detailed architectural requirements
- `service_template.py` - Template to copy from
- `TROUBLESHOOTING.md` - Common issues and fixes
- `dj-r3x-condensed-dev-log.md` - Lessons learned from development