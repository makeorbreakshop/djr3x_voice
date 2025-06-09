"""
DJ R3X CantinaOS - Socket.IO Command Validation Infrastructure

Complete validation infrastructure for socket.io commands including decorators,
validation mixins, error formatting, and the schema registry system.

Provides seamless integration with WebBridge service event handlers and ensures
all socket.io commands are properly validated before CantinaOS event emission.
"""

import asyncio
import functools
import logging
from typing import Any, Callable, Dict, List, Optional, Type, Union

from pydantic import ValidationError

from . import BaseWebCommand, BaseWebResponse, WebCommandError
from .web_commands import (
    VoiceCommandSchema,
    MusicCommandSchema, 
    DJCommandSchema,
    SystemCommandSchema,
    COMMAND_SCHEMA_MAP,
    validate_command_data
)
from ..core.event_topics import EventTopics

# Configure logger for validation operations
logger = logging.getLogger(__name__)


class SocketIOValidationMixin:
    """
    Mixin class for WebBridge service providing socket.io command validation utilities.
    
    Integrates with BaseService pattern and provides standardized validation
    for all socket.io command handlers in WebBridgeService.
    """
    
    async def validate_and_emit_command(
        self, 
        command_type: str, 
        data: Dict[str, Any], 
        sid: str = None
    ) -> BaseWebResponse:
        """
        Validate socket.io command and emit to CantinaOS event bus.
        
        Complete validation and emission pipeline:
        1. Validate command data against schema
        2. Convert to CantinaOS event payload
        3. Emit to appropriate event topic
        4. Return standardized response
        
        Args:
            command_type: Type of command (voice_command, music_command, etc.)
            data: Raw command data from socket.io client
            sid: Socket.io session ID for error reporting
            
        Returns:
            BaseWebResponse with success/error status
        """
        try:
            # Step 1: Validate command data
            logger.debug(f"Validating {command_type} command: {data}")
            command = validate_command_data(command_type, data)
            
            # Step 2: Convert to CantinaOS event payload
            event_payload = command.to_cantina_event()
            
            # Step 3: Add socket.io session context if available
            if sid:
                event_payload["sid"] = sid
            
            # Step 4: Get appropriate event topic
            event_topic = self._get_event_topic_for_command(command)
            
            # Step 5: Emit to CantinaOS event bus
            logger.info(f"Emitting {event_topic} event: {event_payload}")
            self._event_bus.emit(event_topic, event_payload)
            
            # Step 6: Return success response
            return BaseWebResponse.success_response(
                message=f"{command_type} command processed successfully",
                command_id=command.command_id,
                data={"action": command.action, "event_topic": event_topic}
            )
            
        except WebCommandError as e:
            logger.error(f"Command validation error for {command_type}: {e}")
            return BaseWebResponse.error_response(
                message=e.message,
                error_code="VALIDATION_ERROR",
                data={"validation_errors": e.validation_errors}
            )
            
        except Exception as e:
            logger.error(f"Unexpected error processing {command_type}: {e}")
            return BaseWebResponse.error_response(
                message=f"Failed to process {command_type}",
                error_code="PROCESSING_ERROR",
                data={"error": str(e)}
            )
    
    def _get_event_topic_for_command(self, command: BaseWebCommand) -> str:
        """
        Get the appropriate CantinaOS event topic for a validated command.
        
        Uses command-specific logic to determine the correct event topic
        according to CantinaOS Event Bus Topology.
        
        Args:
            command: Validated command instance
            
        Returns:
            EventTopic string
            
        Raises:
            WebCommandError: If no appropriate topic found
        """
        # DJ commands have action-specific topics
        if isinstance(command, DJCommandSchema):
            return command.get_event_topic()
        
        # System commands have action-specific topics  
        if isinstance(command, SystemCommandSchema):
            return command.get_event_topic()
            
        # Voice and music commands use fixed topics
        if isinstance(command, VoiceCommandSchema):
            return EventTopics.SYSTEM_SET_MODE_REQUEST
            
        if isinstance(command, MusicCommandSchema):
            return EventTopics.MUSIC_COMMAND
            
        raise WebCommandError(
            f"No event topic mapping for command type: {type(command).__name__}",
            command=type(command).__name__
        )


def validate_socketio_command(command_type: str):
    """
    Decorator for socket.io command handlers to ensure automatic validation.
    
    Automatically validates incoming command data against appropriate schema
    before calling the handler function. Provides standardized error handling
    and response formatting.
    
    Args:
        command_type: Type of command to validate against
        
    Returns:
        Decorator function
        
    Usage:
        @validate_socketio_command("voice_command")
        async def voice_command(self, sid, data):
            # Handler receives validated command instance
            pass
    """
    def decorator(handler_func: Callable) -> Callable:
        @functools.wraps(handler_func)
        async def wrapper(self, sid: str, data: Dict[str, Any]) -> None:
            try:
                # Validate command data
                logger.debug(f"Validating {command_type} from {sid}: {data}")
                validated_command = validate_command_data(command_type, data)
                
                # Call original handler with validated command
                await handler_func(self, sid, validated_command)
                
            except WebCommandError as e:
                logger.error(f"Validation error for {command_type} from {sid}: {e}")
                
                # Send error response to client
                if hasattr(self, '_sio') and self._sio:
                    await self._sio.emit(
                        "command_error",
                        e.to_dict(),
                        room=sid
                    )
                    
            except Exception as e:
                logger.error(f"Handler error for {command_type} from {sid}: {e}")
                
                # Send generic error response
                error_response = WebCommandError(
                    f"Failed to process {command_type}",
                    command=command_type,
                    validation_errors=[str(e)]
                )
                
                if hasattr(self, '_sio') and self._sio:
                    await self._sio.emit(
                        "command_error", 
                        error_response.to_dict(),
                        room=sid
                    )
        
        return wrapper
    return decorator


