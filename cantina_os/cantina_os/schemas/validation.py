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
from datetime import datetime
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
from ..core.event_payloads import (
    WebMusicStatusPayload,
    WebVoiceStatusPayload,
    WebSystemStatusPayload,
    WebDJStatusPayload,
    WebServiceStatusPayload,
    WebProgressPayload
)

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


class StatusPayloadValidationMixin:
    """
    Mixin class for WebBridge service providing status payload validation utilities.
    
    Provides standardized validation for all outbound status payloads sent to
    the web dashboard, ensuring type safety and consistent data structure.
    """
    
    def validate_and_serialize_status(
        self, 
        status_type: str, 
        data: Dict[str, Any], 
        fallback_data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Validate status payload against appropriate Pydantic model and serialize.
        
        Provides comprehensive validation with fallback mechanisms to ensure
        the dashboard always receives valid data, even if original data is malformed.
        
        Args:
            status_type: Type of status (music, voice, system, dj, service, progress)
            data: Raw status data to validate
            fallback_data: Optional fallback data if validation fails
            
        Returns:
            Validated and serialized status payload dictionary
            
        Raises:
            WebCommandError: If validation fails and no fallback provided
        """
        try:
            # Map status types to their respective Pydantic models
            status_model_map = {
                "music": WebMusicStatusPayload,
                "voice": WebVoiceStatusPayload,
                "system": WebSystemStatusPayload,
                "dj": WebDJStatusPayload,
                "service": WebServiceStatusPayload,
                "progress": WebProgressPayload
            }
            
            # Get the appropriate model for validation
            model_class = status_model_map.get(status_type)
            if not model_class:
                logger.warning(f"Unknown status type '{status_type}', using raw data")
                return self._sanitize_raw_data(data)
            
            # Apply field mapping for specific status types
            mapped_data = self._map_status_fields(status_type, data)
            
            # Attempt validation with the appropriate model
            logger.debug(f"Validating {status_type} status payload: {mapped_data}")
            validated_payload = model_class(**mapped_data)
            
            # Return JSON-serialized dictionary with datetime handling
            # CRITICAL: Use exclude_none=False to preserve Optional fields with actual values
            result = validated_payload.model_dump(mode='json', exclude_none=False)
            logger.debug(f"Serialized {status_type} payload: {result}")
            return result
            
        except ValidationError as e:
            logger.warning(f"Validation failed for {status_type} status: {e}")
            
            # Try fallback data if provided
            if fallback_data:
                try:
                    fallback_payload = model_class(**fallback_data)
                    logger.info(f"Using fallback data for {status_type} status")
                    return fallback_payload.model_dump(mode='json', exclude_none=False)
                except ValidationError as fallback_error:
                    logger.error(f"Fallback data also invalid: {fallback_error}")
            
            # If no fallback or fallback fails, try to create minimal valid payload
            return self._create_minimal_status_payload(status_type, data)
            
        except Exception as e:
            logger.error(f"Unexpected error validating {status_type} status: {e}")
            return self._create_minimal_status_payload(status_type, data)
    
    def _map_status_fields(self, status_type: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Map fields from CantinaOS service format to web dashboard format.
        
        Args:
            status_type: Type of status being mapped
            data: Raw status data from CantinaOS services
            
        Returns:
            Data with fields mapped to expected web dashboard format
        """
        mapped_data = data.copy()
        
        # Map service status fields
        if status_type == "service":
            # Map 'service' to 'service_name' 
            if 'service' in mapped_data and 'service_name' not in mapped_data:
                mapped_data['service_name'] = mapped_data.pop('service')
            
            # Map status values to expected enum values
            if 'status' in mapped_data:
                status_mapping = {
                    'online': 'running',
                    'offline': 'stopped', 
                    'error': 'error',
                    'warning': 'degraded',
                    'RUNNING': 'running',
                    'STOPPED': 'stopped',
                    'ERROR': 'error',
                    'DEGRADED': 'degraded'
                }
                old_status = mapped_data['status']
                mapped_data['status'] = status_mapping.get(old_status, old_status)
        
        # Map progress status fields for WebProgressPayload compatibility
        elif status_type == "progress":
            # Ensure required fields are present
            if 'operation' not in mapped_data:
                mapped_data['operation'] = "music_playback"
            
            # Convert progress_percent (0-100) to progress (0.0-1.0) if needed
            if 'progress_percent' in mapped_data and 'progress' not in mapped_data:
                progress_percent = mapped_data['progress_percent']
                mapped_data['progress'] = progress_percent / 100.0 if progress_percent > 1.0 else progress_percent
            
            # Ensure progress is in correct range
            if 'progress' in mapped_data:
                progress = mapped_data['progress']
                if progress > 1.0:  # Convert from percentage to decimal
                    mapped_data['progress'] = progress / 100.0
                # Clamp to valid range
                mapped_data['progress'] = max(0.0, min(1.0, mapped_data['progress']))
            
            # Ensure status field is present
            if 'status' not in mapped_data:
                mapped_data['status'] = "playing"
            
            # Create details field from position data if available
            if 'details' not in mapped_data and 'position_sec' in mapped_data and 'duration_sec' in mapped_data:
                pos = mapped_data['position_sec']
                dur = mapped_data['duration_sec']
                mapped_data['details'] = f"Position: {pos:.1f}s / {dur:.1f}s"
        
        return mapped_data
    
    def _sanitize_raw_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Sanitize raw data by removing problematic values and ensuring basic structure.
        
        Args:
            data: Raw data dictionary
            
        Returns:
            Sanitized data dictionary
        """
        sanitized = {}
        
        for key, value in data.items():
            # Skip None values and empty strings for optional fields
            if value is None or value == "":
                continue
                
            # Ensure timestamps are strings
            if key == "timestamp" and not isinstance(value, str):
                sanitized[key] = str(value)
            else:
                sanitized[key] = value
        
        # Ensure timestamp is present
        if "timestamp" not in sanitized:
            sanitized["timestamp"] = datetime.now().isoformat()
            
        return sanitized
    
    def _create_minimal_status_payload(
        self, 
        status_type: str, 
        original_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Create minimal valid payload when validation fails.
        
        Args:
            status_type: Type of status payload
            original_data: Original data that failed validation
            
        Returns:
            Minimal valid payload dictionary
        """
        timestamp = datetime.now().isoformat()
        
        # Create minimal payloads based on status type
        minimal_payloads = {
            "music": {
                "action": "stopped",
                "source": "web_bridge",
                "mode": "UNKNOWN",
                "timestamp": timestamp
            },
            "voice": {
                "status": "idle",
                "timestamp": timestamp
            },
            "system": {
                "cantina_os_connected": True,
                "current_mode": "UNKNOWN",
                "services": {},
                "timestamp": timestamp
            },
            "dj": {
                "mode": "idle",
                "timestamp": timestamp
            },
            "service": {
                "service_name": original_data.get("service_name", "unknown"),
                "status": "ERROR",
                "timestamp": timestamp
            },
            "progress": {
                "operation": "music_playback",
                "progress": 0.0,
                "status": "error",
                "details": "Progress tracking failed",
                "timestamp": timestamp
            }
        }
        
        minimal = minimal_payloads.get(status_type, {"timestamp": timestamp})
        
        # Try to preserve some original data if it's safe
        safe_fields = ["action", "source", "service_name", "operation", "mode", "status"]
        for field in safe_fields:
            if field in original_data and isinstance(original_data[field], str):
                minimal[field] = original_data[field]
        
        # For progress type, also try to preserve numeric progress data
        if status_type == "progress":
            if "progress" in original_data and isinstance(original_data["progress"], (int, float)):
                minimal["progress"] = max(0.0, min(1.0, float(original_data["progress"])))
            elif "progress_percent" in original_data and isinstance(original_data["progress_percent"], (int, float)):
                progress_percent = original_data["progress_percent"]
                minimal["progress"] = max(0.0, min(1.0, progress_percent / 100.0 if progress_percent > 1.0 else progress_percent))
        
        logger.info(f"Created minimal {status_type} status payload: {minimal}")
        return minimal
    
    async def broadcast_validated_status(
        self, 
        status_type: str, 
        data: Dict[str, Any], 
        event_topic: str,
        socket_event_name: str,
        fallback_data: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Validate status payload and broadcast to all connected dashboard clients.
        
        Complete validation and broadcast pipeline:
        1. Validate payload against appropriate Pydantic model
        2. Apply fallback mechanisms if validation fails
        3. Broadcast to all connected clients via Socket.IO
        4. Return success status
        
        Args:
            status_type: Type of status (music, voice, etc.)
            data: Raw status data
            event_topic: CantinaOS event topic for audit trail
            socket_event_name: Socket.IO event name for dashboard
            fallback_data: Optional fallback data
            
        Returns:
            True if broadcast successful, False otherwise
        """
        try:
            # Validate and serialize the status payload
            validated_payload = self.validate_and_serialize_status(
                status_type, data, fallback_data
            )
            
            # Use existing broadcast method if available (WebBridge pattern)
            if hasattr(self, '_broadcast_event_to_dashboard'):
                await self._broadcast_event_to_dashboard(
                    event_topic,
                    validated_payload,
                    socket_event_name,
                    skip_validation=True  # Already validated, prevent double validation
                )
                
                logger.debug(f"Successfully broadcast validated {status_type} status")
                return True
            else:
                logger.warning("No broadcast method available on service")
                return False
                
        except Exception as e:
            logger.error(f"Error broadcasting validated {status_type} status: {e}")
            
            # Try to send minimal error status if broadcast fails
            try:
                error_payload = self._create_minimal_status_payload(status_type, data)
                if hasattr(self, '_broadcast_event_to_dashboard'):
                    await self._broadcast_event_to_dashboard(
                        event_topic,
                        error_payload,
                        socket_event_name
                    )
            except Exception as fallback_error:
                logger.error(f"Failed to send error status: {fallback_error}")
            
            return False


# Additional validation helper functions for WebBridge status handling

def validate_music_status_payload(data: Dict[str, Any]) -> WebMusicStatusPayload:
    """
    Validate music status payload with enhanced error handling.
    
    Args:
        data: Raw music status data
        
    Returns:
        Validated WebMusicStatusPayload instance
        
    Raises:
        ValidationError: If data is invalid and cannot be corrected
    """
    try:
        return WebMusicStatusPayload(**data)
    except ValidationError as e:
        # Try to fix common validation issues
        corrected_data = data.copy()
        
        # Ensure required action field is present and valid
        if "action" not in corrected_data or corrected_data["action"] not in ["started", "stopped", "paused"]:
            corrected_data["action"] = "stopped"
        
        # Ensure required source field is present
        if "source" not in corrected_data or not corrected_data["source"]:
            corrected_data["source"] = "web_bridge"
        
        # Ensure required mode field is present  
        if "mode" not in corrected_data or not corrected_data["mode"]:
            corrected_data["mode"] = "UNKNOWN"
        
        # Try validation again with corrected data
        try:
            return WebMusicStatusPayload(**corrected_data)
        except ValidationError:
            # If still invalid, re-raise original error
            raise e


def validate_voice_status_payload(data: Dict[str, Any]) -> WebVoiceStatusPayload:
    """
    Validate voice status payload with enhanced error handling.
    
    Args:
        data: Raw voice status data
        
    Returns:
        Validated WebVoiceStatusPayload instance
        
    Raises:
        ValidationError: If data is invalid and cannot be corrected
    """
    try:
        return WebVoiceStatusPayload(**data)
    except ValidationError as e:
        # Try to fix common validation issues
        corrected_data = data.copy()
        
        # Ensure required status field is present and valid
        if "status" not in corrected_data or corrected_data["status"] not in ["idle", "recording", "processing", "speaking"]:
            corrected_data["status"] = "idle"
        
        # Try validation again with corrected data
        try:
            return WebVoiceStatusPayload(**corrected_data)
        except ValidationError:
            # If still invalid, re-raise original error
            raise e


def validate_service_status_payload(data: Dict[str, Any]) -> WebServiceStatusPayload:
    """
    Validate service status payload with enhanced error handling.
    
    Args:
        data: Raw service status data
        
    Returns:
        Validated WebServiceStatusPayload instance
        
    Raises:
        ValidationError: If data is invalid and cannot be corrected
    """
    try:
        return WebServiceStatusPayload(**data)
    except ValidationError as e:
        # Try to fix common validation issues
        corrected_data = data.copy()
        
        # Ensure required service_name field is present
        if "service_name" not in corrected_data or not corrected_data["service_name"]:
            corrected_data["service_name"] = "unknown"
        
        # Ensure required status field is present and valid
        if "status" not in corrected_data:
            corrected_data["status"] = "ERROR"
        
        # Try validation again with corrected data
        try:
            return WebServiceStatusPayload(**corrected_data)
        except ValidationError:
            # If still invalid, re-raise original error
            raise e


def create_status_validation_fallback(
    status_type: str, 
    original_data: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Create fallback status payload when validation completely fails.
    
    This function ensures that even in the worst case scenario,
    the dashboard receives some valid status information.
    
    Args:
        status_type: Type of status (music, voice, service, etc.)
        original_data: Original data that failed validation
        
    Returns:
        Minimal valid status payload
    """
    timestamp = datetime.now().isoformat()
    service_name = original_data.get("service_name", "unknown")
    
    fallback_payloads = {
        "music": {
            "action": "stopped",
            "source": "web_bridge_fallback",
            "mode": "ERROR",
            "timestamp": timestamp,
            "track": None
        },
        "voice": {
            "status": "idle",
            "timestamp": timestamp,
            "error": "Status validation failed"
        },
        "service": {
            "service_name": service_name,
            "status": "ERROR",
            "error": "Status validation failed",
            "timestamp": timestamp
        },
        "system": {
            "cantina_os_connected": False,
            "current_mode": "ERROR",
            "services": {},
            "timestamp": timestamp
        },
        "dj": {
            "mode": "idle",
            "timestamp": timestamp
        },
        "progress": {
            "operation": "error",
            "progress": 0.0,
            "status": "error",
            "details": "Validation failed",
            "timestamp": timestamp
        }
    }
    
    return fallback_payloads.get(status_type, {"timestamp": timestamp})


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
    "StatusPayloadValidationMixin",
    "validate_socketio_command",
    "CommandSchemaRegistry", 
    "COMMAND_SCHEMA_REGISTRY",
    "format_validation_errors",
    "create_validation_error_response",
    "validate_event_payload_compatibility",
    "validate_music_status_payload",
    "validate_voice_status_payload", 
    "validate_service_status_payload",
    "create_status_validation_fallback"
]