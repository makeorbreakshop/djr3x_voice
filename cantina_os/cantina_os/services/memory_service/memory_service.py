"""
Memory Service for Cantina OS
================================
A service that provides working memory for DJ R3X.
Maintains state keys, chat history, and exposes an API to access and modify this data.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, Dict, List, Optional

from pydantic import BaseModel, ValidationError

from cantina_os.bus import EventTopics, LogLevel, ServiceStatus
from ..base import StandardService
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
        "dj_transition_style", "dj_user_preferences"
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
# Service Implementation
# ---------------------------------------------------------------------------
class MemoryService(StandardService):
    """Working memory service for DJ R3X.
    
    Provides access to state variables, chat history, and emits change events.
    Acts as a central state store for the system.
    """

    def __init__(self, event_bus, config=None, name="memory_service"):
        super().__init__(event_bus, config, name=name)

        # ----- validated configuration -----
        self._config = _Config(**(config or {}))

        # ----- bookkeeping -----
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tasks: List[asyncio.Task] = []
        
        # ----- memory state -----
        self._state: Dict[str, Any] = {key: None for key in self._config.state_keys}
        self._state["chat_history"] = []  # Initialize chat history as empty list
        self._state["music_playing"] = False  # Initialize music playing state
        self._state["dj_mode_active"] = False  # Initialize DJ mode state
        self._state["dj_track_history"] = []  # Initialize DJ track history
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
            self._loop = asyncio.get_running_loop()
            self._setup_subscriptions()  # Not async, don't await
            await self._emit_status(ServiceStatus.RUNNING, "Memory service started")
            self.logger.info("MemoryService started successfully")
        except Exception as e:
            error_msg = f"Failed to start MemoryService: {e}"
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

        await self._emit_status(ServiceStatus.STOPPED, "Memory service stopped")

    # ------------------------------------------------------------------
    # Subscription setup
    # ------------------------------------------------------------------
    def _setup_subscriptions(self) -> None:
        """Register event-handlers for memory updates."""
        # Subscribe to essential music state events
        task = asyncio.create_task(
            self.subscribe(EventTopics.MUSIC_PLAYBACK_STARTED, self._handle_music_started)
        )
        self._tasks.append(task)
        
        task = asyncio.create_task(
            self.subscribe(EventTopics.MUSIC_PLAYBACK_STOPPED, self._handle_music_stopped)
        )
        self._tasks.append(task)
        
        # Add subscription for INTENT_DETECTED events
        task = asyncio.create_task(
            self.subscribe(EventTopics.INTENT_DETECTED, self._handle_intent_detected)
        )
        self._tasks.append(task)
        
        # Add subscription for SYSTEM_MODE_CHANGE events
        task = asyncio.create_task(
            self.subscribe(EventTopics.SYSTEM_MODE_CHANGE, self._handle_mode_change)
        )
        self._tasks.append(task)
        
        # Add subscription for DJ mode events
        task = asyncio.create_task(
            self.subscribe(EventTopics.DJ_MODE_CHANGED, self._handle_dj_mode_changed)
        )
        self._tasks.append(task)
        
        task = asyncio.create_task(
            self.subscribe(EventTopics.DJ_TRACK_QUEUED, self._handle_dj_track_queued)
        )
        self._tasks.append(task)
        
        # Add subscriptions for direct memory access events
        task = asyncio.create_task(
            self.subscribe(EventTopics.MEMORY_GET, self._handle_memory_get)
        )
        self._tasks.append(task)
        
        task = asyncio.create_task(
            self.subscribe(EventTopics.MEMORY_SET, self._handle_memory_set)
        )
        self._tasks.append(task)

    # ------------------------------------------------------------------
    # Memory API methods
    # ------------------------------------------------------------------
    async def set(self, key: str, value: Any) -> None:
        """Set a value in memory and emit update event."""
        if key not in self._state:
            self.logger.warning(f"Setting unknown key in memory: {key}")
        
        old_value = self._state.get(key)
        self._state[key] = value
        
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
        """Handle MUSIC_PLAYBACK_STARTED event."""
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
        """Handle MUSIC_PLAYBACK_STOPPED event."""
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
        try:
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
                    
            else:
                self.logger.error(f"Invalid payload for DJ_MODE_CHANGED: {payload}")
                await self._emit_status(
                    ServiceStatus.ERROR,
                    f"Invalid payload for DJ_MODE_CHANGED: {payload}",
                    LogLevel.ERROR
                )
                
        except Exception as e:
            self.logger.error(f"Error handling DJ mode change: {e}")
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
        """Handle a request to get a memory value.
        
        Args:
            payload: Dict with "key" and optional "callback_topic"
        """
        try:
            if not isinstance(payload, dict) or "key" not in payload:
                self.logger.error("Invalid memory get request: missing key")
                await self._emit_status(
                    ServiceStatus.ERROR,
                    "Invalid memory get request: missing key",
                    LogLevel.ERROR
                )
                return
                
            key = payload.get("key")
            value = self.get(key)
            
            # Determine where to send the response
            callback_topic = payload.get("callback_topic", EventTopics.MEMORY_VALUE)
            
            # Emit the value
            await self._emit_dict(
                callback_topic,
                {
                    "key": key,
                    "value": value,
                    "request_id": payload.get("request_id")  # Pass back any request ID
                }
            )
            
            self.logger.debug(f"Memory get: {key} -> {value}")
            
        except Exception as e:
            self.logger.error(f"Error handling memory get request: {e}")
            await self._emit_status(
                ServiceStatus.ERROR,
                f"Error handling memory get request: {e}",
                LogLevel.ERROR
            )
            
    async def _handle_memory_set(self, payload: Dict[str, Any]) -> None:
        """Handle a request to set a memory value.
        
        Args:
            payload: Dict with "key" and "value"
        """
        try:
            if not isinstance(payload, dict) or "key" not in payload or "value" not in payload:
                self.logger.error("Invalid memory set request: missing key or value")
                await self._emit_status(
                    ServiceStatus.ERROR,
                    "Invalid memory set request: missing key or value",
                    LogLevel.ERROR
                )
                return
                
            key = payload.get("key")
            value = payload.get("value")
            
            # Use the regular set method to update the value
            await self.set(key, value)
            
            self.logger.debug(f"Memory set: {key} = {value}")
            
        except Exception as e:
            self.logger.error(f"Error handling memory set request: {e}")
            await self._emit_status(
                ServiceStatus.ERROR,
                f"Error handling memory set request: {e}",
                LogLevel.ERROR
            ) 