class CommandSchemaRegistry:
    """
    Central registry for all socket.io command schemas.
    
    Provides schema lookup, validation, and metadata for all supported
    command types. Ensures consistency across the validation system.
    """
    
    def __init__(self):
        """Initialize the schema registry with all command types."""
        self._schemas = COMMAND_SCHEMA_MAP.copy()
        self._logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
    
    def register_schema(self, command_type: str, schema_class: Type[BaseWebCommand]) -> None:
        """
        Register a new command schema.
        
        Args:
            command_type: Unique command type identifier
            schema_class: Pydantic schema class
        """
        if not issubclass(schema_class, BaseWebCommand):
            raise ValueError(f"Schema class must inherit from BaseWebCommand")
        
        self._schemas[command_type] = schema_class
        self._logger.info(f"Registered schema for command type: {command_type}")
    
    def get_schema(self, command_type: str) -> Optional[Type[BaseWebCommand]]:
        """
        Get schema class for command type.
        
        Args:
            command_type: Command type to look up
            
        Returns:
            Schema class if found, None otherwise
        """
        return self._schemas.get(command_type)
    
    def get_supported_commands(self) -> List[str]:
        """Get list of all supported command types."""
        return list(self._schemas.keys())
    
    def validate_command(self, command_type: str, data: Dict[str, Any]) -> BaseWebCommand:
        """
        Validate command data against registered schema.
        
        Args:
            command_type: Type of command to validate
            data: Command data dictionary
            
        Returns:
            Validated command instance
            
        Raises:
            WebCommandError: If validation fails
        """
        return validate_command_data(command_type, data)
    
    def get_command_info(self, command_type: str) -> Optional[Dict[str, Any]]:
        """
        Get detailed information about a command type.
        
        Args:
            command_type: Command type to inspect
            
        Returns:
            Dictionary with command metadata
        """
        schema_class = self.get_schema(command_type)
        if not schema_class:
            return None
        
        # Create dummy instance to get allowed actions
        try:
            dummy_instance = schema_class(action="start")  # Use dummy action
            allowed_actions = dummy_instance.get_allowed_actions()
        except Exception:
            allowed_actions = []
        
        return {
            "command_type": command_type,
            "schema_class": schema_class.__name__,
            "allowed_actions": allowed_actions,
            "description": schema_class.__doc__.strip() if schema_class.__doc__ else None,
            "required_fields": list(schema_class.__fields__.keys()),
        }


# Global schema registry instance
COMMAND_SCHEMA_REGISTRY = CommandSchemaRegistry()


def format_validation_errors(errors: List[Dict[str, Any]]) -> List[str]:
    """
    Format Pydantic validation errors for user-friendly display.
    
    Args:
        errors: List of Pydantic validation error dictionaries
        
    Returns:
        List of formatted error messages
    """
    formatted_errors = []
    
    for error in errors:
        field = " -> ".join(str(loc) for loc in error.get("loc", []))
        message = error.get("msg", "Validation error")
        error_type = error.get("type", "")
        
        if field:
            formatted_errors.append(f"{field}: {message}")
        else:
            formatted_errors.append(message)
    
    return formatted_errors


def create_validation_error_response(
    command_type: str, 
    validation_error: ValidationError,
    command_id: Optional[str] = None
) -> BaseWebResponse:
    """
    Create standardized error response for validation failures.
    
    Args:
        command_type: Type of command that failed validation
        validation_error: Pydantic ValidationError instance
        command_id: Optional command ID
        
    Returns:
        BaseWebResponse with formatted validation errors
    """
    formatted_errors = format_validation_errors(validation_error.errors())
    
    return BaseWebResponse.error_response(
        message=f"Validation failed for {command_type}",
        error_code="VALIDATION_ERROR",
        command_id=command_id,
        data={
            "validation_errors": formatted_errors,
            "error_count": len(formatted_errors)
        }
    )


def validate_event_payload_compatibility(
    command: BaseWebCommand, 
    expected_event_topic: str
) -> bool:
    """
    Validate that a command's event payload is compatible with expected topic.
    
    Args:
        command: Validated command instance
        expected_event_topic: Expected CantinaOS event topic
        
    Returns:
        True if payload is compatible, False otherwise
    """
    try:
        # Get the event payload
        payload = command.to_cantina_event()
        
        # Basic validation - payload should be a dictionary
        if not isinstance(payload, dict):
            return False
        
        # Check for required fields based on event topic
        required_fields = {
            EventTopics.SYSTEM_SET_MODE_REQUEST: ["mode", "source"],
            EventTopics.MUSIC_COMMAND: ["action", "source"],
            EventTopics.DJ_COMMAND: ["source"],
            EventTopics.DJ_NEXT_TRACK: ["source"],
            EventTopics.SYSTEM_SHUTDOWN_REQUESTED: ["source"],
        }
        
        topic_requirements = required_fields.get(expected_event_topic, [])
        for field in topic_requirements:
            if field not in payload:
                logger.warning(f"Missing required field '{field}' for topic {expected_event_topic}")
                return False
        
        return True
        
    except Exception as e:
        logger.error(f"Error validating payload compatibility: {e}")
        return False


# Export all validation utilities
__all__ = [
    "SocketIOValidationMixin",
    "validate_socketio_command",
    "CommandSchemaRegistry", 
    "COMMAND_SCHEMA_REGISTRY",
    "format_validation_errors",
    "create_validation_error_response",
    "validate_event_payload_compatibility"
]