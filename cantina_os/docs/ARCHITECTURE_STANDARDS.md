# CantinaOS Architecture Standards

This document outlines the architectural standards that all developers must follow when contributing to the CantinaOS project. Adhering to these standards ensures consistency, maintainability, and reliability of the codebase.

## 1. Service Architecture

### 1.1 Service Structure

All CantinaOS services must:

1. Inherit from `BaseService` or `StandardService`
2. Override `_start()` and `_stop()` methods, not `start()` or `stop()`
3. Implement proper error handling and resource cleanup
4. Use event-based communication through the event bus

```python
# Correct pattern
class MyService(StandardService):
    async def _start(self) -> None:
        # Service-specific initialization
        await self._setup_subscriptions()
        
    async def _stop(self) -> None:
        # Service-specific cleanup
        pass
        
# Incorrect pattern - DO NOT DO THIS
class MyService(BaseService):
    async def start(self) -> None:  # Wrong: overriding start() directly
        pass
```

### 1.2 Attribute Naming

Follow these naming conventions consistently:

1. Use protected attributes with underscore for internal state (`self._name`)
2. Provide property accessors for attributes needing external access
3. Use clear, descriptive names

```python
# Correct pattern
class MyService(StandardService):
    def __init__(self, event_bus, config, logger=None):
        super().__init__(event_bus, config, logger)
        self._counter = 0  # Protected attribute
        
    @property
    def counter(self):  # Public accessor
        return self._counter
        
# Incorrect pattern - DO NOT DO THIS
class MyService(BaseService):
    def __init__(self, event_bus, config, logger=None):
        super().__init__(event_bus, config, logger)
        self.counter = 0  # Wrong: public attribute for internal state
```

### 1.3 Event Handling

Always use the following pattern for event subscriptions:

1. Set up subscriptions in a dedicated `_setup_subscriptions()` method
2. Always wrap subscription calls in `asyncio.create_task()` 
3. Implement one handler method per event type
4. Use proper error handling in event handlers

```python
# Correct subscription pattern
async def _setup_subscriptions(self) -> None:
    asyncio.create_task(self.subscribe(
        EventTopics.EXAMPLE_TOPIC,
        self._handle_example_event
    ))
    
# Incorrect - DO NOT DO THIS
async def _setup_subscriptions(self) -> None:
    self.subscribe(EventTopics.EXAMPLE_TOPIC, self._handle_event)  # Wrong: not awaited or wrapped
```

## 2. Error Handling

### 2.1 Standard Error Handling Pattern

Use this consistent error handling pattern:

```python
try:
    # Operation that might fail
    result = await some_operation()
except SomeSpecificException as e:
    # Log error with context
    self.logger.error(f"Specific error during operation: {e}")
    # Emit error response
    await self.emit_error_response(
        EventTopics.OPERATION_RESPONSE,
        {"error": str(e), "is_error": True}
    )
except Exception as e:
    # Log unexpected error
    self.logger.error(f"Unexpected error: {e}")
    # Emit error response for unexpected error
    await self.emit_error_response(
        EventTopics.OPERATION_RESPONSE,
        {"error": f"Unexpected error: {str(e)}", "is_error": True}
    )
```

### 2.2 Service Status Reporting

Use `_emit_status()` to update service status:

```python
# When service encounters an error
await self._emit_status(
    ServiceStatus.ERROR,
    f"Error during operation: {e}",
    severity=LogLevel.ERROR
)

# When service operation succeeds
await self._emit_status(
    ServiceStatus.RUNNING,
    "Operation completed successfully",
    severity=LogLevel.INFO
)
```

## 3. Asynchronous Programming

### 3.1 Async Method Usage

Follow these async/await best practices:

1. Always `await` async method calls
2. Only mark methods as `async` if they perform async operations
3. Use `asyncio.gather()` for concurrent operations
4. Handle task cancellation properly

