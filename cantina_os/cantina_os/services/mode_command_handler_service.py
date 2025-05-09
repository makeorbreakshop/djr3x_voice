"""
Mode Command Handler Service for CantinaOS

This service handles mode-specific commands and provides a clean interface
for mode transitions and status reporting.
"""

import logging
from typing import Dict, Optional, Any

from pyee.asyncio import AsyncIOEventEmitter
import sounddevice as sd

from ..base_service import BaseService
from ..event_topics import EventTopics
from ..event_payloads import (
    CliCommandPayload,
    CliResponsePayload,
    ServiceStatus,
    LogLevel
)
from .yoda_mode_manager_service import SystemMode

class ModeCommandHandlerService(BaseService):
    """
    Service that handles mode-specific commands.
    
    Features:
    - Mode transition commands (engage, ambient, disengage)
    - System status reporting
    - Help text management
    - System reset handling
    """
    
    def __init__(
        self,
        event_bus: AsyncIOEventEmitter,
        mode_manager_service,
        config: Optional[Dict[str, Any]] = None,
        logger: Optional[logging.Logger] = None
    ):
        """Initialize the mode command handler service.
        
        Args:
            event_bus: Event bus instance
            mode_manager_service: Reference to YodaModeManagerService
            config: Optional configuration dictionary
            logger: Optional logger instance
        """
        super().__init__("mode_command_handler", event_bus, logger)
        self._mode_manager = mode_manager_service
        
    async def _start(self) -> None:
        """Initialize the service."""
        self.logger.info("Starting mode command handler service")
        
        # Subscribe to mode command events
        await self.subscribe(EventTopics.MODE_COMMAND, self._handle_mode_command)
        
        # Subscribe to mode change events (so we can respond to mode changes initiated elsewhere)
        await self.subscribe(EventTopics.SYSTEM_MODE_CHANGE, self._handle_mode_change)
        
        # Subscribe to mode transition complete events to handle failures
        await self.subscribe(EventTopics.MODE_TRANSITION_COMPLETE, self._handle_mode_transition_complete)
        
        # Set service status to RUNNING
        self._status = ServiceStatus.RUNNING
        await self._emit_status(ServiceStatus.RUNNING, "Mode command handler ready")
        
    async def _stop(self) -> None:
        """Clean up resources."""
        self.logger.info("Stopping mode command handler service")
        
    async def _handle_mode_command(self, payload: Dict[str, Any]) -> None:
        """Handle mode-related commands.
        
        Args:
            payload: The command payload
        """
        try:
            if not isinstance(payload, CliCommandPayload):
                try:
                    payload = CliCommandPayload(**payload)
                except Exception as e:
                    error_msg = f"Error handling mode command: {e}"
                    self.logger.error(error_msg)
                    await self._emit_status(
                        ServiceStatus.ERROR,
                        error_msg,
                        severity=LogLevel.ERROR
                    )
                    await self.emit_error_response(
                        EventTopics.CLI_RESPONSE,
                        CliResponsePayload(
                            message=error_msg,
                            is_error=True,
                            command=None
                        )
                    )
                    return
                
            command = payload.command.lower()
            response_msg = None
            
            if command == "engage":
                self.logger.info("Mode command: engage")
                # No longer need to send a response here, as it will be handled by _handle_mode_change
                await self._mode_manager.set_mode(SystemMode.INTERACTIVE)
                
            elif command == "ambient":
                self.logger.info("Mode command: ambient")
                await self._mode_manager.set_mode(SystemMode.AMBIENT)
                # No longer need to send a response here, as it will be handled by _handle_mode_change
                
            elif command == "disengage":
                self.logger.info("Mode command: disengage")
                await self._mode_manager.set_mode(SystemMode.IDLE)
                # No longer need to send a response here, as it will be handled by _handle_mode_change
                
            elif command == "status":
                current_mode = self._mode_manager.current_mode
                response_msg = f"Current System Mode: {current_mode.name}"
                
            elif command == "help":
                response_msg = self._get_help_text()
                
            elif command == "reset":
                self.logger.info("Mode command: reset")
                await self._mode_manager.set_mode(SystemMode.IDLE)
                response_msg = "System reset to idle mode."
                
            # Only send a response for commands that don't trigger a mode change
            # Mode change responses will be handled by _handle_mode_change
            if response_msg:
                await self.emit(
                    EventTopics.CLI_RESPONSE,
                    CliResponsePayload(
                        message=response_msg,
                        command=command
                    )
                )
            # No need for the else case with default confirmation message
                
        except Exception as e:
            error_msg = f"Error handling mode command: {e}"
            self.logger.error(error_msg)
            await self._emit_status(
                ServiceStatus.ERROR,
                error_msg,
                severity=LogLevel.ERROR
            )
            await self.emit_error_response(
                EventTopics.CLI_RESPONSE,
                CliResponsePayload(
                    message=error_msg,
                    is_error=True,
                    command=command if 'command' in locals() else None
                )
            )
            
    async def _handle_mode_change(self, payload: Dict[str, Any]) -> None:
        """Handle mode change events and send appropriate user messages.
        
        Args:
            payload: The mode change payload
        """
        try:
            # Extract mode information
            new_mode_name = payload.get("new_mode", "")
            
            # Skip if we can't determine the new mode
            if not new_mode_name:
                return
                
            # Generate appropriate response based on the new mode
            response_msg = None
            
            if new_mode_name == SystemMode.INTERACTIVE.name:
                # Get microphone info using sounddevice
                try:
                    devices = sd.query_devices()
                    default_input = sd.default.device[0]  # Get default input device index
                    mic_info = devices[default_input]
                    response_msg = (
                        f"Interactive voice mode engaged. Using microphone: {mic_info['name']} "
                        f"({mic_info['max_input_channels']} channels)\n"
                        "Type 'rec' to start recording, then 'done' when finished speaking."
                    )
                except Exception as e:
                    self.logger.warning(f"Could not get microphone info: {e}")
                    response_msg = "Interactive voice mode engaged. Type 'rec' to start recording, then 'done' when finished speaking."
            
            elif new_mode_name == SystemMode.AMBIENT.name:
                response_msg = "Ambient show mode activated. Music will play continuously."
                
            elif new_mode_name == SystemMode.IDLE.name:
                response_msg = "System is now in IDLE mode."
            
            # Send response if we have one
            if response_msg:
                await self.emit(
                    EventTopics.CLI_RESPONSE,
                    CliResponsePayload(
                        message=response_msg,
                        command=f"mode_change_{new_mode_name.lower()}"
                    )
                )
                
        except Exception as e:
            error_msg = f"Error handling mode change: {e}"
            self.logger.error(error_msg)
            await self._emit_status(
                ServiceStatus.ERROR,
                error_msg,
                severity=LogLevel.ERROR
            )
            
    async def _handle_mode_transition_complete(self, payload: Dict[str, Any]) -> None:
        """Handle mode transition complete events, especially failures.
        
        Args:
            payload: The mode transition payload
        """
        try:
            # Extract mode information
            status = payload.get("status", "")
            
            # Only handle failed transitions
            if status != "failed":
                return
                
            new_mode = payload.get("new_mode", "UNKNOWN")
            error = payload.get("error", "Unknown error")
            
            # Send error message
            error_msg = f"Error changing mode to {new_mode}: {error}"
            await self.emit(
                EventTopics.CLI_RESPONSE,
                CliResponsePayload(
                    message=error_msg,
                    is_error=True,
                    command=f"mode_change_{new_mode.lower()}"
                )
            )
                
        except Exception as e:
            error_msg = f"Error handling mode transition event: {e}"
            self.logger.error(error_msg)
            await self._emit_status(
                ServiceStatus.ERROR,
                error_msg,
                severity=LogLevel.ERROR
            )
            
    def _get_help_text(self) -> str:
        """Return formatted help text."""
        return """
Available Commands:
  System Control:
    engage      (e) - Engage DJ R3X (Press SPACE to talk)
    disengage   (d) - Disengage DJ R3X
    ambient     (a) - Enter ambient mode
    status     (st) - Show system status
    reset       (r) - Reset system state
    quit    (q/exit) - Exit the program
    
  Music Control:
    list music      (l) - List available music
    play music <n>  (p) - Play specified music
    stop music      (s) - Stop music playback
    
  Voice Control (in Interactive Mode):
    record    (rec) - Enter text-based recording mode
    done           - Exit recording mode and process text
    [SPACE]        - Press and hold to talk (not implemented yet)
    
  Other:
    help         (h) - Show this help message
""" 