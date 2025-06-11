# CantinaOS Schema System Implementation Plan

## 1. Implementation Overview

This document provides a detailed, step-by-step implementation plan for the centralized Pydantic schema system described in `CENTRALIZED_SCHEMA_SYSTEM_DESIGN.md`. The implementation follows a phased approach to minimize disruption while providing immediate validation benefits.

## 2. Phase 1: Schema Foundation (Week 1)

### 2.1 Directory Structure Creation

**Create schema module structure:**

```bash
# Create schema directories
mkdir -p cantina_os/cantina_os/schemas/web_commands
mkdir -p cantina_os/cantina_os/schemas/responses  
mkdir -p cantina_os/cantina_os/schemas/generators
mkdir -p cantina_os/cantina_os/schemas/validators
```

**Files to create:**
- `cantina_os/cantina_os/schemas/__init__.py`
- `cantina_os/cantina_os/schemas/base.py`
- `cantina_os/cantina_os/schemas/web_commands/__init__.py`
- `cantina_os/cantina_os/schemas/web_commands/voice_commands.py`
- `cantina_os/cantina_os/schemas/web_commands/music_commands.py`
- `cantina_os/cantina_os/schemas/web_commands/dj_commands.py`
- `cantina_os/cantina_os/schemas/web_commands/system_commands.py`
- `cantina_os/cantina_os/schemas/responses/__init__.py`
- `cantina_os/cantina_os/schemas/responses/command_responses.py`
- `cantina_os/cantina_os/schemas/responses/error_responses.py`
- `cantina_os/cantina_os/schemas/generators/__init__.py`
- `cantina_os/cantina_os/schemas/generators/typescript_generator.py`
- `cantina_os/cantina_os/schemas/validators/__init__.py`
- `cantina_os/cantina_os/schemas/validators/socketio_validator.py`

### 2.2 Schema Dependencies

**Add to `cantina_os/requirements.txt`:**
```text
# Schema generation dependencies
datamodel-code-generator==0.21.0  # For advanced TypeScript generation
inflection==0.5.1                 # For naming convention conversion
```

**Install dependencies:**
```bash
cd cantina_os
pip install datamodel-code-generator inflection
```

### 2.3 Base Schema Implementation

**Step 1: Implement `base.py`**
```python
# cantina_os/cantina_os/schemas/base.py
# [Implementation from design document - BaseWebCommand, BaseWebResponse, WebCommandError classes]
```

**Step 2: Create schema registry in `__init__.py`**
```python
# cantina_os/cantina_os/schemas/__init__.py
from .base import BaseWebCommand, BaseWebResponse, WebCommandError
from .web_commands import VoiceCommand, MusicCommand, DJCommand, SystemCommand
from .responses import VoiceCommandResponse, MusicCommandResponse, DJCommandResponse, SystemCommandResponse

__all__ = [
    "BaseWebCommand",
    "BaseWebResponse", 
    "WebCommandError",
    "VoiceCommand",
    "MusicCommand",
    "DJCommand", 
    "SystemCommand",
    "VoiceCommandResponse",
    "MusicCommandResponse",
    "DJCommandResponse",
    "SystemCommandResponse"
]

# Schema registry for runtime lookup
COMMAND_SCHEMAS = {
    "voice_command": VoiceCommand,
    "music_command": MusicCommand,
    "dj_command": DJCommand,
    "system_command": SystemCommand,
}

RESPONSE_SCHEMAS = {
    "voice_command": VoiceCommandResponse,
    "music_command": MusicCommandResponse,
    "dj_command": DJCommandResponse,
    "system_command": SystemCommandResponse,
}
```

### 2.4 Command Schema Implementation

**Step 3: Implement command schemas**
- `voice_commands.py` - [Implementation from design document]
- `music_commands.py` - [Implementation from design document]  
- `dj_commands.py` - [Implementation from design document]
- `system_commands.py` - [Implementation from design document]

**Step 4: Web commands `__init__.py`**
```python
# cantina_os/cantina_os/schemas/web_commands/__init__.py
from .voice_commands import VoiceCommand
from .music_commands import MusicCommand
from .dj_commands import DJCommand
from .system_commands import SystemCommand

__all__ = ["VoiceCommand", "MusicCommand", "DJCommand", "SystemCommand"]
```

### 2.5 Response Schema Implementation

**Step 5: Implement response schemas**
```python
# cantina_os/cantina_os/schemas/responses/command_responses.py
from typing import Literal, Optional, Dict, Any
from pydantic import Field
from ..base import BaseWebResponse

class VoiceCommandResponse(BaseWebResponse):
    """Response schema for voice commands."""
    voice_status: Literal["recording", "processing", "idle"] = Field(..., description="Current voice status")

class MusicCommandResponse(BaseWebResponse):
    """Response schema for music commands."""
    current_track: Optional[dict] = Field(None, description="Currently playing track info")
    queue_length: Optional[int] = Field(None, description="Number of tracks in queue")
    playback_status: Literal["playing", "paused", "stopped"] = Field(..., description="Current playback status")

class DJCommandResponse(BaseWebResponse):
    """Response schema for DJ commands."""
    dj_status: Literal["active", "inactive"] = Field(..., description="Current DJ mode status")
    auto_transition: bool = Field(..., description="Auto transition enabled status")

class SystemCommandResponse(BaseWebResponse):
    """Response schema for system commands."""
    current_mode: Literal["IDLE", "AMBIENT", "INTERACTIVE"] = Field(..., description="Current system mode")
    services_status: Dict[str, Any] = Field(..., description="Current service status overview")
```

### 2.6 TypeScript Generator Implementation

**Step 6: Implement TypeScript generator**
```python
# cantina_os/cantina_os/schemas/generators/typescript_generator.py
# [Full implementation from design document]
```

