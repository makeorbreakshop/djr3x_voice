"""
Event Synchronizer Utility for Testing

This utility provides tools for synchronizing and verifying event timing in tests.
It helps address race conditions by ensuring events are processed in the expected order
and that state updates have completed before assertions are made.
"""

import asyncio
import logging
from typing import Dict, List, Optional, Set, Any, Callable, Tuple
from pyee.asyncio import AsyncIOEventEmitter

logger = logging.getLogger(__name__)

class EventSynchronizer:
    """
    A utility for synchronizing and verifying event timing in tests.
    
    This helps address common race conditions in event-based testing by:
    1. Waiting for specific events to occur
    2. Ensuring state updates have completed
    3. Adding a configurable grace period for event propagation
    4. Tracking event order and timing
    5. Supporting retry mechanisms for flaky events
    """
    
    def __init__(
        self, 
        event_bus: AsyncIOEventEmitter,
        grace_period_ms: int = 500,  # Increased default grace period
        max_retries: int = 3,
        retry_delay_ms: int = 500
    ):
        """
        Initialize the event synchronizer.
        
        Args:
            event_bus: The event bus to monitor
            grace_period_ms: Grace period in milliseconds for state propagation
            max_retries: Maximum number of retries for event waiting
            retry_delay_ms: Delay between retries in milliseconds
        """
        self.event_bus = event_bus
        self.grace_period_ms = grace_period_ms
        self.max_retries = max_retries
        self.retry_delay_ms = retry_delay_ms
        self.received_events: Dict[str, List[Dict[str, Any]]] = {}
        self.event_futures: Dict[str, List[asyncio.Future]] = {}
        self.subscribed_topics: Set[str] = set()
        self._current_subscription_topic = None
        self._cleanup_tasks: List[asyncio.Task] = []
        self._handlers: Dict[str, Callable] = {}  # Track handlers by topic
        logger.info("EventSynchronizer initialized")
        
    def _on_event(self, payload: Any) -> None:
        """
        Handler for events on the bus.
        
        Args:
            payload: Event data (dict or Pydantic model)
        """
        try:
            # Convert payload to dict if it's a Pydantic model
            if hasattr(payload, 'model_dump'):
                event_data = payload.model_dump()
            elif isinstance(payload, dict):
                event_data = payload
            elif payload is None:
                event_data = {}
            else:
                event_data = {"payload": payload}
                
            # Get the event name from the current subscription context
            event_name = self._current_subscription_topic
            logger.debug(f"Received event: {event_name} with data: {event_data}")
            
            # Record the event
            if event_name not in self.received_events:
                self.received_events[event_name] = []
            
            self.received_events[event_name].append(event_data)
            logger.debug(f"Stored event {event_name}. Total events for this topic: {len(self.received_events[event_name])}")
            
            # Resolve any futures waiting for this event
            if event_name in self.event_futures:
                logger.debug(f"Found {len(self.event_futures[event_name])} futures waiting for {event_name}")
                for future in self.event_futures[event_name]:
                    if not future.done():
                        logger.debug(f"Resolving future for {event_name}")
                        future.set_result(event_data)
                
                # Remove resolved futures
                self.event_futures[event_name] = [
                    f for f in self.event_futures[event_name] if not f.done()
                ]
                logger.debug(f"Remaining futures for {event_name}: {len(self.event_futures[event_name])}")
        except Exception as e:
            logger.error(f"Error in event handler: {e}")
            # Don't raise the exception - we don't want to break the event bus
            
    async def _subscribe_to_event(self, event_name: str) -> None:
        """
        Subscribe to an event with proper error handling.
        
        Args:
            event_name: The name of the event to subscribe to
        """
        try:
            if event_name not in self.subscribed_topics:
                logger.debug(f"Subscribing to event: {event_name}")
                self._current_subscription_topic = event_name
                
                # Create a new handler for this topic
                handler = lambda payload: self._on_event(payload)
                self._handlers[event_name] = handler
                
                # Don't await on() since it's not a coroutine in pyee 11.0.1
                self.event_bus.on(event_name, handler)
                self.subscribed_topics.add(event_name)
                logger.debug(f"Subscribed topics: {self.subscribed_topics}")
                
                # Add grace period after subscription
                await asyncio.sleep(0.1)  # Small delay to ensure subscription is active
        except Exception as e:
            logger.error(f"Error subscribing to event {event_name}: {e}")
            raise
            
    async def _wait_with_retry(
        self,
        event_name: str,
        timeout: float,
        condition: Optional[Callable[[Dict[str, Any]], bool]] = None
    ) -> Tuple[Dict[str, Any], int]:
        """
        Wait for an event with retry mechanism.
        
        Args:
            event_name: The name of the event to wait for
            timeout: Timeout in seconds
            condition: Optional function to filter events
            
        Returns:
            Tuple of (event data, retry count)
            
        Raises:
            asyncio.TimeoutError: If the event doesn't occur within all retries
        """
        last_error = None
        for retry in range(self.max_retries):
            try:
                # Create a future to wait for the event
                future = asyncio.Future()
                
                if event_name not in self.event_futures:
                    self.event_futures[event_name] = []
                
                self.event_futures[event_name].append(future)
                
                # Wait for the event
                result = await asyncio.wait_for(future, timeout)
                
                # Check condition if provided
                if condition is None or condition(result):
                    return result, retry
                    
            except asyncio.TimeoutError as e:
                last_error = e
                if retry < self.max_retries - 1:
                    logger.warning(f"Timeout waiting for {event_name}, retry {retry + 1}/{self.max_retries}")
                    await asyncio.sleep(self.retry_delay_ms / 1000)
                continue
                
        if last_error:
            raise last_error
            
    async def wait_for_event(
        self, 
        event_name: str, 
        timeout: float = 5.0,
        condition: Optional[Callable[[Dict[str, Any]], bool]] = None
    ) -> Dict[str, Any]:
        """
        Wait for a specific event to occur.
        
        Args:
            event_name: The name of the event to wait for
            timeout: Timeout in seconds
            condition: Optional function to filter events
            
        Returns:
            Event data
            
        Raises:
            asyncio.TimeoutError: If the event doesn't occur within the timeout
        """
        logger.info(f"Waiting for event: {event_name} (timeout: {timeout}s)")
        
        # Subscribe to the event
        await self._subscribe_to_event(event_name)
        
        # Check if we've already received this event
        if event_name in self.received_events:
            logger.debug(f"Found {len(self.received_events[event_name])} existing events for {event_name}")
            for event_data in reversed(self.received_events[event_name]):
                if condition is None or condition(event_data):
                    logger.info(f"Found matching event for {event_name}")
                    return event_data
        
        # Wait for the event with retry
        result, retry_count = await self._wait_with_retry(event_name, timeout, condition)
        
        # Apply grace period to allow state updates to complete
        if self.grace_period_ms > 0:
            logger.debug(f"Applying grace period: {self.grace_period_ms}ms")
            await asyncio.sleep(self.grace_period_ms / 1000)
            
        if retry_count > 0:
            logger.info(f"Successfully received event: {event_name} after {retry_count + 1} attempts")
        else:
            logger.info(f"Successfully received event: {event_name}")
            
        return result
    
    async def wait_for_events(
        self, 
        event_names: List[str], 
        timeout: float = 5.0,
        in_order: bool = False
    ) -> Dict[str, Dict[str, Any]]:
        """
        Wait for multiple events to occur.
        
        Args:
            event_names: List of event names to wait for
            timeout: Timeout in seconds
            in_order: Whether events must occur in the specified order
            
        Returns:
            Dictionary mapping event names to event data
            
        Raises:
            asyncio.TimeoutError: If any event doesn't occur within the timeout
        """
        logger.info(f"Waiting for events: {event_names} (timeout: {timeout}s, in_order: {in_order})")
        
        # Subscribe to all events
        for event_name in event_names:
            await self._subscribe_to_event(event_name)
        
        results = {}
        start_time = asyncio.get_event_loop().time()
        
        if in_order:
            # Wait for events in specified order
            for event_name in event_names:
                remaining_time = timeout - (asyncio.get_event_loop().time() - start_time)
                if remaining_time <= 0:
                    logger.error("Timeout waiting for events in order")
                    raise asyncio.TimeoutError(f"Timeout waiting for events in order")
                
                logger.debug(f"Waiting for {event_name} with remaining time {remaining_time}s")
                results[event_name] = await self.wait_for_event(
                    event_name, 
                    timeout=remaining_time
                )
        else:
            # Wait for all events in any order
            tasks = [
                self.wait_for_event(event_name, timeout=timeout) 
                for event_name in event_names
            ]
            
            try:
                completed_tasks = await asyncio.gather(*tasks)
                for i, event_data in enumerate(completed_tasks):
                    results[event_names[i]] = event_data
            except asyncio.TimeoutError:
                logger.error("Timeout waiting for events")
                # Cancel any remaining tasks
                for task in tasks:
                    if not task.done():
                        task.cancel()
                raise
        
        # Apply grace period to allow state updates to complete
        if self.grace_period_ms > 0:
            logger.debug(f"Applying grace period: {self.grace_period_ms}ms")
            await asyncio.sleep(self.grace_period_ms / 1000)
            
        logger.info(f"Successfully received all events: {list(results.keys())}")
        return results
    
    def clear_events(self, event_names: Optional[List[str]] = None) -> None:
        """
        Clear tracked events.
        
        Args:
            event_names: List of event names to clear, or None for all
        """
        if event_names is None:
            logger.debug("Clearing all events")
            self.received_events.clear()
        else:
            logger.debug(f"Clearing events: {event_names}")
            for event_name in event_names:
                if event_name in self.received_events:
                    del self.received_events[event_name]
    
    def get_events(self, event_name: str) -> List[Dict[str, Any]]:
        """
        Get all occurrences of a specific event.
        
        Args:
            event_name: The name of the event
            
        Returns:
            List of event data dictionaries
        """
        return self.received_events.get(event_name, [])
        
    def has_received_event(self, event_name: str) -> bool:
        """
        Check if an event has been received.
        
        Args:
            event_name: The name of the event
            
        Returns:
            True if the event has been received, False otherwise
        """
        return event_name in self.received_events and len(self.received_events[event_name]) > 0
        
    async def cleanup(self) -> None:
        """Clean up resources and unsubscribe from events."""
        logger.info("Cleaning up EventSynchronizer")
        try:
            # Cancel any pending futures
            for event_futures in self.event_futures.values():
                for future in event_futures:
                    if not future.done():
                        future.cancel()
            
            # Clear all events
            self.clear_events()
            
            # Remove all subscriptions
            for topic in list(self.subscribed_topics):
                try:
                    if topic in self._handlers:
                        handler = self._handlers[topic]
                        self.event_bus.remove_listener(topic, handler)
                        del self._handlers[topic]
                except Exception as e:
                    logger.warning(f"Error removing listener for {topic}: {e}")
            
            self.subscribed_topics.clear()
            self._handlers.clear()
            
            # Cancel any cleanup tasks
            for task in self._cleanup_tasks:
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
            
            self._cleanup_tasks.clear()
            
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")
            raise
        finally:
            logger.info("EventSynchronizer cleanup completed")