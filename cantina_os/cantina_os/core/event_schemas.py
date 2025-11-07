from pydantic import BaseModel, Field
from typing import Optional, Any
import uuid
import time
# Assuming MusicTrack and MusicLibrary models exist elsewhere or need defining
# For now, using basic dict/Any types, but these should ideally be proper Pydantic models

class EventPayload(BaseModel):
    """Base class for all event payloads."""
    timestamp: float = Field(default_factory=time.time, description="Timestamp when the event was created.")
    # Add other common fields if necessary

class TrackDataPayload(BaseModel):
    """Payload for events related to a specific music track."""
    track_id: str
    title: str
    artist: str
    album: Optional[str] = None
    genre: Optional[str] = None
    duration: Optional[float] = None # Duration in seconds
    filepath: Optional[str] = None # Path to the audio file
    # Add other relevant track metadata

class TrackEndingSoonPayload(EventPayload):
    """Payload for the TRACK_ENDING_SOON event."""
    current_track: TrackDataPayload
    time_remaining: float = Field(..., description="Time remaining for the current track in seconds.")

class DjCommandPayload(EventPayload):
    """Payload for incoming DJ commands."""
    command: str = Field(..., description="The DJ command issued (e.g., 'start', 'stop', 'next').")
    args: list[str] = Field(default_factory=list, description="Arguments for the command.")
    source: str = Field(..., description="Source of the command (e.g., 'cli', 'voice').")

class DjModeChangedPayload(EventPayload):
    """Payload for DJ_MODE_START and DJ_MODE_STOP events."""
    is_active: bool = Field(..., description="True if DJ mode is active, False if stopped.")

class DjNextTrackSelectedPayload(EventPayload):
    """Payload for the DJ_NEXT_TRACK_SELECTED event."""
    next_track: TrackDataPayload
    selection_method: str = Field(..., description="Method used to select the next track (e.g., 'smart', 'random', 'queue').")

class DjCommentaryRequestPayload(EventPayload):
    """Payload for the DJ_COMMENTARY_REQUEST event."""
    context: str = Field(..., description="Context for the commentary (e.g., 'intro', 'transition').")
    current_track: Optional[TrackDataPayload] = None
    next_track: Optional[TrackDataPayload] = None
    persona: str = Field(..., description="The name of the persona to use for commentary generation.")
    request_id: str = Field(..., description="Unique identifier for this commentary request.")

class GptCommentaryResponsePayload(EventPayload):
    """Payload for the GPT_COMMENTARY_RESPONSE event."""
    request_id: str = Field(..., description="Identifier linking to the original commentary request.")
    commentary_text: str = Field(..., description="The generated commentary text.")
    is_partial: bool = Field(default=False, description="True if the response is partial (e.g., due to timeout).")
    context: str = Field(..., description="Context for the commentary (e.g., 'intro', 'transition').")

class SpeechCacheRequestPayload(EventPayload):
    """Payload for requesting speech audio to be cached."""
    text: str = Field(..., description="The text to convert to speech and cache.")
    voice_id: str = Field(..., description="The ID of the voice to use.")
    cache_key: str = Field(..., description="A unique key for this cached speech entry.")
    is_streaming: bool = Field(default=False, description="Whether the original generation request was for streaming.")
    metadata: Optional[dict[str, Any]] = Field(default_factory=dict, description="Optional metadata to store with the cache entry.")

class SpeechCachePlaybackRequestPayload(EventPayload):
    """Payload for requesting playback of cached speech."""
    cache_key: str = Field(..., description="The key for the cached speech entry to play.")
    volume: float = Field(default=1.0, description="Playback volume (0.0-1.0)")
    playback_id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Unique ID for this playback")
    delay_ms: int = Field(default=0, description="Optional delay before playback in milliseconds")
    metadata: Optional[dict[str, Any]] = Field(default_factory=dict, description="Additional metadata")
    # Add other relevant playback parameters if needed (e.g., start_time)

class SpeechCacheReadyPayload(EventPayload):
    """Payload indicating speech audio is ready in the cache."""
    cache_key: str = Field(..., description="The key for the ready cache entry.")
    audio_filepath: str = Field(..., description="The file path where the cached audio is stored.")
    duration: float = Field(..., description="The duration of the audio in seconds.")
    metadata: Optional[dict[str, Any]] = Field(default_factory=dict, description="Metadata associated with the cache entry.")

