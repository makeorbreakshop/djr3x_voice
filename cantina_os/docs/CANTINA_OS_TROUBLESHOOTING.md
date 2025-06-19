# CantinaOS Troubleshooting Guide

This comprehensive troubleshooting guide documents common issues, debugging techniques, and resolution patterns for CantinaOS development. Based on 1 year of development experience and real-world issue resolution.

## 📋 Table of Contents

1. [Event Bus Debugging](#1-event-bus-debugging)
2. [Service Lifecycle Issues](#2-service-lifecycle-issues)
3. [Environment & Setup Problems](#3-environment--setup-problems)
4. [Hardware Integration Debugging](#4-hardware-integration-debugging)
5. [Validation & Serialization Issues](#5-validation--serialization-issues)
6. [Asyncio & Concurrency Problems](#6-asyncio--concurrency-problems)
7. [Logging & Silent Failure Investigation](#7-logging--silent-failure-investigation)
8. [Web Dashboard Integration Issues](#8-web-dashboard-integration-issues)
9. [Performance & Resource Management](#9-performance--resource-management)
10. [Testing & Validation Challenges](#10-testing--validation-challenges)

---

## 1. Event Bus Debugging

### 1.1 Silent Event Emission Failures

**Symptoms:**
- Commands sent but nothing happens
- "Works in CLI but not dashboard"
- Services appear started but don't respond

**Root Causes:**
```python
# WRONG: Direct event bus access
self._event_bus.emit(EventTopics.MUSIC_COMMAND, payload)

# CORRECT: Use BaseService.emit()
await self.emit(EventTopics.MUSIC_COMMAND, payload)
```

**Debugging Steps:**
1. Check if service uses `await self.emit()` instead of direct `_event_bus.emit()`
2. Verify event topic exists in EventTopics enum
3. Confirm receiving service is subscribed to the exact topic
4. Test with DebugService to monitor actual event emissions

### 1.2 Event Topic Name Mismatches

**Symptoms:**
- `AttributeError: module 'core.event_topics' has no attribute 'TOPIC_NAME'`
- Services failing to start with enum errors
- Events emitted but never received

**Resolution Pattern:**
```python
# WRONG: Hardcoded strings
self._event_bus.emit("MODE_TRANSITION_STARTED", data)

# CORRECT: EventTopics enum
from ..core.event_topics import EventTopics
self._event_bus.emit(EventTopics.MODE_TRANSITION_STARTED, data)
```

**Quick Diagnosis:**
```bash
# Search for hardcoded event strings
cd cantina_os
grep -r "emit.*['\"]" . --include="*.py" | grep -v EventTopics
```

### 1.3 Event Topic Subscription Mismatches

**Symptoms:**
- Service has handler method but never receives events
- Other services receive the same event successfully  
- Log shows "events emitted but handler never called"
- WebBridge missing dashboard updates despite backend working

**Root Cause:**
Service subscribes to wrong event topic - similar events with different names.

**Real-World Example:**
```python
# VoiceTab UI stuck because WebBridge subscribed to wrong event
# PROBLEM: WebBridge subscribed to SPEECH_SYNTHESIS_STARTED
await self.subscribe(EventTopics.SPEECH_SYNTHESIS_STARTED, self._handle_speech_started)

# REALITY: ElevenLabsService actually emits SPEECH_GENERATION_STARTED  
await self.emit(EventTopics.SPEECH_GENERATION_STARTED, payload)

# SOLUTION: Subscribe to the actual event being emitted
await self.subscribe(EventTopics.SPEECH_GENERATION_STARTED, self._handle_speech_started)
```

**Debugging Steps:**
1. **Find which service receives the event successfully:**
   ```bash
   # Search for log messages that prove event is being emitted
   grep -r "Speech started\|pattern to SPEAKING" logs/
   ```

2. **Check what events that service subscribes to:**
   ```bash
   # Find the working service and check its subscriptions
   grep -A20 "subscribe.*SPEECH" services/eye_light_controller_service.py
   ```

3. **Compare with broken service subscriptions:**
   ```bash
   # Check if broken service has different event subscriptions
   grep -A20 "subscribe.*SPEECH" services/web_bridge_service.py
   ```

4. **Verify actual event emission:**
   ```bash
   # Find where the event is actually emitted
   grep -r "emit.*SPEECH_GENERATION_STARTED" services/
   ```

**Resolution Pattern:**
```python
# TYPICAL PATTERN: Services subscribe to multiple related events
# Eye controller (WORKING) subscribes to both:
await self.subscribe(EventTopics.SPEECH_SYNTHESIS_STARTED, self._handle_speech_started)
await self.subscribe(EventTopics.SPEECH_GENERATION_STARTED, self._handle_speech_started)

# WebBridge (BROKEN) only subscribed to one:
await self.subscribe(EventTopics.SPEECH_SYNTHESIS_STARTED, self._handle_speech_started)

# FIX: Add missing subscription
await self.subscribe(EventTopics.SPEECH_GENERATION_STARTED, self._handle_speech_started)
```

**Prevention:**
- Always check what events are actually being emitted, not just what you expect
- Look at working services for subscription patterns  
- Use consistent event naming conventions across services
- Subscribe to all variants of related events when in doubt

### 1.4 Command Decorator Auto-Registration Conflicts

**Symptoms:**
- Dashboard/WebBridge commands fail with CLI validation errors
- "Command 'X' requires N arguments. Missing: field_name"
- Multiple services processing same MUSIC_COMMAND events
- Event flow works but CLI handlers interfere

**Root Cause Pattern:**
Service has `@compound_command` decorators that auto-register CLI handlers for events that should be handled by other services.

**Real-World Example (Music System):**
```python
# PROBLEM: MusicControllerService has decorators
class MusicControllerService(BaseService):
    @compound_command("play music")  # Auto-registers for MUSIC_COMMAND events
    @validate_compound_command(min_args=1, required_args=["track_name"])
    async def handle_play_music(self, payload: dict) -> None:
        # Expects CLI args format, not WebBridge event format
        
# MEANWHILE: WebBridge emits structured events
event_payload = {
    "track_name": "Song Name",
    "action": "play",
    "source": "web_dashboard"
}
await self.emit(EventTopics.MUSIC_COMMAND, event_payload)

# RESULT: Both services handle same event with different expectations
```

**Event Flow Conflict:**
```
WebBridge → MUSIC_COMMAND event → MusicControllerService CLI handler (fails validation)
                                → MusicSourceManagerService (correct handler)
```

**Log Evidence:**
```
Registered command 'play music' to service 'MusicController' with topic 'EventTopics.MUSIC_COMMAND'
🎵 DEBUG: Final event_payload: {'track_name': 'Song Name', 'action': 'play'}
ERROR Command 'play music' requires 1 arguments. Missing: track_name
```

**Solution - Remove Conflicting Decorators:**
```python
# BEFORE (causing conflicts):
@compound_command("play music")
@validate_compound_command(min_args=1, required_args=["track_name"])
async def handle_play_music(self, payload: dict) -> None:

# AFTER (direct method calls only):
@command_error_handler
async def handle_play_music(self, payload: dict) -> None:
    # Method available for direct calls from providers
    # No automatic event subscription
```

**Correct Architecture Pattern:**
```
WebBridge → MUSIC_COMMAND event → MusicSourceManagerService → LocalMusicProvider → music_controller.handle_play_music() (direct call)
```

**Diagnostic Steps:**
1. **Check for duplicate command registrations:**
   ```bash
   grep -n "Registered command.*MUSIC_COMMAND" logs/session.log
   ```

2. **Verify single event subscription:**
   ```bash
   # Should only see MusicSourceManagerService, not MusicControllerService
   grep -n "subscribe.*MUSIC_COMMAND" cantina_os/services/
   ```

3. **Test event flow:**
   ```bash
   # Working logs show direct method calls, not CLI validation errors
   grep -A5 -B5 "Playing music track" logs/session.log
   ```

**Prevention:**
- Use `@compound_command` decorators only for services that should handle CLI commands directly
- Provider services should use direct method calls, not event re-emission
- Maintain clear separation: one service per event topic for command processing
- Document intended event flow in service architecture

### 1.5 Event Subscription Race Conditions

**Symptoms:**
- Services start successfully but miss early events
- Intermittent event handling failures
- "MEMORY_VALUE" errors during initialization

**Root Cause:**
```python
# WRONG: Service marked started before subscriptions complete
async def _start(self):
    asyncio.create_task(self._setup_subscriptions())  # Not awaited!
    await self._emit_status(ServiceStatus.RUNNING)

# CORRECT: Await subscription completion
async def _start(self):
    await self._setup_subscriptions()
    await self._emit_status(ServiceStatus.RUNNING)
```

**Advanced Diagnosis:**
```python
# Add to _start() method for debugging
async def _setup_subscriptions(self):
    subscription_tasks = [
        self.subscribe(EventTopics.TOPIC_1, self.handler_1),
        # ... more subscriptions
    ]
    await asyncio.gather(*subscription_tasks)
    self.logger.info(f"All {len(subscription_tasks)} subscriptions established")
```

### 1.6 Pydantic Model Event Payload Issues

**Symptoms:**
- Events emitted but cause validation errors in receivers
- "Extra fields not permitted" errors
- Silent event processing failures

**Root Cause & Solution:**
```python
# WRONG: Emitting Pydantic models directly
payload = TrackDataPayload(title="Song", artist="Artist")
await self.emit(EventTopics.TRACK_SELECTED, payload)

# CORRECT: Convert to dict for event system
payload = TrackDataPayload(title="Song", artist="Artist")
await self.emit(EventTopics.TRACK_SELECTED, payload.model_dump())
```

---

## 2. Service Lifecycle Issues

### 2.1 Service Startup Race Conditions

**Symptoms:**
- Services appear "RUNNING" but don't respond to events
- BrainService failing with "MEMORY_VALUE" error
- Intermittent service communication failures

**Root Cause Pattern:**
Services marked as started before critical initialization completes.

**Resolution:**
```python
class ExampleService(BaseService):
    async def _start(self) -> None:
        # CRITICAL: Complete ALL initialization before marking as started
        await self._setup_subscriptions()
        await self._initialize_resources()
        await self._verify_dependencies()
        
        # Only now mark as running
        await self._emit_status(ServiceStatus.RUNNING, "Service fully initialized")
```

**Verification Script:**
```python
# Add to service for startup debugging
async def _verify_startup_complete(self):
    """Verify service is truly ready for operation."""
    test_payload = {"test": True, "timestamp": datetime.now()}
    
    try:
        # Test event emission
        await self.emit(EventTopics.SERVICE_STATUS_UPDATE, test_payload)
        
        # Test subscription response
        self._startup_verified = True
        self.logger.info("Service startup verification: PASSED")
        
    except Exception as e:
        self.logger.error(f"Service startup verification: FAILED - {e}")
        raise
```

### 2.2 Service Registration Missing from main.py

**Symptoms:**
- Service class exists but never starts
- "Service not found in class map" errors
- CommandDispatcherService missing errors

**Quick Fix Checklist:**
```python
# In cantina_os/main.py

# 1. Import the service class
from .services.your_service import YourService

# 2. Add to SERVICE_CLASS_MAP
SERVICE_CLASS_MAP = {
    # ... existing services
    "your_service": YourService,
}

# 3. Add to service_order (if order matters)
service_order = [
    # ... other services
    "your_service",
]
```

### 2.3 Blocking I/O Operations in Service Startup

**Symptoms:**
- Service startup hangs indefinitely
- Other services wait for hung service
- Architecture Standards violations

**Root Cause & Solution:**
```python
# WRONG: Blocking I/O in _start()
async def _start(self) -> None:
    await self._load_music_library()  # Blocks startup!
    
# CORRECT: Background task for heavy operations
async def _start(self) -> None:
    asyncio.create_task(self._load_music_library())
    await self._emit_status(ServiceStatus.RUNNING)

async def _load_music_library(self) -> None:
    """Load library in background without blocking startup."""
    try:
        # Heavy I/O operations here
        tracks = await self._parse_music_files()
        await self.emit(EventTopics.MUSIC_LIBRARY_UPDATED, tracks)
        
    except Exception as e:
        self.logger.error(f"Library loading failed: {e}")
```

---

## 3. Environment & Setup Problems

### 3.1 Python Interpreter Mismatches

**Symptoms:**
- "No module named 'pydub'" despite being installed
- Dashboard works via CLI but fails when launched by scripts
- Import errors for installed packages

**Root Cause:**
Launch scripts using system Python instead of virtual environment Python.

**Diagnostic Commands:**
```bash
# Check which Python is being used
which python
which python3

# Check if virtual environment is activated
echo $VIRTUAL_ENV

# Verify package installation location
pip show pydub
```

**Resolution Pattern:**
```bash
# In launch scripts (start-dashboard.sh, etc.)

# WRONG: Uses system Python
python -m cantina_os.main

# CORRECT: Explicit venv path
../venv/bin/python -m cantina_os.main

# Also fix pip commands
../venv/bin/pip install -r requirements.txt
```

### 3.2 SDK Version Compatibility Issues

**Symptoms:**
- "unexpected 'proxies' argument" errors
- Import errors after SDK updates
- API method signature mismatches

**Debugging Pattern:**
```python
# Check SDK versions
import deepgram
print(f"Deepgram SDK: {deepgram.__version__}")

import openai
print(f"OpenAI SDK: {openai.__version__}")
```

**Resolution Strategy:**
1. Check migration guides when upgrading major versions
2. Pin critical SDK versions in requirements.txt
3. Test all SDK-dependent functionality after updates

**Emergency Compatibility Fix:**
```python
# Temporary monkey patching for compatibility
import httpx

original_init = httpx.Client.__init__

def patched_init(self, *args, **kwargs):
    kwargs.pop('proxies', None)  # Remove problematic argument
    return original_init(self, *args, **kwargs)

httpx.Client.__init__ = patched_init
```

### 3.3 Import Path Standardization Failures

**Symptoms:**
- `ModuleNotFoundError` after refactoring
- Circular import errors
- "EventTopics import errors in core framework"

**Mass Import Path Fix:**
```bash
# Search and replace pattern for import standardization
cd cantina_os

# Find problematic imports
grep -r "from.*event_topics import" . --include="*.py"

# Fix with consistent pattern
find . -name "*.py" -exec sed -i 's/from.*event_topics import/from ..core.event_topics import/g' {} \;
```

---

## 4. Hardware Integration Debugging

### 4.1 Arduino Communication Protocol Failures

**Symptoms:**
- "Invalid JSON" warnings from Arduino
- Commands rejected by hardware
- Communication timeouts

**Debugging Steps:**
```python
# Test Arduino communication directly
async def test_arduino_connection(self):
    """Test Arduino communication with simple commands."""
    test_commands = ['I', 'L', 'T', 'S']  # Simple single-char commands
    
    for cmd in test_commands:
        try:
            self._serial.write(f"{cmd}\n".encode())  # Always add newline
            await asyncio.sleep(0.1)
            
            response = self._serial.readline().decode().strip()
            self.logger.info(f"Command '{cmd}' -> Response: '{response}'")
            
        except Exception as e:
            self.logger.error(f"Arduino test failed for '{cmd}': {e}")
```

**Protocol Optimization:**
```cpp
// Arduino sketch: Keep commands simple
void processCommand(char cmd) {
    switch(cmd) {
        case 'I': setIdle(); break;
        case 'L': setListening(); break;
        case 'T': setThinking(); break;
        case 'S': setSpeaking(); break;
        default: 
            Serial.println("ERR"); 
            return;
    }
    Serial.println("OK");  // Always acknowledge
}
```

### 4.2 Thread-to-Asyncio Bridge Issues

**Symptoms:**
- "There is no current event loop in thread" errors
- "Task got Future attached to different loop" errors
- Hardware callbacks not reaching event system

**Correct Bridge Pattern:**
```python
import asyncio
from threading import Thread

class HardwareService(BaseService):
    def __init__(self, event_bus, config, logger=None):
        super().__init__(event_bus, config, logger)
        self._event_loop = None
        
    async def _start(self) -> None:
        # Capture the running event loop
        self._event_loop = asyncio.get_running_loop()
        
        # Start hardware thread
        self._hardware_thread = Thread(target=self._hardware_loop)
        self._hardware_thread.start()
    
    def _hardware_callback(self, data):
        """Called from hardware thread - must bridge to async."""
        if self._event_loop and not self._event_loop.is_closed():
            asyncio.run_coroutine_threadsafe(
                self._process_hardware_data(data),
                self._event_loop
            )
    
    async def _process_hardware_data(self, data):
        """Process hardware data in async context."""
        await self.emit(EventTopics.HARDWARE_DATA_RECEIVED, data)
```

### 4.3 Audio Pipeline Threading Issues

**Symptoms:**
- Audio capture not reaching transcription service
- Event subscriptions appearing successful but handlers not called
- Cross-platform audio compatibility issues

**Platform-Specific Audio Handling:**
```python
import platform

async def _setup_audio_backend(self):
    """Setup platform-specific audio backend."""
    system = platform.system()
    
    if system == "Darwin":  # macOS
        self._audio_cmd = ["afplay"]
    elif system == "Linux":
        self._audio_cmd = ["aplay"]
    else:
        self.logger.warning(f"Unsupported platform: {system}")
        self._audio_cmd = ["vlc", "--intf", "dummy"]
```

---

## 5. Validation & Serialization Issues

### 5.1 JSON Serialization DateTime Errors

**Symptoms:**
- "Object of type datetime is not JSON serializable"
- Socket.IO emit failures
- Command acknowledgment failures

**Root Cause & Solution:**
```python
# WRONG: Direct dict() serialization
response = BaseWebResponse.success_response(message="Done")
await self._sio.emit("result", response.dict(), room=sid)

# CORRECT: JSON-mode serialization
response = BaseWebResponse.success_response(message="Done")
await self._sio.emit("result", response.model_dump(mode='json'), room=sid)
```

**Validation Debug Helper:**
```python
def debug_serialization(obj, logger):
    """Debug Pydantic model serialization issues."""
    try:
        # Test different serialization modes
        dict_result = obj.dict()
        logger.info(f"dict() serialization: SUCCESS")
        
        json_result = obj.model_dump(mode='json')
        logger.info(f"model_dump(mode='json'): SUCCESS")
        
        import json
        json_str = json.dumps(json_result)
        logger.info(f"JSON string conversion: SUCCESS")
        
    except Exception as e:
        logger.error(f"Serialization failed: {e}")
        logger.error(f"Object type: {type(obj)}")
        logger.error(f"Object fields: {obj.__dict__ if hasattr(obj, '__dict__') else 'No __dict__'}")
```

### 5.2 Pydantic Field Name Mismatches

**Symptoms:**
- Events emitted but receiving service gets wrong field names
- Validation errors for "unknown fields"
- Silent data transformation failures

**Field Mapping Solution:**
```python
class WebBridgeService(BaseService):
    def _map_status_fields(self, event_type: str, payload: dict) -> dict:
        """Map CantinaOS fields to web-compatible format."""
        
        field_mappings = {
            "service": {
                "service": "service_name",
                "status": "online",
                "RUNNING": "online",
                "STOPPED": "offline",
            },
            "music": {
                "track_name": "title",
                "file_path": "filename",
            }
        }
        
        mapping = field_mappings.get(event_type, {})
        mapped_payload = {}
        
        for key, value in payload.items():
            # Map field name if needed
            new_key = mapping.get(key, key)
            # Map field value if needed
            new_value = mapping.get(value, value) if isinstance(value, str) else value
            mapped_payload[new_key] = new_value
            
        return mapped_payload
```

### 5.3 Socket.IO Handler Signature Mismatches

**Symptoms:**
- `TypeError: missing 1 required positional argument: 'self'`
- WebSocket disconnections after command attempts
- Validation decorator failures

**Correct Handler Pattern:**
```python
# WRONG: Nested function signature
@validate_socketio_command("music_command")
async def music_command(sid, validated_command):
    pass

# CORRECT: Instance method signature
@validate_socketio_command("music_command")
async def _handle_music_command(self, sid, validated_command):
    """Handle music commands with validation."""
    event_payload = validated_command.to_cantina_event()
    await self.emit(EventTopics.MUSIC_COMMAND, event_payload)
```

---

## 6. Asyncio & Concurrency Problems

### 6.1 Incorrect Async/Await Patterns

**Symptoms:**
- "coroutine was never awaited" warnings
- "Task was never retrieved" errors
- Services hanging during shutdown

**Common Async Violations:**
```python
# WRONG: Not awaiting coroutines
result = self.async_method()  # Returns coroutine, not result

# WRONG: Awaiting non-coroutines
result = await self.sync_method()  # TypeError

# WRONG: Using await on pyee emit()
await self._event_bus.emit(topic, data)  # emit() is NOT async

# CORRECT patterns:
result = await self.async_method()
result = self.sync_method()
self._event_bus.emit(topic, data)  # No await needed
```

**Async Method Verification:**
```python
import inspect

def verify_async_usage(self):
    """Debug helper to verify async method usage."""
    method = getattr(self, 'method_name')
    
    if inspect.iscoroutinefunction(method):
        self.logger.info(f"{method.__name__} is async - use await")
    else:
        self.logger.info(f"{method.__name__} is sync - no await needed")
```

### 6.2 Event Loop Context Issues

**Symptoms:**
- "RuntimeError: no running event loop"
- "Task got Future attached to different loop"
- Cross-thread async call failures

**Event Loop Management:**
```python
class ServiceWithThreads(BaseService):
    def __init__(self, event_bus, config, logger=None):
        super().__init__(event_bus, config, logger)
        self._event_loop = None
        
    async def _start(self) -> None:
        # Capture event loop for thread bridge
        self._event_loop = asyncio.get_running_loop()
        
        # Verify event loop is accessible
        loop_id = id(self._event_loop)
        self.logger.info(f"Captured event loop: {loop_id}")
        
    def _thread_callback(self, data):
        """Called from another thread."""
        if self._event_loop and not self._event_loop.is_closed():
            future = asyncio.run_coroutine_threadsafe(
                self._async_handler(data),
                self._event_loop
            )
            # Optional: Wait for completion with timeout
            try:
                result = future.result(timeout=5.0)
            except asyncio.TimeoutError:
                self.logger.error("Cross-thread async call timed out")
```

### 6.3 Resource Cleanup Race Conditions

**Symptoms:**
- Memory leaks during service shutdown
- "No wrapper found for handler" warnings
- Resources not properly released

**Proper Cleanup Pattern:**
```python
class ExampleService(BaseService):
    def __init__(self, event_bus, config, logger=None):
        super().__init__(event_bus, config, logger)
        self._background_tasks = set()
        self._cleanup_complete = asyncio.Event()
        
    async def _start(self) -> None:
        # Track background tasks
        task = asyncio.create_task(self._background_worker())
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        
    async def _stop(self) -> None:
        # Cancel all background tasks
        for task in self._background_tasks:
            task.cancel()
            
        # Wait for cancellation with timeout
        if self._background_tasks:
            await asyncio.wait(
                self._background_tasks, 
                timeout=5.0,
                return_when=asyncio.ALL_COMPLETED
            )
            
        # Clean up resources
        await self._cleanup_resources()
        self._cleanup_complete.set()
        
    async def _cleanup_resources(self):
        """Override in subclasses for specific cleanup."""
        pass
```

---

## 7. Logging & Silent Failure Investigation

### 7.1 Logging Feedback Loops

**Symptoms:**
- Massive log files (100MB+)
- System performance degradation
- Recursive logging causing crashes

**Prevention Pattern:**
```python
class LoggingService(BaseService):
    def _setup_logger_filters(self):
        """Prevent recursive logging in centralized logging service."""
        
        # Self-exclusion: Don't log your own operations
        self_exclusions = [
            "cantina_os.services.logging_service",
            "cantina_os.services.web_bridge_service",  # WebSocket logging
        ]
        
        for exclusion in self_exclusions:
            logger = logging.getLogger(exclusion)
            logger.propagate = False  # Prevent propagation to root
            
        # HTTP client exclusions
        http_loggers = [
            "httpx",
            "httpcore", 
            "socketio",
            "engineio",
        ]
        
        for http_logger in http_loggers:
            logging.getLogger(http_logger).setLevel(logging.WARNING)
```

### 7.2 Silent Service Failure Investigation

**Symptoms:**
- Services appear "online" but don't respond
- Commands sent but no visible effect
- Missing diagnostic information

**Comprehensive Service Health Check:**
```python
async def diagnose_service_health(service_name: str, event_bus):
    """Comprehensive service health diagnostics."""
    
    print(f"\n=== Diagnosing {service_name} ===")
    
    # 1. Check service registration
    if service_name not in SERVICE_CLASS_MAP:
        print(f"❌ Service '{service_name}' not in SERVICE_CLASS_MAP")
        return
    
    # 2. Check service instance
    service_instance = None
    for service in active_services:
        if service.__class__.__name__.lower().replace('service', '') == service_name:
            service_instance = service
            break
    
    if not service_instance:
        print(f"❌ Service '{service_name}' instance not found in active services")
        return
    
    # 3. Check service status
    status = getattr(service_instance, '_status', 'UNKNOWN')
    print(f"✓ Service status: {status}")
    
    # 4. Check event subscriptions
    subscriptions = getattr(service_instance, '_subscriptions', {})
    print(f"✓ Event subscriptions: {len(subscriptions)} topics")
    for topic in subscriptions.keys():
        print(f"  - {topic}")
    
    # 5. Test event emission
    test_payload = {"test": True, "timestamp": datetime.now().isoformat()}
    try:
        await service_instance.emit(EventTopics.SERVICE_STATUS_UPDATE, test_payload)
        print("✓ Event emission: SUCCESS")
    except Exception as e:
        print(f"❌ Event emission: FAILED - {e}")
    
    # 6. Memory usage
    import psutil
    process = psutil.Process()
    memory_mb = process.memory_info().rss / 1024 / 1024
    print(f"✓ Memory usage: {memory_mb:.1f} MB")
```

### 7.3 Event Flow Tracing

**Debugging Events End-to-End:**
```python
class EventTracer:
    """Debug helper for tracing event flow through the system."""
    
    def __init__(self, event_bus, target_topics=None):
        self.event_bus = event_bus
        self.target_topics = target_topics or []
        self.trace_log = []
        
    def start_tracing(self):
        """Start tracing events."""
        original_emit = self.event_bus.emit
        
        def traced_emit(topic, *args, **kwargs):
            if not self.target_topics or topic in self.target_topics:
                self.trace_log.append({
                    "timestamp": datetime.now().isoformat(),
                    "topic": topic,
                    "args": str(args)[:200],  # Truncate for readability
                    "kwargs": str(kwargs)[:200],
                })
            return original_emit(topic, *args, **kwargs)
        
        self.event_bus.emit = traced_emit
        
    def print_trace(self):
        """Print collected event trace."""
        print("\n=== Event Flow Trace ===")
        for entry in self.trace_log:
            print(f"{entry['timestamp']} | {entry['topic']}")
            if entry['args'] != "()":
                print(f"  Args: {entry['args']}")
            if entry['kwargs'] != "{}":
                print(f"  Kwargs: {entry['kwargs']}")
```

---

## 8. Web Dashboard Integration Issues

### 8.1 Payload Unwrapping Mismatches

**Symptoms:**
- Frontend receives events but data is undefined
- Dashboard shows "No track selected" despite backend activity
- Event data nested incorrectly

**WebBridge Standardized Payload Format:**
```typescript
// Frontend must handle this format:
{
  "topic": "MUSIC_PLAYBACK_STARTED",
  "data": { ...actual_payload... },
  "timestamp": "2025-06-16T...",
  "validated": true
}
```

**Frontend Unwrapping Pattern:**
```typescript
// Add to all Socket.IO event handlers
const unwrap = (raw: any) => (raw && raw.data ? raw.data : raw);

// Usage in event handlers
socket.on('music_status', (raw) => {
  const data = unwrap(raw);
  // Now use data.track_name, data.duration, etc.
});
```

### 8.2 React Hydration Errors

**Symptoms:**
- "Extra attributes from the server" warnings
- UI components not updating
- Application-wide rendering freeze

**Hydration-Safe Component Pattern:**
```typescript
import { useState, useEffect } from 'react';

const HydrationSafeComponent = () => {
  const [isClient, setIsClient] = useState(false);
  const [dynamicValue, setDynamicValue] = useState(0);
  
  useEffect(() => {
    setIsClient(true);
    setDynamicValue(Math.random()); // Safe after hydration
  }, []);
  
  return (
    <div>
      {/* Static content renders on server */}
      <h1>Music Player</h1>
      
      {/* Dynamic content only renders on client */}
      {isClient && (
        <div style={{ width: `${dynamicValue * 100}%` }}>
          Dynamic Progress Bar
        </div>
      )}
    </div>
  );
};
```

### 8.3 Command Integration Pattern Mismatch

**Symptoms:**
- Dashboard commands fail with validation errors
- CLI commands work but dashboard doesn't
- Complex Pydantic validation failures

**Simple Command Integration (Recommended):**
```typescript
// FRONTEND: Simple command emission
socket.emit('command', { command: 'dj start' });
socket.emit('command', { command: 'play music Cantina Band' });

// BACKEND: Simple command handler
@self._sio.event
async def command(sid, data):
    command_text = data.get("command", "").strip()
    
    # Parse like CLI does
    parts = command_text.split()
    command = parts[0] if parts else ""
    args = parts[1:] if len(parts) > 1 else []
    
    # Route through CLI command system
    self._event_bus.emit(EventTopics.CLI_COMMAND, {
        "command": command,
        "args": args,
        "raw_input": command_text,
        "source": "dashboard",
        "sid": sid
    })
```

---

## 9. Performance & Resource Management

### 9.1 Memory Leak Detection

**Symptoms:**
- Gradually increasing memory usage
- "No wrapper found for handler" warnings
- Event handler accumulation

**Memory Leak Diagnostic:**
```python
import psutil
import gc

class MemoryMonitor:
    def __init__(self, service_name):
        self.service_name = service_name
        self.baseline_memory = None
        
    def start_monitoring(self):
        """Establish memory baseline."""
        process = psutil.Process()
        self.baseline_memory = process.memory_info().rss / 1024 / 1024
        print(f"Memory baseline for {self.service_name}: {self.baseline_memory:.1f} MB")
        
    def check_memory(self, operation_name):
        """Check memory after operation."""
        process = psutil.Process()
        current_memory = process.memory_info().rss / 1024 / 1024
        increase = current_memory - self.baseline_memory
        
        print(f"Memory after {operation_name}: {current_memory:.1f} MB (+{increase:.1f} MB)")
        
        if increase > 50:  # Alert if >50MB increase
            print(f"⚠️ Potential memory leak detected!")
            
            # Force garbage collection
            collected = gc.collect()
            print(f"Garbage collected: {collected} objects")
            
            # Check again
            new_memory = psutil.Process().memory_info().rss / 1024 / 1024
            print(f"Memory after GC: {new_memory:.1f} MB")
```

### 9.2 Event Handler Cleanup Verification

**Proper Event Handler Cleanup:**
```python
class EventHandlerTracker(BaseService):
    def __init__(self, event_bus, config, logger=None):
        super().__init__(event_bus, config, logger)
        self._handler_count = {}
        
    async def subscribe(self, topic, handler):
        """Track subscription count."""
        result = await super().subscribe(topic, handler)
        self._handler_count[topic] = self._handler_count.get(topic, 0) + 1
        self.logger.debug(f"Subscribed to {topic} (total: {self._handler_count[topic]})")
        return result
        
    async def _stop(self):
        """Verify all handlers are cleaned up."""
        for topic, count in self._handler_count.items():
            remaining = len(self._event_bus.listeners(topic))
            if remaining > 0:
                self.logger.warning(f"Cleanup incomplete: {remaining} handlers remain for {topic}")
        
        await super()._stop()
```

### 9.3 VLC Resource Management

**Symptoms:**
- "AudioObjectAddPropertyListener failed" errors on macOS
- VLC processes not terminating
- Audio resource conflicts

**Proper VLC Lifecycle Management:**
```python
class VLCManager:
    def __init__(self):
        self._vlc_instance = None
        self._vlc_players = set()
        
    def create_vlc_instance(self):
        """Create VLC instance with proper configuration."""
        import vlc
        
        vlc_args = [
            "--quiet",           # Reduce logging
            "--no-video",        # Audio only
            "--verbose", "0",    # Minimal verbosity
            "--intf", "dummy",   # No interface
        ]
        
        self._vlc_instance = vlc.Instance(vlc_args)
        return self._vlc_instance
        
    async def cleanup_vlc_resources(self):
        """Proper VLC cleanup to prevent resource leaks."""
        # Stop all players
        for player in self._vlc_players.copy():
            try:
                player.stop()
                player.release()
                self._vlc_players.discard(player)
            except Exception as e:
                logger.warning(f"VLC player cleanup error: {e}")
        
        # Release VLC instance
        if self._vlc_instance:
            try:
                self._vlc_instance.release()
                self._vlc_instance = None
            except Exception as e:
                logger.warning(f"VLC instance cleanup error: {e}")
        
        # Allow time for system cleanup
        await asyncio.sleep(0.5)
```

---

## 10. Testing & Validation Challenges

### 10.1 Event-Based Testing Patterns

**Testing Services with Event Dependencies:**
```python
import pytest
from unittest.mock import AsyncMock, MagicMock

class TestMusicController:
    @pytest.fixture
    async def mock_event_bus(self):
        """Create mock event bus for testing."""
        event_bus = MagicMock()
        event_bus.emit = AsyncMock()
        event_bus.subscribe = AsyncMock()
        return event_bus
    
    @pytest.fixture
    async def music_service(self, mock_event_bus):
        """Create service instance for testing."""
        config = {"music_directory": "/test/music"}
        service = MusicControllerService(mock_event_bus, config)
        return service
    
    @pytest.mark.asyncio
    async def test_service_startup_sequence(self, music_service, mock_event_bus):
        """Test proper service startup sequence."""
        
        # Start service
        await music_service._start()
        
        # Verify subscriptions were established
        assert mock_event_bus.subscribe.call_count > 0
        
        # Verify status emission
        mock_event_bus.emit.assert_called_with(
            EventTopics.SERVICE_STATUS_UPDATE,
            {
                "service_name": "MusicControllerService",
                "status": "RUNNING",
                "message": "Service started successfully"
            }
        )
```

### 10.2 Mock Hardware for Testing

**Arduino Testing Without Hardware:**
```python
class MockArduinoSerial:
    """Mock Arduino serial interface for testing."""
    
    def __init__(self):
        self.write_buffer = []
        self.read_buffer = ["OK\n"] * 10  # Pre-fill responses
        
    def write(self, data):
        """Mock serial write."""
        command = data.decode().strip()
        self.write_buffer.append(command)
        
        # Simulate Arduino responses
        if command in ['I', 'L', 'T', 'S']:
            self.read_buffer.append("OK\n")
        else:
            self.read_buffer.append("ERR\n")
    
    def readline(self):
        """Mock serial read."""
        if self.read_buffer:
            return self.read_buffer.pop(0).encode()
        return b"TIMEOUT\n"

@pytest.fixture
def mock_arduino(monkeypatch):
    """Replace real Arduino with mock for testing."""
    mock_serial = MockArduinoSerial()
    
    def mock_serial_connect(*args, **kwargs):
        return mock_serial
    
    monkeypatch.setattr("serial.Serial", mock_serial_connect)
    return mock_serial
```

### 10.3 Integration Testing Patterns

**End-to-End Event Flow Testing:**
```python
class TestEventFlowIntegration:
    @pytest.mark.asyncio
    async def test_complete_music_command_flow(self):
        """Test complete flow: CLI -> CommandDispatcher -> MusicController -> WebBridge."""
        
        # Setup real event bus
        event_bus = AsyncIOEventEmitter()
        
        # Create services
        command_dispatcher = CommandDispatcherService(event_bus, {})
        music_controller = MusicControllerService(event_bus, {"music_directory": "/test"})
        web_bridge = WebBridgeService(event_bus, {"port": 8001})
        
        # Start services
        await command_dispatcher._start()
        await music_controller._start()
        await web_bridge._start()
        
        # Capture web bridge emissions
        emitted_events = []
        
        original_emit = web_bridge.emit
        async def capture_emit(topic, payload):
            emitted_events.append((topic, payload))
            return await original_emit(topic, payload)
        web_bridge.emit = capture_emit
        
        # Send CLI command
        cli_payload = {
            "command": "play",
            "args": ["music", "test_song.mp3"],
            "raw_input": "play music test_song.mp3",
            "source": "test"
        }
        
        event_bus.emit(EventTopics.CLI_COMMAND, cli_payload)
        
        # Wait for event processing
        await asyncio.sleep(0.1)
        
        # Verify event flow
        assert len(emitted_events) > 0
        
        # Check that music status was emitted
        music_events = [e for e in emitted_events if "MUSIC" in str(e[0])]
        assert len(music_events) > 0
        
        # Cleanup
        await command_dispatcher._stop()
        await music_controller._stop()
        await web_bridge._stop()
```

---

## Quick Reference Debugging Commands

### Event System Debug
```bash
# Search for hardcoded event strings
grep -r "emit.*['\"]" cantina_os --include="*.py" | grep -v EventTopics

# Find services missing from main.py
grep -r "class.*Service" cantina_os/services --include="*.py" | grep -v "__"
```

### Service Health Check
```python
# Add to any service for quick health check
async def debug_service_health(self):
    """Quick service health diagnostic."""
    print(f"Service: {self.__class__.__name__}")
    print(f"Status: {getattr(self, '_status', 'UNKNOWN')}")
    print(f"Subscriptions: {len(getattr(self, '_subscriptions', {}))}")
    print(f"Event loop: {id(asyncio.get_running_loop())}")
```

### Memory Usage Check
```python
import psutil
process = psutil.Process()
memory_mb = process.memory_info().rss / 1024 / 1024
print(f"Memory usage: {memory_mb:.1f} MB")
```

### VLC Process Check
```bash
# Check for orphaned VLC processes
ps aux | grep vlc | grep -v grep

# Kill orphaned VLC processes
pkill -f vlc
```

---

## Emergency Debugging Procedures

### Service Won't Start
1. Check import paths in main.py
2. Verify service in SERVICE_CLASS_MAP
3. Check _start() method for blocking operations
4. Test event subscription syntax

### Events Not Received
1. Verify EventTopics enum usage
2. Check subscription timing (before or after _start)
3. Test with direct emit/subscribe
4. Verify service is actually started

### WebSocket/Dashboard Issues
1. Check payload unwrapping pattern
2. Verify JSON serialization (.model_dump(mode='json'))
3. Test with simple command handler
4. Check for React hydration errors

### Memory/Performance Issues
1. Monitor memory usage during operations
2. Check for VLC resource leaks
3. Verify event handler cleanup
4. Test garbage collection effectiveness

---

This troubleshooting guide represents 2+ years of real-world CantinaOS development experience. When encountering issues, start with the most common patterns in your relevant section, then work through the diagnostic procedures systematically.

**Remember:** Most "silent failures" are actually event routing issues. When debugging, always verify the complete event flow from emission to reception.