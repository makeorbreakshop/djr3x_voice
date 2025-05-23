import asyncio
import logging
import os
import tempfile
import threading
import queue
from enum import Enum
from typing import Dict, Optional, Union, List, Any

import httpx
from pydantic import BaseModel, ValidationError, Field
from pyee.asyncio import AsyncIOEventEmitter

# Add these imports for streaming support
from elevenlabs import stream as elevenlabs_stream
from elevenlabs.client import ElevenLabs

from cantina_os.base_service import BaseService
from cantina_os.event_payloads import (
    BaseEventPayload,
    SpeechGenerationRequestPayload,
    SpeechGenerationCompletePayload,
    ServiceStatus,
    LogLevel,
    LLMResponsePayload
)
from cantina_os.event_topics import EventTopics


class SpeechPlaybackMethod(str, Enum):
    """Enum for different methods of playing back audio."""
    SOUNDDEVICE = "sounddevice"
    SYSTEM = "system"
    STREAMING = "streaming"  # Add new streaming option


class ElevenLabsConfig(BaseModel):
    """Configuration model for ElevenLabs service."""
    api_key: str = Field(..., description="ElevenLabs API key")
    voice_id: str = Field("P9l1opNa5pWou2X5MwfB", description="Voice ID for DJ R3X")
    model_id: str = Field("eleven_turbo_v2", description="Model ID") # eleven_turbo_v2 or eleven_flash_v2_5
    stability: float = Field(0.60, description="Voice stability (0.0-1.0)")
    similarity_boost: float = Field(0.85, description="Voice similarity boost (0.0-1.0)")
    speed: float = Field(1.2, description="Speech speed multiplier (0.7-1.2)")
    playback_method: SpeechPlaybackMethod = Field(SpeechPlaybackMethod.STREAMING, description="Audio playback method")
    enable_audio_normalization: bool = Field(True, description="Whether to normalize audio")


