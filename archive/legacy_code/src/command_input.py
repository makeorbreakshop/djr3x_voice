"""
Command Input Thread for DJ R3X.
Provides a command-line interface for controlling the system.
"""

import asyncio
import threading
import logging
import time
import sys
import uuid
from typing import Optional, List, Dict, Any

from src.bus import EventBus, EventTypes, EventTopics

# Configure logging
logger = logging.getLogger(__name__)

class CommandInputThread(threading.Thread):
    """Thread to handle command input from the console."""
    
    def __init__(self, event_bus: EventBus, loop: asyncio.AbstractEventLoop):
        """Initialize the command input thread.
        
        Args:
            event_bus: Event bus instance
            loop: Asyncio event loop to use for event emission
        """
        super().__init__(daemon=True, name="CommandInputThread")
        self.event_bus = event_bus
        self.loop = loop
        self._stop_event = threading.Event()
        
        # Map of common shortcuts for commands
        self._command_shortcuts = {
            "e": "engage",
            "a": "ambient",
            "d": "disengage",
            "h": "help",
            "s": "status",
            "q": "quit",
            "exit": "quit",
            "l": "list",
            "p": "play",
            "stop": "stop",
            "r": "reset"
        }
    
    def _show_prompt(self):
        """Show command prompt with a slight delay to avoid log message conflicts."""
        # Short delay to let logs clear
        time.sleep(0.1)
        
        # Add two newlines to separate from logs
        sys.stdout.write("\n\nR3X Command> ")
        sys.stdout.flush()
    
    def run(self) -> None:
        """Run the command input thread."""
        logger.info("Command input ready. Type 'help' for available commands.")
        
        # Initial delay to let system initialization complete
        time.sleep(1)
        
        while not self._stop_event.is_set():
            try:
                # Show prompt 
                self._show_prompt()
                
                # Get input
                user_input = input().strip()
                
                # Skip empty input
                if not user_input:
                    continue
                
                # Process the input
                self._process_input(user_input)
                
            except EOFError:
                # Handle Ctrl+D
                logger.info("EOF received, stopping command input")
                self.stop()
                break
            except KeyboardInterrupt:
                # Handle Ctrl+C
                logger.info("Interrupt received, stopping command input")
                self.stop()
                break
            except Exception as e:
                logger.error(f"Error in command input: {e}")
    
    def _process_input(self, user_input: str) -> None:
        """Process user input and emit command event.
        
        Args:
            user_input: User input string
        """
        # Split input into command and args
        parts = user_input.split()
        command = parts[0].lower()
        args = parts[1:] if len(parts) > 1 else []
        
        # Apply shortcuts if applicable
        if command in self._command_shortcuts:
            command = self._command_shortcuts[command]
        
        # Handle quit command locally
        if command == "quit":
            logger.info("Quit command received, stopping command input")
            self.stop()
            # Emit system shutdown event
            asyncio.run_coroutine_threadsafe(
                self.event_bus.emit(EventTypes.SYSTEM_SHUTDOWN),
                self.loop
            )
            return
        
        # Handle music commands
        if command == "list" and len(args) >= 1 and args[0] == "music":
            # List music command
            asyncio.run_coroutine_threadsafe(
                self.event_bus.emit(
                    EventTopics.MUSIC_COMMAND,
                    {
                        "command": "list",
                        "args": ["music"],
                        "raw_input": user_input,
                        "timestamp": time.time(),
                        "event_id": str(uuid.uuid4()),
                        "conversation_id": None,
                        "schema_version": "1.0"
                    }
                ),
                self.loop
            )
            return
        
        if command == "play" and len(args) >= 1 and args[0] == "music":
            # Play music command
            if len(args) < 2:
                logger.error("Missing track number or name. Usage: play music <number/name>")
                return
                
            # Join remaining args as track name (for multi-word filenames)
            track_name = " ".join(args[1:])
            
            asyncio.run_coroutine_threadsafe(
                self.event_bus.emit(
                    EventTopics.MUSIC_COMMAND,
                    {
                        "command": "play",
                        "args": ["music", track_name],
                        "raw_input": user_input,
                        "timestamp": time.time(),
                        "event_id": str(uuid.uuid4()),
                        "conversation_id": None,
                        "schema_version": "1.0"
                    }
                ),
                self.loop
            )
            return
        
        if command == "stop" and len(args) >= 1 and args[0] == "music":
            # Stop music command
            asyncio.run_coroutine_threadsafe(
                self.event_bus.emit(
                    EventTopics.MUSIC_COMMAND,
                    {
                        "command": "stop",
                        "args": ["music"],
                        "raw_input": user_input,
                        "timestamp": time.time(),
                        "event_id": str(uuid.uuid4()),
                        "conversation_id": None,
                        "schema_version": "1.0"
                    }
                ),
                self.loop
            )
            return
        
        # Emit standard command event for other commands
        asyncio.run_coroutine_threadsafe(
            self.event_bus.emit(
                EventTypes.COMMAND_RECEIVED,
                {"command": command, "args": args}
            ),
            self.loop
        )
    
    def stop(self) -> None:
        """Stop the command input thread."""
        self._stop_event.set() 