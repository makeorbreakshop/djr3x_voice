"""
Brain Service for Cantina OS
================================
A GPT-backed planner service that turns intents and memory into declarative plans
for the timeline executor. Handles the Three-Call GPT pattern for DJ R3X.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, List, Optional, Set
import time

from pydantic import BaseModel, ValidationError, Field

from cantina_os.bus import EventTopics, LogLevel, ServiceStatus
from cantina_os.base_service import BaseService
from cantina_os.event_payloads import (
    IntentPayload, 
    PlanPayload,
    PlanStep,
    MusicCommandPayload,
    LLMResponsePayload,
    IntentExecutionResultPayload,
    SpeechCacheRequestPayload
)
from cantina_os.models.music_models import MusicTrack, MusicLibrary

# ---------------------------------------------------------------------------
# Configuration model
# ---------------------------------------------------------------------------
class _Config(BaseModel):
    """Pydantic‑validated configuration for the brain service."""
    gpt_model_intro: str = Field(default="gpt-3.5-turbo", description="GPT model for track intros")
    gpt_temperature_intro: float = Field(default=0.7, description="Temperature setting for track intros")
    chat_history_max_turns: int = Field(default=10, description="Maximum chat history turns to maintain")
    handled_intents: List[str] = Field(default=["play_music"], description="List of intents handled by brain service")
    dj_mode_transition_lead_time: int = Field(default=15, description="Seconds before track end to prepare transition")
    dj_commentary_styles: List[str] = Field(
        default=[
            "energetic", "chill", "funny", "informative", 
            "mysterious", "dramatic", "galactic"
        ],
        description="Available DJ commentary styles"
    )
    dj_transition_cache_enabled: bool = Field(default=True, description="Whether to cache DJ transitions")
    dj_cache_ttl_seconds: int = Field(default=300, description="How long to keep DJ transitions in cache")
    dj_skip_transition_duration: float = Field(default=5.0, description="Duration for skip transitions in seconds")


# ---------------------------------------------------------------------------
# Service Implementation
# ---------------------------------------------------------------------------
class BrainService(BaseService):
    """Brain service for DJ R3X.
    
    Handles intent processing, music commands, track intro generation, and DJ mode.
    """

    def __init__(self, event_bus, config=None, name="brain_service"):
        super().__init__(service_name=name, event_bus=event_bus)
        
        # ----- validated configuration -----
        self._config = _Config(**(config or {}))
        
        # ----- bookkeeping -----
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tasks: List[asyncio.Task] = []
        
        # ----- brain state -----
        self._current_intent: Optional[IntentPayload] = None
        self._last_track_meta: Optional[Dict[str, Any]] = None
        self._handled_intents: Set[str] = set(self._config.handled_intents)
        
        # ----- music library management -----
        self._music_library = MusicLibrary()
        
        # Track recently played songs to avoid repetition
        self._recently_played_tracks = []
        # Maximum number of tracks to remember for avoiding repetition
        self._max_history = 5  # Increased for better DJ rotation
        
        # ----- DJ mode state -----
        self._dj_mode_active = False
        self._dj_next_track: Optional[str] = None
        self._dj_transition_plan_ready = False
        self._dj_transition_style = "energetic"  # Default style
        self._dj_track_history = []  # For more advanced rotation tracking
        self._dj_current_rotation_index = 0
        
        # Cache for recent DJ transitions
        self._dj_transition_cache = {}
        
        # Genre groupings for smarter transitions
        self._genre_groups = {
            "upbeat": ["Elem Zadowz - Huttuk Cheeka", "Batuu Boogie", "Mus Kat & Nalpak - Turbulence", 
                       "Vee Gooda, Ryco - Aloogahoo", "Mus Kat & Nalpak - Bright Suns"],
            "electronic": ["Droid World", "Duro Droids - Beep Boop Bop", "Mus Kat & Nalpak - Doshka",
                          "Mus Kat & Nalpak - Turbulence"],
            "chill": ["Modal Notes", "Moulee-rah", "Doolstan", "Opening"],
            "traditional": ["Cantina Band", "Mad About Me", "Cantina Song aka Mad About Mad About Me",
                           "Jabba Flow", "Jedi Rocks"],
            "alien": ["Yocola Ateema", "Gaya", "Gaya - Oola Shuka", "Bai Tee Tee", 
                      "Laki Lembeng - Nama Heh", "Zano - Goola Bukee"],
            "misc": ["Kra Mer 5 - Una Duey Dee", "The Dusty Jawas - Utinni", "Utinni"]
        }

    # ------------------------------------------------------------------
    # Helper methods
    # ------------------------------------------------------------------
    async def _emit_dict(self, topic: EventTopics, payload: Any) -> None:
        """Emit a Pydantic model or dict as a dictionary to the event bus.
        
        Args:
            topic: Event topic
            payload: Pydantic model or dict to emit
        """
        try:
            # Convert Pydantic model to dict using model_dump() method
            if hasattr(payload, "model_dump"):
                payload_dict = payload.model_dump()
            else:
                # Fallback for old pydantic versions or dict inputs
                payload_dict = payload if isinstance(payload, dict) else payload.dict()
                
            await self.emit(topic, payload_dict)
        except Exception as e:
            self.logger.error(f"Error emitting event on topic {topic}: {e}")
            await self._emit_status(
                ServiceStatus.ERROR,
                f"Error emitting event: {e}",
                LogLevel.ERROR
            )

    async def unsubscribe(self, topic: EventTopics, handler) -> None:
        """Unsubscribe a handler from an event topic.
        
        Args:
            topic: Event topic to unsubscribe from
            handler: Handler function to remove
        """
        try:
            # Get the event bus instance
            if hasattr(self, "_event_bus") and self._event_bus:
                # Remove the listener from the event bus
                self._event_bus.remove_listener(topic, handler)
                self.logger.debug(f"Unsubscribed handler from topic: {topic}")
            else:
                self.logger.warning(f"Cannot unsubscribe from {topic}: No event bus")
        except Exception as e:
            self.logger.error(f"Error unsubscribing from {topic}: {e}")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def _start(self) -> None:
        """Initialize brain service and set up subscriptions."""
        try:
            self.logger.debug("BrainService: Starting initialization")
            
            # Initialize state with defaults
            self._dj_mode_active = False
            self._dj_next_track = None
            self._dj_transition_plan_ready = False
            self._dj_transition_style = "energetic"
            self._dj_track_history = []
            self._recently_played_tracks = []
            
            self.logger.debug("BrainService: State initialized with defaults")
            self._loop = asyncio.get_running_loop()
            self.logger.debug("BrainService: Got running loop")
            
            # Set up subscriptions first
            await self._setup_subscriptions()
            self.logger.debug("BrainService: Subscriptions set up")
            
            # Try to fetch initial state from memory service, but don't fail if not found
            try:
                await self._emit_dict(
                    EventTopics.MEMORY_GET,
                    {
                        "key": "dj_mode_active",
                        "callback_topic": EventTopics.MEMORY_VALUE
                    }
                )
                await self._emit_dict(
                    EventTopics.MEMORY_GET,
                    {
                        "key": "dj_track_history",
                        "callback_topic": EventTopics.MEMORY_VALUE
                    }
                )
                self.logger.debug("BrainService: Requested initial state from memory")
                
                # Short wait for memory responses
                await asyncio.sleep(0.1)
            except Exception as e:
                self.logger.warning(f"Failed to fetch initial state from memory: {e}")
                self.logger.info("Continuing with default state")
            
            await self._emit_status(ServiceStatus.RUNNING, "Brain service started")
            self.logger.info("BrainService started successfully")
            
        except Exception as e:
            error_msg = f"Failed to start BrainService: {e}"
            self.logger.error(error_msg)
            await self._emit_status(ServiceStatus.ERROR, error_msg)
            raise

    async def _stop(self) -> None:
        """Clean up tasks and subscriptions."""
        for task in self._tasks:
            if not task.done():
                task.cancel()
                
        # Wait for tasks to complete with timeout
        if self._tasks:
            await asyncio.wait(self._tasks, timeout=5.0)
            
        self._tasks.clear()
        
        # Clean up any cached speech data
        if self._dj_mode_active:
            await self._emit_dict(EventTopics.SPEECH_CACHE_CLEANUP, {"service": "brain_service"})
        
        self.logger.info("Brain service stopped")
        await self._emit_status(ServiceStatus.STOPPED, "Brain service stopped")

    # ------------------------------------------------------------------
    # Subscription setup
    # ------------------------------------------------------------------
    async def _setup_subscriptions(self) -> None:
        """Register event-handlers for brain processing."""
        self.logger.debug("BrainService: Starting _setup_subscriptions method.")
        # Subscribe to essential events for the new direct flow # Removing subscriptions to non-existent topics
        # task = asyncio.create_task(self.subscribe(EventTopics.BRAIN_MUSIC_REQUEST, self._handle_music_request))
        # self._tasks.append(task)
        
        # task = asyncio.create_task(self.subscribe(EventTopics.BRAIN_MUSIC_STOP, self._handle_music_stop))
        # self._tasks.append(task)
        
        task = asyncio.create_task(self.subscribe(EventTopics.TRACK_PLAYING, self._handle_music_started))
        self._tasks.append(task)
        
        # Subscribe to LLM responses for monitoring
        task = asyncio.create_task(self.subscribe(EventTopics.LLM_RESPONSE, self._handle_llm_response))
        self._tasks.append(task)
        
        # Subscribe to regular intent events (though mostly using direct paths now)
        task = asyncio.create_task(self.subscribe(EventTopics.INTENT_DETECTED, self._handle_intent_detected))
        self._tasks.append(task)
        
        # Subscribe to memory updates
        task = asyncio.create_task(self.subscribe(EventTopics.MEMORY_UPDATED, self._handle_memory_updated))
        self._tasks.append(task)
        
        # Subscribe to memory value responses
        task = asyncio.create_task(self.subscribe(EventTopics.MEMORY_VALUE, self._handle_memory_value))
        self._tasks.append(task)
        
        # Subscribe to music library updates
        task = asyncio.create_task(self.subscribe(EventTopics.MUSIC_LIBRARY_UPDATED, self._handle_music_library_updated))
        self._tasks.append(task)
        
        # Subscribe to DJ mode topics
        task = asyncio.create_task(self.subscribe(EventTopics.DJ_MODE_CHANGED, self._handle_dj_mode_changed))
        self._tasks.append(task)
        
        task = asyncio.create_task(self.subscribe(EventTopics.TRACK_ENDING_SOON, self._handle_track_ending_soon))
        self._tasks.append(task)
        
        task = asyncio.create_task(self.subscribe(EventTopics.DJ_NEXT_TRACK, self._handle_dj_next_track))
        self._tasks.append(task)
        
        task = asyncio.create_task(self.subscribe(EventTopics.DJ_TRACK_QUEUED, self._handle_dj_track_queued))
        self._tasks.append(task)
        
        # Listen for cached speech events
        task = asyncio.create_task(self.subscribe(EventTopics.SPEECH_CACHE_READY, self._handle_speech_cache_ready))
        self._tasks.append(task)
        
        task = asyncio.create_task(self.subscribe(EventTopics.SPEECH_CACHE_ERROR, self._handle_speech_cache_error))
        self._tasks.append(task)
        
        # Fetch available tracks on start
        self.logger.debug("BrainService: Calling _fetch_available_tracks.")
        await self._fetch_available_tracks()
        self.logger.debug("BrainService: _fetch_available_tracks completed.")
        self.logger.debug("BrainService: _setup_subscriptions method completed.")

    async def _handle_music_library_updated(self, payload: Dict[str, Any]) -> None:
        """Handle music library updates from MusicControllerService."""
        try:
            if not isinstance(payload, dict):
                self.logger.error(f"Invalid music library update payload: {payload}")
                return
                
            if "tracks" in payload and isinstance(payload["tracks"], list):
                tracks_data = payload["tracks"]
                # Convert the list of tracks to a dictionary
                track_dict = {}
                for track_data in tracks_data:
                    track = MusicTrack.from_dict(track_data)
                    track_dict[track.name] = track
                
                # Update the music library with the dictionary
                self._music_library.tracks = track_dict
                self.logger.info(f"Updated music library from event: {len(self._music_library.tracks)} tracks")
        except Exception as e:
            self.logger.error(f"Error handling music library update: {e}")

    async def _fetch_available_tracks(self) -> None:
        """Fetch available tracks from MusicControllerService."""
        try:
            # Request track list via MUSIC_COMMAND event
            track_list_payload = {"command": "list", "subcommand": None, "args": [], "raw_input": "list music"}
            await self._emit_dict(EventTopics.MUSIC_COMMAND, track_list_payload)
            
            # We don't have a direct way to get the response, so we'll use the following approach:
            # Subscribe to CLI_RESPONSE and handle the track list if we get one
            # This is a one-time subscription to get the track list response
            async def track_list_handler(payload):
                if isinstance(payload, dict) and "message" in payload and "is_error" in payload:
                    if not payload["is_error"] and "Available tracks:" in payload["message"]:
                        # Parse track names from the response
                        tracks = {}
                        for line in payload["message"].splitlines():
                            if ". " in line:  # Lines like "1. Track Name"
                                track_name = line.split(". ", 1)[1].strip()
                                # Create a minimal track entry with just the name
                                tracks[track_name] = MusicTrack(name=track_name, path="")
                        
                        if tracks:
                            self._music_library.tracks = tracks
                            self.logger.info(f"Updated available tracks from CLI response, found {len(tracks)} tracks")
                        
                            # Unsubscribe after getting the response
                            await self.unsubscribe(EventTopics.CLI_RESPONSE, track_list_handler)
            
            # Subscribe temporarily to get the response
            await self.subscribe(EventTopics.CLI_RESPONSE, track_list_handler)
            
            # If we don't get a response within 2 seconds, keep current tracks
            await asyncio.sleep(2)

        except Exception as e:
            self.logger.error(f"Error fetching available tracks: {e}")
            # Use fallback tracks in case of error
            self._music_library.tracks = []

    # ------------------------------------------------------------------
    # Direct Music Request Handling (New Path)
    # ------------------------------------------------------------------
    async def _handle_music_request(self, payload: Dict[str, Any]) -> None:
        """Handle direct BRAIN_MUSIC_REQUEST events from GPT service.
        
        This is the new direct pathway that bypasses IntentRouterService.
        Creates a plan for the TimelineExecutor instead of direct commands.
        """
        try:
            track_query = payload.get("track_query", "")
            tool_call_id = payload.get("tool_call_id")
            conversation_id = payload.get("conversation_id")
            original_text = payload.get("original_text", "")
            
            if not track_query:
                self.logger.error("No track query in BRAIN_MUSIC_REQUEST")
                await self._emit_intent_execution_result(
                    "play_music",
                    {"track": ""},
                    {"success": False, "message": "No track specified"},
                    tool_call_id,
                    conversation_id
                )
                return
                
            self.logger.info(f"Handling direct music request for: {track_query}")
            
            # Perform track selection
            selected_track = await self._smart_track_selection(track_query)
            
            if not selected_track:
                self.logger.error(f"Could not find a track matching: {track_query}")
                await self._emit_intent_execution_result(
                    "play_music",
                    {"track": track_query},
                    {"success": False, "message": f"Could not find a track matching: {track_query}"},
                    tool_call_id,
                    conversation_id
                )
                return
                
            self.logger.info(f"Selected track '{selected_track}' for query '{track_query}'")
            
            # Create a plan for the timeline executor
            import uuid
            music_plan = PlanPayload(
                plan_id=str(uuid.uuid4()),
                layer="foreground",
                steps=[
                    PlanStep(
                        id="music",
                        type="play_music",
                        genre=selected_track
                    )
                ]
            )
            
            # Send to timeline executor
            await self._emit_dict(
                EventTopics.PLAN_READY,
                music_plan
            )
            
            # Emit intent execution result for verbal feedback
            await self._emit_intent_execution_result(
                "play_music",
                {"track": track_query},
                {
                    "success": True,
                    "track": selected_track,
                    "original_request": track_query,
                    "action": "play",
                    "message": f"Now playing: {selected_track}"
                },
                tool_call_id,
                conversation_id
            )
            
        except Exception as e:
            self.logger.error(f"Error handling direct music request: {e}")
            await self._emit_intent_execution_result(
                "play_music",
                {"track": track_query if 'track_query' in locals() else ""},
                {"success": False, "message": f"Error: {str(e)}"},
                tool_call_id if 'tool_call_id' in locals() else None,
                conversation_id if 'conversation_id' in locals() else None
            )

    async def _handle_music_stop(self, payload: Dict[str, Any]) -> None:
        """Handle direct BRAIN_MUSIC_STOP events from GPT service.
        
        Creates a plan for the TimelineExecutor to stop music with proper ducking.
        """
        try:
            tool_call_id = payload.get("tool_call_id")
            conversation_id = payload.get("conversation_id")
            
            self.logger.info("Handling direct music stop request")
            
            # Create a plan for the timeline executor to stop music
            import uuid
            stop_plan = PlanPayload(
                plan_id=str(uuid.uuid4()),
                layer="foreground",
                steps=[
                    PlanStep(
                        id="stop_music",
                        type="speak",
                        text="Stopping the music now."
                    ),
                    PlanStep(
                        id="stop_command",
                        type="play_music",
                        genre="stop"  # Special value to indicate stop
                    )
                ]
            )
            
            # Send to timeline executor
            await self._emit_dict(
                EventTopics.PLAN_READY,
                stop_plan
            )
            
            # Emit intent execution result for verbal feedback
            await self._emit_intent_execution_result(
                "stop_music",
                {},
                {
                    "success": True,
                    "action": "stop",
                    "message": "Music stopped"
                },
                tool_call_id,
                conversation_id
            )
            
        except Exception as e:
            self.logger.error(f"Error handling music stop request: {e}")
            await self._emit_intent_execution_result(
                "stop_music",
                {},
                {"success": False, "message": f"Error: {str(e)}"},
                tool_call_id if 'tool_call_id' in locals() else None,
                conversation_id if 'conversation_id' in locals() else None
            )

    async def _smart_track_selection(self, query: str) -> Optional[str]:
        """Select the best matching track from available tracks.
        
        Args:
            query: User's track request (e.g., "funky", "cantina")
            
        Returns:
            Best matching track name or None if no match found
        """
        try:
            self.logger.info(f"Smart track selection for query: '{query}'")
            
            # If we have no tracks, try fetching them
            if not self._music_library.tracks:
                await self._fetch_available_tracks()
                if not self._music_library.tracks:
                    self.logger.error("No tracks available, even after fetching")
                    return None
            
            # Get track names for easier processing
            available_tracks = self._music_library.get_track_names()
            
            # Normalize query
            query_lower = query.lower()
            
            # First, try direct track name match
            direct_matches = []
            for track_name in available_tracks:
                if query_lower in track_name.lower():
                    direct_matches.append(track_name)
            
            if direct_matches:
                # Choose a track that hasn't been played recently if possible
                for track_name in direct_matches:
                    if track_name not in self._recently_played_tracks:
                        self.logger.info(f"Found direct match (not recently played): '{track_name}' for query '{query}'")
                        self._add_to_recently_played(track_name)
                        return track_name
                
                # If all matches have been played recently, pick one randomly
                import random
                selected_track = random.choice(direct_matches)
                self.logger.info(f"Found direct match (choosing randomly): '{selected_track}' for query '{query}'")
                self._add_to_recently_played(selected_track)
                return selected_track
            
            # Try genre/keyword matching with enhanced mappings
            keyword_mapping = {
                # Cantina music keywords
                "cantina": ["Cantina Band", "Mad About Me", "Cantina Song aka Mad About Mad About Me"],
                "band": ["Cantina Band", "Cantina Song aka Mad About Mad About Me"],
                
                # Artist keywords
                "jabba": ["Jabba Flow"],
                "flow": ["Jabba Flow"],
                "jedi": ["Jedi Rocks"],
                "rock": ["Jedi Rocks", "Vee Gooda, Ryco - Aloogahoo"],
                "mad": ["Mad About Me", "Cantina Song aka Mad About Mad About Me"],
                "about": ["Mad About Me", "Cantina Song aka Mad About Mad About Me"],
                "elem": ["Elem Zadowz - Huttuk Cheeka"],
                "zadowz": ["Elem Zadowz - Huttuk Cheeka"],
                "huttuk": ["Elem Zadowz - Huttuk Cheeka"],
                "cheeka": ["Elem Zadowz - Huttuk Cheeka"],
                "gaya": ["Gaya", "Gaya - Oola Shuka"],
                "oola": ["Gaya - Oola Shuka"],
                "shuka": ["Gaya - Oola Shuka"],
                "kra": ["Kra Mer 5 - Una Duey Dee"],
                "mer": ["Kra Mer 5 - Una Duey Dee"],
                "una": ["Kra Mer 5 - Una Duey Dee"],
                "duey": ["Kra Mer 5 - Una Duey Dee"],
                "dee": ["Kra Mer 5 - Una Duey Dee"],
                "mus": ["Mus Kat & Nalpak - Turbulence", "Mus Kat & Nalpak - Bright Suns", "Mus Kat & Nalpak - Doshka"],
                "kat": ["Mus Kat & Nalpak - Turbulence", "Mus Kat & Nalpak - Bright Suns", "Mus Kat & Nalpak - Doshka"],
                "nalpak": ["Mus Kat & Nalpak - Turbulence", "Mus Kat & Nalpak - Bright Suns", "Mus Kat & Nalpak - Doshka"],
                "duro": ["Duro Droids - Beep Boop Bop"],
                "droids": ["Duro Droids - Beep Boop Bop"],
                "beep": ["Duro Droids - Beep Boop Bop"],
                "boop": ["Duro Droids - Beep Boop Bop"],
                "bop": ["Duro Droids - Beep Boop Bop"],
                "dusty": ["The Dusty Jawas - Utinni"],
                "jawas": ["The Dusty Jawas - Utinni"],
                "utinni": ["Utinni", "The Dusty Jawas - Utinni"],
                "vee": ["Vee Gooda, Ryco - Aloogahoo"],
                "gooda": ["Vee Gooda, Ryco - Aloogahoo"],
                "ryco": ["Vee Gooda, Ryco - Aloogahoo"],
                "aloogahoo": ["Vee Gooda, Ryco - Aloogahoo"],
                "laki": ["Laki Lembeng - Nama Heh"],
                "lembeng": ["Laki Lembeng - Nama Heh"],
                "nama": ["Laki Lembeng - Nama Heh"],
                "heh": ["Laki Lembeng - Nama Heh"],
                "zano": ["Zano - Goola Bukee"],
                "goola": ["Zano - Goola Bukee"],
                "bukee": ["Zano - Goola Bukee"],
                
                # Mood/style keywords
                "funky": ["Elem Zadowz - Huttuk Cheeka", "Batuu Boogie", "Mus Kat & Nalpak - Turbulence"],
                "upbeat": ["Batuu Boogie", "Mus Kat & Nalpak - Bright Suns", "Duro Droids - Beep Boop Bop"],
                "dance": ["Batuu Boogie", "Elem Zadowz - Huttuk Cheeka", "Mus Kat & Nalpak - Turbulence"],
                "groovy": ["Batuu Boogie", "Elem Zadowz - Huttuk Cheeka", "Vee Gooda, Ryco - Aloogahoo"],
                "slow": ["Modal Notes", "Moulee-rah", "Doolstan"],
                "chill": ["Doolstan", "Modal Notes", "Opening"],
                "ambient": ["Opening", "Doolstan", "Moulee-rah"],
                "droid": ["Droid World", "Duro Droids - Beep Boop Bop"],
                "robot": ["Droid World", "Duro Droids - Beep Boop Bop"],
                "electronic": ["Droid World", "Duro Droids - Beep Boop Bop", "Mus Kat & Nalpak - Turbulence"],
                "alien": ["Yocola Ateema", "Bai Tee Tee", "Gaya"],
                "weird": ["Yocola Ateema", "Utinni", "Bai Tee Tee"],
                "galactic": ["Mus Kat & Nalpak - Bright Suns", "Droid World", "Zano - Goola Bukee"],
                "space": ["Mus Kat & Nalpak - Bright Suns", "Droid World", "Zano - Goola Bukee"],
                "star wars": ["Cantina Band", "Cantina Song aka Mad About Mad About Me", "Jabba Flow"],
                "traditional": ["Cantina Band", "Cantina Song aka Mad About Mad About Me", "Jabba Flow"]
            }
            
            # Find all potential matches from keywords
            potential_matches = []
            for keyword, tracks in keyword_mapping.items():
                if keyword in query_lower:
                    # Add only tracks that actually exist in our available tracks
                    for track in tracks:
                        if track in available_tracks and track not in potential_matches:
                            potential_matches.append(track)
            
            if potential_matches:
                # Filter out recently played tracks if possible
                fresh_matches = [track for track in potential_matches if track not in self._recently_played_tracks]
                
                # If we have fresh matches, use those; otherwise use all matches
                selection_pool = fresh_matches if fresh_matches else potential_matches
                
                # Choose a random track from our options
                import random
                selected_track = random.choice(selection_pool)
                self.logger.info(f"Found keyword match: '{selected_track}' for query '{query}'")
                self._add_to_recently_played(selected_track)
                return selected_track
            
            # If no matches found, return a random track not recently played
            import random
            
            # Filter out recently played tracks if possible
            selection_pool = [t for t in available_tracks if t not in self._recently_played_tracks]
            
            # If all tracks were recently played, use full list
            if not selection_pool:
                selection_pool = available_tracks
            
            random_track = random.choice(selection_pool)
            self.logger.warning(f"No matches found for '{query}', playing random track: {random_track}")
            self._add_to_recently_played(random_track)
            return random_track
            
        except Exception as e:
            self.logger.error(f"Error in track selection: {e}")
            # In case of error, try to return a track if we have any
            if available_tracks:
                import random
                return random.choice(available_tracks)
            return query  # Last resort fallback

    def _add_to_recently_played(self, track: str) -> None:
        """Add a track to the recently played list, maintaining maximum history size."""
        if track in self._recently_played_tracks:
            # Move to the end (most recent)
            self._recently_played_tracks.remove(track)
        
        self._recently_played_tracks.append(track)
        
        # Trim to max history size
        while len(self._recently_played_tracks) > self._max_history:
            self._recently_played_tracks.pop(0)  # Remove oldest track

    # ------------------------------------------------------------------
    # Legacy Intent handling (keep for compatibility)
    # ------------------------------------------------------------------
    async def _handle_intent_detected(self, payload: Dict[str, Any]) -> None:
        """Handle INTENT_DETECTED events from GPT service.
        
        This is kept for legacy support and other intent types.
        """
        try:
            intent_payload = IntentPayload(**payload)
            intent_name = intent_payload.intent_name
            
            self.logger.info(f"BrainService handling intent: {intent_name}")
            
            # Store current intent for later
            self._current_intent = intent_payload
            
            # For play_music, create a plan for timeline executor
            if intent_name == "play_music":
                track = intent_payload.parameters.get("track", "")
                if not track:
                    self.logger.error("No track specified in play_music intent")
                    return
                    
                self.logger.info(f"Creating play music plan for track: {track}")
                
                # Use smart track selection
                selected_track = await self._smart_track_selection(track)
                
                if selected_track:
                    import uuid
                    music_plan = PlanPayload(
                        plan_id=str(uuid.uuid4()),
                        layer="foreground",
                        steps=[
                            PlanStep(
                                id="music",
                                type="play_music",
                                genre=selected_track
                            )
                        ]
                    )
                    
                    # Send to timeline executor
                    await self._emit_dict(
                        EventTopics.PLAN_READY,
                        music_plan
                    )
            
            # Handle stop music intent with timeline executor
            elif intent_name == "stop_music":
                self.logger.info("Creating stop music plan")
                
                import uuid
                stop_plan = PlanPayload(
                    plan_id=str(uuid.uuid4()),
                    layer="foreground",
                    steps=[
                        PlanStep(
                            id="stop_music",
                            type="speak",
                            text="Stopping the music now."
                        ),
                        PlanStep(
                            id="stop_command",
                            type="play_music",
                            genre="stop"  # Special value to indicate stop
                        )
                    ]
                )
                
                # Send to timeline executor
                await self._emit_dict(
                    EventTopics.PLAN_READY,
                    stop_plan
                )
            
            # Other intents can be handled here
            else:
                self.logger.info(f"Intent {intent_name} not handled by BrainService")
                return
            
            # Mark intent as consumed
            await self._emit_dict(
                EventTopics.INTENT_CONSUMED,
                {"intent_id": intent_payload.event_id}
            )
            
        except ValidationError as e:
            await self._emit_status(
                ServiceStatus.ERROR,
                f"Invalid intent payload: {e}",
                LogLevel.ERROR
            )
        except Exception as e:
            await self._emit_status(
                ServiceStatus.ERROR,
                f"Error handling intent: {e}",
                LogLevel.ERROR
            )

    # ------------------------------------------------------------------
    # Music playback handling
    # ------------------------------------------------------------------
    async def _handle_music_started(self, payload: Dict[str, Any]) -> None:
        """Handle MUSIC_PLAYBACK_STARTED events from music controller.
        
        This is the third step in the Three-Call pattern.
        When music starts playing, we generate a track-specific intro only for 
        voice-initiated requests (not CLI commands).
        """
        try:
            # Store track metadata
            self._last_track_meta = {
                "track_name": payload.get("track_name", "Unknown Track"),
                "duration": payload.get("duration", 0)
            }
            
            self.logger.info(f"Music started playing: {self._last_track_meta['track_name']}")
            
            # Check source of music request - Skip CLI-initiated playback
            source = payload.get("source", "unknown")
            if source == "cli":
                self.logger.info(f"CLI-initiated music playback, skipping track intro")
                return
            
            # Generate track intro plan for voice-initiated or unknown source playback
            await self._make_track_intro_plan(self._last_track_meta)
            
        except ValidationError as e:
            await self._emit_status(
                ServiceStatus.ERROR,
                f"Invalid music playback payload: {e}",
                LogLevel.ERROR
            )
        except Exception as e:
            await self._emit_status(
                ServiceStatus.ERROR,
                f"Error handling music playback: {e}",
                LogLevel.ERROR
            )

    # ------------------------------------------------------------------
    # LLM response handling
    # ------------------------------------------------------------------
    async def _handle_llm_response(self, payload: Dict[str, Any]) -> None:
        """Handle LLM_RESPONSE events from GPT service.
        
        This is mainly used for handling track intro lines from GPT.
        """
        try:
            response = LLMResponsePayload(**payload)
            
            # If this is a track intro response, create a plan for it
            if response.response_type == "track_intro" and response.is_complete:
                # Create plan with speak step from the intro text
                steps = [
                    PlanStep(
                        id="intro",
                        type="speak",
                        text=response.text
                    )
                ]
                
                # Emit plan ready event
                await self._emit_dict(
                    EventTopics.PLAN_READY,
                    PlanPayload(
                        layer="foreground", 
                        steps=steps
                    )
                )
        except ValidationError as e:
            await self._emit_status(
                ServiceStatus.ERROR,
                f"Invalid LLM response payload: {e}",
                LogLevel.ERROR
            )
        except Exception as e:
            await self._emit_status(
                ServiceStatus.ERROR,
                f"Error handling LLM response: {e}",
                LogLevel.ERROR
            )

    # ------------------------------------------------------------------
    # Memory handling
    # ------------------------------------------------------------------
    async def _handle_memory_updated(self, payload: Dict[str, Any]) -> None:
        """Handle MEMORY_UPDATED events from MemoryService."""
        # Future: Add reactive capabilities based on memory changes
        pass
    
    # ------------------------------------------------------------------
    # Intent execution result emission
    # ------------------------------------------------------------------
    async def _emit_intent_execution_result(
        self,
        intent_name: str,
        parameters: Dict[str, Any],
        result: Dict[str, Any],
        tool_call_id: Optional[str] = None,
        conversation_id: Optional[str] = None
    ) -> None:
        """Emit an intent execution result for verbal feedback.
        
        Args:
            intent_name: The intent that was executed
            parameters: The parameters used
            result: The result data
            tool_call_id: Optional OpenAI tool call ID
            conversation_id: Optional conversation ID
        """
        try:
            self.logger.info(f"Emitting intent execution result for: {intent_name}")
            
            success = result.get("success", True)
            error_message = result.get("error", None) if not success else None
            
            payload = IntentExecutionResultPayload(
                intent_name=intent_name,
                parameters=parameters,
                result=result,
                success=success,
                error_message=error_message,
                tool_call_id=tool_call_id,
                conversation_id=conversation_id
            )
            
            await self._emit_dict(EventTopics.INTENT_EXECUTION_RESULT, payload)
            
        except Exception as e:
            self.logger.error(f"Error emitting intent execution result: {e}")

    # ------------------------------------------------------------------
    # Plan generation
    # ------------------------------------------------------------------
    async def _make_track_intro_plan(self, track_meta: Dict[str, Any]) -> None:
        """Generate a track intro plan based on the current track metadata.
        
        This is the third step in the Three-Call pattern.
        """
        try:
            # In a real implementation, we would call GPT directly
            # For this implementation, we'll generate a track intro based on metadata
            
            track_name = track_meta.get("track_name", "this funky track")
            
            # Create a track intro with the track name
            # In a full implementation, this would be a call to the GPT service
            # for a track-specific custom intro
            intro_text = f"Now dropping the beat with {track_name}! This track is absolutely fire!"
            
            self.logger.info(f"Generated track intro: '{intro_text}'")
            
            # Create plan with speak step
            steps = [
                PlanStep(
                    id="intro",
                    type="speak",
                    text=intro_text
                )
            ]
            
            # Emit plan ready event
            await self._emit_dict(
                EventTopics.PLAN_READY,
                PlanPayload(
                    layer="foreground", 
                    steps=steps
                )
            )
            
            self.logger.info(f"Emitted PLAN_READY event with intro for '{track_name}'")
        except Exception as e:
            await self._emit_status(
                ServiceStatus.ERROR,
                f"Error creating track intro plan: {e}",
                LogLevel.ERROR
            ) 

    # ------------------------------------------------------------------
    # DJ Mode handlers
    # ------------------------------------------------------------------
    async def _handle_dj_mode_changed(self, payload: Dict[str, Any]) -> None:
        """Handle DJ mode activation/deactivation.
        
        Args:
            payload: Event payload with dj_mode_active flag or command info
        """
        try:
            # Handle direct mode change payload with dj_mode_active flag
            if "dj_mode_active" in payload:
                is_active = payload["dj_mode_active"]
                self._dj_mode_active = is_active
                self.logger.info(f"DJ Mode {'activated' if is_active else 'deactivated'}")
                
                # If activating, start playing music if nothing is playing
                if is_active and self._music_library and self._music_library.tracks:
                    # Use _smart_track_selection to get a track name
                    # This handles getting a random track not recently played
                    initial_track = await self._smart_track_selection("")
                    
                    if initial_track:
                        await self._emit_dict(
                            EventTopics.MUSIC_COMMAND,
                            {
                                "action": "play",
                                "song_query": initial_track,
                                "source": "dj"
                            }
                        )
                return
            
            # Handle CLI command format
            command = payload.get("command", "").lower()
            
            # Handle the full command strings
            if command == "dj start":
                await self._emit_dict(
                    EventTopics.DJ_MODE_CHANGED,
                    {"dj_mode_active": True}
                )
                
            elif command == "dj stop":
                await self._emit_dict(
                    EventTopics.DJ_MODE_CHANGED,
                    {"dj_mode_active": False}
                )
                
            else:
                self.logger.error(f"Invalid payload for DJ_MODE_CHANGED: {payload}")
                
        except Exception as e:
            self.logger.error(f"Error handling DJ mode change: {e}")
            
    async def _handle_dj_next_track(self, payload: Dict[str, Any]) -> None:
        """Handle DJ next track command."""
        try:
            if not self._dj_mode_active:
                self.logger.warning("DJ next track command received but DJ mode is not active")
                return
                
            # Get currently playing track or default to unknown
            current_track = payload.get("current_track", "Unknown Track")
            
            # Clear any previous transition plan flag
            self._dj_transition_plan_ready = False
            
            # Select the next track to play
            next_track = await self._select_next_dj_track(current_track)
            if not next_track:
                self.logger.error("Failed to select next track for DJ skip")
                await self._emit_status(
                    ServiceStatus.ERROR,
                    "DJ skip failed: could not select next track",
                    LogLevel.ERROR
                )
                return
                
            self.logger.info(f"DJ next track: skipping from {current_track} to {next_track}")
                
            # Request cached commentary for the transition (with skip flag)
            cache_key = await self._request_cached_dj_commentary(current_track, next_track, is_skip=True)
            
            # Create a skip transition plan with the commentary and next track
            import uuid
            skip_plan = PlanPayload(
                plan_id=str(uuid.uuid4()),
                layer="foreground",
                steps=[
                    PlanStep(
                        id="dj_skip_commentary",
                        type="speak",
                        text=cache_key  # Use cache key instead of raw text
                    ),
                    PlanStep(
                        id="next_track",
                        type="play_music",
                        genre=next_track
                    )
                ]
            )
            
            # Emit the plan to timeline executor
            await self._emit_dict(EventTopics.PLAN_READY, skip_plan)
            self.logger.info(f"DJ skip plan emitted: {current_track} → {next_track}")
            
            # Update memory with the track history
            self._add_to_recently_played(next_track)
            await self._emit_dict(
                EventTopics.MEMORY_SET, 
                {
                    "key": "dj_track_history",
                    "value": self._recently_played_tracks
                }
            )
            
        except Exception as e:
            self.logger.error(f"Error handling DJ next track: {e}")
            await self._emit_status(
                ServiceStatus.ERROR,
                f"DJ skip error: {e}",
                LogLevel.ERROR
            )
            
    async def _handle_dj_track_queued(self, payload: Dict[str, Any]) -> None:
        """Handle DJ_TRACK_QUEUED event.
        
        Expected payload: {"command": "dj queue", "args": ["<track_name>"]}
        """
        try:
            if isinstance(payload, dict) and "command" in payload and payload["command"] == "dj queue" and "args" in payload and isinstance(payload["args"], list) and payload["args"]:
                # Extract track name from args
                track_name = " ".join(payload["args"])

                # Validate if track exists in the music library (optional but good practice)
                if not self._music_library.get_track_by_name(track_name):
                    self.logger.warning(f"Attempted to queue unknown track: {track_name}")
                    # Optionally, emit an error response here if needed
                    # await self.emit_error_response(...)
                    return # Stop processing if track is unknown

                # Save queued track
                await self._memory_service.set("dj_next_track", track_name) # Use MemoryService to set state

                # Update track history (this logic can remain or be refined)
                # The _handle_dj_track_queued in MemoryService already handles history update when dj_next_track is set,
                # so we might not need this part here if we rely on MemoryService's handler.
                # Let's remove the redundant history update here and rely on MemoryService.
                # history = await self._memory_service.get("dj_track_history", [])
                # ... history update logic ...
                # await self._memory_service.set("dj_track_history", history)

                self.logger.info(f"Queued track for DJ mode: {track_name}")
                # Optionally, emit a success response here if needed
                # await self._emit_dict(EventTopics.CLI_RESPONSE, {"success": True, "message": f"Track '{track_name}' queued."}) # Example response

            else:
                self.logger.error(f"Invalid payload for DJ_TRACK_QUEUED: {payload}")
                # Emit error response for invalid payload
                # await self._emit_dict(EventTopics.CLI_RESPONSE, {"success": False, "error": "Invalid queue command payload."}) # Example response
                await self._emit_status(
                    ServiceStatus.ERROR,
                    f"Invalid payload for DJ_TRACK_QUEUED: {payload}",
                    LogLevel.ERROR
                )

        except Exception as e:
            self.logger.error(f"Error handling DJ track queued: {e}")
            # Emit error response for exception
            # await self._emit_dict(EventTopics.CLI_RESPONSE, {"success": False, "error": f"Error queuing track: {e}"}) # Example response
            await self._emit_status(
                ServiceStatus.ERROR,
                f"Error updating DJ track queue: {e}",
                LogLevel.ERROR
            )
    
    async def _handle_track_ending_soon(self, payload: Dict[str, Any]) -> None:
        """Handle TRACK_ENDING_SOON events from MusicControllerService.
        
        This is the main trigger for DJ mode transitions.
        
        Args:
            payload: Event payload with track_name and remaining_seconds
        """
        try:
            # Only proceed if DJ mode is active
            if not self._dj_mode_active:
                self.logger.info("Track ending soon, but DJ Mode is not active")
                return
                
            track_name = payload.get("track_name", "Unknown Track")
            remaining_seconds = payload.get("remaining_seconds", 30)
            
            self.logger.info(f"Track ending soon: {track_name}, {remaining_seconds}s remaining")
            
            # If we already have a transition plan ready, don't create another one
            if self._dj_transition_plan_ready:
                self.logger.info("Transition plan already prepared, skipping")
                return
            
            # Check memory service for queued track first
            await self._emit_dict(
                EventTopics.MEMORY_GET, 
                {
                    "key": "dj_next_track",
                    "callback_topic": EventTopics.MEMORY_VALUE
                }
            )
            
            # Wait a short time for the response
            await asyncio.sleep(0.1)
            
            # Check if we have a next track queued (either in memory or local state)
            if self._dj_next_track:
                next_track = self._dj_next_track
                self.logger.info(f"Using queued track for transition: {next_track}")
            else:
                # Select the next track to play
                next_track = await self._select_next_dj_track(track_name)
                if not next_track:
                    self.logger.error("Failed to select next track for DJ transition")
                    await self._emit_status(
                        ServiceStatus.ERROR,
                        "DJ transition failed: could not select next track",
                        LogLevel.ERROR
                    )
                    return
            
            # Request cached commentary for the transition
            cache_key = await self._request_cached_dj_commentary(track_name, next_track)
            
            # Create a transition plan with the commentary and next track
            import uuid
            transition_plan = PlanPayload(
                plan_id=str(uuid.uuid4()),
                layer="foreground",
                steps=[
                    PlanStep(
                        id="dj_commentary",
                        type="speak",
                        text=cache_key  # Use cache key instead of raw text
                    ),
                    PlanStep(
                        id="next_track",
                        type="play_music",
                        genre=next_track
                    )
                ]
            )
            
            # Mark that we have a transition plan ready
            self._dj_transition_plan_ready = True
            
            # Emit the plan
            await self._emit_dict(EventTopics.PLAN_READY, transition_plan)
            self.logger.info(f"DJ transition plan ready: {track_name} → {next_track}")
            
            # Update memory with the track history
            self._add_to_recently_played(next_track)
            await self._emit_dict(
                EventTopics.MEMORY_SET, 
                {
                    "key": "dj_track_history",
                    "value": self._recently_played_tracks
                }
            )
            
        except Exception as e:
            self.logger.error(f"Error handling track ending soon: {e}")
            await self._emit_status(
                ServiceStatus.ERROR,
                f"DJ transition error: {e}",
                LogLevel.ERROR
            )
    
    async def _select_next_dj_track(self, current_track: str) -> Optional[str]:
        """Select the next track for DJ mode based on intelligent sequencing.
        
        This implements the track sequencing algorithm with genre/energy matching.
        
        Args:
            current_track: The currently playing track
            
        Returns:
            Name of the next track to play
        """
        try:
            # Get all available tracks
            if not self._music_library.tracks:
                self.logger.warning("No tracks available for DJ selection")
                return None
                
            # Find all tracks except the current one and recently played
            available_tracks = list(self._music_library.tracks.keys())
            
            # Determine which genre group the current track belongs to
            current_genre_group = None
            for genre, tracks in self._genre_groups.items():
                if current_track in tracks:
                    current_genre_group = genre
                    break
            
            # Find tracks in the same genre group (for continuity)
            same_genre_tracks = []
            if current_genre_group:
                same_genre_tracks = [
                    track for track in self._genre_groups[current_genre_group] 
                    if track in available_tracks and track != current_track and track not in self._recently_played_tracks
                ]
            
            # Also find tracks in related genre groups (for variety)
            related_genre_tracks = []
            
            # Define genre relationships for more natural transitions
            genre_relationships = {
                "upbeat": ["electronic", "traditional", "alien"],
                "electronic": ["upbeat", "alien", "chill"],
                "chill": ["electronic", "traditional", "misc"],
                "traditional": ["upbeat", "chill", "misc"],
                "alien": ["upbeat", "electronic", "misc"],
                "misc": ["traditional", "alien", "chill"]
            }
            
            if current_genre_group and current_genre_group in genre_relationships:
                related_genres = genre_relationships[current_genre_group]
                for genre in related_genres:
                    if genre in self._genre_groups:
                        related_genre_tracks.extend([
                            track for track in self._genre_groups[genre]
                            if track in available_tracks and track != current_track and track not in self._recently_played_tracks
                        ])
            
            # Combine the pools with preferential weighting
            selection_pool = []
            
            # Add same genre tracks with higher weight (more likely to be selected)
            selection_pool.extend(same_genre_tracks * 3)  # Add 3 copies for higher weight
            
            # Add related genre tracks with normal weight
            selection_pool.extend(related_genre_tracks)
            
            # If we still don't have any tracks, add all tracks except current and recently played
            if not selection_pool:
                selection_pool = [
                    track for track in available_tracks 
                    if track != current_track and track not in self._recently_played_tracks
                ]
            
            # If we STILL don't have tracks (rare but possible if all played recently)
            # Then just use all except current
            if not selection_pool:
                selection_pool = [track for track in available_tracks if track != current_track]
                
            # If we have no tracks at all, use any track
            if not selection_pool:
                selection_pool = available_tracks
            
            # Select a random track from our weighted pool
            import random
            selected_track = random.choice(selection_pool)
            
            # Add to recently played to avoid repetition
            self._add_to_recently_played(selected_track)
            
            # Update DJ track history for rotation tracking
            self._dj_track_history.append(selected_track)
            
            self.logger.info(f"Selected next DJ track: {selected_track}")
            return selected_track
            
        except Exception as e:
            self.logger.error(f"Error selecting next DJ track: {e}")
            
            # Fallback to simple random selection
            try:
                import random
                available_tracks = list(self._music_library.tracks.keys())
                if available_tracks:
                    return random.choice(available_tracks)
            except:
                pass
                
            return None
    
    async def _request_cached_dj_commentary(self, current_track: str, next_track: str, is_skip: bool = False) -> str:
        """Generate and cache DJ commentary for track transitions.
        
        Args:
            current_track: Currently playing track name
            next_track: Next track to play
            is_skip: Whether this is a skip command (shorter commentary)
            
        Returns:
            Cache key for the generated commentary
        """
        try:
            # Generate the commentary text
            commentary = await self._generate_dj_commentary(current_track, next_track, is_skip)
            
            # Create a unique cache key
            cache_key = f"dj_commentary_{current_track}_{next_track}_{is_skip}_{int(time.time())}"
            
            # Request caching of the commentary
            await self._emit_dict(
                EventTopics.SPEECH_CACHE_REQUEST,
                SpeechCacheRequestPayload(
                    text=commentary,
                    cache_key=cache_key,
                    priority=2 if is_skip else 1,  # Higher priority for skip commands
                    ttl_seconds=self._config.dj_cache_ttl_seconds,
                    metadata={
                        "current_track": current_track,
                        "next_track": next_track,
                        "is_skip": is_skip,
                        "dj_style": self._dj_transition_style
                    }
                )
            )
            
            self.logger.info(f"Requested DJ commentary caching with key: {cache_key}")
            return cache_key
            
        except Exception as e:
            self.logger.error(f"Error requesting cached DJ commentary: {e}")
            # Return empty cache key to indicate failure
            return ""

    async def _generate_dj_commentary(self, current_track: str, next_track: str, is_skip: bool = False) -> str:
        """Generate DJ-style commentary for transitions between tracks.
        
        Args:
            current_track: The currently playing track
            next_track: The next track to play
            is_skip: Whether this is a skip command (shorter commentary)
            
        Returns:
            DJ commentary text
        """
        try:
            # In production, this would use GPT or a similar model to generate
            # custom commentary based on track metadata, time of day, etc.
            
            # For now, use a template-based approach with some variety
            import random
            
            # Get transition style from state or randomly select a new one
            style = self._dj_transition_style
            if random.random() < 0.3:  # 30% chance to change style
                style = random.choice(self._config.dj_commentary_styles)
                self._dj_transition_style = style
            
            # Shorter commentary for skip commands
            if is_skip:
                skip_templates = [
                    f"Switching it up! Here comes {next_track}!",
                    f"You got it! Let's drop {next_track} right now!",
                    f"Alright, enough of that! Moving to {next_track}!",
                    f"Skip requested! Here's {next_track} coming at you!",
                    f"Time for something different! {next_track}, let's go!"
                ]
                return random.choice(skip_templates)
            
            # Regular transition commentary based on style
            if style == "energetic":
                templates = [
                    f"That was {current_track}! Get ready for the ultimate vibe shift as we drop into {next_track}! Let's GO!",
                    f"Whoa! {current_track} had the energy flowing! Now keeping the momentum with {next_track}! Put your hands UP!",
                    f"R3X is taking you on a journey! From {current_track} straight into the incredible {next_track}! Feel the ENERGY!"
                ]
            elif style == "chill":
                templates = [
                    f"Smooth vibes from {current_track}. Now sliding into the equally smooth {next_track}. Just relax and enjoy.",
                    f"That was {current_track}. Now let's keep the chill atmosphere going with {next_track}.",
                    f"R3X keeping it laid back. From {current_track} we flow into {next_track}. Perfect for this moment."
                ]
            elif style == "funny":
                templates = [
                    f"That was {current_track} - not bad for a droid DJ if I do say so myself! Now here's {next_track} - no applause necessary, just credits!",
                    f"If you enjoyed {current_track}, you'll probably tolerate {next_track}! I'm kidding, it's actually amazing!",
                    f"That was {current_track}! Now switching to {next_track} - a track so good, even Jawas would pay full price for it!"
                ]
            elif style == "informative":
                templates = [
                    f"That was the distinctive sound of {current_track}. Now transitioning to {next_track}, another selection from our galactic playlist.",
                    f"You've been listening to {current_track}. Up next is {next_track}, a perfect follow-up in terms of rhythm and energy.",
                    f"R3X music selection: {current_track} complete. Now continuing with {next_track}, carefully selected to maintain the vibe."
                ]
            elif style == "mysterious":
                templates = [
                    f"The echoes of {current_track} fade into the void... but what emerges? Listen closely to {next_track} and discover its secrets.",
                    f"As {current_track} completes its journey, the cosmic algorithms reveal our next destination: {next_track}.",
                    f"The patterns within {current_track} have led us here, to the enigmatic rhythms of {next_track}. Where will it take us?"
                ]
            elif style == "dramatic":
                templates = [
                    f"The final notes of {current_track} signal a momentous change! Prepare yourselves for the absolutely stunning {next_track}!",
                    f"What an incredible moment! {current_track} draws to a close, and now, the track you've all been waiting for: {next_track}!",
                    f"A defining musical moment! Transitioning from the unforgettable {current_track} to the breathtaking {next_track}!"
                ]
            else:  # "galactic" or fallback
                templates = [
                    f"From across the galaxy, {current_track} has entertained you. Now, jumping to hyperspace with {next_track}!",
                    f"Star systems align as we transition from {current_track} to the stellar sounds of {next_track}.",
                    f"R3X's galaxy-famous mix continues! That was {current_track} from the Outer Rim, now flying to {next_track}!"
                ]
            
            return random.choice(templates)
            
        except Exception as e:
            self.logger.error(f"Error generating DJ commentary: {e}")
            return f"Moving from {current_track} to {next_track}. DJ R3X in the mix!" 

    # Listen for memory values coming back - for async memory coordination
    async def _handle_memory_value(self, payload: Dict[str, Any]) -> None:
        """Handle MEMORY_VALUE events from MemoryService."""
        try:
            if isinstance(payload, dict) and "key" in payload:
                key = payload.get("key")
                value = payload.get("value")
                
                # Handle DJ next track from memory - allow None value
                if key == "dj_next_track":
                    self._dj_next_track = value  # Can be None
                    self.logger.debug(f"Retrieved dj_next_track from memory: {value}")
                
                # Handle DJ mode state - default to False if not found
                elif key == "dj_mode_active":
                    self._dj_mode_active = bool(value) if value is not None else False
                    self.logger.debug(f"Retrieved dj_mode_active from memory: {self._dj_mode_active}")
                
                # Handle track history - default to empty list if not found
                elif key == "dj_track_history":
                    self._recently_played_tracks = list(value) if value is not None else []
                    self.logger.debug(f"Retrieved track history from memory: {len(self._recently_played_tracks)} tracks")
                
        except Exception as e:
            self.logger.error(f"Error handling memory value: {e}")
            # Don't raise the exception - allow service to continue with defaults

    async def _handle_speech_cache_ready(self, payload: Dict[str, Any]) -> None:
        """Handle SPEECH_CACHE_READY events from CachedSpeechService.
        
        Args:
            payload: Event payload with cache information
        """
        try:
            if not isinstance(payload, dict) or "cache_key" not in payload:
                return
            
            cache_key = payload.get("cache_key")
            duration_ms = payload.get("duration_ms", 0)
            metadata = payload.get("metadata", {})
            
            # Extract metadata
            current_track = metadata.get("current_track", "Unknown")
            next_track = metadata.get("next_track", "Unknown")
            is_skip = metadata.get("is_skip", False)
            
            self.logger.info(
                f"DJ commentary cached: {cache_key}, duration: {duration_ms}ms, "
                f"transition: {current_track} → {next_track}"
            )
            
        except Exception as e:
            self.logger.error(f"Error handling speech cache ready: {e}")

    async def _handle_speech_cache_error(self, payload: Dict[str, Any]) -> None:
        """Handle SPEECH_CACHE_ERROR events from CachedSpeechService.
        
        Args:
            payload: Event payload with error information
        """
        try:
            if not isinstance(payload, dict) or "cache_key" not in payload:
                return
            
            cache_key = payload.get("cache_key")
            error = payload.get("error", "Unknown error")
            
            self.logger.error(f"DJ commentary caching failed: {cache_key}, error: {error}")
            
            # Fallback to non-cached commentary if needed
            if "dj_commentary_" in cache_key:
                self.logger.info("Using non-cached DJ commentary as fallback")
            
        except Exception as e:
            self.logger.error(f"Error handling speech cache error: {e}") 