```python
# Correct pattern
async def process_data(self, data):
    task1 = self._process_part1(data)
    task2 = self._process_part2(data)
    results = await asyncio.gather(task1, task2)
    return results
    
# Incorrect - DO NOT DO THIS
async def method_with_no_awaits(self):  # Wrong: async but no awaits
    return 42
```

### 3.2 Task Management

Always manage tasks properly:

1. Store long-running tasks for cancellation
2. Cancel tasks explicitly during cleanup
3. Use proper exception handling with tasks

```python
# Correct task management
def __init__(self, event_bus, config, logger=None):
    super().__init__(event_bus, config, logger)
    self._tasks = []
    
async def _start(self) -> None:
    task = asyncio.create_task(self._background_work())
    self._tasks.append(task)
    
async def _stop(self) -> None:
    # Cancel all tasks
    for task in self._tasks:
        if not task.done():
            task.cancel()
            
    # Wait for tasks to complete with timeout
    if self._tasks:
        await asyncio.wait(self._tasks, timeout=5.0)
    
    # Clear task list
    self._tasks.clear()
```

## 4. Configuration Management

### 4.1 Using Pydantic for Configuration

Use Pydantic models for configuration:

```python
from pydantic import BaseModel, Field

class MyServiceConfig(BaseModel):
    timeout_ms: int = Field(default=1000, description="Timeout in milliseconds")
    retry_attempts: int = Field(default=3, description="Number of retry attempts")
    
# In your service
def __init__(self, event_bus, config_dict, logger=None):
    super().__init__(event_bus, config_dict, logger)
    
    # Convert dict to Pydantic model with validation
    self._config = MyServiceConfig(**config_dict)
```

### 4.2 Configuration Access

Access configuration consistently:

```python
# Correct configuration access
timeout = self._config.timeout_ms
max_retries = self._config.retry_attempts

# Avoid accessing raw dictionaries directly
```

## 5. Code Structure Standards

### 5.1 Method Order

Organize methods in this order:

1. `__init__`
2. Properties (e.g., `@property def name()`)
3. Lifecycle methods (`_start`, `_stop`, etc.)
4. Public methods
5. Protected helper methods (with `_` prefix)
6. Event handlers (with `_handle_` prefix)

### 5.2 Documentation

Document code consistently:

1. Add docstrings to all classes and methods
2. Use type hints for all parameters and return values
3. Document parameters with the Args section
4. Document return values and exceptions

```python
def process_data(self, data: Dict[str, Any]) -> List[str]:
    """Process the input data and return a list of results.
    
    Args:
        data: Dictionary containing the data to process
        
    Returns:
        List of processed strings
        
    Raises:
        ValueError: If data is invalid
    """
    pass
```

## 6. Testing Standards

### 6.1 Service Testing

Test services with these patterns:

1. Test service initialization
2. Test service lifecycle (start/stop)
3. Test event handling with mock event bus
4. Test error conditions and recovery

### 6.2 Mocking

Use these mocking patterns:

1. Mock the event bus with `AsyncMock`
2. Mock external dependencies
3. Verify event emissions
4. Test both success and failure paths

## 7. New Service Creation

When creating a new service:

1. Use `service_template.py` as a starting point
2. Follow the naming and structure conventions precisely
3. Implement proper error handling and resource cleanup
4. Add comprehensive tests

### 7.3 Service Creation Checklist

Use this checklist when creating a new service to ensure you follow all architecture standards:

