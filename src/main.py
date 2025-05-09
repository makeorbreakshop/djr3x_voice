#!/usr/bin/env python3
"""
DJ R3X — Lights & Voice MVP

Main application for the DJ R3X robot, coordinating voice processing, LED animation,
and background music with an event-driven architecture.
"""

import os
import sys
import time
import asyncio
import signal
import logging
import argparse
import json
import platform
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Any, Union
from dotenv import load_dotenv, find_dotenv

# Add the project root to the path so we can use local imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import DEBUG_MODE first for logging configuration
from config.app_settings import DEBUG_MODE

# Configure logging - use a format that's more concise
logging.basicConfig(
    level=logging.DEBUG if DEBUG_MODE else logging.INFO,
    format='%(asctime)s - %(levelname)s - %(name)s - %(message)s',
    datefmt='%H:%M:%S'  # Use shorter time format
)
logger = logging.getLogger(__name__)

# Import remaining configuration
from config.app_settings import (
    TEXT_ONLY_MODE,
    PUSH_TO_TALK_MODE,
    DISABLE_AUDIO_PROCESSING,
    SAMPLE_RATE,
    CHANNELS,
    LED_SERIAL_PORT,
    LED_BAUD_RATE,
    DISABLE_EYES,
    STARTUP_SOUND,
    USE_STREAMING_VOICE
)
from config.voice_settings import active_config as voice_config
from config.openai_settings import active_config as openai_config

# Local imports
from src.bus import EventBus, EventTypes, SystemMode
from src.voice_manager import VoiceManager
from src.led_manager import LEDManager
from src.music_manager import MusicManager
from src.system_mode_manager import SystemModeManager
from src.command_input import CommandInputThread
from src.stream_manager import StreamManager

# Monkey patch httpx.Client to fix the proxies issue with OpenAI 1.3.5
import httpx
original_httpx_init = httpx.Client.__init__
def patched_httpx_init(self, *args, **kwargs):
    # Remove 'proxies' if present to avoid the error
    if 'proxies' in kwargs:
        logger.info("Fixing OpenAI compatibility: Removing 'proxies' from httpx.Client")
        kwargs.pop('proxies')
    # Call original init with the cleaned kwargs
    return original_httpx_init(self, *args, **kwargs)
# Apply the patch
httpx.Client.__init__ = patched_httpx_init

# Global variables
event_bus = None  # Will be initialized in main()
voice_manager: Optional[VoiceManager] = None
stream_manager: Optional[StreamManager] = None
led_manager: Optional[LEDManager] = None
music_manager: Optional[MusicManager] = None
system_mode_manager: Optional[SystemModeManager] = None 
command_input_thread: Optional[CommandInputThread] = None
shutdown_event = asyncio.Event()

def load_environment() -> Dict[str, str]:
    """Load and validate environment variables.
    
    Returns:
        Dict[str, str]: Dictionary of validated environment variables
    """
    # Load .env file
    env_path = find_dotenv(filename='.env', raise_error_if_not_found=True)
    load_dotenv(env_path)
    
    # Required API keys
    required_vars = [
        "OPENAI_API_KEY",
        "ELEVENLABS_API_KEY",
        "ELEVENLABS_VOICE_ID"
    ]
    
    # Conditionally add Deepgram API key if streaming voice is enabled
    if USE_STREAMING_VOICE:
        required_vars.append("DEEPGRAM_API_KEY")
    
    # Validate required variables
    missing = [var for var in required_vars if not os.getenv(var)]
    if missing:
        logger.error(f"Missing required environment variables: {', '.join(missing)}")
        sys.exit(1)
    
    # Load DJ R3X persona
    persona_file = os.getenv("DJ_R3X_PERSONA_FILE")
    if persona_file and os.path.exists(persona_file):
        with open(persona_file, "r") as f:
            dj_rex_persona = f.read()
        logger.info(f"Loaded DJ R3X persona from {persona_file}")
    else:
        dj_rex_persona = os.getenv("DJ_R3X_PERSONA", 
            "You are DJ R3X, a droid DJ from Star Wars. You have an upbeat, quirky personality. "
            "keep responses brief and entertaining. You love music and Star Wars.")
        logger.info("Using default DJ R3X persona")
    
    # Return validated environment
    return {
        "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY"),
        "ELEVENLABS_API_KEY": os.getenv("ELEVENLABS_API_KEY"),
        "ELEVENLABS_VOICE_ID": os.getenv("ELEVENLABS_VOICE_ID"),
        "OPENAI_MODEL": os.getenv("OPENAI_MODEL", "gpt-4"),
        "DJ_R3X_PERSONA": dj_rex_persona,
        "DISABLE_EYES": os.getenv("DISABLE_EYES", "").lower() == "true",
        "DEEPGRAM_API_KEY": os.getenv("DEEPGRAM_API_KEY", "")
    }

