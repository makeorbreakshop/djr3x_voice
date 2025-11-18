"""
Minimal CLI Formatter for DJ R3X Voice
Following best practices for subtle, accessible terminal output
"""

import re
import sys
import os
import logging
from datetime import datetime
from typing import Optional, Dict, Any
from enum import Enum

try:
    from colorama import init, Fore, Back, Style
    # Initialize colorama for cross-platform color support
    init(autoreset=True)
    COLORS_AVAILABLE = True
except ImportError:
    COLORS_AVAILABLE = False
    # Create dummy classes if colorama isn't available
    class Fore:
        BLACK = RED = GREEN = YELLOW = BLUE = MAGENTA = CYAN = WHITE = ''
        LIGHTBLACK_EX = LIGHTRED_EX = LIGHTGREEN_EX = LIGHTYELLOW_EX = ''
        LIGHTBLUE_EX = LIGHTMAGENTA_EX = LIGHTCYAN_EX = LIGHTWHITE_EX = ''
        RESET = ''

    class Back:
        BLACK = RED = GREEN = YELLOW = BLUE = MAGENTA = CYAN = WHITE = ''
        RESET = ''

    class Style:
        DIM = NORMAL = BRIGHT = RESET_ALL = ''


class MinimalCLIFormatter:
    """
    Minimal CLI formatter following best practices:
    - Respects NO_COLOR environment variable
    - Uses color sparingly and functionally
    - Maintains readability without colors
    - Focuses on clarity over decoration
    """

    # Minimal functional color scheme
    COLORS = {
        'error': Fore.RED,
        'warning': Fore.YELLOW,
        'success': Fore.GREEN,
        'info': '',  # Default terminal color
        'dim': Style.DIM,
        'bright': Style.BRIGHT,
        'reset': Style.RESET_ALL
    }

    # Log level colors - minimal and functional
    LOG_LEVEL_COLORS = {
        "DEBUG": Style.DIM,
        "INFO": '',  # Default color
        "WARNING": Fore.YELLOW,
        "ERROR": Fore.RED,
        "CRITICAL": Fore.RED + Style.BRIGHT,
    }

    def __init__(self, enable_colors: bool = None, use_icons: bool = False):
        """
        Initialize the minimal CLI formatter

        Args:
            enable_colors: Whether to use colors (None = auto-detect from environment)
            use_icons: Whether to use unicode icons (default: False for minimal output)
        """
        # Respect NO_COLOR environment variable (CLI best practice)
        if enable_colors is None:
            no_color = os.environ.get('NO_COLOR')
            dumb_term = os.environ.get('TERM') == 'dumb'
            enable_colors = not (no_color or dumb_term)

        self.enable_colors = enable_colors and COLORS_AVAILABLE
        self.use_icons = use_icons

    def format_timestamp(self, timestamp: Optional[datetime] = None) -> str:
        """Format timestamp - dim to de-emphasize"""
        if timestamp is None:
            timestamp = datetime.now()

        time_str = timestamp.strftime("%H:%M:%S")

        if self.enable_colors:
            return f"{Style.DIM}{time_str}{Style.RESET_ALL}"
        return time_str

    def format_service_name(self, service_name: str) -> str:
        """Format service name - dim and shortened"""
        if not service_name:
            return ""

        # Shorten common prefixes
        display_name = service_name.replace("cantina_os.services.", "")
        display_name = display_name.replace("cantina_os.", "")

        # Common abbreviations for cleaner output
        abbreviations = {
            "deepgram_direct_mic_service": "mic",
            "elevenlabs_service": "tts",
            "gpt_service": "gpt",
            "claude_service": "claude",
            "music_controller_service": "music",
            "eye_light_controller_service": "led",
            "brain_service": "brain",
            "timeline_executor_service": "timeline",
            "nervous_system": "memory",
            "cached_speech_service": "cache",
            "cli_service": "cli",
            "yoda_mode_manager_service": "mode",
            "intent_router_service": "intent",
            "command_dispatcher_service": "cmd",
            "mouse_input_service": "mouse",
            "vision_service": "vision",
            "latency_tracker_service": "latency",
            "debug_service": "debug",
        }

        for full, short in abbreviations.items():
            if full in display_name.lower():
                display_name = short
                break

        # Ensure consistent width (8 chars)
        display_name = display_name[:8].ljust(8)

        if self.enable_colors:
            return f"{Style.DIM}{display_name}{Style.RESET_ALL}"
        return f"[{display_name}]"

    def format_log_level(self, level: str) -> str:
        """Format log level with minimal color"""
        level_upper = level.upper()
        display = level_upper[:4].rjust(5)  # Right-align for cleaner look

        if self.enable_colors and level_upper in self.LOG_LEVEL_COLORS:
            color = self.LOG_LEVEL_COLORS[level_upper]
            if color:
                return f"{color}{display}{Style.RESET_ALL}"
        return display

    def format_message(self, message: str, level: str = "INFO") -> str:
        """Format message with minimal enhancement"""
        if not message:
            return ""

        # For errors and warnings, the level color is enough
        # No additional coloring needed in the message itself
        return message

    def format_log_line(
        self,
        timestamp: datetime,
        service_name: str,
        level: str,
        message: str
    ) -> str:
        """
        Format a complete log line with minimal styling

        Format: HH:MM:SS [service] LEVEL message

        Args:
            timestamp: Log timestamp
            service_name: Name of the service
            level: Log level
            message: Log message

        Returns:
            Minimally formatted log line
        """
        parts = [
            self.format_timestamp(timestamp),
            self.format_service_name(service_name),
            self.format_log_level(level),
            self.format_message(message, level)
        ]

        return " ".join(parts)

    def format_cli_response(
        self,
        message: str,
        is_error: bool = False,
        command_context: Optional[str] = None
    ) -> str:
        """
        Format a CLI response with minimal styling

        Args:
            message: Response message
            is_error: Whether this is an error response
            command_context: Optional command that triggered this response

        Returns:
            Minimally formatted response
        """
        # Only use icons for errors and warnings if enabled
        prefix = ""
        if self.use_icons and is_error:
            prefix = "✗ "
        elif is_error:
            prefix = "Error: "

        # Apply color only to error prefix
        if is_error and self.enable_colors:
            prefix = f"{Fore.RED}{prefix}{Style.RESET_ALL}"

        # Add command context if provided (dimmed)
        context = ""
        if command_context:
            if self.enable_colors:
                context = f"{Style.DIM}[{command_context}]{Style.RESET_ALL} "
            else:
                context = f"[{command_context}] "

        return f"{prefix}{context}{message}"

    def format_prompt(self, prompt_text: str = "dj-r3x") -> str:
        """Format the CLI prompt - simple and clean"""
        prompt_text = prompt_text.lower()  # Lowercase for less aggressive look
        if self.enable_colors:
            # Subtle green, not bright
            return f"{Fore.GREEN}{prompt_text}>{Style.RESET_ALL} "
        return f"{prompt_text}> "

    def print_separator(self, char: str = "-", width: int = 60) -> str:
        """Print a subtle separator line"""
        line = char * width
        if self.enable_colors:
            return f"{Style.DIM}{line}{Style.RESET_ALL}"
        return line


