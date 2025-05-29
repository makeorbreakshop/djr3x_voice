"""
Memory Service for Cantina OS
================================
A service that provides working memory for DJ R3X.
Maintains state keys, chat history, and exposes an API to access and modify this data.
"""

from __future__ import annotations

import asyncio
import time
import json
import os
from typing import Any, Callable, Dict, List, Optional

from pydantic import BaseModel, ValidationError

from cantina_os.bus import EventTopics, LogLevel, ServiceStatus
from cantina_os.base_service import BaseService
from cantina_os.event_payloads import BaseEventPayload, IntentPayload

# ---------------------------------------------------------------------------
# Configuration model
# ---------------------------------------------------------------------------
class _Config(BaseModel):
    """Pydantic‑validated configuration for the memory service."""
    chat_history_max_turns: int = 10
    state_keys: List[str] = [
        "mode", "music_playing", "current_track", 
        "last_intent", "chat_history",
        # DJ mode state keys
        "dj_mode_active", "dj_track_history", "dj_next_track",
        "dj_transition_style", "dj_user_preferences",
        "dj_lookahead_cache"
    ]

# ---------------------------------------------------------------------------
# Memory Update Payload
# ---------------------------------------------------------------------------
class MemoryUpdatedPayload(BaseEventPayload):
    """Schema for MEMORY_UPDATED event."""
    key: str
    new_value: Any
    old_value: Optional[Any] = None

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
STATE_FILE_PATH = os.path.join(os.path.dirname(__file__), "data", "memory_state.json")

