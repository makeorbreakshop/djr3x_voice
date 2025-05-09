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

from .event_topics import EventTopics
from .event_payloads import (
    ServiceStatus,
    LogLevel,
    ServiceStatusPayload,
    BaseEventPayload
)

class ServiceStatus(str, Enum):
    """Service status enumeration."""
    INITIALIZING = "INITIALIZING"
    RUNNING = "RUNNING"
    ERROR = "ERROR"
    STOPPED = "STOPPED"
    DEGRADED = "DEGRADED"

class LogLevel(str, Enum):
    """Log level enumeration."""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"

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
            
        await self._start()
        self._is_running = True
        self._started = True
        self._status = ServiceStatus.RUNNING
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

    async def emit(self, event: str, payload: Any) -> None:
        """Emit an event on the event bus.
        
        Args:
            event: Event name/topic
            payload: Event payload
        """
        if not self._event_bus:
            raise RuntimeError("Event bus not set")
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
        
        # Add the handler to the event bus
        self._event_bus.on(event, handler)

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