class ElevenLabsService(BaseService):
    """Service for generating speech using ElevenLabs API and playing it back."""

    def __init__(
        self,
        event_bus: AsyncIOEventEmitter,
        config: Optional[Dict[str, Any]] = None,
        name: str = "elevenlabs_service",
        logger: Optional[logging.Logger] = None
    ):
        """
        Initialize the ElevenLabs service.
        
        Args:
            event_bus: The event bus for inter-service communication
            config: Configuration dictionary
            name: Service name
            logger: Optional custom logger
        """
        super().__init__(name, event_bus, logger)
        
        # Process configuration
        config_dict = config or {}
        
        # API key from config or environment
        api_key = config_dict.get("ELEVENLABS_API_KEY") or os.environ.get("ELEVENLABS_API_KEY")
        if not api_key:
            raise ValueError("ElevenLabs API key is required. Set ELEVENLABS_API_KEY environment variable or pass in config.")
        
        # Get speed from config with clamping to ElevenLabs allowed range
        config_speed = config_dict.get("SPEED", 1.2)
        # Clamp speed to valid range (0.7-1.2)
        clamped_speed = min(max(config_speed, 0.7), 1.2)
        if config_speed != clamped_speed:
            self.logger.warning(f"Speed value {config_speed} out of range (0.7-1.2), clamped to {clamped_speed}")
        
        # Force streaming playback method regardless of config
        # This overrides any settings from the environment or config files
        playback_method = SpeechPlaybackMethod.STREAMING
        self.logger.info(f"Using streaming playback method for ElevenLabs: {playback_method}")
        
        # Create Pydantic config model
        self._config = ElevenLabsConfig(
            api_key=api_key,
            voice_id=config_dict.get("VOICE_ID", "P9l1opNa5pWou2X5MwfB"),
            model_id=config_dict.get("MODEL_ID", "eleven_turbo_v2"),
            stability=config_dict.get("STABILITY", 0.60),
            similarity_boost=config_dict.get("SIMILARITY_BOOST", 0.85),
            speed=clamped_speed,  # Use clamped speed value
            playback_method=playback_method,  # Force streaming playback
            enable_audio_normalization=config_dict.get("ENABLE_AUDIO_NORMALIZATION", True)
        )
        
        # Runtime variables
        self._client = None
        self._current_playback_task = None
        self._temp_dir = None
        self._playback_devices = {}
        self._tasks = []  # Track tasks for proper cleanup
        
        # New streaming-related variables
        self._speech_request_queue = queue.Queue()
        self._audio_thread = None
        self._stop_event = threading.Event()
        self._event_loop = None

        # Buffer for accumulating LLM responses before sending to TTS
        self._llm_response_buffer: str = ""
        self._llm_response_buffer_conversation_id: Optional[str] = None
        self._sentence_terminators = (".", "!", "?", "\n")
        
        # Flag to wait for full response - set to False for true streaming
        self._wait_for_complete_response = True
        
        # Buffering config
        self._min_buffer_size = 20  # Minimum characters before considering a flush
        self._preferred_chunk_size = 100  # Target size for each TTS chunk
        self._max_buffer_size = 200  # Maximum buffer size before forced flush
        
        # Track processed text to avoid duplicates
        self._processed_text_chunks: Dict[str, List[str]] = {}
    
    async def _start(self) -> None:
        """Start the service following architecture standards."""
        self.logger.info(f"Starting ElevenLabsService with playback method: {self._config.playback_method}")
        
        try:
            # Initialize HTTP client
            self._client = httpx.AsyncClient(
                base_url="https://api.elevenlabs.io/v1",
                headers={"xi-api-key": self._config.api_key},
                timeout=30.0
            )
            
            # Create temp directory for audio files
            self._temp_dir = tempfile.TemporaryDirectory()
            
            # Initialize playback devices if using sounddevice
            if self._config.playback_method in [SpeechPlaybackMethod.SOUNDDEVICE, SpeechPlaybackMethod.STREAMING]:
                try:
                    import sounddevice as sd
                    import soundfile as sf
                    self._playback_devices["sounddevice"] = True
                    self.logger.info("SoundDevice available for audio playback")
                except ImportError:
                    self.logger.warning("SoundDevice not available, falling back to system playback")
                    if self._config.playback_method == SpeechPlaybackMethod.STREAMING:
                        self.logger.warning("Cannot use streaming without sounddevice. Falling back to system playback.")
                    self._config.playback_method = SpeechPlaybackMethod.SYSTEM
            
            # Store event loop reference for thread communication
            self._event_loop = asyncio.get_running_loop()
            
            # Start audio streaming thread if using streaming playback
            if self._config.playback_method == SpeechPlaybackMethod.STREAMING:
                self.logger.info("Preparing to start audio streaming worker thread")
                # Check if elevenlabs library is available
                try:
                    import elevenlabs
                    self.logger.info(f"ElevenLabs SDK available, version: {elevenlabs.__version__}")
                except ImportError:
                    self.logger.error("ElevenLabs SDK not available, cannot use streaming mode")
                    self._config.playback_method = SpeechPlaybackMethod.SOUNDDEVICE
                    self.logger.info(f"Falling back to non-streaming mode: {self._config.playback_method}")
                else:
                    # Initialize thread if SDK is available
                    self._stop_event.clear()
                    self._audio_thread = threading.Thread(
                        target=self._audio_worker_loop,
                        daemon=True,
                        name="ElevenLabsAudioThread"
                    )
                    self._audio_thread.start()
                    self.logger.info("Started audio streaming worker thread")
            
            # Log final playback method
            self.logger.info(f"ElevenLabsService final playback method: {self._config.playback_method}")
            
            # Set up event subscriptions
            await self._setup_subscriptions()
            
            # Emit running status
            await self._emit_status(ServiceStatus.RUNNING, "Service started successfully")
            self.logger.info(f"ElevenLabsService started with voice ID: {self._config.voice_id}, model: {self._config.model_id}")
            
        except Exception as e:
            error_msg = f"Error starting ElevenLabsService: {str(e)}"
            self.logger.error(error_msg)
            await self._emit_status(ServiceStatus.ERROR, error_msg, LogLevel.ERROR)
            raise
    
    async def _setup_subscriptions(self) -> None:
        """Set up event subscriptions following architecture standards."""
        self.logger.info("Setting up ElevenLabsService event subscriptions")
        
        # Subscribe to speech generation requests
        speech_gen_task = asyncio.create_task(
            self.subscribe(EventTopics.SPEECH_GENERATION_REQUEST, self._handle_speech_generation_request)
        )
        self._tasks.append(speech_gen_task)
        
        # Subscribe to LLM responses to convert text to speech
        llm_response_task = asyncio.create_task(
            self.subscribe(EventTopics.LLM_RESPONSE, self._handle_llm_response)
        )
        self._tasks.append(llm_response_task)
        
        self.logger.info("ElevenLabsService event subscriptions complete")
    
    async def _cleanup(self) -> None:
        """Stop the service and clean up resources."""
        self.logger.info("Stopping ElevenLabsService")
        
        # Signal audio thread to stop
        if self._audio_thread and self._audio_thread.is_alive():
            self._stop_event.set()
            self._speech_request_queue.put(None)  # Sentinel to unblock queue
            self._audio_thread.join(timeout=5.0)
            self.logger.info("Audio worker thread stopped")
        
        # Cancel any ongoing playback
        if self._current_playback_task and not self._current_playback_task.done():
            self._current_playback_task.cancel()
            try:
                await self._current_playback_task
            except asyncio.CancelledError:
                pass
        
        # Cancel all subscription tasks
        for task in self._tasks:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._tasks.clear()
        
        # Close HTTP client
        if self._client:
            await self._client.aclose()
            self._client = None
        
        # Clean up temp directory
        if self._temp_dir:
            self._temp_dir.cleanup()
            self._temp_dir = None
        
        await self._emit_status(ServiceStatus.STOPPED, "Service stopped successfully")
    
    def _audio_worker_loop(self):
        """Dedicated thread for streaming audio from ElevenLabs and playing it."""
        self.logger.info("Audio worker thread started")
        
        try:
            # Initialize ElevenLabs client for this thread
            self.logger.info("Initializing ElevenLabs client in audio thread")
            eleven_client = ElevenLabs(api_key=self._config.api_key)
            self.logger.info("ElevenLabs client initialized successfully")
            
            while not self._stop_event.is_set():
                try:
                    # Get request with timeout to allow checking stop_event regularly
                    self.logger.debug("Audio thread waiting for requests...")
                    request = self._speech_request_queue.get(timeout=0.5)
                    
                    # Check for sentinel (None) indicating shutdown
                    if request is None:
                        self.logger.info("Received shutdown signal in audio thread")
                        break
                    
                    # Extract request parameters
                    text = request["text"]
                    conversation_id = request["conversation_id"]
                    voice_id = request["voice_id"]
                    model_id = request["model_id"]
                    stability = request["stability"]
                    similarity_boost = request["similarity_boost"]
                    speed = request["speed"]
                    
                    # Clamp speed to valid range (0.7-1.2)
                    speed = min(max(speed, 0.7), 1.2)
                    
                    self.logger.info(f"Audio thread processing speech request: {len(text)} chars with speed {speed}")
                    self.logger.debug(f"Speech text content (first 100 chars): '{text[:100]}...'")
                    
                    # Skip empty text to avoid errors
                    if not text or not text.strip():
                        self.logger.warning("Received empty text for TTS. Skipping synthesis.")
                        
                        # Emit completion event for empty text
                        async def emit_empty_complete():
                            payload = SpeechGenerationCompletePayload(
                                conversation_id=conversation_id,
                                text=text,
                                audio_length_seconds=0.0,
                                success=True
                            )
                            await self.emit(EventTopics.SPEECH_GENERATION_COMPLETE, payload.model_dump())
                        asyncio.run_coroutine_threadsafe(emit_empty_complete(), self._event_loop)
                        
                        # Mark task as done
                        self._speech_request_queue.task_done()
                        continue
                    
                    # Emit event that we're starting audio generation
                    async def emit_started():
                        await self.emit(EventTopics.SPEECH_GENERATION_STARTED, {
                            "conversation_id": conversation_id,
                            "text": text,
                        })
                    asyncio.run_coroutine_threadsafe(emit_started(), self._event_loop)
                    
                    # Get audio stream from ElevenLabs
                    voice_settings = {
                        "stability": stability,
                        "similarity_boost": similarity_boost,
                        "style": 0.25,
                        "use_speaker_boost": True,
                        "speed": speed
                    }
                    
                    try:
                        # Get a streaming response from ElevenLabs
                        self.logger.info(f"Starting streaming TTS with elevenlabs SDK for text: {text[:50]}...")
                        self.logger.info(f"Request details - Model: {model_id}, Voice: {voice_id}, Speed: {speed}")
                        
                        audio_stream = eleven_client.text_to_speech.convert_as_stream(
                            text=text,
                            voice_id=voice_id,
                            model_id=model_id,
                            voice_settings=voice_settings
                        )
                        
                        # Use the ElevenLabs stream utility to play the audio
                        # This blocks in the audio thread until playback is complete
                        self.logger.info("Starting audio playback with elevenlabs.stream")
                        
                        try:
                            from elevenlabs import stream as elevenlabs_stream
                            elevenlabs_stream(audio_stream)
                            self.logger.info("Audio streaming complete")
                        except Exception as stream_error:
                            self.logger.error(f"Error in elevenlabs.stream: {stream_error}")
                            raise
                        
                        # Emit completion event
                        async def emit_complete():
                            payload = SpeechGenerationCompletePayload(
                                conversation_id=conversation_id,
                                text=text,
                                audio_length_seconds=0.0,  # Hard to calculate exact length
                                success=True
                            )
                            await self.emit(EventTopics.SPEECH_GENERATION_COMPLETE, payload.model_dump())
                        asyncio.run_coroutine_threadsafe(emit_complete(), self._event_loop)
                        
                    except Exception as e:
                        self.logger.error(f"Error in audio thread streaming: {e}")
                        # Emit error event
                        async def emit_error():
                            payload = SpeechGenerationCompletePayload(
                                conversation_id=conversation_id,
                                text=text,
                                audio_length_seconds=0.0,
                                success=False,
                                error=str(e)
                            )
                            await self.emit(EventTopics.SPEECH_GENERATION_COMPLETE, payload.model_dump())
                        asyncio.run_coroutine_threadsafe(emit_error(), self._event_loop)
                        
                    # Mark task as done
                    self._speech_request_queue.task_done()
                    
                except queue.Empty:
                    # Timeout on queue.get, just continue to check stop_event
                    continue
                    
        except Exception as e:
            # Log any unexpected errors
            self.logger.error(f"Unexpected error in audio worker thread: {e}")
            # Notify main thread of critical error
            async def notify_critical_error():
                await self._emit_status(
                    ServiceStatus.ERROR,
                    f"Critical error in audio thread: {e}",
                    severity=LogLevel.ERROR
                )
            asyncio.run_coroutine_threadsafe(notify_critical_error(), self._event_loop)
        
        self.logger.info("Audio worker thread exiting")
    
    async def _handle_speech_generation_request(self, event_payload: Union[Dict[str, Any], BaseEventPayload]) -> None:
        """
        Handle a request to generate and play speech.
        
        Args:
            event_payload: The payload containing the text to synthesize and options.
        """
        try:
            # Validate and convert payload
            if isinstance(event_payload, dict):
                try:
                    payload = SpeechGenerationRequestPayload.model_validate(event_payload)
                except ValidationError as e:
                    self.logger.error(f"Invalid speech generation request payload: {e}")
                    return
            elif isinstance(event_payload, SpeechGenerationRequestPayload):
                payload = event_payload
            elif isinstance(event_payload, BaseEventPayload):
                try:
                    payload = SpeechGenerationRequestPayload.model_validate(event_payload.model_dump())
                except ValidationError as e:
                    self.logger.error(f"Could not convert base payload to speech request: {e}")
                    return
            else:
                self.logger.error(f"Unsupported payload type: {type(event_payload)}")
                return
            
            # Get speed from payload or use default
            speed = payload.speed if payload.speed is not None else self._config.speed
            
            # Clamp speed to valid range
            speed = min(max(speed, 0.7), 1.2)
            
            self.logger.info(f"Generating speech for text (length: {len(payload.text)}) with speed: {speed}, method: {self._config.playback_method}")
            
            # Use streaming if configured
            if self._config.playback_method == SpeechPlaybackMethod.STREAMING:
                self.logger.info("Using streaming playback method with the ElevenLabs SDK")
                # Create request data for streaming thread
                request_data = {
                    "text": payload.text,
                    "conversation_id": payload.conversation_id,
                    "voice_id": payload.voice_id or self._config.voice_id,
                    "model_id": payload.model_id or self._config.model_id,
                    "stability": payload.stability or self._config.stability,
                    "similarity_boost": payload.similarity_boost or self._config.similarity_boost,
                    "speed": speed
                }
                
                # Send to audio thread for processing
                self._speech_request_queue.put(request_data)
                self.logger.info(f"Sent speech request to streaming audio thread")
                
                # Note: We don't emit the completion event here
                # The audio thread will do that when playback is done
                
            else:
                self.logger.info(f"Using non-streaming playback method: {self._config.playback_method}")
                # Generate the speech using the traditional API method (non-streaming)
                audio_data = await self._generate_speech(
                    text=payload.text,
                    voice_id=payload.voice_id or self._config.voice_id,
                    model_id=payload.model_id or self._config.model_id,
                    stability=payload.stability or self._config.stability,
                    similarity_boost=payload.similarity_boost or self._config.similarity_boost,
                    speed=speed
                )
                
                if not audio_data:
                    error_msg = "Failed to generate speech: No audio data returned"
                    self.logger.error(error_msg)
                    complete_payload = SpeechGenerationCompletePayload(
                        conversation_id=payload.conversation_id,
                        text=payload.text,
                        audio_length_seconds=0.0,
                        success=False,
                        error=error_msg
                    )
                    await self.emit(EventTopics.SPEECH_GENERATION_COMPLETE, complete_payload.model_dump())
                    return
                
                # Save to temp file
                temp_file_path = os.path.join(self._temp_dir.name, f"{payload.conversation_id}.mp3")
                with open(temp_file_path, "wb") as f:
                    f.write(audio_data)
                
                # Play the audio
                self._current_playback_task = asyncio.create_task(
                    self._play_audio(temp_file_path, payload.conversation_id)
                )
                
                # Wait for playback to complete
                await self._current_playback_task
                
                # Emit event that speech generation and playback is complete
                complete_payload = SpeechGenerationCompletePayload(
                    conversation_id=payload.conversation_id,
                    text=payload.text,
                    audio_length_seconds=0.0,  # TODO: Calculate actual length
                    success=True
                )
                await self.emit(EventTopics.SPEECH_GENERATION_COMPLETE, complete_payload.model_dump())
            
        except Exception as e:
            error_msg = f"Error generating or playing speech: {str(e)}"
            self.logger.error(error_msg)
            
            try:
                # Emit event with failure
                error_payload = SpeechGenerationCompletePayload(
                    conversation_id=payload.conversation_id if 'payload' in locals() else "unknown",
                    text=payload.text if 'payload' in locals() else "",
                    audio_length_seconds=0.0,
                    success=False,
                    error=error_msg
                )
                await self.emit(EventTopics.SPEECH_GENERATION_COMPLETE, error_payload.model_dump())
            except Exception as emit_error:
                self.logger.error(f"Error emitting failure response: {emit_error}")
            
            # Emit service status
            await self._emit_status(ServiceStatus.ERROR, error_msg, LogLevel.ERROR)
    
    async def _handle_llm_response(self, payload: Union[Dict[str, Any], LLMResponsePayload]) -> None:
        """Handle LLM response events by converting text to speech."""
        self.logger.info(f"Received LLM_RESPONSE event: {type(payload)}")

        try:
            if isinstance(payload, dict):
                event_data = LLMResponsePayload.model_validate(payload)
            elif isinstance(payload, LLMResponsePayload):
                event_data = payload
            else:
                self.logger.error(f"Invalid payload type for LLM_RESPONSE: {type(payload)}")
                return
        except ValidationError as e:
            self.logger.error(f"Validation error processing LLM_RESPONSE payload: {e}")
            return
        
        text_chunk = event_data.text
        conversation_id = event_data.conversation_id
        is_complete_chunk = event_data.is_complete

        self.logger.info(f"Current playback method: {self._config.playback_method}, chunk: '{text_chunk[:50]}...', final: {is_complete_chunk}")

        if not conversation_id:
            self.logger.error("LLM_RESPONSE event has no conversation_id. Skipping.")
            return

        # If conversation ID has changed, or if it's the first chunk for a new conversation
        if self._llm_response_buffer_conversation_id != conversation_id:
            # Reset buffer for new conversation
            self._llm_response_buffer_conversation_id = conversation_id
            self._llm_response_buffer = ""
            
            # Reset processed chunks for new conversation
            self._processed_text_chunks[conversation_id] = []

        # Accumulate the text chunks
        if text_chunk:
            self._llm_response_buffer = text_chunk if is_complete_chunk else self._llm_response_buffer + text_chunk
            self.logger.debug(f"Buffering response text. Current buffer length: {len(self._llm_response_buffer)}")

        # Only process when we have the complete response
        if is_complete_chunk and self._wait_for_complete_response:
            self.logger.info(f"Complete response received ({len(self._llm_response_buffer)} chars). Generating speech.")
            
            # Perform a duplicate check to ensure this hasn't already been processed
            if conversation_id in self._processed_text_chunks and self._processed_text_chunks[conversation_id] and \
               self._llm_response_buffer in self._processed_text_chunks[conversation_id]:
                self.logger.info("Exact duplicate of already processed text. Skipping TTS generation.")
                return
                
            await self._flush_complete_response(conversation_id, self._llm_response_buffer)
            
            # Clear buffer after processing
            self._llm_response_buffer = ""

    async def _flush_complete_response(self, conversation_id: str, text: str) -> None:
        """Process a complete response by sending to TTS."""
        if not text or not text.strip():
            self.logger.debug("Empty text, nothing to process")
            return
            
        # Track this as processed to avoid duplicates
        if conversation_id not in self._processed_text_chunks:
            self._processed_text_chunks[conversation_id] = []
        self._processed_text_chunks[conversation_id].append(text)
        
        text_to_speak = text.strip()
        self.logger.info(f"Sending complete response to TTS: {len(text_to_speak)} chars, text: '{text_to_speak[:50]}...'")
        
        speech_request_payload = SpeechGenerationRequestPayload(
            text=text_to_speak,
            conversation_id=conversation_id,
            voice_id=self._config.voice_id,
            model_id=self._config.model_id,
            stability=self._config.stability,
            similarity_boost=self._config.similarity_boost,
            speed=self._config.speed
        )

        if self._config.playback_method == SpeechPlaybackMethod.STREAMING:
            if self._audio_thread and self._audio_thread.is_alive():
                # Convert to dict using model_dump() per architecture standards
                request_data = speech_request_payload.model_dump()
                self._speech_request_queue.put(request_data)
                self.logger.info(f"Sent complete response ({len(text_to_speak)} chars) to streaming audio thread.")
            else:
                self.logger.error("Audio worker thread not running, cannot stream response. Falling back.")
                await self._handle_speech_generation_request(speech_request_payload)
        else:
            await self._handle_speech_generation_request(speech_request_payload)
    
    async def _generate_speech(
        self,
        text: str,
        voice_id: str,
        model_id: str,
        stability: float,
        similarity_boost: float,
        speed: float
    ) -> Optional[bytes]:
        """
        Generate speech using the ElevenLabs API.
        
        Args:
            text: The text to synthesize.
            voice_id: The voice ID to use.
            model_id: The model ID to use.
            stability: Voice stability setting.
            similarity_boost: Voice similarity boost setting.
            speed: Speech speed multiplier.
            
        Returns:
            The audio data as bytes or None if generation failed.
        """
        if not self._client:
            self.logger.error("HTTP client not initialized")
            return None
        
        try:
            # Clamp speed to valid range (0.7-1.2)
            speed = min(max(speed, 0.7), 1.2)
            
            # DJ R3X settings - optimized for consistent, energetic output
            payload = {
                "text": text,
                "model_id": model_id,
                "voice_settings": {
                    "stability": stability,
                    "similarity_boost": similarity_boost,
                    "use_speaker_boost": True,  # Ensure consistent energy levels
                    "style": 0.25,  # Add slight style emphasis for DJ personality
                    "speed": speed  # Control speech rate
                }
            }
            
            self.logger.info(f"Sending TTS request to ElevenLabs for text length: {len(text)} with speed {speed}")
            
            response = await self._client.post(
                f"/text-to-speech/{voice_id}",
                json=payload,
                headers={"Accept": "audio/mpeg"}
            )
            
            if response.status_code != 200:
                self.logger.error(f"Error from ElevenLabs API: {response.status_code} - {response.text}")
                return None
            
            self.logger.info(f"Successfully generated speech, received {len(response.content)} bytes")
            return response.content
            
        except httpx.RequestError as e:
            self.logger.error(f"Error connecting to ElevenLabs API: {str(e)}")
            return None
        except Exception as e:
            self.logger.error(f"Unexpected error generating speech: {str(e)}")
            return None
    
    async def _play_audio(self, file_path: str, conversation_id: str) -> None:
        """
        Play the generated audio file.
        
        Args:
            file_path: Path to the audio file.
            conversation_id: The conversation ID.
        """
        if not os.path.exists(file_path):
            self.logger.error(f"Audio file not found: {file_path}")
            return
        
        try:
            self.logger.info(f"Playing audio for conversation {conversation_id} using {self._config.playback_method}")
            
            if self._config.playback_method == SpeechPlaybackMethod.SOUNDDEVICE:
                await self._play_with_sounddevice(file_path)
            else:
                await self._play_with_system_command(file_path)
                
            self.logger.info(f"Audio playback complete for conversation {conversation_id}")
            
            # Clean up audio resources to prevent degradation in subsequent requests
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except OSError:
                    pass
                    
            # Reset sound device if it was used
            if self._config.playback_method == SpeechPlaybackMethod.SOUNDDEVICE:
                try:
                    import sounddevice as sd
                    sd.stop()  # Ensure any lingering playback is stopped
                except Exception:
                    pass
                
        except Exception as e:
            error_msg = f"Error playing audio: {str(e)}"
            self.logger.error(error_msg)
            await self._emit_status(ServiceStatus.ERROR, error_msg, LogLevel.ERROR)
    
    async def _play_with_sounddevice(self, file_path: str) -> None:
        """
        Play audio using sounddevice.
        
        Args:
            file_path: Path to the audio file.
        """
        try:
            import sounddevice as sd
            import soundfile as sf
            
            def _play_sync():
                data, samplerate = sf.read(file_path)
                sd.play(data, samplerate)
                sd.wait()
            
            # Run in a thread pool to avoid blocking
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _play_sync)
            
        except ImportError as e:
            self.logger.error(f"SoundDevice not available: {e}")
            # Fall back to system command
            await self._play_with_system_command(file_path)
        except Exception as e:
            self.logger.error(f"Error playing with sounddevice: {e}")
            raise
    
    async def _play_with_system_command(self, file_path: str) -> None:
        """
        Play audio using system commands (platform dependent).
        
        Args:
            file_path: Path to the audio file.
        """
        import platform
        system = platform.system()
        
        cmd = None
        if system == "Darwin":  # macOS
            cmd = ["afplay", file_path]
        elif system == "Linux":
            cmd = ["aplay", file_path]
        elif system == "Windows":
            cmd = ["powershell", "-c", f"(New-Object Media.SoundPlayer '{file_path}').PlaySync();"]
        else:
            self.logger.error(f"Unsupported platform for system audio playback: {system}")
            return
        
        try:
            self.logger.info(f"Executing system command: {' '.join(cmd)}")
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await proc.communicate()
            
            if proc.returncode != 0:
                self.logger.error(f"Audio playback command failed: {stderr.decode()}")
        except Exception as e:
            self.logger.error(f"Error executing system command: {e}")
            raise 