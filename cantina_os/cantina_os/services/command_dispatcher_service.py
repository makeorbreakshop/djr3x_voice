"""
Command Dispatcher Service for CantinaOS

This service handles routing of CLI commands to appropriate service handlers
using a registration-based system. It decouples command processing from the CLI
interface and ensures proper event-driven communication.
"""

import logging
from typing import Dict, Tuple, Optional, Any, List
import asyncio

from pyee.asyncio import AsyncIOEventEmitter

from ..base_service import BaseService
from ..event_topics import EventTopics
from ..event_payloads import (
    CliCommandPayload,
    CliResponsePayload,
    ServiceStatus,
    LogLevel,
    StandardCommandPayload
)
from ..event_bus import EventBus

class CommandDispatcherService(BaseService):
    """
    Service that routes CLI commands to appropriate handlers.
    
    Features:
    - Command registration system
    - Event-based command routing
    - Command validation
    - Error handling and reporting
    
    This is the central routing point for ALL commands in the system.
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
        
        # Command routing registry structures
        self.command_handlers: Dict[str, str] = {}  # Basic commands: command -> topic
        self.compound_commands: Dict[str, str] = {}  # Compound commands: "cmd subcmd" -> topic
        
        # Command shortcuts for easier access
        self.shortcuts: Dict[str, str] = {
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
            'stop': 'stop music',
            'i': 'idle',  # Added shortcut for idle command
            'dj': 'dj start',  # Added DJ mode shortcuts
            'djs': 'dj stop',
            'djn': 'dj next',
            'djq': 'dj queue'
        }
        
        # Mode mappings for mode change commands
        self.mode_commands: Dict[str, str] = {
            'engage': 'INTERACTIVE',
            'disengage': 'IDLE',
            'ambient': 'AMBIENT',
            'idle': 'IDLE',  # Added 'idle' as an alias for IDLE mode
        }
        
    async def _start(self) -> None:
        """Initialize the service and register command handlers."""
        self.logger.info("Starting CommandDispatcherService")
        
        # Register for command events - the only subscription needed
        await self.subscribe(EventTopics.CLI_COMMAND, self._handle_command)
        await self.subscribe(EventTopics.REGISTER_COMMAND, self._handle_register_command)
        
        # Subscribe to CLI help and status request topics to directly handle these commands
        # instead of just routing them to topics no one is listening to
        await self.subscribe(EventTopics.CLI_HELP_REQUEST, self._handle_help_request)
        await self.subscribe(EventTopics.CLI_STATUS_REQUEST, self._handle_status_request)
        
        # Subscribe to service-specific command topics for consistent logging and monitoring
        service_topics = [
            EventTopics.MUSIC_COMMAND,
            EventTopics.EYE_COMMAND,
            EventTopics.DEBUG_COMMAND,
            EventTopics.SYSTEM_SET_MODE_REQUEST
        ]
        
        for topic in service_topics:
            # Use lambda to capture the topic for the handler
            handler = lambda payload, t=topic: self._handle_service_command(payload, t)
            self._event_bus.on(topic, handler)
            self.logger.debug(f"Subscribed to service topic: {topic}")
        
        # Register basic commands that are directly handled by this service
        # Note: Other commands should be registered by main.py during system initialization
        self.register_command_handler("help", EventTopics.CLI_HELP_REQUEST)
        self.register_command_handler("status", EventTopics.CLI_STATUS_REQUEST)
        self.register_command_handler("reset", EventTopics.CLI_STATUS_REQUEST)
        
        # Note that we are NOT registering all commands here - this is just a subset
        # The main application will register the rest in _register_commands
        self.logger.info("Registered internal commands only - main.py will register the rest")
        self.logger.debug(f"Initial command handlers: {list(self.command_handlers.keys())}")
        self.logger.debug(f"Initial compound commands: {list(self.compound_commands.keys())}")
        
        self.logger.info("CommandDispatcherService initialized")
        
        # Set service status to RUNNING
        self._status = ServiceStatus.RUNNING
        await self._emit_status(ServiceStatus.RUNNING, "Command dispatcher ready")
        
    async def _stop(self) -> None:
        """Clean up resources."""
        self.logger.info("Stopping command dispatcher service")
        self.command_handlers.clear()
        self.compound_commands.clear()
        
    def register_command_handler(self, command: str, event_topic: str) -> None:
        """
        Register a handler for a specific command
        
        Args:
            command: The command name (e.g., "eye", "music")
            event_topic: The event topic to send the command to
        """
        self.command_handlers[command.lower()] = event_topic
        self.logger.debug(f"Registered handler for command '{command}' to topic '{event_topic}'")

    def register_compound_command(self, command: str, event_topic: str) -> None:
        """
        Register a handler for a compound command
        
        Args:
            command: The compound command (e.g., "eye pattern", "play music")
            event_topic: The event topic to send the command to
        """
        self.compound_commands[command.lower()] = event_topic
        self.logger.debug(f"Registered handler for compound command '{command}' to topic '{event_topic}'")
        
    async def _handle_command(self, payload: Dict[str, Any]) -> None:
        """
        Handle an incoming CLI command
        
        This is the central entry point for all commands. It:
        1. Parses the command and arguments
        2. Expands shortcuts
        3. Handles special cases (mode commands, etc.)
        4. Routes to the appropriate handler via events
        
        Args:
            payload: The command payload from CLI service
        """
        try:
            self.logger.debug(f"Handling command payload: {payload}")
            
            # Extract command information
            if not isinstance(payload, dict):
                raise ValueError(f"Invalid command payload type: {type(payload)}")
                
            # First check if this is a CliCommandPayload
            command = payload.get("command", "").lower()
            args = payload.get("args", [])
            raw_input = payload.get("raw_input", "")
            conversation_id = payload.get("conversation_id")
            
            if not command and not raw_input:
                self.logger.warning("Empty command received")
                return
                
            # Convert to StandardCommandPayload for consistent processing
            if raw_input:
                # Create directly from raw input when available (preferred)
                cmd_payload = StandardCommandPayload.from_raw_input(
                    raw_input, 
                    conversation_id
                )
            else:
                # Fall back to legacy format if needed
                cmd_payload = StandardCommandPayload.from_legacy_format(payload)
                
            self.logger.debug(f"Standardized command payload: {cmd_payload}")
            
            # Process shortcuts
            base_command = cmd_payload.command
            if base_command in self.shortcuts:
                # Handle shortcut by updating the cmd_payload with expanded command
                expanded = self.shortcuts[base_command]
                self.logger.debug(f"Expanding shortcut '{base_command}' to '{expanded}'")
                
                # Re-parse with the expanded command
                if " " in expanded:  # For compound command shortcuts (e.g., 'l' -> 'list music')
                    cmd_payload = StandardCommandPayload.from_raw_input(
                        expanded + (" " + " ".join(cmd_payload.args) if cmd_payload.args else ""),
                        conversation_id
                    )
                else:  # For simple command shortcuts (e.g., 'h' -> 'help')
                    cmd_payload = StandardCommandPayload(
                        command=expanded,
                        subcommand=cmd_payload.subcommand,
                        args=cmd_payload.args,
                        raw_input=raw_input,
                        conversation_id=cmd_payload.conversation_id
                    )
                    
                self.logger.debug(f"Expanded command payload: {cmd_payload}")
            
            # Handle mode commands specially (engage, disengage, ambient)
            if cmd_payload.command in self.mode_commands:
                mode = self.mode_commands[cmd_payload.command]
                self.logger.info(f"Mode command: {cmd_payload.command} -> {mode}")
                
                await self.emit(
                    EventTopics.SYSTEM_SET_MODE_REQUEST,
                    {
                        "mode": mode,
                        "conversation_id": cmd_payload.conversation_id
                    }
                )
                return
            
            # Handle compound commands first
            full_command = cmd_payload.get_full_command()
            if full_command in self.compound_commands:
                topic = self.compound_commands[full_command]
                self.logger.info(f"Routing compound command: '{full_command}' to topic: {topic}")
                
                # Split the compound command into parts
                parts = full_command.split()
                base_command = parts[0]
                subcommand = parts[1] if len(parts) > 1 else None
                
                # Any remaining args from the original command
                remaining_args = cmd_payload.args
                
                # Construct the proper payload
                command_payload = {
                    "command": base_command,
                    "subcommand": subcommand,
                    "args": remaining_args,
                    "raw_input": cmd_payload.raw_input,
                    "conversation_id": cmd_payload.conversation_id
                }
                
                # Send the properly structured payload
                await self.emit(topic, command_payload)
                return
                
            # Handle basic commands
            if cmd_payload.command in self.command_handlers:
                topic = self.command_handlers[cmd_payload.command]
                self.logger.info(f"Routing command: '{cmd_payload.command}' to topic: {topic}")
                
                # Special handling for system commands
                if (topic == EventTopics.SYSTEM_SET_MODE_REQUEST and 
                    cmd_payload.command in self.mode_commands):
                    # Already handled above
                    return
                
                # Special handling for "eye" command without subcommand
                if cmd_payload.command == "eye" and not cmd_payload.subcommand and not cmd_payload.args:
                    await self._send_error("Eye command requires a subcommand: pattern, test, or status")
                    return
                    
                # Send StandardCommandPayload
                await self.emit(topic, cmd_payload.model_dump())
                return
                
            # Special case for "play music N" pattern
            if raw_input and raw_input.strip().lower().startswith("play music "):
                parts = raw_input.strip().split()
                track_index = None
                
                # Extract track number
                if len(parts) > 2 and parts[2].isdigit():
                    track_index = parts[2]
                    
                self.logger.info(f"Special case: 'play music {track_index}'")
                
                # Construct and emit a music command payload
                await self.emit(
                    EventTopics.MUSIC_COMMAND,
                    {
                        "command": "play",
                        "args": ["music", track_index] if track_index else ["music"],
                        "raw_input": raw_input,
                        "conversation_id": conversation_id
                    }
                )
                return
                
            # Unknown command
            self.logger.warning(f"Unknown command: '{base_command}'")
            
            # Format help suggestion
            registered_commands = ", ".join(sorted(list(self.command_handlers.keys()) + list(self.compound_commands.keys())))
            help_message = f"Unknown command: '{base_command}'. Try 'help' for a list of available commands."
            
            await self._send_error(help_message)
                
        except Exception as e:
            self.logger.error(f"Error processing command: {e}", exc_info=True)
            await self._send_error(f"Error processing command: {str(e)}")
            
    async def _handle_register_command(self, payload: dict) -> None:
        """
        Handle command registration requests
        
        Args:
            payload: Command registration payload with:
                - command: Command name or pattern
                - handler_service: Name of handling service
                - event_topic: Topic to emit for this command
        """
        try:
            command = payload.get("command", "").lower()
            handler_service = payload.get("handler_service", "")
            event_topic = payload.get("event_topic", "")
            
            if not command or not handler_service or not event_topic:
                self.logger.error(f"Invalid command registration payload: {payload}")
                return
            
            # Determine if this is a compound command (contains space)
            if " " in command:
                self.register_compound_command(command, event_topic)
            else:
                self.register_command_handler(command, event_topic)
                
            self.logger.info(
                f"Registered command '{command}' to service '{handler_service}' "
                f"with topic '{event_topic}'"
            )
            
        except Exception as e:
            self.logger.error(f"Error handling command registration: {e}", exc_info=True)
            
    async def _send_error(self, message: str) -> None:
        """Send an error response to the CLI"""
        await self.emit(
            EventTopics.CLI_RESPONSE,
            {
                "message": message,
                "is_error": True
            }
        )
        
    async def _send_success(self, message: str) -> None:
        """Send a success response to the CLI"""
        await self.emit(
            EventTopics.CLI_RESPONSE,
            {
                "message": message,
                "is_error": False
            }
        )
        
    def get_registered_commands(self) -> List[str]:
        """Get a list of all registered commands"""
        # Combine basic and compound commands
        commands = list(self.command_handlers.keys())
        commands.extend(self.compound_commands.keys())
        return sorted(commands)
        
    def get_shortcut_map(self) -> Dict[str, str]:
        """Get the shortcut mapping"""
        return self.shortcuts.copy()

    async def _handle_help_request(self, payload: Dict[str, Any]) -> None:
        """
        Handle 'help' command requests
        
        Displays a list of all available commands and shortcuts.
        
        Args:
            payload: The command payload
        """
        self.logger.info("Handling help request")
        
        try:
            # Get all registered basic commands
            basic_commands = sorted(list(self.command_handlers.keys()))
            
            # Get all registered compound commands
            compound_commands = sorted(list(self.compound_commands.keys()))
            
            # Get all shortcuts
            shortcuts = self.shortcuts
            
            # Group commands by category for cleaner display
            command_categories = {
                "Mode Commands": ["engage", "disengage", "ambient"],
                "Eye Commands": ["eye pattern", "eye test", "eye status"],
                "Music Commands": ["play music", "stop music", "list music"],
                "DJ Mode Commands": ["dj start", "dj stop", "dj next", "dj queue"],
                "System Commands": ["help", "status", "reset"],
                "Debug Commands": ["debug level", "debug trace"]
            }
            
            # Build help text
            help_lines = ["Available commands:"]
            
            # Add categorized commands
            for category, cmds in command_categories.items():
                category_commands = [cmd for cmd in cmds if cmd in basic_commands or cmd in compound_commands]
                if category_commands:
                    help_lines.append(f"\n{category}:")
                    if category == "Eye Commands":
                        help_lines.extend([f"  • {cmd}" for cmd in sorted(category_commands)])
                        help_lines.append("\n  Available Eye Patterns:")
                        help_lines.extend([
                            "    • idle      - Default state, shows a static 3x3 grid",
                            "    • startup   - Initial startup animation",
                            "    • listening - Pulsing animation when listening for voice input",
                            "    • thinking  - Rotating animation while processing input",
                            "    • speaking  - Animated pattern while speaking",
                            "    • happy     - Upward curved pattern showing happiness",
                            "    • sad       - Downward curved pattern showing sadness",
                            "    • angry     - Angled lines showing anger",
                            "    • surprised - Special pattern for surprise reactions",
                            "    • error     - X pattern indicating an error state"
                        ])
                    elif category == "DJ Mode Commands":
                        help_lines.extend([f"  • {cmd}" for cmd in sorted(category_commands)])
                        help_lines.append("\n  DJ Mode Commands:")
                        help_lines.extend([
                            "    • dj start        - Start DJ mode with automatic track transitions",
                            "    • dj stop         - Stop DJ mode and return to normal playback",
                            "    • dj next         - Skip to next track with DJ commentary",
                            "    • dj queue <track> - Queue a specific track to play next"
                        ])
                    else:
                        help_lines.extend([f"  • {cmd}" for cmd in sorted(category_commands)])
            
            # Add other commands not in categories
            other_basic = [cmd for cmd in basic_commands if not any(cmd in cat_cmds for cat_cmds in command_categories.values())]
            other_compound = [cmd for cmd in compound_commands if not any(cmd in cat_cmds for cat_cmds in command_categories.values())]
            other_commands = sorted(other_basic + other_compound)
            
            if other_commands:
                help_lines.append("\nOther Commands:")
                help_lines.extend([f"  • {cmd}" for cmd in other_commands])
            
            # Add shortcuts
            help_lines.append("\nShortcuts:")
            shortcut_lines = [f"  • {key} -> {val}" for key, val in sorted(shortcuts.items())]
            help_lines.extend(shortcut_lines)
            
            # Join all lines
            help_text = "\n".join(help_lines)
            
            # Send help text response
            await self._send_success(help_text)
            
        except Exception as e:
            self.logger.error(f"Error handling help request: {e}", exc_info=True)
            await self._send_error(f"Error generating help: {str(e)}")
            
    async def _handle_status_request(self, payload: Dict[str, Any]) -> None:
        """
        Handle 'status' and 'reset' command requests
        
        Provides detailed information about the command system status
        or initiates a system reset.
        
        Args:
            payload: The command payload
        """
        try:
            # Check if this is a reset command
            if payload.get("command") == "reset":
                self.logger.info("Handling reset request")
                await self.emit(
                    EventTopics.CLI_RESPONSE, 
                    {
                        "message": "System reset initiated. Resetting services...",
                        "is_error": False
                    }
                )
                # Emit event for system reset
                await self.emit(
                    EventTopics.SYSTEM_SHUTDOWN_REQUESTED,
                    {
                        "reason": "User requested reset",
                        "restart": True  # Set to True to indicate restart not shutdown
                    }
                )
                return
            
            # Regular status command 
            self.logger.info("Handling status request")
            
            # Get command system statistics
            basic_count = len(self.command_handlers)
            compound_count = len(self.compound_commands)
            shortcut_count = len(self.shortcuts)
            total_commands = basic_count + compound_count
            
            # Format the status message
            status_lines = [
                "Command System Status:",
                f"• Command Dispatcher: ACTIVE",
                f"• Total Commands: {total_commands}",
                f"  - Basic Commands: {basic_count}",
                f"  - Compound Commands: {compound_count}",
                f"  - Command Shortcuts: {shortcut_count}",
                "\nRegistered Command Topics:",
            ]
            
            # Add command topics
            topic_summary = {}
            for topic in {v for v in self.command_handlers.values()}:
                topic_name = topic.split("/")[-1] if "/" in topic else topic
                topic_summary[topic_name] = topic_summary.get(topic_name, 0) + 1
                
            for topic, count in sorted(topic_summary.items()):
                status_lines.append(f"• {topic}: {count} commands")
            
            # Send the status message
            await self._send_success("\n".join(status_lines))
            
        except Exception as e:
            self.logger.error(f"Error handling status request: {e}", exc_info=True)
            await self._send_error(f"Error generating status: {str(e)}")

    async def _handle_service_command(self, payload: Dict[str, Any], topic: str = None) -> None:
        """
        Handle service-specific commands (monitoring/logging function)
        
        This method provides consistent logging for all service commands
        without interfering with their actual processing.
        
        Args:
            payload: The command payload
            topic: The event topic (injected by pyee)
        """
        try:
            # Log command for consistent tracking
            if isinstance(payload, dict):
                # Extract command info for logging
                cmd_info = ""
                if "command" in payload:
                    cmd_info += f"{payload['command']}"
                    if "subcommand" in payload and payload["subcommand"]:
                        cmd_info += f" {payload['subcommand']}"
                    if "args" in payload and payload["args"]:
                        cmd_info += f" {' '.join(payload['args'])}"
                elif "raw_input" in payload:
                    cmd_info = payload["raw_input"]
                
                # Get a readable topic name
                topic_parts = topic.split("/") if topic else []
                topic_name = topic_parts[-1] if len(topic_parts) > 1 else "service"
                
                # Log the command
                self.logger.info(f"Processing {topic_name} command: {cmd_info}")
                
        except Exception as e:
            self.logger.error(f"Error in service command monitoring: {e}")
            # This is just for monitoring, so don't block command processing on errors 