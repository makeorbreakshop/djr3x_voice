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
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union

from pydantic import BaseModel
from pyee.asyncio import AsyncIOEventEmitter

from .core.event_topics import EventTopics
from .event_payloads import (
    BaseEventPayload,
    HealthCheckConfig,
    LogLevel,
    ServiceStatus,
    ServiceStatusPayload,
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

    def __init__(
        self,
        service_name=None,
        event_bus=None,
        logger=None,
        health_config: Optional[Dict] = None,
    ):
        """Initialize the service.

        Args:
            service_name: The name of the service
            event_bus: The event bus to use
            logger: The logger to use
            health_config: Optional health check configuration dictionary
        """
        self._service_name = service_name or self.__class__.__name__.lower()
        self._event_bus = event_bus
        self._logger = logger
        self._is_running = False
        self._status = ServiceStatus.INITIALIZING
        self._event_handlers: Dict[str, List[Callable]] = {}  # topic -> [handlers]
        self._started = False
        self._start_time = None  # Track service start time for uptime calculation
        self._status_task = None  # Task for periodic status emission
        self._last_emitted_status = (
            None  # Track last emitted status to prevent duplicates
        )

        # Configure health check behavior
        self._health_config = HealthCheckConfig(**(health_config or {}))

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
            raise RuntimeError(
                "Event bus not set. Call set_event_bus() before starting the service."
            )

        # Record start time for uptime calculation
        self._start_time = datetime.now()

        # Emit starting event for race condition prevention
        await self.emit(
            EventTopics.SERVICE_STARTING,
            {
                "service_name": self._service_name,
                "timestamp": datetime.now().isoformat(),
            },
        )

        await self._start()
        self._is_running = True
        self._started = True
        self._status = ServiceStatus.RUNNING
        await self._emit_status(
            ServiceStatus.RUNNING, f"{self.__class__.__name__} started successfully"
        )

        # Start periodic status emission task if enabled
        if self._health_config.periodic_emission_enabled:
            self._status_task = asyncio.create_task(self._periodic_status_emission())

        # Subscribe to status requests
        await self.subscribe(
            EventTopics.SERVICE_STATUS_REQUEST, self._handle_status_request
        )

        # Emit ready event to indicate service is fully operational
        await self.emit(
            EventTopics.SERVICE_READY,
            {
                "service_name": self._service_name,
                "timestamp": datetime.now().isoformat(),
            },
        )

    async def stop(self) -> None:
        """Stop the service."""
        if not self._is_running:
            return

        # Stop periodic status task first
        if self._status_task:
            self._status_task.cancel()
            try:
                await self._status_task
            except asyncio.CancelledError:
                pass
            self._status_task = None

        await self._stop()
        await self._remove_subscriptions()
        self._is_running = False
        self._started = False
        self._status = ServiceStatus.STOPPED
        await self._emit_status(
            ServiceStatus.STOPPED, f"{self.__class__.__name__} stopped"
        )

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
            self.logger.debug(
                f"Event bus is None, skipping subscription removal for {self.__class__.__name__}"
            )
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

    async def _emit_status(
        self,
        status: ServiceStatus,
        message: str,
        severity: LogLevel = LogLevel.INFO,
        force_emit: bool = False,
    ) -> None:
        """Emit a service status event.

        Args:
            status: Service status enum value
            message: Status message
            severity: Log level severity
            force_emit: Force emission even if status hasn't changed
        """
        # Only emit if status has actually changed or if forced (respecting configuration)
        if (
            not force_emit
            and self._health_config.emit_only_on_state_change
            and self._last_emitted_status == status
        ):
            # Status hasn't changed, skip emission to reduce noise
            return

        self._status = status
        self._last_emitted_status = status

        # Log the status change
        log_method = getattr(self.logger, severity.lower())
        log_method(message)

        # Calculate uptime for running services
        uptime = "0:00:00"
        if hasattr(self, "_start_time") and self._start_time:
            uptime_seconds = (datetime.now() - self._start_time).total_seconds()
            hours = int(uptime_seconds // 3600)
            minutes = int((uptime_seconds % 3600) // 60)
            seconds = int(uptime_seconds % 60)
            uptime = f"{hours}:{minutes:02d}:{seconds:02d}"

        # Emit the status event using proper EventTopic and WebBridge-compatible format
        await self.emit(
            EventTopics.SERVICE_STATUS_UPDATE,
            {
                "service_name": self._service_name,
                "status": status.value if hasattr(status, "value") else str(status),
                "uptime": uptime,
                "last_update": datetime.now().isoformat(),
                "message": message,
                "severity": (
                    severity.value if hasattr(severity, "value") else str(severity)
                ),
            },
        )

    async def _periodic_status_emission(self) -> None:
        """Periodically emit status updates for services that started before WebBridge."""
        while self._is_running:
            try:
                # Wait based on configuration (default 5 minutes)
                await asyncio.sleep(
                    self._health_config.periodic_emission_interval_seconds
                )

                # Only emit if we're actually running
                if self._is_running and self._status == ServiceStatus.RUNNING:
                    await self._emit_status(
                        ServiceStatus.RUNNING,
                        f"{self.__class__.__name__} is online",
                        severity=self._health_config.periodic_emission_log_level,  # Use configured log level
                        force_emit=True,  # Force emission for periodic updates
                    )
            except asyncio.CancelledError:
                break
            except Exception as e:
                # Log error but don't stop the task
                if self.logger:
                    self.logger.error(f"Error in periodic status emission: {e}")
                await asyncio.sleep(30)  # Wait before retrying

    async def _handle_status_request(self, payload: Any) -> None:
        """Handle requests for current service status."""
        try:
            if self._is_running and self._status == ServiceStatus.RUNNING:
                await self._emit_status(
                    ServiceStatus.RUNNING,
                    f"{self.__class__.__name__} is online",
                    severity=self._health_config.status_request_log_level,  # Use configured log level
                    force_emit=True,  # Force emission for status requests
                )
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error handling status request: {e}")

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
                field_names = [
                    attr
                    for attr in model_attrs
                    if not attr.startswith("_") and not callable(getattr(payload, attr))
                ]
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
            event: Event name/topic
            payload: Event payload
        """
        if not self._event_bus:
            raise RuntimeError("Event bus not set")

        # Debug payload if it's going to CLI_RESPONSE
        if event == "cli_response":
            self.debug_payload(payload, prefix="emit: ")

        # Convert Pydantic models to dictionaries if needed
        if hasattr(payload, "model_dump"):
            self.logger.debug(f"Converting Pydantic model to dict for event {event}")
            payload = payload.model_dump()
        elif hasattr(payload, "dict"):
            self.logger.debug(
                f"Converting Pydantic model to dict using .dict() for event {event}"
            )
            payload = payload.dict()

        # The emit method of pyee.AsyncIOEventEmitter returns a boolean, not a coroutine
        self._event_bus.emit(event, payload)

    async def subscribe(self, event: str, handler: Callable) -> None:
        """Subscribe to an event on the event bus.

        Args:
            event: Event name/topic to subscribe to
            handler: Callback function to handle the event
        """
        if not self._event_bus:
            raise RuntimeError("Event bus not set")

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

    async def emit_error_response(self, topic: str, payload: BaseEventPayload) -> None:
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

    async def debug_log(
        self, level: str, message: str, details: Optional[Dict[str, Any]] = None
    ) -> None:
        """Send a debug log message via the event system."""
        await self.emit(
            EventTopics.DEBUG_LOG,
            {
                "level": level,
                "component": self._service_name,
                "message": message,
                "details": details,
            },
        )

    async def debug_trace_command(
        self,
        command: str,
        execution_time_ms: float,
        status: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Trace a command execution via the event system."""
        await self.emit(
            EventTopics.DEBUG_COMMAND_TRACE,
            {
                "command": command,
                "service": self._service_name,
                "execution_time_ms": execution_time_ms,
                "status": status,
                "details": details,
            },
        )

    async def debug_performance_metric(
        self,
        metric_name: str,
        value: float,
        unit: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Report a performance metric via the event system."""
        await self.emit(
            EventTopics.DEBUG_PERFORMANCE,
            {
                "metric_name": metric_name,
                "value": value,
                "unit": unit,
                "component": self._service_name,
                "details": details,
            },
        )

    async def debug_state_transition(
        self, from_state: str, to_state: str, details: Optional[Dict[str, Any]] = None
    ) -> None:
        """Report a state transition for debugging."""
        await self.emit(
            EventTopics.DEBUG_STATE_TRANSITION,
            {
                "component": self._service_name,
                "from_state": from_state,
                "to_state": to_state,
                "details": details,
            },
        )