class SpeechCacheErrorPayload(EventPayload):
    """Payload for errors during speech caching."""
    cache_key: str = Field(..., description="The key for the cache entry that failed.")
    error_message: str = Field(..., description="Description of the error.")
    error_type: str = Field(..., description="Type of error (e.g., 'generation_failed', 'storage_error').")
    metadata: Optional[dict[str, Any]] = Field(default_factory=dict, description="Metadata associated with the cache entry.")

# Assuming Crossfade events use existing or simple payloads.
# If CROSSFADE_STARTED needs track info, a new payload could be defined,
# but for now, assuming it's a simple signal.

# Define placeholder models for MusicTrack and MusicLibrary if they don't exist elsewhere
# In a real scenario, these would be fully defined with relevant fields.
class MusicTrack(BaseModel):
    track_id: str
    title: str
    artist: str
    album: Optional[str] = None
    genre: Optional[str] = None
    duration: Optional[float] = None # Duration in seconds
    filepath: str # Path to the audio file

class MusicLibrary(BaseModel):
    tracks: list[MusicTrack] = Field(default_factory=list)
    library_path: str
    # Add other relevant library metadata 

class PlanReadyPayload(EventPayload):
    """Payload for the PLAN_READY event."""
    plan_id: str = Field(..., description="Unique identifier for the plan.")
    plan: dict[str, Any] = Field(..., description="The plan details. This should ideally be a specific Pydantic model like DjTransitionPlanPayload.")
    # TODO: Define a specific Pydantic model for the plan structure and use it instead of dict[str, Any]

# Placeholder models for Plan steps. Define actual step models as needed.
class BasePlanStep(BaseModel):
    """Base class for all plan steps."""
    step_type: str
    duration: Optional[float] = None # Expected duration in seconds

class PlayCachedSpeechStep(BasePlanStep):
    """Plan step to play a piece of cached speech."""
    step_type: str = "play_cached_speech"
    cache_key: str = Field(..., description="The cache key for the speech audio to play.")
    # Add other playback parameters like volume, layer, etc.

class MusicCrossfadeStep(BasePlanStep):
    """Plan step to perform a music crossfade."""
    step_type: str = "music_crossfade"
    next_track_id: str = Field(..., description="The ID of the track to fade in.")
    crossfade_duration: float = Field(..., description="The duration of the crossfade in seconds.")
    # Add other crossfade parameters

class MusicDuckStep(BasePlanStep):
    """Plan step to duck (lower) music volume during speech."""
    step_type: str = "music_duck"
    duck_level: float = Field(default=0.3, description="Volume level to duck to (0.0-1.0)")
    fade_duration_ms: int = Field(default=1500, description="Fade duration in milliseconds")

class MusicUnduckStep(BasePlanStep):
    """Plan step to restore (unduck) music volume after speech."""
    step_type: str = "music_unduck"
    fade_duration_ms: int = Field(default=1500, description="Fade duration in milliseconds")

class ParallelSteps(BasePlanStep):
    """Plan step to execute multiple steps concurrently."""
    step_type: str = "parallel_steps"
    steps: list[BasePlanStep] = Field(..., description="List of steps to execute concurrently")

class DjTransitionPlanPayload(BaseModel):
    """Represents a plan for a DJ transition (e.g., between tracks)."""
    plan_id: str = Field(..., description="Unique identifier for this transition plan.")
    steps: list[Any] = Field(..., description="A sequence of plan steps (e.g., PlayCachedSpeechStep, MusicCrossfadeStep).") # TODO: Use Union[PlayCachedSpeechStep, MusicCrossfadeStep, ...] instead of Any
    # Add other plan-specific metadata 

class CrossfadeCompletePayload(EventPayload):
    """Payload for the CROSSFADE_COMPLETE event."""
    crossfade_id: str = Field(..., description="Unique identifier for the completed crossfade.")
    status: str = Field("completed", description="Status of the crossfade (e.g., 'completed', 'error').")
    message: Optional[str] = Field(None, description="Optional message regarding the crossfade completion.") 