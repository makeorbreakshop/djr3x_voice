"""
Base Service Class

This module provides the base class for all services in the system.
"""

import logging
from typing import Dict, Any, Optional, Callable, Awaitable

from .bus.sync_event_bus import SyncEventBus
from .models.service_status import ServiceStatus
from .models.payloads import ServiceStatusPayload
from .event_topics import EventTopics

class BaseService:
    """
    Base class for all services.
    
    Features:
    - Event bus integration
    - Service lifecycle management
    - Status reporting
    - Subscription management
    """
    
    def __init__(
        self,
        service_name: str,
        event_bus: SyncEventBus,
        logger: Optional[logging.Logger] = None
    ):
        """Initialize the service.
        
        Args:
            service_name: Name of the service
            event_bus: Event bus instance
            logger: Optional logger instance
        """
        self.service_name = service_name
        self.event_bus = event_bus
        self.logger = logger or logging.getLogger(service_name)
        
        self._started = False
        self._status = ServiceStatus.STOPPED
        
    async def start(self) -> None:
        """Start the service."""
        if self._started:
            return
            
        try:
            await self._emit_status(ServiceStatus.STARTING, "Service starting")
            await self._start()
            self._started = True
            self._status = ServiceStatus.RUNNING
            await self._emit_status(ServiceStatus.RUNNING, f"{self.service_name} started successfully")
            
        except Exception as e:
            self.logger.error(f"Error starting {self.service_name}: {e}")
            await self._emit_status(
                ServiceStatus.ERROR,
                f"Failed to start: {str(e)}",
                severity="ERROR"
            )
            raise
            
    async def stop(self) -> None:
        """Stop the service."""
        if not self._started:
            return
            
        try:
            await self._emit_status(ServiceStatus.STOPPING, "Service stopping")
            await self._stop()
            self._started = False
            self._status = ServiceStatus.STOPPED
            await self._emit_status(ServiceStatus.STOPPED, f"{self.service_name} stopped successfully")
            
        except Exception as e:
            self.logger.error(f"Error stopping {self.service_name}: {e}")
            await self._emit_status(
                ServiceStatus.ERROR,
                f"Failed to stop: {str(e)}",
                severity="ERROR"
            )
            raise
            
    async def _start(self) -> None:
        """Initialize the service.
        
        Override this method to implement service-specific initialization.
        """
        pass
        
    async def _stop(self) -> None:
        """Clean up resources.
        
        Override this method to implement service-specific cleanup.
        """
        pass
        
    async def subscribe(
        self,
        topic: str,
        handler: Callable[[Dict[str, Any]], Awaitable[None]]
    ) -> None:
        """Subscribe to an event topic.
        
        Args:
            topic: Event topic
            handler: Async callback function
        """
        self.logger.debug(f"Subscribing to topic: {topic}")
        await self.event_bus.on(topic, handler)
        
    async def emit(
        self,
        topic: str,
        payload: Dict[str, Any]
    ) -> None:
        """Emit an event.
        
        Args:
            topic: Event topic
            payload: Event payload
        """
        self.logger.debug(f"Emitting event on topic: {topic}")
        await self.event_bus.emit(topic, payload)
        
    async def _emit_status(
        self,
        status: ServiceStatus,
        message: str,
        severity: Optional[str] = None
    ) -> None:
        """Emit a service status update event.
        
        Args:
            status: Service status
            message: Status message
            severity: Optional severity level
        """
        # Create payload as dictionary
        payload = {
            "service_name": self.service_name,
            "status": status.value,  # Use the enum value
            "message": message,
            "severity": severity
        }
        
        await self.emit(
            EventTopics.SERVICE_STATUS_UPDATE,
            payload
        )
        
    @property
    def status(self) -> ServiceStatus:
        """Get the current service status."""
        return self._status 