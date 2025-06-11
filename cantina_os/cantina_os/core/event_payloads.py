"""Event payload models for CantinaOS."""

from typing import Optional, Dict, Any, Literal
from enum import Enum
from pydantic import BaseModel, Field
from datetime import datetime

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


# Web Dashboard Status Payloads (outbound to web frontend)

class WebMusicStatusPayload(BaseModel):
    """Music status updates for web dashboard."""
    action: Literal["started", "stopped", "paused", "resumed", "track_changed", "volume_changed"]
    track: Optional[Dict[str, Any]] = None
    source: str
    mode: str
    volume: Optional[int] = None
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())


class WebVoiceStatusPayload(BaseModel):
    """Voice status updates for web dashboard."""
    status: Literal["idle", "recording", "processing", "speaking", "error"]
    transcript: Optional[str] = None
    confidence: Optional[float] = None
    error: Optional[str] = None
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())


class WebSystemStatusPayload(BaseModel):
    """System status for web dashboard."""
    cantina_os_connected: bool
    current_mode: str
    services: Dict[str, Any]
    arduino_connected: bool = False
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())


class WebDJStatusPayload(BaseModel):
    """DJ mode status updates for web dashboard."""
    mode: Literal["idle", "active", "transitioning"]
    current_track: Optional[Dict[str, Any]] = None
    next_track: Optional[Dict[str, Any]] = None
    personality_mode: Optional[str] = None
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())


class WebServiceStatusPayload(BaseModel):
    """Individual service status for web dashboard."""
    service_name: str
    status: ServiceStatus
    error: Optional[str] = None
    details: Optional[Dict[str, Any]] = None
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())


class WebProgressPayload(BaseModel):
    """Progress updates for web dashboard (audio processing, etc.)."""
    operation: str
    progress: float  # 0.0 to 1.0
    status: str
    details: Optional[str] = None
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())