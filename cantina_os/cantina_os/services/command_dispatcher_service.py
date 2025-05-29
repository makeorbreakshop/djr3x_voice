"""
Command Dispatcher Service for CantinaOS

This service handles routing of CLI commands to appropriate service handlers
using a registration-based system. It decouples command processing from the CLI
interface and ensures proper event-driven communication.
"""

import logging
from typing import Dict, Tuple, Optional, Any, List
import asyncio
import warnings

from pyee.asyncio import AsyncIOEventEmitter

from ..base_service import BaseService
from ..core.event_topics import EventTopics
from ..event_payloads import (
    CliCommandPayload,
    CliResponsePayload,
    ServiceStatus,
    LogLevel,
    StandardCommandPayload
)
from ..event_bus import EventBus
from ..utils.command_decorators import standardize_command_payload, validate_command_payload

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
            'dj': 'dj start',  # Updated DJ mode shortcuts to use full command strings
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
        [DEPRECATED] Register a handler for a specific command
        Use register_command() instead which includes service context.
        
        Args:
            command: The command name (e.g., "eye", "music")
            event_topic: The event topic to send the command to
        """
        warnings.warn(
            "register_command_handler is deprecated. Use register_command with service context instead.",
            DeprecationWarning,
            stacklevel=2
        )
        self.command_handlers[command.lower()] = event_topic
        self.logger.debug(f"Registered handler for command '{command}' to topic '{event_topic}'")

    def register_compound_command(self, command: str, event_topic: str) -> None:
        """
        [DEPRECATED] Register a handler for a compound command
        Use register_command() instead which includes service context.
        
        Args:
            command: The compound command (e.g., "eye pattern", "play music")
            event_topic: The event topic to send the command to
        """
        warnings.warn(
            "register_compound_command is deprecated. Use register_command with service context instead.",
            DeprecationWarning,
            stacklevel=2
        )
        self.compound_commands[command.lower()] = event_topic
        self.logger.debug(f"Registered handler for compound command '{command}' to topic '{event_topic}'")
        
    def register_command(self, command: str, service_name: str, event_topic: str) -> None:
        """
        Register a command with service name and topic
        
        This is the preferred method for command registration as it tracks both
        the event topic and the service that will handle the command, enabling
        proper payload transformation.
        
        Args:
            command: The command string (e.g., "eye", "dj start")
            service_name: Name of the service that will handle this command
            event_topic: The event topic to send the command to
        """
        command = command.lower()
        
        if " " in command:
            # Compound command (e.g., "dj start")
            self.compound_commands[command] = {
                "service": service_name,
                "topic": event_topic
            }
            self.logger.debug(f"Registered compound command '{command}' to service '{service_name}' with topic '{event_topic}'")
        else:
            # Basic command (e.g., "eye")
            self.command_handlers[command] = {
                "service": service_name,
                "topic": event_topic
            }
            self.logger.debug(f"Registered command '{command}' to service '{service_name}' with topic '{event_topic}'")
            
    def _create_standardized_payload(self, command: str, args: list, raw_input: str) -> dict:
        """
        Create standardized payload for all services using consistent format.
        
        This replaces the complex service-specific transformation logic with
        a simple, consistent format that all services can handle via decorators.
        
        Args:
            command: The command string
            args: Command arguments  
            raw_input: Raw command input
            
        Returns:
            Standardized payload that works with @compound_command decorators
        """
        # Use our standardized payload format
        return standardize_command_payload({
            "command": command,
            "args": args,
            "raw_input": raw_input
        }, command)
        
    async def _handle_command(self, payload: Dict[str, Any]) -> None:
        """Handle a CLI command.
        
        Args:
            payload: Command payload with command string and args
        """
        try:
            # Validate payload structure first
            if not validate_command_payload(payload):
                error_msg = f"Invalid command payload structure. Required fields: command, args, raw_input. Got: {list(payload.keys())}"
                self.logger.error(error_msg)
                await self._send_error(error_msg)
                return
            
            # Extract command and args
            raw_input = payload.get("raw_input", "").strip().lower()
            command = payload.get("command", "").lower()
            args = payload.get("args", [])
            
            # First check for compound commands (e.g. "dj start", "play music")
            compound_cmd = None
            for cmd in self.compound_commands:
                if raw_input.startswith(cmd):
                    compound_cmd = cmd
                    break
                    
            if compound_cmd:
                # Found a matching compound command
                service_info = self.compound_commands[compound_cmd]
                
                # Get service info and topic based on new or old registration format
                if isinstance(service_info, dict):
                    service_name = service_info["service"]
                    topic = service_info["topic"]
                else:
                    # Legacy format without service name
                    service_name = "unknown"
                    topic = service_info
                
                # Get any remaining args after the compound command
                remaining_input = raw_input[len(compound_cmd):].strip()
                remaining_args = remaining_input.split() if remaining_input else []
                
                # Transform the payload based on service expectations
                cmd_payload = self._create_standardized_payload(
                    compound_cmd,
                    remaining_args,
                    raw_input
                )
                
                # Emit the command event
                self.logger.info(f"Routing command '{compound_cmd}' to service '{service_name}' on topic '{topic}'")
                await self.emit(topic, cmd_payload)
                return
                
            # Check for basic commands if no compound command matched
            if command in self.command_handlers:
                service_info = self.command_handlers[command]
                
                # Get service info and topic based on new or old registration format
                if isinstance(service_info, dict):
                    service_name = service_info["service"]
                    topic = service_info["topic"]
                else:
                    # Legacy format without service name
                    service_name = "unknown"
                    topic = service_info
                
                # Transform the payload based on service expectations
                cmd_payload = self._create_standardized_payload(
                    command,
                    args,
                    raw_input
                )
                
                # Emit the command event
                self.logger.info(f"Routing command '{command}' to service '{service_name}' on topic '{topic}'")
                await self.emit(topic, cmd_payload)
                return
                
            # Unknown command
            self.logger.warning(f"Unknown command: '{raw_input}'")
            
            # Format help suggestion
            registered_commands = ", ".join(sorted(list(self.command_handlers.keys()) + list(self.compound_commands.keys())))
            help_message = f"Unknown command: '{raw_input}'. Try 'help' for a list of available commands."
            
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
            
            # Use the new standardized registration method
            self.register_command(command, handler_service, event_topic)
                
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
            
            # Extract topics from command handlers (which now store dicts with service and topic)
            for handler_info in self.command_handlers.values():
                if isinstance(handler_info, dict):
                    topic = handler_info.get("topic", "unknown")
                else:
                    # Fallback for old string format
                    topic = handler_info
                    
                topic_name = topic.split("/")[-1] if "/" in topic else topic
                topic_summary[topic_name] = topic_summary.get(topic_name, 0) + 1
            
            # Also extract topics from compound commands
            for handler_info in self.compound_commands.values():
                if isinstance(handler_info, dict):
                    topic = handler_info.get("topic", "unknown")
                else:
                    # Fallback for old string format
                    topic = handler_info
                    
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