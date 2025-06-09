"""
DJ R3X CantinaOS - Web Command Schema Models

Complete Pydantic schema models for all socket.io commands supported by the web dashboard.
Provides comprehensive validation, type safety, and seamless CantinaOS event bus integration.

This module implements all command types identified in the WebBridge analysis:
- VoiceCommandSchema (start, stop actions)
- MusicCommandSchema (play, pause, resume, stop, next, queue, volume actions)
- DJCommandSchema (start, stop, next, update_settings actions)
- SystemCommandSchema (set_mode, restart, refresh_config actions)
"""

from typing import Any, Dict, List, Optional, Union
from enum import Enum

from pydantic import BaseModel, Field, validator

from . import BaseWebCommand, CantinaOSEventMixin, WebCommandError
from ..core.event_topics import EventTopics


class VoiceActionEnum(str, Enum):
    """Valid actions for voice commands."""
    START = "start"
    STOP = "stop"


class MusicActionEnum(str, Enum):
    """Valid actions for music commands."""
    PLAY = "play"
    PAUSE = "pause"
    RESUME = "resume"
    STOP = "stop"
    NEXT = "next"
    QUEUE = "queue"
    VOLUME = "volume"


class DJActionEnum(str, Enum):
    """Valid actions for DJ mode commands."""
    START = "start"
    STOP = "stop"
    NEXT = "next"
    UPDATE_SETTINGS = "update_settings"


class SystemActionEnum(str, Enum):
    """Valid actions for system commands."""
    SET_MODE = "set_mode"
    RESTART = "restart"
    REFRESH_CONFIG = "refresh_config"


class SystemModeEnum(str, Enum):
    """Valid system modes for CantinaOS."""
    IDLE = "IDLE"
    AMBIENT = "AMBIENT"
    INTERACTIVE = "INTERACTIVE"


class VoiceCommandSchema(BaseWebCommand, CantinaOSEventMixin):
    """
    Schema for voice control commands from web dashboard.
    
    Handles voice recording start/stop commands and translates them
    to proper CantinaOS system mode transitions via YodaModeManagerService.
    
    Examples:
        {"action": "start"} -> SYSTEM_SET_MODE_REQUEST (INTERACTIVE)
        {"action": "stop"} -> SYSTEM_SET_MODE_REQUEST (AMBIENT)
    """
    
    action: VoiceActionEnum = Field(..., description="Voice action to perform")
    
    def get_allowed_actions(self) -> List[str]:
        """Return allowed voice actions."""
        return [action.value for action in VoiceActionEnum]
    
    def to_cantina_event(self) -> Dict[str, Any]:
        """
        Convert voice command to CantinaOS SYSTEM_SET_MODE_REQUEST event.
        
        Voice commands are translated to system mode changes:
        - start: Request INTERACTIVE mode (enables voice listening)
        - stop: Request AMBIENT mode (disables voice listening)
        
        Returns:
            Event payload for EventTopics.SYSTEM_SET_MODE_REQUEST
        """
        self.validate_action_allowed()
        
        # Map voice actions to system modes
        action_value = self.action.value if hasattr(self.action, 'value') else self.action
        mode_mapping = {
            "start": "INTERACTIVE",
            "stop": "AMBIENT"
        }
        
        target_mode = mode_mapping[action_value]
        
        return self.create_cantina_event_payload({
            "mode": target_mode,
            "sid": None,  # Will be set by WebBridgeService
        }, self.command_id)
    
    @validator('action')
    def validate_voice_action(cls, v):
        """Validate voice action is supported."""
        if v not in [action.value for action in VoiceActionEnum]:
            raise ValueError(f"Invalid voice action: {v}")
        return v


