"""
Deepgram Transcription Service

This service handles real-time speech-to-text conversion using Deepgram's streaming API.
It receives audio chunks from MicInputService, processes them via Deepgram,
and emits transcription events (both interim and final).
"""

import asyncio
import logging
import time
import uuid
from typing import Optional, Dict, Any, List, Set
import json

from deepgram import (
    DeepgramClient,
    DeepgramClientOptions,
    LiveOptions,
    LiveTranscriptionEvents
)

from ..base_service import BaseService
from ..event_topics import EventTopics
from ..event_payloads import (
    BaseEventPayload,
    TranscriptionTextPayload,
    ServiceStatus,
    LogLevel,
    AudioChunkPayload
)

class DeepgramTranscriptionService(BaseService):
    """
    Service for real-time speech-to-text conversion using Deepgram.
    
    Features:
    - Streaming transcription with interim and final results
    - Configurable Deepgram model and language
    - Automatic reconnection on disconnection
    - Support for VAD (Voice Activity Detection)
    - Conversation ID tracking for context
    """
    
    def __init__(
        self,
        event_bus,
        config: Optional[Dict[str, Any]] = None,
        logger: Optional[logging.Logger] = None
    ):
        """Initialize the Deepgram transcription service."""
        super().__init__("deepgram_transcription", event_bus, logger)
        
        # Configuration
        self._config = self._load_config(config or {})
        
        # Deepgram client state
        self._client: Optional[DeepgramClient] = None
        self._connection = None
        self._is_streaming = False
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 3
        self._reconnect_delay = 1.0  # seconds
        
        # Transcription state
        self._current_conversation_id: Optional[str] = None
        self._last_final_timestamp = 0
        self._silence_threshold = 3.0  # seconds
        self._interim_stability_window = 0.3  # seconds
        self._last_interim_time = 0
        
        # Performance monitoring
        self._latency_measurements: List[Dict[str, float]] = []
        
    def _load_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Load configuration from provided dict."""
        # Deepgram API key is required
        if "DEEPGRAM_API_KEY" not in config:
            self.logger.warning("DEEPGRAM_API_KEY not provided, service will fail to initialize")
            
        return {
            "DEEPGRAM_API_KEY": config.get("DEEPGRAM_API_KEY", ""),
            "DEEPGRAM_MODEL": config.get("DEEPGRAM_MODEL", "nova-3"),
            "DEEPGRAM_LANGUAGE": config.get("DEEPGRAM_LANGUAGE", "en-US"),
            "DEEPGRAM_TIER": config.get("DEEPGRAM_TIER", "enhanced"),
            "SAMPLE_RATE": config.get("AUDIO_SAMPLE_RATE", 16000),
            "CHANNELS": config.get("AUDIO_CHANNELS", 1),
            "SILENCE_THRESHOLD": config.get("SILENCE_THRESHOLD", 3.0),
            "MAX_RECONNECT_ATTEMPTS": config.get("MAX_RECONNECT_ATTEMPTS", 3),
            "SMART_FORMAT": config.get("SMART_FORMAT", True)
        }
        
    async def _initialize(self) -> None:
        """Initialize the Deepgram client."""
        try:
            # Create Deepgram client with API key
            api_key = self._config.get("DEEPGRAM_API_KEY")
            if not api_key:
                raise ValueError("DEEPGRAM_API_KEY not found in config")
            
            # Initialize client with options
            options = DeepgramClientOptions(
                options={"keepalive": "true"}
            )
            self._client = DeepgramClient(api_key, options)
            
            # Create websocket connection
            self._connection = self._client.listen.websocket.v("1")
            
            # Set up event handlers
            self._connection.on(LiveTranscriptionEvents.Open, self._on_connection_open)
            self._connection.on(LiveTranscriptionEvents.Close, self._on_connection_close)
            self._connection.on(LiveTranscriptionEvents.Error, self._on_error)
            self._connection.on(LiveTranscriptionEvents.Transcript, self._on_transcript)
            
            # Configure transcription options
            options = LiveOptions(
                model="nova-3",
                smart_format=True,
                language="en-US",
                encoding="linear16",
                channels=1,
                sample_rate=16000,
                interim_results=True,
                utterance_end_ms="1000",
            )
            
            # Start the connection
            self._connection.start(options)
            self.logger.info("Initialized Deepgram client and started connection")
            
        except Exception as e:
            self.logger.error(f"Failed to initialize Deepgram client: {e}")
            raise
            
    async def _cleanup(self) -> None:
        """Clean up Deepgram resources."""
        try:
            await self.stop_streaming()
            
            if self._client:
                # Any cleanup needed for the client
                self._client = None
                
            self.logger.info("Cleaned up Deepgram resources")
            
        except Exception as e:
            self.logger.error(f"Error cleaning up Deepgram resources: {str(e)}")
            
    async def _start(self) -> None:
        """Service-specific startup logic."""
        await self._initialize()
        self._setup_subscriptions()
        self.logger.info("Started DeepgramTranscriptionService")
        
    def _setup_subscriptions(self) -> None:
        """Set up event subscriptions."""
        # Wrap subscription in asyncio.create_task to ensure the coroutine is properly handled
        # This is critical for proper event handling
        asyncio.create_task(self.subscribe(
            EventTopics.AUDIO_RAW_CHUNK,
            self._handle_audio_chunk
        ))
        self.logger.info("Set up subscription for audio chunk events")
        
    async def _handle_audio_chunk(self, payload: AudioChunkPayload) -> None:
        """
        Handle audio chunk events from MicInputService.
        
        Args:
            payload: AudioChunkPayload containing audio data and metadata
        """
        try:
            if not self._is_streaming:
                self.logger.warning("Received audio chunk but streaming is not active")
                return

            # Initialize stats tracking if not already done
            if not hasattr(self, '_chunk_count'):
                self._chunk_count = 0
                self._reception_start_time = time.time()
                self._last_log_time = time.time()
                self.logger.info("Started tracking audio chunk stats")

            # Send audio data to Deepgram
            self._connection.send(payload.samples)
            
            # Update stats
            self._chunk_count += 1
            current_time = time.time()
            
            # Log occasionally to avoid flooding
            if self._chunk_count % 10 == 0 or (current_time - self._last_log_time) > 2:
                chunks_per_second = self._chunk_count / (current_time - self._reception_start_time)
                self.logger.debug(
                    f"Processed {self._chunk_count} chunks "
                    f"({chunks_per_second:.2f} chunks/sec)"
                )
                self._last_log_time = current_time
                
        except Exception as e:
            self.logger.error(f"Error handling audio chunk: {e}")
    
    def _on_connection_open(self, client, event, **kwargs) -> None:
        """Handle websocket connection open."""
        self._is_streaming = True
        self.logger.info("Deepgram connection opened")
        
    def _on_connection_close(self, client, event, **kwargs) -> None:
        """Handle websocket connection close."""
        self._is_streaming = False
        self.logger.info("Deepgram connection closed")
        
    def _on_error(self, client, error, **kwargs) -> None:
        """Handle websocket error."""
        self.logger.error(f"Deepgram error: {error}")
        
    def _on_transcript(self, client, transcript: Dict[str, Any], **kwargs) -> None:
        """Handle transcript received from Deepgram.
        
        Args:
            client: Deepgram client instance
            transcript: Transcript data from Deepgram
            **kwargs: Additional keyword arguments
        """
        try:
            # Extract the transcript text from the response format
            if transcript.get("type") == "Results":
                channel = transcript.get("channel", {})
                alternatives = channel.get("alternatives", [])
                
                if alternatives:
                    text = alternatives[0].get("transcript", "").strip()
                    is_final = transcript.get("is_final", False)
                    
                    if text:
                        self.logger.debug(f"Received transcript: {text} (final: {is_final})")
                        # Emit the transcript event with the correct topic based on whether it's final
                        if is_final:
                            asyncio.create_task(self.emit(
                                EventTopics.TRANSCRIPTION_FINAL,
                                {"text": text, "is_final": True}
                            ))
                        else:
                            asyncio.create_task(self.emit(
                                EventTopics.TRANSCRIPTION_INTERIM,
                                {"text": text, "is_final": False}
                            ))
        except Exception as e:
            self.logger.error(f"Error processing transcript: {e}")
        
    def get_latency_stats(self) -> Dict[str, float]:
        """
        Get statistics about transcription latency.
        
        Returns:
            Dictionary with min, max, avg latency in seconds
        """
        if not self._latency_measurements:
            return {"min": 0, "max": 0, "avg": 0, "count": 0}
            
        latencies = [m["latency"] for m in self._latency_measurements]
        return {
            "min": min(latencies),
            "max": max(latencies),
            "avg": sum(latencies) / len(latencies),
            "count": len(latencies)
        }
        
    def reset_conversation_id(self) -> None:
        """Reset the conversation ID to start a new conversation."""
        self._current_conversation_id = str(uuid.uuid4())
        self.logger.info(f"Reset conversation ID to: {self._current_conversation_id}")
        
    @property
    def current_conversation_id(self) -> Optional[str]:
        """Get the current conversation ID."""
        return self._current_conversation_id 

    async def _stop(self) -> None:
        """Service-specific shutdown logic."""
        if self._connection:
            self._connection.finish()
        self._is_streaming = False
        self.logger.info("Stopped DeepgramTranscriptionService") 