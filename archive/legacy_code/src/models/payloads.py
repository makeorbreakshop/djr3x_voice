"""
Event Payload Models

This module defines Pydantic models for event payloads.
"""

from typing import Optional
from pydantic import BaseModel

class ModeTransitionPayload(BaseModel):
    """Mode transition event payload."""
    old_mode: str
    new_mode: str
    status: str
    error: Optional[str] = None

class SystemModeChangePayload(BaseModel):
    """System mode change event payload."""
    old_mode: str
    new_mode: str

class ServiceStatusPayload(BaseModel):
    """Service status update event payload."""
    service_name: str
    status: str
    message: Optional[str] = None
    severity: Optional[str] = None

class CliCommandPayload(BaseModel):
    """CLI command event payload."""
    command: str
    args: Optional[dict] = None
    raw_input: str

class CliResponsePayload(BaseModel):
    """CLI response event payload."""
    message: str
    is_error: bool = False
    error_code: Optional[str] = None 