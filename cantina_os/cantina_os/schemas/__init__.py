"""
DJ R3X CantinaOS - Web Socket.IO Command Schema System

This module provides comprehensive Pydantic validation schemas for all socket.io commands
exchanged between the web dashboard and CantinaOS. Ensures type safety, proper validation,
and seamless integration with the CantinaOS event bus architecture.

Phase 1 Implementation: Foundation schema classes and command validation patterns.
"""

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Union
from abc import ABC, abstractmethod

from pydantic import BaseModel, Field, ValidationError, validator
from ..core.event_topics import EventTopics
from ..core.event_payloads import ServiceStatus


class WebCommandError(Exception):
    """Exception raised when web command validation or processing fails."""
    
    def __init__(self, message: str, command: str = None, validation_errors: List[str] = None):
        """Initialize WebCommandError with detailed error information.
        
        Args:
            message: Primary error message
            command: Command that failed (if available)
            validation_errors: List of specific validation errors
        """
        super().__init__(message)
        self.message = message
        self.command = command
        self.validation_errors = validation_errors or []
        
    def to_dict(self) -> Dict[str, Any]:
        """Convert error to dictionary for socket.io response."""
        return {
            "error": True,
            "message": self.message,
            "command": self.command,
            "validation_errors": self.validation_errors,
            "timestamp": datetime.now().isoformat()
        }


class BaseWebCommand(BaseModel, ABC):
    """
    Base class for all web dashboard socket.io commands.
    
    Provides standardized validation patterns, command metadata,
    and integration with CantinaOS event bus topology.
    
    All web commands must inherit from this class to ensure
    consistent validation and event bus integration.
    """
    
    action: str = Field(..., description="Specific action to perform within command type")
    source: str = Field(default="web_dashboard", description="Source of command for audit trail")
    timestamp: datetime = Field(default_factory=datetime.now, description="Command creation timestamp")
    command_id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Unique command identifier")
    
    class Config:
        """Pydantic configuration for all web commands."""
        extra = "forbid"  # Reject unknown fields
        validate_assignment = True  # Validate on field assignment
        use_enum_values = True  # Use enum values in serialization
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }
    
    @validator('action')
    def validate_action(cls, v):
        """Validate that action is non-empty and contains valid characters."""
        if not v or not v.strip():
            raise ValueError("Action cannot be empty")
        if not v.replace('_', '').replace('-', '').isalnum():
            raise ValueError("Action must contain only alphanumeric characters, hyphens, and underscores")
        return v.strip().lower()
    
    @abstractmethod
    def get_allowed_actions(self) -> List[str]:
        """Return list of allowed actions for this command type."""
        pass
    
    @abstractmethod
    def to_cantina_event(self) -> Dict[str, Any]:
        """
        Convert web command to CantinaOS event payload.
        
        Returns:
            Dictionary containing event data compatible with CantinaOS event bus
            
        Raises:
            WebCommandError: If command cannot be converted to valid event
        """
        pass
    
    def validate_action_allowed(self) -> None:
        """Validate that the action is allowed for this command type."""
        if self.action not in self.get_allowed_actions():
            raise WebCommandError(
                f"Invalid action '{self.action}' for {self.__class__.__name__}",
                command=self.__class__.__name__,
                validation_errors=[f"Allowed actions: {', '.join(self.get_allowed_actions())}"]
            )


