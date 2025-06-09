# Centralized Pydantic Schema System Design

## 1. Executive Summary

This document outlines the design for a comprehensive centralized Pydantic schema system for CantinaOS web dashboard integration. The system addresses critical command validation gaps by providing unified schemas, automatic TypeScript generation, Socket.IO validation middleware, and proper error handling that follows CantinaOS architecture standards.

## 2. Current State Analysis

### 2.1 Identified Issues

**Command Validation Gaps**:
- WebBridge Socket.IO handlers lack input validation
- Manual type checking scattered across handlers
- Inconsistent payload structures between web and CantinaOS
- Silent failures when commands have invalid format
- No TypeScript interface generation from backend schemas

**Architecture Compliance Issues**:
- Event topic translation not standardized
- Error responses don't follow ServiceStatusPayload patterns
- Validation logic not centralized or reusable
- Type safety gaps between frontend and backend

### 2.2 Current Command Flow
```
Dashboard → Socket.IO → Manual Validation → WebBridge → CantinaOS Events
    ↑          ↑              ↑             ↑              ↑
   TypeScript  Ad-hoc      Inconsistent  Manual Topic   Pydantic
   Interfaces  Checking    Error Handling Translation   Payloads
```

## 3. Proposed Architecture

### 3.1 System Overview

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   TypeScript    │    │   Socket.IO     │    │   Pydantic      │    │   CantinaOS     │
│   Interfaces    │◄───│   Validation    │◄───│   Command       │◄───│   Event Bus     │
│   (Generated)   │    │   Middleware    │    │   Schemas       │    │   (Existing)    │
└─────────────────┘    └─────────────────┘    └─────────────────┘    └─────────────────┘
        ▲                        ▲                        ▲                        ▲
        │                        │                        │                        │
  ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
  │   Dashboard     │    │   WebBridge     │    │   Schema        │    │   Service       │
  │   Components    │    │   Service       │    │   Registry      │    │   Handlers      │
  └─────────────────┘    └─────────────────┘    └─────────────────┘    └─────────────────┘
```

### 3.2 Core Components

1. **Schema Registry** (`cantina_os/cantina_os/schemas/`)
2. **Socket.IO Validation Middleware** 
3. **TypeScript Generator** 
4. **Error Response System**
5. **WebBridge Integration Layer**

## 4. Component Design Details

### 4.1 Schema Registry Structure

```
cantina_os/cantina_os/schemas/
├── __init__.py                    # Schema registry and exports
├── base.py                        # Base schema classes
├── web_commands/                  # Web dashboard command schemas
│   ├── __init__.py
│   ├── voice_commands.py          # Voice control schemas
│   ├── music_commands.py          # Music control schemas
│   ├── dj_commands.py             # DJ mode schemas
│   └── system_commands.py         # System mode schemas
├── responses/                     # Response payload schemas
│   ├── __init__.py
│   ├── command_responses.py       # Command response schemas
│   └── error_responses.py         # Error response schemas
└── generators/                    # Code generation utilities
    ├── __init__.py
    ├── typescript_generator.py    # TypeScript interface generator
    └── schema_validator.py        # Runtime validation helpers
```

### 4.2 Base Schema Classes

```python
# cantina_os/cantina_os/schemas/base.py
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, Optional, Union
from pydantic import BaseModel, Field
from enum import Enum

class WebCommandType(str, Enum):
    """Enumeration of web command types."""
    VOICE_COMMAND = "voice_command"
    MUSIC_COMMAND = "music_command"
    DJ_COMMAND = "dj_command"
    SYSTEM_COMMAND = "system_command"

class BaseWebCommand(BaseModel, ABC):
    """Base class for all web dashboard commands."""
    action: str = Field(..., description="The action to perform")
    timestamp: Optional[datetime] = Field(default_factory=datetime.now, description="Command timestamp")
    source: str = Field(default="web_dashboard", description="Command source identifier")
    
    @abstractmethod
    def to_cantina_event(self) -> tuple[str, Dict[str, Any]]:
        """Convert web command to CantinaOS event topic and payload."""
        pass
    
    class Config:
        # Allow extra fields for future extensibility
        extra = "forbid"
        # Generate JSON schema for TypeScript generation
        schema_extra = {
            "required": ["action"],
            "additionalProperties": False
        }

