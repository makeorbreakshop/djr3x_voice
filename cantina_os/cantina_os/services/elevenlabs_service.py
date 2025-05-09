import asyncio
import logging
import os
import tempfile
from enum import Enum
from typing import Dict, Optional, Union

import httpx
from pydantic import ValidationError
from pyee.asyncio import AsyncIOEventEmitter

from cantina_os.base_service import BaseService
from cantina_os.event_payloads import (
    BaseEventPayload,
    SpeechGenerationRequestPayload,
    SpeechGenerationCompletePayload,
    ServiceStatus,
    LogLevel
)
from cantina_os.event_topics import EventTopics


class SpeechPlaybackMethod(str, Enum):
    """Enum for different methods of playing back audio."""
    SOUNDDEVICE = "sounddevice"
    SYSTEM = "system"


class ElevenLabsService(BaseService):
    """Service for generating speech using ElevenLabs API and playing it back."""

    def __init__(
        self,
        event_bus: AsyncIOEventEmitter,
        api_key: Optional[str] = None,
        voice_id: str = "21m00Tcm4TlvDq8ikWAM",  # Default voice ID (Rachel)
        model_id: str = "eleven_turbo_v2",  # Default model
        stability: float = 0.71,
        similarity_boost: float = 0.5,
        playback_method: SpeechPlaybackMethod = SpeechPlaybackMethod.SOUNDDEVICE,
        enable_audio_normalization: bool = True,
        name: str = "elevenlabs_service",
        logger: Optional[logging.Logger] = None
    ):
        """
        Initialize the ElevenLabs service.
        
        Args:
            event_bus: The event bus for inter-service communication
            api_key: ElevenLabs API key. If None, will try to get from environment.
            voice_id: The voice ID to use for synthesis.
            model_id: The model ID to use for synthesis.
            stability: Voice stability setting (0.0-1.0).
            similarity_boost: Voice similarity boost setting (0.0-1.0).
            playback_method: Method to use for playing audio.
            enable_audio_normalization: Whether to normalize audio.
            name: Service name.
            logger: Optional custom logger.
        """
        super().__init__(name, event_bus, logger)
        
        # API configuration
        self.api_key = api_key or os.environ.get("ELEVENLABS_API_KEY")
        if not self.api_key:
            raise ValueError("ElevenLabs API key is required. Set ELEVENLABS_API_KEY environment variable or pass api_key parameter.")
        
        self.voice_id = voice_id
        self.model_id = model_id
        self.stability = stability
        self.similarity_boost = similarity_boost
        self.enable_audio_normalization = enable_audio_normalization
        
        # Playback configuration
        self.playback_method = playback_method
        
        # Runtime variables
        self.client = None
        self.current_playback_task = None
        self.temp_dir = None
        self.playback_devices = {}
        
        # Subscribe to events - make sure to await this
        self._event_subscriptions = []  # Will be processed during initialization
    
    async def _initialize(self) -> None:
        """Initialize HTTP client and prepare for speech synthesis."""
        self.logger.info("Starting ElevenLabsService")
        
        # Set up event subscriptions
        await self.subscribe(EventTopics.SPEECH_GENERATION_REQUEST, self._handle_speech_generation_request)
        
        # Subscribe to LLM response events to convert text to speech
        await self.subscribe(EventTopics.LLM_RESPONSE, self._handle_llm_response)
        
        # Initialize HTTP client
        self.client = httpx.AsyncClient(
            base_url="https://api.elevenlabs.io/v1",
            headers={"xi-api-key": self.api_key},
            timeout=30.0
        )
        
        # Create temp directory for audio files
        self.temp_dir = tempfile.TemporaryDirectory()
        
        # Initialize playback devices if using sounddevice
        if self.playback_method == SpeechPlaybackMethod.SOUNDDEVICE:
            try:
                import sounddevice as sd
                import soundfile as sf
                self.playback_devices["sounddevice"] = True
                self.logger.info("SoundDevice available for audio playback")
            except ImportError:
                self.logger.warning("SoundDevice not available, falling back to system playback")
                self.playback_method = SpeechPlaybackMethod.SYSTEM
        
        self.logger.info(f"ElevenLabsService started with voice ID: {self.voice_id}, model: {self.model_id}")
        await self._emit_status(ServiceStatus.RUNNING, "Service started successfully")
    
    async def _cleanup(self) -> None:
        """Stop the service and clean up resources."""
        self.logger.info("Stopping ElevenLabsService")
        
        # Cancel any ongoing playback
        if self.current_playback_task and not self.current_playback_task.done():
            self.current_playback_task.cancel()
            try:
                await self.current_playback_task
            except asyncio.CancelledError:
                pass
        
        # Close HTTP client
        if self.client:
            await self.client.aclose()
            self.client = None
        
        # Clean up temp directory
        if self.temp_dir:
            self.temp_dir.cleanup()
            self.temp_dir = None
        
        await self._emit_status(ServiceStatus.STOPPED, "Service stopped successfully")
    
    async def _handle_speech_generation_request(self, event_payload: Union[BaseEventPayload, SpeechGenerationRequestPayload]) -> None:
        """
        Handle a request to generate and play speech.
        
        Args:
            event_payload: The payload containing the text to synthesize and options.
        """
        if not isinstance(event_payload, SpeechGenerationRequestPayload):
            try:
                # Try to convert from dict or BaseEventPayload
                event_payload = SpeechGenerationRequestPayload.parse_obj(event_payload)
            except (ValidationError, AttributeError) as e:
                self.logger.error(f"Invalid event payload type: {type(event_payload)}, {str(e)}")
                return
        
        self.logger.info(f"Generating speech for text (length: {len(event_payload.text)})")
        
        try:
            # Generate the speech
            audio_data = await self._generate_speech(
                text=event_payload.text,
                voice_id=event_payload.voice_id or self.voice_id,
                model_id=event_payload.model_id or self.model_id,
                stability=event_payload.stability or self.stability,
                similarity_boost=event_payload.similarity_boost or self.similarity_boost
            )
            
            if not audio_data:
                self.logger.error("Failed to generate speech: No audio data returned")
                await self.emit(
                    EventTopics.SPEECH_GENERATION_COMPLETE,
                    SpeechGenerationCompletePayload(
                        conversation_id=event_payload.conversation_id,
                        text=event_payload.text,
                        audio_length_seconds=0.0,
                        success=False,
                        error="No audio data returned"
                    )
                )
                return
            
            # Save to temp file
            temp_file_path = os.path.join(self.temp_dir.name, f"{event_payload.conversation_id}.mp3")
            with open(temp_file_path, "wb") as f:
                f.write(audio_data)
            
            # Play the audio
            self.current_playback_task = asyncio.create_task(
                self._play_audio(temp_file_path, event_payload.conversation_id)
            )
            
            # Wait for playback to complete
            await self.current_playback_task
            
            # Emit event that speech generation and playback is complete
            complete_payload = SpeechGenerationCompletePayload(
                conversation_id=event_payload.conversation_id,
                text=event_payload.text,
                audio_length_seconds=0.0,  # TODO: Calculate actual length
                success=True
            )
            await self.emit(EventTopics.SPEECH_GENERATION_COMPLETE, complete_payload)
            
        except Exception as e:
            self.logger.error(f"Error generating or playing speech: {str(e)}")
            # Emit event with failure
            error_payload = SpeechGenerationCompletePayload(
                conversation_id=event_payload.conversation_id,
                text=event_payload.text,
                audio_length_seconds=0.0,
                success=False,
                error=str(e)
            )
            await self.emit(EventTopics.SPEECH_GENERATION_COMPLETE, error_payload)
    
    async def _handle_llm_response(self, payload: Dict) -> None:
        """
        Handle a response from the LLM to convert to speech.
        
        Args:
            payload: The LLM response payload
        """
        try:
            # Extract text from payload
            if not payload or "text" not in payload:
                self.logger.warning("Received empty text in LLM_RESPONSE event")
                return
                
            text = payload["text"]
            conversation_id = payload.get("conversation_id", str(asyncio.get_event_loop().time()))
            
            self.logger.info(f"Converting LLM response to speech: {len(text)} chars")
            
            # Create speech generation request
            request = SpeechGenerationRequestPayload(
                text=text,
                conversation_id=conversation_id,
                voice_id=self.voice_id,
                model_id=self.model_id,
                stability=self.stability,
                similarity_boost=self.similarity_boost
            )
            
            # Process the speech generation request
            await self._handle_speech_generation_request(request)
            
        except Exception as e:
            self.logger.error(f"Error handling LLM response: {str(e)}")
            await self._emit_status(
                ServiceStatus.ERROR,
                f"Error handling LLM response: {str(e)}",
                severity=LogLevel.ERROR
            )
    
    async def _generate_speech(
        self,
        text: str,
        voice_id: str,
        model_id: str,
        stability: float,
        similarity_boost: float
    ) -> Optional[bytes]:
        """
        Generate speech using the ElevenLabs API.
        
        Args:
            text: The text to synthesize.
            voice_id: The voice ID to use.
            model_id: The model ID to use.
            stability: Voice stability setting.
            similarity_boost: Voice similarity boost setting.
            
        Returns:
            The audio data as bytes or None if generation failed.
        """
        if not self.client:
            self.logger.error("HTTP client not initialized")
            return None
        
        try:
            payload = {
                "text": text,
                "model_id": model_id,
                "voice_settings": {
                    "stability": stability,
                    "similarity_boost": similarity_boost
                }
            }
            
            response = await self.client.post(
                f"/text-to-speech/{voice_id}",
                json=payload,
                headers={"Accept": "audio/mpeg"}
            )
            
            if response.status_code != 200:
                self.logger.error(f"Error from ElevenLabs API: {response.status_code} - {response.text}")
                return None
            
            return response.content
            
        except httpx.RequestError as e:
            self.logger.error(f"Error connecting to ElevenLabs API: {str(e)}")
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
            if self.playback_method == SpeechPlaybackMethod.SOUNDDEVICE:
                await self._play_with_sounddevice(file_path)
            else:
                await self._play_with_system_command(file_path)
                
            self.logger.info(f"Audio playback complete for conversation {conversation_id}")
            
        except Exception as e:
            self.logger.error(f"Error playing audio: {str(e)}")
    
    async def _play_with_sounddevice(self, file_path: str) -> None:
        """
        Play audio using sounddevice.
        
        Args:
            file_path: Path to the audio file.
        """
        import sounddevice as sd
        import soundfile as sf
        
        def _play_sync():
            data, samplerate = sf.read(file_path)
            sd.play(data, samplerate)
            sd.wait()
        
        # Run in a thread pool to avoid blocking
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _play_sync)
    
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
        
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await proc.communicate()
        
        if proc.returncode != 0:
            self.logger.error(f"Audio playback command failed: {stderr.decode()}") 