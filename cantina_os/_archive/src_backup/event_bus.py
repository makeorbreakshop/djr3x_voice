"""Event bus implementation for CantinaOS."""
from typing import Dict, Any, Callable, Coroutine, List
import asyncio
import logging

EventHandler = Callable[[str, Dict[str, Any]], Coroutine[Any, Any, None]]

class EventBus:
    """Central event bus for inter-service communication."""
    
    def __init__(self):
        """Initialize the event bus."""
        self._subscribers: Dict[str, List[EventHandler]] = {}
        self._logger = logging.getLogger(__name__)
        
    def subscribe(self, topic: str, handler: EventHandler) -> None:
        """Subscribe to a topic with a handler function."""
        if topic not in self._subscribers:
            self._subscribers[topic] = []
        self._subscribers[topic].append(handler)
        
    def unsubscribe(self, topic: str, handler: EventHandler) -> None:
        """Unsubscribe a handler from a topic."""
        if topic in self._subscribers and handler in self._subscribers[topic]:
            self._subscribers[topic].remove(handler)
            
    async def emit(self, topic: str, payload: Dict[str, Any]) -> None:
        """Emit an event to all subscribers of a topic."""
        if topic not in self._subscribers:
            return
            
        tasks = []
        for handler in self._subscribers[topic]:
            try:
                task = asyncio.create_task(handler(topic, payload))
                tasks.append(task)
            except Exception as e:
                self._logger.error(f"Error creating task for handler {handler}: {e}")
                
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True) 