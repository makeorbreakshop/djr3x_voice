"""
CLI Formatter for DJ R3X Voice
Enhanced terminal output with color coding and formatting for better visibility
"""

import re
import sys
import logging
from datetime import datetime
from typing import Optional, Dict, Any, Tuple
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


class EventCategory(Enum):
    """Categories of events for color coding"""
    SYSTEM = "system"
    AUDIO = "audio"
    SPEECH = "speech"
    MUSIC = "music"
    VOICE = "voice"
    LED = "led"
    MODE = "mode"
    DEBUG = "debug"
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"
    TRANSCRIPTION = "transcription"
    LLM = "llm"
    INTENT = "intent"
    TIMELINE = "timeline"
    MEMORY = "memory"
    CACHE = "cache"
    CLI = "cli"
    DJ = "dj"


class CLIFormatter:
    """Enhanced CLI formatter with color coding and better structure"""

    # Color scheme for different event categories
    COLOR_SCHEME = {
        EventCategory.SYSTEM: Fore.CYAN,
        EventCategory.AUDIO: Fore.BLUE,
        EventCategory.SPEECH: Fore.MAGENTA,
        EventCategory.MUSIC: Fore.GREEN,
        EventCategory.VOICE: Fore.LIGHTYELLOW_EX,
        EventCategory.LED: Fore.LIGHTMAGENTA_EX,
        EventCategory.MODE: Fore.LIGHTCYAN_EX,
        EventCategory.DEBUG: Fore.LIGHTBLACK_EX,
        EventCategory.ERROR: Fore.RED,
        EventCategory.WARNING: Fore.YELLOW,
        EventCategory.INFO: Fore.WHITE,
        EventCategory.TRANSCRIPTION: Fore.LIGHTBLUE_EX,
        EventCategory.LLM: Fore.LIGHTGREEN_EX,
        EventCategory.INTENT: Fore.YELLOW,
        EventCategory.TIMELINE: Fore.CYAN,
        EventCategory.MEMORY: Fore.BLUE,
        EventCategory.CACHE: Fore.MAGENTA,
        EventCategory.CLI: Fore.WHITE,
        EventCategory.DJ: Fore.LIGHTGREEN_EX + Style.BRIGHT,
    }

    # Log level colors
    LOG_LEVEL_COLORS = {
        "DEBUG": Fore.LIGHTBLACK_EX,
        "INFO": Fore.WHITE,
        "WARNING": Fore.YELLOW,
        "ERROR": Fore.RED,
        "CRITICAL": Fore.RED + Style.BRIGHT,
    }

    # Service name colors (rotating through a palette)
    SERVICE_PALETTE = [
        Fore.LIGHTBLUE_EX,
        Fore.LIGHTGREEN_EX,
        Fore.LIGHTYELLOW_EX,
        Fore.LIGHTMAGENTA_EX,
        Fore.LIGHTCYAN_EX,
        Fore.LIGHTWHITE_EX,
    ]

    def __init__(self, enable_colors: bool = True, enable_icons: bool = True):
        """
        Initialize the CLI formatter

        Args:
            enable_colors: Whether to use color codes
            enable_icons: Whether to use unicode icons/symbols
        """
        self.enable_colors = enable_colors and COLORS_AVAILABLE
        self.enable_icons = enable_icons
        self.service_color_map: Dict[str, str] = {}
        self.next_service_color_index = 0

    def get_service_color(self, service_name: str) -> str:
        """Get a consistent color for a service name"""
        if not self.enable_colors:
            return ""

        if service_name not in self.service_color_map:
            self.service_color_map[service_name] = self.SERVICE_PALETTE[
                self.next_service_color_index % len(self.SERVICE_PALETTE)
            ]
            self.next_service_color_index += 1

        return self.service_color_map[service_name]

    def get_event_category(self, message: str, service_name: Optional[str] = None) -> EventCategory:
        """Determine the event category from message content"""
        message_lower = message.lower()

        # Check for explicit categories in the message
        if "error" in message_lower:
            return EventCategory.ERROR
        elif "warning" in message_lower or "warn" in message_lower:
            return EventCategory.WARNING
        elif "dj" in message_lower or "brain" in service_name.lower() if service_name else False:
            return EventCategory.DJ
        elif "transcription" in message_lower:
            return EventCategory.TRANSCRIPTION
        elif "llm" in message_lower or "gpt" in message_lower or "claude" in message_lower:
            return EventCategory.LLM
        elif "intent" in message_lower:
            return EventCategory.INTENT
        elif "speech" in message_lower or "elevenlabs" in message_lower or "tts" in message_lower:
            return EventCategory.SPEECH
        elif "music" in message_lower or "track" in message_lower or "crossfade" in message_lower:
            return EventCategory.MUSIC
        elif "audio" in message_lower or "ducking" in message_lower:
            return EventCategory.AUDIO
        elif "voice" in message_lower or "listening" in message_lower:
            return EventCategory.VOICE
        elif "led" in message_lower or "eye" in message_lower or "light" in message_lower:
            return EventCategory.LED
        elif "mode" in message_lower:
            return EventCategory.MODE
        elif "timeline" in message_lower:
            return EventCategory.TIMELINE
        elif "memory" in message_lower:
            return EventCategory.MEMORY
        elif "cache" in message_lower:
            return EventCategory.CACHE
        elif "cli" in message_lower:
            return EventCategory.CLI
        elif "system" in message_lower or "shutdown" in message_lower or "startup" in message_lower:
            return EventCategory.SYSTEM
        elif "debug" in message_lower:
            return EventCategory.DEBUG
        else:
            return EventCategory.INFO

    def get_icon(self, category: EventCategory) -> str:
        """Get an icon for the event category"""
        if not self.enable_icons:
            return ""

        icons = {
            EventCategory.SYSTEM: "⚙️ ",
            EventCategory.AUDIO: "🔊",
            EventCategory.SPEECH: "💬",
            EventCategory.MUSIC: "🎵",
            EventCategory.VOICE: "🎤",
            EventCategory.LED: "💡",
            EventCategory.MODE: "🔄",
            EventCategory.DEBUG: "🐛",
            EventCategory.ERROR: "❌",
            EventCategory.WARNING: "⚠️ ",
            EventCategory.INFO: "ℹ️ ",
            EventCategory.TRANSCRIPTION: "📝",
            EventCategory.LLM: "🤖",
            EventCategory.INTENT: "🎯",
            EventCategory.TIMELINE: "⏱️ ",
            EventCategory.MEMORY: "🧠",
            EventCategory.CACHE: "💾",
            EventCategory.CLI: "⌨️ ",
            EventCategory.DJ: "🎧",
        }
        return icons.get(category, "")

    def format_timestamp(self, timestamp: Optional[datetime] = None) -> str:
        """Format a timestamp with color"""
        if timestamp is None:
            timestamp = datetime.now()

        time_str = timestamp.strftime("%H:%M:%S.%f")[:-3]  # Include milliseconds

        if self.enable_colors:
            return f"{Fore.LIGHTBLACK_EX}{time_str}{Style.RESET_ALL}"
        return time_str

    def format_service_name(self, service_name: str) -> str:
        """Format a service name with consistent color"""
        if not service_name:
            return ""

        # Shorten common service names for cleaner output
        short_names = {
            "cantina_os.main": "MAIN",
            "cantina_os.services.": "",  # Remove common prefix
            "deepgram_direct_mic": "MIC",
            "elevenlabs_service": "TTS",
            "gpt_service": "GPT",
            "claude_service": "CLAUDE",
            "music_controller": "MUSIC",
            "eye_light_controller": "LED",
            "brain_service": "BRAIN",
            "timeline_executor": "TIMELINE",
            "nervous_system": "MEMORY",
            "cached_speech": "CACHE",
            "cli_service": "CLI",
            "yoda_mode_manager": "MODE",
            "intent_router": "INTENT",
            "command_dispatcher": "CMD",
        }

        display_name = service_name
        for pattern, replacement in short_names.items():
            display_name = display_name.replace(pattern, replacement)

        # Truncate if still too long
        max_len = 12
        if len(display_name) > max_len:
            display_name = display_name[:max_len-1] + "…"

        # Pad to consistent width
        display_name = display_name.ljust(max_len)

        if self.enable_colors:
            color = self.get_service_color(service_name)
            return f"{color}{display_name}{Style.RESET_ALL}"
        return display_name

    def format_log_level(self, level: str) -> str:
        """Format log level with color"""
        level_upper = level.upper()
        display = level_upper[:4].ljust(5)  # Truncate and pad for alignment

        if self.enable_colors and level_upper in self.LOG_LEVEL_COLORS:
            color = self.LOG_LEVEL_COLORS[level_upper]
            return f"{color}{display}{Style.RESET_ALL}"
        return display

    def format_message(self, message: str, category: Optional[EventCategory] = None) -> str:
        """Format the main message with appropriate color"""
        if not message:
            return ""

        if category is None:
            category = self.get_event_category(message)

        # Highlight important patterns in the message
        if self.enable_colors:
            # Highlight quoted strings
            message = re.sub(
                r'"([^"]*)"',
                f'{Fore.LIGHTYELLOW_EX}"\\1"{Style.RESET_ALL}',
                message
            )

            # Highlight numbers
            message = re.sub(
                r'\b(\d+(?:\.\d+)?)\b',
                f'{Fore.LIGHTCYAN_EX}\\1{Style.RESET_ALL}',
                message
            )

            # Apply category color to the whole message
            color = self.COLOR_SCHEME.get(category, Fore.WHITE)
            message = f"{color}{message}{Style.RESET_ALL}"

        return message

    def format_log_line(
        self,
        timestamp: datetime,
        service_name: str,
        level: str,
        message: str,
        include_timestamp: bool = True,
        include_icons: bool = True
    ) -> str:
        """
        Format a complete log line with all enhancements

        Args:
            timestamp: Log timestamp
            service_name: Name of the service
            level: Log level
            message: Log message
            include_timestamp: Whether to include timestamp
            include_icons: Whether to include icons

        Returns:
            Formatted log line string
        """
        parts = []

        # Timestamp
        if include_timestamp:
            parts.append(self.format_timestamp(timestamp))

        # Service name
        parts.append(self.format_service_name(service_name))

        # Log level
        parts.append(self.format_log_level(level))

        # Icon
        category = self.get_event_category(message, service_name)
        if include_icons and self.enable_icons:
            icon = self.get_icon(category)
            if icon:
                parts.append(icon)

        # Message
        formatted_message = self.format_message(message, category)
        parts.append(formatted_message)

        return " ".join(filter(None, parts))

    def format_cli_response(
        self,
        message: str,
        is_error: bool = False,
        command_context: Optional[str] = None
    ) -> str:
        """
        Format a CLI response message

        Args:
            message: Response message
            is_error: Whether this is an error response
            command_context: Optional command that triggered this response

        Returns:
            Formatted response string
        """
        if is_error:
            category = EventCategory.ERROR
            prefix = "❌ ERROR"
        else:
            category = self.get_event_category(message)
            icon = self.get_icon(category)
            prefix = f"{icon}" if icon else "→"

        if command_context and self.enable_colors:
            header = f"{Fore.LIGHTBLACK_EX}[{command_context}]{Style.RESET_ALL}"
        else:
            header = f"[{command_context}]" if command_context else ""

        formatted_message = self.format_message(message, category)

        parts = filter(None, [prefix, header, formatted_message])
        return " ".join(parts)

    def format_prompt(self, prompt_text: str = "DJ-R3X") -> str:
        """Format the CLI prompt with color"""
        if self.enable_colors:
            return f"{Fore.GREEN + Style.BRIGHT}{prompt_text}> {Style.RESET_ALL}"
        return f"{prompt_text}> "

    def print_separator(self, char: str = "─", width: int = 80) -> str:
        """Print a separator line"""
        line = char * width
        if self.enable_colors:
            return f"{Fore.LIGHTBLACK_EX}{line}{Style.RESET_ALL}"
        return line

    def format_status_message(
        self,
        service_name: str,
        status: str,
        message: str,
        timestamp: Optional[datetime] = None
    ) -> str:
        """Format a service status update message"""
        if timestamp is None:
            timestamp = datetime.now()

        status_colors = {
            "RUNNING": Fore.GREEN,
            "STARTING": Fore.YELLOW,
            "STOPPING": Fore.YELLOW,
            "STOPPED": Fore.RED,
            "ERROR": Fore.RED + Style.BRIGHT,
            "IDLE": Fore.LIGHTBLACK_EX,
        }

        color = status_colors.get(status.upper(), Fore.WHITE) if self.enable_colors else ""

        formatted_parts = [
            self.format_timestamp(timestamp),
            f"{color}[{status}]{Style.RESET_ALL if self.enable_colors else ''}",
            self.format_service_name(service_name),
            message
        ]

        return " ".join(formatted_parts)


