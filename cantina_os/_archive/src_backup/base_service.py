"""Base service class for CantinaOS."""

import logging
from typing import Any, Dict, Optional
import asyncio

class BaseService:
    """Base class for all CantinaOS services."""
    
    def __init__(self, name: str, event_bus: Any):
        """Initialize the service.
        
        Args:
            name: Service name
            event_bus: Event bus instance for communication
        """
        self.name = name
        self._event_bus = event_bus
        self.logger = logging.getLogger(name)
        
    async def _start(self) -> None:
        """Start the service. Override in subclasses."""
        pass
        
    async def _stop(self) -> None:
        """Stop the service. Override in subclasses."""
        pass
        
    async def _cleanup(self) -> None:
        """Clean up resources. Override in subclasses."""
        pass
        
    async def _emit_event(self, topic: str, payload: Dict[str, Any]) -> None:
        """Emit an event on the event bus.
        
        Args:
            topic: Event topic
            payload: Event payload
        """
        await self._event_bus.emit(topic, payload)

    async def start(self) -> None:
        """Start the service."""
        if self._is_running:
            return
            
        try:
            self._is_running = True
            await self._emit_event(EventTopics.SERVICE_STARTED, {
                "service_name": self.name
            })
        except Exception as e:
            self.logger.error(f"Error starting service: {e}")
            self._is_running = False
            await self._emit_event(EventTopics.SERVICE_ERROR, {
                "service_name": self.name,
                "error": str(e)
            })
            raise
            
    async def stop(self) -> None:
        """Stop the service."""
        if not self._is_running:
            return
            
        try:
            self._is_running = False
            await self._emit_event(EventTopics.SERVICE_STOPPED, {
                "service_name": self.name
            })
        except Exception as e:
            self.logger.error(f"Error stopping service: {e}")
            await self._emit_event(EventTopics.SERVICE_ERROR, {
                "service_name": self.name,
                "error": str(e)
            })
            raise
            
    async def set_state(self, state: str) -> None:
        """Set the service state and emit a state change event."""
        if state == self._current_state:
            return
            
        self._current_state = state
        await self._emit_event(EventTopics.SERVICE_STATE_CHANGED, {
            "service_name": self.name,
            "state": state
        })
        
    @property
    def is_running(self) -> bool:
        """Return whether the service is running."""
        return self._is_running
        
    @property
    def current_state(self) -> Optional[str]:
        """Return the current service state."""
        return self._current_state 