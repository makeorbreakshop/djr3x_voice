"""
Event Payloads for CantinaOS

This module defines the Pydantic models for all event payloads used in the CantinaOS system.
Each payload inherits from BaseEventPayload to ensure consistent metadata across all events.
"""

import time
import uuid
from typing import Optional, Dict, Any, List, Callable, Union
from enum import Enum
from pydantic import BaseModel, Field
from datetime import datetime

class BaseEventPayload(BaseModel):
    """Base class for all event payloads in the system."""
    timestamp: float = Field(
        default_factory=time.time,
        description="Unix timestamp of event creation"
    )
    event_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique ID for this specific event instance"
    )
    conversation_id: Optional[str] = Field(
        default=None,
        description="ID for the overarching user interaction or conversation turn"
    )
    schema_version: str = Field(
        default="1.0",
        description="Version of the event payload schema"
    )

class ServiceStatus(str, Enum):
    """Enumeration of possible service statuses."""
    INITIALIZING = "INITIALIZING"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    DEGRADED = "DEGRADED"
    ERROR = "ERROR"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"

class LogLevel(str, Enum):
    """Enumeration of log levels for service status messages."""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"

    @property
    def value(self) -> int:
        """Get the numeric value for level comparison."""
        return {
            LogLevel.DEBUG: 0,
            LogLevel.INFO: 1,
            LogLevel.WARNING: 2,
            LogLevel.ERROR: 3,
            LogLevel.CRITICAL: 4
        }[self]

class IntentPayload(BaseEventPayload):
    """Payload for intent detection events."""
    intent_name: str = Field(..., description="Name of the detected intent")
    parameters: Dict[str, Any] = Field(
        default_factory=dict, 
        description="Parameters for the intent"
    )
    confidence: Optional[float] = Field(
        None, 
        description="Optional confidence score"
    )
    original_text: str = Field(
        ..., 
        description="The text that triggered the intent"
    )

class IntentExecutionResultPayload(BaseEventPayload):
    """Payload for intent execution result events."""
    intent_name: str = Field(..., description="Name of the executed intent")
    parameters: Dict[str, Any] = Field(
        default_factory=dict,
        description="Parameters used in the intent execution"
    )
    result: Dict[str, Any] = Field(
        default_factory=dict,
        description="Results of the intent execution"
    )
    success: bool = Field(
        default=True,
        description="Whether the intent execution was successful"
    )
    error_message: Optional[str] = Field(
        None,
        description="Error message if execution failed"
    )
    tool_call_id: Optional[str] = Field(
        None,
        description="Original tool call ID for context tracking"
    )
    original_text: Optional[str] = Field(
        None,
        description="The original text that triggered the intent"
    )

class ServiceStatusPayload(BaseEventPayload):
    """Payload for service status update events."""
    service_name: str = Field(..., description="Name of the service")
    status: ServiceStatus = Field(..., description="Current status of the service")
    message: Optional[str] = Field(None, description="Optional status message")
    severity: Optional[LogLevel] = Field(None, description="Optional severity level")

class AudioChunkPayload(BaseEventPayload):
    """Payload for raw audio chunk events."""
    samples: bytes  # Raw audio samples as bytes
    timestamp: float  # Capture timestamp
    sample_rate: int  # Sample rate in Hz
    channels: int  # Number of channels
    dtype: str  # NumPy dtype string

class TranscriptionTextPayload(BaseEventPayload):
    """Payload for transcription text events."""
    text: str = Field(..., description="The transcribed text")
    source: str = Field(..., description="Source of the transcription (e.g., 'deepgram', 'whisper')")
    is_final: bool = Field(default=True, description="Whether this is a final transcription")
    confidence: Optional[float] = Field(None, description="Confidence score of the transcription")
    words: Optional[List[Dict[str, Any]]] = Field(None, description="Words in the transcription")

class SpeechSynthesisPayload(BaseEventPayload):
    """Payload for speech synthesis events."""
    text: str = Field(..., description="Text being synthesized")
    duration_estimate: Optional[float] = Field(None, description="Estimated duration in seconds")
    voice_id: str = Field(..., description="ID of the voice being used")

class SpeechGenerationRequestPayload(BaseEventPayload):
    """Payload for requesting speech generation."""
    text: str = Field(..., description="Text to synthesize into speech")
    voice_id: Optional[str] = Field(None, description="ID of the voice to use (uses service default if None)")
    model_id: Optional[str] = Field(None, description="ID of the TTS model to use (uses service default if None)")
    stability: Optional[float] = Field(None, description="Voice stability setting (0.0-1.0)")
    similarity_boost: Optional[float] = Field(None, description="Voice similarity boost setting (0.0-1.0)")
    speed: Optional[float] = Field(None, description="Speech speed multiplier (0.5-2.0)")
    