class BaseWebResponse(BaseModel):
    """Base class for all web command responses."""
    success: bool = Field(..., description="Whether the command succeeded")
    message: str = Field(..., description="Human-readable response message")
    timestamp: datetime = Field(default_factory=datetime.now, description="Response timestamp")
    data: Optional[Dict[str, Any]] = Field(None, description="Additional response data")

class WebCommandError(BaseModel):
    """Standardized error response for web commands."""
    success: bool = Field(default=False, description="Always false for errors")
    error: str = Field(..., description="Error message")
    error_code: str = Field(..., description="Machine-readable error code")
    command: Optional[str] = Field(None, description="The command that failed")
    validation_errors: Optional[Dict[str, str]] = Field(None, description="Field-specific validation errors")
    timestamp: datetime = Field(default_factory=datetime.now, description="Error timestamp")
```

### 4.3 Voice Command Schemas

```python
# cantina_os/cantina_os/schemas/web_commands/voice_commands.py
from typing import Literal, Optional
from pydantic import Field, validator
from ..base import BaseWebCommand, BaseWebResponse
from ...core.event_topics import EventTopics

class VoiceCommand(BaseWebCommand):
    """Schema for voice control commands from web dashboard."""
    action: Literal["start", "stop"] = Field(..., description="Voice action to perform")
    text: Optional[str] = Field(None, description="Optional text for text-to-speech")
    
    def to_cantina_event(self) -> tuple[str, dict]:
        """Convert to CantinaOS event."""
        if self.action == "start":
            return (
                EventTopics.SYSTEM_SET_MODE_REQUEST,
                {
                    "mode": "INTERACTIVE",
                    "source": self.source,
                    "timestamp": self.timestamp.isoformat()
                }
            )
        elif self.action == "stop":
            return (
                EventTopics.SYSTEM_SET_MODE_REQUEST,
                {
                    "mode": "AMBIENT", 
                    "source": self.source,
                    "timestamp": self.timestamp.isoformat()
                }
            )
    
    @validator("action")
    def validate_action(cls, v):
        if v not in ["start", "stop"]:
            raise ValueError(f"Invalid voice action: {v}")
        return v

class VoiceCommandResponse(BaseWebResponse):
    """Response schema for voice commands."""
    voice_status: Literal["recording", "processing", "idle"] = Field(..., description="Current voice status")
```

### 4.4 Music Command Schemas

```python
# cantina_os/cantina_os/schemas/web_commands/music_commands.py
from typing import Literal, Optional, Union
from pydantic import Field, validator
from ..base import BaseWebCommand, BaseWebResponse
from ...core.event_topics import EventTopics

class MusicCommand(BaseWebCommand):
    """Schema for music control commands from web dashboard."""
    action: Literal["play", "pause", "resume", "stop", "next", "queue"] = Field(
        ..., description="Music action to perform"
    )
    track_name: Optional[str] = Field(None, description="Track name or ID to play")
    track_id: Optional[str] = Field(None, description="Specific track identifier")
    volume: Optional[float] = Field(None, ge=0.0, le=1.0, description="Volume level (0.0-1.0)")
    
    def to_cantina_event(self) -> tuple[str, dict]:
        """Convert to CantinaOS music command event."""
        payload = {
            "action": self.action,
            "source": self.source,
            "conversation_id": None,  # Web commands don't have conversation context
            "timestamp": self.timestamp.isoformat()
        }
        
        # Add track information if provided
        if self.track_name or self.track_id:
            payload["song_query"] = self.track_name or self.track_id
            
        if self.volume is not None:
            payload["volume"] = self.volume
            
        return (EventTopics.MUSIC_COMMAND, payload)
    
    @validator("action")
    def validate_action(cls, v):
        valid_actions = ["play", "pause", "resume", "stop", "next", "queue"]
        if v not in valid_actions:
            raise ValueError(f"Invalid music action: {v}. Must be one of {valid_actions}")
        return v
    
    @validator("track_name", "track_id")
    def validate_track_requirement(cls, v, values):
        """Validate track requirement for play/queue actions."""
        action = values.get("action")
        if action in ["play", "queue"] and not v and not values.get("track_id"):
            raise ValueError(f"Action '{action}' requires either track_name or track_id")
        return v

