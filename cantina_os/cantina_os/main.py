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
    MicInputService, 
    DeepgramTranscriptionService, 
    GPTService,
    ElevenLabsService,
    EyeLightControllerService,
    CLIService,
    YodaModeManagerService,
    ModeChangeSoundService,
    MusicControllerService
)
from .services.elevenlabs_service import SpeechPlaybackMethod
from .services.eye_light_controller_service import EyePattern
from .services.command_dispatcher_service import CommandDispatcherService
from .services.mode_command_handler_service import ModeCommandHandlerService

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    force=True  # Force reconfiguration of the root logger
)
logger = logging.getLogger("cantina_os.main")

# Prevent duplicate logging by removing handlers from the root logger
for handler in logging.root.handlers[:]:
    logging.root.removeHandler(handler)

# Add a single handler to the root logger
handler = logging.StreamHandler()
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
logging.root.addHandler(handler)

# Set specific logger levels
logging.getLogger('cantina_os.deepgram_transcription').setLevel(logging.DEBUG)
logging.getLogger('cantina_os.mic_input').setLevel(logging.DEBUG)

class CantinaOS:
    """
    Main application class that manages the lifecycle of all services.
    """
    
    def __init__(self, config: Dict[str, Any] = None):
        """Initialize the CantinaOS system."""
        self.event_bus = AsyncIOEventEmitter()
        self.services: Dict[str, BaseService] = {}
        self._shutdown_event = asyncio.Event()
        self._load_config()
        self._config = config or {}
        
    def _load_config(self) -> None:
        """Load configuration from environment variables."""
        load_dotenv()
        
        # Set log level from environment or default to INFO
        log_level = os.getenv("LOG_LEVEL", "INFO")
        logging.getLogger("cantina_os").setLevel(log_level)
        
    async def _register_commands(self, dispatcher: CommandDispatcherService) -> None:
        """Register command handlers with the dispatcher.
        
        Args:
            dispatcher: The command dispatcher service
        """
        # Mode commands
        await dispatcher.register_command(
            "engage",
            "mode_command_handler",
            EventTopics.MODE_COMMAND
        )
        await dispatcher.register_command(
            "ambient",
            "mode_command_handler",
            EventTopics.MODE_COMMAND
        )
        await dispatcher.register_command(
            "disengage",
            "mode_command_handler",
            EventTopics.MODE_COMMAND
        )
        await dispatcher.register_command(
            "status",
            "mode_command_handler",
            EventTopics.MODE_COMMAND
        )
        await dispatcher.register_command(
            "reset",
            "mode_command_handler",
            EventTopics.MODE_COMMAND
        )
        
        # Voice recording command
        await dispatcher.register_command(
            "record",
            "cli",
            EventTopics.CLI_COMMAND
        )
        
        # Music commands
        await dispatcher.register_command(
            "list music",
            "music_controller",
            EventTopics.MUSIC_COMMAND
        )
        await dispatcher.register_command(
            "play music",
            "music_controller",
            EventTopics.MUSIC_COMMAND
        )
        await dispatcher.register_command(
            "stop music",
            "music_controller",
            EventTopics.MUSIC_COMMAND
        )
        
        # Help command
        await dispatcher.register_command(
            "help",
            "mode_command_handler",
            EventTopics.MODE_COMMAND
        )
        
    async def _initialize_services(self) -> None:
        """Initialize all services."""
        self.logger.info("EyeLightControllerService running in mock mode (no hardware)")
        
        # Dictionary to hold initialized services
        self._services = {}
        
        # Define the service initialization order
        service_order = [
            "mode_manager",
            "command_dispatcher",
            "mode_command_handler",
            "mic_input",
            "transcription",
            "gpt",
            "mode_change_sound",
            "cli"
        ]
        
        try:
            # Initialize mode manager first - it's required by most services
            self.logger.info("Starting mode manager service")
            self._services["mode_manager"] = self._create_service("mode_manager")
            await self._services["mode_manager"].start()
            
            # Initialize the rest of the services in order
            for service_name in service_order:
                # Skip mode_manager as it's already started
                if service_name == "mode_manager":
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
                    if service_name in ["mode_manager", "command_dispatcher", "cli"]:
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
        
    async def run(self) -> None:
        """Run the CantinaOS system."""
        try:
            # Set up signal handlers
            self._setup_signal_handlers()
            
            # Initialize services
            await self._initialize_services()
            logger.info("CantinaOS system initialized successfully")
            
            # Play startup sound once all services are initialized
            startup_sound_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 
                "audio", 
                "startours_audio",
                "startours_ding.mp3"
            )
            
            if os.path.exists(startup_sound_path):
                logger.info("Playing startup sound")
                await play_audio_file(startup_sound_path, blocking=False)
                # Emit a system event to notify about startup sound
                self.event_bus.emit(
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
                    self.event_bus.emit(
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
        """Create a service instance by name.
        
        Args:
            service_name: The name of the service to create
            
        Returns:
            The service instance
        """
        # Load environment variables for service configuration
        config = {
            "DEEPGRAM_API_KEY": os.getenv("DEEPGRAM_API_KEY"),
            "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY"),
            "ELEVENLABS_API_KEY": os.getenv("ELEVENLABS_API_KEY"),
            "AUDIO_SAMPLE_RATE": int(os.getenv("AUDIO_SAMPLE_RATE", "16000")),
            "AUDIO_CHANNELS": int(os.getenv("AUDIO_CHANNELS", "1")),
            "GPT_MODEL": os.getenv("GPT_MODEL", "gpt-4o"),
            "SYSTEM_PROMPT": os.getenv("SYSTEM_PROMPT", 
                "You are DJ R3X, a helpful and enthusiastic Star Wars droid DJ assistant."),
            "ELEVENLABS_VOICE_ID": os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM"),
            "ELEVENLABS_MODEL_ID": os.getenv("ELEVENLABS_MODEL_ID", "eleven_turbo_v2"),
            "MODE_CHANGE_GRACE_PERIOD_MS": int(os.getenv("MODE_CHANGE_GRACE_PERIOD_MS", "100")),
            "CLI_MAX_HISTORY": int(os.getenv("CLI_MAX_HISTORY", "100"))
        }
        
        # Create and return the appropriate service
        if service_name == "mode_manager":
            from .services.yoda_mode_manager_service import YodaModeManagerService
            return YodaModeManagerService(self.event_bus, config, self.logger)
            
        elif service_name == "command_dispatcher":
            from .services.command_dispatcher_service import CommandDispatcherService
            return CommandDispatcherService(self.event_bus, config, self.logger)
            
        elif service_name == "mode_command_handler":
            from .services.mode_command_handler_service import ModeCommandHandlerService
            # Get the mode manager service
            mode_manager = self._services.get("mode_manager")
            return ModeCommandHandlerService(self.event_bus, mode_manager, config, self.logger)
            
        elif service_name == "mic_input":
            from .services.mic_input_service import MicInputService
            return MicInputService(self.event_bus, config, self.logger)
            
        elif service_name == "transcription":
            from .services.deepgram_transcription_service import DeepgramTranscriptionService
            return DeepgramTranscriptionService(self.event_bus, config, self.logger)
            
        elif service_name == "gpt":
            from .services.gpt_service import GPTService
            return GPTService(self.event_bus, config, self.logger)
            
        elif service_name == "mode_change_sound":
            from .services.mode_change_sound_service import ModeChangeSoundService
            return ModeChangeSoundService(self.event_bus, config, self.logger)
            
        elif service_name == "cli":
            from .services.cli_service import CLIService
            return CLIService(self.event_bus, config, self.logger)
            
        elif service_name == "eye_light_controller":
            from .services.eye_light_controller_service import EyeLightControllerService
            # Configure LED controller
            mock_mode = os.getenv("MOCK_LED_CONTROLLER", "true").lower() == "true"
            serial_port = os.getenv("ARDUINO_SERIAL_PORT", None)
            
            return EyeLightControllerService(
                self.event_bus,
                serial_port=serial_port,
                baud_rate=int(os.getenv("ARDUINO_BAUD_RATE", "115200")),
                mock_mode=mock_mode
            )
            
        elif service_name == "elevenlabs":
            from .services.elevenlabs_service import ElevenLabsService, SpeechPlaybackMethod
            
            # Determine playback method for ElevenLabs
            playback_method_str = os.getenv("AUDIO_PLAYBACK_METHOD", "auto").lower()
            if playback_method_str == "auto":
                try:
                    import sounddevice as sd
                    playback_method = SpeechPlaybackMethod.SOUNDDEVICE
                except ImportError:
                    self.logger.info("Sounddevice not available, falling back to system audio playback")
                    playback_method = SpeechPlaybackMethod.SYSTEM
            elif playback_method_str == "sounddevice":
                playback_method = SpeechPlaybackMethod.SOUNDDEVICE
            else:
                playback_method = SpeechPlaybackMethod.SYSTEM
            
            return ElevenLabsService(
                event_bus=self.event_bus,
                api_key=config.get("ELEVENLABS_API_KEY"),
                voice_id=config.get("ELEVENLABS_VOICE_ID"),
                model_id=config.get("ELEVENLABS_MODEL_ID"),
                playback_method=playback_method,
                stability=float(os.getenv("ELEVENLABS_STABILITY", "0.71")),
                similarity_boost=float(os.getenv("ELEVENLABS_SIMILARITY_BOOST", "0.5"))
            )
            
        elif service_name == "music_controller":
            from .services.music_controller_service import MusicControllerService
            return MusicControllerService(
                self.event_bus, 
                os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "music")
            )
            
        else:
            raise ValueError(f"Unknown service: {service_name}")

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