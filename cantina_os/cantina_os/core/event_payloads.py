"""Event payload models for CantinaOS."""

from typing import Optional
from enum import Enum
from pydantic import BaseModel

class ServiceStatus(str, Enum):
    """Service status enum."""
    STARTING = "starting"
    RUNNING = "running"
    DEGRADED = "degraded"
    ERROR = "error"
    STOPPED = "stopped"

class LogLevel(str, Enum):
    """Log level enum."""
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"

class TranscriptionEventPayload(BaseModel):
    """Payload for transcription-related events."""
    conversation_id: str
    is_final: bool
    transcript: str
    confidence: float


class DashboardLogPayload(BaseModel):
    """Payload for dashboard log events."""
    timestamp: str
    level: str
    service: str
    message: str
    session_id: str
    entry_id: str