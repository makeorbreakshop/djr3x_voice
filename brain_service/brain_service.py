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
from cantina_os.services.base import StandardService
from cantina_os.event_payloads import (
    IntentPayload, 
    PlanPayload,
    PlanStep,
    MusicCommandPayload,
    LLMResponsePayload
)

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
    
    Handles the Three-Call GPT pattern:
    1. Tool call (`play_music`)
    2. Filler line (handled by GPT service)
    3. Track intro after music starts
    
    Generates plans for the timeline executor and manages working memory.
    """

    def __init__(
        self,
        *,
        name: str = "brain_service",
        config: Dict[str, Any] | None = None,
    ) -> None:
        super().__init__(name=name)

        # ----- validated configuration -----
        self._config = _Config(**(config or {}))

        # ----- bookkeeping -----
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tasks: List[asyncio.Task] = []
        self._subs: List[tuple[str, Callable]] = []
        
        # ----- brain state -----
        self._current_intent: Optional[IntentPayload] = None
        self._last_track_meta: Optional[Dict[str, Any]] = None
        self._handled_intents: Set[str] = set(self._config.handled_intents)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def _start(self) -> None:
        """Initialize brain service and set up subscriptions."""
        self._loop = asyncio.get_running_loop()
        await self._setup_subscriptions()
        await self._emit_status(ServiceStatus.OK, "Brain service started")

    async def _stop(self) -> None:
        """Clean up tasks and subscriptions."""
        for task in self._tasks:
            if not task.done():
                task.cancel()
                
        # Wait for tasks to complete with timeout
        if self._tasks:
            await asyncio.wait(self._tasks, timeout=5.0)
            
        self._tasks.clear()

        for topic, handler in self._subs:
            self.unsubscribe(topic, handler)
        self._subs.clear()

        await self._emit_status(ServiceStatus.OK, "Brain service stopped")

    # ------------------------------------------------------------------
    # Subscription setup
    # ------------------------------------------------------------------
    async def _setup_subscriptions(self) -> None:
        """Register event-handlers for brain processing."""
        await super()._setup_subscriptions()
        
        # Create tasks for subscriptions to avoid blocking
        asyncio.create_task(
            self._subscribe(EventTopics.INTENT_DETECTED, self._handle_intent_detected)
        )
        asyncio.create_task(
            self._subscribe(EventTopics.MUSIC_PLAYBACK_STARTED, self._handle_music_started)
        )
        asyncio.create_task(
            self._subscribe(EventTopics.LLM_RESPONSE, self._handle_llm_response)
        )
        asyncio.create_task(
            self._subscribe(EventTopics.MEMORY_UPDATED, self._handle_memory_updated)
        )

    # ------------------------------------------------------------------
    # Intent handling
    # ------------------------------------------------------------------
    async def _handle_intent_detected(self, payload: Dict[str, Any]) -> None:
        """Handle INTENT_DETECTED events from GPT service.
        
        This is the first step in the Three-Call pattern.
        For play_music intents, we immediately forward the command to the music controller.
        """
        try:
            intent_payload = IntentPayload(**payload)
            intent_name = intent_payload.intent_name
            
            # Only handle intents we care about
            if intent_name not in self._handled_intents:
                return
                
            # Store current intent for later
            self._current_intent = intent_payload
            
            # For play_music, immediately forward command to music controller
            if intent_name == "play_music":
                await self._emit_dict(
                    EventTopics.MUSIC_COMMAND,
                    MusicCommandPayload(
                        action="play", 
                        song_query=intent_payload.parameters.get("genre"),
                    )
                )
            
            # Mark intent as consumed
            await self._emit_dict(
                EventTopics.INTENT_CONSUMED,
                {"intent_id": intent_payload.event_id}
            )
            
            # Store intent in memory
            task = asyncio.create_task(self._update_memory_with_intent(intent_payload))
            self._tasks.append(task)
            
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
        
        This triggers the third step in the Three-Call pattern:
        generate a track intro line and create a plan for it.
        """
        try:
            # Store track metadata
            self._last_track_meta = payload.get("track_metadata", {})
            
            # Generate track intro as a plan
            task = asyncio.create_task(
                self._make_track_intro_plan(self._last_track_meta)
            )
            self._tasks.append(task)
        except Exception as e:
            await self._emit_status(
                ServiceStatus.ERROR,
                f"Error handling music started: {e}",
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
    
    async def _update_memory_with_intent(self, intent: IntentPayload) -> None:
        """Update memory with the latest intent information."""
        # In a real implementation, this would call the Memory service's API
        # For now, we'll just emit an event that MemoryService listens to
        pass

    # ------------------------------------------------------------------
    # Plan generation
    # ------------------------------------------------------------------
    async def _make_track_intro_plan(self, track_meta: Dict[str, Any]) -> None:
        """Generate a track intro plan based on the current track metadata.
        
        This is the third step in the Three-Call pattern.
        """
        try:
            # In a real implementation, we would call GPT directly
            # For this implementation, we'll pretend to generate a track intro
            
            # Create a simple track intro based on metadata
            track_name = track_meta.get("title", "this funky track")
            artist = track_meta.get("artist", "the DJ")
            
            intro_text = f"Now playing {track_name} by {artist}. Let's groove!"
            
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
        except Exception as e:
            await self._emit_status(
                ServiceStatus.ERROR,
                f"Error creating track intro plan: {e}",
                LogLevel.ERROR
            ) 