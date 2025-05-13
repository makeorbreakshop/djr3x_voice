"""
Standardized Service Template for CantinaOS

This module provides a standardized template that all CantinaOS services should follow.
Copy this template when creating new services and follow the patterns exactly.
"""

import asyncio
import logging
from typing import Dict, Any, List, Callable, Optional, Union

from pyee.asyncio import AsyncIOEventEmitter
from pydantic import BaseModel

from .base_service import BaseService
from .event_topics import EventTopics
from .event_payloads import ServiceStatus, LogLevel, BaseEventPayload

class ServiceConfig(BaseModel):
    """Configuration for a service."""
    service_name: str
    # Add service-specific configuration parameters here
    # with proper type annotations and default values

class StandardService(BaseService):
    """
    Standardized template for all CantinaOS services.
    
    Follow this pattern precisely when creating new services:
    1. Always use _attribute for internal state
    2. Provide property accessors for attributes that need external access
    3. Override _start and _stop, not start and stop
    4. Use async subscriptions with asyncio.create_task
    5. Clean up resources properly in _stop
    """
    
    def __init__(
        self,
        event_bus: AsyncIOEventEmitter,
        config: Dict[str, Any],
        logger: Optional[logging.Logger] = None,
        name: str = None
    ):
        """Initialize the service.
        
        Args:
            event_bus: Event bus instance
            config: Configuration dictionary
            logger: Optional logger instance
            name: Optional service name override
        """
        # Initialize base service with core components
        super().__init__(
            service_name=name or self.__class__.__name__.lower(),
            event_bus=event_bus,
            logger=logger
        )
        
        # Store configuration
        self._config = config
        
        # Initialize service-specific state
        self._initialized = False
        
        # Log initialization
        self.logger.debug(f"{self.__class__.__name__} initialized")
    
    async def _start(self) -> None:
        """Initialize the service (override this in your service).
        
        This is called by the base service's start() method.
        Do not override start() directly.
        """
        self.logger.info(f"Starting {self.__class__.__name__}")
        
        # Set up event subscriptions
        await self._setup_subscriptions()
        
        # Initialize resources
        await self._initialize_resources()
        
        # Mark as initialized
        self._initialized = True
        self.logger.info(f"{self.__class__.__name__} started successfully")
    
    async def _setup_subscriptions(self) -> None:
        """Set up event subscriptions.
        
        IMPORTANT: Always use asyncio.create_task to wrap subscriptions
        to avoid blocking the start method.
        """
        # Example subscription pattern
        asyncio.create_task(self.subscribe(
            EventTopics.EXAMPLE_TOPIC,
            self._handle_example_event
        ))
        self.logger.debug(f"Set up subscriptions for {self.__class__.__name__}")
    
    async def _initialize_resources(self) -> None:
        """Initialize resources needed by this service.
        
        Override this in your service if needed.
        """
        pass
    
    async def _stop(self) -> None:
        """Clean up resources (override this in your service).
        
        This is called by the base service's stop() method.
        Do not override stop() directly.
        """
        self.logger.info(f"Stopping {self.__class__.__name__}")
        
        # Clean up resources
        await self._cleanup_resources()
        
        # Reset state
        self._initialized = False
        self.logger.info(f"{self.__class__.__name__} stopped successfully")
    
    async def _cleanup_resources(self) -> None:
        """Clean up resources used by this service.
        
        Override this in your service if needed.
        """
        pass
    
    async def _handle_example_event(self, payload: Union[BaseEventPayload, Dict[str, Any]]) -> None:
        """Example event handler method.
        
        Args:
            payload: Event payload which could be a Pydantic model or dict
        """
        try:
            # Log event receipt
            self.logger.debug(f"Received event with payload: {payload}")
            
            # Process event
            # Your event handling logic here
            
            # Emit response event if needed
            await self.emit(
                EventTopics.EXAMPLE_RESPONSE,
                {"message": "Event processed successfully"}
            )
            
        except Exception as e:
            # Log error
            error_msg = f"Error handling event: {str(e)}"
            self.logger.error(error_msg)
            
            # Emit error response
            await self.emit_error_response(
                EventTopics.EXAMPLE_RESPONSE,
                {
                    "error": error_msg,
                    "is_error": True
                }
            )
    
    @property
    def is_initialized(self) -> bool:
        """Check if the service is initialized."""
        return self._initialized 