class MusicCommandResponse(BaseWebResponse):
    """Response schema for music commands."""
    current_track: Optional[dict] = Field(None, description="Currently playing track info")
    queue_length: Optional[int] = Field(None, description="Number of tracks in queue")
    playback_status: Literal["playing", "paused", "stopped"] = Field(..., description="Current playback status")
```

### 4.5 DJ Command Schemas

```python
# cantina_os/cantina_os/schemas/web_commands/dj_commands.py
from typing import Literal, Optional
from pydantic import Field, validator
from ..base import BaseWebCommand, BaseWebResponse
from ...core.event_topics import EventTopics

class DJCommand(BaseWebCommand):
    """Schema for DJ mode commands from web dashboard."""
    action: Literal["start", "stop", "next"] = Field(..., description="DJ action to perform")
    auto_transition: Optional[bool] = Field(True, description="Enable automatic track transitions")
    transition_interval: Optional[int] = Field(None, ge=30, le=600, description="Transition interval in seconds")
    
    def to_cantina_event(self) -> tuple[str, dict]:
        """Convert to CantinaOS DJ command event."""
        if self.action == "start":
            return (
                EventTopics.DJ_COMMAND,
                {
                    "command": "dj start",
                    "auto_transition": self.auto_transition,
                    "source": self.source,
                    "timestamp": self.timestamp.isoformat()
                }
            )
        elif self.action == "stop":
            return (
                EventTopics.DJ_COMMAND,
                {
                    "command": "dj stop",
                    "source": self.source,
                    "timestamp": self.timestamp.isoformat()
                }
            )
        elif self.action == "next":
            return (
                EventTopics.DJ_NEXT_TRACK,
                {
                    "source": self.source,
                    "timestamp": self.timestamp.isoformat()
                }
            )
    
    @validator("action")
    def validate_action(cls, v):
        if v not in ["start", "stop", "next"]:
            raise ValueError(f"Invalid DJ action: {v}")
        return v

class DJCommandResponse(BaseWebResponse):
    """Response schema for DJ commands."""
    dj_status: Literal["active", "inactive"] = Field(..., description="Current DJ mode status")
    auto_transition: bool = Field(..., description="Auto transition enabled status")
```

### 4.6 System Command Schemas

```python
# cantina_os/cantina_os/schemas/web_commands/system_commands.py
from typing import Literal, Optional
from pydantic import Field, validator
from ..base import BaseWebCommand, BaseWebResponse
from ...core.event_topics import EventTopics

class SystemCommand(BaseWebCommand):
    """Schema for system control commands from web dashboard."""
    action: Literal["set_mode", "restart", "refresh_config"] = Field(..., description="System action to perform")
    mode: Optional[Literal["IDLE", "AMBIENT", "INTERACTIVE"]] = Field(None, description="System mode to set")
    
    def to_cantina_event(self) -> tuple[str, dict]:
        """Convert to CantinaOS system event."""
        if self.action == "set_mode":
            if not self.mode:
                raise ValueError("Mode is required for set_mode action")
            return (
                EventTopics.SYSTEM_SET_MODE_REQUEST,
                {
                    "mode": self.mode,
                    "source": self.source,
                    "timestamp": self.timestamp.isoformat()
                }
            )
        elif self.action == "restart":
            return (
                EventTopics.SYSTEM_SHUTDOWN_REQUESTED,
                {
                    "restart": True,
                    "source": self.source,
                    "timestamp": self.timestamp.isoformat()
                }
            )
        elif self.action == "refresh_config":
            return (
                "CONFIG_REFRESH_REQUEST",
                {
                    "source": self.source,
                    "timestamp": self.timestamp.isoformat()
                }
            )
    
    @validator("mode")
    def validate_mode_requirement(cls, v, values):
        """Validate mode requirement for set_mode action."""
        action = values.get("action")
        if action == "set_mode" and not v:
            raise ValueError("Mode is required when action is 'set_mode'")
        return v

class SystemCommandResponse(BaseWebResponse):
    """Response schema for system commands."""
    current_mode: Literal["IDLE", "AMBIENT", "INTERACTIVE"] = Field(..., description="Current system mode")
    services_status: dict = Field(..., description="Current service status overview")
