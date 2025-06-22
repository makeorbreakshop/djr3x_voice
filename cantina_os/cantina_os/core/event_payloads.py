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


# Spotify Web Playback SDK Payloads

class SpotifyPlayerReadyPayload(BaseModel):
    """Payload for Spotify Web Playback SDK ready event."""
    device_id: str = Field(..., description="Spotify Web Playback SDK device ID")
    device_name: str = Field(..., description="Device name for the Spotify player")
    player_ready: bool = Field(True, description="Whether the player is ready to accept commands")
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat(), description="When the player became ready")


class SpotifyAuthStatusPayload(BaseModel):
    """Payload for Spotify authentication status updates."""
    authenticated: bool = Field(..., description="Whether user is authenticated with Spotify")
    access_token: Optional[str] = Field(None, description="Spotify access token (if available)")
    expires_at: Optional[int] = Field(None, description="Unix timestamp when token expires")
    user_id: Optional[str] = Field(None, description="Spotify user ID")
    premium: Optional[bool] = Field(None, description="Whether user has Spotify Premium")
    error: Optional[str] = Field(None, description="Error message if authentication failed")
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat(), description="When the status was updated")


class SpotifyPlaybackStatePayload(BaseModel):
    """Payload for Spotify Web Playback SDK state updates."""
    is_playing: bool = Field(..., description="Whether music is currently playing")
    track_id: Optional[str] = Field(None, description="Spotify track ID")
    track_name: Optional[str] = Field(None, description="Track name")
    artist: Optional[str] = Field(None, description="Artist name")
    album: Optional[str] = Field(None, description="Album name")
    duration_ms: Optional[int] = Field(None, description="Track duration in milliseconds")
    position_ms: Optional[int] = Field(None, description="Current playback position in milliseconds")
    device_id: Optional[str] = Field(None, description="Spotify device ID")
    volume: Optional[float] = Field(None, description="Playback volume (0.0 to 1.0)")
    shuffle: Optional[bool] = Field(None, description="Shuffle state")
    repeat: Optional[str] = Field(None, description="Repeat mode (off, track, context)")
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat(), description="When the state was captured")


class SpotifyPlayFullTrackPayload(BaseModel):
    """Payload for requesting to play a full track on Spotify."""
    track_id: str = Field(..., description="Spotify track ID to play")
    device_id: Optional[str] = Field(None, description="Spotify device ID to play on")
    position_ms: Optional[int] = Field(0, description="Position to start playback from (milliseconds)")
    reason: str = Field(..., description="Reason for playing full track (preview_ended, user_request, etc.)")
    fallback_enabled: bool = Field(True, description="Whether to fall back to local if Spotify fails")
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat(), description="When the request was made")


class SpotifyOfferLocalAlternativePayload(BaseModel):
    """Payload for offering local alternative when Spotify is unavailable."""
    original_spotify_track_id: Optional[str] = Field(None, description="Original Spotify track ID requested")
    spotify_track_name: Optional[str] = Field(None, description="Name of the Spotify track")
    spotify_artist: Optional[str] = Field(None, description="Artist of the Spotify track")
    local_alternatives: List[Dict[str, Any]] = Field(..., description="List of local tracks that match")
    reason: str = Field(..., description="Reason for offering alternatives (auth_failed, premium_required, network_error, etc.)")
    auto_select: bool = Field(False, description="Whether to automatically select best match")
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat(), description="When the alternatives were offered")