**Step 7: Create generation script**
```bash
# scripts/generate-schemas.sh
#!/bin/bash
echo "🔧 Generating TypeScript interfaces from Pydantic schemas..."

# Activate CantinaOS virtual environment
cd cantina_os
source venv/bin/activate

# Generate TypeScript interfaces
python -m cantina_os.schemas.generators.typescript_generator \
  --output ../dj-r3x-dashboard/src/types/generated-schemas.ts

if [ $? -eq 0 ]; then
  echo "✅ TypeScript interfaces generated successfully"
else
  echo "❌ Failed to generate TypeScript interfaces"
  exit 1
fi

cd ../dj-r3x-dashboard

# Format the generated TypeScript file if prettier is available
if command -v prettier &> /dev/null; then
  npx prettier --write src/types/generated-schemas.ts
  echo "✨ Formatted TypeScript interfaces"
fi

echo "📋 TypeScript interface generation complete"
```

**Make script executable:**
```bash
chmod +x scripts/generate-schemas.sh
```

### 2.7 Testing Framework Setup

**Step 8: Create test structure**
```bash
mkdir -p cantina_os/tests/schemas
mkdir -p cantina_os/tests/schemas/web_commands  
mkdir -p cantina_os/tests/schemas/generators
```

**Step 9: Basic schema tests**
```python
# cantina_os/tests/schemas/test_voice_commands.py
import pytest
from datetime import datetime
from pydantic import ValidationError
from cantina_os.schemas.web_commands import VoiceCommand
from cantina_os.core.event_topics import EventTopics

class TestVoiceCommand:
    def test_valid_voice_start_command(self):
        """Test valid voice start command."""
        command = VoiceCommand(action="start")
        assert command.action == "start"
        assert command.source == "web_dashboard"
        assert isinstance(command.timestamp, datetime)

    def test_valid_voice_stop_command(self):
        """Test valid voice stop command."""
        command = VoiceCommand(action="stop", text="Optional text")
        assert command.action == "stop"
        assert command.text == "Optional text"

    def test_invalid_voice_action(self):
        """Test validation error for invalid action."""
        with pytest.raises(ValidationError) as exc_info:
            VoiceCommand(action="invalid_action")
        
        errors = exc_info.value.errors()
        assert any("Invalid voice action" in str(error["msg"]) for error in errors)

    def test_cantina_event_conversion_start(self):
        """Test conversion to CantinaOS event for start action."""
        command = VoiceCommand(action="start")
        topic, payload = command.to_cantina_event()
        
        assert topic == EventTopics.SYSTEM_SET_MODE_REQUEST
        assert payload["mode"] == "INTERACTIVE"
        assert payload["source"] == "web_dashboard"

    def test_cantina_event_conversion_stop(self):
        """Test conversion to CantinaOS event for stop action."""
        command = VoiceCommand(action="stop")
        topic, payload = command.to_cantina_event()
        
        assert topic == EventTopics.SYSTEM_SET_MODE_REQUEST
        assert payload["mode"] == "AMBIENT"
```

**Run initial tests:**
```bash
cd cantina_os
python -m pytest tests/schemas/ -v
```

## 3. Phase 2: Validation Middleware (Week 2)

### 3.1 Socket.IO Validation Implementation

**Step 10: Implement validation middleware**
```python
# cantina_os/cantina_os/schemas/validators/socketio_validator.py
# [Full implementation from design document - validate_socketio_command decorator and SocketIOValidationMixin]
```

**Step 11: Create validation tests**
```python
# cantina_os/tests/schemas/test_socketio_validator.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from cantina_os.schemas.validators.socketio_validator import validate_socketio_command, SocketIOValidationMixin

class MockWebBridge(SocketIOValidationMixin):
    """Mock WebBridge for testing validation."""
    def __init__(self):
        self._sio = AsyncMock()
        self.validation_calls = []

    @validate_socketio_command("voice_command")
    async def voice_command_handler(self, sid: str, validated_command):
        """Mock handler for testing."""
        self.validation_calls.append(("voice_command", sid, validated_command))

class TestSocketIOValidation:
    def test_valid_command_validation(self):
        """Test successful validation of valid command."""
        bridge = MockWebBridge()
        valid_data = {"action": "start"}
        
        # Should not raise validation error
        await bridge.voice_command_handler("test_sid", valid_data)
        
        # Verify handler was called with validated command
        assert len(bridge.validation_calls) == 1
        call_type, call_sid, validated_cmd = bridge.validation_calls[0]
        assert call_type == "voice_command"
        assert call_sid == "test_sid"
        assert validated_cmd.action == "start"

    def test_invalid_command_validation(self):
        """Test validation error handling."""
        bridge = MockWebBridge()
        invalid_data = {"action": "invalid_action"}
        
        # Should call error handler, not main handler
        await bridge.voice_command_handler("test_sid", invalid_data)
        
        # Verify error was sent
        bridge._sio.emit.assert_called_with(
            "command_error", 
            pytest.any(dict), 
            room="test_sid"
        )
        
        # Verify main handler was not called
        assert len(bridge.validation_calls) == 0
```

### 3.2 Error Response System

**Step 12: Enhanced error response handling**
```python
# cantina_os/cantina_os/schemas/responses/error_responses.py
from typing import Dict, Optional, List
from datetime import datetime
from pydantic import BaseModel, Field
from enum import Enum

class ErrorSeverity(str, Enum):
    """Error severity levels."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

class ValidationError(BaseModel):
    """Individual validation error details."""
    field: str = Field(..., description="Field that failed validation")
    message: str = Field(..., description="Validation error message")
    invalid_value: Optional[str] = Field(None, description="The invalid value provided")

class WebCommandError(BaseModel):
    """Comprehensive error response for web commands."""
    success: bool = Field(default=False, description="Always false for errors")
    error: str = Field(..., description="Human-readable error message")
    error_code: str = Field(..., description="Machine-readable error code")
    severity: ErrorSeverity = Field(default=ErrorSeverity.MEDIUM, description="Error severity level")
    command: Optional[str] = Field(None, description="The command that failed")
    validation_errors: Optional[List[ValidationError]] = Field(None, description="Detailed validation errors")
    retry_allowed: bool = Field(default=True, description="Whether the command can be retried")
    timestamp: datetime = Field(default_factory=datetime.now, description="Error timestamp")
    request_id: Optional[str] = Field(None, description="Request tracking ID")

class SystemError(WebCommandError):
    """System-level error response."""
    affected_services: Optional[List[str]] = Field(None, description="Services affected by the error")
    recovery_action: Optional[str] = Field(None, description="Suggested recovery action")
```