class MusicCommandSchema(BaseWebCommand, CantinaOSEventMixin):
    """
    Schema for music control commands from web dashboard.
    
    Handles all music playback controls and translates them to proper
    CantinaOS MUSIC_COMMAND events for MusicControllerService processing.
    
    Supports both track selection and queue management operations.
    """
    
    action: MusicActionEnum = Field(..., description="Music action to perform")
    track_name: Optional[str] = Field(None, description="Track name or search query for play/queue actions")
    track_id: Optional[str] = Field(None, description="Track ID for play/queue actions")
    volume_level: Optional[float] = Field(None, ge=0.0, le=1.0, description="Volume level (0.0-1.0) for volume action")
    
    def get_allowed_actions(self) -> List[str]:
        """Return allowed music actions."""
        return [action.value for action in MusicActionEnum]
    
    def to_cantina_event(self) -> Dict[str, Any]:
        """
        Convert music command to CantinaOS MUSIC_COMMAND event.
        
        All music commands flow through EventTopics.MUSIC_COMMAND to ensure
        proper coordination with audio ducking and timeline execution.
        
        Returns:
            Event payload for EventTopics.MUSIC_COMMAND
        """
        self.validate_action_allowed()
        
        # Build base payload
        action_value = self.action.value if hasattr(self.action, 'value') else self.action
        payload_data = {
            "action": action_value,
            "conversation_id": None,  # Web commands don't have conversation context
        }
        
        # Add action-specific fields
        if action_value in ["play", "queue"]:
            # Use track_name if provided, otherwise track_id, otherwise empty string for general play
            song_query = self.track_name or self.track_id or ""
            payload_data["song_query"] = song_query
            
        elif action_value == "volume":
            if self.volume_level is None:
                raise WebCommandError(
                    "Volume level required for volume action",
                    command="MusicCommand", 
                    validation_errors=["volume_level must be provided for volume action"]
                )
            payload_data["volume"] = self.volume_level
        
        return self.create_cantina_event_payload(payload_data, self.command_id)
    
    @validator('track_name')
    def validate_track_name(cls, v):
        """Validate track name is reasonable length and format."""
        if v is not None:
            v = v.strip()
            if len(v) > 200:
                raise ValueError("Track name too long (max 200 characters)")
            if len(v) == 0:
                raise ValueError("Track name cannot be empty")
        return v
    
    @validator('volume_level')
    def validate_volume_range(cls, v, values):
        """Validate volume level is in proper range when action is volume."""
        if v is not None and values.get('action') == MusicActionEnum.VOLUME:
            if not 0.0 <= v <= 1.0:
                raise ValueError("Volume level must be between 0.0 and 1.0")
        return v


class DJCommandSchema(BaseWebCommand, CantinaOSEventMixin):
    """
    Schema for DJ mode commands from web dashboard.
    
    Handles DJ mode lifecycle and configuration commands, translating them
    to appropriate CantinaOS events for BrainService and timeline coordination.
    """
    
    action: DJActionEnum = Field(..., description="DJ mode action to perform")
    auto_transition: Optional[bool] = Field(True, description="Enable automatic track transitions")
    transition_duration: Optional[float] = Field(5.0, ge=1.0, le=30.0, description="Crossfade duration in seconds")
    genre_preference: Optional[str] = Field(None, description="Preferred music genre for DJ selection")
    
    def get_allowed_actions(self) -> List[str]:
        """Return allowed DJ actions."""
        return [action.value for action in DJActionEnum]
    
    def to_cantina_event(self) -> Dict[str, Any]:
        """
        Convert DJ command to appropriate CantinaOS events.
        
        DJ commands map to different event topics:
        - start/stop: EventTopics.DJ_COMMAND for BrainService 
        - next: EventTopics.DJ_NEXT_TRACK for immediate transitions
        - update_settings: EventTopics.DJ_COMMAND with configuration
        
        Returns:
            Event payload for appropriate DJ event topic
        """
        self.validate_action_allowed()
        
        if self.action == DJActionEnum.START:
            return self.create_cantina_event_payload({
                "command": "dj start",
                "auto_transition": self.auto_transition,
                "transition_duration": self.transition_duration,
                "genre_preference": self.genre_preference,
            }, self.command_id)
            
        elif self.action == DJActionEnum.STOP:
            return self.create_cantina_event_payload({
                "command": "dj stop",
            }, self.command_id)
            
        elif self.action == DJActionEnum.NEXT:
            # DJ next uses different event topic for immediate response
            return self.create_cantina_event_payload({}, self.command_id)
            
        elif self.action == DJActionEnum.UPDATE_SETTINGS:
            return self.create_cantina_event_payload({
                "command": "dj update_settings",
                "auto_transition": self.auto_transition,
                "transition_duration": self.transition_duration,
                "genre_preference": self.genre_preference,
            }, self.command_id)
    
    def get_event_topic(self) -> str:
        """
        Get the appropriate event topic for this DJ command.
        
        Returns:
            EventTopic string for the command action
        """
        if self.action == DJActionEnum.NEXT:
            return EventTopics.DJ_NEXT_TRACK
        else:
            return EventTopics.DJ_COMMAND
    
    @validator('transition_duration')
    def validate_transition_duration(cls, v):
        """Validate crossfade duration is reasonable."""
        if v is not None:
            if not 1.0 <= v <= 30.0:
                raise ValueError("Transition duration must be between 1.0 and 30.0 seconds")
        return v
    
    @validator('genre_preference')
    def validate_genre_preference(cls, v):
        """Validate genre preference format."""
        if v is not None:
            v = v.strip()
            if len(v) > 50:
                raise ValueError("Genre preference too long (max 50 characters)")
            if len(v) == 0:
                return None
        return v


