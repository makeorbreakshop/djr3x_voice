"""
Command Dispatcher Service for CantinaOS

This service handles routing of CLI commands to appropriate service handlers
using a registration-based system. It decouples command processing from the CLI
interface and ensures proper event-driven communication.
"""

import logging
from typing import Dict, Tuple, Optional, Any

from pyee.asyncio import AsyncIOEventEmitter

from ..base_service import BaseService
from ..event_topics import EventTopics
from ..event_payloads import (
    CliCommandPayload,
    CliResponsePayload,
    ServiceStatus,
    LogLevel
)

class CommandDispatcherService(BaseService):
    """
    Service that routes CLI commands to appropriate handlers.
    
    Features:
    - Command registration system
    - Event-based command routing
    - Command validation
    - Error handling and reporting with graceful degradation
    """
    
    def __init__(
        self,
        event_bus: AsyncIOEventEmitter,
        config: Optional[Dict[str, Any]] = None,
        logger: Optional[logging.Logger] = None
    ):
        """Initialize the command dispatcher service.
        
        Args:
            event_bus: Event bus instance
            config: Optional configuration dictionary
            logger: Optional logger instance
        """
        super().__init__("command_dispatcher", event_bus, logger)
        
        # Command routing registry
        self._command_handlers: Dict[str, Tuple[str, str]] = {}  # command -> (handler_service, event_topic)
        self._shortcuts = {
            'e': 'engage',
            'd': 'disengage',
            'a': 'ambient',
            'st': 'status',
            'r': 'reset',
            'q': 'quit',
            'h': 'help',
            'l': 'list music',
            'p': 'play music',
            's': 'stop music',
            'stop': 'stop music'
        }
        
    async def _start(self) -> None:
        """Initialize the service."""
        self.logger.info("Starting command dispatcher service")
        
        # Subscribe to CLI commands
        await self.subscribe(EventTopics.CLI_COMMAND, self._handle_command)
        
        # Set service status to RUNNING
        self._status = ServiceStatus.RUNNING
        await self._emit_status(ServiceStatus.RUNNING, "Command dispatcher ready")
        
    async def _stop(self) -> None:
        """Clean up resources."""
        self.logger.info("Stopping command dispatcher service")
        self._command_handlers.clear()
        
    async def register_command(
        self,
        command: str,
        handler_service: str,
        event_topic: str
    ) -> None:
        """Register a command handler.
        
        Args:
            command: The command to handle (e.g., 'engage', 'ambient')
            handler_service: Name of the service that handles this command
            event_topic: Event topic to emit for this command
        """
        self._command_handlers[command.lower()] = (handler_service, event_topic)
        self.logger.debug(
            f"Registered command '{command}' to service '{handler_service}' "
            f"with topic '{event_topic}'"
        )
        
    async def _handle_command(self, payload: dict) -> None:
        """Handle a command from the CLI.
        
        Args:
            payload: Command payload with command and args
        """
        try:
            command = payload.get("command", "").strip().lower()
            args = payload.get("args", [])
            raw_input = payload.get("raw_input", "")
            conversation_id = payload.get("conversation_id")
            
            # Apply shortcuts if applicable
            if command in self._shortcuts:
                expanded = self._shortcuts[command]
                parts = expanded.split()
                command = parts[0]
                args = parts[1:] + args
                
            # Build full command for handler lookup
            full_command = f"{command}"
            if args:
                full_command = f"{command} {args[0]}"
            
            # Find handler for command
            handler_info = self._command_handlers.get(full_command)
            
            if handler_info:
                handler_service, event_topic = handler_info
                # Emit event to appropriate handler
                await self.emit(
                    event_topic,
                    {
                        "command": command,
                        "args": args,
                        "raw_input": raw_input,
                        "conversation_id": conversation_id
                    }
                )
            else:
                self.logger.warning(f"Unknown command: '{command}'")
                await self.emit(
                    EventTopics.CLI_RESPONSE,
                    {
                        "message": f"Error: Unknown command: {command}",
                        "is_error": True
                    }
                )
                
        except Exception as e:
            self.logger.error(f"Error handling command: {e}")
            await self.emit(
                EventTopics.CLI_RESPONSE,
                {
                    "message": f"Error handling command: {e}",
                    "is_error": True
                }
            )
        
    async def _handle_help(self) -> None:
        """Handle the help command."""
        help_text = """
Available commands:
  help      - Show this help message
  engage    - Enter interactive voice mode
  disengage - Return to idle mode
  ambient   - Enter ambient show mode
"""
        await self._emit_response(help_text)
        
    async def _handle_mode_command(self, command: str) -> None:
        """Handle a mode change command.
        
        Args:
            command: The mode command to handle
        """
        mode_map = {
            "engage": "INTERACTIVE",
            "disengage": "IDLE",
            "ambient": "AMBIENT"
        }
        
        new_mode = mode_map.get(command)
        if new_mode:
            await self.emit(
                EventTopics.SYSTEM_SET_MODE_REQUEST,
                {"mode": new_mode}
            )
            
            # Emit an initial acknowledgment that the command was received
            # (The actual mode change confirmation will come from the mode manager)
            await self._emit_response(f"Processing {command} command...")
        
    async def _emit_response(self, message: str) -> None:
        """Emit a CLI response event.
        
        Args:
            message: Response message to emit
        """
        await self.emit(
            EventTopics.CLI_RESPONSE,
            CliResponsePayload(message=message)
        )
        
    async def _emit_error(self, message: str, command: Optional[str] = None) -> None:
        """Emit a CLI error response.
        
        Args:
            message: Error message to emit
            command: The command that caused the error (if any)
        """
        self.logger.debug(f"Emitting error: {message}")
        await self.emit(
            EventTopics.CLI_RESPONSE,
            CliResponsePayload(
                message=f"Error: {message}",
                is_error=True,
                command=command
            )
        ) 