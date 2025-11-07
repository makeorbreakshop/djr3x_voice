"""
Event Synchronizer

A utility to synchronize tests with events, ensuring proper event sequencing and timing.
"""

import asyncio
import logging
from typing import Dict, Any, Optional, Callable, List, Set, Union, MutableMapping
from dataclasses import dataclass, field
from contextlib import asynccontextmanager

from .sync_event_bus import SyncEventBus

logger = logging.getLogger(__name__)

@dataclass
class SubscriptionContext:
    """Context for an event subscription."""
    handler: Callable[[Dict[str, Any]], None]
    future: Optional[asyncio.Future] = None
    received_events: List[Dict[str, Any]] = field(default_factory=list)
    
class EventSynchronizer:
    """
    Utility to synchronize tests with asynchronous events.
    
    Features:
    - Wait for specific events
    - Wait for event sequences
    - Apply grace periods for state propagation
    - Filter events with conditions
    """
    
    def __init__(
        self,
        event_bus: SyncEventBus,
        grace_period_ms: int = 500,  # Increased from 200ms to 500ms
        subscription_grace_period_ms: int = 100  # Explicit grace period for subscriptions
    ):
        """Initialize the event synchronizer.
        
        Args:
            event_bus: SyncEventBus instance
            grace_period_ms: Grace period in milliseconds after events
            subscription_grace_period_ms: Grace period after subscriptions
        """
        self.event_bus = event_bus
        self.grace_period_ms = grace_period_ms
        self.subscription_grace_period_ms = subscription_grace_period_ms
        self._contexts: MutableMapping[str, SubscriptionContext] = {}
        self._subscription_lock = asyncio.Lock()
        logger.debug("EventSynchronizer initialized")
        
    @asynccontextmanager
    async def _subscription_context(self, topic: str):
        """Get or create a subscription context."""
        async with self._subscription_lock:
            if topic not in self._contexts:
                # Create new context with handler
                # Use an async lambda function for the handler
                async def event_handler(data):
                    self._on_event(topic, data)
                    
                context = SubscriptionContext(
                    handler=event_handler
                )
                self._contexts[topic] = context
                
                # Subscribe to the event bus
                logger.debug(f"Subscribing to {topic}")
                await self.event_bus.on(topic, context.handler)
                
                # Add grace period after subscription
                await asyncio.sleep(self.subscription_grace_period_ms / 1000)  # Explicit wait after subscription
            
            context = self._contexts[topic]
        
        try:
            yield context
        finally:
            # Cleanup if needed in future enhancements
            pass
            
    def _on_event(self, topic: str, data: Dict[str, Any]) -> None:
        """Handle an event.
        
        Args:
            topic: Event topic
            data: Event data
        """
        logger.debug(f"Received event on {topic}: {data.get('status', '')}")
        
        context = self._contexts.get(topic)
        if not context:
            logger.warning(f"Received event for untracked topic: {topic}")
            return
            
        # Add test verification marker for tracing
        data = data.copy()
        data["_test_verification"] = True
        data["timestamp"] = asyncio.get_event_loop().time()
        data["topic"] = topic
        
        # Store event data
        context.received_events.append(data)
        
        # Resolve future if waiting
        if context.future and not context.future.done():
            context.future.set_result(data)
            
    async def wait_for_event(
        self,
        topic: str,
        timeout: float = 10.0,  # Increased from 5.0 to 10.0
        condition: Optional[Callable[[Dict[str, Any]], bool]] = None
    ) -> Dict[str, Any]:
        """
        Wait for a specific event with proper context tracking.
        
        Args:
            topic: Event topic to wait for
            timeout: Timeout in seconds
            condition: Optional filter condition
            
        Returns:
            Event data
            
        Raises:
            asyncio.TimeoutError: If event not received within timeout
        """
        # Pre-subscription grace period
        await asyncio.sleep(0.1)  # 100ms grace period before subscribing
        
        logger.info(f"Waiting for event: {topic} (timeout: {timeout}s)")
        
        async with self._subscription_context(topic) as context:
            # Check existing events first
            if context.received_events:
                for event_data in reversed(context.received_events):
                    if condition is None or condition(event_data):
                        logger.debug(f"Found matching event for {topic} in history")
                        return event_data
            
            # Create future and wait
            logger.debug(f"Creating future for {topic}")
            context.future = asyncio.Future()
            try:
                result = await asyncio.wait_for(context.future, timeout)
                
                # Apply grace period
                if self.grace_period_ms > 0:
                    logger.debug(f"Applying grace period: {self.grace_period_ms}ms")
                    await asyncio.sleep(self.grace_period_ms / 1000)
                    
                return result
            except asyncio.TimeoutError:
                events_received = [e.get('status', '') for e in context.received_events]
                logger.error(f"Timeout waiting for event: {topic}. Events received: {events_received}")
                raise asyncio.TimeoutError(f"Timeout waiting for event: {topic}")
                
    async def wait_for_events(
        self,
        topics: List[str],
        timeout: float = 10.0,  # Increased from 5.0 to 10.0
        in_order: bool = False
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Wait for multiple events with proper ordering.
        
        Args:
            topics: List of event topics to wait for
            timeout: Timeout in seconds
            in_order: Whether events must occur in specified order
            
        Returns:
            Dictionary mapping topics to lists of event data
            
        Raises:
            asyncio.TimeoutError: If any event not received within timeout
        """
        logger.debug(f"Waiting for events: {topics} (in_order={in_order})")
        
        # Pre-subscription grace period
        await asyncio.sleep(0.1)  # 100ms grace period before subscribing
        
        # Make sure we're subscribed to all topics first
        for topic in topics:
            async with self._subscription_context(topic):
                pass
                
        # Add grace period after all subscriptions
        await asyncio.sleep(0.2)  # Increased from 0.1 to 0.2
                
        results: Dict[str, List[Dict[str, Any]]] = {topic: [] for topic in topics}
        start_time = asyncio.get_event_loop().time()
        
        if in_order:
            # Wait for events in sequence
            for topic in topics:
                remaining_time = timeout - (asyncio.get_event_loop().time() - start_time)
                if remaining_time <= 0:
                    raise asyncio.TimeoutError("Timeout waiting for ordered events")
                    
                result = await self.wait_for_event(topic, timeout=remaining_time)
                results[topic].append(result)
        else:
            # Wait for all events in parallel
            tasks = []
            for topic in topics:
                task = asyncio.create_task(self.wait_for_event(topic, timeout=timeout))
                tasks.append((topic, task))
                
            for topic, task in tasks:
                try:
                    result = await task
                    results[topic].append(result)
                except Exception as e:
                    # Cancel remaining tasks
                    for _, t in tasks:
                        if not t.done():
                            t.cancel()
                    raise
                
        # Apply grace period to allow state updates to complete
        if self.grace_period_ms > 0:
            await asyncio.sleep(self.grace_period_ms / 1000)
            
        return results
        
    def get_events(self, topic: str) -> List[Dict[str, Any]]:
        """Get all events received for a topic."""
        context = self._contexts.get(topic)
        if not context:
            return []
        return context.received_events.copy()
        
    def clear_events(self, topics: Optional[List[str]] = None) -> None:
        """Clear events for specified topics or all topics."""
        if topics is None:
            topics = list(self._contexts.keys())
            
        for topic in topics:
            if topic in self._contexts:
                self._contexts[topic].received_events.clear()
                
    async def verify_subscriptions(self, topics: Optional[List[str]] = None) -> bool:
        """Verify that subscriptions are correctly registered.
        
        Args:
            topics: Optional list of topics to verify, or all if None
            
        Returns:
            True if all subscriptions are valid
        """
        if topics is None:
            topics = list(self._contexts.keys())
            
        for topic in topics:
            if topic not in self._contexts:
                logger.warning(f"Topic {topic} not subscribed")
                return False
                
        logger.debug(f"All subscriptions verified: {topics}")
        return True
                
    async def cleanup(self) -> None:
        """Clean up all event contexts."""
        async with self._subscription_lock:
            for topic in list(self._contexts.keys()):
                context = self._contexts.pop(topic)
                try:
                    await self.event_bus.remove_listener(topic, context.handler)
                except Exception as e:
                    logger.error(f"Error cleaning up handler for {topic}: {e}")
                if context.future and not context.future.done():
                    context.future.cancel() 