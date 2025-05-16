"""
Brain Service for Cantina OS
================================
A GPT-backed planner service that turns intents and memory into declarative plans
for the timeline executor. Handles the Three-Call GPT pattern for DJ R3X.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, List, Optional, Set

from pydantic import BaseModel, ValidationError

from cantina_os.bus import EventTopics, LogLevel, ServiceStatus
from ..base import StandardService
from cantina_os.event_payloads import (
    IntentPayload, 
    PlanPayload,
    PlanStep,
    MusicCommandPayload,
    LLMResponsePayload,
    IntentExecutionResultPayload
)
from cantina_os.models.music_models import MusicTrack, MusicLibrary

# ---------------------------------------------------------------------------
# Configuration model
# ---------------------------------------------------------------------------
class _Config(BaseModel):
    """Pydantic‑validated configuration for the brain service."""
    gpt_model_intro: str = "gpt-3.5-turbo"
    gpt_temperature_intro: float = 0.7
    chat_history_max_turns: int = 10
    handled_intents: List[str] = ["play_music"]


# ---------------------------------------------------------------------------
# Service Implementation
# ---------------------------------------------------------------------------
class BrainService(StandardService):
    """Brain service for DJ R3X.
    
    Handles intent processing, music commands, and track intro generation.
    """

    def __init__(self, event_bus, config=None, name="brain_service"):
        super().__init__(event_bus, config, name=name)
        
        # ----- validated configuration -----
        self._config = _Config(**(config or {}))
        
        # ----- bookkeeping -----
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tasks: List[asyncio.Task] = []
        self._subs: List[tuple] = []  # For tracking subscriptions
        
        # ----- brain state -----
        self._current_intent: Optional[IntentPayload] = None
        self._last_track_meta: Optional[Dict[str, Any]] = None
        self._handled_intents: Set[str] = set(self._config.handled_intents)
        
        # ----- music library management -----
        self._music_library = MusicLibrary()
        
        # Track recently played songs to avoid repetition
        self._recently_played_tracks = []
        # Maximum number of tracks to remember for avoiding repetition
        self._max_history = 3

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
    
    async def _subscribe(self, topic: EventTopics, handler: Callable) -> None:
        """Safe async subscription wrapper that tracks tasks for cleanup."""
        self._subs.append((topic, handler))
        task = asyncio.create_task(self.subscribe(topic, handler))
        self._tasks.append(task)
        await task  # Ensure the subscription is established before return
    
    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def _start(self) -> None:
        """Initialize brain service and set up subscriptions."""
        try:
            self._loop = asyncio.get_running_loop()
            await self._setup_subscriptions()  # Now using await
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
        self._subs.clear()
        
        self.logger.info("Brain service stopped")
        await self._emit_status(ServiceStatus.STOPPED, "Brain service stopped")

    # ------------------------------------------------------------------
    # Subscription setup
    # ------------------------------------------------------------------
    async def _setup_subscriptions(self) -> None:
        """Register event-handlers for brain processing."""
        # Subscribe to essential events for the new direct flow
        await self._subscribe(EventTopics.BRAIN_MUSIC_REQUEST, self._handle_music_request)
        await self._subscribe(EventTopics.BRAIN_MUSIC_STOP, self._handle_music_stop)
        await self._subscribe(EventTopics.MUSIC_PLAYBACK_STARTED, self._handle_music_started)
        
        # Keep INTENT_DETECTED for legacy/other intents
        await self._subscribe(EventTopics.INTENT_DETECTED, self._handle_intent_detected)
        
        # Also subscribe to LLM responses for track intro handling
        await self._subscribe(EventTopics.LLM_RESPONSE, self._handle_llm_response)
        
        # Subscribe to memory updates for reactive capabilities
        await self._subscribe(EventTopics.MEMORY_UPDATED, self._handle_memory_updated)
        
        # Subscribe to music library updates to keep track list in sync
        await self._subscribe(EventTopics.MUSIC_LIBRARY_UPDATED, self._handle_music_library_updated)
        
        # Fetch available tracks at startup
        await self._fetch_available_tracks()

    async def _handle_music_library_updated(self, payload: Dict[str, Any]) -> None:
        """Handle music library updates from MusicControllerService."""
        try:
            if not isinstance(payload, dict):
                self.logger.error(f"Invalid music library update payload: {payload}")
                return
                
            if "tracks" in payload and isinstance(payload["tracks"], list):
                tracks_data = payload["tracks"]
                self._music_library.tracks = tracks_data
                self.logger.info(f"Updated music library from event: {len(self._music_library.tracks)} tracks")
        except Exception as e:
            self.logger.error(f"Error handling music library update: {e}")

    async def _fetch_available_tracks(self) -> None:
        """Fetch available tracks from MusicControllerService."""
        try:
            # Request track list via MUSIC_COMMAND event
            track_list_payload = {"command": "list", "subcommand": None, "args": [], "raw_input": "list music"}
            await self.emit(EventTopics.MUSIC_COMMAND, track_list_payload)
            
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