class SystemCommandSchema(BaseWebCommand, CantinaOSEventMixin):
    """
    Schema for system control commands from web dashboard.
    
    Handles system-level operations like mode changes, restarts, and configuration
    updates, ensuring proper integration with CantinaOS system management.
    """
    
    action: SystemActionEnum = Field(..., description="System action to perform")
    mode: Optional[SystemModeEnum] = Field(None, description="Target system mode for set_mode action")
    restart_delay: Optional[float] = Field(5.0, ge=0.0, le=60.0, description="Delay before restart in seconds")
    
    def get_allowed_actions(self) -> List[str]:
        """Return allowed system actions."""
        return [action.value for action in SystemActionEnum]
    
    def to_cantina_event(self) -> Dict[str, Any]:
        """
        Convert system command to appropriate CantinaOS events.
        
        System commands map to various event topics:
        - set_mode: EventTopics.SYSTEM_SET_MODE_REQUEST
        - restart: EventTopics.SYSTEM_SHUTDOWN_REQUESTED
        - refresh_config: CONFIG_REFRESH_REQUEST
        
        Returns:
            Event payload for appropriate system event topic
        """
        self.validate_action_allowed()
        
        action_value = self.action.value if hasattr(self.action, 'value') else self.action
        
        if action_value == "set_mode":
            if not self.mode:
                raise WebCommandError(
                    "Mode required for set_mode action",
                    command="SystemCommand",
                    validation_errors=["mode must be provided for set_mode action"]
                )
            
            mode_value = self.mode.value if hasattr(self.mode, 'value') else self.mode
            return self.create_cantina_event_payload({
                "mode": mode_value,
                "sid": None,  # Will be set by WebBridgeService
            }, self.command_id)
            
        elif action_value == "restart":
            return self.create_cantina_event_payload({
                "restart": True,
                "delay": self.restart_delay,
            }, self.command_id)
            
        elif action_value == "refresh_config":
            return self.create_cantina_event_payload({}, self.command_id)
    
    def get_event_topic(self) -> str:
        """
        Get the appropriate event topic for this system command.
        
        Returns:
            EventTopic string for the command action
        """
        topic_mapping = {
            SystemActionEnum.SET_MODE: EventTopics.SYSTEM_SET_MODE_REQUEST,
            SystemActionEnum.RESTART: EventTopics.SYSTEM_SHUTDOWN_REQUESTED,
            SystemActionEnum.REFRESH_CONFIG: "CONFIG_REFRESH_REQUEST",
        }
        return topic_mapping[self.action]
    
    @validator('mode')
    def validate_mode_required(cls, v, values):
        """Validate mode is provided when action is set_mode."""
        action = values.get('action')
        action_value = action.value if hasattr(action, 'value') else action
        if action_value == "set_mode" and v is None:
            raise ValueError("Mode is required for set_mode action")
        return v
    
    @validator('restart_delay')
    def validate_restart_delay(cls, v):
        """Validate restart delay is reasonable."""
        if v is not None:
            if not 0.0 <= v <= 60.0:
                raise ValueError("Restart delay must be between 0.0 and 60.0 seconds")
        return v


# Command type mapping for schema registry
COMMAND_SCHEMA_MAP = {
    "voice_command": VoiceCommandSchema,
    "music_command": MusicCommandSchema,
    "dj_command": DJCommandSchema,
    "system_command": SystemCommandSchema,
}


def get_command_schema(command_type: str) -> Optional[BaseWebCommand]:
    """
    Get the appropriate schema class for a command type.
    
    Args:
        command_type: Type of command (voice_command, music_command, etc.)
        
    Returns:
        Schema class if found, None otherwise
    """
    return COMMAND_SCHEMA_MAP.get(command_type)


def validate_command_data(command_type: str, data: Dict[str, Any]) -> BaseWebCommand:
    """
    Validate command data against appropriate schema.
    
    Args:
        command_type: Type of command to validate
        data: Command data dictionary
        
    Returns:
        Validated command instance
        
    Raises:
        WebCommandError: If validation fails or command type unknown
    """
    schema_class = get_command_schema(command_type)
    if not schema_class:
        raise WebCommandError(
            f"Unknown command type: {command_type}",
            command=command_type,
            validation_errors=[f"Supported types: {', '.join(COMMAND_SCHEMA_MAP.keys())}"]
        )
    
    try:
        return schema_class(**data)
    except Exception as e:
        raise WebCommandError(
            f"Validation failed for {command_type}",
            command=command_type,
            validation_errors=[str(e)]
        )


# Export all command schemas and utilities
__all__ = [
    "VoiceCommandSchema",
    "MusicCommandSchema", 
    "DJCommandSchema",
    "SystemCommandSchema",
    "VoiceActionEnum",
    "MusicActionEnum",
    "DJActionEnum",
    "SystemActionEnum",
    "SystemModeEnum",
    "COMMAND_SCHEMA_MAP",
    "get_command_schema",
    "validate_command_data"
]