# Create a global formatter instance
cli_formatter = CLIFormatter()


def setup_custom_logging_formatter():
    """
    Set up a custom logging formatter that uses our CLI formatter

    This should be called early in the application startup
    """

    class ColoredFormatter(logging.Formatter):
        """Custom formatter that uses CLIFormatter for enhanced output"""

        def format(self, record):
            # Extract components from the log record
            timestamp = datetime.fromtimestamp(record.created)
            service_name = record.name
            level = record.levelname
            message = record.getMessage()

            # Use our CLI formatter
            return cli_formatter.format_log_line(
                timestamp=timestamp,
                service_name=service_name,
                level=level,
                message=message
            )

    return ColoredFormatter()


def demo():
    """Demo function to show the formatting capabilities"""
    print("\n" + cli_formatter.print_separator())
    print(f"{Fore.CYAN + Style.BRIGHT}DJ R3X Voice Control - Enhanced CLI Output Demo{Style.RESET_ALL}")
    print(cli_formatter.print_separator() + "\n")

    # Demo various message types
    examples = [
        ("cantina_os.main", "INFO", "System startup complete"),
        ("deepgram_direct_mic", "DEBUG", "Microphone initialized with sample rate 16000"),
        ("gpt_service", "INFO", 'Processing transcription: "Play some jazz music"'),
        ("intent_router", "INFO", "Intent detected: PLAY_MUSIC genre='jazz'"),
        ("music_controller", "INFO", "Starting track: \"Take Five\" by Dave Brubeck"),
        ("elevenlabs_service", "INFO", "Speech synthesis started, duration: 3.5 seconds"),
        ("eye_light_controller", "DEBUG", "LED pattern set to SPEAKING"),
        ("brain_service", "INFO", "DJ mode activated, selecting next track"),
        ("timeline_executor", "INFO", "Executing timeline step 1/4: Duck audio"),
        ("cached_speech", "INFO", "Commentary cached for track: \"Blue Train\""),
        ("nervous_system", "DEBUG", "State updated: dj_mode_active=true"),
        ("cli_service", "WARNING", "Command not recognized: 'plya music'"),
        ("music_controller", "ERROR", "Failed to load track: file not found"),
    ]

    print(f"{Fore.LIGHTWHITE_EX}Sample Log Output:{Style.RESET_ALL}\n")
    for service, level, message in examples:
        formatted = cli_formatter.format_log_line(
            timestamp=datetime.now(),
            service_name=service,
            level=level,
            message=message
        )
        print(formatted)

    print("\n" + cli_formatter.print_separator() + "\n")

    # Demo CLI responses
    print(f"{Fore.LIGHTWHITE_EX}Sample CLI Responses:{Style.RESET_ALL}\n")

    responses = [
        ("Playing track 3: 'Fly Me to the Moon'", False, "play music 3"),
        ("Track not found in playlist", True, "play music 99"),
        ("DJ mode activated. Starting auto-mix session...", False, "dj start"),
        ("Volume set to 75%", False, "volume 75"),
    ]

    for message, is_error, context in responses:
        formatted = cli_formatter.format_cli_response(message, is_error, context)
        print(formatted)

    print("\n" + cli_formatter.print_separator() + "\n")

    # Demo prompt
    print(f"{Fore.LIGHTWHITE_EX}Sample Prompt:{Style.RESET_ALL}")
    print(cli_formatter.format_prompt(), end="")
    print("(user input would go here)\n")


if __name__ == "__main__":
    demo()