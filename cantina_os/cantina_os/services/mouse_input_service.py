"""
MouseInputService for CantinaOS

This service manages mouse click input for controlling microphone recording.
It follows the event-based architecture and provides a more intuitive way
to start/stop recording through mouse clicks.
"""

import asyncio
import logging
from typing import Dict, Any, Optional, List
from pydantic import BaseModel
from pynput import mouse

from ..base_service import BaseService
from ..event_topics import EventTopics
from ..event_payloads import (
    ServiceStatus,
    LogLevel,
    BaseEventPayload,
    SystemModeChangePayload
)
from ..services.yoda_mode_manager_service import SystemMode

class MouseInputServiceConfig(BaseModel):
    """Configuration for the MouseInputService."""
    enabled: bool = True
    double_click_timeout_ms: int = 500  # Time window for double click detection

class MouseInputService(BaseService):
    """
    Service for handling mouse input to control microphone recording.
    
    Features:
    - Single click to start/stop recording (only in INTERACTIVE mode)
    - Event-based communication
    - Configurable settings
    - Mode-aware behavior
    """
    
    def __init__(
        self,
        event_bus,
        config: Optional[Dict[str, Any]] = None,
        logger: Optional[logging.Logger] = None
    ):
        """Initialize the mouse input service."""
        super().__init__("mouse_input", event_bus, logger)
        
        # Initialize configuration
        self._config = MouseInputServiceConfig(**(config or {}))
        
        # Initialize state
        self._mouse_listener: Optional[mouse.Listener] = None
        self._is_recording: bool = False
        self._tasks: List[asyncio.Task] = []
        self._current_mode: SystemMode = SystemMode.STARTUP
        
        # Store the event loop for use in callbacks
        self._loop = None
        
    async def _start(self) -> None:
        """Initialize the service."""
        try:
            self.logger.info("Starting MouseInputService")
            
            # Store the event loop
            self._loop = asyncio.get_running_loop()
            
            # Set up mouse listener
            await self._setup_mouse_listener()
            
            # Subscribe to mode change events
            await self._setup_subscriptions()
            
            await self._emit_status(
                ServiceStatus.RUNNING,
                "Mouse input service started successfully"
            )
            
        except Exception as e:
            error_msg = f"Failed to start MouseInputService: {str(e)}"
            self.logger.error(error_msg)
            await self._emit_status(
                ServiceStatus.ERROR,
                error_msg,
                severity=LogLevel.ERROR
            )
            raise
            
    async def _stop(self) -> None:
        """Clean up resources."""
        try:
            self.logger.info("Stopping MouseInputService")
            
            # Stop mouse listener
            if self._mouse_listener:
                self._mouse_listener.stop()
                self._mouse_listener = None
            
            # Cancel any pending tasks
            for task in self._tasks:
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
            
            self._tasks.clear()
            
            await self._emit_status(
                ServiceStatus.STOPPED,
                "Mouse input service stopped"
            )
            
        except Exception as e:
            error_msg = f"Error stopping MouseInputService: {str(e)}"
            self.logger.error(error_msg)
            await self._emit_status(
                ServiceStatus.ERROR,
                error_msg,
                severity=LogLevel.ERROR
            )
            raise
    
    async def _setup_subscriptions(self) -> None:
        """Set up event subscriptions."""
        # Subscribe to mode change events
        asyncio.create_task(self.subscribe(
            EventTopics.SYSTEM_MODE_CHANGE,
            self._handle_mode_change
        ))
        self.logger.info("Subscribed to system mode change events")
            
    async def _setup_mouse_listener(self) -> None:
        """Set up the mouse click listener."""
        def on_click(x: int, y: int, button: mouse.Button, pressed: bool) -> None:
            """Handle mouse click events."""
            if button == mouse.Button.left and pressed:
                # Use run_coroutine_threadsafe to handle the async operation from a sync callback
                if self._loop and not self._loop.is_closed():
                    asyncio.run_coroutine_threadsafe(
                        self._handle_mouse_click(),
                        self._loop
                    )
        
        try:
            self._mouse_listener = mouse.Listener(on_click=on_click)
            self._mouse_listener.start()
            self.logger.info("Mouse listener initialized successfully")
            
        except Exception as e:
            error_msg = f"Failed to initialize mouse listener: {str(e)}"
            self.logger.error(error_msg)
            raise
    
    async def _handle_mode_change(self, payload: Dict[str, Any]) -> None:
        """Handle system mode change events.
        
        Args:
            payload: Mode change event payload
        """
        try:
            # Extract new mode from payload
            new_mode = payload.get("new_mode")
            if new_mode:
                try:
                    # Convert string to SystemMode enum if needed
                    if isinstance(new_mode, str):
                        self._current_mode = SystemMode(new_mode)
                    else:
                        self._current_mode = new_mode
                        
                    self.logger.info(f"Updated current mode to: {self._current_mode.name}")
                    
                    # If transitioning out of INTERACTIVE mode and recording was active,
                    # automatically stop recording
                    if self._current_mode != SystemMode.INTERACTIVE and self._is_recording:
                        self.logger.info("Automatically stopping recording due to mode change")
                        await self.emit(EventTopics.MIC_RECORDING_STOP, {})
                        self._is_recording = False
                        
                except (ValueError, AttributeError) as e:
                    self.logger.error(f"Invalid mode value received: {new_mode}, error: {e}")
                    
        except Exception as e:
            error_msg = f"Error handling mode change: {str(e)}"
            self.logger.error(error_msg)
            await self._emit_status(
                ServiceStatus.ERROR,
                error_msg,
                severity=LogLevel.ERROR
            )
            
    async def _handle_mouse_click(self) -> None:
        """Handle mouse click event and toggle recording state.
        
        Only processes mouse clicks when in INTERACTIVE mode.
        """
        try:
            # Only process mouse clicks in INTERACTIVE mode
            if self._current_mode != SystemMode.INTERACTIVE:
                self.logger.debug(f"Ignoring mouse click - not in INTERACTIVE mode (current: {self._current_mode.name})")
                return
                
            # Toggle recording state
            self._is_recording = not self._is_recording
            
            if self._is_recording:
                self.logger.info("Mouse click detected - starting recording")
                await self.emit(EventTopics.MIC_RECORDING_START, {})
                await self._emit_status(
                    ServiceStatus.RUNNING,
                    "Recording started via mouse click"
                )
            else:
                self.logger.info("Mouse click detected - stopping recording")
                await self.emit(EventTopics.MIC_RECORDING_STOP, {})
                await self._emit_status(
                    ServiceStatus.RUNNING,
                    "Recording stopped via mouse click"
                )
                
        except Exception as e:
            error_msg = f"Error handling mouse click: {str(e)}"
            self.logger.error(error_msg)
            await self._emit_status(
                ServiceStatus.ERROR,
                error_msg,
                severity=LogLevel.ERROR
            ) 