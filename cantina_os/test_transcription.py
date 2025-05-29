#!/usr/bin/env python3
"""
Simple CLI to test the CantinaOS transcription system.
This script starts the system and enables mic capture to test transcription.
"""
import asyncio
import logging
import os
import signal
import threading
import time
from dotenv import load_dotenv
from pyee.asyncio import AsyncIOEventEmitter
import keyboard

# Add the current directory to python path
import sys
sys.path.append('.')

# Import CantinaOS components
from cantina_os.base_service import BaseService
from cantina_os.event_topics import EventTopics 
from cantina_os.event_payloads import ServiceStatus, LogLevel
from cantina_os.services import MicInputService, DeepgramTranscriptionService, GPTService

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("test_transcription")

class TranscriptionTestRunner:
    """Simple runner to test transcription."""
    
    def __init__(self):
        """Initialize the test runner."""
        self.event_bus = AsyncIOEventEmitter()
        self.services = {}
        self._shutdown_event = asyncio.Event()
        self._is_recording = False
        self._loop = None
        self._load_config()
        
        # Set up handlers for transcription events
        self.event_bus.on(EventTopics.TRANSCRIPTION_INTERIM, self._on_interim_transcript)
        self.event_bus.on(EventTopics.TRANSCRIPTION_FINAL, self._on_final_transcript)
        self.event_bus.on(EventTopics.LLM_PROCESSING_STARTED, self._on_llm_processing_started)
        self.event_bus.on(EventTopics.LLM_PROCESSING_ENDED, self._on_llm_processing_ended)
        self.event_bus.on(EventTopics.LLM_RESPONSE_TEXT, self._on_llm_response)
        self.event_bus.on(EventTopics.TRANSCRIPTION_FINAL, self._on_audio_final_transcript)
        
    def _load_config(self):
        """Load configuration from environment variables."""
        load_dotenv()
        
    def _on_interim_transcript(self, payload):
        """Handle interim transcription events."""
        text = payload.get('text', '')
        confidence = payload.get('confidence', 0.0)
        print(f"INTERIM ({confidence:.2f}): {text}")
        
    def _on_final_transcript(self, payload):
        """Handle final transcription events."""
        text = payload.get('text', '')
        confidence = payload.get('confidence', 0.0)
        print(f"FINAL ({confidence:.2f}): {text}")
        
    def _on_audio_final_transcript(self, payload):
        """Handle audio final transcription events."""
        text = payload.get('text', '')
        confidence = payload.get('confidence', 0.0)
        print(f"AUDIO FINAL ({confidence:.2f}): {text}")
        
    def _on_llm_processing_started(self, payload):
        """Handle LLM processing start events."""
        print("LLM processing started...")
        
    def _on_llm_processing_ended(self, payload):
        """Handle LLM processing end events."""
        print("LLM processing ended...")
        
    def _on_llm_response(self, payload):
        """Handle LLM response text events."""
        text = payload.get('text', '')
        print(f"GPT RESPONSE: {text}")
        
    def _setup_signal_handlers(self):
        """Set up handlers for system signals."""
        for sig in (signal.SIGTERM, signal.SIGINT):
            asyncio.get_event_loop().add_signal_handler(
                sig,
                lambda s=sig: asyncio.create_task(self._handle_shutdown(s))
            )
            
    def _keyboard_listener(self):
        """Listen for keyboard events in a separate thread."""
        print("\nPress and hold SPACE to talk, release to process")
        
        def on_space_press(e):
            if e.name == 'space' and e.event_type == keyboard.KEY_DOWN and not self._is_recording:
                self._is_recording = True
                print("\n[Recording started - speak now]")
                if self._loop:
                    asyncio.run_coroutine_threadsafe(self._start_recording(), self._loop)
            
        def on_space_release(e):
            if e.name == 'space' and e.event_type == keyboard.KEY_UP and self._is_recording:
                self._is_recording = False
                print("[Recording stopped - processing...]")
                if self._loop:
                    asyncio.run_coroutine_threadsafe(self._stop_recording(), self._loop)
        
        keyboard.hook(on_space_press)
        keyboard.hook(on_space_release)
        
        # Keep the thread running until shutdown
        while not self._shutdown_event.is_set():
            time.sleep(0.1)
            
        keyboard.unhook_all()
            
    async def _start_recording(self):
        """Start recording audio."""
        try:
            # Start DeepgramTranscriptionService streaming if not already started
            if hasattr(self.services["transcription"], "start_streaming"):
                await self.services["transcription"].start_streaming()
                
            # Start audio capture
            await self.services["mic_input"].start_capture()
        except Exception as e:
            logger.error(f"Error starting recording: {e}")
            
    async def _stop_recording(self):
        """Stop recording and process the audio."""
        try:
            # Stop audio capture
            await self.services["mic_input"].stop_capture()
            
            # Stop DeepgramTranscriptionService streaming
            if hasattr(self.services["transcription"], "stop_streaming"):
                await self.services["transcription"].stop_streaming()
        except Exception as e:
            logger.error(f"Error stopping recording: {e}")
    
    async def _handle_shutdown(self, sig):
        """Handle system shutdown signals."""
        logger.info(f"Received shutdown signal: {sig.name}")
        self._shutdown_event.set()
        
    async def run(self):
        """Run the transcription test."""
        try:
            # Save the event loop for the keyboard thread to use
            self._loop = asyncio.get_running_loop()
            
            # Set up signal handlers
            self._setup_signal_handlers()
            
            # Initialize services
            config = {
                "DEEPGRAM_API_KEY": os.getenv("DEEPGRAM_API_KEY"),
                "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY"),
                "AUDIO_SAMPLE_RATE": int(os.getenv("AUDIO_SAMPLE_RATE", "16000")),
                "AUDIO_CHANNELS": int(os.getenv("AUDIO_CHANNELS", "1")),
                "GPT_MODEL": os.getenv("GPT_MODEL", "gpt-4o"),
                "SYSTEM_PROMPT": os.getenv("SYSTEM_PROMPT", 
                  "You are DJ R3X, a helpful and enthusiastic Star Wars droid DJ assistant.")
            }
            
            self.services["mic_input"] = MicInputService(self.event_bus, config)
            self.services["transcription"] = DeepgramTranscriptionService(self.event_bus, config)
            self.services["gpt"] = GPTService(self.event_bus, config)
            
            # Start all services
            for service_name, service in self.services.items():
                await service.start()
                logger.info(f"Started service: {service_name}")
                
            print("\n=== Transcription Test Started ===")
            
            # Start keyboard listener in a separate thread
            keyboard_thread = threading.Thread(target=self._keyboard_listener)
            keyboard_thread.daemon = True
            keyboard_thread.start()
            
            # Wait for shutdown signal
            await self._shutdown_event.wait()
            
        except Exception as e:
            logger.error(f"Error running transcription test: {e}")
            raise
            
        finally:
            # Ensure services are shut down
            for service_name, service in self.services.items():
                try:
                    if service.is_started:
                        await service.stop()
                        logger.info(f"Stopped service: {service_name}")
                except Exception as e:
                    logger.error(f"Error stopping service {service_name}: {e}")
            
            logger.info("Transcription test completed")

def main():
    """Entry point for the application."""
    runner = TranscriptionTestRunner()
    
    try:
        asyncio.run(runner.run())
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        raise

if __name__ == "__main__":
    main() 