### 3.3 Validation Middleware Testing

**Step 13: Integration tests for validation**
```bash
# Run validation tests
cd cantina_os
python -m pytest tests/schemas/test_socketio_validator.py -v
```

## 4. Phase 3: WebBridge Integration (Week 3)

### 4.1 WebBridge Service Updates

**Step 14: Update WebBridge service imports**
```python
# At the top of cantina_os/cantina_os/services/web_bridge_service.py
from ..schemas import COMMAND_SCHEMAS, RESPONSE_SCHEMAS
from ..schemas.validators.socketio_validator import validate_socketio_command, SocketIOValidationMixin
from ..schemas.responses.error_responses import WebCommandError, SystemError
from ..schemas.web_commands import VoiceCommand, MusicCommand, DJCommand, SystemCommand
```

**Step 15: Update WebBridge class declaration**
```python
# Modify class declaration in web_bridge_service.py
class WebBridgeService(BaseService, SocketIOValidationMixin):
    """Web Bridge Service with centralized schema validation."""
```

**Step 16: Replace existing handlers with validated versions**
```python
# In WebBridgeService._add_socketio_handlers() method

@self._sio.event
@validate_socketio_command("voice_command")
async def voice_command(sid: str, validated_command: VoiceCommand):
    """Handle validated voice commands from dashboard."""
    logger.info(f"✅ Validated voice command from {sid}: {validated_command.action}")
    
    try:
        # Convert to CantinaOS event using schema method
        event_topic, payload = validated_command.to_cantina_event()
        
        # Emit to CantinaOS event bus
        self._event_bus.emit(event_topic, payload)
        
        # Send success response
        response = VoiceCommandResponse(
            success=True,
            message=f"Voice command '{validated_command.action}' executed successfully",
            voice_status="processing" if validated_command.action == "start" else "idle"
        )
        await self._sio.emit("command_response", response.dict(), room=sid)
        
        logger.info(f"✅ Voice command '{validated_command.action}' executed successfully")
        
    except Exception as e:
        logger.error(f"❌ Error executing voice command: {e}")
        await self._send_command_error(
            sid, 
            f"Failed to execute voice command: {str(e)}", 
            "EXECUTION_ERROR",
            command=validated_command.action
        )

@self._sio.event  
@validate_socketio_command("music_command")
async def music_command(sid: str, validated_command: MusicCommand):
    """Handle validated music commands from dashboard."""
    logger.info(f"✅ Validated music command from {sid}: {validated_command.action}")
    
    try:
        event_topic, payload = validated_command.to_cantina_event()
        self._event_bus.emit(event_topic, payload)
        
        response = MusicCommandResponse(
            success=True,
            message=f"Music command '{validated_command.action}' executed successfully",
            playback_status="playing" if validated_command.action in ["play", "resume"] else "stopped",
            current_track={"name": validated_command.track_name} if validated_command.track_name else None
        )
        await self._sio.emit("command_response", response.dict(), room=sid)
        
    except Exception as e:
        logger.error(f"❌ Error executing music command: {e}")
        await self._send_command_error(
            sid,
            f"Failed to execute music command: {str(e)}",
            "EXECUTION_ERROR",
            command=validated_command.action
        )

@self._sio.event
@validate_socketio_command("dj_command") 
async def dj_command(sid: str, validated_command: DJCommand):
    """Handle validated DJ commands from dashboard."""
    logger.info(f"✅ Validated DJ command from {sid}: {validated_command.action}")
    
    try:
        event_topic, payload = validated_command.to_cantina_event()
        self._event_bus.emit(event_topic, payload)
        
        response = DJCommandResponse(
            success=True,
            message=f"DJ command '{validated_command.action}' executed successfully",
            dj_status="active" if validated_command.action == "start" else "inactive",
            auto_transition=validated_command.auto_transition if validated_command.action == "start" else False
        )
        await self._sio.emit("command_response", response.dict(), room=sid)
        
    except Exception as e:
        logger.error(f"❌ Error executing DJ command: {e}")
        await self._send_command_error(
            sid,
            f"Failed to execute DJ command: {str(e)}",
            "EXECUTION_ERROR", 
            command=validated_command.action
        )

@self._sio.event
@validate_socketio_command("system_command")
async def system_command(sid: str, validated_command: SystemCommand):
    """Handle validated system commands from dashboard."""
    logger.info(f"✅ Validated system command from {sid}: {validated_command.action}")
    
    try:
        event_topic, payload = validated_command.to_cantina_event()
        self._event_bus.emit(event_topic, payload)
        
        response = SystemCommandResponse(
            success=True,
            message=f"System command '{validated_command.action}' executed successfully",
            current_mode=validated_command.mode if validated_command.mode else "IDLE",
            services_status=self._get_service_status()
        )
        await self._sio.emit("command_response", response.dict(), room=sid)
        
    except Exception as e:
        logger.error(f"❌ Error executing system command: {e}")
        await self._send_command_error(
            sid,
            f"Failed to execute system command: {str(e)}",
            "EXECUTION_ERROR",
            command=validated_command.action
        )
```

