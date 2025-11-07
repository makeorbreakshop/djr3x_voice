"""
Base Service Class

Provides the foundation for all services with proper event bus integration
and lifecycle management.
"""

import asyncio
import logging
from typing import Dict, Any, Optional, Callable, Awaitable, Union, Set
from pydantic import BaseModel

from ..bus.sync_event_bus import SyncEventBus
from ..models.service_status import ServiceStatus, ServiceStatusPayload

logger = logging.getLogger(__name__)

class BaseService:
    """
    Base class for all services with robust event handling.
    
    Features:
    1. Synchronous event registration
    2. Proper subscription lifecycle management
    3. Graceful startup/shutdown
    4. Status tracking and reporting
    """
    
    def __init__(
        self,
        service_name: str,
        event_bus: SyncEventBus,
        logger: Optional[logging.Logger] = None
    ):
        """
        Initialize the service.
        
        Args:
            service_name: Unique name for this service
            event_bus: SyncEventBus instance
            logger: Optional custom logger
        """
        self.service_name = service_name
        self.event_bus = event_bus
        self.logger = logger or logging.getLogger(service_name)
        self._status = ServiceStatus.INITIALIZING
        self._started = False
        self._topic_handlers: Dict[str, Set[Callable]] = {}  # Track handlers per topic
        self._handler_wrappers: Dict[Callable, Callable] = {}  # Track handler wrappers
        
    async def subscribe(
        self,
        topic: str,
        handler: Callable[[Dict[str, Any]], Awaitable[None]]
    ) -> None:
        """
        Subscribe to an event topic with proper tracking.
        
        Args:
            topic: Event topic to subscribe to
            handler: Async callback function to handle events
            
        Raises:
            ValueError: If handler is invalid
        """
        try:
            # Validate handler is async
            if not asyncio.iscoroutinefunction(handler):
                raise ValueError("Handler must be an async function")
                
            # Create wrapper to maintain consistent error handling
            async def handler_wrapper(payload: Dict[str, Any]) -> None:
                try:
                    await handler(payload)
                except Exception as e:
                    self.logger.error(f"Error in handler for {topic}: {e}")
                    raise  # Propagate the error
                    
            # Store mapping between original handler and wrapper
            self._handler_wrappers[handler] = handler_wrapper
            
            # Track handler for this topic
            if topic not in self._topic_handlers:
                self._topic_handlers[topic] = set()
            self._topic_handlers[topic].add(handler)
            
            # Register with event bus
            await self.event_bus.on(topic, handler_wrapper)
            self.logger.debug(f"Subscribed to {topic}")
            
        except Exception as e:
            self.logger.error(f"Error subscribing to {topic}: {e}")
            # Clean up tracking on failure
            if handler in self._handler_wrappers:
                del self._handler_wrappers[handler]
            if topic in self._topic_handlers:
                self._topic_handlers[topic].discard(handler)
                if not self._topic_handlers[topic]:
                    del self._topic_handlers[topic]
            raise
            
    async def unsubscribe(
        self,
        topic: str,
        handler: Callable[[Dict[str, Any]], Awaitable[None]]
    ) -> None:
        """
        Unsubscribe from an event topic.
        
        Args:
            topic: Event topic to unsubscribe from
            handler: Handler to remove
        """
        try:
            # Get the wrapper for this handler
            wrapper = self._handler_wrappers.get(handler)
            if wrapper:
                # Remove from event bus
                self.event_bus.remove_listener(topic, wrapper)
                # Clean up tracking
                del self._handler_wrappers[handler]
                if topic in self._topic_handlers:
                    self._topic_handlers[topic].discard(handler)
                    if not self._topic_handlers[topic]:
                        del self._topic_handlers[topic]
                self.logger.debug(f"Unsubscribed from {topic}")
            else:
                self.logger.warning(f"No wrapper found for handler on topic: {topic}")
                
        except Exception as e:
            self.logger.error(f"Error unsubscribing from {topic}: {e}")
            raise
            
    async def _cleanup_subscriptions(self) -> None:
        """Clean up all subscriptions for this service."""
        try:
            self.logger.debug("Cleaning up subscriptions")
            
            # Create copy of topics to avoid modification during iteration
            topics = list(self._topic_handlers.keys())
            
            for topic in topics:
                # Create copy of handlers to avoid modification during iteration
                handlers = list(self._topic_handlers[topic])
                for handler in handlers:
                    await self.unsubscribe(topic, handler)
                    
            # Verify cleanup
            if self._topic_handlers or self._handler_wrappers:
                self.logger.warning("Some subscriptions were not properly cleaned up")
                
        except Exception as e:
            self.logger.error(f"Error cleaning up subscriptions: {e}")
            raise
            
    async def start(self) -> None:
        """Start the service with proper initialization."""
        if self._started:
            return
            
        try:
            self._status = ServiceStatus.STARTING
            await self._emit_status(ServiceStatus.STARTING)
            
            # Initialize service
            await self._start()
            
            # Verify subscriptions
            await self._verify_subscriptions()
            
            self._started = True
            self._status = ServiceStatus.RUNNING
            await self._emit_status(ServiceStatus.RUNNING)
            
        except Exception as e:
            self.logger.error(f"Error starting {self.service_name}: {e}")
            self._status = ServiceStatus.ERROR
            await self._emit_status(ServiceStatus.ERROR, str(e))
            raise
            
    async def stop(self) -> None:
        """Stop the service with proper cleanup."""
        if not self._started:
            return
            
        try:
            self._status = ServiceStatus.STOPPING
            await self._emit_status(ServiceStatus.STOPPING)
            
            # Stop service
            await self._stop()
            
            # Clean up subscriptions
            await self._cleanup_subscriptions()
            
            self._started = False
            self._status = ServiceStatus.STOPPED
            await self._emit_status(ServiceStatus.STOPPED)
            
        except Exception as e:
            self.logger.error(f"Error stopping {self.service_name}: {e}")
            self._status = ServiceStatus.ERROR
            await self._emit_status(ServiceStatus.ERROR, str(e))
            raise
            
    async def _emit_status(
        self,
        status: ServiceStatus,
        message: Optional[str] = None
    ) -> None:
        """Emit service status update."""
        try:
            payload = ServiceStatusPayload(
                service=self.service_name,
                status=status,
                message=message or f"Service {status.value}"
            ).model_dump()  # Use model_dump instead of dict
            await self.event_bus.emit("service/status", payload)
        except Exception as e:
            self.logger.error(f"Error emitting status: {e}")
            
    async def _start(self) -> None:
        """Service-specific startup logic. Override in derived classes."""
        pass
        
    async def _stop(self) -> None:
        """Service-specific shutdown logic. Override in derived classes."""
        pass
        
    async def _verify_subscriptions(self) -> None:
        """Verify all subscriptions are working."""
        try:
            self.logger.debug("Verifying subscriptions")
            
            # Add small delay to ensure subscriptions are ready
            await asyncio.sleep(0.1)
            
            # Verify each subscription
            for topic in self._topic_handlers:
                for handler in self._topic_handlers[topic]:
                    wrapper = self._handler_wrappers.get(handler)
                    if not wrapper:
                        raise RuntimeError(f"No wrapper found for handler on topic: {topic}")
                        
            self.logger.debug("All subscriptions verified")
            
        except Exception as e:
            self.logger.error(f"Error verifying subscriptions: {e}")
            raise 