class SpeechGenerationCompletePayload(BaseEventPayload):
    """Payload for speech generation completion events."""
    text: str = Field(..., description="Text that was synthesized")
    audio_length_seconds: float = Field(..., description="Length of generated audio in seconds")
    success: bool = Field(..., description="Whether generation was successful")
    error: Optional[str] = Field(None, description="Error message if generation failed")

class SpeechAmplitudePayload(BaseEventPayload):
    """Payload for speech amplitude events during synthesis."""
    amplitude: float = Field(..., description="Current amplitude value")
    timestamp_offset: float = Field(..., description="Offset from synthesis start in seconds")

class LLMResponsePayload(BaseEventPayload):
    """Payload for LLM response events."""
    text: str = Field(..., description="The LLM's response text")
    tool_calls: Optional[List[Dict[str, Any]]] = Field(
        None, 
        description="Tool calls requested by the LLM"
    )
    is_complete: bool = Field(
        default=True, 
        description="Whether this is a complete response or a streaming chunk"
    )
    model_name: Optional[str] = Field(
        None, 
        description="Name of the LLM model used"
    )
    tokens_used: Optional[int] = Field(
        None, 
        description="Number of tokens used in the request and response"
    )
    completion_tokens: Optional[int] = Field(
        None, 
        description="Number of tokens in the completion"
    )
    prompt_tokens: Optional[int] = Field(
        None, 
        description="Number of tokens in the prompt"
    )
    latency: Optional[float] = Field(
        None, 
        description="API request latency in seconds"
    )

class SentimentPayload(BaseEventPayload):
    """Payload for sentiment analysis events."""
    label: str = Field(..., description="Sentiment label (e.g., 'positive', 'negative', 'neutral')")
    score: float = Field(..., description="Sentiment score")
    confidence: Optional[float] = Field(None, description="Confidence in the sentiment analysis")

class CommandCallPayload(BaseEventPayload):
    """Payload for tool/command execution requests."""
    tool_name: str = Field(..., description="Name of the tool to execute")
    parameters: Dict[str, Any] = Field(default_factory=dict, description="Parameters for the tool")
    timeout: Optional[float] = Field(None, description="Optional timeout in seconds")

class CommandResultPayload(BaseEventPayload):
    """Payload for tool/command execution results."""
    tool_name: str = Field(..., description="Name of the tool that was executed")
    success: bool = Field(..., description="Whether the execution was successful")
    result: Optional[Dict[str, Any]] = Field(None, description="Result data if successful")
    error: Optional[str] = Field(None, description="Error message if unsuccessful")

class SystemModePayload(BaseEventPayload):
    """Payload for system mode change events."""
    mode: str = Field(..., description="The target system mode")
    previous_mode: Optional[str] = Field(None, description="The previous system mode")
    parameters: Dict[str, Any] = Field(default_factory=dict, description="Mode-specific parameters")

class SystemModeChangePayload(BaseEventPayload):
    """Payload for system mode change notification events."""
    old_mode: str = Field(..., description="The previous system mode")
    new_mode: str = Field(..., description="The new system mode")
    message: Optional[str] = Field(None, description="Optional message about the mode change")

class EyeCommandPayload(BaseEventPayload):
    """Payload for eye control commands."""
    pattern: str = Field(..., description="The LED pattern to display")
    color: Optional[str] = Field(None, description="Color for the pattern (if applicable)")
    intensity: Optional[float] = Field(None, description="Intensity/brightness (0.0-1.0)")
    duration: Optional[float] = Field(None, description="Duration in seconds (if applicable)")

class MusicCommandPayload(BaseEventPayload):
    """Payload for music control commands."""
    action: str = Field(..., description="The music control action (play, stop, etc.)")
    song_query: Optional[str] = Field(None, description="Song query or identifier")
    volume: Optional[float] = Field(None, description="Volume level (0.0-1.0)")
    fade_duration: Optional[float] = Field(None, description="Fade duration in seconds")

class ModeChangedPayload(BaseEventPayload):
    """Payload for mode changed events."""
    mode: str = Field(..., description="The new mode")
    previous_mode: Optional[str] = Field(None, description="The previous mode")
    message: Optional[str] = Field(None, description="Optional message about the mode change")

