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
from typing import Any, Callable, Dict, List, Optional, Set, Union

from pydantic import BaseModel, ValidationError

from cantina_os.bus import EventTopics, LogLevel, ServiceStatus
from cantina_os.base_service import BaseService
from cantina_os.core.event_schemas import (
    PlanReadyPayload,
    DjTransitionPlanPayload,
    PlayCachedSpeechStep,
    MusicCrossfadeStep,
    SpeechCachePlaybackRequestPayload, # For playing cached speech
    BasePlanStep, # Base for new step types
    EventPayload, # Base for PlanReadyPayload
    TrackDataPayload,
    TrackEndingSoonPayload,
    CrossfadeCompletePayload # Assuming this will be defined or updated
)
from cantina_os.event_payloads import (
    PlanStartedPayload,
    StepReadyPayload,
    StepExecutedPayload,
    PlanEndedPayload,
    MusicCommandPayload,
    SpeechGenerationRequestPayload, # Keep for legacy speak step
    SpeechGenerationCompletePayload, # Keep for legacy speak step
    BaseEventPayload, # Base for old payloads
    SpeechCachePlaybackCompletedPayload, # Added for new completion event
)
from cantina_os.models.music_models import MusicTrack, MusicLibrary

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
class TimelineExecutorService(BaseService):
    """Timeline Executor Service for DJ R3X.
    
    Handles layered execution of plans, audio coordination, and timing.
    """

    def __init__(self, event_bus, config=None, name="timeline_executor_service"):
        super().__init__(service_name=name, event_bus=event_bus)
        
        # ----- validated configuration -----
        self._config = _Config(**(config or {}))
        
        # ----- bookkeeping -----
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tasks: List[asyncio.Task] = []
        
        # ----- timeline state -----
        self._layer_tasks: Dict[str, asyncio.Task] = {}  # Tasks running plans on each layer
        self._active_plans: Dict[str, Union[PlanPayload, DjTransitionPlanPayload]] = {}  # Currently active plans by ID
        self._timeline_layers: Dict[str, str] = {}  # Maps plan_id -> layer (Legacy? Or needed for paused plans?)
        self._paused_timelines: Dict[str, Union[PlanPayload, DjTransitionPlanPayload]] = {}  # For resuming after interrupts
        
        self._layer_events: Dict[str, asyncio.Event] = {}  # Events for pausing/resuming layers
        self._speech_end_events: Dict[str, asyncio.Event] = {}  # Events for speech completion (Legacy?)
        self._cached_speech_playback_events: Dict[str, asyncio.Event] = {} # Events for cached speech playback completion
        self._crossfade_complete_events: Dict[str, asyncio.Event] = {} # Events for music crossfade completion
        
        # Initialize layer events
        for layer in self._config.layer_priorities:
            self._layer_events[layer] = asyncio.Event()
            self._layer_events[layer].set()  # Layers start unpaused
        
        # ----- audio state -----
        self._audio_ducked: bool = False
        self._current_music_playing: bool = False
        self._active_speech_playbacks: Set[str] = set() # Track active cached speech playback IDs

    # ------------------------------------------------------------------
    # Helper methods
    # ------------------------------------------------------------------
    async def _emit_dict(self, topic: EventTopics, payload: BaseModel) -> None:
        """Emit a Pydantic model as a dictionary to the event bus.
        
        Args:
            topic: Event topic
            payload: Pydantic model to emit
        """
        try:
            # Convert Pydantic model to dict using model_dump() method
            # Assumes payload is a Pydantic BaseModel
            payload_dict = payload.model_dump()
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
        self.logger.info(f"Stopping {self.name}")
        # Cancel all running tasks (includes layer tasks)
        for task in self._tasks:
            if not task.done():
                task.cancel()

        # Wait for tasks to complete cancellation with timeout
        if self._tasks:
            await asyncio.wait(self._tasks, timeout=5.0)

        self._tasks.clear()
        self._layer_tasks.clear()
        self._active_plans.clear()
        self._timeline_layers.clear()
        self._paused_timelines.clear()
        # Clear events (might need cancellation if waiting) - simpler to just clear dicts
        self._layer_events.clear()
        self._speech_end_events.clear()
        self._cached_speech_playback_events.clear()
        self._crossfade_complete_events.clear()
        self._active_speech_playbacks.clear()

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
            self.subscribe(EventTopics.TRACK_PLAYING, self._handle_music_started)
        )
        self._tasks.append(task)
        
        task = asyncio.create_task(
            self.subscribe(EventTopics.TRACK_STOPPED, self._handle_music_stopped)
        )
        self._tasks.append(task)
        
        # Subscribe to memory updates for state coordination
        task = asyncio.create_task(
            self.subscribe(EventTopics.MEMORY_UPDATED, self._handle_memory_updated)
        )
        self._tasks.append(task)
        
        # Subscribe to speech completion events for un-duck/plan progress
        # Keep old handler for legacy speak step if still used
        task = asyncio.create_task(
            self.subscribe(EventTopics.SPEECH_GENERATION_COMPLETE, self._handle_speech_generation_complete)
        )
        self._tasks.append(task)

        # Add handler for new cached speech playback completion
        task = asyncio.create_task(
            self.subscribe(EventTopics.SPEECH_CACHE_PLAYBACK_COMPLETED, self._handle_cached_speech_playback_completed)
        )
        self._tasks.append(task)

        # Subscribe to speech ended events for plan coordination (Legacy?)
        # task = asyncio.create_task(
        #     self.subscribe(EventTopics.SPEECH_SYNTHESIS_ENDED, self._handle_speech_ended)
        # )
        # self._tasks.append(task)  # Comment out this line as well
        
        # Subscribe to direct LLM responses (Legacy, likely not needed for DJ mode)
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

        # Subscribe to MusicController crossfade complete event
        task = asyncio.create_task(
            self.subscribe(EventTopics.CROSSFADE_COMPLETE, self._handle_crossfade_complete)
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

    async def _run_plan(self, plan: Union[PlanPayload, DjTransitionPlanPayload], layer: str) -> None:
        """Executes a given timeline plan.

        Args:
            plan: The PlanPayload containing the steps to execute.
        """
        self.logger.info(f"Running plan {plan.plan_id} on layer {layer}")
        self._active_plans[plan.plan_id] = plan

        await self._emit_dict(EventTopics.PLAN_STARTED, PlanStartedPayload(plan_id=plan.plan_id, layer=layer))

        try:
            # Wait for the layer to be ready (not paused)
            await self._layer_events[layer].wait()

            # Execute steps sequentially
            for step in plan.steps:
                self.logger.debug(f"Executing step {step.id} ({step.type}) for plan {plan.plan_id}")
                await self._emit_dict(
                    EventTopics.STEP_READY,
                    StepReadyPayload(plan_id=plan.plan_id, step_id=getattr(step, 'id', 'N/A'))
                )

                # Execute the step based on its parsed type
                # The execute methods will now handle waiting for completion if necessary
                step_success, step_details = await self._execute_step(step, plan.plan_id)

                await self._emit_dict(
                    EventTopics.STEP_EXECUTED,
                    StepExecutedPayload(
                        plan_id=plan.plan_id,
                        step_id=step.id,
                        status="success" if step_success else "failure",
                        details=step_details or {}
                    )
                )

                if not step_success:
                    self.logger.error(f"Step {step.id} failed for plan {plan.plan_id}.")
                    # Decide how to handle step failure - continue, stop plan, etc.
                    # For now, let's stop the plan on step failure.
                    await self._emit_dict(
                        EventTopics.PLAN_ENDED,
                        PlanEndedPayload(plan_id=plan.plan_id, layer=layer, status="failed")
                    )
                    return

                # Handle delays within steps (if applicable) or explicit delay steps
                if step.delay and step.delay > 0:
                    await asyncio.sleep(step.delay)

            # Plan completed successfully
            self.logger.info(f"Plan {plan.plan_id} completed successfully on layer {layer}")
            await self._emit_dict(
                EventTopics.PLAN_ENDED,
                PlanEndedPayload(plan_id=plan.plan_id, layer=layer, status="completed")
            )

        except asyncio.CancelledError:
            self.logger.info(f"Plan {plan.plan_id} on layer {layer} cancelled")
            await self._emit_dict(
                EventTopics.PLAN_ENDED,
                PlanEndedPayload(plan_id=plan.plan_id, layer=layer, status="cancelled")
            )
        except Exception as e:
            self.logger.error(f"Error running plan {plan.plan_id} on layer {layer}: {e}", exc_info=True)
            await self._emit_dict(
                EventTopics.PLAN_ENDED,
                PlanEndedPayload(plan_id=plan.plan_id, layer=layer, status="error")
            )
        finally:
            # Clean up active plan entry
            self._active_plans.pop(plan.plan_id, None)

    # ------------------------------------------------------------------
    # Step execution
    # ------------------------------------------------------------------
    async def _execute_step(self, step: PlanStep, plan_id: str) -> tuple[bool, Optional[Dict[str, Any]]]:
        """Executes a single step within a plan.

        Args:
            step: The PlanStep to execute.
            plan_id: The ID of the plan this step belongs to.

        Returns:
            A tuple of (success: bool, details: Optional[Dict[str, Any]]).
        """
        try:
            if step.type == "play_music":
                return await self._execute_play_music_step(step)
            elif step.type == "speak":
                return await self._execute_speak_step(step, plan_id)
            elif step.type == "eye_pattern":
                return await self._execute_eye_pattern_step(step)
            elif step.type == "move":
                return await self._execute_move_step(step)
            elif step.type == "wait_for_event":
                # TODO: Implement wait_for_event step logic
                self.logger.warning(f"wait_for_event step type not yet implemented. Step: {step.id}")
                return False, {"error": "Not implemented"}
            elif step.type == "delay":
                await asyncio.sleep(step.delay or 0) # delay is handled in _run_plan as well, keep here for explicit delay steps
                return True, {}
            # TODO: Add handling for new DJ mode step types: play_cached_speech, music_crossfade

            else:
                self.logger.error(f"Unknown step type: {step.type} for step {step.id} in plan {plan_id}")
                return False, {"error": f"Unknown step type: {step.type}"}

        except asyncio.CancelledError:
            raise # Propagate cancellation
        except Exception as e:
            self.logger.error(f"Error executing step {step.id} ({step.type}) for plan {plan_id}: {e}", exc_info=True)
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
        # Route through CommandDispatcher instead of direct service emission
        if step.genre == "stop":
            command = "stop music"
            args = []
        else:
            command = "play music"
            args = [step.genre] if step.genre else []
        
        await self.emit(
            EventTopics.CLI_COMMAND,
            {
                "command": command.split()[0],
                "args": command.split()[1:] + args,
                "raw_input": f"{command} {' '.join(args)}".strip(),
                "conversation_id": getattr(step, "conversation_id", None)
            }
        )
        return True, {"genre": step.genre}

    async def _execute_eye_pattern_step(self, step: PlanStep) -> tuple[bool, Dict[str, Any]]:
        """Execute an eye_pattern step."""
        # Route through CommandDispatcher instead of direct service emission
        command = "eye pattern"
        args = [step.pattern] if step.pattern else ["default"]
        
        await self.emit(
            EventTopics.CLI_COMMAND,
            {
                "command": command.split()[0],
                "args": command.split()[1:] + args,
                "raw_input": f"{command} {' '.join(args)}".strip()
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
        """Handle SPEECH_SYNTHESIS_ENDED events to signal speech completion.
        
        This is used by plans that need to wait for speech before proceeding.
        """
        # This handler is for the old SPEECH_SYNTHESIS_ENDED event.
        # We need to handle completion for cached speech playback using SPEECH_CACHE_PLAYBACK_COMPLETED.
        self.logger.debug(f"Received SPEECH_SYNTHESIS_ENDED for plan {payload.get('plan_id')}, step {payload.get('step_id')}")
        # Signal speech completion for the relevant plan/step
        plan_id = payload.get('plan_id') # Need to ensure this is passed in payload
        step_id = payload.get('step_id') # Need to ensure this is passed in payload

        if plan_id and step_id:
             event_key = f"speech_ended_{plan_id}_{step_id}"
             if event_key in self._speech_end_events:
                  self.logger.debug(f"Setting speech end event for {event_key}")
                  self._speech_end_events[event_key].set() # Signal completion

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
        """Handle speech generation complete for un-ducking or plan progress.

        This is primarily for the old speak step which requested generation directly.
        New DJ mode should use cached speech.
        """
        self.logger.debug("Received SPEECH_GENERATION_COMPLETE")
        try:
            # Use Pydantic model for validation
            complete_payload = SpeechGenerationCompletePayload(**payload)

            if complete_payload.success:
                self.logger.info(f"Speech generation complete for text: {complete_payload.text[:50]}...")
                # We might need to signal something here if a plan step was waiting for this.
                # However, the new DJ mode uses cached speech, which has a separate completion event.
                # This handler might become less relevant for DJ mode plans.

            else:
                self.logger.error(f"Speech generation failed: {complete_payload.error}")

        except ValidationError as e:
            self.logger.error(f"Validation error for SPEECH_GENERATION_COMPLETE payload: {e}")
        except Exception as e:
            self.logger.error(f"Error handling SPEECH_GENERATION_COMPLETE: {e}", exc_info=True)

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
        """Handle LLM response events.

        This is a legacy handler for direct LLM responses triggering speech. Not used for DJ mode plans.
        """
        self.logger.debug("Received LLM_RESPONSE")
        # This handler seems to be for triggering direct speech synthesis from LLM text.
        # DJ mode plans handle speech via cached entries, so this handler is not directly relevant.
        pass

    # ------------------------------------------------------------------
    # Add handlers for voice activity
    # ------------------------------------------------------------------
    async def _handle_voice_listening_started(self, payload: Dict[str, Any]) -> None:
        """Handle voice listening started events to duck music.

        Payload format: {}
        """
        self.logger.info("Voice listening started, attempting to duck music.")
        await self._duck_music()

    async def _handle_voice_listening_stopped(self, payload: Dict[str, Any]) -> None:
        """Handle voice listening stopped events to unduck music.

        Payload format: {}
        """
        self.logger.info("Voice listening stopped, attempting to unduck music.")
        # Only unduck if speech is not currently playing (cached or generated)
        # Need to track active speech playback
        # For now, a simple unduck
        await self._unduck_music()


    # --- New Handlers for DJ Mode --- #

    async def _execute_play_cached_speech_step(self, step: PlayCachedSpeechStep) -> tuple[bool, Optional[Dict[str, Any]]]:
        """Executes a PlayCachedSpeechStep.

        Requests CachedSpeechService to play a cached audio entry.
        Waits for playback completion.
        """
        self.logger.info(f"Executing PlayCachedSpeechStep for cache_key: {step.cache_key}")
        # Generate a unique playback ID for this request
        playback_id = str(uuid.uuid4())
        event_key = f"cached_speech_complete_{playback_id}"

        # Store an event that will be set when this playback completes
        self._cached_speech_playback_events[event_key] = asyncio.Event()
        self._active_speech_playbacks.add(playback_id)
        self.logger.debug(f"Created cached speech completion event for {event_key}")

        try:
            # Emit event to CachedSpeechService to request playback
            playback_request_payload = SpeechCachePlaybackRequestPayload(
                # timestamp is auto-generated
                cache_key=step.cache_key,
                volume=1.0, # TODO: Make volume configurable in step or plan
                playback_id=playback_id,
                # Pass step/plan ID in metadata for linking completion event
                metadata={'plan_id': self._active_plans.get(self._layer_tasks.get(self._timeline_layers.get(getattr(self, '_current_plan_id', None)), None)
                                                                            .plan_id if hasattr(self._layer_tasks.get(self._timeline_layers.get(getattr(self, '_current_plan_id', None)), None), 'plan_id') else None), # Attempt to get current plan_id
                          'step_id': getattr(step, 'id', 'N/A'),
                          'cache_key': step.cache_key, # Include cache key for easier lookup
                         }
            )

            await self._emit_dict(
                EventTopics.SPEECH_CACHE_PLAYBACK_REQUEST,
                playback_request_payload
            )

            # Wait for playback completion
            self.logger.debug(f"Waiting for cached speech playback completion event for {event_key}")
            try:
                # TODO: Get actual duration from cached speech metadata to set a more accurate timeout
                # For now, use a default timeout or a timeout from config
                await asyncio.wait_for(self._cached_speech_playback_events[event_key].wait(), timeout=self._config.speech_wait_timeout)
                self.logger.debug(f"Cached speech playback completion event received for {event_key}")
                return True, {"cache_key": step.cache_key, "playback_id": playback_id, "status": "completed"}
            except asyncio.TimeoutError:
                self.logger.warning(f"Timeout waiting for cached speech playback completion event for {event_key}")
                return False, {"cache_key": step.cache_key, "playback_id": playback_id, "error": "Timeout waiting for playback completion"}

        except Exception as e:
            self.logger.error(f"Error executing PlayCachedSpeechStep for cache_key {step.cache_key}: {e}", exc_info=True)
            return False, {"error": str(e)}
        finally:
            # Clean up the event and active playback tracking
            self._cached_speech_playback_events.pop(event_key, None)
            self._active_speech_playbacks.discard(playback_id)


    async def _execute_music_crossfade_step(self, step: MusicCrossfadeStep) -> tuple[bool, Optional[Dict[str, Any]]]:
        """Executes a MusicCrossfadeStep.

        Requests MusicControllerService to perform a crossfade.
        Waits for crossfade completion.
        """
        self.logger.info(f"Executing MusicCrossfadeStep to track: {step.next_track_id} with duration {step.crossfade_duration}s")
        # Generate a unique crossfade ID for this request
        crossfade_id = str(uuid.uuid4())
        event_key = f"crossfade_complete_{crossfade_id}"

        # Store an event that will be set when this crossfade completes
        self._crossfade_complete_events[event_key] = asyncio.Event()
        self.logger.debug(f"Created crossfade completion event for {event_key}")

        try:
            # Route through CommandDispatcher instead of direct service emission
            # Note: This assumes a "crossfade music" command exists or will be added
            command = "crossfade music"
            args = [step.next_track_id, str(step.crossfade_duration)]
            
            await self.emit(
                EventTopics.CLI_COMMAND,
                {
                    "command": command.split()[0],
                    "args": command.split()[1:] + args,
                    "raw_input": f"{command} {' '.join(args)}".strip()
                }
            )

            # Wait for crossfade completion
            self.logger.debug(f"Waiting for crossfade completion event for {event_key}")
            try:
                # Timeout slightly longer than expected duration
                await asyncio.wait_for(self._crossfade_complete_events[event_key].wait(), timeout=step.crossfade_duration + 5.0)
                self.logger.debug(f"Crossfade completion event received for {event_key}")
                return True, {"next_track_id": step.next_track_id, "duration": step.crossfade_duration, "status": "completed"}
            except asyncio.TimeoutError:
                self.logger.warning(f"Timeout waiting for crossfade completion event for {event_key}")
                return False, {"next_track_id": step.next_track_id, "duration": step.crossfade_duration, "error": "Timeout waiting for crossfade completion"}

        except Exception as e:
            self.logger.error(f"Error executing MusicCrossfadeStep to track {step.next_track_id}: {e}", exc_info=True)
            return False, {"error": str(e)}
        finally:
            # Clean up the event
            self._crossfade_complete_events.pop(event_key, None)

    # --- New DJ Mode Completion Handlers --- #

    async def _handle_cached_speech_playback_completed(self, payload: Dict[str, Any]) -> None:
         """Handle SPEECH_CACHE_PLAYBACK_COMPLETED events.

         Signals completion of cached speech playback to unblock waiting plan steps.
         Also handles audio un-ducking if necessary.
         """
         self.logger.debug(f"Received SPEECH_CACHE_PLAYBACK_COMPLETED payload: {payload}")
         try:
              # Use Pydantic model for incoming payload
              completion_payload = SpeechCachePlaybackCompletedPayload(**payload)
              playback_id = completion_payload.playback_id
              completion_status = completion_payload.completion_status
              metadata = completion_payload.metadata # Access metadata

              self.logger.info(f"Cached speech playback {playback_id} completed with status: {completion_status}")

              # Signal the waiting step event using the playback_id
              event_key = f"cached_speech_complete_{playback_id}"
              if event_key in self._cached_speech_playback_events:
                   self.logger.debug(f"Setting cached speech completion event for {event_key}")
                   self._cached_speech_playback_events[event_key].set() # Signal completion
              else:
                   self.logger.warning(f"Received SPEECH_CACHE_PLAYBACK_COMPLETED for unknown playback_id: {playback_id}")

              # Handle audio un-ducking if this was the last active speech playback
              # Remove from active playbacks set
              self._active_speech_playbacks.discard(playback_id)
              # If no active speech playbacks remain and music is playing and ducked, unduck.
              # TODO: Implement proper ducking/unducking logic based on active audio sources.
              # For now, a simplified check:
              if not self._active_speech_playbacks and self._current_music_playing and self._audio_ducked:
                   self.logger.info("Last cached speech playback completed, attempting to unduck music.")
                   # TODO: Emit unduck command to MusicController or a central AudioService
                   self.logger.warning("Music unducking not yet implemented in TimelineExecutorService after cached speech completion.")
                   # await self._unduck_music() # Placeholder

         except ValidationError as e:
              self.logger.error(f"Validation error for SPEECH_CACHE_PLAYBACK_COMPLETED payload: {e}")
         except Exception as e:
              self.logger.error(f"Error handling SPEECH_CACHE_PLAYBACK_COMPLETED: {e}", exc_info=True)


    async def _handle_crossfade_complete(self, payload: Dict[str, Any]) -> None:
         """Handle CROSSFADE_COMPLETE events.

         Signals completion of a music crossfade to unblock waiting plan steps.
         """
         self.logger.debug(f"Received CROSSFADE_COMPLETE payload: {payload}")
         try:
              # TODO: Use Pydantic model for incoming payload if one is defined for CROSSFADE_COMPLETE
              # Assuming payload includes crossfade_id for linking
              crossfade_id = payload.get('crossfade_id') # Need CROSSFADE_COMPLETE payload to include this

              if crossfade_id:
                   self.logger.info(f"Crossfade {crossfade_id} completed.")
                   # Signal the waiting step event
                   event_key = f"crossfade_complete_{crossfade_id}"
                   if event_key in self._crossfade_complete_events:
                        self.logger.debug(f"Setting crossfade completion event for {event_key}")
                        self._crossfade_complete_events[event_key].set() # Signal completion
                   else:
                        self.logger.warning(f"Received CROSSFADE_COMPLETE for unknown crossfade_id: {crossfade_id}")
              else:
                   self.logger.warning("Received CROSSFADE_COMPLETE event without a crossfade_id in payload. Cannot signal specific step.")
                   # TODO: Consider how to handle this - maybe signal the most recent crossfade event?

         except Exception as e:
              self.logger.error(f"Error handling CROSSFADE_COMPLETE: {e}", exc_info=True)

    # ------------------------------------------------------------------
    # Audio ducking methods
    # ------------------------------------------------------------------
    async def _duck_music(self) -> None:
        """Duck music volume during voice activity."""
        if self._current_music_playing:
            self.logger.info("Ducking music volume for voice activity")
            await self._emit_dict(
                EventTopics.AUDIO_DUCKING_START,
                {"level": self._config.default_ducking_level, "fade_ms": self._config.ducking_fade_ms}
            )
            self._audio_ducked = True
            # Small delay to ensure ducking has started
            await asyncio.sleep(0.15)

    async def _unduck_music(self) -> None:
        """Restore music volume after voice activity."""
        if self._current_music_playing and self._audio_ducked:
            self.logger.info("Restoring music volume after voice activity")
            await self._emit_dict(
                EventTopics.AUDIO_DUCKING_STOP,
                {"fade_ms": self._config.ducking_fade_ms}
            )
            self._audio_ducked = False
            # Small delay to ensure unducking has started
            await asyncio.sleep(0.15) 