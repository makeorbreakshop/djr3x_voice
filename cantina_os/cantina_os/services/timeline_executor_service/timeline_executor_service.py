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
    MusicDuckStep, # Add import for music duck step
    MusicUnduckStep, # Add import for music unduck step
    ParallelSteps, # Add import for parallel steps
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
    default_ducking_level: float = 0.5  # Default ducking level (0.0-1.0) - Updated to 50%
    ducking_fade_ms: int = 500  # Fade time in ms for ducking - Updated for longer transitions
    speech_wait_timeout: float = 25.0  # Timeout for waiting for speech to complete (increased from 10.0 to handle long commentary)
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
        self._active_plans: Dict[str, DjTransitionPlanPayload] = {}  # Currently active plans by ID
        self._timeline_layers: Dict[str, str] = {}  # Maps plan_id -> layer (Legacy? Or needed for paused plans?)
        self._paused_timelines: Dict[str, DjTransitionPlanPayload] = {}  # For resuming after interrupts
        
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
            await self._setup_subscriptions()
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
    async def _setup_subscriptions(self) -> None:
        """Register event-handlers for timeline execution."""
        # Subscribe to timeline management events
        await self.subscribe(EventTopics.PLAN_READY, self._handle_plan_ready)
        
        # Listen for music state changes for coordination
        await self.subscribe(EventTopics.TRACK_PLAYING, self._handle_music_started)
        
        await self.subscribe(EventTopics.TRACK_STOPPED, self._handle_music_stopped)
        
        # Subscribe to memory updates for state coordination
        await self.subscribe(EventTopics.MEMORY_UPDATED, self._handle_memory_updated)
        
        # Subscribe to speech completion events for un-duck/plan progress
        # Keep old handler for legacy speak step if still used
        await self.subscribe(EventTopics.SPEECH_GENERATION_COMPLETE, self._handle_speech_generation_complete)

        # Add handler for new cached speech playback completion
        await self.subscribe(EventTopics.SPEECH_CACHE_PLAYBACK_COMPLETED, self._handle_cached_speech_playback_completed)

        # Subscribe to direct LLM responses (Legacy, likely not needed for DJ mode)
        await self.subscribe(EventTopics.LLM_RESPONSE, self._handle_llm_response)
        
        # Subscribe to voice recording events to duck during microphone activity
        await self.subscribe(EventTopics.VOICE_LISTENING_STARTED, self._handle_voice_listening_started)
        
        await self.subscribe(EventTopics.VOICE_LISTENING_STOPPED, self._handle_voice_listening_stopped)

        # Subscribe to MusicController crossfade complete event
        await self.subscribe(EventTopics.CROSSFADE_COMPLETE, self._handle_crossfade_complete)

    # ------------------------------------------------------------------
    # Plan handling
    # ------------------------------------------------------------------
    async def _handle_plan_ready(self, payload: Dict[str, Any]) -> None:
        """Handle PLAN_READY events from Brain service.
        
        This is the entry point for executing plans on different layers.
        """
        self.logger.info(f"Received PLAN_READY event with payload keys: {list(payload.keys())}")
        
        try:
            # Parse the PlanReadyPayload structure from BrainService
            plan_ready = PlanReadyPayload(**payload)
            
            # Extract the nested plan data
            plan_data = plan_ready.plan
            plan_id = plan_ready.plan_id
            
            self.logger.info(f"Processing plan {plan_id} with {len(plan_data.get('steps', []))} steps")
            
            # The plan_data is a dict containing the DjTransitionPlanPayload structure
            # We need to convert the steps back to proper Pydantic models
            raw_steps = plan_data.get('steps', [])
            converted_steps = []
            
            for step_dict in raw_steps:
                step_type = step_dict.get('step_type')
                self.logger.debug(f"Converting step of type: {step_type}")
                
                if step_type == 'speak':
                    # Leave speak steps as dictionaries - _execute_speak_step handles this format
                    step_obj = step_dict
                elif step_type == 'play_cached_speech':
                    step_obj = PlayCachedSpeechStep(**step_dict)
                elif step_type == 'music_crossfade':
                    step_obj = MusicCrossfadeStep(**step_dict)
                elif step_type == 'music_duck':
                    step_obj = MusicDuckStep(**step_dict)
                elif step_type == 'music_unduck':
                    step_obj = MusicUnduckStep(**step_dict)
                elif step_type == 'parallel_steps':
                    # Convert nested steps recursively
                    nested_steps = []
                    for nested_dict in step_dict.get('steps', []):
                        nested_type = nested_dict.get('step_type')
                        if nested_type == 'speak':
                            nested_steps.append(nested_dict)  # Leave as dict
                        elif nested_type == 'play_cached_speech':
                            nested_steps.append(PlayCachedSpeechStep(**nested_dict))
                        elif nested_type == 'music_crossfade':
                            nested_steps.append(MusicCrossfadeStep(**nested_dict))
                        elif nested_type == 'music_duck':
                            nested_steps.append(MusicDuckStep(**nested_dict))
                        elif nested_type == 'music_unduck':
                            nested_steps.append(MusicUnduckStep(**nested_dict))
                        else:
                            nested_steps.append(nested_dict)  # Leave unknown as dict
                    step_obj = ParallelSteps(step_type='parallel_steps', steps=nested_steps)
                else:
                    # For unknown step types, leave as dictionary 
                    step_obj = step_dict
                converted_steps.append(step_obj)
            
            # Create DjTransitionPlanPayload with converted steps
            dj_plan = DjTransitionPlanPayload(
                plan_id=plan_data['plan_id'],
                steps=converted_steps
            )
            
            # Default to foreground layer for DJ plans
            layer = "foreground"
            
            # Check if this layer is valid
            if layer not in self._config.layer_priorities:
                raise ValueError(f"Unknown layer: {layer}")
            
            self.logger.info(f"Starting plan {plan_id} on layer {layer}")
            
            # Check for existing plan on this layer and manage layer priorities
            if layer in self._layer_tasks and not self._layer_tasks[layer].done():
                self.logger.info(f"Cancelling existing plan on layer {layer}")
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
            self._active_plans[dj_plan.plan_id] = dj_plan
            task = asyncio.create_task(self._run_plan(dj_plan, layer))
            self._layer_tasks[layer] = task
            self._tasks.append(task)
            
            self.logger.info(f"Successfully started plan {plan_id} execution task")
            
            # Emit plan started event
            await self._emit_dict(
                EventTopics.PLAN_STARTED,
                PlanStartedPayload(
                    plan_id=dj_plan.plan_id,
                    layer=layer
                )
            )
        except ValidationError as e:
            self.logger.error(f"Validation error parsing PLAN_READY payload: {e}")
            await self._emit_status(
                ServiceStatus.ERROR,
                f"Invalid plan payload: {e}",
                LogLevel.ERROR
            )
        except Exception as e:
            self.logger.error(f"Error handling PLAN_READY event: {e}", exc_info=True)
            await self._emit_status(
                ServiceStatus.ERROR,
                f"Error handling plan: {e}",
                LogLevel.ERROR
            )

    async def _run_plan(self, plan: DjTransitionPlanPayload, layer: str) -> None:
        """Executes a given timeline plan.

        Args:
            plan: The DjTransitionPlanPayload containing the steps to execute.
            layer: The layer this plan is running on.
        """
        self.logger.info(f"Running plan {plan.plan_id} on layer {layer}")
        self._active_plans[plan.plan_id] = plan

        await self._emit_dict(EventTopics.PLAN_STARTED, PlanStartedPayload(plan_id=plan.plan_id, layer=layer))

        try:
            # Wait for the layer to be ready (not paused)
            await self._layer_events[layer].wait()

            # Execute steps sequentially
            for step in plan.steps:
                # Get proper step ID and type
                if hasattr(step, 'step_type'):
                    step_type = step.step_type
                    step_id = getattr(step, 'id', step_type)  # Use step ID if available, fallback to type
                elif isinstance(step, PlayCachedSpeechStep):
                    step_type = "play_cached_speech"
                    step_id = getattr(step, 'cache_key', step_type)  # Use cache_key as unique ID
                elif isinstance(step, MusicCrossfadeStep):
                    step_type = "music_crossfade"
                    step_id = getattr(step, 'next_track_id', step_type)  # Use track ID as unique ID
                elif isinstance(step, MusicDuckStep):
                    step_type = "music_duck"
                elif isinstance(step, MusicUnduckStep):
                    step_type = "music_unduck"
                elif isinstance(step, ParallelSteps):
                    step_type = "parallel_steps"
                else:
                    step_type = getattr(step, 'step_type', 'unknown')
                    step_id = getattr(step, 'id', step_type)
                
                self.logger.debug(f"Executing step {step_id} ({step_type}) for plan {plan.plan_id}")
                await self._emit_dict(
                    EventTopics.STEP_READY,
                    StepReadyPayload(plan_id=plan.plan_id, step_id=step_id)
                )

                # Execute the step based on its parsed type
                # The execute methods will now handle waiting for completion if necessary
                step_success, step_details = await self._execute_step(step, plan.plan_id)

                await self._emit_dict(
                    EventTopics.STEP_EXECUTED,
                    StepExecutedPayload(
                        plan_id=plan.plan_id,
                        step_id=step_id,
                        status="success" if step_success else "failure",
                        details=step_details or {}
                    )
                )

                if not step_success:
                    self.logger.error(f"Step {step_id} failed for plan {plan.plan_id}.")
                    # Decide how to handle step failure - continue, stop plan, etc.
                    # For now, let's stop the plan on step failure.
                    await self._emit_dict(
                        EventTopics.PLAN_ENDED,
                        PlanEndedPayload(plan_id=plan.plan_id, layer=layer, status="failed")
                    )
                    return

                # Handle delays within steps (if applicable) or explicit delay steps
                delay = getattr(step, 'delay', None) or getattr(step, 'duration', None)
                if delay and delay > 0:
                    await asyncio.sleep(delay)

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
                PlanEndedPayload(plan_id=plan.plan_id, layer=layer, status="failed")
            )
        finally:
            # Clean up active plan entry
            self._active_plans.pop(plan.plan_id, None)

    # ------------------------------------------------------------------
    # Step execution
    # ------------------------------------------------------------------
    async def _execute_step(self, step: BasePlanStep, plan_id: str) -> tuple[bool, Optional[Dict[str, Any]]]:
        """Executes a single step within a plan.

        Args:
            step: The BasePlanStep to execute (can be dict or Pydantic model).
            plan_id: The ID of the plan this step belongs to.

        Returns:
            A tuple of (success: bool, details: Optional[Dict[str, Any]]).
        """
        try:
            # Handle both dictionary and Pydantic model formats
            if isinstance(step, dict):
                # Dictionary format (from new helper method)
                step_type = step.get('step_type')
                if not step_type:
                    self.logger.error(f"Step dictionary missing step_type field: {list(step.keys())}")
                    return False, {"error": "Missing step_type field"}
                    
                self.logger.debug(f"Executing dict step type: {step_type} with fields: {list(step.keys())}")
            else:
                # Pydantic model format - determine step type
                if hasattr(step, 'step_type'):
                    step_type = step.step_type
                elif hasattr(step, 'type'):
                    # Handle PlanStep which uses 'type' field instead of 'step_type'
                    step_type = step.type
                elif isinstance(step, PlayCachedSpeechStep):
                    step_type = "play_cached_speech"
                elif isinstance(step, MusicCrossfadeStep):
                    step_type = "music_crossfade"
                elif isinstance(step, MusicDuckStep):
                    step_type = "music_duck"
                elif isinstance(step, MusicUnduckStep):
                    step_type = "music_unduck"
                elif isinstance(step, ParallelSteps):
                    step_type = "parallel_steps"
                else:
                    # Fallback: try to get from model fields
                    step_type = getattr(step, 'step_type', getattr(step, 'type', 'unknown'))
                
                self.logger.debug(f"Executing Pydantic step type: {step_type} for plan {plan_id}")
            
            if step_type == "play_music":
                return await self._execute_play_music_step(step)
            elif step_type == "speak":
                return await self._execute_speak_step(step, plan_id)
            elif step_type == "eye_pattern":
                return await self._execute_eye_pattern_step(step)
            elif step_type == "move":
                return await self._execute_move_step(step)
            elif step_type == "play_cached_speech":
                # Handle DJ mode cached speech step
                return await self._execute_play_cached_speech_step(step)
            elif step_type == "music_crossfade":
                # Handle DJ mode crossfade step
                return await self._execute_music_crossfade_step(step)
            elif step_type == "music_duck":
                # Handle DJ mode music ducking step
                return await self._execute_music_duck_step(step)
            elif step_type == "music_unduck":
                # Handle DJ mode music unducking step
                return await self._execute_music_unduck_step(step)
            elif step_type == "parallel_steps":
                # Handle parallel execution of multiple steps
                return await self._execute_parallel_steps(step, plan_id)
            elif step_type == "wait_for_event":
                # TODO: Implement wait_for_event step logic
                self.logger.warning(f"wait_for_event step type not yet implemented. Step: {step_type}")
                return False, {"error": "Not implemented"}
            elif step_type == "delay":
                delay = getattr(step, 'duration', 0) if not isinstance(step, dict) else step.get('duration', 0)
                await asyncio.sleep(delay or 0)
                return True, {}
            else:
                self.logger.error(f"Unknown step type: {step_type} for step in plan {plan_id}")
                return False, {"error": f"Unknown step type: {step_type}"}

        except asyncio.CancelledError:
            raise # Propagate cancellation
        except Exception as e:
            if isinstance(step, dict):
                step_type = step.get('step_type', 'unknown')
            else:
                step_type = getattr(step, 'step_type', getattr(step, 'type', 'unknown'))
            self.logger.error(f"Error executing step {step_type} for plan {plan_id}: {e}", exc_info=True)
            return False, {"error": str(e)}

    async def _execute_speak_step(self, step, plan_id: str) -> tuple[bool, Dict[str, Any]]:
        """Execute a speak step with audio ducking.
        
        Sequence: duck audio → generate TTS → wait for speech to end → unduck audio
        
        Args:
            step: Either PlanStep model or dict with text and optional id
            plan_id: The plan ID this step belongs to
        """
        # Handle both dictionary and Pydantic model formats
        if isinstance(step, dict):
            text = step.get('text')
            step_id = step.get('id', str(uuid.uuid4()))
            if not text:
                self.logger.error("Speak step dict missing text field")
                return False, {"error": "Missing text field"}
        else:
            text = step.text
            step_id = step.id if step.id else str(uuid.uuid4())
            
        # Create an event for speech completion
        speech_event = asyncio.Event()
        speech_id = step_id
        self._speech_end_events[speech_id] = speech_event
        
        try:
            # Start audio ducking if music is playing
            if self._current_music_playing:
                self.logger.info(f"Ducking audio for speech step '{step_id}'")
                await self.emit(
                    EventTopics.AUDIO_DUCKING_START,
                    {"level": self._config.default_ducking_level, "fade_ms": self._config.ducking_fade_ms}
                )
                self._audio_ducked = True
                
                # Small delay to ensure ducking has started
                await asyncio.sleep(0.15)
            
            # Request TTS generation
            self.logger.info(f"Generating speech for step '{step_id}': '{text[:30]}...'")
            await self.emit(
                EventTopics.TTS_GENERATE_REQUEST,
                SpeechGenerationRequestPayload(
                    text=text,
                    clip_id=speech_id,
                    step_id=step_id,
                    plan_id=plan_id,
                    conversation_id=None  # Add conversation_id parameter
                ).model_dump()
            )
            
            # Wait for speech synthesis to complete with timeout
            try:
                self.logger.info(f"Waiting for speech synthesis to complete (timeout: {self._config.speech_wait_timeout}s)")
                await asyncio.wait_for(
                    speech_event.wait(),
                    timeout=self._config.speech_wait_timeout
                )
                speech_success = True
                self.logger.info(f"Speech synthesis completed successfully for step '{step_id}'")
            except asyncio.TimeoutError:
                speech_success = False
                self.logger.error(f"Timeout waiting for speech synthesis to complete for step {step_id}")
                await self._emit_status(
                    ServiceStatus.ERROR,
                    f"Timeout waiting for speech synthesis to complete for step {step_id}",
                    LogLevel.ERROR
                )
                # Force progress even on timeout
                speech_success = True  # We'll continue with the plan despite the timeout
            
            # Add a small delay to ensure speech playback has finished
            await asyncio.sleep(0.25)
            
            # Note: We don't unduck audio here - that's now handled by the _handle_speech_generation_complete method
            # which responds to the SPEECH_GENERATION_COMPLETE event from ElevenLabsService
            
            return speech_success, {"text": text, "speech_id": speech_id}
            
        except Exception as e:
            self.logger.error(f"Error in speech step execution: {e}")
            
            # Ensure we stop ducking even if there's an error
            if self._current_music_playing and self._audio_ducked:
                try:
                    await self.emit(
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

        This is primarily for the speak step which requested generation directly.
        """
        self.logger.debug(f"Received SPEECH_GENERATION_COMPLETE: {payload}")
        try:
            # Use Pydantic model for validation
            complete_payload = SpeechGenerationCompletePayload(**payload)

            if complete_payload.success:
                self.logger.info(f"Speech generation complete for text: {complete_payload.text[:50]}...")
                
                # Signal the waiting speak step using clip_id, step_id, or conversation_id
                speech_id = complete_payload.clip_id or complete_payload.step_id or complete_payload.conversation_id
                
                if speech_id and speech_id in self._speech_end_events:
                    self.logger.debug(f"Setting speech completion event for speech_id: {speech_id}")
                    self._speech_end_events[speech_id].set()
                else:
                    self.logger.warning(f"No waiting event found for speech_id: {speech_id}")
                
                # Handle unducking if music is playing and ducked
                if self._current_music_playing and self._audio_ducked:
                    self.logger.info("Speech generation complete, restoring music volume")
                    await self.emit(
                        EventTopics.AUDIO_DUCKING_STOP,
                        {"fade_ms": self._config.ducking_fade_ms}
                    )
                    self._audio_ducked = False

            else:
                error_msg = complete_payload.error or "Unknown error"
                self.logger.error(f"Speech generation failed: {error_msg}")
                
                # Still signal completion even on failure so the plan can continue
                speech_id = complete_payload.clip_id or complete_payload.step_id or complete_payload.conversation_id
                if speech_id and speech_id in self._speech_end_events:
                    self.logger.debug(f"Setting speech completion event (failed) for speech_id: {speech_id}")
                    self._speech_end_events[speech_id].set()
                
                # Unduck music even on failure
                if self._current_music_playing and self._audio_ducked:
                    self.logger.info("Speech generation failed, restoring music volume")
                    await self.emit(
                        EventTopics.AUDIO_DUCKING_STOP,
                        {"fade_ms": self._config.ducking_fade_ms}
                    )
                    self._audio_ducked = False

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

    async def _execute_play_cached_speech_step(self, step) -> tuple[bool, Optional[Dict[str, Any]]]:
        """Executes a PlayCachedSpeechStep.

        Requests CachedSpeechService to play a cached audio entry.
        Waits for playback completion.
        
        Args:
            step: Either PlayCachedSpeechStep model or dict with cache_key
        """
        # Handle both dictionary and Pydantic model formats
        if isinstance(step, dict):
            cache_key = step.get('cache_key')
            if not cache_key:
                self.logger.error("PlayCachedSpeechStep dict missing cache_key field")
                return False, {"error": "Missing cache_key field"}
        else:
            cache_key = step.cache_key
            
        self.logger.info(f"Executing PlayCachedSpeechStep for cache_key: {cache_key}")
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
                timestamp=time.time(),
                cache_key=cache_key,
                volume=1.0,  # TODO: Make volume configurable in step or plan
                playback_id=playback_id,
                # Simple metadata - just include what we know
                metadata={
                    'cache_key': cache_key,
                    'step_type': 'play_cached_speech'
                }
            )

            await self._emit_dict(
                EventTopics.SPEECH_CACHE_PLAYBACK_REQUEST,
                playback_request_payload
            )

            # Wait for playback completion
            self.logger.debug(f"Waiting for cached speech playback completion event for {event_key}")
            try:
                await asyncio.wait_for(self._cached_speech_playback_events[event_key].wait(), timeout=self._config.speech_wait_timeout)
                self.logger.debug(f"Cached speech playback completion event received for {event_key}")
                return True, {"cache_key": cache_key, "playback_id": playback_id, "status": "completed"}
            except asyncio.TimeoutError:
                self.logger.warning(f"Timeout waiting for cached speech playback completion event for {event_key}")
                return False, {"cache_key": cache_key, "playback_id": playback_id, "error": "Timeout waiting for playback completion"}

        except Exception as e:
            self.logger.error(f"Error executing PlayCachedSpeechStep for cache_key {cache_key}: {e}", exc_info=True)
            return False, {"error": str(e)}
        finally:
            # Clean up the event and active playback tracking
            self._cached_speech_playback_events.pop(event_key, None)
            self._active_speech_playbacks.discard(playback_id)


    async def _execute_music_crossfade_step(self, step) -> tuple[bool, Optional[Dict[str, Any]]]:
        """Executes a MusicCrossfadeStep.

        Requests MusicControllerService to perform a crossfade.
        Waits for crossfade completion.
        
        Args:
            step: Either MusicCrossfadeStep model or dict with next_track_id and crossfade_duration
        """
        # Handle both dictionary and Pydantic model formats
        if isinstance(step, dict):
            next_track_id = step.get('next_track_id')
            crossfade_duration = step.get('crossfade_duration', 3.0)
            if not next_track_id:
                self.logger.error("MusicCrossfadeStep dict missing next_track_id field")
                return False, {"error": "Missing next_track_id field"}
        else:
            next_track_id = step.next_track_id
            crossfade_duration = step.crossfade_duration
            
        self.logger.info(f"Executing MusicCrossfadeStep to track: {next_track_id} with duration {crossfade_duration}s")
        # Generate a unique crossfade ID for this request
        crossfade_id = str(uuid.uuid4())
        event_key = f"crossfade_complete_{crossfade_id}"

        # Store an event that will be set when this crossfade completes
        self._crossfade_complete_events[event_key] = asyncio.Event()
        self.logger.debug(f"Created crossfade completion event for {event_key}")

        try:
            # Emit MUSIC_COMMAND event with crossfade action
            await self.emit(
                EventTopics.MUSIC_COMMAND,
                {
                    "action": "crossfade",
                    "song_query": next_track_id,
                    "fade_duration": crossfade_duration,
                    "crossfade_id": crossfade_id  # Include for completion tracking
                }
            )

            # Wait for crossfade completion
            self.logger.debug(f"Waiting for crossfade completion event for {event_key}")
            try:
                # Timeout slightly longer than expected duration
                await asyncio.wait_for(self._crossfade_complete_events[event_key].wait(), timeout=crossfade_duration + 5.0)
                self.logger.debug(f"Crossfade completion event received for {event_key}")
                return True, {"next_track_id": next_track_id, "duration": crossfade_duration, "status": "completed"}
            except asyncio.TimeoutError:
                self.logger.warning(f"Timeout waiting for crossfade completion event for {event_key}")
                return False, {"next_track_id": next_track_id, "duration": crossfade_duration, "error": "Timeout waiting for crossfade completion"}

        except Exception as e:
            self.logger.error(f"Error executing MusicCrossfadeStep to track {next_track_id}: {e}", exc_info=True)
            return False, {"error": str(e)}
        finally:
            # Clean up the event
            self._crossfade_complete_events.pop(event_key, None)

    async def _execute_music_duck_step(self, step) -> tuple[bool, Optional[Dict[str, Any]]]:
        """Executes a MusicDuckStep to lower music volume during speech.
        
        Args:
            step: Either MusicDuckStep model or dict with duck_level and fade_duration_ms
            
        Returns:
            Tuple of (success, details)
        """
        # Handle both dictionary and Pydantic model formats
        if isinstance(step, dict):
            duck_level = step.get('duck_level', 0.3)
            fade_duration_ms = step.get('fade_duration_ms', 1500)
        else:
            duck_level = step.duck_level
            fade_duration_ms = step.fade_duration_ms
            
        self.logger.info(f"Executing MusicDuckStep: ducking music to {duck_level * 100}% volume")
        
        try:
            # Emit audio ducking start event
            await self.emit(
                EventTopics.AUDIO_DUCKING_START,
                {
                    "level": duck_level,
                    "fade_ms": fade_duration_ms
                }
            )
            
            # Mark that audio is ducked for tracking
            self._audio_ducked = True
            
            # Small delay to ensure ducking has started
            await asyncio.sleep(fade_duration_ms / 1000.0)
            
            return True, {"duck_level": duck_level, "fade_duration_ms": fade_duration_ms}
            
        except Exception as e:
            self.logger.error(f"Error executing MusicDuckStep: {e}", exc_info=True)
            return False, {"error": str(e)}

    async def _execute_music_unduck_step(self, step) -> tuple[bool, Optional[Dict[str, Any]]]:
        """Executes a MusicUnduckStep to restore music volume after speech.
        
        Args:
            step: Either MusicUnduckStep model or dict with fade_duration_ms
            
        Returns:
            Tuple of (success, details)
        """
        # Handle both dictionary and Pydantic model formats
        if isinstance(step, dict):
            fade_duration_ms = step.get('fade_duration_ms', 1500)
        else:
            fade_duration_ms = step.fade_duration_ms
            
        self.logger.info(f"Executing MusicUnduckStep: restoring music volume")
        
        try:
            # Emit audio ducking stop event
            await self.emit(
                EventTopics.AUDIO_DUCKING_STOP,
                {
                    "fade_ms": fade_duration_ms
                }
            )
            
            # Mark that audio is no longer ducked
            self._audio_ducked = False
            
            # Small delay to ensure unducking has started
            await asyncio.sleep(fade_duration_ms / 1000.0)
            
            return True, {"fade_duration_ms": fade_duration_ms}
            
        except Exception as e:
            self.logger.error(f"Error executing MusicUnduckStep: {e}", exc_info=True)
            return False, {"error": str(e)}

    # ------------------------------------------------------------------
    # Add new handler for speech generation complete
    # ------------------------------------------------------------------
    async def _handle_cached_speech_playback_completed(self, payload: Dict[str, Any]) -> None:
         """Handle SPEECH_CACHE_PLAYBACK_COMPLETED events.

         Signals completion of cached speech playback to unblock waiting plan steps.
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

              # Remove from active playbacks set for tracking
              self._active_speech_playbacks.discard(playback_id)
              
              # Note: Music unducking is handled by the timeline plan's MusicUnduckStep
              # No automatic unduck needed here - the timeline orchestrates the audio coordination

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

    async def _execute_parallel_steps(self, step: ParallelSteps, plan_id: str) -> tuple[bool, Optional[Dict[str, Any]]]:
        """Execute multiple steps concurrently using asyncio.gather().
        
        Args:
            step: The ParallelSteps containing sub-steps to execute concurrently
            plan_id: The ID of the plan this step belongs to
            
        Returns:
            Tuple of (success, details) - success is True only if ALL sub-steps succeed
        """
        self.logger.info(f"Executing ParallelSteps with {len(step.steps)} concurrent sub-steps for plan {plan_id}")
        
        try:
            # Execute all sub-steps concurrently
            tasks = [self._execute_step(sub_step, plan_id) for sub_step in step.steps]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Check results - success only if all steps succeeded
            successes = []
            details = {"sub_step_results": []}
            
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    self.logger.error(f"Sub-step {i} raised exception: {result}")
                    successes.append(False)
                    details["sub_step_results"].append({"success": False, "error": str(result)})
                elif isinstance(result, tuple) and len(result) == 2:
                    success, step_details = result
                    successes.append(success)
                    details["sub_step_results"].append({"success": success, "details": step_details})
                else:
                    self.logger.warning(f"Sub-step {i} returned unexpected result format: {result}")
                    successes.append(False)
                    details["sub_step_results"].append({"success": False, "error": "Unexpected result format"})
            
            overall_success = all(successes)
            details["overall_success"] = overall_success
            details["successful_steps"] = sum(successes)
            details["total_steps"] = len(step.steps)
            
            self.logger.info(f"ParallelSteps completed: {details['successful_steps']}/{details['total_steps']} steps succeeded")
            
            return overall_success, details
            
        except Exception as e:
            self.logger.error(f"Error executing ParallelSteps: {e}", exc_info=True)
            return False, {"error": str(e)}

    # ------------------------------------------------------------------
    # Audio ducking methods
    # ------------------------------------------------------------------
    async def _duck_music(self) -> None:
        """Duck music volume during voice activity."""
        if self._current_music_playing:
            self.logger.info("Ducking music volume for voice activity")
            await self.emit(
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
            await self.emit(
                EventTopics.AUDIO_DUCKING_STOP,
                {"fade_ms": self._config.ducking_fade_ms}
            )
            self._audio_ducked = False
            # Small delay to ensure unducking has started
            await asyncio.sleep(0.15) 