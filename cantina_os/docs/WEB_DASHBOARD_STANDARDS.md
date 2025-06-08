# CantinaOS Web Dashboard Integration Standards

This document defines the standards and requirements for integrating web-based interfaces with the CantinaOS event-driven architecture. These standards ensure proper event flow, maintain system integrity, and provide a consistent user experience.

## 1. Overview

The CantinaOS web dashboard integration must follow strict architectural patterns to maintain the integrity of the event-driven system. Web interfaces are considered external clients that must properly integrate with the core CantinaOS event bus topology and service architecture.

### 1.1 Key Principles

- **Event Bus Compliance**: All web interactions must flow through the established CantinaOS event bus topology
- **Event Topic Translation**: Web-specific events must be translated to proper CantinaOS event topics
- **Service Architecture Respect**: Web integration must not bypass or violate core service patterns
- **State Synchronization**: Dashboard state must accurately reflect actual CantinaOS system state

## 2. Web Integration Architecture

### 2.1 Required Components

All web dashboard implementations must include:

1. **WebBridge Service**: A proper CantinaOS service that inherits from `BaseService`
2. **Event Translation Layer**: Converts web events to CantinaOS event topics
3. **State Synchronization System**: Maintains real-time sync between web and CantinaOS
4. **Command Validation**: Ensures web commands follow CantinaOS standards

### 2.2 Integration Flow

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Web UI    │────▶│ WebSocket/  │────▶│  WebBridge  │────▶│  CantinaOS  │
│ (Dashboard) │     │  HTTP API   │     │   Service   │     │ Event Bus   │
└─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘
                                               │                     │
                                               ▼                     ▼
                                        ┌─────────────┐     ┌─────────────┐
                                        │   Event     │     │   Target    │
                                        │ Translation │     │  Services   │
                                        └─────────────┘     └─────────────┘
```

## 3. Event System Integration Standards

### 3.1 Critical Event Flow Requirements

**MANDATORY**: All web dashboard interactions must follow the established CantinaOS Event Bus Topology as defined in `CANTINA_OS_SYSTEM_ARCHITECTURE.md`.

#### System Mode Changes

**Required Flow**:
```
Web Dashboard → SYSTEM_SET_MODE_REQUEST → YodaModeManagerService → SYSTEM_MODE_CHANGE → All Services
```

**Prohibited Flows**:
```
❌ Web Dashboard → generic system_command → (nowhere)
❌ Web Dashboard → direct service calls
❌ Web Dashboard → bypassing YodaModeManagerService
```

#### Voice Commands

**Required Flow**:
```
Web Dashboard → SYSTEM_SET_MODE_REQUEST (INTERACTIVE) → YodaModeManagerService → VOICE_LISTENING_STARTED → DeepgramDirectMicService
```

**Prohibited Flows**:
```
❌ Web Dashboard → MIC_RECORDING_START (bypasses engagement system)
❌ Web Dashboard → direct microphone control
```

### 3.2 Event Topic Translation Requirements

The WebBridge service MUST translate web events to proper CantinaOS event topics:

```python
# REQUIRED: Event topic translation patterns
def _translate_web_event_to_cantina_topic(self, web_event: dict) -> Tuple[str, dict]:
    """Translate web events to proper CantinaOS event topics and payloads."""
    
    # System mode changes
    if web_event.get("action") == "set_mode":
        return (
            EventTopics.SYSTEM_SET_MODE_REQUEST,
            {"mode": web_event["mode"].upper(), "source": "web_dashboard"}
        )
    
    # DJ Mode commands
    elif web_event.get("action") == "dj_start":
        return (
            EventTopics.DJ_COMMAND,
            {"command": "dj start", "source": "web_dashboard"}
        )
    
    # Music commands - must go through proper command flow
    elif web_event.get("action") == "play_music":
        return (
            EventTopics.CLI_COMMAND,
            {
                "command": "play music",
                "args": [web_event.get("track_name", "")],
                "source": "web_dashboard"
            }
        )
    
    else:
        raise ValueError(f"Unknown web event action: {web_event.get('action')}")
