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
from ..core.event_topics import EventTopics
from ..event_payloads import (
    CliCommandPayload,
    CliResponsePayload,
    ServiceStatus,
    LogLevel
)
from ..utils.command_decorators import compound_command, register_service_commands, validate_compound_command, command_error_handler
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
        
        # Set default command topic for auto-registration
        self._default_command_topic = EventTopics.MODE_COMMAND
        
    async def _start(self) -> None:
        """Initialize the service."""
        self.logger.info("Starting mode command handler service")
        
        # Subscribe to mode command events
        await self.subscribe(EventTopics.MODE_COMMAND, self._handle_mode_command)
        
        # Subscribe to mode change events (so we can respond to mode changes initiated elsewhere)
        await self.subscribe(EventTopics.SYSTEM_MODE_CHANGE, self._handle_mode_change)
        
        # Subscribe to mode transition complete events to handle failures
        await self.subscribe(EventTopics.MODE_TRANSITION_COMPLETE, self._handle_mode_transition_complete)
        
        # Auto-register compound commands using decorators
        register_service_commands(self, self._event_bus)
        self.logger.info("Auto-registered mode commands using decorators")
        
        # Set service status to RUNNING
        self._status = ServiceStatus.RUNNING
        await self._emit_status(ServiceStatus.RUNNING, "Mode command handler ready")
        
    async def _stop(self) -> None:
        """Clean up resources."""
        self.logger.info("Stopping mode command handler service")
        
    async def _handle_mode_command(self, payload: Dict[str, Any]) -> None:
        """Handle mode-related commands.
        For decorated commands like 'engage', 'idle', 'ambient', 'disengage', this handler
        now directly invokes the appropriate decorated method.
        This method also handles other commands like 'status', 'help', 'reset' if they
        are routed here without specific decorated handlers.
        """
        self.logger.debug(f"_handle_mode_command received payload: {payload}")

        # Extract the core command string to check if it's one handled by decorators
        raw_input_command = payload.get("raw_input", "").strip().lower()
        
        # Check for decorated handler commands and invoke them directly
        if raw_input_command == "engage":
            await self.handle_engage(payload)
            return
        elif raw_input_command == "disengage":
            await self.handle_disengage(payload)
            return
        elif raw_input_command == "ambient":
            await self.handle_ambient(payload)
            return
        elif raw_input_command == "idle":
            await self.handle_idle(payload)
            return

        # Handle other commands not managed by decorators
        try:
            # Use .get() for safety as processed_payload is a dict here
            command = payload.get("command", "").lower()
            if not command and isinstance(payload, dict) and "raw_input" in payload:
                 # Fallback for payloads that might just have raw_input
                 command = payload["raw_input"].strip().lower().split()[0]

            response_msg = None
            
            if command == "status":
                current_mode = self._mode_manager.current_mode
                response_msg = f"Current System Mode: {current_mode.name}"
                
            elif command == "help":
                self.logger.debug("Generating help text for help command")
                response_msg = self._get_help_text()
                
            elif command == "reset":
                self.logger.info("Mode command: reset")
                await self._mode_manager.set_mode(SystemMode.IDLE)
                response_msg = "System reset to idle mode."
                
            # Only send a response for commands that don't trigger a mode change
            # Mode change responses will be handled by _handle_mode_change
            if response_msg:
                self.logger.debug(f"Sending response for command '{command}': {response_msg[:30]}...")
                
                # Create the CliResponsePayload
                cli_response = CliResponsePayload(
                    message=response_msg,
                    command=command
                )
                
                # Important: Convert the Pydantic model to a dictionary with model_dump()
                cli_response_dict = cli_response.model_dump()
                
                # Emit the dictionary, not the model directly
                await self.emit(
                    EventTopics.CLI_RESPONSE,
                    cli_response_dict
                )
                
        except Exception as e:
            error_msg = f"Error handling mode command: {e}"
            self.logger.error(error_msg)
            await self._emit_status(
                ServiceStatus.ERROR,
                error_msg,
                severity=LogLevel.ERROR
            )
            
            # Fix: Convert the CliResponsePayload to dict before passing to emit_error_response
            response_payload = CliResponsePayload(
                message=error_msg,
                is_error=True,
                command=command if 'command' in locals() else None
            ).model_dump()
            
            await self.emit_error_response(
                EventTopics.CLI_RESPONSE,
                response_payload
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
                        "Click once to start recording, then click again to stop."
                    )
                except Exception as e:
                    self.logger.warning(f"Could not get microphone info: {e}")
                    response_msg = "Interactive voice mode engaged. Click once to start recording, then click again to stop."
            
            elif new_mode_name == SystemMode.AMBIENT.name:
                response_msg = "Ambient show mode activated. Music will play continuously."
                
            elif new_mode_name == SystemMode.IDLE.name:
                response_msg = "System is now in IDLE mode."
            
            # Send response if we have one
            if response_msg:
                self.logger.debug(f"Sending mode change response for {new_mode_name}: {response_msg[:30]}...")
                
                # Create the CliResponsePayload
                cli_response = CliResponsePayload(
                    message=response_msg,
                    command=f"mode_change_{new_mode_name.lower()}"
                )
                
                # Important: Convert the Pydantic model to a dictionary with model_dump()
                cli_response_dict = cli_response.model_dump()
                
                # Emit the dictionary, not the model directly
                await self.emit(
                    EventTopics.CLI_RESPONSE,
                    cli_response_dict
                )
                
        except Exception as e:
            error_msg = f"Error handling mode change: {e}"
            self.logger.error(error_msg)
            await self._emit_status(
                ServiceStatus.ERROR,
                error_msg,
                severity=LogLevel.ERROR
            )
            # Ensure a CLI response is sent for unexpected errors in this handler
            try:
                await self.emit(
                    EventTopics.CLI_RESPONSE,
                    CliResponsePayload(
                        message=f"Internal error handling mode change: {e}",
                        is_error=True,
                        command=f"mode_change_{payload.get('new_mode', 'unknown').lower()}_error"
                    ).model_dump()
                )
            except Exception as cli_emit_error:
                self.logger.error(f"Failed to emit CLI error response during mode change handling: {cli_emit_error}")
            
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
            self.logger.debug(f"Sending mode transition error: {error_msg}")
            
            # Create the CliResponsePayload
            cli_response = CliResponsePayload(
                message=error_msg,
                is_error=True,
                command=f"mode_change_{new_mode.lower()}"
            )
            
            # Important: Convert the Pydantic model to a dictionary with model_dump()
            cli_response_dict = cli_response.model_dump()
            
            # Emit the dictionary, not the model directly
            await self.emit(
                EventTopics.CLI_RESPONSE,
                cli_response_dict
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
    idle        (i) - Enter idle mode (same as disengage)
    ambient     (a) - Enter ambient mode
    status     (st) - Show system status
    reset       (r) - Reset system state
    quit    (q/exit) - Exit the program
    
  Music Control:
    list music      (l) - List available music
    play music <n>  (p) - Play specified music
    stop music      (s) - Stop music playback
    
  Debug Control:
    debug level <component|all> <level> - Set log level (DEBUG/INFO/WARNING/ERROR) for a component or all components
    
  Eye Control:
    eye status           - Show current eye light controller status
    eye pattern <name>   - Set a specific eye pattern (see patterns below)
    eye test             - Run a test sequence of all eye patterns

    Available Eye Patterns:
      • idle      - Default state, shows a static 3x3 grid
      • startup   - Initial startup animation
      • listening - Pulsing animation when listening for voice input
      • thinking  - Rotating animation while processing input
      • speaking  - Animated pattern while speaking
      • happy     - Upward curved pattern showing happiness
      • sad       - Downward curved pattern showing sadness
      • angry     - Angled lines showing anger
      • surprised - Special pattern for surprise reactions
      • error     - X pattern indicating an error state
    
  Voice Control (in Interactive Mode):
    record    (rec) - Enter text-based recording mode
    done           - Exit recording mode and process text
    [SPACE]        - Press and hold to talk (not implemented yet)
    
  Other:
    help         (h) - Show this help message
""" 

    # Add compound command methods using decorators
    @compound_command("engage")
    @command_error_handler
    async def handle_engage(self, payload: dict) -> None:
        """Handle the 'engage' command to enter interactive mode."""
        self.logger.info("Mode command: engage")
        await self._mode_manager.set_mode(SystemMode.INTERACTIVE)

    @compound_command("disengage")
    @command_error_handler
    async def handle_disengage(self, payload: dict) -> None:
        """Handle the 'disengage' command to return to idle mode."""
        self.logger.info("Mode command: disengage")
        await self._mode_manager.set_mode(SystemMode.IDLE)

    @compound_command("ambient")
    @command_error_handler
    async def handle_ambient(self, payload: dict) -> None:
        """Handle the 'ambient' command to enter ambient mode."""
        self.logger.info("Mode command: ambient")
        await self._mode_manager.set_mode(SystemMode.AMBIENT)

    @compound_command("idle")
    @command_error_handler
    async def handle_idle(self, payload: dict) -> None:
        """Handle the 'idle' command to enter idle mode."""
        self.logger.info("Mode command: idle")
        await self._mode_manager.set_mode(SystemMode.IDLE) 