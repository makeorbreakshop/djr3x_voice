# Cantina OS Service Template Guidelines

⚠️ **CRITICAL: service_template.py is a COPY template, NOT a module to import from**
- ✅ DO copy service_template.py to create new services
- ❌ DO NOT import from service_template.py
- ❌ DO NOT use it as a base class

> **Use this doc verbatim in Cursor's "Prompt to… file" field when you spin
> up a new service.**

---

## 0 · Purpose

A one‑page checklist + rationale that bakes our Architecture Standards and
recurring bug‑fixes directly into day‑zero development.  Follow it and
your service will:

* boot without explosions,
* shut down cleanly,
* never leak tasks or threads,
* talk to the bus with validated payloads,
* and satisfy the automated lint gate.

---

## 1 · Repo Boiler‑plate

```text
cantina_os/
└── cantina_os/
    └── services/
        └── <your_service>/
            ├── __init__.py
            ├── <your_service>.py   ← copy of service_template.py, renamed (NEVER import from template)
            └── tests/
                └── test_<your_service>.py
```

IMPORTANT: 
1. Note the nested cantina_os directory structure. Always place services in the inner cantina_os/services/ directory.
2. NEVER import from service_template.py - it is a template to copy from, not a module to import.

---

## 2 · Mandatory steps (check them *all*)

| #  | Step                                                                  | Why it matters                                                         |
| -- | --------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| 1  | **Duplicate & rename** `service_template.py` → `<your_service>.py`    | Keeps imports & CLSID unique (pre‑commit will yell otherwise).         |
| 2  | Rename the class from `ServiceTemplate` → `YourServiceName`           | Duplicate class‑names cause service‑loader collisions.                 |
| 3  | Extend the inner `_Config` Pydantic model with your settings          | Prevents runtime KeyErrors & instantly documents the API.              |
| 4  | Implement `_setup_subscriptions()` using `_subscribe(topic, handler)` | Guards against forgotten `await` & tracks tasks for cleanup.           |
| 5  | Use `_emit_dict()` for *every* event to auto‑dump Pydantic payloads   | Avoids the "object has no attribute get" bug.                          |
| 6  | Store thread callbacks via `run_threadsafe()`                         | Eliminates *"no running loop in thread"* exceptions with audio/serial. |
| 7  | Put long‑running coroutines in `self._tasks`                          | Lets `_stop()` cancel them gracefully.                                 |
| 8  | Flesh out `_stop()` to close hardware, cancel tasks, and unsubscribe  | Prevents resource leaks and hanging processes.                         |
| 9  | Emit statuses via `_emit_status()` (OK, WARN, ERROR)                  | Surfaces problems to monitoring & CLI dashboards.                      |
| 10 | Write tests: **init**, **event flow**, **error path**                 | CI gates for regressions.                                              |

Tick all ten boxes before opening a PR.  The reviewer will copy/paste
this grid and mark ☑️ or ❌.

---

## 3 · Service Initialization Requirements (CRITICAL)

The StandardService base class has specific initialization requirements:

```python
# CORRECT: StandardService requires event_bus as first positional parameter
class MyService(StandardService):
    def __init__(self, event_bus, config=None, name="my_service"):
        super().__init__(event_bus, config, name=name)
        # Service-specific initialization
```

CRITICAL REQUIREMENTS:
- `event_bus` must be the first positional parameter
- `config` must be the second positional parameter
- DO NOT use keyword-only arguments syntax (`*,`) in your `__init__` method
- Always pass event_bus and config to super().__init__
- Your service will not initialize if these requirements are not met

```python
# INCORRECT: This pattern will fail at runtime
class MyService(StandardService):
    def __init__(
        self,
        *,  # DO NOT USE keyword-only arguments syntax
        name: str = "my_service",
        config: Dict[str, Any] | None = None,
    ) -> None:
        super().__init__(name=name)  # WRONG: missing event_bus and config
```

---

## 4 · Service Registration in main.py (CRITICAL)

Each service MUST be properly registered in the service_class_map within main.py:

```python
# In main.py
service_class_map = {
    # Existing services
    "your_service": YourService,  # Add your service here
}
```

CRITICAL REQUIREMENTS:
- Service key name MUST match the name parameter in your service constructor
- Service class must be imported at the top of main.py
- Service name should follow snake_case convention
- Verify service is registered BEFORE running the application

EXAMPLE:
```python
# Top of main.py
from cantina_os.services.your_service.your_service import YourService

# In service_class_map
service_class_map = {
    "command_dispatcher": CommandDispatcherService,
    "your_service": YourService,  # Added here
}
```

Missing service registration causes `'NoneType' object has no attribute 'start'` errors.

---

## 4.1 · Command Registration for Services (CRITICAL)

If your service handles CLI commands, especially multi-word commands, you MUST properly register them:

```python
# In main.py after initializing services
command_dispatcher.register_command("command_name", "service_name", EventTopics.COMMAND_TOPIC)
```

