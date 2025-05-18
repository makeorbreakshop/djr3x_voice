"""
Timeline Executor Service for Cantina OS
================================
A layered cue engine that runs Plans, handles audio ducking, 
timing, and manages multiple concurrent timelines across three layers:
ambient, foreground, and override.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Set

from pydantic import BaseModel, ValidationError

from cantina_os.bus import EventTopics, LogLevel, ServiceStatus
from ..base import StandardService
from cantina_os.event_payloads import (
    PlanPayload,
    PlanStartedPayload,
    StepReadyPayload,
    StepExecutedPayload,
    PlanEndedPayload,
    MusicCommandPayload,
    SpeechGenerationRequestPayload
)

# ---------------------------------------------------------------------------
# Configuration model
# ---------------------------------------------------------------------------
class _Config(BaseModel):
    """Pydantic‑validated configuration for the timeline executor service."""
    default_ducking_level: float = 0.3  # Default ducking level (0.0-1.0)
    ducking_fade_ms: int = 300  # Fade time in ms for ducking
    speech_wait_timeout: float = 10.0  # Timeout for waiting for speech to complete (was 5.0)
    layer_priorities: Dict[str, int] = {
        "ambient": 0,     # Lowest priority
        "foreground": 1,  # User-initiated content
        "override": 2     # System messages, critical alerts
    }


# ---------------------------------------------------------------------------
# Service Implementation
# ---------------------------------------------------------------------------
class TimelineExecutorService(StandardService):
    """Timeline Executor Service for DJ R3X.
    
    Handles layered execution of plans, audio coordination, and timing.
    """

    def __init__(self, event_bus, config=None, name="timeline_executor_service"):
        super().__init__(event_bus, config, name=name)
        
        # ----- validated configuration -----
        self._config = _Config(**(config or {}))
        
        # ----- bookkeeping -----
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tasks: List[asyncio.Task] = []
        
        # ----- timeline state -----
        self._ambient_timelines: Dict[str, asyncio.Task] = {}  # Background/ambient timelines
        self._foreground_timelines: Dict[str, asyncio.Task] = {}  # User-driven timelines
        self._override_timelines: Dict[str, asyncio.Task] = {}  # System/critical timelines
        self._timeline_layers: Dict[str, str] = {}  # Maps timeline_id -> layer
        self._paused_timelines: Dict[str, PlanPayload] = {}  # For resuming after interrupts
        
        # Also initialize the dictionaries used in the _run_plan and other methods
        self._layer_tasks: Dict[str, asyncio.Task] = {}  # Tasks running plans on each layer
        self._active_plans: Dict[str, PlanPayload] = {}  # Currently active plans by ID
        self._layer_events: Dict[str, asyncio.Event] = {}  # Events for pausing/resuming layers
        self._speech_end_events: Dict[str, asyncio.Event] = {}  # Events for speech completion
        
        # Initialize layer events
        for layer in self._config.layer_priorities:
            self._layer_events[layer] = asyncio.Event()
            self._layer_events[layer].set()  # Layers start unpaused
        
        # ----- audio state -----
        self._audio_ducked: bool = False
        self._current_music_playing: bool = False

    # ------------------------------------------------------------------
    # Helper methods
    # ------------------------------------------------------------------
    async def _emit_dict(self, topic: EventTopics, payload: BaseEventPayload) -> None:
        """Emit a Pydantic model as a dictionary to the event bus.
        
        Args:
            topic: Event topic
            payload: Pydantic model to emit
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
        """Initialize timeline service and set up subscriptions."""
        try:
            self._loop = asyncio.get_running_loop()
            self._setup_subscriptions()  # Not async, don't await
            await self._emit_status(ServiceStatus.RUNNING, "Timeline execution service started")
            self.logger.info("TimelineExecutorService started successfully")
        except Exception as e:
            error_msg = f"Failed to start TimelineExecutorService: {e}"
            self.logger.error(error_msg)
            await self._emit_status(ServiceStatus.ERROR, error_msg)
            raise

    async def _stop(self) -> None:
        """Clean up tasks and subscriptions."""
        # Cancel all running timelines
        for timeline_id, task in self._ambient_timelines.items():
            if not task.done():
                task.cancel()
                
        for timeline_id, task in self._foreground_timelines.items():
            if not task.done():
                task.cancel()
                
        for timeline_id, task in self._override_timelines.items():
            if not task.done():
                task.cancel()
        
        # Cancel all other tasks
        for task in self._tasks:
            if not task.done():
                task.cancel()
                
        # Wait for tasks to complete with timeout
        all_tasks = self._tasks + list(self._ambient_timelines.values()) + \
                   list(self._foreground_timelines.values()) + \
                   list(self._override_timelines.values())
        
        if all_tasks:
            await asyncio.wait(all_tasks, timeout=5.0)
            
        self._tasks.clear()
        self._ambient_timelines.clear()
        self._foreground_timelines.clear()
        self._override_timelines.clear()
        self._timeline_layers.clear()
        self._paused_timelines.clear()

        await self._emit_status(ServiceStatus.STOPPED, "Timeline execution service stopped")

    # ------------------------------------------------------------------
    # Subscription setup
    # ------------------------------------------------------------------
    def _setup_subscriptions(self) -> None:
        """Register event-handlers for timeline execution."""
        # Subscribe to timeline management events
        task = asyncio.create_task(
            self.subscribe(EventTopics.PLAN_READY, self._handle_plan_ready)
        )
        self._tasks.append(task)
        
        # Listen for music state changes for coordination
        task = asyncio.create_task(
            self.subscribe(EventTopics.MUSIC_PLAYBACK_STARTED, self._handle_music_started)
        )
        self._tasks.append(task)
        
        task = asyncio.create_task(
            self.subscribe(EventTopics.MUSIC_PLAYBACK_STOPPED, self._handle_music_stopped)
        )
        self._tasks.append(task)
        
        # Subscribe to memory updates for state coordination
        task = asyncio.create_task(
            self.subscribe(EventTopics.MEMORY_UPDATED, self._handle_memory_updated)
        )
        self._tasks.append(task)
        
        # Subscribe to speech synthesis completion for audio unduck
        task = asyncio.create_task(
            self.subscribe(EventTopics.SPEECH_GENERATION_COMPLETE, self._handle_speech_generation_complete)
        )
        self._tasks.append(task)
        
        # Subscribe to speech synthesis ended events for plan coordination
        task = asyncio.create_task(
            self.subscribe(EventTopics.SPEECH_SYNTHESIS_ENDED, self._handle_speech_ended)
        )
        self._tasks.append(task)
        
        # Subscribe to direct LLM responses for automatic speech handling
        task = asyncio.create_task(
            self.subscribe(EventTopics.LLM_RESPONSE, self._handle_llm_response)
        )
        self._tasks.append(task)
        
        # Subscribe to voice recording events to duck during microphone activity
        task = asyncio.create_task(
            self.subscribe(EventTopics.VOICE_LISTENING_STARTED, self._handle_voice_listening_started)
        )
        self._tasks.append(task)
        
        task = asyncio.create_task(
            self.subscribe(EventTopics.VOICE_LISTENING_STOPPED, self._handle_voice_listening_stopped)
        )
        self._tasks.append(task)

    # ------------------------------------------------------------------
    # Plan handling
    # ------------------------------------------------------------------
    async def _handle_plan_ready(self, payload: Dict[str, Any]) -> None:
        """Handle PLAN_READY events from Brain service.
        
        This is the entry point for executing plans on different layers.
        """
        try:
            plan = PlanPayload(**payload)
            layer = plan.layer
            
            # Check if this layer is valid
            if layer not in self._config.layer_priorities:
                raise ValueError(f"Unknown layer: {layer}")
            
            # Check for existing plan on this layer and manage layer priorities
            if layer in self._layer_tasks and not self._layer_tasks[layer].done():
                await self._emit_status(
                    ServiceStatus.RUNNING, 
                    f"Cancelling existing plan on layer {layer}", 
                    LogLevel.INFO
                )
                self._layer_tasks[layer].cancel()
                
            # Handle layer priority
            if layer == "override":
                await self._cancel_lower_priority_layers(layer)
            elif layer == "foreground":
                await self._pause_lower_priority_layers(layer)
            
            # Start the new plan
            self._active_plans[plan.plan_id] = plan
            task = asyncio.create_task(self._run_plan(plan))
            self._layer_tasks[layer] = task
            self._tasks.append(task)
            
            # Emit plan started event
            await self._emit_dict(
                EventTopics.PLAN_STARTED,
                PlanStartedPayload(
                    plan_id=plan.plan_id,
                    layer=layer
                )
            )
        except ValidationError as e:
            await self._emit_status(
                ServiceStatus.ERROR,
                f"Invalid plan payload: {e}",
                LogLevel.ERROR
            )
        except Exception as e:
            await self._emit_status(
                ServiceStatus.ERROR,
                f"Error handling plan: {e}",
                LogLevel.ERROR
            )

    async def _run_plan(self, plan: PlanPayload) -> None:
        """Execute a plan by running each step in sequence.
        
        Handles waiting for events, delays, and executing step actions.
        """
        try:
            # Reset the layer event in case it was paused
            layer_event = self._layer_events[plan.layer]
            layer_event.set()
            
            for step in plan.steps:
                # Wait for any specified delay
                if step.delay and step.delay > 0:
                    await asyncio.sleep(step.delay)
                
                # Wait for any specified event
                if step.event:
                    # TODO: Implement event waiting
                    pass
                
                # Check if layer is paused (for ambient layer when foreground is active)
                await layer_event.wait()
                
                # Emit step ready event
                await self._emit_dict(
                    EventTopics.STEP_READY,
                    StepReadyPayload(
                        plan_id=plan.plan_id,
                        step_id=step.id
                    )
                )
                
                # Execute the step
                success, details = await self._execute_step(step, plan.plan_id)
                
                # Emit step executed event
                await self._emit_dict(
                    EventTopics.STEP_EXECUTED,
                    StepExecutedPayload(
                        plan_id=plan.plan_id,
                        step_id=step.id,
                        status="success" if success else "failure",
                        details=details
                    )
                )
                
                # If step failed, stop the plan
                if not success:
                    break
            
            # Plan completed successfully
            await self._emit_dict(
                EventTopics.PLAN_ENDED,
                PlanEndedPayload(
                    plan_id=plan.plan_id,
                    layer=plan.layer,
                    status="completed"
                )
            )
            
            # If this was a foreground plan, resume ambient layer
            if plan.layer == "foreground":
                await self._resume_layer("ambient")
                
            # Remove from active plans
            if plan.plan_id in self._active_plans:
                del self._active_plans[plan.plan_id]
                
        except asyncio.CancelledError:
            # Plan was cancelled
            await self._emit_dict(
                EventTopics.PLAN_ENDED,
                PlanEndedPayload(
                    plan_id=plan.plan_id,
                    layer=plan.layer,
                    status="cancelled"
                )
            )
            
            # Remove from active plans
            if plan.plan_id in self._active_plans:
                del self._active_plans[plan.plan_id]
                
            # Re-raise so asyncio knows it was cancelled
            raise
        except Exception as e:
            # Plan failed with error
            await self._emit_status(
                ServiceStatus.ERROR,
                f"Error executing plan {plan.plan_id}: {e}",
                LogLevel.ERROR
            )
            
            # Emit plan ended with failure
            await self._emit_dict(
                EventTopics.PLAN_ENDED,
                PlanEndedPayload(
                    plan_id=plan.plan_id,
                    layer=plan.layer,
                    status="failed"
                )
            )
            
            # Remove from active plans
            if plan.plan_id in self._active_plans:
                del self._active_plans[plan.plan_id]

    # ------------------------------------------------------------------
    # Step execution
    # ------------------------------------------------------------------
    async def _execute_step(self, step: PlanStep, plan_id: str) -> tuple[bool, Optional[Dict[str, Any]]]:
        """Execute a single step in a plan.
        
        Returns:
            Tuple of (success, details) where details is optional context for the step execution
        """
        try:
            if step.type == "speak":
                return await self._execute_speak_step(step, plan_id)
            elif step.type == "play_music":
                return await self._execute_play_music_step(step)
            elif step.type == "eye_pattern":
                return await self._execute_eye_pattern_step(step)
            elif step.type == "move":
                return await self._execute_move_step(step)
            elif step.type == "delay":
                # Delays are handled at the plan level
                return True, {"message": "Delay completed"}
            elif step.type == "wait_for_event":
                # Event waits are handled at the plan level
                return True, {"message": "Event wait completed"}
            else:
                await self._emit_status(
                    ServiceStatus.ERROR,
                    f"Unknown step type: {step.type}",
                    LogLevel.ERROR
                )
                return False, {"error": f"Unknown step type: {step.type}"}
        except Exception as e:
            await self._emit_status(
                ServiceStatus.ERROR,
                f"Error executing step {step.id} in plan {plan_id}: {e}",
                LogLevel.ERROR
            )
            return False, {"error": str(e)}

    async def _execute_speak_step(self, step: PlanStep, plan_id: str) -> tuple[bool, Dict[str, Any]]:
        """Execute a speak step with audio ducking.
        
        Sequence: duck audio → generate TTS → wait for speech to end → unduck audio
        """
        # Create an event for speech completion
        speech_event = asyncio.Event()
        speech_id = step.id if step.id else str(uuid.uuid4())
        self._speech_end_events[speech_id] = speech_event
        
        try:
            # Start audio ducking if music is playing
            if self._current_music_playing:
                self.logger.info(f"Ducking audio for speech step '{step.id}'")
                await self._emit_dict(
                    EventTopics.AUDIO_DUCKING_START,
                    {"level": self._config.default_ducking_level, "fade_ms": self._config.ducking_fade_ms}
                )
                self._audio_ducked = True
                
                # Small delay to ensure ducking has started
                await asyncio.sleep(0.15)
            
            # Request TTS generation
            self.logger.info(f"Generating speech for step '{step.id}': '{step.text[:30]}...'")
            await self._emit_dict(
                EventTopics.TTS_GENERATE_REQUEST,
                SpeechGenerationRequestPayload(
                    text=step.text,
                    clip_id=speech_id,
                    step_id=step.id,
                    plan_id=plan_id,
                    conversation_id=None  # Add conversation_id parameter
                )
            )
            
            # Wait for speech synthesis to complete with timeout
            try:
                self.logger.info(f"Waiting for speech synthesis to complete (timeout: {self._config.speech_wait_timeout}s)")
                await asyncio.wait_for(
                    speech_event.wait(),
                    timeout=self._config.speech_wait_timeout
                )
                speech_success = True
                self.logger.info(f"Speech synthesis completed successfully for step '{step.id}'")
            except asyncio.TimeoutError:
                speech_success = False
                self.logger.error(f"Timeout waiting for speech synthesis to complete for step {step.id}")
                await self._emit_status(
                    ServiceStatus.ERROR,
                    f"Timeout waiting for speech synthesis to complete for step {step.id}",
                    LogLevel.ERROR
                )
                # Force progress even on timeout
                speech_success = True  # We'll continue with the plan despite the timeout
            
            # Add a small delay to ensure speech playback has finished
            await asyncio.sleep(0.25)
            
            # Note: We don't unduck audio here - that's now handled by the _handle_speech_generation_complete method
            # which responds to the SPEECH_GENERATION_COMPLETE event from ElevenLabsService
            
            return speech_success, {"text": step.text, "speech_id": speech_id}
            
        except Exception as e:
            self.logger.error(f"Error in speech step execution: {e}")
            
            # Ensure we stop ducking even if there's an error
            if self._current_music_playing and self._audio_ducked:
                try:
                    await self._emit_dict(
                        EventTopics.AUDIO_DUCKING_STOP,
                        {"fade_ms": self._config.ducking_fade_ms}
                    )
                    self._audio_ducked = False
                except Exception as ducking_error:
                    self.logger.error(f"Error stopping ducking after speech error: {ducking_error}")
                    
            return False, {"error": str(e)}
            
        finally:
            # Clean up the speech event
            if speech_id in self._speech_end_events:
                del self._speech_end_events[speech_id]

    async def _execute_play_music_step(self, step: PlanStep) -> tuple[bool, Dict[str, Any]]:
        """Execute a play_music step."""
        # Emit music command
        await self._emit_dict(
            EventTopics.MUSIC_COMMAND,
            {
                "action": "play",
                "song_query": step.genre or "",
                "source": "voice",  # Indicate this is from voice command through timeline
                "conversation_id": step.conversation_id if hasattr(step, "conversation_id") else None
            }
        )
        return True, {"genre": step.genre}

    async def _execute_eye_pattern_step(self, step: PlanStep) -> tuple[bool, Dict[str, Any]]:
        """Execute an eye_pattern step."""
        # Emit eye command
        await self._emit_dict(
            EventTopics.EYE_COMMAND,
            {
                "pattern": step.pattern or "default",
                "color": "#FFFFFF"  # Default color
            }
        )
        return True, {"pattern": step.pattern}

    async def _execute_move_step(self, step: PlanStep) -> tuple[bool, Dict[str, Any]]:
        """Execute a move step."""
        # Would normally trigger motion service
        # Placeholder implementation
        return True, {"motion": step.motion}

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------
    async def _handle_speech_ended(self, payload: Dict[str, Any]) -> None:
        """Handle SPEECH_SYNTHESIS_ENDED events.
        
        Sets the relevant speech event to unblock waiting speak steps.
        """
        try:
            # Check if we have a clip_id or step_id to match
            clip_id = payload.get("clip_id", "")
            step_id = payload.get("step_id", "")
            
            self.logger.info(f"Received speech ended event for clip_id={clip_id}, step_id={step_id}")
            
            # First try to match by clip_id
            if clip_id and clip_id in self._speech_end_events:
                self.logger.info(f"Setting speech event for clip_id={clip_id}")
                self._speech_end_events[clip_id].set()
                return
                
            # Then try to match by step_id
            if step_id and step_id in self._speech_end_events:
                self.logger.info(f"Setting speech event for step_id={step_id}")
                self._speech_end_events[step_id].set()
                return
                
            # No exact match, try to find any waiting steps
            if self._speech_end_events:
                # This is a fallback for when the IDs don't match but we need to advance
                # Consider enabling this only in development mode
                waiting_id = next(iter(self._speech_end_events))
                self.logger.warning(f"No exact match for speech end event; using fallback id={waiting_id}")
                self._speech_end_events[waiting_id].set()
            else:
                self.logger.warning(f"Received speech ended event but no waiting steps found")
                
        except Exception as e:
            await self._emit_status(
                ServiceStatus.ERROR,
                f"Error handling speech ended: {e}",
                LogLevel.ERROR
            )

    async def _handle_music_started(self, payload: Dict[str, Any]) -> None:
        """Handle MUSIC_PLAYBACK_STARTED events.
        
        Updates internal state to track when music is playing.
        """
        self._current_music_playing = True
        await self._emit_status(
            ServiceStatus.RUNNING,
            "Music playback started - timeline coordinator informed",
            LogLevel.INFO
        )
    
    async def _handle_music_stopped(self, payload: Dict[str, Any]) -> None:
        """Handle MUSIC_PLAYBACK_STOPPED events.
        
        Updates internal state to track when music is stopped.
        """
        self._current_music_playing = False
        await self._emit_status(
            ServiceStatus.RUNNING,
            "Music playback stopped - timeline coordinator informed",
            LogLevel.INFO
        )
    
    async def _handle_memory_updated(self, payload: Dict[str, Any]) -> None:
        """Handle MEMORY_UPDATED events.
        
        Updates internal state based on memory changes.
        """
        # Not implemented in the initial version
        pass

    # ------------------------------------------------------------------
    # Add new handler for speech generation complete
    # ------------------------------------------------------------------
    async def _handle_speech_generation_complete(self, payload: Dict[str, Any]) -> None:
        """Handle SPEECH_GENERATION_COMPLETE events from ElevenLabsService.
        
        This ensures audio is unducked after speech playback completes,
        for both plan-based speech and direct LLM responses.
        """
        self.logger.info(f"Received speech generation complete event")
        
        # If we have music playing and it's currently ducked, restore it
        if self._current_music_playing and self._audio_ducked:
            self.logger.info("Unducking audio after speech playback complete")
            await self._emit_dict(
                EventTopics.AUDIO_DUCKING_STOP,
                {"fade_ms": self._config.ducking_fade_ms}
            )
            self._audio_ducked = False

    # ------------------------------------------------------------------
    # Layer management
    # ------------------------------------------------------------------
    async def _cancel_lower_priority_layers(self, layer: str) -> None:
        """Cancel all layers with lower priority than the given layer."""
        current_priority = self._config.layer_priorities.get(layer, 0)
        
        for other_layer, priority in self._config.layer_priorities.items():
            if priority < current_priority and other_layer in self._layer_tasks:
                task = self._layer_tasks[other_layer]
                if not task.done():
                    task.cancel()
                    await self._emit_status(
                        ServiceStatus.RUNNING,
                        f"Cancelling {other_layer} layer due to {layer} priority",
                        LogLevel.INFO
                    )

    async def _pause_lower_priority_layers(self, layer: str) -> None:
        """Pause all layers with lower priority than the given layer."""
        current_priority = self._config.layer_priorities.get(layer, 0)
        
        for other_layer, priority in self._config.layer_priorities.items():
            if priority < current_priority:
                # Clear the event to pause the layer
                if other_layer in self._layer_events:
                    self._layer_events[other_layer].clear()
                    
                    # Emit plan paused event for any active plans on this layer
                    for plan_id, plan in self._active_plans.items():
                        if plan.layer == other_layer:
                            await self._emit_dict(
                                EventTopics.PLAN_ENDED,
                                PlanEndedPayload(
                                    plan_id=plan_id,
                                    layer=other_layer,
                                    status="paused"
                                )
                            )
                    
                    await self._emit_status(
                        ServiceStatus.RUNNING,
                        f"Pausing {other_layer} layer due to {layer} priority",
                        LogLevel.INFO
                    )

    async def _resume_layer(self, layer: str) -> None:
        """Resume a paused layer."""
        if layer in self._layer_events:
            self._layer_events[layer].set()
            
            # Log resumption
            await self._emit_status(
                ServiceStatus.RUNNING,
                f"Resuming {layer} layer",
                LogLevel.INFO
            )

    # ------------------------------------------------------------------
    # Add new handler for LLM responses (direct speech)
    # ------------------------------------------------------------------
    async def _handle_llm_response(self, payload: Dict[str, Any]) -> None:
        """Handle direct LLM_RESPONSE events.
        
        Treats direct speech responses as automatic foreground activities
        with appropriate audio ducking.
        """
        # Only duck if this is a direct speech response (not a filler)
        response_type = payload.get("response_type", "")
        if response_type == "filler":
            self.logger.info("Received filler response, not ducking audio")
            return
        
        # Only duck if we have music playing and aren't already ducked
        if self._current_music_playing and not self._audio_ducked:
            self.logger.info("Ducking audio for direct LLM response")
            await self._emit_dict(
                EventTopics.AUDIO_DUCKING_START,
                {"level": self._config.default_ducking_level, "fade_ms": self._config.ducking_fade_ms}
            )
            self._audio_ducked = True

    # ------------------------------------------------------------------
    # Add handlers for voice activity
    # ------------------------------------------------------------------
    async def _handle_voice_listening_started(self, payload: Dict[str, Any]) -> None:
        """Handle VOICE_LISTENING_STARTED events.
        
        Ducks audio while microphone is active.
        """
        if self._current_music_playing and not self._audio_ducked:
            self.logger.info("Ducking audio for microphone activity")
            await self._emit_dict(
                EventTopics.AUDIO_DUCKING_START,
                {"level": self._config.default_ducking_level, "fade_ms": self._config.ducking_fade_ms}
            )
            self._audio_ducked = True

    async def _handle_voice_listening_stopped(self, payload: Dict[str, Any]) -> None:
        """Handle VOICE_LISTENING_STOPPED events.
        
        Note: We don't unduck here as speech response may follow.
        The _handle_speech_generation_complete will handle unducking
        after any response speech is complete.
        """
        # We intentionally don't unduck here since a speech response might follow
        # The full conversation flow is: mic → LLM response → unduck
        self.logger.info("Voice recording stopped, maintaining audio duck for potential response") 