"""
CantinaOS Main Application

This module serves as the entry point for the CantinaOS system.
It handles service initialization, orchestration, and shutdown.
"""

import asyncio
import logging
import os
import signal
from typing import Dict, List, Optional, Any
from dotenv import load_dotenv
from pyee.asyncio import AsyncIOEventEmitter

from .base_service import BaseService
from .event_topics import EventTopics
from .event_payloads import ServiceStatus, LogLevel
from .utils.audio_utils import play_audio_file
from .services import (
    # MicInputService,  # Commented out as we're replacing with DeepgramDirectMicService
    # DeepgramTranscriptionService,  # Commented out as we're replacing with DeepgramDirectMicService
    DeepgramDirectMicService,  # New service for direct microphone access
    GPTService,
    ElevenLabsService,
    EyeLightControllerService,
    CLIService,
    YodaModeManagerService,
    ModeChangeSoundService,
    MusicControllerService,
    MouseInputService,
    IntentRouterService
)
from .services.elevenlabs_service import SpeechPlaybackMethod
from .services.eye_light_controller_service import EyePattern
from .services.command_dispatcher_service import CommandDispatcherService
from .services.mode_command_handler_service import ModeCommandHandlerService

# Configure logging
# logging.basicConfig(
#     level=logging.INFO,
#     format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
#     force=True  # Force reconfiguration of the root logger
# )
# logger = logging.getLogger("cantina_os.main")

# Prevent duplicate logging by removing handlers from the root logger
# for handler in logging.root.handlers[:]:
# logging.root.removeHandler(handler)

# Add a single handler to the root logger
# handler = logging.StreamHandler()
# formatter = logging.Formatter(\'%(asctime)s - %(name)s - %(levelname)s - %(message)s\')
# handler.setFormatter(formatter)
# logging.root.addHandler(handler)

# Set specific logger levels for detailed debugging
# logging.getLogger(\'cantina_os.mode_command_handler\').setLevel(logging.DEBUG)
# logging.getLogger(\'cantina_os.command_dispatcher\').setLevel(logging.DEBUG)
# logging.getLogger(\'cantina_os.base_service\').setLevel(logging.DEBUG)
# logging.getLogger(\'cantina_os.cli\').setLevel(logging.DEBUG)
# Keep these less verbose to avoid too much noise
# logging.getLogger(\'cantina_os.deepgram_direct_mic\').setLevel(logging.INFO)  # Updated logger name

def set_global_log_level(level: int) -> None:
    """Sets the global logging level for the root logger and all handlers."""
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    for handler in root_logger.handlers:
        handler.setLevel(level)
    # Also update all existing loggers
    for logger_name in logging.root.manager.loggerDict:
        logger_instance = logging.getLogger(logger_name)
        logger_instance.setLevel(level)
        for h in logger_instance.handlers:
            h.setLevel(level)
    logging.info(f"Global log level set to: {logging.getLevelName(level)}")

# Initial logging setup
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    force=True # Force reconfiguration
)
set_global_log_level(logging.INFO) # Default to INFO

logger = logging.getLogger("cantina_os.main")