CRITICAL REQUIREMENTS FOR MULTI-WORD COMMANDS:
- Register the FULL command string including spaces ("dj start", not just "dj")
- Use the same service name that's in service_class_map
- Specify the appropriate EventTopic the service listens on
- Verify command registration in logs during startup

EXAMPLE:
```python
# Register eye commands
command_dispatcher.register_command("eye pattern", "eye_light_controller", EventTopics.EYE_COMMAND)
command_dispatcher.register_command("eye status", "eye_light_controller", EventTopics.EYE_COMMAND)

# Register DJ commands
command_dispatcher.register_command("dj start", "brain_service", EventTopics.DJ_COMMAND)
command_dispatcher.register_command("dj stop", "brain_service", EventTopics.DJ_COMMAND)
```

Common issues caused by improper command registration:
- "Unknown command" errors for multi-word commands
- Base command parsed without arguments (e.g., "dj" command with "start" argument)
- Commands appearing in help but not functioning

---

## 5 · Event‑bus patterns (CRITICAL UPDATES)

### Event Bus Methods Are NOT Coroutines

```python
# CORRECT: emit() is NOT a coroutine
def _emit(self, topic, payload):
    self._event_bus.emit(topic, payload)  # DO NOT use await here!
    
# CORRECT: on() is NOT a coroutine
def _subscribe(self, topic, handler):
    self._event_bus.on(topic, handler)  # DO NOT use await here!
    
# INCORRECT: This will cause TypeError
async def _emit_wrong(self, topic, payload):
    await self._event_bus.emit(topic, payload)  # WRONG! Not a coroutine!
```

* **Emit is sync** – *never* `await self.emit(...)` or `await self._event_bus.emit(...)`. 
* **Subscribe is sync** – *never* `await self._event_bus.on(...)`. 
* Always use the helper methods provided in BaseService to handle subscriptions correctly.
* Validation should happen **before** side‑effects; log & `_emit_status` on `ValidationError`.
* Keep topic names in the shared `EventTopics` enum; don't invent raw strings.

### Event Payload Validation

Always validate payloads using Pydantic models:

```python
# Define a Pydantic model for your event payload
class MyEventPayload(BaseModel):
    value: str
    timestamp: datetime = Field(default_factory=datetime.now)

# Emit with automatic validation
payload = MyEventPayload(value="some_value")
self._emit_dict(EventTopics.MY_TOPIC, payload)
```

---

## 6 · Task Management (Critical)

### Creating and Tracking Tasks

Always use asyncio.create_task and store tasks for proper cleanup:

```python
async def _start(self):
    # Create a background task
    task = asyncio.create_task(self._background_worker())
    
    # IMPORTANT: Add to self._tasks for tracking and cleanup
    self._tasks.append(task)
    
    # Add error handling
    task.add_done_callback(self._handle_task_exception)
```

### Task Cleanup

Properly cancel all tasks during service shutdown:

```python
async def _stop(self):
    # Cancel all tracked tasks
    for task in self._tasks:
        if not task.done():
            task.cancel()
            
    # Wait for tasks to complete cancellation
    if self._tasks:
        await asyncio.gather(*self._tasks, return_exceptions=True)
        
    # Clear task list
    self._tasks.clear()
```

### Error Handling

Add error callbacks to all tasks:

```python
def _handle_task_exception(self, task):
    try:
        exc = task.exception()
        if exc:
            self._logger.error(f"Task failed with exception: {exc}")
            self._emit_status(ServiceStatus.ERROR, f"Background task error: {exc}")
    except asyncio.CancelledError:
        pass  # Task was cancelled, this is normal during shutdown
```

---

## 7 · Thread bridging cheat‑sheet

```python
audio_interface.on_peak(lambda level:  # called from audio thread
    service.run_threadsafe(service.handle_peak(level))
)
```

`run_threadsafe` drops the coroutine back onto the service's main loop.

### Proper Thread Handling Example

```python
def _setup_hardware(self):
    # Store callback reference for cleanup
    self._audio_callback = lambda level: self.run_threadsafe(self._handle_audio_level(level))
    
    # Register with hardware
    self._audio_device.register_callback(self._audio_callback)
    
async def _stop(self):
    # Unregister callback during cleanup
    if hasattr(self, '_audio_callback') and self._audio_device:
        self._audio_device.unregister_callback(self._audio_callback)
```

---

## 8 · Configuration and Path Resolution

### Standardized Configuration Model

Use Pydantic for all service configuration:

```python
class MusicControllerConfig(BaseModel):
    music_dir: str = "audio/music"
    supported_formats: List[str] = ["mp3", "wav"]
    volume_default: float = 0.5
```

### Robust Path Resolution

Implement multi-level path checking for file/directory paths:

