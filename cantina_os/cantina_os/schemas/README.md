# DJ R3X Socket.IO Command Schema System

## Overview

The DJ R3X Socket.IO Command Schema System provides comprehensive Pydantic validation for all socket.io commands exchanged between the web dashboard and CantinaOS. This system ensures type safety, proper validation, and seamless integration with the CantinaOS event bus architecture.

## Phase 1 Implementation Status ✅

This is the **Phase 1** foundation implementation as specified in the design document, providing:

- ✅ **Base Schema Classes**: Complete validation patterns and CantinaOS integration
- ✅ **All Command Schema Models**: Voice, Music, DJ, and System commands
- ✅ **Validation Infrastructure**: Decorators, mixins, and schema registry  
- ✅ **Error Handling**: Comprehensive error hierarchy with ServiceStatusPayload compatibility
- ✅ **Event Bus Integration**: Proper `to_cantina_event()` methods following Event Bus Topology

## Architecture

### Core Components

1. **Base Schema Classes** (`__init__.py`)
   - `BaseWebCommand`: Abstract base for all commands with validation patterns
   - `BaseWebResponse`: Standardized response format  
   - `WebCommandError`: Specialized error handling
   - `CantinaOSEventMixin`: Event bus integration utilities

2. **Command Schema Models** (`web_commands.py`)
   - `VoiceCommandSchema`: Voice recording start/stop
   - `MusicCommandSchema`: Music playback controls with volume support
   - `DJCommandSchema`: DJ mode lifecycle and settings updates
   - `SystemCommandSchema`: System mode changes and configuration

3. **Validation Infrastructure** (`validation.py`)
   - `@validate_socketio_command`: Decorator for automatic validation
   - `SocketIOValidationMixin`: WebBridge service integration
   - `CommandSchemaRegistry`: Central schema registry
   - Error formatting and compatibility validation

## Supported Commands

### Voice Commands
```json
{"action": "start"}  → SYSTEM_SET_MODE_REQUEST (INTERACTIVE)
{"action": "stop"}   → SYSTEM_SET_MODE_REQUEST (AMBIENT)
```

### Music Commands
```json
{"action": "play", "track_name": "Cantina Band"}
{"action": "pause"}
{"action": "resume"}
{"action": "stop"}
{"action": "next"}
{"action": "queue", "track_name": "Imperial March"}
{"action": "volume", "volume_level": 0.8}  ← NEW: Volume control
```

### DJ Commands
```json
{"action": "start", "auto_transition": true}
{"action": "stop"}
{"action": "next"}
{"action": "update_settings", "transition_duration": 10.0}  ← NEW: Settings updates
```

### System Commands
```json
{"action": "set_mode", "mode": "INTERACTIVE"}
{"action": "restart", "restart_delay": 5.0}
{"action": "refresh_config"}
```

## Usage Examples

### WebBridge Service Integration

```python
from cantina_os.schemas.validation import SocketIOValidationMixin, validate_socketio_command

class WebBridgeService(BaseService, SocketIOValidationMixin):
    
    @validate_socketio_command("music_command")
    async def music_command(self, sid: str, validated_command: MusicCommandSchema):
        """Handle validated music commands."""
        # Command is already validated - direct event emission
        event_payload = validated_command.to_cantina_event()
        event_payload["sid"] = sid
        
        self._event_bus.emit(EventTopics.MUSIC_COMMAND, event_payload)
```

### Manual Validation

```python
from cantina_os.schemas.validation import COMMAND_SCHEMA_REGISTRY

# Validate command data
try:
    command = COMMAND_SCHEMA_REGISTRY.validate_command(
        "music_command", 
        {"action": "volume", "volume_level": 0.8}
    )
    
    # Convert to CantinaOS event
    event_payload = command.to_cantina_event()
    
except WebCommandError as e:
    print(f"Validation failed: {e.message}")
    print(f"Errors: {e.validation_errors}")
```

## Event Bus Integration

All commands implement `to_cantina_event()` methods that produce event payloads compatible with CantinaOS Event Bus Topology:

```python
# Voice command integration
voice_cmd = VoiceCommandSchema(action="start")
payload = voice_cmd.to_cantina_event()
# Result: {"mode": "INTERACTIVE", "source": "web_dashboard", "timestamp": "..."}

# Music command with volume
music_cmd = MusicCommandSchema(action="volume", volume_level=0.8)  
payload = music_cmd.to_cantina_event()
# Result: {"action": "volume", "volume": 0.8, "source": "web_dashboard", ...}
```

## Error Handling

The system provides comprehensive error handling with proper error hierarchies:

```python
# Validation errors
try:
    command = validate_command_data("music_command", {"action": "invalid"})
except WebCommandError as e:
    response = {
        "error": True,
        "message": e.message,
        "validation_errors": e.validation_errors,
        "timestamp": "..."
    }

# ServiceStatusPayload compatibility
response = BaseWebResponse.error_response("Command failed")
status_payload = response.to_service_status_payload("web_bridge")
```

## Testing

Run comprehensive tests with:

```python
from cantina_os.schemas.examples import run_comprehensive_tests

run_comprehensive_tests()
```

This will test:
- ✅ Command validation with valid/invalid examples
- ✅ Event payload generation for all command types  
- ✅ Socket.IO validation decorator functionality
- ✅ Schema registry operations

## Missing Commands Addressed

Phase 1 addresses the missing commands identified in the WebBridge analysis:

1. **Volume Control**: `MusicCommandSchema` now supports `volume` action with `volume_level` field
2. **DJ Settings Updates**: `DJCommandSchema` now supports `update_settings` action with configuration fields
3. **Proper Validation**: All commands now have comprehensive field validation and error handling

## Integration Requirements

For WebBridge service integration:

1. **Import the validation system**:
   ```python
   from cantina_os.schemas.validation import SocketIOValidationMixin, validate_socketio_command
   ```

2. **Inherit from SocketIOValidationMixin**:
   ```python
   class WebBridgeService(BaseService, SocketIOValidationMixin):
   ```

3. **Apply validation decorators**:
   ```python
   @validate_socketio_command("command_type")
   async def command_handler(self, sid, validated_command):
   ```

4. **Use validated commands directly**:
   ```python
   event_payload = validated_command.to_cantina_event()
   self._event_bus.emit(event_topic, event_payload)
   ```

## Benefits

- **Type Safety**: Full Pydantic validation with proper type hints
- **Error Prevention**: Comprehensive validation prevents invalid commands reaching CantinaOS
- **Event Bus Compliance**: All commands generate proper CantinaOS event payloads
- **Maintainability**: Centralized schema registry makes adding new commands easy
- **Testing**: Complete testing framework ensures system reliability
- **Documentation**: Self-documenting schemas with clear validation rules

## Next Steps (Future Phases)

- Phase 2: Integration with WebBridge service
- Phase 3: Real-time validation feedback in dashboard
- Phase 4: Advanced command batching and transaction support

---

**Implementation Status**: Phase 1 Complete ✅  
**CantinaOS Compatibility**: Full Event Bus Topology compliance ✅  
**Testing Coverage**: Comprehensive test suite included ✅