#### Required Structural Elements
- [ ] Inherit from `BaseService` or `StandardService`
- [ ] Define descriptive class docstring explaining service purpose and features
- [ ] Use protected attributes with underscore prefix (`self._name`) for internal state
- [ ] Implement `_start()` and `_stop()` methods (don't override `start()` or `stop()`)
- [ ] Create a dedicated `_setup_subscriptions()` method for event subscriptions

#### Event Handling
- [ ] Import required `EventTopics` enum values for all event topics
- [ ] Import Pydantic payload models from `event_payloads.py`
- [ ] Wrap ALL subscriptions in `asyncio.create_task()`: 
      ```python
      asyncio.create_task(self.subscribe(EventTopics.EXAMPLE_TOPIC, self._handle_example_event))
      ```
- [ ] Create dedicated handler methods with `_handle_` prefix for each event type
- [ ] Convert all Pydantic payload models to dictionaries with `model_dump()` before emission
- [ ] Follow consistent payload pattern: create model, dump to dict, then emit

#### Thread Safety (if applicable)
- [ ] Store event loop reference during service initialization
- [ ] Update loop reference in `_start()` with `asyncio.get_running_loop()`
- [ ] Use `run_coroutine_threadsafe` for thread-to-asyncio communication
- [ ] Implement proper error handling and timeouts for thread boundary operations
- [ ] Follow single direction data flow (avoid multiple thread crossings)

#### Error Handling
- [ ] Implement comprehensive error handling in all methods
- [ ] Use `try/except` blocks with specific exception types
- [ ] Emit appropriate error events with context using `ServiceStatusPayload`
- [ ] Use `emit_error_response()` for user-facing errors
- [ ] Ensure proper resource cleanup in error paths

#### Resource Management
- [ ] Store tasks in `self._tasks` list for proper cancellation
- [ ] Implement comprehensive cleanup in `_stop()` method
- [ ] Cancel all tasks and wait for completion with timeout
- [ ] Release all hardware resources (connections, devices, etc.)
- [ ] Clear all internal state appropriately

#### Configuration
- [ ] Use Pydantic models for configuration validation if complex
- [ ] Provide sensible defaults for all configuration options
- [ ] Validate configuration values during initialization
- [ ] Handle missing or invalid configuration gracefully

#### Documentation and Testing
- [ ] Add docstrings to all methods explaining purpose and parameters
- [ ] Use type hints for all parameters and return values
- [ ] Create comprehensive unit tests for the service
- [ ] Add integration tests for interaction with other services
- [ ] Document any special requirements or dependencies

This checklist should be reviewed for each new service and for significant service updates to ensure consistency across the codebase. Mark each item as it's completed to ensure nothing is missed.

## 8. Event System Guidelines

### 8.1 Event Topic Naming

Follow these event naming conventions:

1. Use `EventTopics` enum for all event topics
2. Use UPPERCASE_WITH_UNDERSCORES for event topic names
3. Use descriptive names that indicate the event purpose

### 8.2 Event Payload Structure

Structure event payloads consistently:

1. Use Pydantic models for all event payloads
2. Include common fields like `timestamp` and `service_name`
3. Include explicit success/error indicators when appropriate
4. Use descriptive field names

## 9. CLI Command System Standards

### 9.1 Command Registration

All CLI commands must be registered through the CommandDispatcherService using these guidelines:

1. **Basic Command Registration**:
```python
# In main.py during service initialization
await command_dispatcher.register_command_handler(
    command="status",
    handler_topic=EventTopics.CLI_STATUS_REQUEST,
    description="Display system status"
)
```

2. **Compound Command Registration**:
```python
# For commands with subcommands like "eye pattern"
await command_dispatcher.register_compound_command(
    base_command="eye",
    subcommand="pattern",
    handler_topic=EventTopics.EYE_COMMAND,
    description="Set eye LED pattern"
)
```

### 9.2 Command Handler Implementation

Implement command handlers following these standards:

1. **Handler Method Naming**:
   - Use `_handle_<command>_command` for basic commands
   - Use `_handle_<service>_command` for service-specific commands

2. **Payload Processing**:
```python
async def _handle_eye_command(self, payload: Dict[str, Any]) -> None:
    try:
        # Convert to StandardCommandPayload
        command_payload = StandardCommandPayload(**payload)
        
        # Extract command components
        command = command_payload.command
        subcommand = command_payload.subcommand
        args = command_payload.args
        
        # Process command
        if subcommand == "pattern":
            await self._handle_pattern_command(args)
        elif subcommand == "test":
            await self._handle_test_command()
        else:
            await self.emit_error_response(
                EventTopics.EYE_COMMAND_RESPONSE,
                {"error": f"Unknown subcommand: {subcommand}"}
            )
    except Exception as e:
        await self.emit_error_response(
            EventTopics.EYE_COMMAND_RESPONSE,
            {"error": f"Error processing command: {e}"}
        )
```

### 9.3 Command Response Standards

Follow these standards for command responses:

1. **Success Response**:
```python
await self.emit(
    EventTopics.COMMAND_RESPONSE,
    {
        "success": True,
        "message": "Command completed successfully",
        "data": result_data  # Optional
    }
)
```

2. **Error Response**:
```python
await self.emit_error_response(
    EventTopics.COMMAND_RESPONSE,
    {
        "error": str(error),
        "command": command,
        "args": args
    }
)
```

### 9.4 Command Implementation Checklist

When adding a new command:

- [ ] Define command in EventTopics if needed
- [ ] Create StandardCommandPayload model if custom fields needed
- [ ] Register command in main.py
- [ ] Implement handler in appropriate service
- [ ] Add command to help text
- [ ] Add error handling
- [ ] Test all command variations
- [ ] Document command in README.md

### 9.5 Unified Command Flow Architecture

Commands must follow the unified three-tier architecture:

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│ CLI Command │────▶│   Command   │────▶│  Timeline   │────▶│    Music    │
└─────────────┘     │ Dispatcher  │     │  Executor   │     │ Controller  │
                    └─────────────┘     └─────────────┘     └─────────────┘
┌─────────────┐     ┌─────────────┐            ▲            
│    Voice    │────▶│    Brain    │────────────┘            
└─────────────┘     └─────────────┘                         
                           ▲                                
┌─────────────┐           │                                 
│   DJ Mode   │───────────┘                                 
└─────────────┘                                             
```

1. **Command Entry**:
   - CLI command: CLIService emits to CLI_COMMAND topic
   - Voice command: BrainService processes intent into command
   - DJ Mode: Automatic transitions and command generation

2. **Command Routing**:
   - CommandDispatcherService routes command to appropriate service
   - Apply service-specific payload transformation to match expected formats
   - Use `register_command` with service name parameter:
     ```python
     # Register command with service context
     dispatcher.register_command(
         command="dj start", 
         service_name="brain_service", 
         event_topic=EventTopics.DJ_COMMAND
     )
     ```

3. **Payload Transformation**:
   - Commands must be transformed to match service expectations:
     ```python
     def _transform_payload_for_service(self, service_name: str, command: str, args: list, raw_input: str) -> dict:
         """Transform command payload to match service expectations"""
         # Special handling for brain_service DJ mode commands
         if service_name == "brain_service" and command.startswith("dj"):
             if command == "dj start":
                 return {"dj_mode_active": True}
             elif command == "dj stop":
                 return {"dj_mode_active": False}
         
         # Create timeline plan payloads for music commands
         if command == "play music":
             track_query = " ".join(args) if args else ""
             # Create a plan payload
             return PlanPayload(
                 plan_id=str(uuid.uuid4()),
                 layer="foreground",
                 steps=[
                     PlanStep(
                         id="music",
                         type="play_music",
                         genre=track_query
                     )
                 ]
             )
         
         # Default generic payload format
         return {"command": command, "args": args, "raw_input": raw_input}
     ```

4. **Plan-Based Execution**:
   - All music commands must flow through TimelineExecutorService
   - Consistent data structures must be used across all command sources
   - Use Pydantic models for validation and serialization:
     ```python
     # Standard music track model
     class MusicTrack(BaseModel):
         name: str
         path: str
         duration: Optional[float] = None
         artist: Optional[str] = None
         genre: Optional[str] = None
     
     # Standard plan structure
     class PlanStep(BaseModel):
         id: str
         type: str
         genre: Optional[str] = None
         text: Optional[str] = None
         # Additional fields as needed
     
     class PlanPayload(BaseModel):
         plan_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
         layer: str = "foreground"
         steps: List[PlanStep]
     ```

5. **Service Response**:
   - Services respond with standard response formats
   - Include success/error indicators
   - Provide helpful context in response

This unified architecture ensures consistent handling of commands from all sources, proper audio coordination, and standardized data structures throughout the system. All new command implementations MUST follow this architecture.

### 9.6 Command Validation

Implement these validation steps:

1. **Input Validation**:
   - Validate required arguments
   - Check argument types and ranges
   - Verify command format

2. **State Validation**:
   - Check if service is ready
   - Verify required resources available
   - Check for conflicting operations

3. **Response Validation**:
   - Ensure response includes success/error status
   - Include relevant context in errors
   - Provide helpful error messages

### 9.7 Payload Transformation Standards

Commands like "list music", "play music", "dj start" require special handling:

```python
# In CommandDispatcher._transform_payload_for_service()
if command == "list" and args and args[0] == "music":
    actual_command = "list music"
    actual_args = args[1:]  # Remove "music" from args

# Service-specific transformations
if service_name == "brain_service" and command == "dj start":
    return {"dj_mode_active": True}

When implementing command handling, services MUST properly transform payloads to match expected formats:

1. **Multi-word Command Handling**:
   ```python
   # CORRECT: Handle multi-word commands properly
   def _transform_payload_for_service(self, service_name: str, command: str, args: list, raw_input: str) -> dict:
       # For multi-word commands like "list music", "play music", etc.
       if command == "list" and args and args[0] == "music":
           actual_command = "list music"
           actual_args = args[1:]  # Remove "music" from args
       
       # Transform to service-specific format
       return self._get_service_payload(service_name, actual_command, actual_args)

2. **Service-Specific Payload Formats:**
    - Document expected payload format for each service
    - Include examples of correct transformations
    - Specify which fields are required vs optional
    - Use Pydantic models for validation where appropriate

    **Examples Transformations**

    ```python
    # For BrainService DJ commands
    if service_name == "brain_service" and command.startswith("dj"):
        if command == "dj start":
            return {"dj_mode_active": True}
        elif command == "dj stop":
            return {"dj_mode_active": False}

    # For MusicController commands
    if command == "play music":
        track_query = " ".join(args) if args else ""
        return PlanPayload(
            plan_id=str(uuid.uuid4()),
            layer="foreground",
            steps=[
                PlanStep(
                    id="music",
                    type="play_music",
                    genre=track_query
                )
            ]
        ).model_dump()

3. **Backward Compatibility:* 
    - Maintain support for legacy payload formats during transitions
    - Use compatibility layers when updating payload structures
    - Document migration paths for payload format changes
    
    ```python
    # Example compatibility layer
    def _handle_command(self, payload: dict):
        # Support both old and new payload formats
        if isinstance(payload.get("service_info"), dict):
            # New format
            service_name = payload["service_info"]["service_name"]
        else:
            # Legacy format
            service_name = self._infer_service_name(payload["command"])


## 10. Audio Processing Standards

### 10.1 Threading Model

Audio processing services must follow these threading guidelines:

1. Use a dedicated thread for audio I/O operations (recording/playback)
2. Maintain the main event loop for service coordination
3. Use thread-safe mechanisms for communication between audio thread and main event loop
4. Store the event loop during service initialization for thread-safe operations

The key principle is to maintain a **single direction of data flow**:

```python
class AudioService(StandardService):
    def __init__(self, event_bus, config, logger=None):
        super().__init__(event_bus, config, logger)
        self._event_loop = asyncio.get_event_loop()
        self._audio_thread = None
        self._audio_queue = asyncio.Queue(maxsize=100)
        
    def _audio_thread_function(self):
        """Dedicated thread for audio I/O operations."""
        try:
            while not self._stop_flag.is_set():
                # Audio I/O operations here
                # Use a single thread-safe queue for all audio data
                future = asyncio.run_coroutine_threadsafe(
                    self._audio_queue.put(data),
                    self._event_loop
                )
                # Wait for the queue operation to complete
                future.result(timeout=1.0)
        except Exception as e:
            self._event_loop.call_soon_threadsafe(
                self._handle_audio_thread_error, e
            )

    async def _process_audio_queue(self):
        """Process audio data in the event loop."""
        while True:
            try:
                # Get data from the queue
                data = await self._audio_queue.get()
                
                # Process in the event loop context
                await self._process_audio_data(data)
                
                # Mark task as done
                self._audio_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Error processing audio: {e}")
```

### 10.2 Audio Data Flow

Follow these patterns for audio data handling:

1. Use a **single** asyncio.Queue for audio data transfer
2. Process all audio data in the event loop context
3. Avoid multiple thread crossings for the same data
4. Use timeouts on thread-safe operations

```python
class AudioProcessor(StandardService):
    async def _process_audio_data(self, data: np.ndarray):
        """Process audio data in the event loop context."""
        try:
            # All processing happens in the event loop
            processed_data = await self._apply_processing(data)
            
            # Emit events in the event loop context
            await self.emit(EventTopics.AUDIO_PROCESSED, {
                "data": processed_data,
                "timestamp": time.time()
            })
        except Exception as e:
            self.logger.error(f"Error processing audio: {e}")
            await self._emit_status(
                ServiceStatus.ERROR,
                f"Audio processing error: {e}",
                severity=LogLevel.ERROR
            )
```

### 10.3 Resource Management

Audio services must properly manage system resources:

1. Release audio devices on service stop
2. Implement graceful shutdown of audio threads
3. Handle device disconnection/reconnection
4. Monitor system audio resources

```python
async def _stop(self):
    """Cleanup audio resources."""
    # Signal audio thread to stop
    self._stop_flag.set()
    
    # Wait for audio thread with timeout
    if self._audio_thread and self._audio_thread.is_alive():
        self._audio_thread.join(timeout=5.0)
        
    # Cancel queue processing task
    if self._queue_task:
        self._queue_task.cancel()
        try:
            await self._queue_task
        except asyncio.CancelledError:
            pass
        
    # Release audio resources
    if hasattr(self, '_audio_device'):
        self._audio_device.close()
```

### 10.4 Error Handling

Implement robust error handling for audio operations:

1. Handle device errors (disconnection, access denied)
2. Manage buffer underrun/overflow conditions
3. Implement automatic recovery strategies
4. Provide detailed error reporting

```python
async def _handle_audio_device_error(self, error):
    """Handle audio device errors with recovery."""
    await self._emit_status(
        ServiceStatus.ERROR,
        f"Audio device error: {error}",
        severity=LogLevel.ERROR
    )
    
    # Attempt recovery
    for attempt in range(self._config.max_retry_attempts):
        try:
            await self._reinitialize_audio_device()
            return
        except Exception as e:
            self.logger.error(f"Recovery attempt {attempt + 1} failed: {e}")
            
    # Failed to recover
    await self._emit_status(
        ServiceStatus.FAILED,
        "Failed to recover audio device",
        severity=LogLevel.CRITICAL
    )
```

### 10.5 Thread Synchronization Rules

To maintain proper thread synchronization:

1. Audio thread should ONLY write to a single queue
2. Event loop should handle ALL processing
3. Never cross thread boundaries multiple times with the same data
4. Use timeouts on ALL thread-safe operations
5. Keep audio callback functions minimal and fast
6. Handle errors at both thread and event loop levels

```python
# CORRECT: Single thread crossing
def _audio_callback(self, indata, frames, time, status):
    if status:
        self._event_loop.call_soon_threadsafe(self._handle_status_error, status)
        return
        
    try:
        # Single thread crossing with timeout
        future = asyncio.run_coroutine_threadsafe(
            self._audio_queue.put(indata.copy()),
            self._event_loop
        )
        future.result(timeout=0.1)  # Short timeout to avoid blocking
    except Exception as e:
        self._event_loop.call_soon_threadsafe(self._handle_callback_error, e)

# INCORRECT: Multiple thread crossings
def _audio_callback(self, indata, frames, time, status):
    # DON'T DO THIS: Multiple thread crossings
    future1 = asyncio.run_coroutine_threadsafe(self._process1(indata), self._event_loop)
    future2 = asyncio.run_coroutine_threadsafe(self._process2(indata), self._event_loop)
    future3 = asyncio.run_coroutine_threadsafe(self._process3(indata), self._event_loop)
```

## 11. I/O and Logging

### 11.1 Asynchronous Logging to Console

When logging in an asyncio application, using standard blocking logging handlers (like `StreamHandler` writing directly to `sys.stdout` or `sys.stderr`) from within the event loop can cause `BlockingIOError`.

To prevent this, use a queued logging approach where log records are put into a queue from the event loop, and a separate thread processes the queue and writes to the console.

Use `logging.handlers.QueueHandler` and `logging.handlers.QueueListener` for this pattern.

**Implementation Pattern (typically in main application entry point):**

1.  Create a `queue.Queue`.
2.  Create a `logging.handlers.QueueHandler` pointing to the queue and add it to the root logger.
3.  Remove any other blocking handlers from the root logger.
4.  Create a standard blocking handler (e.g., `logging.StreamHandler`) to write to the console.
5.  Create a `logging.handlers.QueueListener` with the queue and the console handler.
6.  Start the `QueueListener` in a separate thread at application startup (`listener.start()`).
7.  Stop the `QueueListener` gracefully at application shutdown (`listener.stop()`).

```python
import asyncio
import logging
import queue
import logging.handlers
import sys

# ... (rest of imports and setup)

# Create a queue for log records
log_queue = queue.Queue()

# Create a handler to put records in the queue
queue_handler = logging.handlers.QueueHandler(log_queue)

# Configure the root logger to use the queue handler
root_logger = logging.getLogger()
for handler in root_logger.handlers[:]:
    root_logger.removeHandler(handler)
root_logger.addHandler(queue_handler)
root_logger.setLevel(logging.DEBUG) # Capture all messages at root

# Create a handler to write logs to the console (runs in listener thread)
console_handler = logging.StreamHandler(sys.stdout)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
console_handler.setFormatter(formatter)
console_handler.setLevel(logging.INFO) # Set desired output level

# Create and start the QueueListener
log_listener = logging.handlers.QueueListener(log_queue, console_handler)
# Start the listener thread (call this in your application's startup)
# log_listener.start()

# ... (rest of application code)

# Stop the listener thread gracefully (call this in your application's shutdown)
# log_listener.stop()
```

**Why this is important:**

- Prevents `BlockingIOError` in the asyncio event loop.
- Ensures smooth application operation even with high logging volume.
- Decouples log generation from log output I/O.

## 12. Preventing Event Subscription Race Conditions

### 12.1 Critical Pattern - Always Await Subscriptions

Services that need responses from other services during startup MUST await their subscriptions:

```python
# CORRECT - Prevents race conditions
async def _start(self) -> None:
    # Wait for all subscriptions to complete
    await asyncio.gather(
        self.subscribe(EventTopics.MEMORY_VALUE, self._handle_memory_value),
        self.subscribe(EventTopics.DJ_MODE_CHANGED, self._handle_dj_mode_changed)
    )
    
    # NOW safe to request data that requires responses
    await self.emit(EventTopics.MEMORY_GET, {"key": "dj_mode"})

# WRONG - Race condition, might miss response
async def _start(self) -> None:
    asyncio.create_task(self.subscribe(EventTopics.MEMORY_VALUE, self._handle_memory))
    await self.emit(EventTopics.MEMORY_GET, {"key": "dj_mode"})  # Response might be missed!


## Conclusion


Following these architectural standards consistently will help ensure that the CantinaOS codebase remains maintainable, reliable, and scalable. These standards should be reviewed and updated as needed based on project evolution and team feedback. 