```

### 4.7 Socket.IO Validation Middleware

```python
# cantina_os/cantina_os/schemas/validators/socketio_validator.py
import functools
import logging
from typing import Any, Callable, Dict, Type, Union
from pydantic import ValidationError
from ..base import BaseWebCommand, WebCommandError
from ..web_commands import VoiceCommand, MusicCommand, DJCommand, SystemCommand

logger = logging.getLogger(__name__)

# Command registry mapping Socket.IO events to schema classes
COMMAND_SCHEMA_REGISTRY: Dict[str, Type[BaseWebCommand]] = {
    "voice_command": VoiceCommand,
    "music_command": MusicCommand,
    "dj_command": DJCommand,
    "system_command": SystemCommand,
}

def validate_socketio_command(command_type: str):
    """
    Decorator for Socket.IO event handlers that validates incoming commands.
    
    Args:
        command_type: The type of command (must be in COMMAND_SCHEMA_REGISTRY)
    
    Returns:
        Decorated handler function with automatic validation
    """
    def decorator(handler_func: Callable) -> Callable:
        @functools.wraps(handler_func)
        async def wrapper(self, sid: str, data: Dict[str, Any]):
            try:
                # Get the appropriate schema class
                schema_class = COMMAND_SCHEMA_REGISTRY.get(command_type)
                if not schema_class:
                    await self._send_validation_error(
                        sid, f"Unknown command type: {command_type}", "UNKNOWN_COMMAND_TYPE"
                    )
                    return
                
                # Validate the incoming data
                try:
                    validated_command = schema_class(**data)
                except ValidationError as e:
                    await self._send_validation_error(
                        sid, "Command validation failed", "VALIDATION_ERROR", 
                        validation_errors=self._format_validation_errors(e)
                    )
                    return
                
                # Log successful validation
                logger.info(f"✅ Command validated: {command_type} from {sid}")
                
                # Call the original handler with validated data
                return await handler_func(self, sid, validated_command)
                
            except Exception as e:
                logger.error(f"❌ Validation middleware error for {command_type}: {e}")
                await self._send_validation_error(
                    sid, f"Internal validation error: {str(e)}", "INTERNAL_ERROR"
                )
                
        return wrapper
    return decorator

class SocketIOValidationMixin:
    """Mixin class for WebBridge service to add validation capabilities."""
    
    async def _send_validation_error(
        self, 
        sid: str, 
        message: str, 
        error_code: str,
        command: str = None,
        validation_errors: Dict[str, str] = None
    ):
        """Send standardized validation error to client."""
        error_response = WebCommandError(
            error=message,
            error_code=error_code,
            command=command,
            validation_errors=validation_errors
        )
        
        await self._sio.emit("command_error", error_response.dict(), room=sid)
        logger.warning(f"🚫 Sent validation error to {sid}: {error_code} - {message}")
    
    def _format_validation_errors(self, validation_error: ValidationError) -> Dict[str, str]:
        """Format Pydantic validation errors for client consumption."""
        formatted_errors = {}
        for error in validation_error.errors():
            field_path = ".".join(str(loc) for loc in error["loc"])
            formatted_errors[field_path] = error["msg"]
        return formatted_errors
```

### 4.8 TypeScript Generator

```python
# cantina_os/cantina_os/schemas/generators/typescript_generator.py
import json
import os
from pathlib import Path
from typing import Dict, Any, List
from pydantic import BaseModel
from pydantic.schema import schema
from ..base import BaseWebCommand, BaseWebResponse, WebCommandError
from ..web_commands import VoiceCommand, MusicCommand, DJCommand, SystemCommand

