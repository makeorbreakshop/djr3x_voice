"""
BrainService for DJ R3X

This service acts as the "brain" for DJ R3X, handling high-level logic,
intent processing, command routing, and orchestrating DJ mode transitions,
including commentary generation and caching.
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
    MusicUnduckStep # Import MusicUnduckStep for restoring music volume
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

                # Add to recently played
                self._recently_played_tracks.append(track_name)
                if len(self._recently_played_tracks) > self._config.max_recent_tracks:
                    self._recently_played_tracks.pop(0)

                # Emit DJ mode start event with initial track
                await self.emit(
                    EventTopics.DJ_MODE_START,
                    DJModeChangedPayload(
                        is_active=True,
                        # Note: current_track and next_track info will be managed internally
                        # and potentially included in other specific events or state updates
                    ).model_dump()
                )

                # REQUEST INITIAL COMMENTARY Timeline Plan for the just-started track (timeline-based approach)
                # This provides an immediate DJ introduction when mode starts through timeline execution
                self.logger.info(f"Creating initial commentary timeline plan for track: {self._current_track.title}")
                
                # Generate cache key for initial commentary 
                initial_cache_key = f"commentary_{uuid.uuid4().hex[:8]}"
                
                # Request speech caching for initial commentary first
                initial_request_id = str(uuid.uuid4())
                initial_commentary_request = DjCommentaryRequestPayload(
                    timestamp=time.time(),
                    context="intro",  # Use "intro" context for initial DJ commentary
                    current_track=None,  # No previous track for intro
                    next_track=TrackDataPayload(**self._current_track.model_dump()),  # Introduce the track that just started
                    persona=self._dj_persona,
                    request_id=initial_request_id
                )
                
                # Add to our tracking
                self._commentary_cache_keys[initial_request_id] = initial_cache_key
                self._commentary_request_next_track[initial_request_id] = None  # No next track for intro
                
                # Request commentary generation first (will get cached)
                await self.emit(
                    EventTopics.DJ_COMMENTARY_REQUEST,
                    initial_commentary_request.model_dump()
                )
                
                # Cache the generated commentary using SpeechCacheRequestPayload
                # Note: We'll get the commentary text from GPT response and then cache it
                # For now, create a basic cache request that will be updated when GPT responds
                self.logger.info(f"Initial commentary requested with ID: {initial_request_id}, cache_key: {initial_cache_key}")
                
                # The commentary will be processed in _handle_gpt_commentary_response
                # and then trigger a timeline plan for immediate playback

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

            self.logger.info(f"Received commentary response for request_id: {request_id}. Partial: {is_partial}")
            self.logger.debug(f"Commentary text: {commentary_text[:100]}...") # Log snippet

            if not commentary_text:
                self.logger.warning(f"Received empty commentary text for request_id: {request_id}")
                # TODO: Handle empty commentary - maybe mark cache as failed for this request?
                return

            # Generate a cache key based on the request ID or use existing mapping
            cache_key = self._commentary_cache_keys.get(request_id)
            if not cache_key:
                cache_key = f"commentary_{request_id}"
                self._commentary_cache_keys[request_id] = cache_key # Store mapping
            
            self._cached_commentary_ready[cache_key] = False

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
                        "context": getattr(response_payload, 'context', 'unknown'),  # Track context
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

            # Check if this is an initial commentary (intro context)
            # If so, create an immediate timeline plan for playback once cached
            next_track_for_request = self._commentary_request_next_track.get(request_id)
            if next_track_for_request is None:  # None means this is initial commentary
                self.logger.info(f"Initial commentary caching requested for request_id: {request_id}")
                # Mark that we have an initial commentary being cached
                # We'll create the timeline plan in _handle_speech_cache_ready when caching completes
                pass

            # Do NOT set _next_track_commentary_cached to True yet.
            # This flag should only be set when SPEECH_CACHE_READY is received for the corresponding cache key.

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

            # Mark the cache entry as ready
            if cache_key in self._cached_commentary_ready:
                 self._cached_commentary_ready[cache_key] = True
                 self.logger.info(f"Marked cache_key {cache_key} as ready.")

                 # Check if this ready cache entry corresponds to the currently planned next track commentary
                 commentary_request_id = metadata.get("commentary_request_id")
                 if commentary_request_id and commentary_request_id in self._commentary_request_next_track:
                     associated_next_track = self._commentary_request_next_track[commentary_request_id]
                     # Check if this cached commentary is for the CURRENTLY planned next track
                     if self._next_track and associated_next_track and associated_next_track.track_id == self._next_track.track_id:
                         self._next_track_commentary_cached = True # Mark as cached and ready
                         self.logger.info(f"Speech cache for next track '{self._next_track.title}' is ready (cache_key: {cache_key}).")
                     else:
                         self.logger.debug(f"Speech cache ready, but not for the currently planned next track. Expected: {self._next_track.track_id if self._next_track else 'None'}, Got: {associated_next_track.track_id if associated_next_track else 'None'}")
                     
                     # Check if this is initial commentary (associated_next_track would be None for intro)
                     if associated_next_track is None:  # This indicates initial commentary
                         self.logger.info(f"Initial commentary cache ready (cache_key: {cache_key}). Creating immediate timeline plan.")
                         
                         # Create timeline plan for immediate initial commentary playback with ducking
                         plan_id = str(uuid.uuid4())
                         
                         # Create steps with music ducking for professional intro
                         duck_step = MusicDuckStep(
                             step_type="music_duck",
                             duck_level=0.3,  # Lower music to 30% for intro commentary
                             fade_duration_ms=300
                         )
                         
                         initial_speech_step = PlayCachedSpeechStep(
                             step_type="play_cached_speech",
                             cache_key=cache_key
                         )
                         
                         unduck_step = MusicUnduckStep(
                             step_type="music_unduck",
                             fade_duration_ms=300
                         )
                         
                         # Create the DJ transition plan payload (duck + speech + unduck for intro)
                         initial_plan = DjTransitionPlanPayload(
                             plan_id=plan_id,
                             steps=[duck_step, initial_speech_step, unduck_step]
                         )
                         
                         # Create the PlanReady payload
                         plan_ready_payload = PlanReadyPayload(
                             timestamp=time.time(),
                             plan_id=plan_id,
                             plan=initial_plan.model_dump()
                         )
                         
                         self.logger.info(f"Emitting PLAN_READY for initial commentary with plan_id: {plan_id}")
                         # Emit the PLAN_READY event for immediate execution
                         await self.emit(
                             EventTopics.PLAN_READY,
                             plan_ready_payload.model_dump()
                         )
                 
                 # If no commentary_request_id found in the main logic above, this might be legacy or unmapped cache
                 elif commentary_request_id:
                     self.logger.warning(f"Speech cache ready for request_id {commentary_request_id} but no mapping found in _commentary_request_next_track")
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
        """Handle the TRACK_ENDING_SOON event and trigger the transition plan."""
        self.logger.info("Handling TRACK_ENDING_SOON")
        try:
            # Use Pydantic model for incoming payload
            ending_soon_payload = TrackEndingSoonPayload(**payload)
            current_track_data = ending_soon_payload.current_track
            time_remaining = ending_soon_payload.time_remaining

            self.logger.info(f"Track '{current_track_data.title}' ending soon. {time_remaining:.2f}s remaining.")

            # Only proceed if DJ mode is active
            if not self._dj_mode_active:
                self.logger.debug("DJ mode not active, ignoring TRACK_ENDING_SOON event.")
                return

            # Ensure current track state is updated based on event payload
            # (In case the internal state somehow got out of sync)
            if self._current_track is None or self._current_track.track_id != current_track_data.track_id:
                self.logger.warning("BrainService current track state out of sync with TRACK_ENDING_SOON event.")
                self._current_track = self._music_library.get(current_track_data.track_id)
                if not self._current_track:
                    self.logger.error(f"Current track from event not found in music library: {current_track_data.title}")
                    return # Cannot proceed without current track metadata

            # Check if we have a next track selected and commentary cached
            if self._next_track and self._next_track_commentary_cached:
                self.logger.info(f"Next track '{self._next_track.title}' selected and commentary cached. Preparing transition plan.")

                # Use the stored linkage to find the correct cache key for the current _next_track
                commentary_cache_key = None
                target_request_id = None
                # Find the request ID associated with the current _next_track
                for req_id, next_track in self._commentary_request_next_track.items():
                    if self._next_track and next_track and next_track.track_id == self._next_track.track_id:
                        target_request_id = req_id
                        break

                if target_request_id and target_request_id in self._commentary_cache_keys:
                    potential_cache_key = self._commentary_cache_keys[target_request_id]
                    if self._cached_commentary_ready.get(potential_cache_key, False):
                        commentary_cache_key = potential_cache_key
                        self.logger.debug(f"Found ready commentary cache key {commentary_cache_key} for next track {self._next_track.title}.")

                if not commentary_cache_key:
                    self.logger.error("TRACK_ENDING_SOON: _next_track_commentary_cached is True but no ready cache key found!")
                    # Fallback: proceed with a simpler transition without commentary
                    plan_steps: List[BasePlanStep] = []
                    self.logger.warning("Proceeding with transition plan without commentary due to missing cache key.")
                else:
                    # Create the plan steps with proper music ducking:
                    # 1. Duck music volume
                    # 2. Play cached speech (commentary)
                    # 3. Unduck music volume
                    # 4. Perform music crossfade
                    
                    duck_step = MusicDuckStep(
                        step_type="music_duck",
                        duck_level=0.3,  # Lower music to 30% volume during speech
                        fade_duration_ms=300
                    )
                    
                    play_speech_step = PlayCachedSpeechStep(
                        step_type="play_cached_speech",
                        cache_key=commentary_cache_key,
                        # TODO: Add duration from cache metadata if needed by TimelineExecutor
                        # duration = ready_payload.duration # Need access to duration
                    )
                    
                    unduck_step = MusicUnduckStep(
                        step_type="music_unduck",
                        fade_duration_ms=300
                    )

                    crossfade_step = MusicCrossfadeStep(
                        step_type="music_crossfade",
                        next_track_id=self._next_track.track_id,
                        crossfade_duration=self._config.crossfade_duration # Use configured duration
                    )

                    plan_steps = [duck_step, play_speech_step, unduck_step, crossfade_step]
                    self.logger.info(f"Created plan steps with music ducking: {plan_steps}")

                # Generate a unique plan ID
                plan_id = str(uuid.uuid4())

                # Create the DJ transition plan payload
                transition_plan = DjTransitionPlanPayload(
                    plan_id=plan_id,
                    steps=plan_steps
                )

                # Create the PlanReady payload
                plan_ready_payload = PlanReadyPayload(
                    timestamp=time.time(),
                    plan_id=plan_id,
                    plan=transition_plan.model_dump() # Emit the plan as a dictionary for now
                    # TODO: Update TimelineExecutorService to accept DjTransitionPlanPayload directly
                )

                self.logger.info(f"Emitting PLAN_READY with plan_id: {plan_id}")
                # Emit the PLAN_READY event
                await self.emit(
                    EventTopics.PLAN_READY,
                    plan_ready_payload.model_dump()
                )

                # Reset state after planning the transition
                self._next_track = None # Next track is now the current track after transition
                self._next_track_commentary_cached = False # Need to cache for the *next* next track
                # Keep _commentary_cache_keys and _cached_commentary_ready until cleanup logic is added

            elif self._dj_mode_active and self._next_track and not self._next_track_commentary_cached:
                self.logger.warning(f"Track '{current_track_data.title}' ending soon, but next track commentary for '{self._next_track.title}' is not cached.")
                # TODO: Implement fallback: Trigger on-demand generation/caching and create a plan
                # This might involve a simpler transition initially, then potentially adding commentary if caching finishes in time.
                # For now, log the warning and potentially just trigger a crossfade plan without commentary.
                # Let's emit a basic crossfade plan as a fallback.

                self.logger.warning("Emitting basic crossfade plan without commentary as fallback.")
                if self._next_track:
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
                        plan=fallback_plan.model_dump()
                    )
                    await self.emit(
                        EventTopics.PLAN_READY,
                        fallback_plan_ready.model_dump()
                    )
                    # Reset state similar to successful plan
                    self._next_track = None
                    self._next_track_commentary_cached = False
                    # Keep cache keys/readiness for potential future cleanup
                else:
                    self.logger.error("TRACK_ENDING_SOON: _next_track is not set when commentary is not cached. This shouldn't happen.")

            elif self._dj_mode_active and not self._next_track:
                self.logger.warning(f"Track '{current_track_data.title}' ending soon, but next track is not selected.")
                # This indicates an issue in the caching loop or initial selection.
                # Trigger track selection immediately.
                self.logger.info("Attempting to select next track immediately.")
                # Running this directly might block, ideally trigger the caching loop logic.
                # await self._select_and_cache_next_track_commentary() # Needs refactoring
                # For now, just log and the caching loop should pick it up eventually.
                pass # The caching loop should handle selecting the next track if _next_track is None

        except Exception as e:
            self.logger.error(f"Error handling TRACK_ENDING_SOON: {e}", exc_info=True)

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
        if self._dj_mode_active:
            await self.emit(EventTopics.DJ_NEXT_TRACK, {})
            await self._send_success("Skipping to next track")
        else:
            await self._send_error("Cannot skip track, DJ mode is not active")

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



