"""
BrainService for DJ R3X

This service acts as the "brain" for DJ R3X, handling high-level logic,
intent processing, command routing, and orchestrating DJ mode transitions,
including commentary generation and caching.
"""

"""
SERVICE: BrainService
PURPOSE: Central orchestration service for DJ mode, track selection, commentary caching, and timeline plan creation
EVENTS_IN: DJ_COMMAND, DJ_MODE_CHANGED, DJ_NEXT_TRACK, MUSIC_LIBRARY_UPDATED, GPT_COMMENTARY_RESPONSE, TRACK_ENDING_SOON, SPEECH_CACHE_READY, SPEECH_CACHE_ERROR, PLAN_ENDED
EVENTS_OUT: DJ_MODE_START, DJ_MODE_STOP, DJ_MODE_CHANGED, MUSIC_COMMAND, DJ_COMMENTARY_REQUEST, PLAN_READY, CLI_RESPONSE, MEMORY_SET, SPEECH_CACHE_REQUEST
KEY_METHODS: handle_dj_start, handle_dj_stop, handle_dj_next, handle_dj_queue, _smart_track_selection, _create_and_emit_transition_plan, _commentary_caching_loop
DEPENDENCIES: Music library, MemoryService coordination, persona files (dj_r3x-transition-persona.txt, dj_r3x-verbal-feedback-persona.txt)
"""

import asyncio
import logging
import time
import uuid
from typing import Dict, Optional, Any, List
from pydantic import BaseModel, Field, ValidationError

from ..base_service import BaseService
from cantina_os.core.event_topics import EventTopics
from cantina_os.event_payloads import (
    ServiceStatus,
    LogLevel,
    DJModeChangedPayload,
    SpeechCacheRequestPayload,
    SpeechCacheReadyPayload,
    SpeechCacheErrorPayload,
    PlanStep,  # Add import for timeline plan creation
    PlanPayload,  # Add import for timeline plan creation
)
from cantina_os.models.music_models import MusicTrack  # Use correct MusicTrack model
from cantina_os.core.event_schemas import (
    TrackDataPayload, # Import TrackDataPayload
    TrackEndingSoonPayload, # Import TrackEndingSoonPayload
    DjCommandPayload,
    DjNextTrackSelectedPayload,
    DjCommentaryRequestPayload, # Import from event_schemas instead of event_payloads
    GptCommentaryResponsePayload, # Import GptCommentaryResponsePayload
    PlanReadyPayload, # Import PlanReadyPayload
    DjTransitionPlanPayload, # Import DjTransitionPlanPayload
    PlayCachedSpeechStep, # Import PlayCachedSpeechStep
    MusicCrossfadeStep, # Import MusicCrossfadeStep
    MusicDuckStep, # Import MusicDuckStep for ducking music during speech
    MusicUnduckStep, # Import MusicUnduckStep for restoring music volume
    ParallelSteps # Import ParallelSteps for concurrent step execution
)
from ..utils.command_decorators import compound_command, register_service_commands, validate_compound_command, command_error_handler

# Define BrainService configuration model
class BrainServiceConfig(BaseModel):
    """Configuration for BrainService."""
    max_recent_tracks: int = Field(default=10, description="Maximum number of recently played tracks to remember")
    commentary_cache_interval: int = Field(default=30, description="Interval in seconds for the commentary caching loop")
    dj_persona_path: str = Field(default="dj_r3x-transition-persona.txt", description="Path to the DJ R3X transition persona file for commentary generation.")
    verbal_feedback_persona_path: str = Field(default="dj_r3x-verbal-feedback-persona.txt", description="Path to the DJ R3X verbal feedback persona file, relative to execution dir or findable in common locations.")
    tts_voice_id: str = Field(default="YOUR_DEFAULT_VOICE_ID", description="Default voice ID for TTS caching") # Add default voice ID config
    crossfade_duration: float = Field(default=8.0, description="Default duration for music crossfades in seconds") # Add crossfade duration config