**Step 17: Add enhanced error handling method**
```python
# Add to WebBridgeService class
async def _send_command_error(
    self, 
    sid: str, 
    error_message: str, 
    error_code: str,
    command: str = None,
    validation_errors: Dict[str, str] = None,
    severity: str = "medium"
):
    """Send standardized command error response to client."""
    error_response = WebCommandError(
        error=error_message,
        error_code=error_code,
        severity=severity,
        command=command,
        validation_errors=[
            ValidationError(field=field, message=message, invalid_value=None)
            for field, message in (validation_errors or {}).items()
        ] if validation_errors else None,
        retry_allowed=error_code not in ["AUTHORIZATION_ERROR", "SERVICE_UNAVAILABLE"]
    )
    
    await self._sio.emit("command_error", error_response.dict(), room=sid)
    logger.warning(f"🚫 Sent command error to {sid}: {error_code} - {error_message}")
```

### 4.2 WebBridge Integration Testing

**Step 18: Create WebBridge integration tests**
```python
# cantina_os/tests/integration/test_webbridge_validation.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from cantina_os.services.web_bridge_service import WebBridgeService
from cantina_os.schemas.web_commands import VoiceCommand

class TestWebBridgeValidation:
    @pytest.fixture
    async def web_bridge(self):
        """Create WebBridge service for testing."""
        mock_event_bus = AsyncMock()
        mock_config = {"host": "127.0.0.1", "port": 8000}
        
        service = WebBridgeService(mock_event_bus, mock_config)
        service._sio = AsyncMock()  # Mock Socket.IO server
        
        return service

    async def test_valid_voice_command_handling(self, web_bridge):
        """Test handling of valid voice command."""
        mock_sid = "test_client_123"
        valid_command_data = {"action": "start"}
        
        # Create validated command (this would normally be done by decorator)
        validated_command = VoiceCommand(**valid_command_data)
        
        # Test the handler directly
        await web_bridge.voice_command(mock_sid, validated_command)
        
        # Verify event was emitted to CantinaOS
        web_bridge._event_bus.emit.assert_called_once()
        
        # Verify success response was sent
        web_bridge._sio.emit.assert_called_with(
            "command_response",
            pytest.any(dict),
            room=mock_sid
        )
    
    async def test_command_execution_error_handling(self, web_bridge):
        """Test proper error handling when command execution fails."""
        mock_sid = "test_client_123"
        
        # Mock event bus to raise an exception
        web_bridge._event_bus.emit.side_effect = Exception("Event bus failure")
        
        validated_command = VoiceCommand(action="start")
        
        # Should handle the exception gracefully
        await web_bridge.voice_command(mock_sid, validated_command)
        
        # Verify error response was sent
        web_bridge._sio.emit.assert_called_with(
            "command_error",
            pytest.any(dict), 
            room=mock_sid
        )
```

**Step 19: Run integration tests**
```bash
cd cantina_os
python -m pytest tests/integration/test_webbridge_validation.py -v
```

## 5. Phase 4: Frontend Integration (Week 4)

### 5.1 TypeScript Interface Generation

**Step 20: Generate initial TypeScript interfaces**
```bash
# Run the generation script
./scripts/generate-schemas.sh
```

**Verify generated file:**
```bash
# Check the generated TypeScript file
ls -la dj-r3x-dashboard/src/types/generated-schemas.ts
head -20 dj-r3x-dashboard/src/types/generated-schemas.ts
```

### 5.2 Dashboard Package.json Updates

**Step 21: Update build scripts**
```json
// dj-r3x-dashboard/package.json
{
  "scripts": {
    "generate-schemas": "../scripts/generate-schemas.sh",
    "prebuild": "npm run generate-schemas",
    "predev": "npm run generate-schemas", 
    "dev": "next dev",
    "build": "next build",
    "lint": "next lint",
    "format": "prettier --write ."
  }
}
```

### 5.3 useSocket Hook Updates

