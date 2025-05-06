"""
Event bus implementation for DJ R3X using pyee.AsyncIOEventEmitter.
"""

from enum import Enum, auto
from typing import Any, Dict, Optional, Callable, Coroutine
from pyee.asyncio import AsyncIOEventEmitter
import asyncio
import inspect

class SystemMode(Enum):
    """System operation modes for DJ R3X."""
    STARTUP = "startup"           # Initial startup and self-check
    IDLE = "idle"                 # Default idle state after startup
    AMBIENT = "ambient"           # Ambient show mode (pre-scripted animations)
    INTERACTIVE = "interactive"   # Interactive voice conversation mode

class EventTypes(Enum):
    """Event types that can be emitted on the event bus."""
    
    # System events
    SYSTEM_READY = "system.ready"
    SYSTEM_SHUTDOWN = "system.shutdown"
    SYSTEM_ERROR = "system.error"
    SYSTEM_MODE_CHANGED = "system.mode_changed"
    
    # Voice events
    VOICE_LISTENING_STARTED = "voice.listening_started"
    VOICE_LISTENING_STOPPED = "voice.listening_stopped"
    VOICE_PROCESSING_STARTED = "voice.processing_started"
    VOICE_SPEAKING_STARTED = "voice.speaking_started"
    VOICE_BEAT = "voice.beat"
    VOICE_SPEAKING_FINISHED = "voice.speaking_finished"
    
    # Music events
    MUSIC_TRACK_STARTED = "music.track_started"
    MUSIC_VOLUME_DUCKED = "music.volume_ducked"
    MUSIC_VOLUME_RESTORED = "music.volume_restored"
    MUSIC_CONTROL_COMMAND = "music.control_command"
    
    # LED events
    LED_ANIMATION_STARTED = "led.animation_started"
    LED_ANIMATION_STOPPED = "led.animation_stopped"
    LED_ANIMATION_ATTEMPTED = "led.animation_attempted"
    
    # Command events
    COMMAND_RECEIVED = "command.received"
    COMMAND_EXECUTED = "command.executed"

class EventBus:
    """
    Event bus for inter-component communication using pyee.AsyncIOEventEmitter.
    Provides type-safe event emission and subscription with proper async handling.
    """
    
    def __init__(self, loop: Optional[asyncio.AbstractEventLoop] = None):
        """Initialize the event bus.
        
        Args:
            loop: Explicit event loop to use for async operations
        """
        self._emitter = AsyncIOEventEmitter()
        
        # Try to get the running loop first, then use the provided loop,
        # fall back to get_event_loop() only if neither is available
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running event loop, use provided loop or get a new one
            self._loop = loop or asyncio.get_event_loop()
    
    async def emit(self, event_type: EventTypes, data: Optional[Dict[str, Any]] = None) -> None:
        """
        Emit an event on the bus.
        
        Args:
            event_type: The type of event to emit
            data: Optional data payload for the event
        """
        if not isinstance(event_type, EventTypes):
            raise ValueError(f"event_type must be an EventTypes enum, got {type(event_type)}")
        
        # Get all listeners for this event
        listeners = self._emitter.listeners(event_type.value)
        
        # Create tasks for all async listeners
        tasks = []
        for listener in listeners:
            if asyncio.iscoroutinefunction(listener):
                # For async callbacks, create a task on the current running loop
                try:
                    # Always get the currently running loop to ensure we're using the right one
                    loop = asyncio.get_running_loop()
                    task = loop.create_task(listener(data or {}))
                    tasks.append(task)
                except RuntimeError:
                    # Fallback to stored loop reference if no running loop found
                    task = self._loop.create_task(listener(data or {}))
                    tasks.append(task)
            else:
                # For sync callbacks, just call directly
                listener(data or {})
        
        # Wait for all async tasks to complete
        if tasks:
            await asyncio.gather(*tasks)
    
    def on(self, event_type: EventTypes, callback: Callable) -> None:
        """
        Subscribe to an event type.
        
        Args:
            event_type: The type of event to subscribe to
            callback: Callback function to handle the event (can be async or sync)
        """
        if not isinstance(event_type, EventTypes):
            raise ValueError(f"event_type must be an EventTypes enum, got {type(event_type)}")
        
        # Verify callback is callable
        if not callable(callback):
            raise ValueError(f"callback must be callable, got {type(callback)}")
        
        self._emitter.on(event_type.value, callback)
    
    def remove_listener(self, event_type: EventTypes, callback: Callable) -> None:
        """
        Remove a listener for an event type.
        
        Args:
            event_type: The type of event to unsubscribe from
            callback: The callback function to remove
        """
        if not isinstance(event_type, EventTypes):
            raise ValueError(f"event_type must be an EventTypes enum, got {type(event_type)}")
        
        self._emitter.remove_listener(event_type.value, callback)
    
    def remove_all_listeners(self, event_type: Optional[EventTypes] = None) -> None:
        """
        Remove all listeners for an event type or all events.
        
        Args:
            event_type: Optional event type to remove listeners for. If None, removes all listeners.
        """
        if event_type is not None:
            if not isinstance(event_type, EventTypes):
                raise ValueError(f"event_type must be an EventTypes enum, got {type(event_type)}")
            self._emitter.remove_all_listeners(event_type.value)
        else:
            self._emitter.remove_all_listeners() 