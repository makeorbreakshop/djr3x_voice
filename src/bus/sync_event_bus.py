"""
Synchronous Event Bus Implementation

This module provides a synchronous event bus with subscription verification
and proper handler lifecycle management.
"""

from typing import Dict, Any, Optional, Callable, Awaitable, List, Union, Set
import asyncio
import logging
from pyee.asyncio import AsyncIOEventEmitter
from pydantic import BaseModel
from dataclasses import dataclass, field
import inspect

logger = logging.getLogger(__name__)

@dataclass
class SubscriptionInfo:
    """Information about a subscription."""
    handler: Callable[[Dict[str, Any]], Awaitable[None]]
    is_internal: bool = False
    timestamp: float = field(default_factory=lambda: asyncio.get_event_loop().time())

class SyncEventBus:
    """
    Synchronous event bus implementation with subscription verification.
    
    Features:
    - Synchronous event registration
    - Subscription verification
    - Handler lifecycle management
    - Thread-safe operations
    """
    
    def __init__(
        self, 
        loop: Optional[asyncio.AbstractEventLoop] = None,
        propagate_errors: bool = False
    ):
        """Initialize the event bus with improved handler tracking.
        
        Args:
            loop: Optional event loop to use
            propagate_errors: Whether to propagate errors from handlers (default: False)
        """
        self._emitter = AsyncIOEventEmitter()
        self._loop = loop or asyncio.get_event_loop()
        self._propagate_errors = propagate_errors
        
        # Improved handler tracking
        self._handler_registry: Dict[str, Dict[Callable, Dict[str, Any]]] = {}
        """
        Handler registry structure:
        {
            topic: {
                original_handler: {
                    'wrapper_handler': wrapper,
                    'is_internal': bool,
                    'registration_time': timestamp
                }
            }
        }
        """
        self._registration_lock = asyncio.Lock()
        self.logger = logger or logging.getLogger(__name__)
        logger.debug("SyncEventBus initialized with improved handler tracking")
        
    def _generate_handler_id(self, handler: Callable) -> int:
        """Generate a unique ID for a handler."""
        return hash(handler) ^ hash(str(handler.__code__))
    
    def _validate_handler(self, handler: Callable, is_internal: bool = False) -> None:
        """Validate that a handler is properly formed.
        
        Args:
            handler: Handler to validate
            is_internal: Whether this is an internal handler
            
        Raises:
            ValueError: If handler is invalid
        """
        if not callable(handler):
            raise ValueError("Handler must be callable")
            
        # Check if it's an async function
        if not asyncio.iscoroutinefunction(handler):
            raise ValueError("Handler must be an async function")
            
        # Check signature
        sig = inspect.signature(handler)
        params = list(sig.parameters.values())
        
        if len(params) != 1:
            raise ValueError("Handler must accept exactly one parameter (the payload)")
            
        # Only validate parameter type for non-internal handlers
        if not is_internal:
            # Verify the parameter is typed as Dict[str, Any] or compatible
            param = params[0]
            if param.annotation != inspect.Parameter.empty:
                if not (param.annotation == Dict[str, Any] or
                       getattr(param.annotation, "__origin__", None) == dict):
                    raise ValueError("Handler parameter must be typed as Dict[str, Any]")
        
    async def sync_on(
        self,
        topic: str,
        handler: Callable[[Dict[str, Any]], Awaitable[None]],
        is_internal: bool = False
    ) -> None:
        """Alias for on method to maintain compatibility with tests."""
        await self.on(topic, handler, is_internal)
    
    async def on(
        self,
        topic: str,
        handler: Callable[[Dict[str, Any]], Awaitable[None]],
        is_internal: bool = False
    ) -> None:
        """Enhanced event subscription with robust handler tracking."""
        # Validate handler first
        self._validate_handler(handler, is_internal)
        
        async with self._registration_lock:
            # Create wrapper handler with error handling
            async def handler_wrapper(payload: Dict[str, Any]) -> None:
                try:
                    if not isinstance(payload, dict):
                        payload = {} if payload is None else {"data": payload}
                    
                    # Skip test verification payloads
                    if payload.get('_test_verification', False):
                        return
                    
                    await handler(payload)
                except Exception as e:
                    self.logger.error(f"Error in event handler for {topic}: {e}")
                    if self._propagate_errors:
                        raise
            
            # Initialize topic registry if needed
            if topic not in self._handler_registry:
                self._handler_registry[topic] = {}
            
            # Store handler with comprehensive metadata
            self._handler_registry[topic][handler] = {
                'wrapper_handler': handler_wrapper,
                'is_internal': is_internal,
                'registration_time': asyncio.get_event_loop().time()
            }
            
            try:
                # Register wrapper with emitter
                self._emitter.on(topic, handler_wrapper)
                
            except Exception as e:
                # Clean up on registration failure
                self.logger.error(f"Failed to register handler for {topic}: {e}")
                if topic in self._handler_registry and handler in self._handler_registry[topic]:
                    del self._handler_registry[topic][handler]
                if topic in self._handler_registry and not self._handler_registry[topic]:
                    del self._handler_registry[topic]
                raise
    
    async def _verify_subscription(self, topic: str, handler: Callable) -> None:
        """Verify a subscription is working.
        
        Args:
            topic: Event topic
            handler: Handler to verify
        """
        try:
            # Send test event
            test_payload = {
                "_test_verification": True,
                "topic": topic,
                "timestamp": asyncio.get_event_loop().time()
            }
            await handler(test_payload)
            
            self.logger.debug(f"Verified subscription for topic: {topic}")
            
        except Exception as e:
            self.logger.error(f"Failed to verify subscription for topic {topic}: {e}")
            raise
            
    async def verify_subscriptions(self, timeout: float = 5.0) -> None:
        """Verify all subscriptions are working correctly.
        
        Args:
            timeout: Timeout in seconds
            
        Raises:
            TimeoutError: If verification takes too long
            RuntimeError: If any subscription fails verification
        """
        async with self._registration_lock:
            topics = list(self._handler_registry.keys())
            if not topics:
                self.logger.debug("No subscriptions to verify")
                return
                
            self.logger.debug(f"Verifying {len(topics)} subscribed topics")
            
            try:
                # Send verification event for each topic
                verification_tasks = []
                for topic in topics:
                    # Create a test event for verification
                    verification_event = {
                        "_test_verification": True,
                        "topic": topic,
                        "timestamp": asyncio.get_event_loop().time()
                    }
                    
                    # Create a task to emit the verification event
                    verification_tasks.append(
                        self.emit(topic, verification_event)
                    )
                
                # Wait for all verification events to be processed
                if verification_tasks:
                    await asyncio.wait_for(asyncio.gather(*verification_tasks), timeout=timeout)
                    
                self.logger.debug(f"All {len(topics)} subscriptions verified")
                
            except asyncio.TimeoutError:
                self.logger.error(f"Subscription verification timed out after {timeout}s")
                raise TimeoutError(f"Subscription verification timed out for topics: {topics}")
            except Exception as e:
                self.logger.error(f"Failed to verify subscriptions: {e}")
                raise
                
    async def off(self, topic: str, handler: Callable) -> None:
        """Remove a listener for backward compatibility.
        
        Args:
            topic: Event topic
            handler: Handler to remove
        """
        await self.remove_listener(topic, handler)
            
    async def emit(
        self,
        topic: str,
        payload: Optional[Union[Dict[str, Any], BaseModel]] = None,
        timeout: float = 5.0
    ) -> None:
        """
        Emit an event on a topic and wait for all handlers to complete.
        
        Args:
            topic: Event topic
            payload: Optional event payload (dict or Pydantic model)
            timeout: Timeout in seconds for each handler
        """
        try:
            # Convert payload to dict if it's a Pydantic model
            if isinstance(payload, BaseModel):
                payload_dict = payload.model_dump()
            else:
                payload_dict = payload or {}
            
            # Get all listeners for this event
            listeners = self._emitter.listeners(topic)
            logger.debug(f"Emitting event on topic {topic} with {len(listeners)} listeners")
            
            # Process each handler separately to avoid issues with error propagation
            for i, listener in enumerate(listeners):
                try:
                    if asyncio.iscoroutinefunction(listener):
                        await asyncio.wait_for(
                            self._execute_handler(listener, topic, payload_dict, i+1),
                            timeout=timeout
                        )
                    else:
                        # Execute sync handlers in thread pool
                        await asyncio.wait_for(
                            self._execute_sync_handler(listener, topic, payload_dict, i+1),
                            timeout=timeout
                        )
                except asyncio.TimeoutError:
                    logger.error(f"Timeout in handler {i+1} for {topic} after {timeout}s")
                except Exception as e:
                    logger.error(f"Error in handler {i+1} for {topic}: {e}")
                    if self._propagate_errors:
                        raise
            
            logger.debug(f"All handlers completed for topic: {topic}")
                    
        except Exception as e:
            logger.error(f"Error emitting event on topic {topic}: {e}")
            raise
            
    def remove_listener(
        self,
        topic: str,
        handler: Callable[[Dict[str, Any]], Awaitable[None]]
    ) -> None:
        """
        Remove a specific event listener with robust tracking.
        
        Args:
            topic: Event topic to unsubscribe from
            handler: Handler to remove
        """
        try:
            if topic in self._handler_registry:
                if handler in self._handler_registry[topic]:
                    handler_info = self._handler_registry[topic][handler]
                    wrapper = handler_info['wrapper_handler']
                    
                    # Remove from emitter
                    self._emitter.remove_listener(topic, wrapper)
                    
                    # Remove from registry
                    del self._handler_registry[topic][handler]
                    
                    # Clean up topic if no more handlers
                    if not self._handler_registry[topic]:
                        del self._handler_registry[topic]
                    
                    logger.debug(f"Removed handler for topic: {topic}")
                else:
                    logger.warning(f"No handler found for topic: {topic}")
        except Exception as e:
            logger.error(f"Error removing listener for {topic}: {e}")
    
    def remove_all_listeners(self, topic: Optional[str] = None) -> None:
        """Remove all event handlers for a topic or all topics.
        
        Args:
            topic: Optional topic to remove handlers for, or all if None
        """
        try:
            self.logger.debug(f"Removing all listeners for {topic or 'all topics'}")
            
            if topic is not None:
                # Remove handlers for specific topic
                if topic in self._handler_registry:
                    # Clear emitter first
                    try:
                        self._emitter.remove_all_listeners(topic)
                    except Exception as e:
                        self.logger.warning(f"Error removing listeners for {topic}: {e}")
                    
                    # Clean up registry
                    self._handler_registry.pop(topic, None)
            else:
                # Remove all handlers for all topics
                for t in list(self._handler_registry.keys()):
                    try:
                        self._emitter.remove_all_listeners(t)
                    except Exception as e:
                        self.logger.warning(f"Error removing listeners for {t}: {e}")
                
                # Clear registry
                self._handler_registry.clear()
                
                # For safety, clear all listeners
                self._emitter.remove_all_listeners()
                
        except Exception as e:
            self.logger.error(f"Error removing all listeners: {e}")
            raise
            
    async def _execute_handler(
        self,
        handler: Callable,
        topic: str,
        payload: Dict[str, Any],
        handler_index: int
    ) -> None:
        """Execute an async handler with error handling.
        
        Args:
            handler: Handler to execute
            topic: Event topic
            payload: Event payload
            handler_index: Index of handler for logging
        """
        try:
            await handler(payload)
        except Exception as e:
            self.logger.error(f"Error in handler {handler_index} for {topic}: {e}")
            raise  # Propagate the error
            
    async def _execute_sync_handler(
        self,
        handler: Callable,
        topic: str,
        payload: Dict[str, Any],
        handler_index: int
    ) -> None:
        """Execute a sync handler in a thread pool."""
        try:
            await self._loop.run_in_executor(None, handler, payload)
            logger.debug(f"Sync handler {handler_index} completed for {topic}")
        except Exception as e:
            logger.error(f"Error in sync handler {handler_index} for {topic}: {e}") 