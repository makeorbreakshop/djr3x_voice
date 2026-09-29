"""Event payload models for CantinaOS."""

from typing import Optional, Dict, Any, List, Literal
from enum import Enum
from pydantic import BaseModel, Field, field_validator
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


# Vision Service Payloads

class VisionSceneCapturedPayload(BaseModel):
    """Payload for VISION_SCENE_CAPTURED event."""
    description: str
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    confidence: Optional[float] = None


class VisionPersonDetectedPayload(BaseModel):
    """Payload for VISION_PERSON_DETECTED event."""
    name: str
    confidence: float
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())


class VisionPersonExitedPayload(BaseModel):
    """Payload for VISION_PERSON_EXITED event."""
    name: str
    duration_seconds: float
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())


class VisionEngagementPayload(BaseModel):
    """Payload for vision engagement events (STARTED/PAUSED/ENDED)."""
    name: Optional[str] = None  # Person name if identified
    event_type: Literal["started", "paused", "ended"]
    duration_seconds: Optional[float] = None  # For paused/ended events
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())


class SpeechAmplitudePayload(BaseModel):
    """Payload for real-time speech amplitude during TTS playback."""
    conversation_id: str = Field(..., description="Conversation ID for tracking")
    amplitude: float = Field(..., description="Normalized RMS amplitude (0.0-1.0)")
    timestamp_offset: float = Field(..., description="Offset from speech start in seconds")
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    event_id: str = Field(default_factory=lambda: f"amp_{datetime.now().timestamp()}")


class ChestCommandPayload(BaseModel):
    """A command written (or, in mock mode, that would be written) to the chest-lights board."""
    command: str = Field(..., description="Serial command without newline, e.g. 'SS', 'M128', 'H1FF'")
    connected: bool = Field(..., description="True if it actually went to hardware")
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())


# ---------------------------------------------------------------------------
# Show system payloads (show/SPEC.md "Live bus contract"). Field names are the contract
# with the sim (TypeScript); keep them exactly as the spec writes them.
# ---------------------------------------------------------------------------

ShowSource = Literal["jev", "claude", "timeline", "idle", "ui", "cli"]


class ShowParams(BaseModel):
    """Trigger-time params: intensity 0-1.5 scales motion, speed 0.5-2 scales clip time.

    Out-of-range values are clamped, not rejected: a slightly-off request should still move.
    """
    intensity: float = 1.0
    speed: float = 1.0

    @field_validator("intensity")
    @classmethod
    def _clamp_intensity(cls, v: float) -> float:
        return min(1.5, max(0.0, float(v)))

    @field_validator("speed")
    @classmethod
    def _clamp_speed(cls, v: float) -> float:
        return min(2.0, max(0.5, float(v)))


class ShowPerformPayload(BaseModel):
    """show.perform - anyone asks the timeline to perform a clip, cue or sequence."""
    id: str
    params: Optional[ShowParams] = None
    source: ShowSource
    conversation_id: Optional[str] = None


class ShowStopPayload(BaseModel):
    """show.stop - end runs by id, by layer, or all of them (clips blend out, never cut)."""
    id: Optional[str] = None
    layer: Optional[str] = None
    all: Optional[bool] = None


class ShowRunPayload(BaseModel):
    """show.started / show.ended."""
    id: str
    kind: str
    source: str
    run_id: str
    reason: Optional[Literal["done", "interrupted", "rejected"]] = None
    # Not in the spec's table; carried so a show triggered by a voice turn stays traceable.
    conversation_id: Optional[str] = None


class ShowMotionPayload(BaseModel):
    """show.motion - the body compositor starts ``clip`` at ``start_at`` (epoch seconds)."""
    run_id: str
    clip: str
    intensity: float
    speed: float
    start_at: float
    layer: str
    owns: Optional[List[str]] = None


class StageLightsPayload(BaseModel):
    """stage.lights - a lights cue and/or mode; ``hold`` > 0 returns to the desk's program."""
    cue: Optional[str] = None
    mode: Optional[str] = None
    fade: float = 0.0
    hold: float = 0.0
    rig: Optional[str] = None


class ChestOverridePayload(BaseModel):
    """chest.override - send ``command`` to the chest board, resume status after ``hold`` s (0 = keep)."""
    command: str
    hold: float = 0.0


class ShowSfxPayload(BaseModel):
    """show.sfx - play a sound effect by file stem."""
    id: str


class MotionFreezePayload(BaseModel):
    """motion.freeze - the "motion stop". on: stop every show and gesture and hold; off: resume."""
    on: bool