class BaseWebResponse(BaseModel):
    """
    Base class for all web dashboard socket.io responses.
    
    Provides standardized response format with success/error handling,
    timestamp tracking, and integration with ServiceStatusPayload patterns.
    """
    
    success: bool = Field(..., description="Whether the command was successful")
    message: str = Field(..., description="Human-readable response message")
    timestamp: datetime = Field(default_factory=datetime.now, description="Response creation timestamp")
    command_id: Optional[str] = Field(None, description="Original command ID if available")
    data: Optional[Dict[str, Any]] = Field(None, description="Additional response data")
    error_code: Optional[str] = Field(None, description="Error code for failed commands")
    
    class Config:
        """Pydantic configuration for web responses."""
        extra = "forbid"
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }
    
    @classmethod
    def success_response(
        cls, 
        message: str, 
        data: Optional[Dict[str, Any]] = None,
        command_id: Optional[str] = None
    ) -> "BaseWebResponse":
        """Create a successful response."""
        return cls(
            success=True,
            message=message,
            data=data,
            command_id=command_id
        )
    
    @classmethod
    def error_response(
        cls, 
        message: str, 
        error_code: Optional[str] = None,
        command_id: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None
    ) -> "BaseWebResponse":
        """Create an error response."""
        return cls(
            success=False,
            message=message,
            error_code=error_code,
            command_id=command_id,
            data=data
        )
    
    def to_service_status_payload(self, service_name: str) -> Dict[str, Any]:
        """
        Convert response to ServiceStatusPayload format for event bus.
        
        Args:
            service_name: Name of the service reporting status
            
        Returns:
            Dictionary compatible with ServiceStatusPayload
        """
        status = ServiceStatus.RUNNING if self.success else ServiceStatus.ERROR
        
        return {
            "service_name": service_name,
            "status": status,
            "message": self.message,
            "timestamp": self.timestamp.isoformat(),
            "command_id": self.command_id,
            "data": self.data
        }


class CantinaOSEventMixin:
    """
    Mixin class providing CantinaOS event bus integration utilities.
    
    Provides common methods for converting web commands to CantinaOS events
    and ensuring proper event topic usage according to Event Bus Topology.
    """
    
    @staticmethod
    def validate_event_topic(topic: str) -> bool:
        """
        Validate that an event topic exists in EventTopics enum.
        
        Args:
            topic: Event topic string to validate
            
        Returns:
            True if topic is valid, False otherwise
        """
        try:
            # Check if topic exists in EventTopics enum
            return hasattr(EventTopics, topic) or topic in [e.value for e in EventTopics]
        except Exception:
            return False
    
    @staticmethod
    def get_event_topic_for_command(command_type: str, action: str) -> Optional[str]:
        """
        Get appropriate EventTopic for a command type and action.
        
        Maps web dashboard commands to proper CantinaOS event topics
        according to Event Bus Topology specifications.
        
        Args:
            command_type: Type of command (voice, music, dj, system)
            action: Specific action within command type
            
        Returns:
            EventTopic string if mapping exists, None otherwise
        """
        command_mapping = {
            "voice": {
                "start": EventTopics.SYSTEM_SET_MODE_REQUEST,
                "stop": EventTopics.SYSTEM_SET_MODE_REQUEST,
            },
            "music": {
                "play": EventTopics.MUSIC_COMMAND,
                "pause": EventTopics.MUSIC_COMMAND,
                "resume": EventTopics.MUSIC_COMMAND,
                "stop": EventTopics.MUSIC_COMMAND,
                "next": EventTopics.MUSIC_COMMAND,
                "queue": EventTopics.MUSIC_COMMAND,
                "volume": EventTopics.MUSIC_COMMAND,
            },
            "dj": {
                "start": EventTopics.DJ_COMMAND,
                "stop": EventTopics.DJ_COMMAND,
                "next": EventTopics.DJ_NEXT_TRACK,
                "update_settings": EventTopics.DJ_COMMAND,
            },
            "system": {
                "set_mode": EventTopics.SYSTEM_SET_MODE_REQUEST,
                "restart": EventTopics.SYSTEM_SHUTDOWN_REQUESTED,
                "refresh_config": "CONFIG_REFRESH_REQUEST",
            }
        }
        
        return command_mapping.get(command_type, {}).get(action)
    
    def create_cantina_event_payload(
        self, 
        base_data: Dict[str, Any], 
        command_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create standardized CantinaOS event payload.
        
        Args:
            base_data: Command-specific data
            command_id: Optional command ID for tracking
            
        Returns:
            Event payload dictionary with standardized metadata
        """
        payload = {
            "source": "web_dashboard",
            "timestamp": datetime.now().isoformat(),
            **base_data
        }
        
        if command_id:
            payload["command_id"] = command_id
            
        return payload


# Export all base classes for use in command schema modules
__all__ = [
    "BaseWebCommand",
    "BaseWebResponse", 
    "WebCommandError",
    "CantinaOSEventMixin"
]