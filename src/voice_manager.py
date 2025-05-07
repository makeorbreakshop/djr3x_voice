"""
Voice Manager for DJ R3X.
Handles audio input/output, speech recognition, text processing, and speech synthesis.
"""

import os
import asyncio
import logging
import numpy as np
import sounddevice as sd
import speech_recognition as sr
from typing import Optional, Dict, Any
from pynput import keyboard
from openai import OpenAI
import requests
import io
from pydub import AudioSegment

from src.bus import EventBus, EventTypes, SystemMode
from src.utils.envelope import AudioEnvelope
from src.holocron.holocron_manager import HolocronManager
from whisper_manager import WhisperManager
from audio_processor import process_and_play_audio

# Configure logging
logger = logging.getLogger(__name__)

class VoiceManager:
    """Manages voice interaction including STT, LLM processing, and TTS."""
    
    def __init__(self,
                 event_bus: EventBus,
                 openai_key: str,
                 openai_model: str,
                 elevenlabs_key: str,
                 elevenlabs_voice_id: str,
                 persona: str,
                 voice_config: Dict[str, Any],
                 openai_config: Dict[str, Any],
                 text_only_mode: bool = False,
                 push_to_talk_mode: bool = True,
                 disable_audio_processing: bool = False,
                 sample_rate: int = 44100,
                 channels: int = 1,
                 test_mode: bool = False,
                 loop: Optional[asyncio.AbstractEventLoop] = None):
        """Initialize the voice manager.
        
        Args:
            event_bus: Event bus instance
            openai_key: OpenAI API key
            openai_model: OpenAI model to use
            elevenlabs_key: ElevenLabs API key
            elevenlabs_voice_id: ElevenLabs voice ID
            persona: System prompt for DJ R3X personality
            voice_config: ElevenLabs voice configuration
            openai_config: OpenAI API configuration
            text_only_mode: Skip audio output if True
            push_to_talk_mode: Use push-to-talk instead of VAD
            disable_audio_processing: Skip audio effects if True
            sample_rate: Audio sample rate
            channels: Number of audio channels
            test_mode: Run in test mode without API calls
            loop: Explicit event loop to use for thread-safe operations
        """
        self.event_bus = event_bus
        self.text_only_mode = text_only_mode
        self.push_to_talk_mode = push_to_talk_mode
        self.disable_audio_processing = disable_audio_processing
        self.sample_rate = sample_rate
        self.channels = channels
        self.test_mode = test_mode
        
        # Store configuration
        self.voice_config = voice_config
        self.openai_config = openai_config
        self.openai_model = openai_model
        self.persona = persona
        
        # Store reference to the main event loop for thread-safe operations
        self.main_loop = loop or asyncio.get_event_loop()
        
        # Initialize clients if not in test mode
        if not test_mode:
            self.openai_client = OpenAI(api_key=openai_key)
            self.elevenlabs_key = elevenlabs_key
            self.elevenlabs_voice_id = elevenlabs_voice_id
            self.whisper_manager = WhisperManager(model_size="base")
            self.whisper_manager.load_model()
            
        # Initialize speech recognition
        self.recognizer = sr.Recognizer()
        
        # Initialize audio envelope analyzer
        self.envelope_analyzer = AudioEnvelope()
        
        # Push-to-talk state
        self.is_recording = False
        self.recording_event = asyncio.Event()
        
        # System mode flag - starts as inactive
        self.interactive_mode_enabled = False
        
        # Task that runs the interaction loop - will be created when interactive mode is activated
        self.interaction_task = None
        
        # Initialize the Holocron Manager for Star Wars knowledge
        self.holocron_manager = HolocronManager(event_bus)
        
        # Initialize keyboard listener if using push-to-talk
        # But don't actually start it until interactive mode is activated
        if push_to_talk_mode:
            self.keyboard_listener = keyboard.Listener(
                on_press=self._on_key_press,
                on_release=self._on_key_release
            )
            # Register for system mode changes
            self.event_bus.on(EventTypes.SYSTEM_MODE_CHANGED, self._handle_mode_change)
    
    async def _handle_mode_change(self, data: Dict[str, Any]) -> None:
        """Handle system mode changes.
        
        Args:
            data: Event data containing old_mode and new_mode
        """
        if "new_mode" not in data:
            return
            
        new_mode = data["new_mode"]
        
        if new_mode == SystemMode.INTERACTIVE.value:
            # Enable interactive mode
            await self.activate_interactive_mode()
        else:
            # Disable interactive mode for all other modes
            await self.deactivate_interactive_mode()
    
    async def activate_interactive_mode(self) -> None:
        """Enable interactive voice mode."""
        if self.interactive_mode_enabled:
            return
            
        logger.info("Activating voice interactive mode")
        self.interactive_mode_enabled = True
        
        # Start keyboard listener in push-to-talk mode
        if self.push_to_talk_mode:
            # Ensure any previous listener is stopped before creating a new one
            if hasattr(self, 'keyboard_listener') and self.keyboard_listener.is_alive():
                self.keyboard_listener.stop()
                logger.info("Stopped existing keyboard listener")
                
            # Create a fresh keyboard listener
            self.keyboard_listener = keyboard.Listener(
                on_press=self._on_key_press,
                on_release=self._on_key_release
            )
            self.keyboard_listener.start()
            logger.info("Push-to-talk listener activated")
            
        # Start the interaction loop as a proper asyncio task, if not already running
        if self.interaction_task is None or self.interaction_task.done():
            self.interaction_task = asyncio.create_task(self.start_interaction_loop())
            logger.info("Started voice interaction loop task")
    
    async def deactivate_interactive_mode(self) -> None:
        """Disable interactive voice mode."""
        if not self.interactive_mode_enabled:
            return
            
        logger.info("Deactivating voice interactive mode")
        self.interactive_mode_enabled = False
        
        # Stop any active recording
        if self.is_recording:
            self.is_recording = False
            self.recording_event.clear()
            await self._emit_listening_stopped()
            
        # Stop the keyboard listener to prevent further key events
        if self.push_to_talk_mode and hasattr(self, 'keyboard_listener') and self.keyboard_listener.is_alive():
            self.keyboard_listener.stop()
            logger.info("Push-to-talk listener stopped")
            
        # Cancel the interaction loop task if it's running
        if self.interaction_task and not self.interaction_task.done():
            logger.info("Cancelling voice interaction loop task")
            self.interaction_task.cancel()
            try:
                # Wait briefly for the task to acknowledge cancellation
                await asyncio.wait_for(asyncio.shield(self.interaction_task), timeout=1.0)
                logger.info("Voice interaction loop task cancelled successfully")
            except (asyncio.TimeoutError, asyncio.CancelledError):
                # This is expected - either timeout or the task was cancelled
                logger.info("Voice interaction loop task cancellation completed")
            except Exception as e:
                logger.error(f"Error during interaction task cancellation: {e}")
            
            # Clear the task reference
            self.interaction_task = None
    
    def _on_key_press(self, key):
        """Handle key press events for push-to-talk."""
        # Skip key press events if not in interactive mode
        if not self.interactive_mode_enabled:
            return
            
        # Check if it's the spacebar
        if key == keyboard.Key.space:
            # Only start recording if not already recording
            if not self.is_recording:
                self.is_recording = True
                
                # Use run_coroutine_threadsafe to safely call coroutines from this thread
                asyncio.run_coroutine_threadsafe(
                    self._emit_listening_started(),
                    self.main_loop
                ).add_done_callback(self._handle_future_exceptions)
                
                # Set the recording event (this is used by the capture_audio method)
                asyncio.run_coroutine_threadsafe(
                    self.recording_event.set(),
                    self.main_loop
                )
                logger.debug("Push-to-talk: Started recording")
    
    def _on_key_release(self, key):
        """Handle key release events for push-to-talk."""
        # Skip key release events if not in interactive mode
        if not self.interactive_mode_enabled:
            return
            
        # Check if it's the spacebar and we're currently recording
        if key == keyboard.Key.space and self.is_recording:
            self.is_recording = False
            
            # Use run_coroutine_threadsafe to safely call coroutines from this thread
            asyncio.run_coroutine_threadsafe(
                self._emit_listening_stopped(),
                self.main_loop
            ).add_done_callback(self._handle_future_exceptions)
            
            # Clear the recording event
            asyncio.run_coroutine_threadsafe(
                self.recording_event.clear(),
                self.main_loop
            )
            logger.debug("Push-to-talk: Stopped recording")
            
        # Handle escape key for quitting (for convenience during development)
        elif key == keyboard.Key.esc:
            logger.debug("Escape key pressed - this has no effect in the final app")
    
    def _handle_future_exceptions(self, future):
        """Handle exceptions from futures created by run_coroutine_threadsafe."""
        try:
            future.result()
        except Exception as e:
            logger.error(f"Error in async operation from key event: {e}")
    
    async def _emit_listening_started(self):
        """Emit event when R3X starts listening."""
        await self.event_bus.emit(EventTypes.VOICE_LISTENING_STARTED)
    
    async def _emit_listening_stopped(self):
        """Emit event when R3X stops listening."""
        await self.event_bus.emit(EventTypes.VOICE_LISTENING_STOPPED)
    
    async def capture_audio(self) -> Optional[np.ndarray]:
        """Capture audio from microphone."""
        if self.test_mode:
            await asyncio.sleep(1)
            return np.zeros(8000)
            
        try:
            if self.push_to_talk_mode:
                # Wait for the recording event to be set (when space bar is pressed)
                await self.recording_event.wait()
                
                # Create a recognizer and microphone instance
                microphone = sr.Microphone()
                
                # Start recording
                with microphone as source:
                    logger.info("Listening with push-to-talk...")
                    self.recognizer.adjust_for_ambient_noise(source)
                    
                    # Record until the recording event is cleared (space bar released)
                    while self.recording_event.is_set():
                        try:
                            # Listen with a short timeout to allow checking if we should stop
                            audio_data = await asyncio.get_event_loop().run_in_executor(
                                None,
                                lambda: self.recognizer.listen(source, timeout=0.5, phrase_time_limit=10)
                            )
                            return np.frombuffer(audio_data.get_raw_data(), dtype=np.int16)
                        except sr.WaitTimeoutError:
                            # This is expected - just loop around and check recording_event again
                            continue
            else:
                # TODO: Implement VAD-based listening
                logger.error("VAD-based listening not yet implemented")
                return None
                
        except Exception as e:
            logger.error(f"Error capturing audio: {e}")
            return None
    
    async def transcribe_audio(self, audio_data: np.ndarray) -> Optional[str]:
        """Convert audio to text using Whisper."""
        if self.test_mode:
            return "Test input text"
            
        try:
            if audio_data is None or len(audio_data) == 0:
                logger.warning("No audio data to transcribe")
                return None
                
            logger.info("Transcribing audio...")
            
            # Process using WhisperManager
            transcription = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.whisper_manager.transcribe_audio(audio_data.astype(np.float32) / 32768.0)
            )
            
            return transcription
            
        except Exception as e:
            logger.error(f"Error transcribing audio: {e}")
            return None
    
    async def process_text(self, text: str) -> str:
        """Process text using OpenAI API with Holocron knowledge enhancement."""
        if self.test_mode:
            return "Test mode response"
            
        try:
            logger.info(f"Processing text: {text}")
            
            # Enhance the system prompt with Star Wars knowledge if needed
            enhanced_prompt, knowledge_used, modification_desc = await self.holocron_manager.enhance_voice_processing(
                text, self.persona
            )
            
            if knowledge_used:
                logger.info(f"Holocron integration: {modification_desc}")
            
            # Make the API call with the enhanced prompt
            response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.openai_client.chat.completions.create(
                    model=self.openai_model,
                    messages=[
                        {"role": "system", "content": enhanced_prompt},
                        {"role": "user", "content": text}
                    ],
                    **self.openai_config
                )
            )
            
            response_text = response.choices[0].message.content
            
            # Process the response to ensure proper holocron integration
            processed_response = await self.holocron_manager.process_response(
                text, response_text, knowledge_used
            )
            
            return processed_response
            
        except Exception as e:
            logger.error(f"Error processing text: {e}")
            return "I'm having trouble thinking right now. Can you try again?"
    
    async def synthesize_speech(self, text: str) -> Optional[bytes]:
        """Convert text to speech using ElevenLabs API."""
        if self.test_mode or self.text_only_mode:
            logger.info("Skipping speech synthesis (test mode or text-only mode)")
            return None
            
        try:
            logger.info(f"Synthesizing speech with ElevenLabs (text length: {len(text)} chars)")
            url = f"https://api.elevenlabs.io/v1/text-to-speech/{self.elevenlabs_voice_id}"
            
            headers = {
                "Accept": "audio/mpeg",
                "Content-Type": "application/json",
                "xi-api-key": self.elevenlabs_key
            }
            
            # Get the voice config as a dictionary and extract required values
            voice_config_dict = self.voice_config.to_dict()
            logger.debug(f"Voice config: {voice_config_dict}")
            
            data = {
                "text": text,
                "model_id": voice_config_dict["model_id"],
                "voice_settings": voice_config_dict
            }
            
            logger.info(f"Sending request to ElevenLabs API with model: {voice_config_dict['model_id']}")
            response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: requests.post(url, json=data, headers=headers)
            )
            
            if response.status_code == 200:
                audio_size = len(response.content)
                logger.info(f"Speech synthesized successfully ({audio_size} bytes)")
                return response.content
            else:
                logger.error(f"ElevenLabs API error: {response.status_code}, Response: {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"Error synthesizing speech: {e}")
            return None
    
    async def play_audio(self, audio_data: bytes) -> None:
        """Play audio data with optional processing."""
        try:
            logger.info(f"Starting audio playback (data size: {len(audio_data)} bytes)")
            
            # Convert response content to audio segment
            audio_segment = AudioSegment.from_mp3(io.BytesIO(audio_data))
            duration = len(audio_segment) / 1000.0  # Duration in seconds
            logger.info(f"Audio duration: {duration:.2f} seconds")
            
            # Emit speaking started event
            await self.event_bus.emit(
                EventTypes.VOICE_SPEAKING_STARTED,
                {"duration": duration, "emotion": "neutral"}
            )
            
            if self.disable_audio_processing:
                logger.info("Using direct audio playback (processing disabled)")
                
                # Method 1: Use sounddevice (may fail on some macOS configurations)
                try:
                    # Try sounddevice playback first
                    logger.info("Attempting playback via sounddevice...")
                    samples = np.array(audio_segment.get_array_of_samples(), dtype=np.float32) / 32768.0
                    logger.debug(f"Audio samples shape: {samples.shape}")
                    
                    # Convert to stereo if mono
                    if audio_segment.channels == 1:
                        logger.info("Converting mono audio to stereo for playback")
                        stereo = np.column_stack((samples, samples))
                        sd.play(stereo, audio_segment.frame_rate)
                    else:
                        sd.play(samples, audio_segment.frame_rate)
                    
                    logger.info("Audio playback started via sounddevice")
                    sd.wait()  # Wait until playback is finished
                    logger.info("Audio playback via sounddevice completed")
                except Exception as e:
                    logger.error(f"Sounddevice playback failed: {e}", exc_info=True)
                    
                    # Method 2: Fall back to temporary file method if sounddevice fails
                    logger.info("Falling back to file-based playback method")
                    temp_path = "audio/temp_playback.wav"
                    os.makedirs(os.path.dirname(temp_path), exist_ok=True)
                    
                    # Export as WAV for simpler playback
                    audio_segment.export(temp_path, format="wav")
                    logger.info(f"Saved audio to temporary WAV file: {temp_path}")
                    
                    # Use system command for playback
                    logger.info("Playing audio using system command")
                    try:
                        import platform
                        system = platform.system()
                        
                        if system == "Darwin":  # macOS
                            os.system(f"afplay {temp_path}")
                            logger.info("Audio playback via afplay completed")
                        elif system == "Linux":
                            os.system(f"aplay {temp_path}")
                            logger.info("Audio playback via aplay completed")
                        elif system == "Windows":
                            os.system(f"start {temp_path}")
                            logger.info("Audio playback via start command completed")
                        else:
                            logger.error(f"Unsupported platform for audio playback: {system}")
                    except Exception as playback_error:
                        logger.error(f"System audio playback failed: {playback_error}", exc_info=True)
                
                logger.info("Direct audio playback completed")
            else:
                logger.info("Using audio processing pipeline")
                # Save to temporary file for processing
                temp_path = "audio/temp_tts_output.mp3"
                os.makedirs(os.path.dirname(temp_path), exist_ok=True)
                with open(temp_path, "wb") as f:
                    f.write(audio_data)
                
                logger.info(f"Saved audio to temporary file: {temp_path}")
                
                # Process and play audio with effects
                await process_and_play_audio(temp_path)
                logger.info("Audio processing and playback completed")
            
            # Emit speaking finished event
            await self.event_bus.emit(EventTypes.VOICE_SPEAKING_FINISHED)
            
        except Exception as e:
            logger.error(f"Error playing audio: {e}", exc_info=True)
            await self.event_bus.emit(EventTypes.SYSTEM_ERROR, {
                "source": "voice_manager",
                "msg": f"Audio playback error: {e}"
            })
    
    async def start_interaction_loop(self) -> None:
        """Start the voice interaction loop."""
        logger.info("Starting voice interaction loop")
        
        while True:
            try:
                # Exit loop if not in interactive mode
                if not self.interactive_mode_enabled:
                    # Sleep a bit to avoid constant checking
                    await asyncio.sleep(0.5)
                    continue
                    
                # Capture audio from microphone
                audio_data = await self.capture_audio()
                if audio_data is None:
                    continue
                
                # Transcribe to text
                text = await self.transcribe_audio(audio_data)
                if not text:
                    logger.info("No speech detected")
                    continue
                    
                logger.info(f"Recognized: {text}")
                
                # Process with LLM and Holocron knowledge
                response_text = await self.process_text(text)
                if not response_text:
                    logger.warning("Failed to get AI response")
                    continue
                
                # Synthesize speech
                audio_bytes = await self.synthesize_speech(response_text)
                if not audio_bytes:
                    logger.warning("Failed to synthesize speech")
                    continue

                # Play response
                await self.play_audio(audio_bytes)
                
            except asyncio.CancelledError:
                logger.info("Voice interaction loop cancelled")
                break
            except Exception as e:
                logger.error(f"Error in voice interaction loop: {e}", exc_info=True)
                await asyncio.sleep(1)  # Avoid tight error loops
    
    async def stop(self) -> None:
        """Clean up resources and stop the voice manager."""
        logger.info("Stopping voice manager")
        
        # Deactivate interactive mode to stop tasks
        await self.deactivate_interactive_mode()
        
        if self.push_to_talk_mode and hasattr(self, 'keyboard_listener') and self.keyboard_listener.is_alive():
            self.keyboard_listener.stop()
            logger.info("Stopped keyboard listener")
            
        logger.info("Voice manager stopped") 