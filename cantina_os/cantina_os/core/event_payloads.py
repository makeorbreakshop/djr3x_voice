"""Event payload models for CantinaOS."""

from typing import Optional, Dict, Any, Literal, List
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


# Web Dashboard Command Payloads (inbound from web frontend)

class WebDashboardCommandPayload(BaseModel):
    """Base web dashboard command payload."""
    action: str
    source: str = "web_dashboard"
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    data: Optional[Dict[str, Any]] = None


class WebVoiceCommandPayload(WebDashboardCommandPayload):
    """Voice commands from web dashboard."""
    action: Literal["start", "stop"]


class WebMusicCommandPayload(WebDashboardCommandPayload):
    """Music commands from web dashboard."""
    action: Literal["play", "pause", "stop", "next", "volume"]
    track_id: Optional[str] = None
    track_name: Optional[str] = None
    volume: Optional[int] = None


class WebSystemCommandPayload(WebDashboardCommandPayload):
    """System commands from web dashboard."""
    action: Literal["set_mode", "restart", "refresh_config"]
    mode: Optional[Literal["IDLE", "AMBIENT", "INTERACTIVE"]] = None


class WebDJCommandPayload(WebDashboardCommandPayload):
    """DJ mode commands from web dashboard."""
    action: Literal["start", "stop", "next_track", "set_personality"]
    personality_mode: Optional[str] = None


# Web Dashboard Status Payloads (outbound to web frontend)

class WebMusicStatusPayload(BaseModel):
    """Music status updates for web dashboard."""
    action: Literal["started", "stopped", "paused", "resumed"]
    track: Optional[Dict[str, Any]] = None
    source: str
    mode: str
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    # Phase 2.3: Client-side progress calculation fields
    start_timestamp: Optional[float] = None  # Unix timestamp for when playback started
    duration: Optional[float] = None  # Track duration in seconds


class WebVoiceStatusPayload(BaseModel):
    """Voice status updates for web dashboard."""
    status: Literal["idle", "recording", "processing", "speaking"]
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    error: Optional[str] = None
    transcript: Optional[str] = None
    confidence: Optional[float] = None


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


# MusicSourceManagerService payloads

class MusicProviderChangedPayload(BaseModel):
    """Payload for music provider change events."""
    previous_provider: str = Field(..., description="The previously active music provider")
    current_provider: str = Field(..., description="The newly active music provider")
    reason: str = Field(..., description="Reason for the provider change (user_request, fallback, auto_switch, etc.)")
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat(), description="When the change occurred")
    available_providers: List[str] = Field(..., description="List of all available providers")


class SpotifyCommandPayload(BaseModel):
    """Payload for Spotify-specific commands."""
    action: str = Field(..., description="The Spotify action to perform (play, search, auth, status, etc.)")
    query: Optional[str] = Field(None, description="Search query or command parameter")
    track_id: Optional[str] = Field(None, description="Spotify track ID")
    playlist_id: Optional[str] = Field(None, description="Spotify playlist ID")
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat(), description="When the command was issued")


class MusicSourceStatusPayload(BaseModel):
    """Payload for music source status updates."""
    provider: str = Field(..., description="The music provider name")
    status: str = Field(..., description="Provider status (available, unavailable, error, initializing)")
    health_score: float = Field(..., ge=0.0, le=1.0, description="Health score from 0.0 to 1.0")
    last_check: str = Field(default_factory=lambda: datetime.now().isoformat(), description="When the status was last checked")
    error_message: Optional[str] = Field(None, description="Error message if status is error")
    library_size: Optional[int] = Field(None, description="Number of tracks available from this provider")


class MusicLibrarySearchPayload(BaseModel):
    """Payload for cross-provider music library search requests."""
    query: str = Field(..., description="The search query")
    providers: List[str] = Field(..., description="List of providers to search")
    max_results: int = Field(default=20, description="Maximum number of results to return")
    search_type: str = Field(default="all", description="Type of search (track, artist, album, all)")
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat(), description="When the search was requested")