class CantinaOS:
    """
    Main application class that manages the lifecycle of all services.
    """
    
    def __init__(self, config: Dict[str, Any] = None):
        """Initialize the CantinaOS system."""
        self._event_bus = AsyncIOEventEmitter()
        self._services: Dict[str, BaseService] = {}
        self._shutdown_event = asyncio.Event()
        self._logger = logging.getLogger("cantina_os.main")
        self._load_config()  # Load values from .env file
        
        # Merge provided config with loaded values instead of replacing them
        if config:
            self._config.update(config)  # Update with any provided values, preserving loaded ones
        
        # Subscribe to global log level changes
        self._event_bus.on(EventTopics.DEBUG_SET_GLOBAL_LEVEL, self._handle_set_global_log_level)
        
        # Subscribe to system shutdown events
        self._event_bus.on(EventTopics.SYSTEM_SHUTDOWN_REQUESTED, self._handle_shutdown_requested)
        
    @property
    def event_bus(self):
        """Get the event bus."""
        return self._event_bus
        
    @property
    def logger(self):
        """Get the logger for this application."""
        return self._logger
        
    def _load_config(self) -> None:
        """Load configuration from environment variables."""
        # Load environment variables from .env file if present
        load_dotenv()
        
        # Initialize config dictionary
        self._config = {}
        
        # Get Deepgram API key
        deepgram_api_key = os.getenv("DEEPGRAM_API_KEY")
        if deepgram_api_key:
            self._config["DEEPGRAM_API_KEY"] = deepgram_api_key
            self.logger.info(f"Using Deepgram API key: {deepgram_api_key[:5]}...{deepgram_api_key[-5:]}")
        else:
            self.logger.warning("DEEPGRAM_API_KEY not found in environment")
        
        # Get OpenAI API key
        openai_api_key = os.getenv("OPENAI_API_KEY")
        if openai_api_key:
            self._config["OPENAI_API_KEY"] = openai_api_key
            self.logger.info(f"Using OpenAI API key: {openai_api_key[:5]}...{openai_api_key[-5:]}")
        else:
            self.logger.warning("OPENAI_API_KEY not found in environment")
        
        # Get ElevenLabs API key
        elevenlabs_api_key = os.getenv("ELEVENLABS_API_KEY")
        if elevenlabs_api_key:
            self._config["ELEVENLABS_API_KEY"] = elevenlabs_api_key
            self.logger.info(f"Using ElevenLabs API key: {elevenlabs_api_key[:5]}...{elevenlabs_api_key[-5:]}")
        else:
            self.logger.warning("ELEVENLABS_API_KEY not found in environment")
        
        # Get OpenAI model
        openai_model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
        self._config["OPENAI_MODEL"] = openai_model
        
        # Set log level from environment or default to INFO
        log_level = os.getenv("LOG_LEVEL", "INFO")
        logging.getLogger("cantina_os").setLevel(log_level)
        
        # Load API keys and configuration into self._config
        self._config = {
            "DEEPGRAM_API_KEY": os.getenv("DEEPGRAM_API_KEY", ""),
            "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY", ""),
            "ELEVENLABS_API_KEY": os.getenv("ELEVENLABS_API_KEY", ""),
            "ELEVENLABS_VOICE_ID": os.getenv("ELEVENLABS_VOICE_ID", ""),
            "OPENAI_MODEL": os.getenv("OPENAI_MODEL", "gpt-4o"),
            "AUDIO_SAMPLE_RATE": int(os.getenv("AUDIO_SAMPLE_RATE", "16000")),
            "AUDIO_CHANNELS": int(os.getenv("AUDIO_CHANNELS", "1")),
        }
        
        # Log loaded configuration (masking API keys for security)
        self.logger.info("Loaded configuration from environment")
        if self._config["DEEPGRAM_API_KEY"]:
            key = self._config["DEEPGRAM_API_KEY"]
            self.logger.info(f"Using Deepgram API key: {key[:5]}...{key[-5:] if len(key) > 10 else ''}")
        else:
            self.logger.warning("No Deepgram API key found in environment")
        
        if self._config["OPENAI_API_KEY"]:
            key = self._config["OPENAI_API_KEY"]
            self.logger.info(f"Using OpenAI API key: {key[:5]}...{key[-5:] if len(key) > 10 else ''}")
        else:
            self.logger.warning("No OpenAI API key found in environment")
        
        if self._config["ELEVENLABS_API_KEY"]:
            key = self._config["ELEVENLABS_API_KEY"]
            self.logger.info(f"Using ElevenLabs API key: {key[:5]}...{key[-5:] if len(key) > 10 else ''}")
        else:
            self.logger.warning("No ElevenLabs API key found in environment")
        
    async def _register_commands(self, dispatcher: CommandDispatcherService) -> None:
        """Register command handlers with the dispatcher.
        
        Args:
            dispatcher: The command dispatcher service
        """
        self.logger.info("Registering commands with dispatcher")
        
        # The current issue is that main.py believes the dispatcher handles all registrations,
        # but it doesn't. We need to manually register the missing commands.
        
        # Register basic commands that aren't being auto-registered by the dispatcher
        if "help" not in dispatcher.get_registered_commands():
            dispatcher.register_command_handler("help", EventTopics.CLI_HELP_REQUEST)
            
        if "reset" not in dispatcher.get_registered_commands():
            dispatcher.register_command_handler("reset", EventTopics.CLI_STATUS_REQUEST)
        
        # Additional commands that might need registration
        if "status" not in dispatcher.get_registered_commands():
            dispatcher.register_command_handler("status", EventTopics.CLI_STATUS_REQUEST)
        
        if "debug" not in dispatcher.get_registered_commands():
            dispatcher.register_command_handler("debug", EventTopics.DEBUG_COMMAND)
        
        # Handle mode commands
        for mode_cmd in ["engage", "disengage", "ambient", "idle"]:
            if mode_cmd not in dispatcher.get_registered_commands():
                dispatcher.register_command_handler(mode_cmd, EventTopics.SYSTEM_SET_MODE_REQUEST)
        
        # Basic music and eye commands
        if "music" not in dispatcher.get_registered_commands():
            dispatcher.register_command_handler("music", EventTopics.MUSIC_COMMAND)
            
        if "eye" not in dispatcher.get_registered_commands():
            dispatcher.register_command_handler("eye", EventTopics.EYE_COMMAND)
        
        # Compound commands
        # Music commands
        for cmd in ["play music", "stop music", "list music", "install music"]:
            if cmd not in dispatcher.get_registered_commands():
                dispatcher.register_compound_command(cmd, EventTopics.MUSIC_COMMAND)
        
        # Eye commands
        for cmd in ["eye pattern", "eye test", "eye status"]:
            if cmd not in dispatcher.get_registered_commands():
                dispatcher.register_compound_command(cmd, EventTopics.EYE_COMMAND)
        
        # Debug commands
        for cmd in ["debug level", "debug trace", "debug music"]:
            if cmd not in dispatcher.get_registered_commands():
                dispatcher.register_compound_command(cmd, EventTopics.DEBUG_COMMAND)
        
        # Log registered commands for debugging
        commands = dispatcher.get_registered_commands()
        self.logger.info(f"Registered commands: {', '.join(commands)}")
        
        # Log command shortcuts
        shortcuts = dispatcher.get_shortcut_map()
        self.logger.info(f"Command shortcuts: {shortcuts}")
        
    async def _initialize_services(self) -> None:
        """Initialize all services."""
        self.logger.info("Initializing services")
        
        # Define the service initialization order
        service_order = [
            "yoda_mode_manager",
            "command_dispatcher",
            "mouse_input",  # Keep mouse input service for click control
            "deepgram_direct_mic",  # New service for audio capture and transcription
            "gpt",
            "intent_router",  # Add IntentRouterService to route LLM intents to hardware commands
            "elevenlabs",  # Add ElevenLabs service to convert LLM responses to speech
            "mode_change_sound",
            "music_controller",
            "eye_light_controller",  # Add eye light controller service for LED control
            "debug",  # Add debug service for LLM response logging
            "cli"
        ]
        
        try:
            # Initialize mode manager first - it's required by most services
            self.logger.info("Starting yoda_mode_manager service")
            self._services["yoda_mode_manager"] = self._create_service("yoda_mode_manager")
            await self._services["yoda_mode_manager"].start()
            
            # Initialize the rest of the services in order
            for service_name in service_order:
                # Skip mode_manager as it's already started
                if service_name == "yoda_mode_manager":
                    continue
                    
                self.logger.info(f"Starting {service_name} service")
                self._services[service_name] = self._create_service(service_name)
                
                try:
                    await self._services[service_name].start()
                    self.logger.info(f"Started service: {service_name}")
                except Exception as e:
                    # Log the error but continue with other services if possible
                    self.logger.error(f"Failed to start service {service_name}: {e}")
                    
                    # If a critical service fails, we need to abort
                    if service_name in ["yoda_mode_manager", "command_dispatcher", "cli"]:
                        self.logger.error(f"Critical service {service_name} failed to start: {e}")
                        # Clean up any started services
                        await self._cleanup_services()
                        raise RuntimeError(f"Critical service {service_name} failed to start: {e}")
                        
        except Exception as e:
            self.logger.error(f"Error during service initialization: {e}")
            await self._cleanup_services()
            raise
            
    async def _cleanup_services(self) -> None:
        """Clean up all started services in reverse order."""
        # Stop services in reverse order
        for service_name in reversed(list(self._services.keys())):
            try:
                self.logger.info(f"Stopping {service_name} service")
                await self._services[service_name].stop()
                self.logger.info(f"Stopped service: {service_name}")
            except Exception as e:
                self.logger.error(f"Error stopping service {service_name}: {e}")
                
    def _setup_signal_handlers(self) -> None:
        """Set up handlers for system signals."""
        for sig in (signal.SIGTERM, signal.SIGINT):
            asyncio.get_event_loop().add_signal_handler(
                sig,
                lambda s=sig: asyncio.create_task(self._handle_shutdown(s))
            )
            
    async def _handle_shutdown(self, sig: signal.Signals) -> None:
        """Handle system shutdown signals."""
        logger.info(f"Received shutdown signal: {sig.name}")
        self._shutdown_event.set()
        
    async def _handle_shutdown_requested(self, payload: Dict[str, Any]) -> None:
        """Handle system shutdown requests from events."""
        reason = payload.get('reason', 'Unknown reason')
        restart = payload.get('restart', False)
        logger.info(f"Received shutdown request event: {reason}, restart={restart}")
        self._shutdown_event.set()
        
    async def run(self) -> None:
        """Run the CantinaOS system."""
        try:
            # Set up signal handlers
            self._setup_signal_handlers()
            
            # Initialize services
            await self._initialize_services()
            
            # Register commands with the command dispatcher
            self.logger.info("Registering commands")
            command_dispatcher = self._services.get("command_dispatcher")
            if command_dispatcher:
                await self._register_commands(command_dispatcher)
                self.logger.info("Commands registered successfully")
            else:
                self.logger.error("Cannot register commands - command_dispatcher service not found")
                
            logger.info("CantinaOS system initialized successfully")
            
            # Play startup sound once all services are initialized
            startup_sound_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 
                "audio", 
                "startours_audio",
                "startours_ding.mp3"
            )
            
            # Add debug event listener for TRANSCRIPTION_FINAL events
            async def debug_transcription_handler(payload):
                logger.info(f"DEBUG EVENT MONITOR - Received TRANSCRIPTION_FINAL event: {str(payload)[:200]}...")
            
            self._event_bus.on(EventTopics.TRANSCRIPTION_FINAL, debug_transcription_handler)
            logger.info(f"Added debug monitor for TRANSCRIPTION_FINAL events - topic value: '{str(EventTopics.TRANSCRIPTION_FINAL)}'")
            
            if os.path.exists(startup_sound_path):
                logger.info("Playing startup sound")
                await play_audio_file(startup_sound_path, blocking=False)
                # Emit a system event to notify about startup sound
                self._event_bus.emit(
                    EventTopics.SYSTEM_STARTUP,
                    {"message": "System fully initialized, startup sound played"}
                )
            else:
                logger.warning(f"Startup sound file not found: {startup_sound_path}")
                # Try alternate path
                alternate_path = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                    "audio",
                    "startours_audio",
                    "startours_ding.mp3"
                )
                if os.path.exists(alternate_path):
                    logger.info("Playing startup sound (alternate path)")
                    await play_audio_file(alternate_path, blocking=False)
                    # Emit a system event to notify about startup sound
                    self._event_bus.emit(
                        EventTopics.SYSTEM_STARTUP,
                        {"message": "System fully initialized, startup sound played"}
                    )
                else:
                    logger.warning(f"Alternate startup sound file not found: {alternate_path}")
            
            # Wait for shutdown signal
            await self._shutdown_event.wait()
            
        except Exception as e:
            logger.error(f"Error running CantinaOS: {e}")
            raise
            
        finally:
            # Ensure services are shut down
            await self._cleanup_services()
            logger.info("CantinaOS system shutdown complete")
            
    def _create_service(self, service_name: str):
        """Create and configure a service instance for the given name."""
        service_class_map = {
            "deepgram_direct_mic": DeepgramDirectMicService,
            "gpt": GPTService,
            "elevenlabs": ElevenLabsService,
            "eye_light_controller": EyeLightControllerService,
            "cli": CLIService,
            "yoda_mode_manager": YodaModeManagerService,
            "mode_change_sound": ModeChangeSoundService,
            "music_controller": MusicControllerService,
            "mouse_input": MouseInputService,
            "intent_router": IntentRouterService,
            "command_dispatcher": CommandDispatcherService
        }
        
        # Early return if service doesn't exist in map
        if service_name not in service_class_map:
            self.logger.warning(f"No service class found for {service_name}")
            return None
            
        service_class = service_class_map[service_name]
        
        # Get service config by name or empty dict if not found
        service_config = self._config.get(service_name, {})
        
        # Special config handling for specific services
        if service_name == "gpt":
            # Ensure GPT service has OpenAI API key
            if "OPENAI_API_KEY" not in service_config:
                service_config["OPENAI_API_KEY"] = self._config.get("OPENAI_API_KEY", "")
            if "GPT_MODEL" not in service_config:
                service_config["GPT_MODEL"] = self._config.get("OPENAI_MODEL", "gpt-4.1-mini")
            # Disable streaming to ensure proper text and tool call handling
            service_config["STREAMING"] = False
                
        elif service_name == "elevenlabs":
            # Ensure ElevenLabs service has API key and other configuration
            if "ELEVENLABS_API_KEY" not in service_config:
                service_config["ELEVENLABS_API_KEY"] = self._config.get("ELEVENLABS_API_KEY", "")
            if "VOICE_ID" not in service_config:
                service_config["VOICE_ID"] = self._config.get("ELEVENLABS_VOICE_ID", "")
                
        elif service_name == "deepgram_direct_mic":
            # Ensure Deepgram service has API key and audio configuration
            if "DEEPGRAM_API_KEY" not in service_config:
                service_config["DEEPGRAM_API_KEY"] = self._config.get("DEEPGRAM_API_KEY", "")
            if "SAMPLE_RATE" not in service_config:
                service_config["SAMPLE_RATE"] = self._config.get("AUDIO_SAMPLE_RATE", 16000)
            if "CHANNELS" not in service_config:
                service_config["CHANNELS"] = self._config.get("AUDIO_CHANNELS", 1)
            
        elif service_name == "music_controller":
            # Configure music controller with proper music directory
            # Look in the standard audio/music folder instead
            music_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "audio", "music")
            self.logger.info(f"Configuring MusicController with music_dir: {music_dir}")
            
            # Fallback to assets directory only if primary does not exist
            if not os.path.exists(music_dir):
                fallback_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "music")
                self.logger.info(f"Primary music directory not found, using fallback: {fallback_dir}")
                music_dir = fallback_dir
            
            # Create music directory if it doesn't exist
            os.makedirs(music_dir, exist_ok=True)
            self.logger.info(f"Ensured music directory exists: {music_dir}")
            
            # Set the music_dir in the config
            if isinstance(service_config, dict):
                service_config["music_dir"] = music_dir
            else:
                service_config = {"music_dir": music_dir}
        
        # All services need the global event bus
        try:
            service = service_class(self._event_bus, service_config)
            return service
        except Exception as e:
            self.logger.error(f"Error creating service {service_name}: {str(e)}")
            return None

    async def _handle_set_global_log_level(self, payload: Dict[str, Any]) -> None:
        """Handles the event to set the global log level."""
        try:
            level_name = payload.get("level")
            if level_name:
                python_log_level = getattr(logging, level_name.upper(), None)
                if python_log_level is not None:
                    set_global_log_level(python_log_level)
                    self.logger.info(f"Global log level changed to {level_name.upper()} via event.")
                else:
                    self.logger.warning(f"Invalid log level received in event: {level_name}")
            else:
                self.logger.warning("No log level provided in DEBUG_SET_GLOBAL_LEVEL event payload.")
        except Exception as e:
            self.logger.error(f"Error handling DEBUG_SET_GLOBAL_LEVEL event: {e}")

def main() -> None:
    """Entry point for the application."""
    cantina_os = CantinaOS()
    
    try:
        asyncio.run(cantina_os.run())
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        raise
        
if __name__ == "__main__":
    main() 