**Step 22: Create new typed useSocket hook**
```typescript
// dj-r3x-dashboard/src/hooks/useTypedSocket.ts
'use client'

import { useEffect, useState, useRef } from 'react'
import { io, Socket } from 'socket.io-client'
import { 
  VoiceCommand, 
  MusicCommand, 
  DJCommand, 
  SystemCommand,
  BaseWebResponse,
  WebCommandError,
  VoiceCommandResponse,
  MusicCommandResponse,
  DJCommandResponse,
  SystemCommandResponse
} from '../types/generated-schemas'

// Define command result type
export interface CommandResult<T = BaseWebResponse> {
  success: boolean
  data?: T
  error?: WebCommandError
}

export const useTypedSocket = () => {
  const [socket, setSocket] = useState<Socket | null>(null)
  const [connected, setConnected] = useState(false)
  const [lastError, setLastError] = useState<WebCommandError | null>(null)
  
  // ... existing state from useSocket ...

  useEffect(() => {
    const newSocket = io('http://localhost:8000', {
      transports: ['websocket'],
      autoConnect: true,
    })

    setSocket(newSocket)

    // Connection events
    newSocket.on('connect', () => {
      console.log('🔌 Connected with typed socket interface')
      setConnected(true)
      setLastError(null)
    })

    newSocket.on('disconnect', () => {
      console.log('🔌 Disconnected from typed socket')
      setConnected(false)
    })

    // Enhanced error handling
    newSocket.on('command_error', (error: WebCommandError) => {
      console.error('🚫 Command error received:', error)
      setLastError(error)
    })

    // Enhanced response handling
    newSocket.on('command_response', (response: BaseWebResponse) => {
      console.log('✅ Command response received:', response)
      setLastError(null)
    })

    // ... existing event handlers ...

    return () => {
      newSocket.close()
    }
  }, [])

  // Generic command sender with full type safety
  const sendCommand = async <TResponse extends BaseWebResponse>(
    eventName: string,
    command: VoiceCommand | MusicCommand | DJCommand | SystemCommand
  ): Promise<CommandResult<TResponse>> => {
    return new Promise((resolve) => {
      if (!socket || !connected) {
        resolve({
          success: false,
          error: {
            success: false,
            error: "Not connected to server",
            error_code: "CONNECTION_ERROR",
            severity: "high",
            retry_allowed: true,
            timestamp: new Date()
          }
        })
        return
      }

      const timeoutMs = 5000 // 5 second timeout
      let isResolved = false

      // Set up response listeners
      const responseHandler = (data: TResponse) => {
        if (isResolved) return
        isResolved = true
        
        socket.off('command_response', responseHandler)
        socket.off('command_error', errorHandler)
        clearTimeout(timeoutId)
        
        resolve({ success: true, data })
      }

      const errorHandler = (error: WebCommandError) => {
        if (isResolved) return
        isResolved = true
        
        socket.off('command_response', responseHandler)
        socket.off('command_error', errorHandler)
        clearTimeout(timeoutId)
        
        resolve({ success: false, error })
      }

      // Set up timeout
      const timeoutId = setTimeout(() => {
        if (isResolved) return
        isResolved = true
        
        socket.off('command_response', responseHandler)
        socket.off('command_error', errorHandler)
        
        resolve({
          success: false,
          error: {
            success: false,
            error: "Command timeout",
            error_code: "TIMEOUT_ERROR",
            severity: "medium",
            retry_allowed: true,
            timestamp: new Date()
          }
        })
      }, timeoutMs)

      socket.on('command_response', responseHandler)
      socket.on('command_error', errorHandler)

      // Send the command with validation
      try {
        socket.emit(eventName, command)
        console.log(`📤 Sent ${eventName}:`, command)
      } catch (error) {
        if (isResolved) return
        isResolved = true
        
        socket.off('command_response', responseHandler)
        socket.off('command_error', errorHandler)
        clearTimeout(timeoutId)
        
        resolve({
          success: false,
          error: {
            success: false,
            error: `Failed to send command: ${error}`,
            error_code: "SEND_ERROR",
            severity: "medium",
            retry_allowed: true,
            timestamp: new Date()
          }
        })
      }
    })
  }

  // Typed command senders
  const sendVoiceCommand = async (
    action: 'start' | 'stop', 
    text?: string
  ): Promise<CommandResult<VoiceCommandResponse>> => {
    const command: VoiceCommand = {
      action,
      text,
      timestamp: new Date(),
      source: 'web_dashboard'
    }
    return sendCommand<VoiceCommandResponse>('voice_command', command)
  }

  const sendMusicCommand = async (
    action: 'play' | 'pause' | 'resume' | 'stop' | 'next' | 'queue',
    options?: {
      track_name?: string
      track_id?: string  
      volume?: number
    }
  ): Promise<CommandResult<MusicCommandResponse>> => {
    const command: MusicCommand = {
      action,
      track_name: options?.track_name,
      track_id: options?.track_id,
      volume: options?.volume,
      timestamp: new Date(),
      source: 'web_dashboard'
    }
    return sendCommand<MusicCommandResponse>('music_command', command)
  }

  const sendDJCommand = async (
    action: 'start' | 'stop' | 'next',
    options?: {
      auto_transition?: boolean
      transition_interval?: number
    }
  ): Promise<CommandResult<DJCommandResponse>> => {
    const command: DJCommand = {
      action,
      auto_transition: options?.auto_transition,
      transition_interval: options?.transition_interval,
      timestamp: new Date(),
      source: 'web_dashboard'
    }
    return sendCommand<DJCommandResponse>('dj_command', command)
  }

  const sendSystemCommand = async (
    action: 'set_mode' | 'restart' | 'refresh_config',
    options?: {
      mode?: 'IDLE' | 'AMBIENT' | 'INTERACTIVE'
    }
  ): Promise<CommandResult<SystemCommandResponse>> => {
    const command: SystemCommand = {
      action,
      mode: options?.mode,
      timestamp: new Date(),
      source: 'web_dashboard'
    }
    return sendCommand<SystemCommandResponse>('system_command', command)
  }

  return {
    socket,
    connected,
    lastError,
    // ... existing state exports ...
    sendVoiceCommand,
    sendMusicCommand,
    sendDJCommand,
    sendSystemCommand,
  }
}
```

### 5.4 Component Updates with Error Handling