class BrainService(BaseService):
    """Service for core logic and orchestration."""

    def __init__(self, event_bus, config=None, name="brain_service"):
        """Initialize the service with proper event bus and config."""
        super().__init__(service_name=name, event_bus=event_bus)

        # Store name as property
        self.name = name

        # Convert config dict to Pydantic model
        self._config = BrainServiceConfig(**(config or {}))
        
        # Set default command topic for auto-registration
        self._default_command_topic = EventTopics.DJ_COMMAND

        # Initialize state
        self._dj_mode_active = False
        self._recently_played_tracks: List[str] = [] # Track names
        self._music_library: Dict[str, MusicTrack] = {} # Store music library data
        self._tasks: List[asyncio.Task] = [] # List to hold background tasks
        self._next_track_commentary_cached = False # Flag to track if commentary for the next track is cached
        self._current_track: Optional[MusicTrack] = None
        self._next_track: Optional[MusicTrack] = None
        self._dj_persona: str = "" # Store DJ persona text
        self._verbal_feedback_persona: str = "" # Store verbal feedback persona text
        
        # Get reference to MemoryService for state coordination
        # This will be set during startup once services are available
        self._memory_service = None
        
        # Deprecated: These internal cache tracking dictionaries are being replaced by MemoryService
        # TODO: Remove these completely once refactor is complete
        # Dictionary to map commentary request IDs to speech cache keys.
        # Used to track which cache key corresponds to which commentary request.
        # Expected format: {request_id: cache_key}
        self._commentary_cache_keys: Dict[str, str] = {} # Map request_id to cache_key
        # Dictionary to track if cached commentary is ready for a given cache key.
        # Expected format: {cache_key: bool (True if ready, False otherwise)}
        self._cached_commentary_ready: Dict[str, bool] = {} # Map cache_key to readiness status
        # Map commentary request IDs to the next track they are for
        self._commentary_request_next_track: Dict[str, MusicTrack] = {} # Map request_id to next_track

    async def _start(self) -> None:
        """Start the service and initialize resources."""
        await self._setup_subscriptions()

        # Load personas
        await self._load_personas()

        # Start the continuous commentary caching task
        commentary_task = asyncio.create_task(self._commentary_caching_loop())
        self._tasks.append(commentary_task)
        commentary_task.add_done_callback(self._handle_task_exception)
        
        # Auto-register compound commands using decorators
        register_service_commands(self, self._event_bus)
        self.logger.info("Auto-registered DJ commands using decorators")

        await self._emit_status(
            ServiceStatus.RUNNING,
            "BrainService started successfully",
            severity=LogLevel.INFO
        )
        self.logger.info("Brain service started")

        self._next_track = None
        self._next_track_commentary_cached = False # Reset cache status
        self._commentary_cache_keys.clear()
        self._cached_commentary_ready.clear()
        self._commentary_request_next_track.clear()

    async def _stop(self) -> None:
        """Stop the brain service and clean up resources."""
        try:
            # Cancel background tasks
            for task in self._tasks:
                if not task.done():
                    task.cancel()
            
            # Wait for tasks to complete cancellation
            if self._tasks:
                await asyncio.gather(*self._tasks, return_exceptions=True)
                
            self.logger.info("BrainService stopped successfully")
            
        except Exception as e:
            self.logger.error(f"Error stopping BrainService: {e}", exc_info=True)

    async def _setup_subscriptions(self) -> None:
        """Set up event subscriptions."""
        # Collect all subscription tasks to ensure they complete before proceeding
        subscription_tasks = []
        
        subscription_tasks.append(asyncio.create_task(self.subscribe(
            EventTopics.DJ_COMMAND,
            self._handle_dj_command
        )))
        subscription_tasks.append(asyncio.create_task(self.subscribe(
            EventTopics.DJ_MODE_CHANGED,
            self._handle_dj_mode_changed
        )))
        subscription_tasks.append(asyncio.create_task(self.subscribe(
            EventTopics.DJ_NEXT_TRACK,
            self._handle_dj_next_track
        )))
        subscription_tasks.append(asyncio.create_task(self.subscribe(
            EventTopics.MUSIC_LIBRARY_UPDATED,
            self._handle_music_library_updated
        )))
        subscription_tasks.append(asyncio.create_task(self.subscribe(
            EventTopics.GPT_COMMENTARY_RESPONSE,
            self._handle_gpt_commentary_response
        )))
        subscription_tasks.append(asyncio.create_task(self.subscribe(
            EventTopics.TRACK_ENDING_SOON,
            self._handle_track_ending_soon
        )))
        subscription_tasks.append(asyncio.create_task(self.subscribe(
            EventTopics.SPEECH_CACHE_READY,
            self._handle_speech_cache_ready
        )))
        subscription_tasks.append(asyncio.create_task(self.subscribe(
            EventTopics.SPEECH_CACHE_ERROR,
            self._handle_speech_cache_error
        )))
        
        # Add subscription for timeline execution errors
        subscription_tasks.append(asyncio.create_task(self.subscribe(
            EventTopics.PLAN_ENDED,
            self._handle_plan_ended
        )))
        
        # Wait for all subscriptions to complete before proceeding
        await asyncio.gather(*subscription_tasks)
        
        # Add completed tasks to self._tasks for proper cleanup
        self._tasks.extend(subscription_tasks)
        
        self.logger.debug("All BrainService event subscriptions established successfully")

    def _handle_task_exception(self, task: asyncio.Task) -> None:
        """Handle exceptions raised by background tasks."""
        try:
            exception = task.exception()
            if exception:
                self.logger.error(f"Background task failed with exception: {exception}")
                self._emit_status(
                    ServiceStatus.ERROR,
                    f"Background task error: {exception}",
                    severity=LogLevel.ERROR
                )
        except asyncio.CancelledError:
            pass # Task was cancelled, this is expected during shutdown

    def _create_track_data_payload(self, track: MusicTrack) -> TrackDataPayload:
        """
        Create a TrackDataPayload from a MusicTrack.
        Ensures all required fields are properly populated.
        """
        return TrackDataPayload(
            track_id=track.track_id,
            title=track.title,
            artist=track.artist or "Cantina Band",  # Use default if None
            album=track.album,
            genre=track.genre,
            duration=track.duration
        )

    async def _load_personas(self) -> None:
        """Load DJ personas from files."""
        
        persona_loaded = False
        # Try loading DJ persona from a list of common paths
        dj_persona_paths_to_try = [
            self._config.dj_persona_path, # Configured path first (should be dj_r3x-transition-persona.txt)
            "dj_r3x-transition-persona.txt", # Current directory
            "cantina_os/dj_r3x-transition-persona.txt", # Inside cantina_os (if run from parent)
            "../dj_r3x-transition-persona.txt", # Parent directory (if run from a subdir)
            "dj_r3x-persona.txt", # Fallback to general persona
            "cantina_os/dj_r3x-persona.txt", # Inside cantina_os (if run from parent)
            "../dj_r3x-persona.txt" # Parent directory (if run from a subdir)
        ]
        for path in dj_persona_paths_to_try:
            if not path: continue
            try:
                # Attempt to construct an absolute path if not already, or handle relative paths correctly
                # For simplicity here, assuming paths are relative to where search happens or are absolute
                with open(path, "r", encoding="utf-8") as f:
                    self._dj_persona = f.read()
                self.logger.info(f"Loaded DJ persona from {path}")
                persona_loaded = True
                break
            except FileNotFoundError:
                self.logger.debug(f"DJ persona file not found at {path}")
            except Exception as e:
                self.logger.error(f"Error loading DJ persona from {path}: {e}")
                break # If other error, stop trying

        if not persona_loaded:
            self.logger.error(f"Failed to load DJ persona from any attempted paths. Defaulting to empty.")
            self._dj_persona = ""
            await self._emit_status(
                ServiceStatus.DEGRADED,
                f"Missing DJ persona file. Looked in: {dj_persona_paths_to_try}",
                severity=LogLevel.ERROR
            )

        feedback_persona_loaded = False
        verbal_feedback_paths_to_try = [
            self._config.verbal_feedback_persona_path,
            "dj_r3x-verbal-feedback-persona.txt",
            "cantina_os/dj_r3x-verbal-feedback-persona.txt",
            "../dj_r3x-verbal-feedback-persona.txt"
        ]
        for path in verbal_feedback_paths_to_try:
            if not path: continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self._verbal_feedback_persona = f.read()
                self.logger.info(f"Loaded verbal feedback persona from {path}")
                feedback_persona_loaded = True
                break
            except FileNotFoundError:
                self.logger.debug(f"Verbal feedback persona file not found at {path}")
            except Exception as e:
                self.logger.error(f"Error loading verbal feedback persona from {path}: {e}")
                break

        if not feedback_persona_loaded:
            self.logger.error(f"Failed to load verbal feedback persona. Defaulting to empty.")
            self._verbal_feedback_persona = ""
            # Optionally emit degraded status for this too
            await self._emit_status(
                ServiceStatus.DEGRADED,
                f"Missing verbal feedback persona file. Looked in: {verbal_feedback_paths_to_try}",
                severity=LogLevel.WARNING # Or ERROR if critical
            )

    async def _handle_dj_mode_changed(self, payload: Dict[str, Any]) -> None:
        """Handle DJ mode activation/deactivation."""
        try:
            # Use Pydantic model for incoming payload
            mode_change_payload = DJModeChangedPayload(**payload)
            dj_mode_active = mode_change_payload.is_active

            if dj_mode_active:
                self.logger.info("DJ mode activated")
                self._dj_mode_active = True

                # --- Initiate initial DJ mode sequence ---
                # Select initial track (this happens outside the caching loop)
                track_name = await self._smart_track_selection()
                if not track_name:
                    self.logger.warning("No tracks available for DJ mode")
                    # Emit DJ mode stop if no tracks
                    await self.emit(EventTopics.DJ_MODE_STOP, DJModeChangedPayload(is_active=False).model_dump())
                    return

                # Get track metadata
                self._current_track = self._music_library.get(track_name)
                if not self._current_track:
                     self.logger.error(f"Metadata not found for initial track: {track_name}")
                     await self.emit(EventTopics.DJ_MODE_STOP, DJModeChangedPayload(is_active=False).model_dump())
                     return

                # Store track in MemoryService as single source of truth
                await self.emit(EventTopics.MEMORY_SET, {
                    "key": "current_track", 
                    "value": self._current_track.model_dump()
                })
                
                # Add to recently played
                self._recently_played_tracks.append(track_name)
                if len(self._recently_played_tracks) > self._config.max_recent_tracks:
                    self._recently_played_tracks.pop(0)

                # Update MemoryService with track history
                await self.emit(EventTopics.MEMORY_SET, {
                    "key": "dj_track_history",
                    "value": self._recently_played_tracks.copy()
                })

                # Emit DJ mode start event with initial track - tell MusicController which track to play
                await self.emit(
                    EventTopics.DJ_MODE_START,
                    DJModeChangedPayload(
                        is_active=True,
                        # Note: current_track and next_track info will be managed internally
                        # and potentially included in other specific events or state updates
                    ).model_dump()
                )

                # NEW: Emit specific play command to MusicController with the selected track
                await self.emit(
                    EventTopics.MUSIC_COMMAND,
                    {
                        "action": "play",
                        "song_query": track_name,
                        "conversation_id": None
                    }
                )

                self.logger.info(f"DJ mode: Instructed MusicController to play '{track_name}'")

                # Generate initial commentary for the selected track
                self.logger.info(f"Requesting initial commentary generation for track: {self._current_track.title}")
                
                # Generate a unique request ID for this commentary request
                request_id = str(uuid.uuid4())
                cache_key = f"commentary_{request_id[:8]}"  # Short cache key
                
                # Store the linkage between the request ID and the cache key
                self._commentary_cache_keys[request_id] = cache_key
                
                # Create commentary request for the CURRENT track as an "intro"
                intro_request = DjCommentaryRequestPayload(
                    timestamp=time.time(),
                    context="intro",
                    current_track=self._create_track_data_payload(self._current_track),
                    next_track=None,  # No next track for intro
                    persona=self._dj_persona,
                    request_id=request_id
                )
                
                self.logger.info(f"Initial commentary requested with ID: {request_id}, cache_key: {cache_key}")
                await self.emit(EventTopics.DJ_COMMENTARY_REQUEST, intro_request.model_dump())

                # The commentary caching loop starts and will select the NEXT track and cache its intro/transition commentary.
                # The loop will update self._next_track and trigger the commentary request.
                self._next_track = None # Ensure next track is selected by the loop
                self._next_track_commentary_cached = False # Reset cache status
                # DON'T clear the cache tracking - we need it for initial commentary
                # self._commentary_cache_keys.clear() # Clear previous cache key mapping
                # self._cached_commentary_ready.clear() # Clear previous cache readiness status
                # self._commentary_request_next_track.clear()

            else:
                self.logger.info("DJ mode deactivated")
                self._dj_mode_active = False
                self._recently_played_tracks.clear()
                self._current_track = None
                self._next_track = None
                self._next_track_commentary_cached = False # Reset cache status
                self._commentary_cache_keys.clear()
                self._cached_commentary_ready.clear()
                self._commentary_request_next_track.clear()

                # Emit DJ mode stop event
                await self.emit(
                    EventTopics.DJ_MODE_STOP,
                     DJModeChangedPayload(
                        is_active=False,
                    ).model_dump()
                )

        except Exception as e:
            self.logger.error(f"Error handling DJ mode change: {e}", exc_info=True)

    async def _smart_track_selection(self, query: str = None) -> Optional[str]:
        """Smart track selection for both voice commands and DJ mode."""
        try:
            # Get all available tracks from the music library attribute
            available_tracks = list(self._music_library.keys())
            if not available_tracks:
                self.logger.warning("No tracks available in music library")
                return None

            # Filter out recently played tracks for DJ mode if active
            if self._dj_mode_active:
                available_tracks = [t for t in available_tracks if t not in self._recently_played_tracks]
                if not available_tracks:
                    # If all tracks have been played recently, clear history and try again
                    self._recently_played_tracks.clear()
                    available_tracks = list(self._music_library.keys())

            # Basic random selection for now. Enhance with genre/energy matching later.
            import random
            selected_track_name = random.choice(available_tracks)
            return selected_track_name

        except Exception as e:
            self.logger.error(f"Error during smart track selection: {e}", exc_info=True)
            return None

    async def _handle_dj_next_track(self, payload: Dict[str, Any]) -> None:
        """Handle the DJ_NEXT_TRACK command (e.g., via CLI)."""
        self.logger.info("Handling DJ_NEXT_TRACK command")
        # This command should trigger the execution of the *already planned* next transition.
        # The logic for preparing the next transition is in the _commentary_caching_loop and _handle_track_ending_soon.
        # If a plan is ready, we should activate it via TimelineExecutorService.
        # If not ready, we might need to generate/cache on demand (potentially causing a delay).

        # For now, let's assume _handle_track_ending_soon or the caching loop prepares the plan.
        # We need a way to signal TimelineExecutorService to execute the *currently ready* plan.
        # This might require emitting a specific event or calling a TimelineExecutorService method.
        # Let's emit a placeholder event for now, assuming TimelineExecutorService listens for it.

        # TODO: Implement actual plan activation logic here.
        # This might involve storing the ready plan ID and emitting an event like PLAN_EXECUTE.
        if self._next_track and self._next_track_commentary_cached:
             self.logger.info(f"Forcing transition to next track: {self._next_track.title}")
             # Assuming the PLAN_READY event for this transition has already been emitted
             # We might need to store the plan_id and emit a PLAN_EXECUTE event
             # For now, let's re-trigger the logic that would normally happen at TRACK_ENDING_SOON
             # This is NOT ideal and should be replaced with actual plan activation.

             # Simulate triggering the logic that would normally happen at TRACK_ENDING_SOON
             # Create a dummy TrackEndingSoonPayload
             if self._current_track:
                 dummy_payload = TrackEndingSoonPayload(
                     timestamp=time.time(),
                     current_track=TrackDataPayload(**self._current_track.model_dump()),
                     time_remaining=0.0 # Simulate end of track
                 ).model_dump()
                 await self._handle_track_ending_soon(dummy_payload)
             else:
                 self.logger.warning("Cannot force next track, current track is not set.")
        else:
             self.logger.warning("Next track commentary not cached, forcing next track may be delayed.")
             # Potentially trigger on-demand generation/caching and then execution
             # This is a complex scenario that needs further refinement based on TimelineExecutorService capabilities.


    async def _handle_dj_command(self, payload: Dict[str, Any]) -> None:
        """Handle incoming DJ command events.
        
        This handler dispatches CLI commands routed via CommandDispatcher to 
        the appropriate decorated methods (handle_dj_start, handle_dj_stop, etc.).
        """
        self.logger.debug(f"_handle_dj_command received payload: {payload}")
        
        # Handle CLI command payloads - dispatch to decorated methods
        if isinstance(payload, dict) and "command" in payload:
            command = payload.get("command", "")
            subcommand = payload.get("subcommand", "")
            
            # Create command pattern for matching
            if subcommand:
                command_pattern = f"{command} {subcommand}"
            else:
                command_pattern = command
            
            self.logger.debug(f"Dispatching DJ command: {command_pattern}")
            
            # Dispatch to appropriate decorated method
            if command_pattern == "dj start":
                await self.handle_dj_start(payload)
            elif command_pattern == "dj stop":
                await self.handle_dj_stop(payload)
            elif command_pattern == "dj next":
                await self.handle_dj_next(payload)
            elif command_pattern == "dj queue":
                await self.handle_dj_queue(payload)
            else:
                self.logger.warning(f"Unknown DJ command pattern: {command_pattern}")
                await self._send_error(f"Unknown DJ command: {command_pattern}")
            return
        
        # Handle legacy direct DjCommandPayload format (from other services)
        try:
            command_payload = DjCommandPayload(**payload)
            command = command_payload.command
            args = command_payload.args

            self.logger.info(f"Received legacy DJ command: {command} with args {args}")

            if command == "start":
                if not self._dj_mode_active:
                    await self.emit(EventTopics.SYSTEM_MODE_CHANGE, {"mode": "dj"})
                else:
                    self.logger.info("DJ mode is already active.")

            elif command == "stop":
                if self._dj_mode_active:
                     await self.emit(EventTopics.SYSTEM_MODE_CHANGE, {"mode": "standard"})
                else:
                    self.logger.info("DJ mode is not active.")

            elif command == "next":
                if self._dj_mode_active:
                    await self.emit(EventTopics.DJ_NEXT_TRACK, {})
                else:
                    self.logger.warning("Cannot skip track, DJ mode is not active.")

            elif command == "queue":
                if args:
                    track_name = " ".join(args)
                    self.logger.warning(f"Queue command received for '{track_name}', but queuing is not yet implemented.")
                else:
                    self.logger.warning("Queue command requires a track name.")

            else:
                self.logger.warning(f"Unknown DJ command: {command}")

        except Exception as e:
            self.logger.error(f"Error handling DJ command: {e}", exc_info=True)

    async def _handle_music_library_updated(self, payload: Dict[str, Any]) -> None:
        """Handle updates to the music library."""
        self.logger.info("Music library updated")
        # Assuming the payload contains the full music library data
        # TODO: Define a Pydantic model for MusicLibraryUpdatedPayload if needed
        # For now, assuming payload contains 'tracks' which is a dict mapping track_name to MusicTrack data
        if 'tracks' in payload:
            # Convert incoming track data (assuming dict) to MusicTrack models
            # This requires that the incoming payload structure matches what MusicTrack expects
            try:
                self._music_library = {name: MusicTrack(**data) for name, data in payload['tracks'].items()}
                self.logger.info(f"Loaded {len(self._music_library)} tracks into the music library.")
                # If DJ mode is active and current/next tracks are not set, select one
                if self._dj_mode_active and not self._current_track:
                     self.logger.info("DJ mode active, selecting initial track after library update.")
                     track_name = await self._smart_track_selection()
                     if track_name:
                         self._current_track = self._music_library.get(track_name)
                         self.logger.info(f"Selected initial track: {self._current_track.title}")
                         # Trigger initial commentary caching for the next track
                         await self._select_and_cache_next_track_commentary()

            except Exception as e:
                self.logger.error(f"Error processing music library update: {e}", exc_info=True)


    async def _commentary_caching_loop(self) -> None:
        """Continuous loop to select the next track and cache its commentary."""
        self.logger.info("Starting commentary caching loop")
        while True:
            try:
                if self._dj_mode_active and not self._next_track_commentary_cached and self._current_track:
                    self.logger.debug("Commentary caching loop: Selecting next track and requesting commentary")

                    # Select the next track
                    next_track_name = await self._smart_track_selection()
                    if not next_track_name:
                        self.logger.warning("Commentary caching loop: No tracks available to select next.")
                        # Wait before trying again
                        await asyncio.sleep(self._config.commentary_cache_interval)
                        continue

                    self._next_track = self._music_library.get(next_track_name)

                    if not self._next_track:
                        self.logger.error(f"Commentary caching loop: Metadata not found for next track: {next_track_name}")
                         # Wait before trying again
                        await asyncio.sleep(self._config.commentary_cache_interval)
                        continue

                    self.logger.info(f"Commentary caching loop: Selected next track: {self._next_track.title}")

                    # CRITICAL FIX: Emit DJ_NEXT_TRACK_SELECTED event for dashboard queue updates
                    try:
                        track_payload = self._create_track_data_payload(self._next_track).model_dump()
                        self.logger.info(f"CRITICAL FIX: About to emit DJ_NEXT_TRACK_SELECTED for track: {self._next_track.title}")
                        await self.emit(EventTopics.DJ_NEXT_TRACK_SELECTED, {
                            "track": track_payload,
                            "timestamp": time.time(),
                            "source": "brain_service"
                        })
                        self.logger.info(f"CRITICAL FIX: Successfully emitted DJ_NEXT_TRACK_SELECTED event for track: {self._next_track.title}")
                    except Exception as e:
                        self.logger.error(f"CRITICAL FIX: Failed to emit DJ_NEXT_TRACK_SELECTED event: {e}", exc_info=True)

                    # Generate a unique request ID for this commentary request
                    request_id = str(uuid.uuid4())
                    # Store the linkage between the request ID and the selected next track
                    self._commentary_request_next_track[request_id] = self._next_track

                    # Request commentary from GPTService
                    # Use the DJ transition persona
                    # Pass current and next track metadata for contextual commentary
                    commentary_request_payload = DjCommentaryRequestPayload(
                        timestamp=time.time(),
                        context="transition",
                        current_track=TrackDataPayload(**self._current_track.model_dump()),
                        next_track=TrackDataPayload(**self._next_track.model_dump()),
                        persona=self._dj_persona, # Use the loaded DJ persona
                        request_id=request_id
                    )

                    self.logger.info(f"Commentary caching loop: Emitting DJ_COMMENTARY_REQUEST with request_id: {request_id}")
                    await self.emit(
                        EventTopics.DJ_COMMENTARY_REQUEST,
                        commentary_request_payload.model_dump()
                    )

                    # Mark that we have requested commentary for this next track
                    # We will set _next_track_commentary_cached to True when we receive SPEECH_CACHE_READY for this request.
                    # Store the linkage between the request_id and the expected cache_key (which will be generated in _handle_gpt_commentary_response)
                    # For simplicity now, let's assume the cache_key will be the request_id + ".mp3" or similar.
                    # A more robust approach would be to get the cache_key back from the SpeechCacheRequestPayload emission or the Ready event.
                    # Let's update _handle_gpt_commentary_response to generate the cache key and store the mapping.
                    # For now, mark the commentary request as pending caching.
                    self._next_track_commentary_cached = False # Still False until cache ready

                # Wait for the configured interval before checking again
                await asyncio.sleep(self._config.commentary_cache_interval)

            except asyncio.CancelledError:
                self.logger.info("Commentary caching loop cancelled")
                break # Exit loop when cancelled
            except Exception as e:
                self.logger.error(f"Error in commentary caching loop: {e}", exc_info=True)
                # Wait a bit before retrying after an error
                await asyncio.sleep(self._config.commentary_cache_interval)

    async def _handle_gpt_commentary_response(self, payload: Dict[str, Any]) -> None:
        """Handle the GPT_COMMENTARY_RESPONSE event and trigger speech caching."""
        self.logger.info("Handling GPT_COMMENTARY_RESPONSE")
        try:
            # Use Pydantic model for incoming payload with error handling
            try:
                response_payload = GptCommentaryResponsePayload(**payload)
            except ValidationError as e:
                self.logger.error(f"Validation error in GPT_COMMENTARY_RESPONSE payload: {e}")
                return
                
            request_id = response_payload.request_id
            commentary_text = response_payload.commentary_text
            is_partial = response_payload.is_partial
            context = response_payload.context  # Now available in the payload

            self.logger.info(f"Received commentary response for request_id: {request_id}. Context: {context}, Partial: {is_partial}")
            self.logger.debug(f"Commentary text: {commentary_text[:100]}...") # Log snippet

            if not commentary_text:
                self.logger.warning(f"Received empty commentary text for request_id: {request_id}")
                return

            # Route based on context: intro uses timeline plan with speak step, transitions use caching
            if context == "intro":
                self.logger.info(f"Processing INTRO commentary via timeline plan for request_id: {request_id}")
                
                # Create timeline plan with speak step for proper ducking coordination
                await self._create_initial_commentary_timeline_plan(commentary_text, request_id)
                    
            elif context == "transition":
                self.logger.info(f"Processing TRANSITION commentary as cached speech for request_id: {request_id}")
                
                # Generate a cache key based on the request ID or use existing mapping
                cache_key = await self._get_commentary_cache_key(request_id)
                if not cache_key:
                    cache_key = f"commentary_{request_id}"
                    # Store the mapping using MemoryService
                    next_track_for_mapping = self._commentary_request_next_track.get(request_id)
                    await self._store_commentary_cache_mapping(request_id, cache_key, next_track_for_mapping)
                    
                    # Also store in legacy dict during transition
                    self._commentary_cache_keys[request_id] = cache_key # Store mapping
                
                # Mark cache as not ready initially using MemoryService
                await self._set_commentary_cache_ready(cache_key, False)
                self._cached_commentary_ready[cache_key] = False  # Legacy fallback

                # Request speech caching from CachedSpeechService with error handling
                try:
                    cache_request_payload = SpeechCacheRequestPayload(
                        timestamp=time.time(),
                        text=commentary_text,
                        voice_id=self._config.tts_voice_id, # Use configured voice ID
                        cache_key=cache_key,
                        is_streaming=False, # Commentary is for caching, not streaming playback
                        metadata={
                            "commentary_request_id": request_id,
                            "context": context,
                        }
                    )
                except ValidationError as e:
                    self.logger.error(f"Validation error creating SpeechCacheRequestPayload: {e}")
                    return

                self.logger.info(f"Emitting SPEECH_CACHE_REQUEST for cache_key: {cache_key}")
                await self.emit(
                    EventTopics.SPEECH_CACHE_REQUEST,
                    cache_request_payload.model_dump()
                )

                # Check if this is a transition commentary
                next_track_for_request = self._commentary_request_next_track.get(request_id)
                if next_track_for_request is not None:
                    self.logger.info(f"Transition commentary caching requested for request_id: {request_id}")
                    
            else:
                self.logger.warning(f"Unknown context '{context}' for commentary request_id: {request_id}, falling back to caching")
                # Fallback to caching for unknown contexts - implement if needed
                pass

        except Exception as e:
            self.logger.error(f"Error handling GPT commentary response: {e}", exc_info=True)

    async def _handle_speech_cache_ready(self, payload: Dict[str, Any]) -> None:
        """Handle the SPEECH_CACHE_READY event and update internal state."""
        self.logger.info("Handling SPEECH_CACHE_READY")
        try:
            # Use Pydantic model for incoming payload with error handling
            try:
                ready_payload = SpeechCacheReadyPayload(**payload)
            except ValidationError as e:
                self.logger.error(f"Validation error in SPEECH_CACHE_READY payload: {e}")
                return
                
            cache_key = ready_payload.cache_key
            duration = ready_payload.duration_ms / 1000.0  # Convert to seconds
            metadata = ready_payload.metadata

            self.logger.info(f"Speech cache ready for cache_key: {cache_key} (Duration: {duration:.2f}s)")

            # Mark the cache entry as ready using MemoryService
            await self._set_commentary_cache_ready(cache_key, True, duration)
            
            # Legacy fallback during transition
            if cache_key in self._cached_commentary_ready:
                 self._cached_commentary_ready[cache_key] = True
                 self.logger.info(f"Marked cache_key {cache_key} as ready.")

                 # Get commentary request ID from metadata
                 commentary_request_id = metadata.get("commentary_request_id")
                 
                 if commentary_request_id:
                     # Handle transition commentary caching readiness
                     if commentary_request_id in self._commentary_request_next_track:
                         associated_next_track = self._commentary_request_next_track[commentary_request_id]
                         # Check if this cached commentary is for the CURRENTLY planned next track
                         if self._next_track and associated_next_track and associated_next_track.track_id == self._next_track.track_id:
                             self._next_track_commentary_cached = True # Mark as cached and ready
                             self.logger.info(f"Speech cache for next track '{self._next_track.title}' is ready (cache_key: {cache_key}).")
                         else:
                             self.logger.debug(f"Speech cache ready, but not for the currently planned next track. Expected: {self._next_track.track_id if self._next_track else 'None'}, Got: {associated_next_track.track_id if associated_next_track else 'None'}")
                 else:
                     self.logger.debug(f"Speech cache ready for cache_key {cache_key} but no commentary_request_id in metadata")
            else:
                self.logger.warning(f"Received SPEECH_CACHE_READY for unknown cache_key: {cache_key}")

        except Exception as e:
            self.logger.error(f"Error handling SPEECH_CACHE_READY: {e}", exc_info=True)

    async def _handle_speech_cache_error(self, payload: Dict[str, Any]) -> None:
        """Handle the SPEECH_CACHE_ERROR event and update internal state."""
        self.logger.error("Handling SPEECH_CACHE_ERROR")
        try:
            # Use Pydantic model for incoming payload with error handling
            try:
                error_payload = SpeechCacheErrorPayload(**payload)
            except ValidationError as e:
                self.logger.error(f"Validation error in SPEECH_CACHE_ERROR payload: {e}")
                return
                
            cache_key = error_payload.cache_key
            error_message = error_payload.error  # FIXED: Use correct field name 'error'
            metadata = error_payload.metadata # Get metadata to find request_id

            self.logger.error(f"Speech cache error for cache_key {cache_key}: {error_message}")

            # Mark the cache entry as not ready (or failed)
            if cache_key in self._cached_commentary_ready:
                self._cached_commentary_ready[cache_key] = False
                self.logger.warning(f"Marked cache_key {cache_key} as not ready due to error.")

                # If this error is for the next track's commentary, reset the flag
                # Use the linkage stored in _commentary_request_next_track
                commentary_request_id = metadata.get("commentary_request_id")
                if commentary_request_id and commentary_request_id in self._commentary_request_next_track:
                    associated_next_track = self._commentary_request_next_track[commentary_request_id]
                    # Check if this error is for the CURRENTLY planned next track
                    if self._next_track and associated_next_track and associated_next_track.track_id == self._next_track.track_id:
                        self.logger.warning("Resetting _next_track_commentary_cached due to error for current next track.")
                        self._next_track_commentary_cached = False
                        # TODO: Consider triggering a new commentary request immediately for the same next track
                        # asyncio.create_task(self._select_and_cache_next_track_commentary())
                    else:
                        self.logger.warning(f"Speech cache error for cache_key {cache_key} (request_id: {commentary_request_id}) but it does not match the current next track ('{self._next_track.title if self._next_track else 'None'}').")
                elif commentary_request_id:
                    self.logger.warning(f"Speech cache error for cache_key {cache_key} but request_id {commentary_request_id} not found in _commentary_request_next_track.")
            else:
                self.logger.warning(f"Received SPEECH_CACHE_ERROR for unknown cache_key: {cache_key}")

        except Exception as e:
            self.logger.error(f"Error handling SPEECH_CACHE_ERROR: {e}", exc_info=True)

    async def _handle_track_ending_soon(self, payload: Dict[str, Any]) -> None:
        """Handle the TRACK_ENDING_SOON event and create transition plans."""
        self.logger.info("Handling TRACK_ENDING_SOON")
        try:
            # Use Pydantic model for incoming payload
            try:
                ending_soon_payload = TrackEndingSoonPayload(**payload)
            except ValidationError as e:
                self.logger.error(f"Validation error in TRACK_ENDING_SOON payload: {e}")
                return
                
            current_track_data = ending_soon_payload.current_track
            time_remaining = ending_soon_payload.time_remaining

            self.logger.info(f"Track '{current_track_data.title}' ending soon. {time_remaining:.2f}s remaining.")

            # Only proceed if DJ mode is active
            if not self._dj_mode_active:
                self.logger.debug("DJ mode not active, ignoring TRACK_ENDING_SOON event.")
                return

            # Ensure current track state is updated based on event payload
            if self._current_track is None or self._current_track.track_id != current_track_data.track_id:
                self.logger.warning("BrainService current track state out of sync with TRACK_ENDING_SOON event.")
                self._current_track = self._music_library.get(current_track_data.track_id)
                if not self._current_track:
                    self.logger.error(f"Current track from event not found in music library: {current_track_data.title}")
                    return

            # Check if we have a next track selected and attempt to create transition plan
            if self._next_track:
                self.logger.info(f"Next track '{self._next_track.title}' selected. Creating transition plan.")
                await self._create_and_emit_transition_plan()
            else:
                # No next track selected - attempt immediate selection and create simple crossfade
                self.logger.warning("No next track selected for ending track. Attempting immediate selection.")
                await self._handle_emergency_track_selection()

        except Exception as e:
            self.logger.error(f"Error handling TRACK_ENDING_SOON: {e}", exc_info=True)
            # Emergency fallback - try to continue playing current track or stop gracefully
            await self._handle_transition_failure()

    async def _handle_emergency_track_selection(self) -> None:
        """Handle emergency track selection when no next track is prepared."""
        try:
            next_track_name = await self._smart_track_selection()
            if not next_track_name:
                self.logger.error("Emergency track selection failed - no tracks available")
                await self._handle_transition_failure()
                return

            self._next_track = self._music_library.get(next_track_name)
            if not self._next_track:
                self.logger.error(f"Emergency selected track not found in library: {next_track_name}")
                await self._handle_transition_failure()
                return

            self.logger.info(f"Emergency selected next track: {self._next_track.title}")
            
            # Create simple crossfade plan without commentary (no time for caching)
            await self._create_fallback_transition_plan()
            
        except Exception as e:
            self.logger.error(f"Error in emergency track selection: {e}", exc_info=True)
            await self._handle_transition_failure()

    async def _handle_transition_failure(self) -> None:
        """Handle complete transition failure with graceful degradation."""
        self.logger.error("Transition failure - implementing emergency fallback")
        try:
            # Option 1: Repeat current track if available
            if self._current_track:
                self.logger.info("Emergency: Repeating current track")
                fallback_plan = self._create_simple_repeat_plan()
                await self._emit_validated_plan(fallback_plan)
                return
            
            # Option 2: Stop music gracefully
            self.logger.warning("Emergency: No fallback available, stopping music")
            await self.emit(EventTopics.MUSIC_COMMAND, {
                "action": "stop",
                "conversation_id": None
            })
            
            # Reset DJ mode state
            self._current_track = None
            self._next_track = None
            self._next_track_commentary_cached = False
            
        except Exception as e:
            self.logger.critical(f"Critical failure in transition fallback: {e}", exc_info=True)

    def _create_simple_repeat_plan(self) -> DjTransitionPlanPayload:
        """Create a simple plan to repeat the current track."""
        if not self._current_track:
            raise ValueError("Cannot create repeat plan - no current track")
            
        # Simple crossfade back to beginning of same track
        crossfade_step = MusicCrossfadeStep(
            step_type="music_crossfade",
            next_track_id=self._current_track.track_id,
            crossfade_duration=2.0  # Shorter crossfade for repeat
        )
        
        plan_id = str(uuid.uuid4())
        return DjTransitionPlanPayload(
            plan_id=plan_id,
            steps=[crossfade_step]
        )

    async def _create_fallback_transition_plan(self) -> None:
        """Create a basic transition plan without commentary."""
        if not self._next_track:
            self.logger.error("Cannot create fallback transition - no next track")
            return
            
        crossfade_step = MusicCrossfadeStep(
            step_type="music_crossfade",
            next_track_id=self._next_track.track_id,
            crossfade_duration=self._config.crossfade_duration
        )
        
        plan_id = str(uuid.uuid4())
        transition_plan = DjTransitionPlanPayload(
            plan_id=plan_id,
            steps=[crossfade_step]
        )
        
        await self._emit_validated_plan(transition_plan)
        
        # Update state
        self._current_track = self._next_track
        self._next_track = None
        self._next_track_commentary_cached = False

    # Helper method to trigger next track selection and commentary caching
    # This is called from _music_library_updated and potentially needed elsewhere
    async def _select_and_cache_next_track_commentary(self):
         """Helper to select the next track and trigger commentary caching."""
         if self._dj_mode_active and not self._next_track:
              self.logger.info("Selecting and caching commentary for the next track.")
              # Trigger the logic that normally runs in the caching loop for selecting the next track
              # and requesting commentary. This avoids duplicating the selection/request logic.
              # The caching loop checks `not self._next_track_commentary_cached` and `self._current_track`.
              # If _next_track is None, the loop needs to select it FIRST, then request commentary.
              # We need to signal the loop or duplicate the initial steps of the loop here.

              # Let's duplicate the initial steps of the caching loop for immediate selection and request.
              # This is not ideal and suggests the caching loop logic might need restructuring
              # to be easily triggered on demand or by specific state changes.

              # For now, duplicate:
              next_track_name = await self._smart_track_selection()
              if not next_track_name:
                   self.logger.warning("Could not select next track for immediate caching.")
                   return

              self._next_track = self._music_library.get(next_track_name)
              if not self._next_track:
                   self.logger.error(f"Metadata not found for immediately selected next track: {next_track_name}")
                   return

              self.logger.info(f"Immediately selected next track: {self._next_track.title}. Requesting commentary.")

              request_id = str(uuid.uuid4())
              # Store the linkage between the request ID and the selected next track
              self._commentary_request_next_track[request_id] = self._next_track

              commentary_request_payload = DjCommentaryRequestPayload(
                   timestamp=time.time(),
                   context="transition",
                   current_track=TrackDataPayload(**self._current_track.model_dump()) if self._current_track else None,
                   next_track=TrackDataPayload(**self._next_track.model_dump()),
                   persona=self._dj_persona,
                   request_id=request_id
              )

              self.logger.info(f"Emitting DJ_COMMENTARY_REQUEST for immediately selected next track with request_id: {request_id}")
              await self.emit(
                   EventTopics.DJ_COMMENTARY_REQUEST,
                   commentary_request_payload.model_dump()
              )
              # Cache readiness will be set by _handle_speech_cache_ready
              # Store linkage: _commentary_cache_keys will be updated in _handle_gpt_commentary_response

         elif self._dj_mode_active and self._next_track_commentary_cached:
              self.logger.info("Commentary for next track is already cached.")
         elif self._dj_mode_active and self._next_track and not self._next_track_commentary_cached:
              self.logger.info("Commentary for next track is being cached or generation is pending.")

    # Add compound command methods using decorators
    @compound_command("dj start")
    @command_error_handler
    async def handle_dj_start(self, payload: dict) -> None:
        """Handle the 'dj start' command to activate DJ mode."""
        self.logger.info("DJ command: start")
        if not self._dj_mode_active:
            # First check if we have tracks available
            if not self._music_library:
                self.logger.warning("No tracks available in music library")
                await self._send_error("No music tracks available. Please ensure music files are loaded.")
                return

            # Emit DJ mode activation event with correct payload format
            await self.emit(EventTopics.DJ_MODE_CHANGED, DJModeChangedPayload(is_active=True).model_dump())
            await self._send_success("DJ mode activated...")
        else:
            await self._send_success("DJ mode is already active")

    @compound_command("dj stop")
    @command_error_handler
    async def handle_dj_stop(self, payload: dict) -> None:
        """Handle the 'dj stop' command to deactivate DJ mode."""
        self.logger.info("DJ command: stop")
        if self._dj_mode_active:
            # Stop any playing music
            await self.emit(EventTopics.MUSIC_COMMAND, {
                "action": "stop"
            })
            
            # Emit DJ mode deactivation event
            await self.emit(EventTopics.DJ_MODE_CHANGED, DJModeChangedPayload(is_active=False).model_dump())
            
            self._dj_mode_active = False
            self._current_track = None
            self._next_track = None
            self._next_track_commentary_cached = False
            await self._send_success("DJ mode deactivated")
        else:
            await self._send_success("DJ mode is not active")

    @compound_command("dj next")
    @command_error_handler
    async def handle_dj_next(self, payload: dict) -> None:
        """Handle the 'dj next' command to skip to next track."""
        self.logger.info("DJ command: next")
        if not self._dj_mode_active:
            await self._send_error("Cannot skip track, DJ mode is not active")
            return
            
        # Check if next track and commentary are ready
        if self._next_track and self._next_track_commentary_cached:
            self.logger.info(f"Manual skip with cached commentary for '{self._next_track.title}'")
            # Create immediate transition plan with cached commentary
            await self._create_and_emit_transition_plan()
        else:
            # Fallback: Create transition plan without commentary (direct crossfade)
            self.logger.warning("Manual skip without cached commentary - using direct crossfade")
            
            # Select next track if not already selected
            if not self._next_track:
                next_track_name = await self._smart_track_selection()
                if next_track_name:
                    self._next_track = self._music_library.get(next_track_name)
            
            if self._next_track:
                # Create simple crossfade plan without commentary
                fallback_crossfade_step = MusicCrossfadeStep(
                    step_type="music_crossfade",
                    next_track_id=self._next_track.track_id,
                    crossfade_duration=self._config.crossfade_duration
                )
                
                fallback_plan = DjTransitionPlanPayload(
                    plan_id=str(uuid.uuid4()),
                    steps=[fallback_crossfade_step]
                )
                
                fallback_plan_ready = PlanReadyPayload(
                    timestamp=time.time(),
                    plan_id=fallback_plan.plan_id,
                    plan={
                        "plan_id": fallback_plan.plan_id,
                        "steps": [fallback_crossfade_step.model_dump()]
                    }
                )
                
                await self.emit(
                    EventTopics.PLAN_READY,
                    fallback_plan_ready.model_dump()
                )
                
                # Update state for next cycle
                self._current_track = self._next_track
                self._next_track = None
                self._next_track_commentary_cached = False
                
                await self._send_success("Skipping to next track...")
            else:
                await self._send_error("Cannot skip - no next track available")
                
    async def _create_and_emit_transition_plan(self):
        """Create and emit transition plan with cached commentary and comprehensive error recovery."""
        if not self._next_track:
            self.logger.error("Cannot create transition plan - no next track selected")
            await self._handle_transition_failure()
            return
            
        plan_steps = []
        commentary_cache_key = None
        commentary_info = None
        
        try:
            # Find ready commentary cache key using MemoryService first
            commentary_info = await self._get_ready_commentary_for_track(self._next_track.track_id)
            commentary_cache_key = commentary_info.get("cache_key") if commentary_info else None
            
            # Fallback to legacy method during transition period
            if not commentary_cache_key:
                self.logger.debug("MemoryService lookup failed, trying legacy method")
                for cache_key, is_ready in self._cached_commentary_ready.items():
                    if is_ready:
                        # Check if this cache key is for our next track
                        for request_id, track in self._commentary_request_next_track.items():
                            if (track and track.track_id == self._next_track.track_id and 
                                self._commentary_cache_keys.get(request_id) == cache_key):
                                commentary_cache_key = cache_key
                                commentary_info = {
                                    "request_id": request_id,
                                    "cache_key": cache_key,
                                    "next_track": track
                                }
                                break
                        if commentary_cache_key:
                            break
            
            # Create transition plan based on available commentary
            if commentary_cache_key:
                self.logger.info(f"Creating transition plan with commentary (cache_key: {commentary_cache_key})")
                plan_steps = await self._create_commentary_transition_steps(commentary_cache_key)
            else:
                self.logger.warning("No ready commentary found - creating crossfade-only plan")
                plan_steps = await self._create_simple_crossfade_steps()
            
            # Validate plan steps
            if not plan_steps or len(plan_steps) == 0:
                self.logger.error("Generated plan has no steps - cannot proceed")
                await self._create_fallback_transition_plan()
                return
            
            # Create and validate the complete plan
            plan_id = str(uuid.uuid4())
            transition_plan = DjTransitionPlanPayload(
                plan_id=plan_id,
                steps=plan_steps
            )
            
            # Validate plan structure
            try:
                # Test serialization to catch any validation issues
                plan_dict = transition_plan.model_dump()
                if not plan_dict.get("steps"):
                    raise ValueError("Plan serialization resulted in empty steps")
                    
                self.logger.info(f"Plan validation successful with {len(plan_steps)} steps")
                
            except Exception as validation_error:
                self.logger.error(f"Plan validation failed: {validation_error}")
                await self._create_fallback_transition_plan()
                return
            
            # Emit the validated plan
            await self._emit_validated_plan(transition_plan)
            
            # Clean up used cache data using MemoryService
            await self._cleanup_used_commentary_cache(commentary_info)
            
        except Exception as e:
            self.logger.error(f"Error creating transition plan: {e}", exc_info=True)
            await self._create_fallback_transition_plan()

    async def _create_commentary_transition_steps(self, commentary_cache_key: str) -> List:
        """Create transition steps with commentary, ducking, and crossfade."""
        try:
            duck_step = self._create_dj_plan_step("music_duck", {
                "duck_level": 0.5,  # Lower music to 50% for commentary - consistent with other ducking
                "fade_duration_ms": 2000,  # Longer, professional ducking speed
            })
            
            play_speech_step = self._create_dj_plan_step("play_cached_speech", {
                "cache_key": commentary_cache_key,
            })
            
            crossfade_step = self._create_dj_plan_step("music_crossfade", {
                "next_track_id": self._next_track.track_id,
                "crossfade_duration": self._config.crossfade_duration,
            })
            
            # Create parallel step for concurrent speech and crossfade
            parallel_step = self._create_dj_plan_step("parallel_steps", {
                "steps": [play_speech_step, crossfade_step],
            })
            
            unduck_step = self._create_dj_plan_step("music_unduck", {
                "fade_duration_ms": 2000,  # Longer, professional unducking speed
            })

            steps = [duck_step, parallel_step, unduck_step]
            self.logger.debug(f"Created {len(steps)} commentary transition steps")
            return steps
            
        except Exception as e:
            self.logger.error(f"Error creating commentary transition steps: {e}", exc_info=True)
            # Fallback to simple crossfade
            return await self._create_simple_crossfade_steps()

    def _create_dj_plan_step(self, step_type: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Create a DJ plan step with consistent format and validation.
        
        This ensures all DJ mode steps use the correct BasePlanStep format
        with step_type field and proper serialization.
        
        Args:
            step_type: The type of step (e.g., "play_cached_speech", "music_duck")
            params: Additional parameters for the step
            
        Returns:
            Dictionary representation of the step ready for timeline execution
        """
        step = {
            "step_type": step_type,
            "duration": None,  # Default duration
            **params  # Merge in the specific parameters
        }
        
        # Validate required fields based on step type
        if step_type == "play_cached_speech" and "cache_key" not in params:
            raise ValueError(f"cache_key is required for {step_type} step")
        elif step_type == "music_crossfade" and "next_track_id" not in params:
            raise ValueError(f"next_track_id is required for {step_type} step")
        elif step_type == "parallel_steps" and "steps" not in params:
            raise ValueError(f"steps is required for {step_type} step")
            
        self.logger.debug(f"Created DJ plan step: {step_type} with params: {list(params.keys())}")
        return step

    async def _create_simple_crossfade_steps(self) -> List:
        """Create simple crossfade steps without commentary."""
        try:
            crossfade_step = self._create_dj_plan_step("music_crossfade", {
                "next_track_id": self._next_track.track_id,
                "crossfade_duration": self._config.crossfade_duration,
            })
            
            steps = [crossfade_step]
            self.logger.debug("Created simple crossfade step")
            return steps
            
        except Exception as e:
            self.logger.error(f"Error creating simple crossfade steps: {e}", exc_info=True)
            return []

    async def _cleanup_used_commentary_cache(self, commentary_info: Optional[Dict[str, Any]]) -> None:
        """Clean up used commentary cache data from both MemoryService and legacy storage."""
        if not commentary_info:
            return
            
        try:
            cache_key = commentary_info.get("cache_key")
            request_id = commentary_info.get("request_id")
            
            if request_id:
                # Clean up via MemoryService
                try:
                    # Use the MemoryService cleanup method
                    memory_service = None
                    for service_name, service in self._event_bus._services.items() if hasattr(self._event_bus, '_services') else []:
                        if service_name == "memory_service":
                            memory_service = service
                            break
                    
                    if memory_service:
                        await memory_service.cleanup_commentary_cache_mapping(request_id)
                        self.logger.debug(f"Cleaned up commentary cache via MemoryService for request_id: {request_id}")
                    
                except Exception as cleanup_error:
                    self.logger.warning(f"MemoryService cleanup failed: {cleanup_error}")
                
                # Also clean up legacy dicts during transition period
                if request_id in self._commentary_cache_keys:
                    del self._commentary_cache_keys[request_id]
                if request_id in self._commentary_request_next_track:
                    del self._commentary_request_next_track[request_id]
                    
            if cache_key:
                self._cached_commentary_ready.pop(cache_key, None)
                
            self.logger.debug(f"Cache cleanup completed for commentary")
            
        except Exception as e:
            self.logger.warning(f"Error during cache cleanup: {e}", exc_info=True)

    @compound_command("dj queue")
    @validate_compound_command(min_args=1, required_args=["track_name"])
    @command_error_handler
    async def handle_dj_queue(self, payload: dict) -> None:
        """Handle the 'dj queue' command to queue a specific track."""
        self.logger.info("DJ command: queue")
        track_name = " ".join(payload["args"])
        # TODO: Implement queuing logic
        self.logger.warning(f"Queue command received for '{track_name}', but queuing is not yet implemented.")
        await self._send_error(f"Queuing not yet implemented for track: {track_name}")
        
    async def _send_success(self, message: str) -> None:
        """Send a success response to CLI."""
        await self.emit(
            EventTopics.CLI_RESPONSE,
            {
                "message": message,
                "is_error": False,
                "service": "brain_service"
            }
        )
        
    async def _send_error(self, message: str) -> None:
        """Send an error response to CLI."""
        await self.emit(
            EventTopics.CLI_RESPONSE,
            {
                "message": f"Error: {message}",
                "is_error": True,
                "service": "brain_service"
            }
        )

    # =================== MEMORY SERVICE INTEGRATION ===================
    # Phase 2: Replace internal cache tracking with MemoryService coordination
    
    async def _store_commentary_cache_mapping(self, request_id: str, cache_key: str, next_track: Optional[MusicTrack] = None) -> None:
        """Store commentary cache mapping in MemoryService."""
        track_data = None
        if next_track:
            track_data = {
                "track_id": next_track.track_id,
                "title": next_track.title,
                "artist": next_track.artist,
                "album": next_track.album,
                "genre": next_track.genre
            }
        
        # Store using event-based memory access
        await self.emit(EventTopics.MEMORY_SET, {
            "key": f"commentary_cache_mapping_{request_id}",
            "value": {
                "cache_key": cache_key,
                "next_track": track_data,
                "timestamp": time.time()
            }
        })
        self.logger.debug(f"Stored commentary cache mapping via MemoryService: {request_id} -> {cache_key}")

    async def _get_commentary_cache_key(self, request_id: str) -> Optional[str]:
        """Get commentary cache key from MemoryService."""
        # For simplicity, we'll use the current state direct access
        # In a more event-driven approach, this would be async with callbacks
        memory_service = None
        for service_name, service in self._event_bus._services.items() if hasattr(self._event_bus, '_services') else []:
            if service_name == "memory_service":
                memory_service = service
                break
        
        if memory_service:
            mapping = memory_service.get(f"commentary_cache_mapping_{request_id}")
            return mapping.get("cache_key") if mapping else None
        
        # Fallback to internal dict during transition
        return self._commentary_cache_keys.get(request_id)

    async def _set_commentary_cache_ready(self, cache_key: str, is_ready: bool = True, duration: Optional[float] = None) -> None:
        """Mark commentary cache as ready in MemoryService."""
        await self.emit(EventTopics.MEMORY_SET, {
            "key": f"commentary_cache_ready_{cache_key}",
            "value": {
                "ready": is_ready,
                "duration": duration,
                "timestamp": time.time()
            }
        })
        self.logger.debug(f"Set commentary cache ready via MemoryService: {cache_key} = {is_ready}")

    async def _is_commentary_cache_ready(self, cache_key: str) -> bool:
        """Check if commentary cache is ready via MemoryService."""
        # Get from MemoryService if available
        memory_service = None
        for service_name, service in self._event_bus._services.items() if hasattr(self._event_bus, '_services') else []:
            if service_name == "memory_service":
                memory_service = service
                break
        
        if memory_service:
            cache_state = memory_service.get(f"commentary_cache_ready_{cache_key}")
            return cache_state.get("ready", False) if cache_state else False
        
        # Fallback to internal dict during transition
        return self._cached_commentary_ready.get(cache_key, False)

    async def _get_ready_commentary_for_track(self, track_id: str) -> Optional[Dict[str, Any]]:
        """Get ready commentary cache info for a specific track via MemoryService."""
        # Access MemoryService if available
        memory_service = None
        for service_name, service in self._event_bus._services.items() if hasattr(self._event_bus, '_services') else []:
            if service_name == "memory_service":
                memory_service = service
                break
        
        if memory_service:
            return memory_service.get_ready_commentary_for_track(track_id)
        
        # Fallback to internal logic during transition
        for request_id, next_track in self._commentary_request_next_track.items():
            if next_track and next_track.track_id == track_id:
                cache_key = self._commentary_cache_keys.get(request_id)
                if cache_key and self._cached_commentary_ready.get(cache_key, False):
                    return {
                        "request_id": request_id,
                        "cache_key": cache_key,
                        "next_track": next_track
                    }
        return None

    async def _emit_validated_plan(self, plan: DjTransitionPlanPayload) -> None:
        """Emit a validated plan with proper state management and error handling."""
        try:
            # Validate plan structure before emission
            if not plan.steps or len(plan.steps) == 0:
                self.logger.error("Cannot emit plan with no steps")
                await self._handle_transition_failure()
                return
            
            # Test serialization to catch validation issues
            try:
                plan_dict = plan.model_dump()
                if not plan_dict.get("steps"):
                    raise ValueError("Plan serialization resulted in empty steps")
            except Exception as validation_error:
                self.logger.error(f"Plan validation failed during emission: {validation_error}")
                await self._handle_transition_failure()
                return
            
            # Serialize steps - handle both dict and Pydantic model formats
            serialized_steps = []
            for step in plan.steps:
                if isinstance(step, dict):
                    # Already serialized (from helper method)
                    serialized_steps.append(step)
                else:
                    # Pydantic model - needs serialization
                    serialized_steps.append(step.model_dump())
            
            # Create PlanReady payload with properly serialized steps
            plan_ready_payload = PlanReadyPayload(
                timestamp=time.time(),
                plan_id=plan.plan_id,
                plan={
                    "plan_id": plan.plan_id,
                    "steps": serialized_steps
                }
            )
            
            self.logger.info(f"Emitting validated plan with ID: {plan.plan_id}")
            await self.emit(EventTopics.PLAN_READY, plan_ready_payload.model_dump())
            
            # Update state after successful emission
            if self._next_track:
                self._current_track = self._next_track
                self._next_track = None
                self._next_track_commentary_cached = False
                self.logger.debug("Updated track state after plan emission")
             
        except Exception as e:
            self.logger.error(f"Error emitting validated plan: {e}", exc_info=True)
            await self._handle_transition_failure()

    async def _handle_plan_ended(self, payload: Dict[str, Any]) -> None:
        """Handle timeline plan completion and failure events for recovery strategies."""
        try:
            plan_id = payload.get("plan_id")
            status = payload.get("status", "unknown")
            layer = payload.get("layer", "unknown")
            
            self.logger.info(f"Plan {plan_id} ended with status: {status} on layer: {layer}")
            
            # Only handle failures and implement recovery for DJ transition plans
            if status == "failed":
                self.logger.error(f"Plan execution failed for plan_id: {plan_id}")
                
                # Implement recovery strategy for failed DJ transitions
                self.logger.warning("Implementing emergency recovery for failed DJ transition")
                
                # Try emergency track selection if we don't have a next track ready
                if not self._next_track:
                    await self._handle_emergency_track_selection()
                
                # If we still don't have a track after emergency selection, try basic crossfade
                if not self._next_track:
                    self.logger.error("No next track available even after emergency selection")
                    await self._handle_transition_failure()
                    return
                
                # Try a simplified transition without commentary
                try:
                    self.logger.info("Attempting simplified crossfade without commentary")
                    await self.emit(EventTopics.MUSIC_COMMAND, {
                        "action": "crossfade",
                        "song_query": self._next_track.title,
                        "fade_duration": 3.0,
                        "conversation_id": None
                    })
                    
                    # Update current track if crossfade succeeds
                    self._current_track = self._next_track
                    self._next_track = None
                    
                except Exception as recovery_error:
                    self.logger.error(f"Recovery crossfade also failed: {recovery_error}")
                    await self._handle_transition_failure()
            
            elif status == "completed":
                self.logger.debug(f"Plan {plan_id} completed successfully")
                # No action needed for successful completions
            
            elif status == "cancelled":
                self.logger.debug(f"Plan {plan_id} was cancelled")
                # No action needed for cancellations
                
        except Exception as e:
            self.logger.critical(f"Error handling plan ended event: {e}", exc_info=True)
            # Last resort - try to stop music gracefully
            try:
                await self.emit(EventTopics.MUSIC_COMMAND, {
                    "action": "stop",
                    "conversation_id": None
                })
            except:
                pass  # Even this failed, but we've done our best

    async def _create_initial_commentary_timeline_plan(self, commentary_text: str, request_id: str) -> None:
        """Create timeline plan for initial commentary with proper ducking coordination.
        
        This uses BasePlanStep format (with step_type field) to be compatible with 
        TimelineExecutorService validation, ensuring initial commentary uses the 
        same ducking coordination as transitions.
        """
        try:
            self.logger.info(f"Creating timeline plan for initial commentary (request_id: {request_id})")
            
            # Create speak step using BasePlanStep format (step_type field, not type)
            speak_step = {
                "step_type": "speak",
                "text": commentary_text,
                "duration": None
            }
            
            # Create timeline plan ID
            plan_id = str(uuid.uuid4())
            
            # Create PlanReadyPayload with proper structure (same as _emit_validated_plan)
            plan_ready_payload = PlanReadyPayload(
                timestamp=time.time(),
                plan_id=plan_id,
                plan={
                    "plan_id": plan_id,
                    "steps": [speak_step]  # Already a dict, no need for model_dump()
                }
            )
            
            self.logger.info(f"Emitting PLAN_READY for initial commentary plan {plan_id}")
            await self.emit(EventTopics.PLAN_READY, plan_ready_payload.model_dump())
            
        except Exception as e:
            self.logger.error(f"Error creating initial commentary timeline plan: {e}", exc_info=True)



