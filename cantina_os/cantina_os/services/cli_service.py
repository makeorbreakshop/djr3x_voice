"""
CLI Service for CantinaOS

This service provides a command-line interface for system control.
It uses the CommandDispatcherService for routing commands to appropriate handlers.
"""

"""
SERVICE: CLIService
PURPOSE: Command-line interface for system control with async input processing and command history
EVENTS_IN: CLI_RESPONSE, VOICE_LISTENING_STARTED, VOICE_LISTENING_STOPPED
EVENTS_OUT: CLI_COMMAND, VOICE_LISTENING_STARTED, VOICE_LISTENING_STOPPED, SERVICE_STATUS_UPDATE, SYSTEM_SHUTDOWN
KEY_METHODS: _process_command, _handle_response, _process_input, _handle_recording_input
DEPENDENCIES: stdin/stdout for command line interaction, keyboard input
"""

import asyncio
import logging
import os
import sys
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from pyee.asyncio import AsyncIOEventEmitter

from ..base_service import BaseService
from ..core.console_logging import write_with_retry
from ..core.event_topics import EventTopics
from ..event_payloads import (
    CliCommandPayload,
    CliResponsePayload,
    LogLevel,
    ServiceStatus,
)

# Import the CLI formatter for enhanced output (using minimal version)
try:
    from ..utils.cli_formatter_minimal import cli_formatter
    FORMATTER_AVAILABLE = True
