"""
Base Service Class for CantinaOS

This module provides the BaseService class that all CantinaOS services should inherit from.
It implements common functionality such as lifecycle management, event bus integration,
and standardized logging.
"""

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Optional, Dict, Any, Callable, Awaitable, Union, List
from pyee.asyncio import AsyncIOEventEmitter
from pydantic import BaseModel
from enum import Enum

from .core.event_topics import EventTopics
from .event_payloads import (
    ServiceStatus,
    LogLevel,
    ServiceStatusPayload,
    BaseEventPayload
)

# ServiceStatus and LogLevel are imported from event_payloads.py

class BaseService:
    """
    Base class for all CantinaOS services.
    
    Provides:
    - Standardized lifecycle management (start/stop)
    - Event bus integration
    - Contextual logging
    - Service status reporting
    - Graceful error handling with fallback mechanisms
    """
    
    def __init__(self, service_name=None, event_bus=None, logger=None):
        """Initialize the service.
        
        Args:
            service_name: The name of the service
            event_bus: The event bus to use
            logger: The logger to use
        """
        self._service_name = service_name or self.__class__.__name__.lower()
        self._event_bus = event_bus
        self._logger = logger
        self._is_running = False
        self._status = ServiceStatus.INITIALIZING
        self._event_handlers: Dict[str, List[Callable]] = {}  # topic -> [handlers]
        self._started = False
    
    @property
    def service_name(self):
        """Get the service name with public accessor."""
        return self._service_name
        
    def set_event_bus(self, event_bus) -> None:
        """Set the event bus for this service.
        
        Args:
            event_bus: The event bus to use
        """
        self._event_bus = event_bus

    @property
    def logger(self):
        """Get the logger for this service."""
        if not self._logger:
            self._logger = logging.getLogger(f"cantina_os.{self._service_name}")
        return self._logger

    @property
    def is_running(self) -> bool:
        """Check if the service is currently running."""
        return self._started and self._status == ServiceStatus.RUNNING

    async def start(self) -> None:
        """Start the service."""
        if self._is_running:
            return
        
        if not self._event_bus:
            raise RuntimeError("Event bus not set. Call set_event_bus() before starting the service.")

        self.logger.debug(f"{self.__class__.__name__} starting...")
        await self._start()
        self._is_running = True
        self._started = True
        self._status = ServiceStatus.RUNNING
        self.logger.debug(f"{self.__class__.__name__} initialization complete")
        await self._emit_status(ServiceStatus.RUNNING, f"{self.__class__.__name__} started successfully")

    async def stop(self) -> None:
        """Stop the service."""
        if not self._is_running:
            return
            
        await self._stop()
        await self._remove_subscriptions()
        self._is_running = False
        self._started = False
        self._status = ServiceStatus.STOPPED
        await self._emit_status(ServiceStatus.STOPPED, f"{self.__class__.__name__} stopped")

    async def _start(self) -> None:
        """Service-specific startup logic. Override in subclass."""
        pass
        
    async def _stop(self) -> None:
        """Service-specific shutdown logic. Override in subclass."""
        pass
        
    async def _remove_subscriptions(self) -> None:
        """Remove all event subscriptions."""
        self.logger.debug(f"Removing all subscriptions for {self.__class__.__name__}")
        
        # Check if event_bus is None before attempting to remove listeners
        if self._event_bus is None:
            self.logger.debug(f"Event bus is None, skipping subscription removal for {self.__class__.__name__}")
            self._event_handlers.clear()
            return
            
        # Remove listeners with proper error handling
        for topic, handlers in list(self._event_handlers.items()):
            for handler in list(handlers):
                try:
                    # Don't await remove_listener since it's not a coroutine in pyee 11.0.1
                    self._event_bus.remove_listener(topic, handler)
                    self.logger.debug(f"Removed handler for topic {topic}")
                except Exception as e:
                    self.logger.debug(f"Error removing handler for {topic}: {e}")
            
        # Clear the handlers dictionary
        self._event_handlers.clear()

    async def _emit_status(self, status: ServiceStatus, message: str, severity: LogLevel = LogLevel.INFO) -> None:
        """Emit a service status event.
        
        Args:
            status: Service status enum value
            message: Status message
            severity: Log level severity
        """
        self._status = status
        
        # Log the status change
        log_method = getattr(self.logger, severity.lower())
        log_method(message)
        
        # Emit the status event
        await self.emit(
            "service_status",
            {
                "service": self.__class__.__name__,
                "status": status,
                "message": message,
                "severity": severity
            }
        )

    def debug_payload(self, payload: Any, prefix: str = "") -> None:
        """Debug helper for event payloads.
        
        Args:
            payload: The payload to debug
            prefix: Optional prefix for the log message
        """
        try:
            # Get payload type information
            payload_type = type(payload).__name__
            
            # Check if it's a Pydantic model
            is_pydantic = hasattr(payload, "model_dump") or hasattr(payload, "dict")
            
            # Try to get some payload content for debugging
            if isinstance(payload, dict):
                payload_keys = list(payload.keys())
                self.logger.debug(
                    f"{prefix}Payload is a dict with keys: {payload_keys}"
                )
                
                # Check for timestamp format
                if "timestamp" in payload:
                    self.logger.debug(
                        f"{prefix}timestamp value: {payload['timestamp']} (type: {type(payload['timestamp']).__name__})"
                    )
                    
            elif is_pydantic:
                # It's a Pydantic model
                model_attrs = dir(payload)
                field_names = [attr for attr in model_attrs if not attr.startswith("_") and not callable(getattr(payload, attr))]
                self.logger.debug(
                    f"{prefix}Payload is a Pydantic model ({payload_type}) with fields: {field_names}"
                )
                
                # Check for timestamp format if it exists
                if hasattr(payload, "timestamp"):
                    timestamp = getattr(payload, "timestamp")
                    self.logger.debug(
                        f"{prefix}timestamp value: {timestamp} (type: {type(timestamp).__name__})"
                    )
                    
            else:
                # Some other type
                self.logger.debug(
                    f"{prefix}Payload is type {payload_type}, content: {str(payload)[:100]}"
                )
                
        except Exception as e:
            self.logger.debug(f"{prefix}Error debugging payload: {e}")

    async def emit(self, event: str, payload: Any) -> None:
        """Emit an event on the event bus.

        Args:
            event: Event name/topic (can be string or EventTopics enum)
            payload: Event payload
        """
        if not self._event_bus:
            raise RuntimeError("Event bus not set")

        # Convert EventTopics enum to its string value if needed
        if isinstance(event, Enum):
            event = event.value

        # Debug payload if it's going to CLI_RESPONSE
        if event == "cli_response":
            self.debug_payload(payload, prefix="emit: ")

        # Convert Pydantic models to dictionaries if needed
        if hasattr(payload, "model_dump"):
            self.logger.debug(f"Converting Pydantic model to dict for event {event}")
            payload = payload.model_dump()
        elif hasattr(payload, "dict"):
            self.logger.debug(f"Converting Pydantic model to dict using .dict() for event {event}")
            payload = payload.dict()

        # Debug: Log event emission (truncate payload for readability)
        payload_preview = str(payload)[:100] if payload else "None"
        self.logger.debug(f"Emitting event '{event}' with payload: {payload_preview}...")

        # The emit method of pyee.AsyncIOEventEmitter returns a boolean, not a coroutine
        self._event_bus.emit(event, payload)

    async def subscribe(self, event: str, handler: Callable) -> None:
        """Subscribe to an event on the event bus.

        Args:
            event: Event name/topic to subscribe to (can be string or EventTopics enum)
            handler: Callback function to handle the event
        """
        if not self._event_bus:
            raise RuntimeError("Event bus not set")

        # Convert EventTopics enum to its string value if needed
        if isinstance(event, Enum):
            event = event.value

        # Store the handler for cleanup
        if event not in self._event_handlers:
            self._event_handlers[event] = []
        self._event_handlers[event].append(handler)

        # Add the handler to the event bus - don't await since pyee.AsyncIOEventEmitter.on is not a coroutine
        self._event_bus.on(event, handler)
        self.logger.debug(f"Subscribed to event: {event}")

    @property
    def status(self) -> ServiceStatus:
        """Get the current service status."""
        return self._status
        
    @property
    def is_started(self) -> bool:
        """Check if the service is started."""
        return self._started

    async def emit_error_response(
        self,
        topic: str,
        payload: BaseEventPayload
    ) -> None:
        """Emit an error response event.
        
        Args:
            topic: Event topic to emit on
            payload: Event payload
        """
        try:
            # Try to convert payload to dict if it's a Pydantic model
            if hasattr(payload, "model_dump"):
                payload_dict = payload.model_dump()
            elif hasattr(payload, "dict"):
                payload_dict = payload.dict()
            else:
                payload_dict = payload
                
            await self.emit(topic, payload_dict)
        except Exception as e:
            # If event emission fails, log the error
            self.logger.error(f"Failed to emit error response: {e}")
            # Try to log the payload content
            try:
                self.logger.error(f"Failed payload: {payload}")
            except:
                self.logger.error("Could not serialize error payload")

    async def debug_log(self, level: str, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        """Send a debug log message via the event system."""
        await self.emit(
            EventTopics.DEBUG_LOG,
            {
                "level": level,
                "component": self._service_name,
                "message": message,
                "details": details
            }
        )

    async def debug_trace_command(self, command: str, execution_time_ms: float, 
                                status: str, details: Optional[Dict[str, Any]] = None) -> None:
        """Trace a command execution via the event system."""
        await self.emit(
            EventTopics.DEBUG_COMMAND_TRACE,
            {
                "command": command,
                "service": self._service_name,
                "execution_time_ms": execution_time_ms,
                "status": status,
                "details": details
            }
        )

    async def debug_performance_metric(self, metric_name: str, value: float, 
                                    unit: str, details: Optional[Dict[str, Any]] = None) -> None:
        """Report a performance metric via the event system."""
        await self.emit(
            EventTopics.DEBUG_PERFORMANCE,
            {
                "metric_name": metric_name,
                "value": value,
                "unit": unit,
                "component": self._service_name,
                "details": details
            }
        )

    async def debug_state_transition(self, from_state: str, to_state: str,
                                   details: Optional[Dict[str, Any]] = None) -> None:
        """Report a state transition for debugging."""
        await self.emit(
            EventTopics.DEBUG_STATE_TRANSITION,
            {
                "component": self._service_name,
                "from_state": from_state,
                "to_state": to_state,
                "details": details
            }
        )


class RealtimeService(BaseService):
    """
    Base class for services requiring real-time control loops.

    Extends BaseService with a built-in control loop that runs at a specified frequency
    (default 60Hz). This is designed for hardware control where smooth execution at high
    frequencies is required (LED animations, servo control, audio synchronization, etc.).

    Architecture Pattern:
    - Event handlers SET STATE (high-level decisions)
    - Control loop EXECUTES STATE (smooth hardware updates)

    Example Use Cases:
    - LED brightness pulsing with speech amplitude (60fps)
    - Servo position interpolation (100Hz)
    - Beat-synchronized animations

    Usage:
        class MyHardwareService(RealtimeService):
            def __init__(self, event_bus):
                super().__init__("my_service", event_bus, loop_rate_hz=60)
                self._target_position = 0

            async def _on_start(self):
                await self.subscribe(EventTopics.COMMAND, self._handle_command)

            async def _handle_command(self, payload):
                # Event handler - just sets state
                self._target_position = payload["position"]

            async def _control_update(self):
                # Called 60x/sec - executes smooth movement
                await self._send_position_to_hardware(self._target_position)
    """

    def __init__(self, service_name: str, event_bus, loop_rate_hz: int = 60):
        """
        Initialize the realtime service.

        Args:
            service_name: Name of the service
            event_bus: Event bus instance
            loop_rate_hz: Control loop frequency in Hz (default: 60)
        """
        super().__init__(service_name, event_bus)
        self._loop_rate_hz = loop_rate_hz
        self._loop_task: Optional[asyncio.Task] = None
        self._loop_running = False
        self._loop_iteration = 0

        # Performance tracking
        self._loop_overruns = 0
        self._max_loop_time = 0.0

    async def start(self) -> None:
        """Start the service and its control loop."""
        if self._is_running:
            return

        # Start base service first
        await super().start()

        # Start control loop
        self._loop_running = True
        self._loop_task = asyncio.create_task(self._run_control_loop())
        self.logger.info(
            f"{self.__class__.__name__} control loop started at {self._loop_rate_hz}Hz"
        )

    async def stop(self) -> None:
        """Stop the control loop and service."""
        # Stop control loop first
        self._loop_running = False
        if self._loop_task:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
            self._loop_task = None

        # Log performance stats
        if self._loop_overruns > 0:
            self.logger.warning(
                f"Control loop had {self._loop_overruns} overruns "
                f"(max execution time: {self._max_loop_time*1000:.2f}ms)"
            )

        # Stop base service
        await super().stop()

    async def _run_control_loop(self) -> None:
        """
        Main control loop that runs at specified Hz.

        Calls _control_update() every frame and maintains precise timing.
        Tracks performance metrics to detect loop overruns.
        """
        import time

        interval = 1.0 / self._loop_rate_hz
        interval_ms = interval * 1000

        self.logger.debug(
            f"Control loop running: target={self._loop_rate_hz}Hz "
            f"(interval={interval_ms:.2f}ms)"
        )

        while self._loop_running:
            loop_start = time.time()

            try:
                # Call subclass implementation
                await self._control_update()

                # Track iteration count
                self._loop_iteration += 1

                # Performance monitoring
                loop_time = time.time() - loop_start
                self._max_loop_time = max(self._max_loop_time, loop_time)

                # Detect overruns (loop took longer than interval)
                if loop_time > interval:
                    self._loop_overruns += 1
                    if self._loop_overruns % 100 == 1:  # Log every 100th overrun
                        self.logger.warning(
                            f"Control loop overrun: {loop_time*1000:.2f}ms "
                            f"(target: {interval_ms:.2f}ms) "
                            f"[{self._loop_overruns} total overruns]"
                        )

            except asyncio.CancelledError:
                # Loop cancelled during shutdown
                break
            except Exception as e:
                self.logger.error(f"Error in control loop iteration {self._loop_iteration}: {e}")
                # Continue running despite errors

            # Sleep to maintain loop rate
            elapsed = time.time() - loop_start
            sleep_time = max(0, interval - elapsed)

            if sleep_time > 0:
                await asyncio.sleep(sleep_time)

    async def _control_update(self) -> None:
        """
        Control loop update called every frame.

        Override this method in your subclass to implement real-time control logic.
        This method should:
        - Read current state set by event handlers
        - Perform calculations (interpolation, filtering, etc.)
        - Send commands to hardware

        Example:
            async def _control_update(self):
                # Read state (set by event handlers)
                target_brightness = self._target_brightness

                # Calculate output (with smoothing)
                current = self._smooth_brightness(target_brightness)

                # Send to hardware
                await self._arduino.write_brightness(current)

        Note: Keep this method fast! It runs 60+ times per second.
        Avoid blocking operations, heavy computation, or slow I/O.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement _control_update()"
        )

    @property
    def loop_rate_hz(self) -> int:
        """Get the control loop frequency in Hz."""
        return self._loop_rate_hz

    @property
    def loop_iteration(self) -> int:
        """Get the current loop iteration count."""
        return self._loop_iteration

    @property
    def loop_overruns(self) -> int:
        """Get the number of loop overruns (when execution exceeded interval)."""
        return self._loop_overruns