**Step 23: Update VoiceTab with typed commands and error handling**
```typescript
// dj-r3x-dashboard/src/components/tabs/VoiceTab.tsx
'use client'

import { useState } from 'react'
import { useTypedSocket } from '../../hooks/useTypedSocket'
import { WebCommandError } from '../../types/generated-schemas'

export default function VoiceTab() {
  const { connected, voiceStatus, sendVoiceCommand, lastError } = useTypedSocket()
  const [isLoading, setIsLoading] = useState(false)
  const [commandError, setCommandError] = useState<WebCommandError | null>(null)

  const handleVoiceAction = async (action: 'start' | 'stop') => {
    setIsLoading(true)
    setCommandError(null)
    
    try {
      const result = await sendVoiceCommand(action)
      
      if (result.success) {
        console.log('✅ Voice command successful:', result.data)
      } else {
        console.error('❌ Voice command failed:', result.error)
        setCommandError(result.error || null)
      }
    } catch (error) {
      console.error('❌ Unexpected error:', error)
      setCommandError({
        success: false,
        error: 'Unexpected error occurred',
        error_code: 'UNEXPECTED_ERROR',
        severity: 'high',
        retry_allowed: true,
        timestamp: new Date()
      })
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <div className="space-y-6">
      {/* Error Display */}
      {(commandError || lastError) && (
        <div className="bg-red-900/20 border border-red-500/30 rounded-lg p-4">
          <div className="flex items-center space-x-2">
            <span className="text-red-400">⚠️</span>
            <h3 className="text-red-300 font-medium">Command Error</h3>
          </div>
          <p className="text-red-200 mt-2">
            {(commandError || lastError)?.error}
          </p>
          {(commandError || lastError)?.validation_errors && (
            <div className="mt-2 text-sm text-red-300">
              <p>Validation errors:</p>
              <ul className="list-disc list-inside ml-4">
                {(commandError || lastError)?.validation_errors?.map((err, idx) => (
                  <li key={idx}>
                    <strong>{err.field}:</strong> {err.message}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {(commandError || lastError)?.retry_allowed && (
            <button
              onClick={() => setCommandError(null)}
              className="mt-3 px-3 py-1 bg-red-600 hover:bg-red-700 rounded text-white text-sm"
            >
              Dismiss
            </button>
          )}
        </div>
      )}

      {/* Voice Controls */}
      <div className="bg-gray-800/50 rounded-lg p-6">
        <h2 className="text-xl font-bold text-blue-300 mb-4">Voice Control</h2>
        
        <div className="flex space-x-4">
          <button
            onClick={() => handleVoiceAction('start')}
            disabled={!connected || isLoading || voiceStatus.status === 'recording'}
            className={`px-6 py-3 rounded-lg font-medium transition-all ${
              voiceStatus.status === 'recording'
                ? 'bg-red-600 text-white'
                : 'bg-blue-600 hover:bg-blue-700 text-white disabled:opacity-50'
            }`}
          >
            {isLoading ? '⏳ Starting...' : '🎤 Start Recording'}
          </button>
          
          <button
            onClick={() => handleVoiceAction('stop')}
            disabled={!connected || isLoading || voiceStatus.status === 'idle'}
            className="px-6 py-3 bg-red-600 hover:bg-red-700 text-white rounded-lg font-medium disabled:opacity-50 transition-all"
          >
            {isLoading ? '⏳ Stopping...' : '⏹️ Stop Recording'}
          </button>
        </div>

        {/* Status Display */}
        <div className="mt-4 p-3 bg-gray-700/50 rounded">
          <p className="text-sm text-gray-300">
            Status: <span className={`font-medium ${
              voiceStatus.status === 'recording' ? 'text-red-400' :
              voiceStatus.status === 'processing' ? 'text-yellow-400' :
              'text-green-400'
            }`}>
              {voiceStatus.status.toUpperCase()}
            </span>
          </p>
        </div>
      </div>
    </div>
  )
}
```

### 5.5 Frontend Testing

**Step 24: Test typed interface generation**
```bash
# Generate schemas and test compilation
cd dj-r3x-dashboard
npm run generate-schemas
npm run build

# Check for TypeScript compilation errors
npx tsc --noEmit
```

**Step 25: Test runtime behavior**
```bash
# Start dashboard in development mode
npm run dev

# In browser console, test command validation:
# Should see proper TypeScript intellisense and runtime validation
```

## 6. Phase 5: Testing & Optimization (Week 5)

### 6.1 Comprehensive Integration Testing

**Step 26: End-to-end integration tests**
```python
# cantina_os/tests/integration/test_schema_system_e2e.py
import pytest
import asyncio
from unittest.mock import AsyncMock
from cantina_os.services.web_bridge_service import WebBridgeService
from cantina_os.schemas.web_commands import VoiceCommand, MusicCommand

class TestSchemaSystemE2E:
    """End-to-end tests for the complete schema validation system."""
    
    @pytest.fixture
    async def running_web_bridge(self):
        """Create and start a WebBridge service."""
        mock_event_bus = AsyncMock()
        config = {"host": "127.0.0.1", "port": 8001}  # Use different port for testing
        
        service = WebBridgeService(mock_event_bus, config)
        service._sio = AsyncMock()
        
        await service._start()
        return service

    async def test_voice_command_complete_flow(self, running_web_bridge):
        """Test complete voice command flow from validation to execution."""
        # Simulate receiving a voice command from Socket.IO
        mock_sid = "integration_test_client"
        command_data = {"action": "start", "text": "Test voice input"}
        
        # The validation decorator should handle this
        validated_command = VoiceCommand(**command_data)
        await running_web_bridge.voice_command(mock_sid, validated_command)
        
        # Verify CantinaOS event was emitted
        running_web_bridge._event_bus.emit.assert_called_once()
        call_args = running_web_bridge._event_bus.emit.call_args
        assert call_args[0][0] == "SYSTEM_SET_MODE_REQUEST"  # Event topic
        assert call_args[0][1]["mode"] == "INTERACTIVE"       # Payload
        
        # Verify success response was sent
        running_web_bridge._sio.emit.assert_called_with(
            "command_response",
            pytest.any(dict),
            room=mock_sid
        )

    async def test_invalid_command_validation_flow(self, running_web_bridge):
        """Test validation error handling for invalid commands."""
        mock_sid = "integration_test_client"
        
        # This should trigger validation error before reaching handler
        invalid_data = {"action": "invalid_action", "extra_field": "not_allowed"}
        
        # Mock the validation decorator behavior
        try:
            VoiceCommand(**invalid_data)
            assert False, "Should have raised validation error"
        except Exception as e:
            # Simulate validation middleware error handling
            await running_web_bridge._send_validation_error(
                mock_sid, 
                "Command validation failed", 
                "VALIDATION_ERROR"
            )
        
        # Verify error response was sent
        running_web_bridge._sio.emit.assert_called_with(
            "command_error",
            pytest.any(dict), 
            room=mock_sid
        )

    async def test_multiple_command_types_validation(self, running_web_bridge):
        """Test validation works for all command types."""
        test_commands = [
            ("voice_command", VoiceCommand(action="start")),
            ("music_command", MusicCommand(action="play", track_name="test_track")),
            ("dj_command", DJCommand(action="start", auto_transition=True)),
            ("system_command", SystemCommand(action="set_mode", mode="INTERACTIVE"))
        ]
        
        mock_sid = "multi_command_test"
        
        for command_type, command in test_commands:
            # Reset mock for each command
            running_web_bridge._event_bus.emit.reset_mock()
            running_web_bridge._sio.emit.reset_mock()
            
            # Call appropriate handler based on command type
            if command_type == "voice_command":
                await running_web_bridge.voice_command(mock_sid, command)
            elif command_type == "music_command":
                await running_web_bridge.music_command(mock_sid, command)
            elif command_type == "dj_command":
                await running_web_bridge.dj_command(mock_sid, command)
            elif command_type == "system_command":
                await running_web_bridge.system_command(mock_sid, command)
            
            # Verify each command was processed
            running_web_bridge._event_bus.emit.assert_called_once()
            running_web_bridge._sio.emit.assert_called()
```