def play_startup_sound():
    """Play the startup sound using platform-specific commands."""
    if not os.path.exists(STARTUP_SOUND):
        logger.warning(f"Startup sound file not found: {STARTUP_SOUND}")
        return

    try:
        system = platform.system().lower()
        if system == 'darwin':  # macOS
            subprocess.run(['afplay', STARTUP_SOUND])
        elif system == 'linux':
            subprocess.run(['aplay', STARTUP_SOUND])
        elif system == 'windows':
            import winsound
            winsound.PlaySound(STARTUP_SOUND, winsound.SND_FILENAME)
    except Exception as e:
        logger.warning(f"Could not play startup sound: {e}")

async def initialize_components(env: Dict[str, str], test_mode: bool = False) -> bool:
    """Initialize all system components.
    
    Args:
        env: Dictionary of environment variables
        test_mode: Run in test mode without external dependencies
    
    Returns:
        bool: True if all components initialized successfully
    """
    global voice_manager, stream_manager, led_manager, music_manager, system_mode_manager, command_input_thread
    
    try:
        logger.info("Initializing components...")
        
        # Get the currently running event loop to ensure consistency
        current_loop = asyncio.get_running_loop()
        
        # Initialize Voice Manager first (always needed for GPT and TTS)
        try:
            voice_manager = VoiceManager(
                event_bus=event_bus,
                openai_key=env["OPENAI_API_KEY"],
                openai_model=env["OPENAI_MODEL"],
                elevenlabs_key=env["ELEVENLABS_API_KEY"],
                elevenlabs_voice_id=env["ELEVENLABS_VOICE_ID"],
                persona=env["DJ_R3X_PERSONA"],
                voice_config=voice_config,
                openai_config=openai_config,
                text_only_mode=TEXT_ONLY_MODE,
                push_to_talk_mode=PUSH_TO_TALK_MODE,
                disable_audio_processing=DISABLE_AUDIO_PROCESSING,
                sample_rate=SAMPLE_RATE,
                channels=CHANNELS,
                test_mode=test_mode,
                loop=current_loop
            )
            await voice_manager.start()
            event_bus.voice_manager = voice_manager
            logger.info("Voice Manager initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize Voice Manager: {e}")
            voice_manager = None
            return False

        # Initialize Stream Manager if streaming is enabled
        if USE_STREAMING_VOICE:
            try:
                logger.info("Initializing Stream Manager for streaming voice processing...")
                stream_manager = StreamManager(
                    event_bus=event_bus,
                    deepgram_api_key=env["DEEPGRAM_API_KEY"],
                    push_to_talk_mode=PUSH_TO_TALK_MODE,
                    sample_rate=SAMPLE_RATE,
                    channels=CHANNELS,
                    test_mode=test_mode,
                    loop=current_loop,
                    debug_mode=DEBUG_MODE
                )
                event_bus.stream_manager = stream_manager
                logger.info("Stream Manager initialized successfully")
            except Exception as e:
                logger.error(f"Failed to initialize Stream Manager: {e}")
                stream_manager = None
                # Continue with Voice Manager only
                logger.info("Continuing with Voice Manager only...")

        # Initialize LED Manager
        try:
            led_manager = LEDManager(
                event_bus=event_bus,
                disable_eyes=DISABLE_EYES
            )
            await led_manager.start()
            event_bus.led_manager = led_manager
        except Exception as e:
            logger.warning(f"Failed to initialize LED Manager: {e}")
            led_manager = None
        
        # Initialize Music Manager
        try:
            music_manager = MusicManager(event_bus)
            await music_manager.start()
            event_bus.music_manager = music_manager
        except Exception as e:
            logger.warning(f"Failed to initialize Music Manager: {e}")
            music_manager = None

        # Initialize System Mode Manager
        # Start in STARTUP mode, will transition to IDLE mode after initialization
        system_mode_manager = SystemModeManager(event_bus, initial_mode=SystemMode.STARTUP)
        
        # Play startup sound after all components are initialized
        play_startup_sound()
        
        # Transition to IDLE mode after startup
        logger.info("Startup complete. Transitioning to IDLE mode...")
        await system_mode_manager.change_mode(SystemMode.IDLE)
        
        print("\n" + "="*50)
        print(" DJ R3X system initialized and ready!")  
        print(" Currently in IDLE mode")
        print(" Type 'help' for available commands")
        print("="*50 + "\n")
            
        # Initialize Command Input Thread (after all init is complete)
        command_input_thread = CommandInputThread(event_bus, current_loop)
        command_input_thread.start()

        return True

    except Exception as e:
        logger.error(f"Error during component initialization: {e}")
        return False

