"""Event bus implementation for CantinaOS."""

from typing import Any, Dict, Callable, Coroutine
from pyee.asyncio import AsyncIOEventEmitter

class EventBus:
    """Event bus for asynchronous event handling."""
    
    def __init__(self):
        """Initialize the event bus."""
        self._emitter = AsyncIOEventEmitter()
    
    def emit(self, event: str, data: Dict[str, Any]) -> None:
        """Emit an event with data.
        
        Args:
            event: Event name/topic
            data: Event data
        """
        self._emitter.emit(event, data)
    
    def publish(self, event: str, data: Dict[str, Any]) -> None:
        """Publish an event with data (alias for emit).
        
        Args:
            event: Event name/topic
            data: Event data
        """
        self.emit(event, data)
    
    def on(self, event: str, callback: Callable[[Dict[str, Any]], Coroutine[Any, Any, None]]) -> None:
        """Subscribe to an event.
        
        Args:
            event: Event name/topic
            callback: Async callback function
        """
        self._emitter.on(event, callback)
    
    def remove_listener(self, event: str, callback: Callable[[Dict[str, Any]], Coroutine[Any, Any, None]]) -> None:
        """Remove a listener for an event.
        
        Args:
            event: Event name/topic
            callback: Callback function to remove
        """
        self._emitter.remove_listener(event, callback)
    
    def remove_all_listeners(self, event: str = None) -> None:
        """Remove all listeners for an event or all events.
        
        Args:
            event: Optional event name/topic. If None, removes all listeners for all events.
        """
        if event:
            self._emitter.remove_all_listeners(event)
        else:
            self._emitter.remove_all_listeners() 