```

### 3.3 Event Topic Registration

WebBridge services must subscribe to the correct CantinaOS event topics:

```python
async def _setup_subscriptions(self) -> None:
    """Subscribe to CantinaOS events for web dashboard updates."""
    await asyncio.gather(
        # System mode events
        self.subscribe(EventTopics.SYSTEM_MODE_CHANGE, self._handle_system_mode_change),
        self.subscribe(EventTopics.MODE_TRANSITION_STARTED, self._handle_mode_transition),
        self.subscribe(EventTopics.MODE_TRANSITION_COMPLETE, self._handle_mode_transition),
        
        # Service status events
        self.subscribe(EventTopics.SERVICE_STATUS_UPDATE, self._handle_service_status),
        
        # Voice events
        self.subscribe(EventTopics.VOICE_LISTENING_STARTED, self._handle_voice_event),
        self.subscribe(EventTopics.VOICE_LISTENING_STOPPED, self._handle_voice_event),
        self.subscribe(EventTopics.TRANSCRIPTION_FINAL, self._handle_transcription),
        
        # DJ Mode events
        self.subscribe(EventTopics.DJ_MODE_CHANGED, self._handle_dj_mode_change),
        
        # Music events
        self.subscribe(EventTopics.MUSIC_PLAYBACK_STARTED, self._handle_music_event),
        self.subscribe(EventTopics.MUSIC_PLAYBACK_STOPPED, self._handle_music_event),
    )
```

## 4. Service Integration Standards

### 4.1 WebBridge Service Requirements

All WebBridge services must:

1. **Inherit from BaseService**: Follow CantinaOS service patterns
2. **Implement Proper Lifecycle**: Use `_start()` and `_stop()` methods
3. **Emit Service Status**: Report health to service monitoring system
4. **Handle Errors Gracefully**: Follow CantinaOS error handling standards

```python
class WebBridgeService(BaseService):
    """Web dashboard bridge service for CantinaOS integration."""
    
    def __init__(self, event_bus, config, logger=None):
        super().__init__(event_bus, config, logger)
        self._web_server = None
        self._connected_clients = set()
        
    async def _start(self) -> None:
        """Start web bridge service."""
        await self._setup_subscriptions()
        await self._start_web_server()
        await self._emit_status(
            ServiceStatus.RUNNING,
            "WebBridge service started successfully"
        )
        
    async def _stop(self) -> None:
        """Stop web bridge service."""
        if self._web_server:
            await self._web_server.close()
        await self._emit_status(
            ServiceStatus.STOPPED,
            "WebBridge service stopped"
        )
```

### 4.2 Service Registry Integration

WebBridge services must be properly registered in the CantinaOS Service Registry:

| Service Name | Purpose | Events Subscribed (Inputs) | Events Published (Outputs) | Configuration | Hardware Dependencies |
|--------------|---------|----------------------------|----------------------------|---------------|----------------------|
| WebBridgeService | Web dashboard integration | SYSTEM_MODE_CHANGE, SERVICE_STATUS_UPDATE, VOICE_LISTENING_STARTED, VOICE_LISTENING_STOPPED, DJ_MODE_CHANGED, MUSIC_PLAYBACK_STARTED, MUSIC_PLAYBACK_STOPPED | SYSTEM_SET_MODE_REQUEST, CLI_COMMAND, DJ_COMMAND | web_port, cors_origins | None |

## 5. State Synchronization Standards

### 5.1 Real-Time State Updates

The web dashboard must maintain real-time synchronization with CantinaOS state:

```python
async def _handle_system_mode_change(self, payload: dict) -> None:
    """Handle system mode changes from CantinaOS."""
    try:
        mode_payload = SystemModePayload(**payload)
        
        # Send real-time update to all connected web clients
        await self._broadcast_to_clients({
            "event": "system_mode_change",
            "data": {
                "current_mode": mode_payload.mode,
                "previous_mode": mode_payload.previous_mode,
                "timestamp": mode_payload.timestamp
            }
        })
        
    except Exception as e:
        self.logger.error(f"Error handling system mode change: {e}")
```

### 5.2 Service Status Synchronization

Service status must be accurately reflected in the dashboard:

```python
async def _handle_service_status(self, payload: dict) -> None:
    """Handle service status updates."""
    try:
        status_payload = ServiceStatusPayload(**payload)
        
        # Map CantinaOS status to web-compatible format
        web_status = self._map_service_status(status_payload.status)
        
        await self._broadcast_to_clients({
            "event": "service_status_update",
            "data": {
                "service_name": status_payload.service_name,
                "status": web_status,
                "uptime": status_payload.uptime,
                "message": status_payload.message,
                "timestamp": status_payload.timestamp
            }
        })
        
    except Exception as e:
        self.logger.error(f"Error handling service status: {e}")

def _map_service_status(self, cantina_status: str) -> str:
    """Map CantinaOS service status to web dashboard format."""
    status_map = {
        "RUNNING": "online",
        "STOPPED": "offline", 
        "ERROR": "error",
        "STARTING": "starting",
        "STOPPING": "stopping"
    }
    return status_map.get(cantina_status, "unknown")