def generate_typescript_interfaces(output_path: str = None) -> str:
    """
    Generate TypeScript interfaces from Pydantic schemas.
    
    Args:
        output_path: Optional path to write TypeScript file
        
    Returns:
        Generated TypeScript interface definitions
    """
    # Collect all schema classes
    schemas_to_export = [
        ("VoiceCommand", VoiceCommand),
        ("MusicCommand", MusicCommand), 
        ("DJCommand", DJCommand),
        ("SystemCommand", SystemCommand),
        ("BaseWebResponse", BaseWebResponse),
        ("WebCommandError", WebCommandError),
    ]
    
    # Generate JSON schemas
    json_schemas = {}
    for name, schema_class in schemas_to_export:
        json_schemas[name] = schema([schema_class], ref_template="{model}")
    
    # Convert to TypeScript interfaces
    typescript_content = _generate_typescript_header()
    
    for name, schema_class in schemas_to_export:
        interface_content = _json_schema_to_typescript(name, json_schemas[name])
        typescript_content += interface_content + "\n\n"
    
    # Add utility types and command union
    typescript_content += _generate_utility_types()
    
    # Write to file if path provided
    if output_path:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, 'w') as f:
            f.write(typescript_content)
    
    return typescript_content

def _generate_typescript_header() -> str:
    """Generate TypeScript file header with imports and comments."""
    return '''// Auto-generated TypeScript interfaces from CantinaOS Pydantic schemas
// DO NOT EDIT MANUALLY - This file is generated by typescript_generator.py

/**
 * CantinaOS Web Dashboard Command Interfaces
 * 
 * These interfaces are automatically generated from the Python Pydantic schemas
 * to ensure type safety between the frontend and backend.
 */

'''

def _json_schema_to_typescript(name: str, json_schema: Dict[str, Any]) -> str:
    """Convert JSON schema to TypeScript interface."""
    # Extract the specific schema for this model
    model_schema = json_schema["definitions"][name]
    
    interface_lines = [f"export interface {name} {{"]
    
    # Process properties
    properties = model_schema.get("properties", {})
    required_fields = set(model_schema.get("required", []))
    
    for field_name, field_schema in properties.items():
        field_type = _json_type_to_typescript(field_schema)
        optional_marker = "" if field_name in required_fields else "?"
        
        # Add field documentation if available
        description = field_schema.get("description")
        if description:
            interface_lines.append(f"  /** {description} */")
        
        interface_lines.append(f"  {field_name}{optional_marker}: {field_type};")
    
    interface_lines.append("}")
    
    return "\n".join(interface_lines)

def _json_type_to_typescript(field_schema: Dict[str, Any]) -> str:
    """Convert JSON schema field type to TypeScript type."""
    field_type = field_schema.get("type")
    
    if field_type == "string":
        # Handle enum/literal types
        enum_values = field_schema.get("enum")
        if enum_values:
            return " | ".join(f'"{value}"' for value in enum_values)
        return "string"
    
    elif field_type == "number":
        return "number"
    
    elif field_type == "integer":
        return "number"
    
    elif field_type == "boolean":
        return "boolean"
    
    elif field_type == "array":
        items_type = _json_type_to_typescript(field_schema.get("items", {}))
        return f"{items_type}[]"
    
    elif field_type == "object":
        return "Record<string, any>"
    
    else:
        # Handle refs and complex types
        if "$ref" in field_schema:
            ref_name = field_schema["$ref"].split("/")[-1]
            return ref_name
        return "any"

def _generate_utility_types() -> str:
    """Generate utility TypeScript types for command handling."""
    return '''// Utility types for command handling
export type WebCommand = VoiceCommand | MusicCommand | DJCommand | SystemCommand;

export interface CommandResult<T = any> {
  success: boolean;
  data?: T;
  error?: WebCommandError;
}

// Socket.IO event type mappings
export interface SocketEvents {
  voice_command: (command: VoiceCommand) => Promise<BaseWebResponse>;
  music_command: (command: MusicCommand) => Promise<BaseWebResponse>;
  dj_command: (command: DJCommand) => Promise<BaseWebResponse>;
  system_command: (command: SystemCommand) => Promise<BaseWebResponse>;
}

// Client-side command sender type
export interface CommandSender {
  sendVoiceCommand: (command: VoiceCommand) => Promise<CommandResult>;
  sendMusicCommand: (command: MusicCommand) => Promise<CommandResult>;
  sendDJCommand: (command: DJCommand) => Promise<CommandResult>;
  sendSystemCommand: (command: SystemCommand) => Promise<CommandResult>;
}'''