async def cleanup_components() -> None:
    """Clean up all system components and resources."""
    logger.info("Cleaning up components...")
    
    # Order matters: stop components in reverse order of dependencies
    
    # Stop Command Input Thread
    if command_input_thread and command_input_thread.is_alive():
        command_input_thread.stop()
    
    # Stop Music Manager
    if music_manager:
        try:
            await music_manager.stop()
            logger.info("Music Manager stopped")
        except Exception as e:
            logger.error(f"Error stopping Music Manager: {e}")
    
    # Stop LED Manager
    if led_manager:
        try:
            await led_manager.stop()
            logger.info("LED Manager stopped")
        except Exception as e:
            logger.error(f"Error stopping LED Manager: {e}")
    
    # Stop Stream Manager (if using streaming voice)
    if stream_manager:
        try:
            await stream_manager.stop()
            logger.info("Stream Manager stopped")
        except Exception as e:
            logger.error(f"Error stopping Stream Manager: {e}")
    
    # Stop Voice Manager
    if voice_manager:
        try:
            await voice_manager.stop()
            logger.info("Voice Manager stopped")
        except Exception as e:
            logger.error(f"Error stopping Voice Manager: {e}")
    
    logger.info("All components cleaned up")

def signal_handler() -> None:
    """Handle system signals for graceful shutdown."""
    global shutdown_event
    shutdown_event.set()
    logger.info("Shutdown signal received")

async def process_demo_interaction(text: str) -> None:
    """Process a demo interaction with text input.
    
    Args:
        text: User input text
    """
    if not voice_manager:
        logger.error("Voice Manager not initialized")
        return
    
    try:
        # Get AI response
        response_text = await voice_manager.process_text(text)
        
        # Speak the response
        await voice_manager.speak(response_text)
        
    except Exception as e:
        logger.error(f"Error in demo interaction: {e}")
        await event_bus.emit(EventTypes.SYSTEM_ERROR, {
            "source": "main",
            "msg": f"Demo interaction error: {e}"
        })

async def play_background_music(music_file: str) -> None:
    """Play background music file.
    
    Args:
        music_file: Path to music file
    """
    if not music_manager:
        logger.error("Music Manager not initialized")
        return
    
    # Check if file exists
    if not os.path.exists(music_file):
        logger.error(f"Music file not found: {music_file}")
        return
    
    # Play the file
    success = music_manager.play_file(music_file)
    
    if success:
        logger.info(f"Playing background music: {music_file}")
    else:
        logger.error(f"Failed to play background music: {music_file}")