```

## 6. Command Validation and Security

### 6.1 Command Validation

All web commands must be validated before processing:

```python
async def _validate_web_command(self, command: dict) -> bool:
    """Validate web commands before processing."""
    
    # Required fields validation
    if "action" not in command:
        raise ValueError("Command missing required 'action' field")
    
    # Action-specific validation
    if command["action"] == "set_mode":
        if "mode" not in command:
            raise ValueError("Mode change command missing 'mode' field")
        if command["mode"] not in ["IDLE", "AMBIENT", "INTERACTIVE"]:
            raise ValueError(f"Invalid mode: {command['mode']}")
    
    elif command["action"] == "play_music":
        if "track_name" not in command:
            raise ValueError("Music command missing 'track_name' field")
    
    return True
```

### 6.2 Security Standards

Web integrations must implement proper security measures:

1. **Input Validation**: Validate all incoming web commands
2. **Rate Limiting**: Prevent command flooding
3. **Authentication**: Verify client permissions (if required)
4. **Error Handling**: Avoid exposing internal system details

```python
class CommandRateLimiter:
    """Rate limiting for web commands."""
    
    def __init__(self, max_commands_per_minute: int = 60):
        self._max_commands = max_commands_per_minute
        self._command_history = defaultdict(list)
    
    def is_allowed(self, client_id: str) -> bool:
        """Check if client is within rate limits."""
        now = time.time()
        client_history = self._command_history[client_id]
        
        # Remove commands older than 1 minute
        client_history[:] = [cmd_time for cmd_time in client_history if now - cmd_time < 60]
        
        # Check if within limits
        if len(client_history) >= self._max_commands:
            return False
        
        # Record this command
        client_history.append(now)
        return True
```

## 7. Error Handling and Recovery

### 7.1 Connection Error Handling

Web connections must handle disconnections gracefully:

```python
async def _handle_client_disconnect(self, client_id: str) -> None:
    """Handle client disconnection."""
    try:
        self._connected_clients.discard(client_id)
        self.logger.info(f"Client {client_id} disconnected")
        
        # Clean up client-specific resources
        if client_id in self._client_subscriptions:
            del self._client_subscriptions[client_id]
            
    except Exception as e:
        self.logger.error(f"Error handling client disconnect: {e}")
```

### 7.2 Event Processing Error Recovery

Event processing errors must not crash the web bridge:

```python
async def _safe_event_handler(self, handler_func, payload: dict) -> None:
    """Safely execute event handlers with error recovery."""
    try:
        await handler_func(payload)
    except Exception as e:
        self.logger.error(f"Error in event handler {handler_func.__name__}: {e}")
        
        # Emit error status
        await self._emit_status(
            ServiceStatus.ERROR,
            f"Event handler error: {e}",
            severity=LogLevel.ERROR
        )
        
        # Continue operation - don't crash the bridge
        pass
```

## 8. Testing Requirements

### 8.1 Integration Testing Standards

Web dashboard integrations must include comprehensive tests:

```python
class TestWebBridgeIntegration:
    """Integration tests for web bridge service."""
    
    async def test_system_mode_change_flow(self):
        """Test complete system mode change flow."""
        # 1. Send web command
        await self.web_client.send_command({
            "action": "set_mode",
            "mode": "INTERACTIVE"
        })
        
        # 2. Verify proper event emission
        emitted_events = await self.mock_event_bus.get_emitted_events()
        assert EventTopics.SYSTEM_SET_MODE_REQUEST in emitted_events
        
        # 3. Simulate CantinaOS response
        await self.mock_event_bus.emit(EventTopics.SYSTEM_MODE_CHANGE, {
            "mode": "INTERACTIVE",
            "previous_mode": "IDLE"
        })
        
        # 4. Verify web client receives update
        received = await self.web_client.wait_for_event("system_mode_change")
        assert received["data"]["current_mode"] == "INTERACTIVE"
```

### 8.2 End-to-End Testing

All web functionality must be tested end-to-end:

1. **Dashboard Startup**: Test full system startup with dashboard
2. **Real Service Integration**: Test with actual CantinaOS services
3. **State Synchronization**: Verify real-time state updates
4. **Error Scenarios**: Test error handling and recovery

## 9. Performance Standards

### 9.1 Event Throttling

High-frequency events must be throttled for web clients:

```python
class EventThrottler:
    """Throttle high-frequency events for web clients."""
    
    def __init__(self):
        self._throttle_config = {
            "high_frequency": {"max_per_second": 10, "events": [
                "TRANSCRIPTION_INTERIM", "SPEECH_SYNTHESIS_AMPLITUDE"
            ]},
            "medium_frequency": {"max_per_second": 30, "events": [
                "SERVICE_STATUS_UPDATE", "VOICE_LISTENING_STARTED"
            ]},
            "low_frequency": {"max_per_second": None, "events": [
                "SYSTEM_MODE_CHANGE", "DJ_MODE_CHANGED"
            ]}
        }
```

### 9.2 Connection Limits

Web bridges must limit concurrent connections:

```python
MAX_CONCURRENT_CLIENTS = 10

