"""
System Mode Manager for DJ R3X.
Handles system mode state and transitions between modes.
"""

import asyncio
import logging
from typing import Optional, Callable, Dict, Any

from src.bus import EventBus, EventTypes, SystemMode

# Configure logging
logger = logging.getLogger(__name__)

class SystemModeManager:
    """Manages system operation modes and transitions between them."""
    
    def __init__(self, event_bus: EventBus, initial_mode: SystemMode = SystemMode.STARTUP):
        """Initialize the system mode manager.
        
        Args:
            event_bus: Event bus instance
            initial_mode: Initial system mode (default: STARTUP)
        """
        self.event_bus = event_bus
        self._current_mode = initial_mode
        self._mode_transition_callbacks: Dict[SystemMode, Callable] = {}
        self._command_handlers: Dict[str, Callable] = {}
        
        # Register standard command handlers
        self.register_command_handler("engage", self._handle_engage_command)
        self.register_command_handler("ambient", self._handle_ambient_command)
        self.register_command_handler("disengage", self._handle_disengage_command)
        self.register_command_handler("help", self._handle_help_command)
        self.register_command_handler("status", self._handle_status_command)
        self.register_command_handler("reset", self._handle_reset_command)
        
        # Register for command events
        self.event_bus.on(EventTypes.COMMAND_RECEIVED, self._handle_command)
        
    @property
    def current_mode(self) -> SystemMode:
        """Get the current system mode."""
        return self._current_mode
    
    async def change_mode(self, new_mode: SystemMode) -> None:
        """Change the system mode and emit event.
        
        Args:
            new_mode: The new system mode to transition to
        """
        if new_mode == self._current_mode:
            logger.info(f"Already in {new_mode.value} mode")
            return
            
        old_mode = self._current_mode
        logger.info(f"Changing system mode: {old_mode.value} → {new_mode.value}")
        
        # Execute transition callback if registered
        if old_mode in self._mode_transition_callbacks:
            try:
                await self._mode_transition_callbacks[old_mode](new_mode)
            except Exception as e:
                logger.error(f"Error in mode transition callback: {e}")
        
        # Update current mode
        self._current_mode = new_mode
        
        # Emit mode change event
        await self.event_bus.emit(
            EventTypes.SYSTEM_MODE_CHANGED, 
            {
                "old_mode": old_mode.value,
                "new_mode": new_mode.value
            }
        )
    
    def register_mode_transition_callback(self, from_mode: SystemMode, callback: Callable) -> None:
        """Register a callback to execute when transitioning from a specific mode.
        
        Args:
            from_mode: The mode being transitioned from
            callback: Async callback function to execute
        """
        if not asyncio.iscoroutinefunction(callback):
            raise ValueError("Transition callback must be a coroutine function")
        self._mode_transition_callbacks[from_mode] = callback
    
    def register_command_handler(self, command: str, handler: Callable) -> None:
        """Register a handler for a specific command.
        
        Args:
            command: Command string to handle (lowercase)
            handler: Async function to handle the command
        """
        if not asyncio.iscoroutinefunction(handler):
            raise ValueError("Command handler must be a coroutine function")
        self._command_handlers[command.lower()] = handler
    
    async def _handle_command(self, data: Dict[str, Any]) -> None:
        """Handle received commands from the command input thread.
        
        Args:
            data: Command data containing the command string
        """
        if "command" not in data:
            logger.warning(f"Received command event without command: {data}")
            return
            
        command = data["command"].lower()
        args = data.get("args", [])
        
        logger.debug(f"Processing command: {command} with args: {args}")
        
        if command in self._command_handlers:
            try:
                await self._command_handlers[command](*args)
                await self.event_bus.emit(
                    EventTypes.COMMAND_EXECUTED, 
                    {"command": command, "success": True}
                )
            except Exception as e:
                logger.error(f"Error executing command '{command}': {e}")
                await self.event_bus.emit(
                    EventTypes.COMMAND_EXECUTED, 
                    {"command": command, "success": False, "error": str(e)}
                )
        else:
            logger.warning(f"Unknown command: {command}")
            await self.event_bus.emit(
                EventTypes.COMMAND_EXECUTED, 
                {"command": command, "success": False, "error": "Unknown command"}
            )
            await self._handle_help_command()
    
    async def _handle_engage_command(self) -> None:
        """Handle the 'engage' command to enter interactive mode."""
        if self._current_mode == SystemMode.INTERACTIVE:
            logger.info("Already in interactive mode")
            return
            
        await self.change_mode(SystemMode.INTERACTIVE)
        logger.info("Interactive mode engaged. Now listening for voice input.")
    
    async def _handle_ambient_command(self) -> None:
        """Handle the 'ambient' command to enter ambient show mode."""
        if self._current_mode == SystemMode.AMBIENT:
            logger.info("Already in ambient mode")
            return
            
        await self.change_mode(SystemMode.AMBIENT)
        logger.info("Ambient show mode activated.")
    
    async def _handle_disengage_command(self) -> None:
        """Handle the 'disengage' command to return to idle mode."""
        if self._current_mode == SystemMode.IDLE:
            logger.info("Already in idle mode")
            return
            
        await self.change_mode(SystemMode.IDLE)
        logger.info("System disengaged. Returned to idle mode.")
    
    async def _handle_reset_command(self) -> None:
        """Handle the 'reset' command to force cleanup and return to idle mode."""
        logger.info("Executing system reset...")
        
        try:
            # Cancel all active tasks in managers
            if hasattr(self.event_bus, "voice_manager") and self.event_bus.voice_manager:
                await self.event_bus.voice_manager.stop()
                await self.event_bus.voice_manager.start()
                
            if hasattr(self.event_bus, "led_manager") and self.event_bus.led_manager:
                await self.event_bus.led_manager.stop()
                await self.event_bus.led_manager.start()
                
            if hasattr(self.event_bus, "music_manager") and self.event_bus.music_manager:
                await self.event_bus.music_manager.stop()
                await self.event_bus.music_manager.start()
            
            # Force transition to IDLE mode
            await self.change_mode(SystemMode.IDLE)
            logger.info("System reset complete. Returned to idle mode.")
            
        except Exception as e:
            logger.error(f"Error during system reset: {e}")
            # Still try to return to IDLE mode even if cleanup failed
            await self.change_mode(SystemMode.IDLE)
    
    async def _handle_help_command(self) -> None:
        """Handle the 'help' command to display available commands."""
        commands = [
            "engage - Enter interactive voice mode",
            "ambient - Enter ambient show mode with pre-scripted animations",
            "disengage - Return to idle mode",
            "reset - Force cleanup and return to idle mode (emergency recovery)",
            "status - Display current system mode and status",
            "help - Show this help message",
            "quit - Exit the application",
            "",
            "Music commands:",
            "list music - Show available music tracks with numbers",
            "play music <number/name> - Play a track by number or name",
            "stop music - Stop music playback"
        ]
        
        logger.info("Available commands:")
        for cmd in commands:
            logger.info(f"  {cmd}")
    
    async def _handle_status_command(self) -> None:
        """Handle the 'status' command to show system status."""
        logger.info(f"Current system mode: {self._current_mode.value}") 