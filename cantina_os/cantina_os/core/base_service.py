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