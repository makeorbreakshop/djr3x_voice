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

### 9.5 Command Flow Architecture

Commands must follow this flow:

1. CLIService receives user input
2. CLIService emits to CLI_COMMAND topic
3. CommandDispatcherService receives and routes command
4. Service-specific handler processes command
5. Handler emits response
6. CLIService displays response to user

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

## Conclusion

Following these architectural standards consistently will help ensure that the CantinaOS codebase remains maintainable, reliable, and scalable. These standards should be reviewed and updated as needed based on project evolution and team feedback. 