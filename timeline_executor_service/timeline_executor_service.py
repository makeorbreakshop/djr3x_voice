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

from pydantic import BaseModel, ValidationError, Field

from cantina_os.bus import EventTopics, LogLevel, ServiceStatus
from cantina_os.services.base import StandardService
from cantina_os.event_payloads import (
    PlanPayload,
    PlanStartedPayload,
    StepReadyPayload,
    StepExecutedPayload,
    PlanEndedPayload,
    PlanStep
)

# ---------------------------------------------------------------------------
# Configuration model
# ---------------------------------------------------------------------------
class _Config(BaseModel):
    """Pydantic‑validated configuration for the timeline executor service."""
    default_ducking_level: float = Field(default=0.3, description="Default ducking level (0.0-1.0)")
    ducking_fade_ms: int = Field(default=500, description="Ducking fade time in milliseconds")
    layer_priorities: Dict[str, int] = Field(
        default={
            "override": 3,
            "foreground": 2,
            "ambient": 1
        },
        description="Priority values for different timeline layers"
    )
    speech_wait_timeout: float = Field(default=10.0, description="Maximum seconds to wait for speech to complete")


# ---------------------------------------------------------------------------
# Service Implementation
# ---------------------------------------------------------------------------
class TimelineExecutorService(StandardService):
    """Timeline executor service for DJ R3X.
    
    Handles layered execution of plans:
    - override layer cancels lower layers
    - foreground pauses ambient; ambient resumes on finish
    - each layer can have its own plan running
    
    Sequence for speak steps: duck → TTS → wait → unduck
    """

    def __init__(
        self,
        event_bus,
        config: Dict[str, Any] | None = None,
        name: str = "timeline_executor_service",
    ) -> None:
        super().__init__(event_bus, config, name=name)

        # ----- validated configuration -----
        self._config = _Config(**(config or {}))

        # ----- bookkeeping -----
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tasks: List[asyncio.Task] = []
        self._subs: List[tuple[str, Callable]] = []
        
        # ----- timeline state -----
        self._layer_tasks: Dict[str, asyncio.Task] = {}  # Tasks running plans on each layer
        self._active_plans: Dict[str, PlanPayload] = {}  # Currently active plans by ID
        self._layer_events: Dict[str, asyncio.Event] = {}  # Events for pausing/resuming layers
        self._speech_end_events: Dict[str, asyncio.Event] = {}  # Events for speech completion
        
    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def _start(self) -> None:
        """Initialize timeline executor service and set up subscriptions."""
        self._loop = asyncio.get_running_loop()
        await self._setup_subscriptions()
        
        # Initialize layer events
        for layer in self._config.layer_priorities:
            self._layer_events[layer] = asyncio.Event()
            self._layer_events[layer].set()  # Start in unpaused state
            
        await self._emit_status(ServiceStatus.OK, "Timeline executor service started")

    async def _stop(self) -> None:
        """Clean up tasks and subscriptions."""
        # Cancel all layer tasks
        for layer, task in self._layer_tasks.items():
            if not task.done():
                task.cancel()
        
        # Cancel all other tasks
        for task in self._tasks:
            if not task.done():
                task.cancel()
                
        # Wait for tasks to complete with timeout
        all_tasks = list(self._layer_tasks.values()) + self._tasks
        if all_tasks:
            await asyncio.wait(all_tasks, timeout=5.0)
            
        self._tasks.clear()
        self._layer_tasks.clear()

        for topic, handler in self._subs:
            self.unsubscribe(topic, handler)
        self._subs.clear()

        await self._emit_status(ServiceStatus.OK, "Timeline executor service stopped")

    # ------------------------------------------------------------------
    # Subscription setup
    # ------------------------------------------------------------------
    async def _setup_subscriptions(self) -> None:
        """Register event-handlers for timeline execution."""
        await super()._setup_subscriptions()
        
        # Create task for main subscription
        asyncio.create_task(
            self._subscribe(EventTopics.PLAN_READY, self._handle_plan_ready)
        )
        
        # Subscribe to events needed for step execution
        asyncio.create_task(
            self._subscribe(EventTopics.SPEECH_SYNTHESIS_ENDED, self._handle_speech_ended)
        )

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
                    ServiceStatus.OK, 
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
            # Start audio ducking
            await self._emit_dict(
                EventTopics.AUDIO_DUCKING_START,
                {"level": self._config.default_ducking_level, "fade_ms": self._config.ducking_fade_ms}
            )
            
            # Small delay to ensure ducking has started
            await asyncio.sleep(0.1)
            
            # Request TTS generation
            await self._emit_dict(
                EventTopics.TTS_GENERATE_REQUEST,
                {
                    "text": step.text,
                    "clip_id": speech_id,
                    "step_id": step.id,
                    "plan_id": plan_id
                }
            )
            
            # Wait for speech synthesis to complete with timeout
            try:
                await asyncio.wait_for(
                    speech_event.wait(),
                    timeout=self._config.speech_wait_timeout
                )
                speech_success = True
            except asyncio.TimeoutError:
                speech_success = False
                await self._emit_status(
                    ServiceStatus.ERROR,
                    f"Timeout waiting for speech synthesis to complete for step {step.id}",
                    LogLevel.ERROR
                )
            
            # Add a small delay to ensure speech playback has finished
            await asyncio.sleep(0.25)
            
            # Stop audio ducking
            await self._emit_dict(
                EventTopics.AUDIO_DUCKING_STOP,
                {"fade_ms": self._config.ducking_fade_ms}
            )
            
            return speech_success, {"text": step.text, "speech_id": speech_id}
            
        finally:
            # Clean up the speech event
            if speech_id in self._speech_end_events:
                del self._speech_end_events[speech_id]

    async def _execute_play_music_step(self, step: PlanStep) -> tuple[bool, Dict[str, Any]]:
        """Execute a play_music step.
        
        Special handling for genre="stop" to stop music playback.
        """
        # Check for special "stop" genre value
        if step.genre == "stop":
            # Emit stop music command
            await self._emit_dict(
                EventTopics.MUSIC_COMMAND,
                {
                    "action": "stop"
                }
            )
            return True, {"action": "stop"}
        else:
            # Normal play command
            await self._emit_dict(
                EventTopics.MUSIC_COMMAND,
                {
                    "action": "play",
                    "song_query": step.genre or ""
                }
            )
            return True, {"action": "play", "genre": step.genre}

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
            
            if clip_id in self._speech_end_events:
                self._speech_end_events[clip_id].set()
            else:
                # Look for matching step_id in active plans
                step_id = payload.get("step_id", "")
                if step_id in self._speech_end_events:
                    self._speech_end_events[step_id].set()
        except Exception as e:
            await self._emit_status(
                ServiceStatus.ERROR,
                f"Error handling speech ended: {e}",
                LogLevel.ERROR
            )

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
                        ServiceStatus.OK,
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
                        ServiceStatus.OK,
                        f"Pausing {other_layer} layer due to {layer} priority",
                        LogLevel.INFO
                    )

    async def _resume_layer(self, layer: str) -> None:
        """Resume a paused layer."""
        if layer in self._layer_events:
            self._layer_events[layer].set()
            
            # Log resumption
            await self._emit_status(
                ServiceStatus.OK,
                f"Resuming {layer} layer",
                LogLevel.INFO
            ) 