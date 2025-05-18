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
from cantina_os.services.base import StandardService
from cantina_os.event_payloads import BaseEventPayload, IntentPayload

# ---------------------------------------------------------------------------
# Configuration model
# ---------------------------------------------------------------------------
class _Config(BaseModel):
    """Pydantic‑validated configuration for the memory service."""
    chat_history_max_turns: int = 10
    state_keys: List[str] = [
        "mode", "music_playing", "current_track", 
        "last_intent", "chat_history"
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

    def __init__(
        self,
        *,
        name: str = "memory_service",
        config: Dict[str, Any] | None = None,
    ) -> None:
        super().__init__(name=name)

        # ----- validated configuration -----
        self._config = _Config(**(config or {}))

        # ----- bookkeeping -----
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tasks: List[asyncio.Task] = []
        self._subs: List[tuple[str, Callable]] = []
        
        # ----- memory state -----
        self._state: Dict[str, Any] = {key: None for key in self._config.state_keys}
        self._state["chat_history"] = []  # Initialize chat history as empty list
        self._state["music_playing"] = False  # Initialize music playing state
        self._waiters: Dict[str, List[asyncio.Event]] = {}  # For wait_for predicate functionality

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def _start(self) -> None:
        """Initialize memory service and set up subscriptions."""
        self._loop = asyncio.get_running_loop()
        await self._setup_subscriptions()
        await self._emit_status(ServiceStatus.OK, "Memory service started")

    async def _stop(self) -> None:
        """Clean up tasks and subscriptions."""
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

        for topic, handler in self._subs:
            self.unsubscribe(topic, handler)
        self._subs.clear()

        await self._emit_status(ServiceStatus.OK, "Memory service stopped")

    # ------------------------------------------------------------------
    # Subscription setup
    # ------------------------------------------------------------------
    async def _setup_subscriptions(self) -> None:
        """Register event-handlers for memory updates."""
        await super()._setup_subscriptions()
        
        # Subscribe to music events
        await self._subscribe(EventTopics.MUSIC_PLAYBACK_STARTED, self._handle_music_started)
        await self._subscribe(EventTopics.MUSIC_PLAYBACK_STOPPED, self._handle_music_stopped)
        
        # Subscribe to intent events
        await self._subscribe(EventTopics.INTENT_DETECTED, self._handle_intent_detected)
        
        # Subscribe to system mode changes
        await self._subscribe(EventTopics.SYSTEM_MODE_CHANGE, self._handle_mode_change)

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
            
    # ------------------------------------------------------------------
    # Helper methods
    # ------------------------------------------------------------------
    def _check_waiters(self) -> None:
        """Check all waiting predicates and set events as needed."""
        for waiter_id, events in list(self._waiters.items()):
            for event in events:
                if not event.is_set():
                    event.set() 