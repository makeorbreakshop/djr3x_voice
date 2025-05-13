"""
CLI Service for CantinaOS

This service provides a command-line interface for system control.
It uses the CommandDispatcherService for routing commands to appropriate handlers.
"""

import asyncio
import logging
import sys
import os
from typing import Dict, Optional, Any, List, Callable
from datetime import datetime
import uuid
import signal
import time  # Added import for time module

from pyee.asyncio import AsyncIOEventEmitter

from ..base_service import BaseService
from ..event_topics import EventTopics
from ..event_payloads import (
    CliCommandPayload,
    CliResponsePayload,
    ServiceStatus,
    LogLevel,
    SystemModeChangePayload
)

class CLIService(BaseService):
    """
    Service that provides command-line interface functionality.
    
    Features:
    - Command input handling using pure asyncio (no threads)
    - Command shortcuts
    - Command history
    - Response display
    """
    
    # Command shortcuts
    SHORTCUTS = {
        'e': 'engage',
        'a': 'ambient',
        'd': 'disengage',
        'h': 'help',
        'st': 'status',
        'r': 'reset',
        'q': 'quit',
        'l': 'list music',
        'p': 'play music',
        's': 'stop music',
        'rec': 'record'
    }
    
    # Mode command mappings
    MODE_COMMANDS = {
        'engage': 'INTERACTIVE',
        'ambient': 'AMBIENT',
        'disengage': 'IDLE'
    }
    
    def __init__(
        self,
        event_bus: AsyncIOEventEmitter,
        config: Optional[Dict[str, Any]] = None,
        logger: Optional[logging.Logger] = None,
        io_functions: Optional[Dict[str, Callable]] = None
    ):
        """Initialize the CLI service.
        
        Args:
            event_bus: Event bus instance
            config: Optional configuration dictionary
            logger: Optional logger instance
            io_functions: Optional dict with 'output' and 'error' functions for I/O
        """
        super().__init__("cli", event_bus, logger)
        
        # Configuration
        self._config = config or {}
        self._max_history = int(self._config.get('CLI_MAX_HISTORY', 100))
        
        # Command history
        self._command_history: List[str] = []
        
        # Input loop control
        self._running = False
        self._input_task: Optional[asyncio.Task] = None
        
        # I/O functions (only output and error, input is handled by asyncio)
        self._io = io_functions or {
            'output': lambda x: sys.stdout.write(str(x) + '\n'),
            'error': lambda x: sys.stderr.write(str(x) + '\n')
        }
        
        # Stdin/stdout reader/writer
        self._stdin_reader: Optional[asyncio.StreamReader] = None
        self._stdout_writer: Optional[asyncio.StreamWriter] = None
        
        # Recording mode state
        self._is_recording = False
        self._recording_text = ""
        self._mic_recording_active = False
        
    @property
    def event_bus(self):
        """Get the event bus with public accessor."""
        return self._event_bus
        
    async def _start(self) -> None:
        """Initialize the service."""
        self.logger.info("Starting CLI service")
        
        # Subscribe to response events
        await self.subscribe(EventTopics.CLI_RESPONSE, self._handle_response)
        
        # Subscribe to voice events to track recording state
        await self.subscribe(EventTopics.VOICE_LISTENING_STARTED, self._handle_voice_listening_started)
        await self.subscribe(EventTopics.VOICE_LISTENING_STOPPED, self._handle_voice_listening_stopped)
        
        # Start input loop
        self._running = True
        
        # Set up stdin reader
        await self._setup_stdin_reader()
        
        # Display startup message
        self._io['output']("\nDJ R3X Voice Control CLI")
        self._io['output']("Type 'help' for available commands\n")
        
        # Explicitly display initial prompt - ensure it's on a new line and properly flushed
        print("DJ-R3X> ", end="", flush=True)
        
        # Start the async input processing task
        self._input_task = asyncio.create_task(self._process_input())
        
        # Set service status to RUNNING
        self._status = ServiceStatus.RUNNING
        await self._emit_status(ServiceStatus.RUNNING, "CLI service ready")
        
    async def _setup_stdin_reader(self) -> None:
        """Set up the stdin reader using asyncio streams."""
        loop = asyncio.get_running_loop()
        
        # Create stdin reader
        if sys.platform == 'win32':
            # Windows-specific handling
            self.logger.info("Setting up Windows console input")
            
            # Use different approach for Windows
            # We can't use asyncio.StreamReader directly with stdin on Windows
            # Instead we'll use run_in_executor, but in a more efficient way
            self._stdin_reader = None
        else:
            # Unix-like systems can use asyncio streams
            self.logger.info("Setting up Unix stdin reader")
            reader = asyncio.StreamReader()
            protocol = asyncio.StreamReaderProtocol(reader)
            
            # Get file descriptor for stdin
            fd = sys.stdin.fileno()
            
            # Set stdin to non-blocking mode
            os.set_blocking(fd, False)
            
            # Create connection to stdin
            await loop.connect_read_pipe(lambda: protocol, sys.stdin)
            self._stdin_reader = reader
            
    async def _stop(self) -> None:
        """Clean up resources."""
        self.logger.info("Stopping CLI service")
        
        # Stop input task
        self._running = False
        
        # Cancel the input processing task
        if self._input_task and not self._input_task.done():
            self._input_task.cancel()
            try:
                await self._input_task
            except asyncio.CancelledError:
                self.logger.info("Input processing task cancelled")
                
    async def _process_input(self) -> None:
        """Process input from stdin asynchronously."""
        self.logger.debug("Starting async input processing")
        
        try:
            loop = asyncio.get_running_loop()
            
            # Different handling for Windows vs. Unix-like systems
            if sys.platform == 'win32':
                # Windows approach using run_in_executor for each line
                while self._running:
                    # Get input asynchronously - The prompt is already displayed from _start or _handle_response
                    try:
                        user_input = await loop.run_in_executor(None, sys.stdin.readline)
                        user_input = user_input.strip()  # Remove trailing newline
                        
                        self.logger.debug(f"Received input: '{user_input}', is_recording: {self._is_recording}, mic_active: {self._mic_recording_active}")
                        
                        if self._is_recording:
                            # Text-based recording mode - no longer used
                            await self._handle_recording_input(user_input)
                        elif user_input:
                            # Process the command directly in the event loop
                            await self._process_command(user_input)
                            
                        # Check for quit
                        if user_input and user_input.strip().lower() in ['quit', 'exit', 'q'] and not self._is_recording:
                            break
                        else:
                            # Display prompt for next command - ensure it's properly flushed
                            if not self._is_recording:
                                print("DJ-R3X> ", end="", flush=True)
                            
                    except (EOFError, KeyboardInterrupt):
                        self.logger.info("Input processing received quit signal")
                        await self._process_command("quit")
                        break
                        
                    except Exception as e:
                        self.logger.error(f"Error processing input: {e}")
                        if not self._running:
                            break
            else:
                # Unix approach using asyncio.StreamReader
                while self._running:
                    # Read a line asynchronously - The prompt is already displayed from _start or _handle_response
                    try:
                        line = await self._stdin_reader.readline()
                        user_input = line.decode().strip()
                        
                        # Important: Log what we received and current recording state
                        self.logger.debug(f"Received input: '{user_input}', is_recording: {self._is_recording}, mic_active: {self._mic_recording_active}")
                        
                        if self._is_recording:
                            # Text-based recording mode - no longer used
                            await self._handle_recording_input(user_input)
                        elif user_input:
                            # Process the command directly
                            await self._process_command(user_input)
                            
                        # Check for quit
                        if user_input and user_input.strip().lower() in ['quit', 'exit', 'q'] and not self._is_recording:
                            break
                        else:
                            # Display prompt for next command - ensure it's properly flushed
                            if not self._is_recording:
                                print("DJ-R3X> ", end="", flush=True)
                            
                    except (EOFError, KeyboardInterrupt):
                        self.logger.info("Input processing received quit signal")
                        await self._process_command("quit")
                        break
                        
                    except Exception as e:
                        self.logger.error(f"Error processing input: {e}")
                        if not self._running:
                            break
                
        except asyncio.CancelledError:
            self.logger.debug("Input processing task cancelled")
            raise
        finally:
            self.logger.debug("Input processing stopped")
    
    async def _handle_recording_input(self, user_input: str) -> None:
        """Handle input in recording mode.
        
        Args:
            user_input: User input string
        """
        # This method is no longer used - we're using microphone recording now
        self.logger.warning("Text-based recording input handler called but not in use")
        pass

    async def _process_command(self, user_input: str) -> None:
        """Process a command from user input.
        
        Args:
            user_input: Raw user input string
        """
        try:
            # Add to history
            self._add_to_history(user_input)
            
            # Parse command and args
            parts = user_input.strip().split()
            if not parts:
                return
                
            command = parts[0].lower()
            args = parts[1:]
            
            # Handle shortcuts
            if command in self.SHORTCUTS:
                command = self.SHORTCUTS[command]
                
            # Handle quit command
            if command in ['quit', 'exit']:
                self._event_bus.emit(EventTopics.SYSTEM_SHUTDOWN, {})
                return
            
            # Handle 'done' command to stop recording
            if command == 'done':
                if self._mic_recording_active:
                    self.logger.info("Stopping microphone recording with 'done' command")
                    # Emit event to stop microphone recording
                    await self.emit(EventTopics.VOICE_LISTENING_STOPPED, {})
                    self._mic_recording_active = False
                    return
                else:
                    self.logger.info("'done' command received but no recording is active")
                    self._io['output']("No recording is currently active.")
                    return
                
            # Handle record command
            if command == 'record':
                if self._mic_recording_active:
                    self.logger.info("Recording already active")
                    self._io['output']("Recording is already active. Type 'done' when finished.")
                    return
                
                self.logger.info("Activating microphone recording")
                self._mic_recording_active = True
                
                # Start microphone recording without entering text input mode
                # Emit voice listening started event to trigger microphone capture
                self.logger.debug("Emitting VOICE_LISTENING_STARTED event")
                await self.emit(EventTopics.VOICE_LISTENING_STARTED, {})
                self._io['output']("[Microphone recording active - type 'done' when finished speaking]")
                return
                
            # Handle mode commands
            if command in self.MODE_COMMANDS:
                self.logger.debug(f"Processing mode command: {command}")
                self._event_bus.emit(
                    EventTopics.SYSTEM_SET_MODE_REQUEST,
                    {"mode": self.MODE_COMMANDS[command]}
                )
                return
                
            # Handle other commands
            event_topic = EventTopics.CLI_COMMAND
            if command in ['status', 'help', 'reset']:
                event_topic = EventTopics.MODE_COMMAND
            elif command.startswith('play') or command.startswith('list') or command == 'stop music':
                event_topic = EventTopics.MUSIC_COMMAND
                
            self.logger.debug(f"Emitting command on topic {event_topic}: {command} {args}")
            
            # Combine command and args into single command string for payload
            full_command = command
            if args:
                full_command += " " + " ".join(args)
                
            # Create command payload
            payload = CliCommandPayload(
                command=command,
                args=args,
                raw_command=full_command,
                timestamp=time.time(),  # Fixed: Using time.time() instead of datetime.now().timestamp()
                command_id=str(uuid.uuid4())
            )
            
            # Emit command event
            self._event_bus.emit(event_topic, payload.model_dump())
            
        except Exception as e:
            self.logger.error(f"Error processing command '{user_input}': {e}")
            error_msg = f"Error: {str(e)}"
            self._io['error'](error_msg)
            
            # Emit error response
            payload = CliResponsePayload(
                message=error_msg,
                success=False,
                timestamp=time.time(),  # Fixed: Using time.time() instead of datetime.now().timestamp()
                severity=LogLevel.ERROR
            )
            await self.emit_error_response(EventTopics.CLI_RESPONSE, payload)
            
    async def _handle_response(self, payload: Dict[str, Any]) -> None:
        """Handle a response event and display it to the user.
        
        Args:
            payload: Response payload, should contain 'message' and optionally 'success'
        """
        message = payload.get('message', '')
        success = payload.get('success', True)
        severity = payload.get('severity', LogLevel.INFO)
        
        # Format based on success/severity
        if not success or severity in [LogLevel.ERROR, LogLevel.CRITICAL]:
            self._io['error'](message)
        else:
            self._io['output'](message)
            
        # Display prompt again after response - make sure it's properly flushed
        if not self._is_recording and not self._mic_recording_active:
            print("DJ-R3X> ", end="", flush=True)
            
    def _add_to_history(self, command: str) -> None:
        """Add a command to the history.
        
        Args:
            command: The command to add
        """
        if command.strip():
            self._command_history.append(command)
            # Trim if exceeding max history
            if len(self._command_history) > self._max_history:
                self._command_history.pop(0)
                
    async def _emit_status(
        self,
        status: ServiceStatus,
        message: str,
        severity: Optional[LogLevel] = None
    ) -> None:
        """Emit a service status update event.
        
        Args:
            status: Service status enum value
            message: Status message
            severity: Optional severity level
        """
        severity = severity or LogLevel.INFO
        
        payload = {
            "service_name": self.service_name,
            "status": status,
            "message": message,
            "timestamp": time.time(),  # Fixed: Using time.time() instead of datetime.now().timestamp()
            "severity": severity
        }
        
        await self.emit(EventTopics.SERVICE_STATUS_UPDATE, payload)

    async def _handle_voice_listening_started(self, payload: Dict[str, Any]) -> None:
        """
        Handle voice listening started event.
        
        Args:
            payload: Event payload (not used)
        """
        self.logger.debug("Received VOICE_LISTENING_STARTED event")
        self._mic_recording_active = True
        
    async def _handle_voice_listening_stopped(self, payload: Dict[str, Any]) -> None:
        """
        Handle voice listening stopped event.
        
        Args:
            payload: Event payload (not used)
        """
        self.logger.debug("Received VOICE_LISTENING_STOPPED event")
        self._mic_recording_active = False