except ImportError:
    FORMATTER_AVAILABLE = False

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
        
        # I/O functions - use async-friendly versions that won't block
        self._io = io_functions or {
            'output': self._async_write_output,
            'error': self._async_write_error
        }
        
        # Stdin/stdout reader/writer
        self._stdin_reader: Optional[asyncio.StreamReader] = None
        self._stdin_transport = None
        self._stdin_pipe = None
        self._stdout_writer: Optional[asyncio.StreamWriter] = None
        self._output_stream = None
        self._error_stream = None
        
        # Output queue to prevent blocking
        self._output_queue = asyncio.Queue()
        self._output_task = None
        
        # Recording mode state
        self._is_recording = False
        self._recording_text = ""
        self._mic_recording_active = False

        # Voice interaction display state
        self._last_interim_text = ""
        # The conversation_id of the in-flight voice turn. Set by `record` (and by incoming
        # transcription payloads), read by `done` so VOICE_LISTENING_STOPPED carries it, and by
        # _handle_llm_response, which drops any reply whose id does not match this one.
        self._current_conversation_id: Optional[str] = None
        
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

        # Subscribe to transcription events for voice feedback
        await self.subscribe(EventTopics.TRANSCRIPTION_INTERIM, self._handle_transcription_interim)
        await self.subscribe(EventTopics.TRANSCRIPTION_FINAL, self._handle_transcription_final)

        # Subscribe to LLM response events for assistant feedback
        await self.subscribe(EventTopics.LLM_RESPONSE, self._handle_llm_response)
        
        # Start input loop
        self._running = True
        
        # Set up stdin reader
        await self._setup_stdin_reader()
        
        # Start output processor task
        self._output_task = asyncio.create_task(self._output_processor())
        
        # The output processor is the sole owner of terminal writes. Keeping the banner and
        # prompt in one queued item prevents the prompt from racing ahead of the banner.
        if FORMATTER_AVAILABLE:
            startup_message = "\n".join(
                [
                    cli_formatter.print_separator(),
                    "DJ R3X Voice Control",
                    "Type 'help' for available commands",
                    cli_formatter.print_separator(),
                ]
            )
        else:
            startup_message = "DJ R3X Voice Control\nType 'help' for available commands"
        await self._async_write_output(startup_message, show_prompt=True)
        
        # Start the async input processing task
        self._input_task = asyncio.create_task(self._process_input())
        
        # Set service status to RUNNING
        self._status = ServiceStatus.RUNNING
        await self._emit_status(ServiceStatus.RUNNING, "CLI service ready")
        
    async def _setup_stdin_reader(self) -> None:
        """Set up the stdin reader using asyncio streams."""
        loop = asyncio.get_running_loop()

        if sys.platform == 'win32':
            # Windows-specific handling
            self.logger.info("Setting up Windows console input")
            
            # Use different approach for Windows
            # We can't use asyncio.StreamReader directly with stdin on Windows
            # Instead we'll use run_in_executor, but in a more efficient way
            self._stdin_reader = None
        else:
            # asyncio makes a connected read pipe non-blocking. Terminal stdin/stdout can be
            # duplicate descriptors for the same open file description, so changing the
            # original stdin flags can also make stdout non-blocking. Reopening the concrete
            # terminal device gives the reader its own file description while preserving
            # selector-driven shutdown. macOS kqueue cannot watch the /dev/tty alias itself,
            # so use the concrete path returned by ttyname.
            reader = asyncio.StreamReader()
            protocol = asyncio.StreamReaderProtocol(reader)

            read_pipe = sys.stdin
            if sys.stdin.isatty():
                self.logger.info("Setting up isolated Unix terminal reader")
                terminal_path = os.ttyname(sys.stdin.fileno())
                self._stdin_pipe = open(terminal_path, "rb", buffering=0)
                read_pipe = self._stdin_pipe
            else:
                self.logger.info("Setting up Unix stdin reader")

            self._stdin_transport, _ = await loop.connect_read_pipe(
                lambda: protocol, read_pipe
            )
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
                
        # Cancel the output processing task
        if self._output_task and not self._output_task.done():
            self._output_task.cancel()
            try:
                await self._output_task
            except asyncio.CancelledError:
                self.logger.info("Output processing task cancelled")

        if self._stdin_transport is not None:
            self._stdin_transport.close()
            self._stdin_transport = None
        if self._stdin_pipe is not None:
            self._stdin_pipe.close()
            self._stdin_pipe = None
        
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
                            
                        # Check for quit. Non-empty commands get their next prompt with the
                        # response so response and prompt stay atomically ordered.
                        if user_input and user_input.strip().lower() in ['quit', 'exit', 'q'] and not self._is_recording:
                            break
                        elif not user_input and not self._is_recording:
                            await self._queue_prompt()
                            
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
                        if not line:
                            await self._process_command("quit")
                            break
                        user_input = line.decode().strip()
                        
                        # Important: Log what we received and current recording state
                        self.logger.debug(f"Received input: '{user_input}', is_recording: {self._is_recording}, mic_active: {self._mic_recording_active}")
                        
                        if self._is_recording:
                            # Text-based recording mode - no longer used
                            await self._handle_recording_input(user_input)
                        elif user_input:
                            # Process the command directly
                            await self._process_command(user_input)
                            
                        # Check for quit. Non-empty commands get their next prompt with the
                        # response so response and prompt stay atomically ordered.
                        if user_input and user_input.strip().lower() in ['quit', 'exit', 'q'] and not self._is_recording:
                            break
                        elif not user_input and not self._is_recording:
                            await self._queue_prompt()
                            
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
            
        This method handles initial command processing and emits all commands 
        to the CLI_COMMAND topic for the CommandDispatcherService to handle.
        It no longer routes commands directly to service-specific topics.
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
                await self.emit(
                    EventTopics.SYSTEM_SHUTDOWN_REQUESTED,
                    {"reason": "CLI quit command", "restart": False},
                )
                return
            
            # Handle 'done' command to stop recording
            if command == 'done':
                if self._mic_recording_active:
                    self.logger.info("Stopping microphone recording with 'done' command")
                    # Emit event to stop microphone recording
                    # FIXED 2026-09-17: carry the conversation_id minted by `record` below.
                    # Emitting `{}` here broke the turn's identity for every downstream
                    # consumer, exactly as in deepgram_direct_mic_service.
                    await self.emit(
                        EventTopics.VOICE_LISTENING_STOPPED,
                        {"conversation_id": self._current_conversation_id},
                    )
                    self._mic_recording_active = False
                    self._current_conversation_id = None
                    return
                else:
                    self.logger.info("'done' command received but no recording is active")
                    await self._async_write_output(
                        "No recording is currently active.", show_prompt=True
                    )
                    return
                
            # Handle record command
            if command == 'record':
                if self._mic_recording_active:
                    self.logger.info("Recording already active")
                    await self._async_write_output(
                        "Recording is already active. Type 'done' when finished.",
                        show_prompt=True,
                    )
                    return
                
                self.logger.info("Activating microphone recording")
                self._mic_recording_active = True

                # Generate a conversation ID for this voice interaction
                conversation_id = str(uuid.uuid4())
                self._current_conversation_id = conversation_id
                self.logger.info(f"Starting voice conversation with ID: {conversation_id}")

                # Start microphone recording without entering text input mode
                # Emit voice listening started event to trigger microphone capture
                self.logger.debug(f"Emitting VOICE_LISTENING_STARTED event with conversation_id: {conversation_id}")
                await self.emit(EventTopics.VOICE_LISTENING_STARTED, {
                    "conversation_id": conversation_id,
                    "timestamp": time.time()
                })
                await self._async_write_output(
                    "[Microphone recording active - type 'done' when finished speaking]",
                    show_prompt=True,
                )
                return
                
            # Handle all other commands by emitting to CLI_COMMAND
            # Create command payload with original command, args, and full raw_input
            payload = CliCommandPayload(
                command=command,
                args=args,
                raw_input=user_input,
                timestamp=time.time(),
                command_id=str(uuid.uuid4())
            )
            
            # Log the command being emitted
            self.logger.info(f"CLIService emitting to CLI_COMMAND: {payload.model_dump(exclude_none=True)}")
            
            # Emit to CLI_COMMAND topic - all commands go through this single path
            await self.emit(EventTopics.CLI_COMMAND, payload.model_dump())
            
        except Exception as e:
            self.logger.error(f"Error processing command '{user_input}': {e}")
            error_msg = f"Error: {str(e)}"
            # Emit one error response. Writing here as well made exception messages appear
            # twice when the CLI consumed its own CLI_RESPONSE event.
            payload = CliResponsePayload(
                message=error_msg,
                is_error=True,
            )
            await self.emit_error_response(EventTopics.CLI_RESPONSE, payload)
            
    async def _handle_response(self, payload: Dict[str, Any]) -> None:
        """Handle response events from other services."""
        is_error = payload.get("is_error", False)
        message = payload.get("message", "")
        command_context = payload.get("command", "N/A") # Get command context if available

        self.logger.info(f"CLI received response. Error: {is_error}, Command context: '{command_context}', Message: '{message[:100]}...'") # Added INFO log

        # Use enhanced formatter if available
        if FORMATTER_AVAILABLE:
            formatted_message = cli_formatter.format_cli_response(
                message, is_error, command_context if command_context != "N/A" else None
            )
            await self._queue_output(
                formatted_message, is_error=is_error, show_prompt=True
            )
        else:
            if is_error:
                await self._async_write_error(message, show_prompt=True)
            else:
                await self._async_write_output(message, show_prompt=True)
            
        # Set service status to RUNNING
        self._status = ServiceStatus.RUNNING
        
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

    async def _handle_transcription_interim(self, payload: Dict[str, Any]) -> None:
        """
        Handle interim transcription events - show partial recognition.

        Args:
            payload: Event payload with 'text' field
        """
        text = payload.get("text", "")
        conversation_id = payload.get("conversation_id")

        # Only show if we're in an active voice conversation
        if not self._mic_recording_active:
            return

        # Track conversation ID
        if conversation_id:
            self._current_conversation_id = conversation_id

        # Format interim transcription with visual indicator
        if text and text != self._last_interim_text:
            self._last_interim_text = text
            # Clear previous line and show interim (use \r to overwrite)
            interim_display = f"\r🎤 You: {text}..."
            await self._queue_output(interim_display, add_newline=False)

    async def _handle_transcription_final(self, payload: Dict[str, Any]) -> None:
        """
        Handle final transcription events - show confirmed recognition.

        Args:
            payload: Event payload with 'text' field
        """
        text = payload.get("text", "")
        conversation_id = payload.get("conversation_id")

        # Only show if we're in an active voice conversation
        if not self._mic_recording_active and conversation_id != self._current_conversation_id:
            return

        if text:
            # Clear interim line and show final with checkmark
            final_display = f"\r🎤 You: \"{text}\""
            await self._async_write_output(final_display)
            self._last_interim_text = ""

    async def _handle_llm_response(self, payload: Dict[str, Any]) -> None:
        """
        Handle LLM response events - show assistant's response.

        Args:
            payload: Event payload with 'text' field (from LLMResponsePayload)
        """
        response_text = payload.get("text", "")
        conversation_id = payload.get("conversation_id")

        # Only show if it matches our current conversation
        if conversation_id != self._current_conversation_id:
            return

        if response_text:
            # Format as assistant response with robot emoji
            assistant_display = f"🤖 R3X: \"{response_text}\""
            await self._async_write_output(assistant_display)
            await self._async_write_output("", show_prompt=True)  # Blank line and prompt

    async def _queue_output(
        self,
        message: str,
        *,
        is_error: bool = False,
        show_prompt: bool = False,
        add_newline: bool = True,
    ) -> None:
        # Capture the destination with the message. Test capture layers and embedding hosts
        # may replace sys.stdout between enqueue and the executor thread actually writing.
        if is_error:
            stream = self._error_stream or sys.stderr
        else:
            stream = self._output_stream or sys.stdout
        await self._output_queue.put(
            (message, stream, show_prompt, add_newline)
        )

    async def _queue_prompt(self) -> None:
        await self._queue_output("", show_prompt=True, add_newline=False)

    async def _async_write_output(
        self, message: str, *, show_prompt: bool = False
    ) -> None:
        """Non-blocking output function that respects asyncio principles.
        
        Args:
            message: The message to output
        """
        await self._queue_output(message, show_prompt=show_prompt)
        
    async def _async_write_error(
        self, message: str, *, show_prompt: bool = False
    ) -> None:
        """Non-blocking error output function that respects asyncio principles.
        
        Args:
            message: The error message to output
        """
        await self._queue_output(message, is_error=True, show_prompt=show_prompt)
        
    async def _output_processor(self) -> None:
        """Process output messages from the queue to avoid blocking."""
        try:
            while self._running:
                message, stream, show_prompt, add_newline = await self._output_queue.get()
                try:
                    # Use loop.run_in_executor for potentially blocking operations.
                    #
                    # write_with_retry tracks partial non-blocking writes and retries flushes
                    # without submitting already-accepted text a second time. That prevents a
                    # transient EAGAIN from duplicating an entire help listing.
                    loop = asyncio.get_running_loop()
                    text = str(message)
                    if add_newline:
                        text += '\n'
                    if show_prompt and not self._is_recording and not self._mic_recording_active:
                        text += (
                            cli_formatter.format_prompt()
                            if FORMATTER_AVAILABLE
                            else "DJ-R3X> "
                        )
                    written = await loop.run_in_executor(
                        None, lambda: write_with_retry(stream, text)
                    )
                    if not written:
                        self.logger.error(
                            "Output dropped: stdout stayed blocked for the full retry budget"
                        )
                except Exception as e:
                    self.logger.error(f"Error writing output: {e}")
                finally:
                    self._output_queue.task_done()
        except asyncio.CancelledError:
            self.logger.debug("Output processor task cancelled")
            raise