# Build script integration
def main():
    """Main function for running as script during build process."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Generate TypeScript interfaces from Pydantic schemas")
    parser.add_argument("--output", "-o", required=True, help="Output TypeScript file path")
    parser.add_argument("--watch", action="store_true", help="Watch for schema changes and regenerate")
    
    args = parser.parse_args()
    
    if args.watch:
        # TODO: Implement file watching for development
        print("Watch mode not implemented yet")
        return
    
    # Generate interfaces
    typescript_content = generate_typescript_interfaces(args.output)
    print(f"✅ Generated TypeScript interfaces: {args.output}")
    print(f"📊 Generated {len(typescript_content.splitlines())} lines")

if __name__ == "__main__":
    main()
```

## 5. WebBridge Service Integration

### 5.1 Updated WebBridge Handler Pattern

```python
# Enhanced WebBridge service with validation middleware
class WebBridgeService(BaseService, SocketIOValidationMixin):
    """Web Bridge Service with centralized schema validation."""
    
    def _add_socketio_handlers(self) -> None:
        """Add Socket.IO event handlers with validation."""
        
        @self._sio.event
        @validate_socketio_command("voice_command")
        async def voice_command(sid: str, validated_command: VoiceCommand):
            """Handle validated voice commands from dashboard."""
            try:
                # Convert to CantinaOS event using schema method
                event_topic, payload = validated_command.to_cantina_event()
                
                # Emit to CantinaOS event bus
                self._event_bus.emit(event_topic, payload)
                
                # Send success response
                response = VoiceCommandResponse(
                    success=True,
                    message=f"Voice command '{validated_command.action}' executed",
                    voice_status="processing" if validated_command.action == "start" else "idle"
                )
                await self._sio.emit("command_response", response.dict(), room=sid)
                
            except Exception as e:
                logger.error(f"Error executing voice command: {e}")
                await self._send_command_error(sid, str(e), "EXECUTION_ERROR")
        
        @self._sio.event
        @validate_socketio_command("music_command")
        async def music_command(sid: str, validated_command: MusicCommand):
            """Handle validated music commands from dashboard."""
            try:
                event_topic, payload = validated_command.to_cantina_event()
                self._event_bus.emit(event_topic, payload)
                
                response = MusicCommandResponse(
                    success=True,
                    message=f"Music command '{validated_command.action}' executed",
                    playback_status="playing" if validated_command.action == "play" else "stopped"
                )
                await self._sio.emit("command_response", response.dict(), room=sid)
                
            except Exception as e:
                await self._send_command_error(sid, str(e), "EXECUTION_ERROR")
    
    async def _send_command_error(self, sid: str, error_message: str, error_code: str):
        """Send standardized command error response."""
        error_response = WebCommandError(
            error=error_message,
            error_code=error_code
        )
        await self._sio.emit("command_error", error_response.dict(), room=sid)
```

## 6. Frontend Integration

### 6.1 Updated useSocket Hook

```typescript
// Enhanced useSocket hook with generated types
import { 
  VoiceCommand, 
  MusicCommand, 
  DJCommand, 
  SystemCommand,
  CommandResult,
  WebCommandError 
} from '../types/generated-schemas';

export const useSocket = () => {
  // ... existing state ...

  const sendCommand = async <T extends WebCommand>(
    eventName: string,
    command: T
  ): Promise<CommandResult> => {
    return new Promise((resolve) => {
      if (!socket) {
        resolve({
          success: false,
          error: {
            success: false,
            error: "Not connected to server",
            error_code: "CONNECTION_ERROR",
            timestamp: new Date()
          }
        });
        return;
      }

      // Set up response listeners
      const responseHandler = (data: any) => {
        socket.off('command_response', responseHandler);
        socket.off('command_error', errorHandler);
        resolve({ success: true, data });
      };

      const errorHandler = (error: WebCommandError) => {
        socket.off('command_response', responseHandler);
        socket.off('command_error', errorHandler);
        resolve({ success: false, error });
      };

      socket.on('command_response', responseHandler);
      socket.on('command_error', errorHandler);

      // Send the command
      socket.emit(eventName, command);
    });
  };

  const sendVoiceCommand = async (action: 'start' | 'stop', text?: string): Promise<CommandResult> => {
    const command: VoiceCommand = {
      action,
      text,
      timestamp: new Date(),
      source: 'web_dashboard'
    };
    return sendCommand('voice_command', command);
  };

  const sendMusicCommand = async (
    action: 'play' | 'pause' | 'resume' | 'stop' | 'next' | 'queue',
    trackName?: string,
    volume?: number
  ): Promise<CommandResult> => {
    const command: MusicCommand = {
      action,
      track_name: trackName,
      volume,
      timestamp: new Date(),
      source: 'web_dashboard'
    };
    return sendCommand('music_command', command);
  };

  // ... other methods with type safety ...
};
```

## 7. Build Integration

### 7.1 Schema Generation Script

```bash
#!/bin/bash
# scripts/generate-schemas.sh

echo "🔧 Generating TypeScript interfaces from Pydantic schemas..."

# Activate CantinaOS virtual environment
source cantina_os/venv/bin/activate

# Generate TypeScript interfaces
python -m cantina_os.schemas.generators.typescript_generator \
  --output dj-r3x-dashboard/src/types/generated-schemas.ts

# Verify generation success
if [ $? -eq 0 ]; then
  echo "✅ TypeScript interfaces generated successfully"
else
  echo "❌ Failed to generate TypeScript interfaces"
  exit 1
fi

# Format the generated TypeScript file
cd dj-r3x-dashboard
npm run format src/types/generated-schemas.ts

echo "📋 TypeScript interface generation complete"
```

### 7.2 Package.json Integration

```json
{
  "scripts": {
    "generate-schemas": "../scripts/generate-schemas.sh",
    "prebuild": "npm run generate-schemas",
    "predev": "npm run generate-schemas",
    "dev": "next dev",
    "build": "next build"
  }
}
```

## 8. Error Handling Strategy

### 8.1 Error Response Hierarchy

```
WebCommandError (Base)
├── ValidationError (Invalid request format)
├── AuthorizationError (Permission denied)
├── ExecutionError (CantinaOS processing failure)
├── ConnectionError (Event bus communication failure)
└── InternalError (Unexpected system error)
```

### 8.2 Error Response Mapping

```python
# Error mapping from CantinaOS ServiceStatus to web responses
ERROR_STATUS_MAPPING = {
    ServiceStatus.ERROR: {
        "error_code": "SERVICE_ERROR",
        "retry_allowed": True,
        "escalation_level": "warning"
    },
    ServiceStatus.STOPPED: {
        "error_code": "SERVICE_UNAVAILABLE", 
        "retry_allowed": False,
        "escalation_level": "error"
    },
    ServiceStatus.DEGRADED: {
        "error_code": "SERVICE_DEGRADED",
        "retry_allowed": True,
        "escalation_level": "warning"
    }
}
```

## 9. Migration Plan

### 9.1 Phase 1: Schema Foundation (Week 1)
1. **Create schema module structure**
2. **Implement base schema classes**
3. **Build command schema definitions**
4. **Develop TypeScript generator**
5. **Create build integration scripts**

### 9.2 Phase 2: Validation Middleware (Week 2)
1. **Implement Socket.IO validation decorators**
2. **Add validation mixin to WebBridge**
3. **Create error response system**
4. **Test validation with mock data**

### 9.3 Phase 3: WebBridge Integration (Week 3)
1. **Update WebBridge handlers with validation**
2. **Replace manual validation with schema validation**
3. **Implement proper error responses**
4. **Test end-to-end command flow**

### 9.4 Phase 4: Frontend Integration (Week 4)
1. **Generate initial TypeScript interfaces**
2. **Update useSocket hook with typed commands**
3. **Implement proper error handling in components**
4. **Add loading/error states to UI**

### 9.5 Phase 5: Testing & Optimization (Week 5)
1. **Comprehensive integration testing**
2. **Performance optimization**
3. **Error handling edge cases**
4. **Documentation updates**

## 10. Testing Strategy

### 10.1 Schema Validation Tests

```python
# tests/unit/test_schema_validation.py
import pytest
from cantina_os.schemas.web_commands import VoiceCommand, MusicCommand
from pydantic import ValidationError

class TestVoiceCommandValidation:
    def test_valid_voice_command(self):
        """Test valid voice command creation."""
        command = VoiceCommand(action="start")
        assert command.action == "start"
        assert command.source == "web_dashboard"
    
    def test_invalid_voice_action(self):
        """Test validation error for invalid action."""
        with pytest.raises(ValidationError) as exc_info:
            VoiceCommand(action="invalid_action")
        
        errors = exc_info.value.errors()
        assert any("Invalid voice action" in str(error) for error in errors)
    
    def test_cantina_event_conversion(self):
        """Test conversion to CantinaOS event format."""
        command = VoiceCommand(action="start")
        topic, payload = command.to_cantina_event()
        
        assert topic == "SYSTEM_SET_MODE_REQUEST"
        assert payload["mode"] == "INTERACTIVE"
        assert payload["source"] == "web_dashboard"
```

### 10.2 Integration Tests

```python
# tests/integration/test_webbridge_validation.py
import pytest
from unittest.mock import AsyncMock
from cantina_os.services.web_bridge_service import WebBridgeService

class TestWebBridgeValidation:
    @pytest.fixture
    async def web_bridge(self):
        """Create WebBridge service for testing."""
        mock_event_bus = AsyncMock()
        service = WebBridgeService(mock_event_bus)
        await service._start()
        return service
    
    async def test_valid_voice_command_handling(self, web_bridge):
        """Test handling of valid voice command."""
        mock_sid = "test_client_123"
        valid_command = {"action": "start"}
        
        # Should not raise any validation errors
        await web_bridge.voice_command(mock_sid, valid_command)
        
        # Verify event was emitted to CantinaOS
        web_bridge._event_bus.emit.assert_called_once()
    
    async def test_invalid_command_error_response(self, web_bridge):
        """Test proper error response for invalid command."""
        mock_sid = "test_client_123"
        invalid_command = {"action": "invalid_action"}
        
        # Mock the Socket.IO emit method
        web_bridge._sio.emit = AsyncMock()
        
        await web_bridge.voice_command(mock_sid, invalid_command)
        
        # Verify error response was sent
        web_bridge._sio.emit.assert_called_with(
            "command_error", 
            pytest.any(dict), 
            room=mock_sid
        )
```

## 11. Performance Considerations

### 11.1 Schema Validation Optimization
- **Cached Validation**: Pre-compile schemas for repeated validation
- **Lazy Loading**: Load schema classes only when needed
- **Validation Levels**: Different validation strictness for development vs production

### 11.2 TypeScript Generation Optimization
- **Incremental Generation**: Only regenerate changed schemas
- **Build Caching**: Cache generated interfaces between builds
- **Watch Mode**: File watching for development workflow

## 12. Security Considerations

### 12.1 Input Sanitization
- **Schema-Level Validation**: Pydantic field validators for data sanitization
- **Content Filtering**: Prevent injection attacks in text fields
- **Rate Limiting**: Command frequency limits per client

### 12.2 Error Information Disclosure
- **Sanitized Error Messages**: Don't expose internal system details
- **Validation Error Filtering**: Only return relevant validation errors
- **Logging vs Response**: Log detailed errors but send generic responses

## 13. Success Metrics

### 13.1 Development Metrics
- **Type Safety Coverage**: 100% of web commands use generated types
- **Validation Coverage**: All Socket.IO handlers use schema validation
- **Error Handling**: Standardized error responses for all failure cases

### 13.2 Runtime Metrics
- **Validation Performance**: <1ms average validation time
- **Error Reduction**: 90% reduction in command processing errors
- **Developer Experience**: Faster development with type safety

## 14. Conclusion

This centralized Pydantic schema system provides a comprehensive solution for command validation, type safety, and error handling in the CantinaOS web dashboard integration. By following CantinaOS architecture standards and implementing proper validation middleware, the system ensures robust communication between the web frontend and backend services while maintaining the integrity of the event-driven architecture.

**Key Benefits**:
- **Type Safety**: End-to-end type safety from frontend to backend
- **Validation**: Comprehensive command validation with proper error responses
- **Maintainability**: Centralized schema definitions reduce duplication
- **Developer Experience**: Auto-generated TypeScript interfaces improve development workflow
- **Architecture Compliance**: Proper integration with CantinaOS event bus topology

The system is designed to be incrementally implementable with minimal disruption to existing functionality while providing immediate benefits in terms of reliability and developer productivity.