**Run comprehensive tests:**
```bash
cd cantina_os
python -m pytest tests/integration/test_schema_system_e2e.py -v
python -m pytest tests/schemas/ -v
python -m pytest tests/integration/ -v
```

### 6.2 Performance Testing

**Step 27: Performance benchmarks**
```python
# cantina_os/tests/performance/test_schema_validation_performance.py
import pytest
import time
import statistics
from cantina_os.schemas.web_commands import VoiceCommand, MusicCommand

class TestSchemaValidationPerformance:
    """Performance tests for schema validation."""
    
    def test_validation_performance(self):
        """Test validation performance under load."""
        # Test data
        valid_voice_data = {"action": "start", "text": "Performance test"}
        valid_music_data = {"action": "play", "track_name": "Test Track", "volume": 0.8}
        
        # Warm up
        for _ in range(100):
            VoiceCommand(**valid_voice_data)
            MusicCommand(**valid_music_data)
        
        # Measure voice command validation
        voice_times = []
        for _ in range(1000):
            start_time = time.perf_counter()
            VoiceCommand(**valid_voice_data)
            end_time = time.perf_counter()
            voice_times.append(end_time - start_time)
        
        # Measure music command validation
        music_times = []
        for _ in range(1000):
            start_time = time.perf_counter()
            MusicCommand(**valid_music_data)
            end_time = time.perf_counter()
            music_times.append(end_time - start_time)
        
        # Performance assertions (should be under 1ms average)
        voice_avg = statistics.mean(voice_times)
        music_avg = statistics.mean(music_times)
        
        print(f"Voice command validation avg: {voice_avg*1000:.3f}ms")
        print(f"Music command validation avg: {music_avg*1000:.3f}ms")
        
        assert voice_avg < 0.001, f"Voice validation too slow: {voice_avg*1000:.3f}ms"
        assert music_avg < 0.001, f"Music validation too slow: {music_avg*1000:.3f}ms"

    def test_typescript_generation_performance(self):
        """Test TypeScript generation performance."""
        from cantina_os.schemas.generators.typescript_generator import generate_typescript_interfaces
        
        start_time = time.perf_counter()
        typescript_content = generate_typescript_interfaces()
        end_time = time.perf_counter()
        
        generation_time = end_time - start_time
        content_lines = len(typescript_content.splitlines())
        
        print(f"TypeScript generation time: {generation_time:.3f}s")
        print(f"Generated {content_lines} lines")
        
        # Should generate TypeScript in reasonable time
        assert generation_time < 5.0, f"TypeScript generation too slow: {generation_time:.3f}s"
        assert content_lines > 50, f"Generated content too small: {content_lines} lines"
```

### 6.3 Error Handling Edge Cases

**Step 28: Edge case testing**
```python
# cantina_os/tests/integration/test_error_handling_edge_cases.py
import pytest
from unittest.mock import AsyncMock, patch
from cantina_os.services.web_bridge_service import WebBridgeService

class TestErrorHandlingEdgeCases:
    """Test error handling edge cases and recovery scenarios."""
    
    @pytest.fixture
    async def web_bridge_with_failures(self):
        """Create WebBridge with simulated failures."""
        mock_event_bus = AsyncMock()
        config = {"host": "127.0.0.1", "port": 8002}
        
        service = WebBridgeService(mock_event_bus, config)
        service._sio = AsyncMock()
        
        return service

    async def test_event_bus_connection_failure(self, web_bridge_with_failures):
        """Test handling when event bus is disconnected."""
        # Simulate event bus failure
        web_bridge_with_failures._event_bus.emit.side_effect = ConnectionError("Event bus disconnected")
        
        from cantina_os.schemas.web_commands import VoiceCommand
        command = VoiceCommand(action="start")
        
        # Should handle gracefully without crashing
        await web_bridge_with_failures.voice_command("test_sid", command)
        
        # Should send error response
        web_bridge_with_failures._sio.emit.assert_called_with(
            "command_error",
            pytest.any(dict),
            room="test_sid"
        )

    async def test_schema_validation_with_malformed_data(self, web_bridge_with_failures):
        """Test validation with various malformed data types."""
        malformed_inputs = [
            None,
            "",
            "not_a_dict",
            {"action": None},
            {"action": 123},
            {"action": "start", "timestamp": "invalid_date"},
            {"action": "start", "extra_unknown_field": "value"},
        ]
        
        for malformed_input in malformed_inputs:
            with pytest.raises(Exception):
                # Should raise validation error for any malformed input
                from cantina_os.schemas.web_commands import VoiceCommand
                VoiceCommand(**malformed_input) if isinstance(malformed_input, dict) else VoiceCommand(malformed_input)

    async def test_concurrent_command_validation(self, web_bridge_with_failures):
        """Test validation under concurrent load."""
        import asyncio
        from cantina_os.schemas.web_commands import VoiceCommand, MusicCommand
        
        async def validate_command(command_class, data):
            """Validate a command concurrently."""
            try:
                return command_class(**data)
            except Exception as e:
                return e
        
        # Create many concurrent validation tasks
        tasks = []
        for i in range(100):
            if i % 2 == 0:
                tasks.append(validate_command(VoiceCommand, {"action": "start"}))
            else:
                tasks.append(validate_command(MusicCommand, {"action": "play", "track_name": f"track_{i}"}))
        
        # Run all validations concurrently
        results = await asyncio.gather(*tasks)
        
        # All should succeed
        for result in results:
            assert not isinstance(result, Exception), f"Validation failed: {result}"
```

