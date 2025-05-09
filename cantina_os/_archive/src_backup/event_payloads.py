from typing import Any, Dict, Optional, List, Callable, Union
from pydantic import BaseModel, Field, field_validator

class ToolRegistrationPayload(BaseModel):
    """Payload for tool registration events."""
    tool_name: str = Field(..., description="Name of the tool being registered")
    tool_function: Any = Field(..., description="Function to be called when tool is executed")
    description: Optional[str] = Field(None, description="Optional description of the tool")
    
    @field_validator('tool_function')
    @classmethod
    def validate_callable(cls, v):
        if not callable(v):
            raise ValueError(f"Tool function must be callable")
        return v

class ToolExecutionRequestPayload(BaseModel):
    """Payload for tool execution request events."""
    request_id: str = Field(..., description="Unique identifier for this execution request")
    tool_name: str = Field(..., description="Name of the tool to execute")
    arguments: Dict[str, Any] = Field(default_factory=dict, description="Arguments to pass to the tool")

class ToolExecutionResultPayload(BaseModel):
    """Payload for tool execution result events."""
    request_id: str = Field(..., description="ID matching the original request")
    tool_name: str = Field(..., description="Name of the tool that was executed")
    success: bool = Field(..., description="Whether the execution was successful")
    result: Optional[Any] = Field(None, description="Result of the tool execution if successful")
    error: Optional[str] = Field(None, description="Error message if execution failed")

class ServiceStatusPayload(BaseModel):
    """Payload for service status update events."""
    status: str = Field(..., description="Status string (e.g., 'RUNNING', 'ERROR')")
    message: str = Field(..., description="Status message")

class TranscriptionEventPayload(BaseModel):
    """Payload for transcription-related events."""
    conversation_id: str
    is_final: bool
    transcript: str
    confidence: float 