async def _handle_new_connection(self, websocket, path):
    """Handle new web client connection."""
    if len(self._connected_clients) >= MAX_CONCURRENT_CLIENTS:
        await websocket.close(code=1013, reason="Server overloaded")
        return
    
    # Accept connection
    client_id = str(uuid.uuid4())
    self._connected_clients.add(client_id)
    await self._handle_client_session(websocket, client_id)
```

## 10. Documentation Requirements

### 10.1 API Documentation

All web APIs must be documented:

```python
@dataclass
class WebAPICommand:
    """Documentation for web API commands."""
    action: str
    description: str
    required_fields: List[str]
    optional_fields: List[str]
    example_payload: dict
    expected_response: dict

# Example API documentation
SYSTEM_MODE_COMMAND = WebAPICommand(
    action="set_mode",
    description="Change system mode between IDLE, AMBIENT, and INTERACTIVE",
    required_fields=["action", "mode"],
    optional_fields=[],
    example_payload={"action": "set_mode", "mode": "INTERACTIVE"},
    expected_response={"success": True, "message": "Mode change initiated"}
)
```

### 10.2 Event Documentation

All web events must be documented with their CantinaOS mappings:

| Web Event | CantinaOS Event Topic | Purpose | Payload Format |
|-----------|----------------------|---------|----------------|
| `set_mode` | `SYSTEM_SET_MODE_REQUEST` | Change system mode | `{"mode": "INTERACTIVE"}` |
| `start_recording` | `SYSTEM_SET_MODE_REQUEST` | Start voice recording | `{"mode": "INTERACTIVE"}` |
| `dj_start` | `DJ_COMMAND` | Activate DJ mode | `{"command": "dj start"}` |
| `play_music` | `CLI_COMMAND` | Play music track | `{"command": "play music", "args": ["track_name"]}` |

## 11. Deployment Standards

### 11.1 Service Registration

WebBridge services must be properly registered in CantinaOS main.py:

```python
# In cantina_os/main.py
async def initialize_services():
    """Initialize all CantinaOS services including web bridge."""
    
    # ... other service initialization
    
    # Web bridge service
    web_bridge_config = {
        "web_port": int(os.getenv("WEB_BRIDGE_PORT", "8000")),
        "cors_origins": os.getenv("CORS_ORIGINS", "http://localhost:3000").split(","),
        "max_clients": int(os.getenv("MAX_WEB_CLIENTS", "10"))
    }
    
    web_bridge_service = WebBridgeService(
        event_bus=event_bus,
        config=web_bridge_config,
        logger=logger.getChild("web_bridge")
    )
    
    services.append(web_bridge_service)
    
    return services
```

### 11.2 Configuration Management

Web bridge configuration must follow CantinaOS standards:

```python
from pydantic import BaseModel, Field

class WebBridgeConfig(BaseModel):
    """Configuration for web bridge service."""
    web_port: int = Field(default=8000, description="Web server port")
    cors_origins: List[str] = Field(default=["http://localhost:3000"], description="Allowed CORS origins")
    max_clients: int = Field(default=10, description="Maximum concurrent clients")
    rate_limit_per_minute: int = Field(default=60, description="Commands per minute per client")
    enable_debug_events: bool = Field(default=False, description="Enable debug event broadcasting")
```

## 12. Common Failure Patterns and Solutions

### 12.1 Event Topic Mismatches

**Problem**: Dashboard events not reaching CantinaOS services
**Cause**: Using generic event names instead of proper EventTopics enum values
**Solution**: Always use EventTopics enum and verify service subscriptions

### 12.2 Service Bypass Issues

**Problem**: Dashboard bypassing critical CantinaOS services
**Cause**: Direct service calls instead of proper event flow
**Solution**: Follow Event Bus Topology - never bypass mode management or command processing

### 12.3 State Synchronization Failures

**Problem**: Dashboard showing incorrect system state
**Cause**: Missing event subscriptions or improper state mapping
**Solution**: Subscribe to all relevant CantinaOS events and implement proper state translation

### 12.4 Service Startup Race Conditions

**Problem**: WebBridge starting before core services
**Cause**: Improper service initialization order
**Solution**: Ensure WebBridge starts after all core services are initialized

## 13. Conclusion

Web dashboard integration with CantinaOS requires strict adherence to the established event-driven architecture. By following these standards, web interfaces can provide rich user experiences while maintaining the integrity and reliability of the core CantinaOS system.

**Key Takeaways**:
- Always follow the Event Bus Topology defined in the system architecture
- Never bypass core services like YodaModeManagerService
- Implement proper event topic translation between web and CantinaOS
- Maintain real-time state synchronization
- Follow CantinaOS service patterns for all web bridge components

Failure to follow these standards will result in integration failures, broken functionality, and inconsistent user experiences.