class ModeTransitionStartedPayload(BaseEventPayload):
    """Payload for mode transition started events."""
    target_mode: str = Field(..., description="The target mode to transition to")
    current_mode: str = Field(..., description="The current mode transitioning from")
    message: Optional[str] = Field(None, description="Optional message about the transition")

class CliCommandPayload(BaseEventPayload):
    """Payload for CLI command events."""
    command: str = Field(..., description="Command name")
    args: List[str] = Field(default_factory=list, description="Command arguments")
    raw_input: Optional[str] = Field(None, description="Raw command input")

class StandardCommandPayload(BaseModel):
    """Standardized payload for all command events"""
    command: str = Field(..., description="Primary command (e.g., 'eye', 'music')")
    subcommand: Optional[str] = Field(None, description="Subcommand (e.g., 'pattern', 'test')")
    args: List[str] = Field(default_factory=list, description="Additional arguments")
    raw_input: str = Field(..., description="Original raw command input")
    conversation_id: Optional[str] = Field(None, description="Associated conversation ID if any")

    class Config:
        frozen = True  # Make immutable to prevent accidental modifications

    @classmethod
    def from_legacy_format(cls, payload: dict) -> "StandardCommandPayload":
        """Convert legacy command format to StandardCommandPayload
        
        This improved implementation better handles compound commands with proper
        extraction of subcommands.
        """
        command = payload.get("command", "")
        args = payload.get("args", [])
        raw_input = payload.get("raw_input", "")
        
        # Ensure args is a list
        if isinstance(args, str):
            args = args.split()
        
        # Handle compound commands (e.g., "eye pattern")
        parts = command.split(" ", 1) if " " in command else [command]
        main_command = parts[0]
        subcommand = parts[1] if len(parts) > 1 else None
        
        # If we don't have a subcommand extracted from the command but have args,
        # check if the first arg might be a subcommand
        if not subcommand and args:
            # Common subcommands for different command types
            subcommand_map = {
                "eye": ["pattern", "test", "status"],
                "music": ["play", "stop", "list"],
                "debug": ["level", "trace", "performance"]
            }
            
            # If the main command has known subcommands and the first arg matches one
            if main_command in subcommand_map and args[0] in subcommand_map[main_command]:
                subcommand = args[0]
                args = args[1:]  # Remove the subcommand from args
        
        # Further improve raw_input parsing if needed
        if not subcommand and not args and raw_input:
            # Try to extract info from raw_input if payload is incomplete
            parts = raw_input.strip().split()
            if len(parts) > 1 and parts[0] == main_command:
                # Check if second part might be a subcommand
                potential_subcommand = parts[1]
                subcommand_map = {
                    "eye": ["pattern", "test", "status"],
                    "music": ["play", "stop", "list"],
                    "debug": ["level", "trace", "performance"]
                }
                if main_command in subcommand_map and potential_subcommand in subcommand_map[main_command]:
                    subcommand = potential_subcommand
                    args = parts[2:]  # Remaining parts become args
        
        return cls(
            command=main_command,
            subcommand=subcommand,
            args=args,
            raw_input=raw_input,
            conversation_id=payload.get("conversation_id")
        )
        
    @classmethod
    def from_raw_input(cls, raw_input: str, conversation_id: Optional[str] = None) -> "StandardCommandPayload":
        """
        Parse a raw command string into StandardCommandPayload
        
        This provides direct parsing from a raw command string without going through a dict.
        Used for direct command creation from user input.
        
        Args:
            raw_input: The raw command string (e.g., "eye pattern happy")
            conversation_id: Optional ID for the conversation context
            
        Returns:
            StandardCommandPayload: The parsed command payload
        """
        parts = raw_input.strip().split()
        if not parts:
            return cls(command="", args=[], raw_input=raw_input, conversation_id=conversation_id)
            
        command = parts[0].lower()
        
        # Handle special cases for compound commands
        if len(parts) >= 2:
            # Check for known compound command patterns (eye pattern, play music, etc.)
            compound_commands = ["eye pattern", "eye test", "eye status", 
                                "play music", "stop music", "list music",
                                "debug level", "debug trace"]
                                
            possible_compound = f"{parts[0]} {parts[1]}"
            if possible_compound in compound_commands:
                return cls(
                    command=parts[0],
                    subcommand=parts[1],
                    args=parts[2:],
                    raw_input=raw_input,
                    conversation_id=conversation_id
                )
        
        # Standard single command
        return cls(
            command=command,
            subcommand=None,
            args=parts[1:],
            raw_input=raw_input,
            conversation_id=conversation_id
        )
    
    def get_full_command(self) -> str:
        """Get the full command string including subcommand if present."""
        if self.subcommand:
            return f"{self.command} {self.subcommand}"
        return self.command
        
    def validate_arg_count(self, min_count: int = 0, max_count: Optional[int] = None) -> bool:
        """
        Validate that the number of arguments is within the specified range.
        
        Args:
            min_count: Minimum number of arguments required
            max_count: Maximum number of arguments allowed (None for unlimited)
            
        Returns:
            bool: True if valid, False otherwise
        """
        if len(self.args) < min_count:
            return False
            
        if max_count is not None and len(self.args) > max_count:
            return False
            
        return True
        
    def get_arg_as_int(self, index: int, default: Optional[int] = None) -> Optional[int]:
        """
        Get an argument as an integer with validation.
        
        Args:
            index: Index of the argument to convert
            default: Default value if argument is missing or invalid
            
        Returns:
            int or None: The parsed integer or default value
        """
        if index >= len(self.args):
            return default
            
        try:
            return int(self.args[index])
        except (ValueError, TypeError):
            return default
            
    def __str__(self) -> str:
        """String representation of the command"""
        result = self.command
        if self.subcommand:
            result += f" {self.subcommand}"
        if self.args:
            result += f" {' '.join(self.args)}"
        return result