# ---------------------------------------------------------------------------
# Service Implementation
# ---------------------------------------------------------------------------
class MemoryService(BaseService):
    """Working memory service for DJ R3X.
    
    Provides access to state variables, chat history, and emits change events.
    Acts as a central state store for the system.
    """

    def __init__(self, event_bus, config=None, name="memory_service"):
        super().__init__(service_name=name, event_bus=event_bus)

        # ----- validated configuration -----
        self._config = _Config(**(config or {}))

        # ----- bookkeeping -----
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tasks: List[asyncio.Task] = []
        
        # ----- memory state -----
        # Load state from file, initialize defaults for missing keys
        loaded_state = self._load_state()
        self._state: Dict[str, Any] = {key: loaded_state.get(key, None) for key in self._config.state_keys}

        # Ensure specific initial values if not loaded (e.g., empty lists/dicts)
        if self._state.get("chat_history") is None:
             self._state["chat_history"] = []  # Initialize chat history as empty list
        if self._state.get("music_playing") is None:
            self._state["music_playing"] = False  # Initialize music playing state
        if self._state.get("dj_mode_active") is None:
            self._state["dj_mode_active"] = False  # Initialize DJ mode state
        if self._state.get("dj_track_history") is None:
            self._state["dj_track_history"] = []  # Initialize DJ track history
        if self._state.get("dj_user_preferences") is None:
            self._state["dj_user_preferences"] = {}  # Initialize DJ user preferences

        self._waiters: Dict[str, List[asyncio.Event]] = {}  # For wait_for predicate functionality

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

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def _start(self) -> None:
        """Initialize memory service and set up subscriptions."""
        try:
            self.logger.debug("MemoryService: Starting initialization")
            
            self._loop = asyncio.get_running_loop()
            
            # Set up subscriptions
            await self._setup_subscriptions()
            self.logger.debug("MemoryService: Subscriptions set up")
            
            # Initialize state with defaults for critical keys
            if self._state.get("dj_mode_active") is None:
                self._state["dj_mode_active"] = False
            if self._state.get("dj_track_history") is None:
                self._state["dj_track_history"] = []
            if self._state.get("dj_next_track") is None:
                self._state["dj_next_track"] = None
            if self._state.get("chat_history") is None:
                self._state["chat_history"] = []
            if self._state.get("music_playing") is None:
                self._state["music_playing"] = False
                
            self.logger.debug("MemoryService: State initialized with defaults")
            
            # Save initial state
            self._save_state()
            
            await self._emit_status(ServiceStatus.RUNNING, "Memory service started")
            self.logger.info("MemoryService started successfully")
            
        except Exception as e:
            error_msg = f"Failed to start MemoryService: {e}"
            self.logger.error(error_msg)
            await self._emit_status(ServiceStatus.ERROR, error_msg)
            raise

    async def _stop(self) -> None:
        """Clean up tasks and subscriptions and save state."""
        # Save state before stopping
        self._save_state()

        for task in self._tasks:
            if not task.done():
                task.cancel()
                
        # Wait for tasks to complete with timeout
        if self._tasks:
            await asyncio.wait(self._tasks, timeout=5.0)
            
        self._tasks.clear()

        await self._emit_status(ServiceStatus.STOPPED, "Memory service stopped")

    # ------------------------------------------------------------------
    # Subscription setup
    # ------------------------------------------------------------------
    async def _setup_subscriptions(self) -> None:
        """Register event-handlers for memory updates."""
        self.logger.debug("MemoryService: Setting up subscriptions...")

        # Create tasks for all subscriptions
        subscription_tasks = []

        # Subscribe to essential music state events
        subscription_tasks.extend([
            asyncio.create_task(self.subscribe(EventTopics.TRACK_PLAYING, self._handle_music_started)),
            asyncio.create_task(self.subscribe(EventTopics.TRACK_STOPPED, self._handle_music_stopped)),
            asyncio.create_task(self.subscribe(EventTopics.INTENT_DETECTED, self._handle_intent_detected)),
            asyncio.create_task(self.subscribe(EventTopics.SYSTEM_MODE_CHANGE, self._handle_mode_change)),
            # Add memory access subscriptions
            asyncio.create_task(self.subscribe(EventTopics.MEMORY_GET, self._handle_memory_get)),
            asyncio.create_task(self.subscribe(EventTopics.MEMORY_SET, self._handle_memory_set))
        ])

        # Add tasks to the service's task list for cleanup
        self._tasks.extend(subscription_tasks)
        
        self.logger.debug("MemoryService: All subscription tasks created")

        # Wait for all subscriptions to complete
        try:
            await asyncio.gather(*subscription_tasks)
            self.logger.debug("MemoryService: All subscriptions completed successfully")
        except Exception as e:
            self.logger.error(f"MemoryService: Error during subscription setup: {e}")
            raise  # Re-raise to ensure service startup fails if subscriptions fail

    # ------------------------------------------------------------------
    # Memory API methods
    # ------------------------------------------------------------------
    async def set(self, key: str, value: Any) -> None:
        """Set a value in memory and emit update event."""
        if key not in self._state:
            self.logger.warning(f"Setting unknown key in memory: {key}")
        
        old_value = self._state.get(key)
        self._state[key] = value
        
        # Save state after setting a value
        self._save_state()
        
        # Emit memory updated event
        await self._emit_dict(
            EventTopics.MEMORY_UPDATED,
            MemoryUpdatedPayload(
                key=key,
                new_value=value,
                old_value=old_value
            )
        )
        
        # Check and notify any waiters
        self._check_waiters()
    
    def get(self, key: str, default=None) -> Any:
        """Get a value from memory."""
        return self._state.get(key, default)
    
    async def append_chat(self, message: Dict[str, Any]) -> None:
        """Add a message to chat history, pruning if necessary."""
        chat_history = self._state["chat_history"]
        chat_history.append(message)
        
        # Prune if exceeds max turns
        while len(chat_history) > self._config.chat_history_max_turns:
            chat_history.pop(0)
        
        # Emit memory updated
        await self._emit_dict(
            EventTopics.MEMORY_UPDATED,
            MemoryUpdatedPayload(
                key="chat_history",
                new_value=chat_history
            )
        )
        
        # Check and notify any waiters
        self._check_waiters()
    
    def get_recent_track_history(self, count: int = 10) -> List[str]:
        """Get the most recent tracks from the DJ track history.
        
        Args:
            count: The number of recent tracks to retrieve.
            
        Returns:
            A list of recent track names.
        """
        history = self._state.get("dj_track_history", [])
        return history[-count:] if history else []

    async def set_user_preference(self, key: str, value: Any) -> None:
        """Set a specific DJ user preference.
        
        Args:
            key: The key for the user preference.
            value: The value to set for the preference.
        """
        preferences = self._state.get("dj_user_preferences", {})
        preferences[key] = value
        await self.set("dj_user_preferences", preferences)
        self.logger.debug(f"DJ user preference set: {key} = {value}")

    def get_user_preference(self, key: str, default: Any = None) -> Any:
        """Get a specific DJ user preference.
        
        Args:
            key: The key for the user preference.
            default: The default value to return if the preference is not found.
            
        Returns:
            The value of the user preference, or the default if not found.
        """
        preferences = self._state.get("dj_user_preferences", {})
        return preferences.get(key, default)

    async def set_lookahead_cache_state(self, track_id: str, state: str, details: Optional[Dict[str, Any]] = None) -> None:
        """Set the state of the lookahead speech cache for a specific track.
        
        Args:
            track_id: The identifier of the track the cache is for.
            state: The state of the cache (e.g., "pending", "ready", "failed", "cleared").
            details: Optional dictionary with additional details about the cache state.
        """
        cache_state = {
            "track_id": track_id,
            "state": state,
            "details": details,
            "timestamp": time.time()
        }
        await self.set("dj_lookahead_cache", cache_state)
        self.logger.debug(f"DJ lookahead cache state updated: {cache_state}")

    def get_lookahead_cache_state(self) -> Optional[Dict[str, Any]]:
        """Get the current state of the lookahead speech cache.
        
        Returns:
            A dictionary representing the cache state, or None if not set.
        """
        return self.get("dj_lookahead_cache", None)

    async def clear_lookahead_cache_state(self) -> None:
        """Clear the lookahead speech cache state.
        """
        await self.set("dj_lookahead_cache", None)
        self.logger.debug("DJ lookahead cache state cleared.")

    async def wait_for(self, predicate: Callable[[Dict[str, Any]], bool], timeout: Optional[float] = None) -> bool:
        """Wait until a predicate function on state returns True.
        
        Args:
            predicate: A function that takes the state dict and returns True when condition is met
            timeout: Optional timeout in seconds
            
        Returns:
            True if predicate was satisfied, False if timeout occurred
        """
        # Check if predicate is already satisfied
        if predicate(self._state):
            return True
            
        # Create a waiter event
        waiter_id = str(time.time())
        event = asyncio.Event()
        
        # Store it in waiters
        if waiter_id not in self._waiters:
            self._waiters[waiter_id] = []
        self._waiters[waiter_id].append(event)
        
        try:
            # Wait for the event with optional timeout
            if timeout is not None:
                return await asyncio.wait_for(event.wait(), timeout)
            else:
                await event.wait()
                return True
        except asyncio.TimeoutError:
            return False
        finally:
            # Clean up
            if waiter_id in self._waiters:
                self._waiters[waiter_id].remove(event)
                if not self._waiters[waiter_id]:
                    del self._waiters[waiter_id]

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------
    async def _handle_music_started(self, payload: Dict[str, Any]) -> None:
        """Handle TRACK_PLAYING event."""
        try:
            await self.set("music_playing", True)
            if "track_metadata" in payload:
                await self.set("current_track", payload["track_metadata"])
        except Exception as e:
            await self._emit_status(
                ServiceStatus.ERROR,
                f"Error updating music state: {e}",
                LogLevel.ERROR
            )

    async def _handle_music_stopped(self, payload: Dict[str, Any]) -> None:
        """Handle TRACK_STOPPED event."""
        try:
            await self.set("music_playing", False)
        except Exception as e:
            await self._emit_status(
                ServiceStatus.ERROR,
                f"Error updating music state: {e}",
                LogLevel.ERROR
            )
    
    async def _handle_intent_detected(self, payload: Dict[str, Any]) -> None:
        """Handle INTENT_DETECTED event."""
        try:
            intent_payload = IntentPayload(**payload)
            await self.set("last_intent", {
                "name": intent_payload.intent_name,
                "parameters": intent_payload.parameters,
                "original_text": intent_payload.original_text
            })
            
            # Add to chat history
            await self.append_chat({
                "role": "user",
                "content": intent_payload.original_text
            })
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

    async def _handle_mode_change(self, payload: Dict[str, Any]) -> None:
        """Handle SYSTEM_MODE_CHANGE events."""
        try:
            if "new_mode" in payload:
                await self.set("mode", payload["new_mode"])
        except Exception as e:
            await self._emit_status(
                ServiceStatus.ERROR,
                f"Error updating mode state: {e}",
                LogLevel.ERROR
            )
            
    async def _handle_dj_mode_changed(self, payload: Dict[str, Any]) -> None:
        """Handle DJ_MODE_CHANGED event."""
        self.logger.debug(f"Received DJ_MODE_CHANGED event with payload: {payload}")
        try:
            # Handle direct mode change payload with dj_mode_active flag
            if isinstance(payload, dict) and "dj_mode_active" in payload:
                # Update DJ mode state
                is_active = payload["dj_mode_active"]
                await self.set("dj_mode_active", is_active)
                
                self.logger.info(f"DJ Mode state updated: {is_active}")
                
                # If mode is deactivated, clear other DJ state
                if not is_active:
                    await self.set("dj_next_track", None)
                    await self.set("dj_track_history", [])
                    await self.set("dj_transition_style", None)
            
            # Handle CLI command payload format (e.g., from "dj start" command)
            elif isinstance(payload, dict) and "command" in payload and payload["command"] == "dj":
                # Extract action from args or use default "start"
                args = payload.get("args", [])
                action = args[0] if args else "start"
                
                # Set state based on action
                is_active = (action == "start")
                await self.set("dj_mode_active", is_active)
                self.logger.info(f"DJ Mode state updated via CLI: {is_active}")
                
                # Clear other DJ state if mode is deactivated
                if not is_active:
                    await self.set("dj_next_track", None)
                    await self.set("dj_track_history", [])
                    await self.set("dj_transition_style", None)
            else:
                # Log unexpected payload structure
                self.logger.error(f"Invalid or unexpected payload structure for DJ_MODE_CHANGED: {payload}")
                await self._emit_status(
                    ServiceStatus.ERROR,
                    f"Invalid or unexpected payload structure for DJ_MODE_CHANGED: {payload}",
                    LogLevel.ERROR
                )
                
        except Exception as e:
            self.logger.error(f"Error handling DJ mode change: {e}. Payload: {payload}")
            await self._emit_status(
                ServiceStatus.ERROR,
                f"Error updating DJ mode state: {e}",
                LogLevel.ERROR
            )

    async def _handle_dj_track_queued(self, payload: Dict[str, Any]) -> None:
        """Handle DJ_TRACK_QUEUED event."""
        try:
            if isinstance(payload, dict) and "track_name" in payload:
                # Save queued track
                track_name = payload["track_name"]
                await self.set("dj_next_track", track_name)
                
                # Update track history
                history = self.get("dj_track_history", [])
                if not history:
                    history = []
                    
                # Add to track history if not already in recent history 
                if track_name not in history[-5:] if history else []:
                    history.append(track_name)
                    # Keep history at a reasonable size
                    if len(history) > 20:
                        history = history[-20:]
                    await self.set("dj_track_history", history)
                
                self.logger.info(f"Queued track for DJ mode: {track_name}")
            else:
                self.logger.error(f"Invalid payload for DJ_TRACK_QUEUED: {payload}")
                await self._emit_status(
                    ServiceStatus.ERROR,
                    f"Invalid payload for DJ_TRACK_QUEUED: {payload}",
                    LogLevel.ERROR
                )
                
        except Exception as e:
            self.logger.error(f"Error handling DJ track queued: {e}")
            await self._emit_status(
                ServiceStatus.ERROR,
                f"Error updating DJ track queue: {e}",
                LogLevel.ERROR
            )

    # ------------------------------------------------------------------
    # Helper methods
    # ------------------------------------------------------------------
    def _check_waiters(self) -> None:
        """Check all waiting predicates and set events as needed."""
        for waiter_id, events in list(self._waiters.items()):
            for event in events:
                if not event.is_set():
                    event.set()

    # ------------------------------------------------------------------
    # Direct memory access handlers
    # ------------------------------------------------------------------
    async def _handle_memory_get(self, payload: Dict[str, Any]) -> None:
        """Handle memory get requests."""
        try:
            if not isinstance(payload, dict):
                self.logger.warning(f"Invalid memory get payload type: {type(payload)}")
                return
                
            key = payload.get("key")
            callback_topic = payload.get("callback_topic")
            
            if key is None or callback_topic is None:
                self.logger.warning("Memory get payload missing key or callback_topic")
                return
                
            self.logger.debug(f"Handling memory get request for key: {key}")
            
            # Get value from state, defaulting to None if not found
            value = self._state.get(key)
            
            # Emit value back on callback topic
            await self._emit_dict(
                callback_topic,
                {
                    "key": key,
                    "value": value
                }
            )
            self.logger.debug(f"Emitted memory value for key {key}: {value}")
            
        except Exception as e:
            self.logger.error(f"Error handling memory get request: {e}")

    async def _handle_memory_set(self, payload: Dict[str, Any]) -> None:
        """Handle memory set requests."""
        try:
            if not isinstance(payload, dict):
                self.logger.warning(f"Invalid memory set payload type: {type(payload)}")
                return
                
            key = payload.get("key")
            value = payload.get("value")
            
            if key is None:
                self.logger.warning("Memory set payload missing key")
                return
                
            self.logger.debug(f"Setting memory key {key} to value: {value}")
            
            # Store old value for event
            old_value = self._state.get(key)
            
            # Update state
            self._state[key] = value
            
            # Save state to disk
            self._save_state()
            
            # Emit memory updated event
            await self._emit_dict(
                EventTopics.MEMORY_UPDATED,
                {
                    "key": key,
                    "old_value": old_value,
                    "new_value": value
                }
            )
            self.logger.debug(f"Memory key {key} updated and saved")
            
        except Exception as e:
            self.logger.error(f"Error handling memory set request: {e}")

    def _load_state(self) -> Dict[str, Any]:
        """Load state from disk, creating if not exists."""
        try:
            # Ensure data directory exists
            os.makedirs(os.path.dirname(STATE_FILE_PATH), exist_ok=True)
            
            # Try to load existing state
            if os.path.exists(STATE_FILE_PATH):
                with open(STATE_FILE_PATH, 'r') as f:
                    state = json.load(f)
                self.logger.debug(f"Loaded state from {STATE_FILE_PATH}")
                return state
                
        except Exception as e:
            self.logger.warning(f"Error loading state from {STATE_FILE_PATH}: {e}")
            
        # Return empty dict if file doesn't exist or load fails
        return {}

    def _save_state(self) -> None:
        """Save current state to disk."""
        try:
            # Ensure data directory exists
            os.makedirs(os.path.dirname(STATE_FILE_PATH), exist_ok=True)
            
            # Write state to file
            with open(STATE_FILE_PATH, 'w') as f:
                json.dump(self._state, f, indent=2)
            self.logger.debug(f"Saved state to {STATE_FILE_PATH}")
            
        except Exception as e:
            self.logger.error(f"Error saving state to {STATE_FILE_PATH}: {e}") 