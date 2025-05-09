"""Event bus for CantinaOS."""
import asyncio
import logging
from typing import Any, Dict, Optional, Callable, Awaitable, List, Union, Set
from pyee.asyncio import AsyncIOEventEmitter
from pydantic import BaseModel

from .event_topics import EventTopics

logger = logging.getLogger(__name__)

class EventBus:
    """Event bus for system-wide event communication."""
    
    def __init__(self):
        """Initialize the event bus."""
        self._emitter = AsyncIOEventEmitter()
        self._loop = asyncio.get_running_loop()
        self._handler_wrappers: Dict[str, Dict[Callable, Callable]] = {}  # Map topic -> {original -> wrapper}
        self._topic_handlers: Dict[str, Set[Callable]] = {}  # Track handlers per topic
        logger.debug("EventBus initialized")
        
    async def on(self, topic: str, handler: Callable[[Dict[str, Any]], Awaitable[None]]) -> None:
        """Subscribe to an event topic.
        
        Args:
            topic: Event topic to subscribe to
            handler: Async callback function to handle events
        """
        logger.debug(f"Adding listener for topic: {topic}")
        
        # Create wrapper to handle payload properly
        async def handler_wrapper(payload: Dict[str, Any]) -> None:
            try:
                await handler(payload)
            except Exception as e:
                logger.error(f"Error in event handler for {topic}: {e}")
                
        # Store mapping between original handler and wrapper in a nested dict by topic
        if topic not in self._handler_wrappers:
            self._handler_wrappers[topic] = {}
        self._handler_wrappers[topic][handler] = handler_wrapper
        
        # Track handler for this topic
        if topic not in self._topic_handlers:
            self._topic_handlers[topic] = set()
        self._topic_handlers[topic].add(handler)
        
        self._emitter.on(topic, handler_wrapper)
        logger.debug(f"Current listeners for {topic}: {len(self._emitter.listeners(topic))}")
        
    def remove_listener(self, topic: str, handler: Callable[[Dict[str, Any]], Awaitable[None]]) -> None:
        """Remove a specific event listener.
        
        Args:
            topic: Event topic to unsubscribe from
            handler: Handler to remove
        """
        logger.debug(f"Removing listener for topic: {topic}")
        
        # Get the wrapper for this handler from the nested dict
        wrapper = None
        if topic in self._handler_wrappers and handler in self._handler_wrappers[topic]:
            wrapper = self._handler_wrappers[topic][handler]
            
        if wrapper:
            self._emitter.remove_listener(topic, wrapper)
            del self._handler_wrappers[topic][handler]
            
            # Clean up empty dicts
            if not self._handler_wrappers[topic]:
                del self._handler_wrappers[topic]
                
            # Remove from topic tracking
            if topic in self._topic_handlers:
                self._topic_handlers[topic].discard(handler)
                if not self._topic_handlers[topic]:
                    del self._topic_handlers[topic]
                    
            logger.debug(f"Removed wrapper for handler on topic {topic}")
        else:
            logger.debug(f"No wrapper found for handler on topic {topic}")
            
        logger.debug(f"Remaining listeners for {topic}: {len(self._emitter.listeners(topic))}")
        
    def remove_all_listeners(self, topic: Optional[str] = None) -> None:
        """Remove all event handlers for a topic or all topics.
        
        Args:
            topic: Optional topic to remove handlers from. If None, removes all handlers.
        """
        if topic is None:
            logger.debug("Removing all listeners from all topics")
            # Remove all handlers from all topics
            topics = list(self._topic_handlers.keys())
            for t in topics:
                self.remove_all_listeners(t)
            self._handler_wrappers.clear()
            self._topic_handlers.clear()
        else:
            logger.debug(f"Removing all listeners from topic: {topic}")
            # Remove all handlers for this topic
            if topic in self._topic_handlers:
                handlers = list(self._topic_handlers[topic])
                for handler in handlers:
                    self.remove_listener(topic, handler)
                self._emitter.remove_all_listeners(topic)  # Ensure all are removed
                
        # Verify removal
        if topic:
            remaining = self._emitter.listeners(topic)
            if remaining:
                logger.warning(f"Failed to remove all listeners for {topic}, {len(remaining)} remain")
        
    def clear_all_handlers(self) -> None:
        """Remove all event handlers and reset tracking dictionaries."""
        logger.debug("Clearing all event handlers")
        self._emitter.remove_all_listeners()
        self._handler_wrappers.clear()
        self._topic_handlers.clear()
        
    async def emit(self, topic: str, payload: Optional[Union[Dict[str, Any], BaseModel]] = None) -> None:
        """Emit an event on a topic and wait for all handlers to complete.
        
        Args:
            topic: Event topic
            payload: Optional event payload (dict or Pydantic model)
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
            logger.debug(f"Event payload: {payload_dict}")
            
            # Create tasks for all async listeners
            tasks: List[asyncio.Task] = []
            sync_handlers: List[Callable] = []
            
            for i, listener in enumerate(listeners):
                logger.debug(f"Processing listener {i+1} for {topic}")
                if asyncio.iscoroutinefunction(listener):
                    logger.debug(f"Creating async task for listener {i+1}")
                    task = self._loop.create_task(
                        self._execute_handler(listener, topic, payload_dict, i+1)
                    )
                    tasks.append(task)
                else:
                    logger.debug(f"Adding sync listener {i+1} to batch")
                    sync_handlers.append(listener)
            
            # Execute sync handlers in a thread pool to prevent blocking
            if sync_handlers:
                logger.debug(f"Processing {len(sync_handlers)} sync handlers")
                for i, handler in enumerate(sync_handlers):
                    task = self._loop.create_task(
                        self._execute_sync_handler(handler, topic, payload_dict, i+1)
                    )
                    tasks.append(task)
            
            # Wait for all tasks with timeout
            if tasks:
                logger.debug(f"Waiting for {len(tasks)} tasks to complete")
                try:
                    await asyncio.wait_for(asyncio.gather(*tasks), timeout=5.0)
                    logger.debug("All tasks completed successfully")
                except asyncio.TimeoutError:
                    logger.error(f"Timeout waiting for handlers on topic {topic}")
                    # Cancel any remaining tasks
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                except Exception as e:
                    logger.error(f"Error in event handlers for {topic}: {e}")
                    raise
                
        except Exception as e:
            logger.error(f"Error emitting event on topic {topic}: {e}")
            raise
            
    async def _execute_handler(
        self,
        handler: Callable,
        topic: str,
        payload: Dict[str, Any],
        handler_index: int
    ) -> None:
        """Execute an async handler with error handling."""
        try:
            await handler(payload)
            logger.debug(f"Async handler {handler_index} for {topic} completed")
        except Exception as e:
            logger.error(f"Error in async handler {handler_index} for {topic}: {e}")
            
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
            logger.debug(f"Sync handler {handler_index} for {topic} completed")
        except Exception as e:
            logger.error(f"Error in sync handler {handler_index} for {topic}: {e}")
            
    def get_topic_handlers(self, topic: str) -> Set[Callable]:
        """Get all handlers for a topic.
        
        Args:
            topic: Event topic
            
        Returns:
            Set of handlers for the topic
        """
        return self._topic_handlers.get(topic, set()) 