### 6.4 Documentation and Final Testing

**Step 29: Update documentation**
```bash
# Update main README with schema system information
# Update ARCHITECTURE_STANDARDS.md with new validation patterns
# Create schema system usage examples
```

**Step 30: Final integration test**
```bash
# Test complete system startup with schemas
./start-dashboard.sh

# Test schema generation
./scripts/generate-schemas.sh

# Test dashboard functionality with validation
# Navigate to http://localhost:3000 and test all command types

# Run full test suite
cd cantina_os
python -m pytest tests/ -v --cov=cantina_os.schemas

# Test TypeScript compilation
cd ../dj-r3x-dashboard  
npm run build
```

## 7. Deployment and Monitoring

### 7.1 Production Deployment Steps

**Step 31: Production build preparation**
```bash
# Ensure schemas are generated in production build
echo "Checking production build process..."

# Test production build
cd dj-r3x-dashboard
npm run build

# Verify generated schemas exist
ls -la src/types/generated-schemas.ts

# Test CantinaOS with schemas
cd ../cantina_os
python -m pytest tests/schemas/ --no-cov
```

### 7.2 Monitoring and Metrics

**Step 32: Add schema validation metrics**
```python
# Add to cantina_os/cantina_os/schemas/validators/socketio_validator.py

import time
from collections import defaultdict, deque
from datetime import datetime, timedelta

class ValidationMetrics:
    """Track validation performance and error rates."""
    
    def __init__(self):
        self.validation_times = defaultdict(deque)
        self.validation_errors = defaultdict(int)
        self.total_validations = defaultdict(int)
        
    def record_validation(self, command_type: str, validation_time_ms: float, success: bool):
        """Record validation metrics."""
        self.total_validations[command_type] += 1
        self.validation_times[command_type].append(validation_time_ms)
        
        # Keep only last 1000 entries
        if len(self.validation_times[command_type]) > 1000:
            self.validation_times[command_type].popleft()
            
        if not success:
            self.validation_errors[command_type] += 1
    
    def get_metrics(self, command_type: str = None) -> dict:
        """Get validation metrics."""
        if command_type:
            times = list(self.validation_times[command_type])
            return {
                "command_type": command_type,
                "total_validations": self.total_validations[command_type],
                "total_errors": self.validation_errors[command_type],
                "error_rate": self.validation_errors[command_type] / max(1, self.total_validations[command_type]),
                "avg_validation_time_ms": sum(times) / len(times) if times else 0,
                "max_validation_time_ms": max(times) if times else 0
            }
        else:
            return {command_type: self.get_metrics(command_type) for command_type in self.total_validations}

# Global metrics instance
validation_metrics = ValidationMetrics()
```

## 8. Success Criteria and Verification

### 8.1 Functional Requirements Verification

**✅ Centralized Command API Schema**
- [ ] All Socket.IO commands use Pydantic validation
- [ ] Schema registry accessible from all services
- [ ] Command-to-event conversion methods implemented

**✅ TypeScript Schema Generation**
- [ ] Generated interfaces match Pydantic schemas
- [ ] Build integration generates schemas automatically
- [ ] Type safety end-to-end verified

**✅ Socket.IO Validation Middleware**
- [ ] All handlers use validation decorators
- [ ] Proper error responses for invalid commands
- [ ] Performance within acceptable limits (<1ms validation)

**✅ Type-Safe Frontend**
- [ ] Dashboard uses generated TypeScript interfaces
- [ ] Proper error handling and user feedback
- [ ] No runtime type errors in browser console

**✅ Proper Error Handling**
- [ ] Standardized error response format
- [ ] Validation errors sent to dashboard
- [ ] ServiceStatusPayload compliance maintained

### 8.2 Architecture Compliance Verification

**✅ CantinaOS Event-Driven Patterns**
- [ ] Event Bus Topology followed correctly
- [ ] No service architecture violations
- [ ] Proper event topic usage

**✅ Service Integration Standards**
- [ ] WebBridge follows WEB_DASHBOARD_STANDARDS.md
- [ ] Event translation preserves CantinaOS patterns
- [ ] Service status reporting maintained

### 8.3 Performance and Reliability Verification

**✅ Performance Metrics**
- [ ] Schema validation <1ms average
- [ ] TypeScript generation <5s
- [ ] No memory leaks in validation
- [ ] Concurrent validation handles 100+ requests

**✅ Error Reduction**
- [ ] 90% reduction in command processing errors
- [ ] Proper error recovery mechanisms
- [ ] Comprehensive error logging

## 9. Rollback Plan

If issues arise during implementation:

### 9.1 Phase-by-Phase Rollback
1. **Phase 5 Issues**: Disable performance optimizations, use basic validation
2. **Phase 4 Issues**: Revert to original useSocket hook, keep backend validation
3. **Phase 3 Issues**: Disable validation decorators, use basic error handling
4. **Phase 2 Issues**: Remove validation middleware, keep schemas for future use
5. **Phase 1 Issues**: Remove schema module, revert to original manual validation

### 9.2 Emergency Rollback
```bash
# Quick rollback script
git checkout HEAD~1 cantina_os/cantina_os/services/web_bridge_service.py
git checkout HEAD~1 dj-r3x-dashboard/src/hooks/useSocket.ts
# Remove schema imports and validation decorators
```

## 10. Conclusion

This implementation plan provides a systematic approach to deploying the centralized schema system while maintaining system stability and following CantinaOS architecture standards. The phased approach allows for validation at each step and provides clear rollback options if issues arise.

**Key Implementation Principles:**
- **Incremental deployment** with validation at each phase
- **Backward compatibility** during transition period  
- **Comprehensive testing** before proceeding to next phase
- **Clear success criteria** for each deliverable
- **Detailed rollback plan** for risk mitigation

The implementation should result in a robust, type-safe command validation system that significantly improves the reliability and developer experience of the CantinaOS web dashboard integration.