```python
def _resolve_path(self, base_path):
    """Resolve a path with multiple fallback options."""
    # Try relative to current working directory
    if os.path.exists(base_path):
        return os.path.abspath(base_path)
        
    # Try relative to application root
    app_root = os.path.dirname(os.path.dirname(__file__))
    app_path = os.path.join(app_root, base_path)
    if os.path.exists(app_path):
        return app_path
        
    # Try relative to the root directory
    root_path = os.path.join("/", base_path)
    if os.path.exists(root_path):
        return root_path
        
    # Log the failure
    self._logger.error(f"Could not resolve path: {base_path}")
    self._emit_status(ServiceStatus.ERROR, f"Path not found: {base_path}")
    return None
```

---

## 9 · API Streaming Response Handling

If your service handles streaming API responses (e.g., from OpenAI):

### Accumulating Streaming Responses

```python
async def _process_streaming_response(self, response_stream):
    accumulated_text = ""
    tool_calls = []
    current_tool_call = None
    
    async for chunk in response_stream:
        # Process text content
        if chunk.choices[0].delta.content:
            content = chunk.choices[0].delta.content
            accumulated_text += content
            
        # Process tool calls
        if chunk.choices[0].delta.tool_calls:
            for tool_call_delta in chunk.choices[0].delta.tool_calls:
                # Handle new tool call
                if tool_call_delta.index is not None and (not current_tool_call or 
                                                         current_tool_call.index != tool_call_delta.index):
                    if current_tool_call:
                        tool_calls.append(current_tool_call)
                    current_tool_call = ToolCall(index=tool_call_delta.index)
                
                # Accumulate function name
                if tool_call_delta.function and tool_call_delta.function.name:
                    if not current_tool_call.function.name:
                        current_tool_call.function.name = tool_call_delta.function.name
                    else:
                        current_tool_call.function.name += tool_call_delta.function.name
                
                # Accumulate arguments (JSON string)
                if tool_call_delta.function and tool_call_delta.function.arguments:
                    if not current_tool_call.function.arguments:
                        current_tool_call.function.arguments = tool_call_delta.function.arguments
                    else:
                        current_tool_call.function.arguments += tool_call_delta.function.arguments
    
    # Add the last tool call if exists
    if current_tool_call:
        tool_calls.append(current_tool_call)
        
    # Process complete response
    await self._process_complete_response(accumulated_text, tool_calls)
```

### Validating Complete Tool Calls

Always validate tool call arguments for completeness:

```python
def _validate_tool_call(self, tool_call):
    if not tool_call.function.name:
        return False
        
    # Validate JSON arguments
    try:
        # Attempt to parse JSON
        json.loads(tool_call.function.arguments)
        return True
    except json.JSONDecodeError:
        self._logger.error(f"Invalid JSON in tool call arguments: {tool_call.function.arguments}")
        return False
```

---

## 10 · Tool Call Processing

If your service handles LLM tool calls (e.g., from GPT), consider the two-step approach:

### Two-Step Tool Call Pattern

```python
async def _process_with_gpt(self, user_input):
    # Step 1: Process for tool calls
    tool_call_response = await self._get_gpt_response(
        user_input,
        tool_choice="auto"  # Force tool calls when appropriate
    )
    
    # Process any tool calls
    tool_execution_results = []
    if tool_call_response.tool_calls:
        for tool_call in tool_call_response.tool_calls:
            # Execute the tool call
            result = await self._execute_tool_call(tool_call)
            tool_execution_results.append(result)
    
    # Step 2: Get verbal response about the actions
    if tool_execution_results:
        verbal_response = await self._get_gpt_response(
            user_input,
            tool_choice="none",  # Force text-only response
            additional_context={"tool_execution_results": tool_execution_results}
        )
        
        # Emit the verbal response
        await self._emit_speech(verbal_response.content)
```

---

## 11 · Common anti‑patterns to repel

* **Bare `subscribe(...)`** – you'll forget to await, handler never fires.
* **Duplicate class names** – only the last one imported wins.
* **Emitting Pydantic objects raw** – downstream consumers explode.
* **Leaving tasks un‑cancelled** – hangs pytest & dev‑server shutdown.
* **Missing event_bus parameter** – service will fail to initialize.
* **Awaiting non-coroutine methods** – TypeError from awaiting emit() or on() methods.
* **Missing service registration** – 'NoneType' object has no attribute 'start' errors.
* **Improper stream processing** – missing tool calls or partial responses.
* **Untracked background tasks** – memory leaks and hanging processes.
* **Direct path references** – fails when run from different directories.
* **Invalid tool call validation** – malformed JSON arguments or incomplete tool calls.

---

## 12 · Reference links

* `ARCHITECTURE_STANDARDS.md` – §3 Async, §4 Config, §7 New‑Service checklist
* `CANTINA_OS_SYSTEM_ARCHITECTURE.md` – high‑level flow diagrams
* `service_template.py` – living source of truth
* `docs/working_logs/archive_logs/dj-r3x-working-dev-log_05-16-2025.md` - Recent fixes & lessons learned

---

### Finish line

When every box is ☑️, run `make test && make lint`.  If CI is green, you
are cleared for hyperspace.