async def run_demo_mode() -> None:
    """Run demo mode with predefined interactions."""
    logger.info("Starting demo mode...")
    
    # Start background music if available
    if music_manager and music_manager.vlc_available:
        music_dir = Path("audio/bgmusic")
        if music_dir.exists() and music_dir.is_dir():
            music_files = list(music_dir.glob("*.mp3"))
            if music_files:
                await play_background_music(str(music_files[0]))
            else:
                logger.warning("No music files found in audio/bgmusic directory")
    
    # Demo interactions
    demo_phrases = [
        "Hello! Who are you?",
        "Tell me a short Star Wars joke",
        "What's your favorite music genre?"
    ]
    
    for phrase in demo_phrases:
        logger.info(f"Demo phrase: '{phrase}'")
        await process_demo_interaction(phrase)
        await asyncio.sleep(2)  # Pause between interactions
    
    logger.info("Demo mode completed")

async def interactive_cli_mode() -> None:
    """Run interactive CLI mode for testing."""
    logger.info("Starting interactive CLI mode")
    logger.info("Type 'exit' or 'quit' to exit, 'help' for commands")
    
    while not shutdown_event.is_set():
        try:
            # Non-blocking input
            line = await asyncio.get_event_loop().run_in_executor(
                None, lambda: input("\nR3X> ")
            )
            
            line = line.strip()
            if not line:
                continue
            
            if line.lower() in ("exit", "quit"):
                logger.info("Exiting interactive mode")
                break
                
            elif line.lower() == "help":
                print("\nAvailable commands:")
                print("  speak <text>   - Generate and speak response to text")
                print("  music <file>   - Play background music file")
                print("  stop           - Stop music playback")
                print("  duck           - Duck music volume")
                print("  restore        - Restore music volume")
                print("  exit, quit     - Exit program")
                print("  help           - Show this help message")
                
            elif line.lower().startswith("speak "):
                text = line[6:].strip()
                if text:
                    await process_demo_interaction(text)
                    
            elif line.lower().startswith("music "):
                file_path = line[6:].strip()
                if file_path:
                    await play_background_music(file_path)
                    
            elif line.lower() == "stop":
                if music_manager:
                    music_manager.stop_playback()
                    
            elif line.lower() == "duck":
                if music_manager:
                    music_manager.duck_volume()
                    
            elif line.lower() == "restore":
                if music_manager:
                    music_manager.restore_volume()
                    
            else:
                # Treat as text input for R3X
                await process_demo_interaction(line)
                
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in interactive mode: {e}")

async def main() -> int:
    """Main application entry point.
    
    Returns:
        int: Exit code (0 for success, non-zero for error)
    """
    global event_bus
    
    try:
        # Parse command line arguments
        parser = argparse.ArgumentParser(description="DJ R3X Lights & Voice MVP")
        parser.add_argument("--test", action="store_true", help="Run in test mode without API calls")
        args = parser.parse_args()
        
        # Set up event bus
        event_bus = EventBus()
        
        # Set up signal handlers for clean shutdown
        # Register SIGINT and SIGTERM handlers
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, signal_handler)
        
        # Load environment variables
        env = load_environment()
        
        # Initialize components
        if not await initialize_components(env, test_mode=args.test):
            logger.error("Failed to initialize components")
            return 1
        
        # Set up system startup/shutdown event handlers
        event_bus.on(EventTypes.SYSTEM_SHUTDOWN, lambda _: shutdown_event.set())
        
        # Start voice interaction loop - this runs in the background
        # but will only actively listen when in INTERACTIVE mode
        # NOTE: Task creation removed - now handled by VoiceManager's activate_interactive_mode/deactivate_interactive_mode
        # voice_loop_task = asyncio.create_task(voice_manager.start_interaction_loop())
        
        # Wait for shutdown signal
        await shutdown_event.wait()
        
        # Cancel all tasks
        logger.info("Shutting down...")
        # voice_loop_task.cancel() # No longer needed - handled by cleanup_components()
        
        # Clean up resources
        await cleanup_components()
        
        return 0
    
    except Exception as e:
        logger.error(f"Error in main: {e}")
        return 1

if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        logger.info("Application terminated by user")
    except Exception as e:
        logger.error(f"Unhandled exception: {e}")
        sys.exit(1) 