# Create a global formatter instance
cli_formatter = MinimalCLIFormatter()


def setup_minimal_logging_formatter():
    """
    Set up a minimal logging formatter

    This should be called early in the application startup
    """

    class MinimalColoredFormatter(logging.Formatter):
        """Custom formatter using minimal CLI formatter"""

        def __init__(self):
            super().__init__()
            self.cli_formatter = MinimalCLIFormatter()

        def format(self, record):
            # Extract components from the log record
            timestamp = datetime.fromtimestamp(record.created)
            service_name = record.name
            level = record.levelname
            message = record.getMessage()

            # Use our minimal formatter
            return self.cli_formatter.format_log_line(
                timestamp=timestamp,
                service_name=service_name,
                level=level,
                message=message
            )

    return MinimalColoredFormatter()


def demo():
    """Demo function to show the minimal formatting"""
    formatter = MinimalCLIFormatter()

    print("\n" + formatter.print_separator())
    print("DJ R3X Voice - Minimal CLI Output Demo")
    print(formatter.print_separator() + "\n")

    # Demo various message types
    examples = [
        ("cantina_os.main", "INFO", "System startup complete"),
        ("deepgram_direct_mic_service", "DEBUG", "Microphone initialized with sample rate 16000"),
        ("gpt_service", "INFO", 'Processing transcription: "Play some jazz music"'),
        ("music_controller_service", "INFO", "Starting track: Take Five by Dave Brubeck"),
        ("elevenlabs_service", "INFO", "Speech synthesis started, duration: 3.5 seconds"),
        ("brain_service", "INFO", "DJ mode activated, selecting next track"),
        ("cli_service", "WARNING", "Command not recognized: 'plya music'"),
        ("music_controller_service", "ERROR", "Failed to load track: file not found"),
    ]

    print("Sample Log Output:\n")
    for service, level, message in examples:
        formatted = formatter.format_log_line(
            timestamp=datetime.now(),
            service_name=service,
            level=level,
            message=message
        )
        print(formatted)

    print("\n" + formatter.print_separator() + "\n")

    # Demo CLI responses
    print("Sample CLI Responses:\n")

    responses = [
        ("Playing track 3: 'Fly Me to the Moon'", False, "play music 3"),
        ("Track not found in playlist", True, "play music 99"),
        ("DJ mode activated", False, "dj start"),
        ("Volume set to 75%", False, "volume 75"),
    ]

    for message, is_error, context in responses:
        formatted = formatter.format_cli_response(message, is_error, context)
        print(formatted)

    print("\n" + formatter.print_separator() + "\n")

    # Demo prompt
    print("Sample Prompt:")
    print(formatter.format_prompt(), end="")
    print("(user input here)\n")

    # Demo NO_COLOR mode
    print(formatter.print_separator())
    print("\nWith NO_COLOR environment variable set:")
    no_color_formatter = MinimalCLIFormatter(enable_colors=False)
    formatted = no_color_formatter.format_log_line(
        timestamp=datetime.now(),
        service_name="music_controller_service",
        level="INFO",
        message="Colors disabled for accessibility"
    )
    print(formatted)
    print(no_color_formatter.format_prompt(), end="")
    print("(no colors)")


if __name__ == "__main__":
    # Check if NO_COLOR is set
    if os.environ.get('NO_COLOR'):
        print("NO_COLOR environment variable detected - colors disabled")
    demo()