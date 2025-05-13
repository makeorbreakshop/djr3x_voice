"""
Service Template for CantinaOS

This file serves as a standardized template for creating new CantinaOS services.
All services should follow this structure and naming conventions.
"""

import asyncio
import logging
from typing import Dict, Optional, Any, List, Callable

from ..base_service import BaseService
from ..event_topics import EventTopics
from ..event_payloads import (
    ServiceStatus,
    LogLevel,
    BaseEventPayload
)

class ServiceTemplate(BaseService):
    """
    Template for creating new CantinaOS services.
    
    Features:
    - Standard initialization pattern
    - Proper event subscription handling
    - Consistent naming conventions
    - Standardized lifecycle methods
    """
    
    def __init__(
        self,
        event_bus,
        config: Optional[Dict[str, Any]] = None,
        logger: Optional[logging.Logger] = None,
        name: str = "service_template"
    ):
        """Initialize the service.
        
        Args:
            event_bus: Event bus instance
            config: Optional configuration dictionary
            logger: Optional logger instance
            name: Service name for logging
        """
        # Always use a consistent service name for logging
        super().__init__(name, event_bus, logger)
        
        # Store configuration
        self._config = config or {}
        
        # Initialize state variables with underscore prefix
        self._some_state_variable = None
        
    async def _start(self) -> None:
        """Initialize the service (called by BaseService.start).
        
        All initialization that could fail should happen here, not in __init__.
        """
        # Set up connections, initialize resources
        await self._initialize_resources()
        
        # Set up event subscriptions
        await self._setup_subscriptions()
        
        self.logger.info("Service started successfully")
        
    async def _initialize_resources(self) -> None:
        """Initialize service resources."""
        # Initialize any resources needed by the service
        pass
        
    async def _setup_subscriptions(self) -> None:
        """Set up event subscriptions.
        
        IMPORTANT: All subscriptions must be wrapped in asyncio.create_task.
        """
        # Example subscription pattern - use this exact pattern for all subscriptions
        asyncio.create_task(self.subscribe(
            EventTopics.SOME_EVENT_TOPIC,
            self._handle_some_event
        ))
        self.logger.debug("Set up subscriptions")
        
    async def _stop(self) -> None:
        """Clean up resources (called by BaseService.stop)."""
        # Clean up any resources
        await self._cleanup_resources()
        self.logger.info("Service stopped")
        
    async def _cleanup_resources(self) -> None:
        """Clean up service resources."""
        # Clean up any resources initialized in _initialize_resources
        pass
        
    async def _handle_some_event(self, payload: Dict[str, Any]) -> None:
        """Handle an event.
        
        Args:
            payload: Event payload
        """
        # Process the event
        pass
        
    async def emit_event(self, topic: str, data: Dict[str, Any]) -> None:
        """Emit an event with proper error handling.
        
        Args:
            topic: Event topic
            data: Event data
        """
        try:
            await self.emit(topic, data)
        except Exception as e:
            self.logger.error(f"Error emitting event on topic {topic}: {e}") 