class CliResponsePayload(BaseEventPayload):
    """Payload for CLI response events."""
    message: str = Field(..., description="Response message")
    is_error: bool = Field(default=False, description="Whether this is an error response")
    command: Optional[str] = Field(None, description="Original command that triggered this response")

class ToolRegistrationPayload(BaseEventPayload):
    """Payload for tool registration requests."""
    tool_name: str = Field(..., description="Name of the tool to register")
    tool_function: Callable = Field(..., description="The callable tool function")
    description: Optional[str] = Field(None, description="Optional description of the tool")
    parameters: Optional[Dict[str, Any]] = Field(None, description="Optional parameter schema")

class ToolExecutionRequestPayload(BaseEventPayload):
    """Payload for tool execution requests."""
    request_id: str = Field(..., description="Unique ID for this execution request")
    tool_name: str = Field(..., description="Name of the tool to execute")
    arguments: Dict[str, Any] = Field(default_factory=dict, description="Arguments for the tool")
    timeout: Optional[float] = Field(None, description="Optional execution timeout in seconds")

class ToolExecutionResultPayload(BaseEventPayload):
    """Payload for tool execution results."""
    request_id: str = Field(..., description="ID of the original execution request")
    tool_name: str = Field(..., description="Name of the tool that was executed")
    success: bool = Field(..., description="Whether the execution was successful")
    result: Optional[Any] = Field(None, description="Result data if successful")
    error: Optional[str] = Field(None, description="Error message if unsuccessful")
    execution_time: Optional[float] = Field(None, description="Execution time in seconds")

class ModeTransitionPayload(BaseEventPayload):
    """Payload for mode transition events."""
    old_mode: str = Field(..., description="The previous system mode")
    new_mode: str = Field(..., description="The new system mode")
    status: str = Field(..., description="The transition status (started/complete)")
    message: Optional[str] = Field(None, description="Optional message about the transition")

class DebugLogPayload(BaseEventPayload):
    level: LogLevel
    component: str
    message: str
    details: Optional[Dict[str, Any]] = None

class CommandTracePayload(BaseEventPayload):
    command: str
    service: str
    execution_time_ms: float
    status: str
    details: Optional[Dict[str, Any]] = None

class PerformanceMetricPayload(BaseEventPayload):
    metric_name: str
    value: float
    unit: str
    component: str
    details: Optional[Dict[str, Any]] = None

class DebugConfigPayload(BaseEventPayload):
    component: str
    log_level: LogLevel
    enable_tracing: bool = True
    enable_metrics: bool = True

class DebugCommandPayload(BaseEventPayload):
    """Payload for debug command events."""
    command: str = Field(..., description="Debug command (level, trace, etc.)")
    component: str = Field(..., description="Target component name or 'all'")
    level: Optional[LogLevel] = Field(None, description="Log level for level command")
    enable: Optional[bool] = Field(None, description="Enable/disable flag for trace/metrics")
    args: List[str] = Field(default_factory=list, description="Additional command arguments") 