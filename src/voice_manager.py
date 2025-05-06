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

from src.bus import EventBus, EventTypes
from src.utils.envelope import AudioEnvelope
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
        
        # Initialize keyboard listener if using push-to-talk
        if push_to_talk_mode:
            self.keyboard_listener = keyboard.Listener(
                on_press=self._on_key_press,
                on_release=self._on_key_release
            )
            self.keyboard_listener.start()
    
    def _on_key_press(self, key):
        """Handle key press events for push-to-talk."""
        try:
            if key == keyboard.Key.space and not self.is_recording:
                self.is_recording = True
                self.recording_event.set()
                # Use run_coroutine_threadsafe to safely schedule the coroutine from this thread
                try:
                    future = asyncio.run_coroutine_threadsafe(self._emit_listening_started(), self.main_loop)
                    # Add a callback to handle any exceptions
                    future.add_done_callback(self._handle_future_exceptions)
                except Exception as e:
                    logger.error(f"Error scheduling listening_started event: {e}")
        except AttributeError:
            pass
    
    def _on_key_release(self, key):
        """Handle key release events for push-to-talk."""
        try:
            if key == keyboard.Key.space and self.is_recording:
                self.is_recording = False
                self.recording_event.clear()
                # Use run_coroutine_threadsafe to safely schedule the coroutine from this thread
                try:
                    future = asyncio.run_coroutine_threadsafe(self._emit_listening_stopped(), self.main_loop)
                    # Add a callback to handle any exceptions
                    future.add_done_callback(self._handle_future_exceptions)
                except Exception as e:
                    logger.error(f"Error scheduling listening_stopped event: {e}")
        except AttributeError:
            pass
    
    def _handle_future_exceptions(self, future):
        """Handle exceptions from threadsafe coroutines."""
        if future.cancelled():
            return
        if future.exception():
            logger.error(f"Threadsafe coroutine raised exception: {future.exception()}")
    
    async def _emit_listening_started(self):
        """Emit voice.listening_started event."""
        await self.event_bus.emit(EventTypes.VOICE_LISTENING_STARTED)
    
    async def _emit_listening_stopped(self):
        """Emit voice.listening_stopped event."""
        await self.event_bus.emit(EventTypes.VOICE_LISTENING_STOPPED)
    
    async def capture_audio(self) -> Optional[np.ndarray]:
        """Capture audio from microphone."""
        try:
            with sr.Microphone(sample_rate=16000) as source:  # Set sample rate to 16kHz
                logger.info("Listening for speech...")
                
                if self.push_to_talk_mode:
                    # Wait for spacebar press
                    await self.recording_event.wait()
                    
                    # Record until spacebar release or timeout
                    audio = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: self.recognizer.listen(source, timeout=30.0, phrase_time_limit=30.0)
                    )
                else:
                    # Use VAD to detect speech
                    audio = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: self.recognizer.listen(source)
                    )
                
                # Convert to numpy array - audio is already at 16kHz
                audio_data = np.frombuffer(audio.get_raw_data(), dtype=np.int16)
                return audio_data.astype(np.float32) / 32768.0
                
        except Exception as e:
            logger.error(f"Error capturing audio: {e}")
            return None
    
    async def transcribe_audio(self, audio_data: np.ndarray) -> Optional[str]:
        """Transcribe audio using Whisper."""
        if self.test_mode:
            return "Test mode transcription"
            
        try:
            await self.event_bus.emit(EventTypes.VOICE_PROCESSING_STARTED)
            
            # Use run_in_executor to run the CPU-bound Whisper transcription in a thread pool
            # This ensures we don't block the event loop and avoids potential loop conflicts
            text = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.whisper_manager.transcribe(audio_data, sample_rate=16000)
            )
            
            return text
        except Exception as e:
            logger.error(f"Error transcribing audio: {e}")
            return None
    
    async def process_text(self, text: str) -> str:
        """Process text using OpenAI API."""
        if self.test_mode:
            return "Test mode response"
            
        try:
            response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.openai_client.chat.completions.create(
                    model=self.openai_model,
                    messages=[
                        {"role": "system", "content": self.persona},
                        {"role": "user", "content": text}
                    ],
                    **self.openai_config
                )
            )
            return response.choices[0].message.content
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
        """Start the main voice interaction loop."""
        logger.info("Starting voice interaction loop...")
        
        while True:
            try:
                # Capture audio
                logger.info("Waiting for audio input...")
                audio_data = await self.capture_audio()
                if audio_data is None:
                    logger.warning("Failed to capture audio, retrying...")
                    continue
                
                # Transcribe audio
                logger.info("Transcribing audio...")
                text = await self.transcribe_audio(audio_data)
                if not text:
                    logger.warning("Failed to transcribe audio, retrying...")
                    continue
                
                logger.info(f"Transcribed: {text}")
                
                # Process with OpenAI
                logger.info(f"Processing text with OpenAI ({self.openai_model})...")
                response = await self.process_text(text)
                logger.info(f"Response: {response}")
                
                # Skip TTS in text-only mode
                if self.text_only_mode:
                    logger.info("Skipping speech synthesis (text-only mode)")
                    continue
                
                # Convert to speech
                logger.info("Synthesizing speech...")
                audio_data = await self.synthesize_speech(response)
                if audio_data:
                    logger.info("Speech synthesized, starting playback...")
                    await self.play_audio(audio_data)
                    logger.info("Audio playback completed")
                else:
                    logger.error("Failed to synthesize speech")
                
            except asyncio.CancelledError:
                logger.info("Interaction loop cancelled")
                break
            except Exception as e:
                logger.error(f"Error in interaction loop: {e}", exc_info=True)
                await self.event_bus.emit(EventTypes.SYSTEM_ERROR, {
                    "source": "voice_manager",
                    "msg": f"Interaction error: {e}"
                })
    
    async def stop(self) -> None:
        """Stop the voice manager."""
        if self.push_to_talk_mode:
            self.keyboard_listener.stop()
        
        # Reset state
        self.is_recording = False
        self.recording_event.clear()
        self.envelope_analyzer.reset() 