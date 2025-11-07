"""
Deepgram Transcription Service

This service handles real-time speech-to-text conversion using Deepgram's streaming API.
It receives audio chunks from MicInputService, processes them via Deepgram,
and emits transcription events (both interim and final).
"""

"""
SERVICE: DeepgramTranscriptionService
PURPOSE: Real-time speech-to-text conversion using Deepgram streaming API with dedicated thread handling
EVENTS_IN: AUDIO_RAW_CHUNK, VOICE_LISTENING_STARTED, VOICE_LISTENING_STOPPED
EVENTS_OUT: TRANSCRIPTION_FINAL, TRANSCRIPTION_INTERIM, SERVICE_STATUS_UPDATE
KEY_METHODS: start_streaming, stop_streaming, _process_transcripts, _process_single_transcript, _extract_audio_samples
DEPENDENCIES: Deepgram API key, Deepgram SDK, threading for WebSocket I/O, asyncio queues
"""

import asyncio
import logging
import time
import uuid
from typing import Optional, Dict, Any, List, Set
import json
import threading
import queue

from deepgram import (
    DeepgramClient,
    DeepgramClientOptions,
    LiveOptions,
    LiveTranscriptionEvents
)

from ..base_service import BaseService
from ..core.event_topics import EventTopics
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
        
        # Thread-safe queues for cross-thread communication
        self._audio_queue = asyncio.Queue(maxsize=100)  # Audio data from event loop to Deepgram thread
        self._transcript_queue = asyncio.Queue(maxsize=100)  # Transcripts from Deepgram thread to event loop
        self._error_queue = asyncio.Queue(maxsize=100)  # Errors from Deepgram thread to event loop
        
        # Store event loop for thread-safe operations
        self._event_loop = asyncio.get_event_loop()
        
        # Deepgram thread state
        self._deepgram_thread = None
        self._stop_flag = threading.Event()
        self._is_streaming = False
        
        # Processing tasks
        self._audio_processor: Optional[asyncio.Task] = None
        self._transcript_processor: Optional[asyncio.Task] = None
        self._error_processor: Optional[asyncio.Task] = None
        
        # State tracking
        self._current_conversation_id: Optional[str] = None
        self._last_final_timestamp = 0
        self._chunks_processed = 0
        
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
            "MAX_RECONNECT_ATTEMPTS": config.get("MAX_RECONNECT_ATTEMPTS", 5),
            "SMART_FORMAT": config.get("SMART_FORMAT", True),
            "UTTERANCE_END_MS": config.get("UTTERANCE_END_MS", "1000")
        }
        
    def _deepgram_thread_function(self):
        """Dedicated thread for Deepgram WebSocket I/O."""
        try:
            # Initialize Deepgram client in this thread
            options = DeepgramClientOptions(options={"keepalive": "true"})
            client = DeepgramClient(self._config["DEEPGRAM_API_KEY"], options)
            connection = client.listen.websocket.v("1")
            
            # Set up event handlers that queue data for the event loop
            def on_transcript(*args, **kwargs):
                result = kwargs.get('result') if kwargs else args[1] if len(args) > 1 else args[0]
                future = asyncio.run_coroutine_threadsafe(
                    self._transcript_queue.put(result),
                    self._event_loop
                )
                try:
                    future.result(timeout=0.1)
                except Exception as e:
                    self._event_loop.call_soon_threadsafe(
                        lambda: self.logger.error(f"Failed to queue transcript: {e}")
                    )
            
            def on_error(*args, **kwargs):
                error = kwargs.get('error') if kwargs else args[0] if args else "Unknown error"
                future = asyncio.run_coroutine_threadsafe(
                    self._error_queue.put(str(error)),
                    self._event_loop
                )
                try:
                    future.result(timeout=0.1)
                except Exception as e:
                    self._event_loop.call_soon_threadsafe(
                        lambda: self.logger.error(f"Failed to queue error: {e}")
                    )
            
            # Configure and start connection
            options = LiveOptions(
                model=self._config.get("DEEPGRAM_MODEL", "nova-3"),
                smart_format=self._config.get("SMART_FORMAT", True),
                language=self._config.get("DEEPGRAM_LANGUAGE", "en-US"),
                encoding="linear16",
                channels=self._config.get("CHANNELS", 1),
                sample_rate=self._config.get("SAMPLE_RATE", 16000),
                interim_results=True,
                utterance_end_ms=self._config.get("UTTERANCE_END_MS", "1000"),
            )
            
            connection.on(LiveTranscriptionEvents.Transcript, on_transcript)
            connection.on(LiveTranscriptionEvents.Error, on_error)
            connection.start(options)
            
            self._event_loop.call_soon_threadsafe(
                lambda: self.logger.info("Deepgram WebSocket connection established")
            )
            
            # Main thread loop - process audio data from queue
            while not self._stop_flag.is_set():
                try:
                    # Get audio data with timeout
                    audio_data = self._audio_queue.get(timeout=0.1)
                    connection.send(audio_data)
                except queue.Empty:
                    continue
                except Exception as e:
                    self._event_loop.call_soon_threadsafe(
                        lambda: self.logger.error(f"Error in Deepgram thread: {e}")
                    )
                    
        except Exception as e:
            self._event_loop.call_soon_threadsafe(
                lambda: self.logger.error(f"Fatal error in Deepgram thread: {e}")
            )
        finally:
            try:
                if connection:
                    connection.finish()
            except:
                pass
            self._event_loop.call_soon_threadsafe(
                lambda: self.logger.info("Deepgram thread stopped")
            )

    async def _handle_audio_chunk(self, payload) -> None:
        """Handle audio chunk in the event loop context."""
        if not self._is_streaming:
            return
            
        try:
            # Extract and convert audio data (same as before)
            samples = self._extract_audio_samples(payload)
            if not samples:
                return
                
            # Queue audio data for Deepgram thread
            try:
                self._audio_queue.put_nowait(samples)
            except asyncio.QueueFull:
                self.logger.warning("Audio queue full, dropping chunk")
                
        except Exception as e:
            self.logger.error(f"Error handling audio chunk: {e}")

    async def start_streaming(self) -> None:
        """Start streaming transcription."""
        if self._is_streaming:
            return
            
        try:
            # Start Deepgram thread
            self._stop_flag.clear()
            self._deepgram_thread = threading.Thread(
                target=self._deepgram_thread_function,
                name="DeepgramThread"
            )
            self._deepgram_thread.start()
            
            # Start processors in event loop
            self._is_streaming = True
            self._transcript_processor = asyncio.create_task(self._process_transcripts())
            self._error_processor = asyncio.create_task(self._process_errors())
            
            self.logger.info("Started streaming transcription")
            await self._emit_status(ServiceStatus.RUNNING, "Streaming started")
            
        except Exception as e:
            self._is_streaming = False
            error_msg = f"Failed to start streaming: {e}"
            self.logger.error(error_msg)
            await self._emit_status(ServiceStatus.ERROR, error_msg)

    async def stop_streaming(self) -> None:
        """Stop streaming transcription."""
        if not self._is_streaming:
            return
            
        try:
            # Signal thread to stop and wait
            self._stop_flag.set()
            if self._deepgram_thread and self._deepgram_thread.is_alive():
                self._deepgram_thread.join(timeout=5.0)
            
            # Stop processors
            self._is_streaming = False
            for processor in [self._transcript_processor, self._error_processor]:
                if processor:
                    processor.cancel()
                    try:
                        await processor
                    except asyncio.CancelledError:
                        pass
            
            # Clear queues
            for queue in [self._audio_queue, self._transcript_queue, self._error_queue]:
                while not queue.empty():
                    try:
                        queue.get_nowait()
                        queue.task_done()
                    except asyncio.QueueEmpty:
                        break
            
            self.logger.info("Stopped streaming transcription")
            await self._emit_status(ServiceStatus.STOPPED, "Streaming stopped")
            
        except Exception as e:
            self.logger.error(f"Error stopping streaming: {e}")

    async def _process_transcripts(self) -> None:
        """Process transcripts in the event loop context."""
        self.logger.info("Starting transcript processor")
        
        try:
            while self._is_streaming:
                try:
                    # Get result from queue with timeout
                    result = await asyncio.wait_for(
                        self._transcript_queue.get(),
                        timeout=0.1
                    )
                    
                    # Process the transcript
                    await self._process_single_transcript(result)
                    self._transcript_queue.task_done()
                    
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    self.logger.error(f"Error processing transcript: {e}")
                    await asyncio.sleep(0.1)
                    
        except asyncio.CancelledError:
            self.logger.info("Transcript processor cancelled")
            raise
        finally:
            self.logger.info("Transcript processor stopped")

    async def _process_single_transcript(self, result: Any) -> None:
        """Process a single transcript result in the event loop context."""
        try:
            # Extract text and is_final using object or dict access
            text = None
            is_final = False
            
            # Try object-style access first
            if hasattr(result, 'channel') and hasattr(result.channel, 'alternatives'):
                alternatives = result.channel.alternatives
                if alternatives and len(alternatives) > 0:
                    text = alternatives[0].transcript.strip()
                    is_final = getattr(result, 'is_final', False)
            
            # Fall back to dict-style access
            elif isinstance(result, dict):
                channel = result.get('channel', {})
                alternatives = channel.get('alternatives', [])
                if alternatives and 'transcript' in alternatives[0]:
                    text = alternatives[0]['transcript'].strip()
                    is_final = result.get('is_final', False)
            
            if text:
                # Create payload
                payload = {
                    "text": text,
                    "is_final": is_final,
                    "source": "deepgram",
                    "conversation_id": self._current_conversation_id,
                    "timestamp": time.time()
                }
                
                # Emit event
                topic = EventTopics.TRANSCRIPTION_FINAL if is_final else EventTopics.TRANSCRIPTION_INTERIM
                await self.emit(topic, payload)
                
                # Update timestamp for final transcripts
                if is_final:
                    self._last_final_timestamp = time.time()
                    
        except Exception as e:
            self.logger.error(f"Error processing single transcript: {e}")
            raise

    async def _process_errors(self) -> None:
        """Process errors in the event loop context."""
        try:
            while self._is_streaming:
                try:
                    # Get error from queue with timeout
                    error_msg = await asyncio.wait_for(
                        self._error_queue.get(),
                        timeout=0.1
                    )
                    
                    # Log the error
                    self.logger.error(f"Deepgram error: {error_msg}")
                    
                    # Emit error status
                    await self._emit_status(
                        ServiceStatus.ERROR,
                        f"Deepgram error: {error_msg}"
                    )
                    
                    self._error_queue.task_done()
                    
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    self.logger.error(f"Error processing error message: {e}")
                    await asyncio.sleep(0.1)
                    
        except asyncio.CancelledError:
            self.logger.info("Error processor cancelled")
            raise
        finally:
            self.logger.info("Error processor stopped")

    def get_latency_stats(self) -> Dict[str, float]:
        """Get latency statistics for the transcription service."""
        if not self._latency_measurements:
            return {"count": 0, "avg_ms": 0, "min_ms": 0, "max_ms": 0}
            
        latencies = [m["latency_ms"] for m in self._latency_measurements]
        return {
            "count": len(latencies),
            "avg_ms": sum(latencies) / len(latencies),
            "min_ms": min(latencies),
            "max_ms": max(latencies)
        }
        
    def reset_conversation_id(self) -> None:
        """Reset the conversation ID to start a new conversation context."""
        self._current_conversation_id = str(uuid.uuid4())
        self.logger.info(f"Reset conversation ID to {self._current_conversation_id}")
        
    @property
    def current_conversation_id(self) -> Optional[str]:
        """Get the current conversation ID."""
        return self._current_conversation_id
        
    async def _stop(self) -> None:
        """Service-specific cleanup logic."""
        await self.stop_streaming()
        await self._cleanup()
        self.logger.info("Stopped DeepgramTranscriptionService")

    async def _start(self) -> None:
        """Service-specific startup logic."""
        # Update event loop reference to ensure we have the running loop
        self._event_loop = asyncio.get_running_loop()
        
        # Set up event subscriptions
        await self._setup_subscriptions()
        
        # Initialize state
        self._current_conversation_id = str(uuid.uuid4())
        self.logger.info(f"Started DeepgramTranscriptionService with conversation ID: {self._current_conversation_id}")
    
    async def _setup_subscriptions(self) -> None:
        """Set up event subscriptions for audio and recording events."""
        # Subscribe to audio chunks
        asyncio.create_task(self.subscribe(
            EventTopics.AUDIO_RAW_CHUNK,
            self._handle_audio_chunk
        ))
        self.logger.info("Set up subscription for audio chunk events")
        
        # Subscribe to recording state changes
        asyncio.create_task(self.subscribe(
            EventTopics.VOICE_LISTENING_STARTED,
            self._handle_voice_listening_started
        ))
        asyncio.create_task(self.subscribe(
            EventTopics.VOICE_LISTENING_STOPPED,
            self._handle_voice_listening_stopped
        ))
        self.logger.info("Set up subscriptions for voice listening events")
    
    async def _handle_voice_listening_started(self, payload) -> None:
        """Handle voice listening start event."""
        self.logger.info("Voice listening started, starting streaming transcription")
        await self.start_streaming()
        
    async def _handle_voice_listening_stopped(self, payload) -> None:
        """Handle voice listening stop event."""
        self.logger.info("Voice listening stopped, stopping streaming transcription")
        await self.stop_streaming()

    def _extract_audio_samples(self, payload) -> Optional[bytes]:
        """
        Extract audio samples from various payload formats.
        
        Args:
            payload: AudioChunkPayload object or dictionary with audio data
            
        Returns:
            bytes: Audio samples as bytes, or None if extraction failed
        """
        try:
            # Extract audio samples based on payload type
            # It could be a dict (serialized Pydantic model) or AudioChunkPayload object
            samples = None
            
            if isinstance(payload, dict):
                if 'samples' in payload:
                    # Standard dictionary format
                    samples = payload['samples']
                    self.logger.debug(
                        f"Extracted audio from dict: {len(samples) if hasattr(samples, '__len__') else 'unknown'} bytes"
                    )
                # Check for other known dict formats
                elif any(key in payload for key in ['data', 'audio', 'content', 'buffer']):
                    # Try alternative key names
                    for key in ['data', 'audio', 'content', 'buffer']:
                        if key in payload:
                            samples = payload[key]
                            self.logger.debug(f"Found audio data using alternate key: {key}")
                            break
            elif hasattr(payload, 'samples'):
                # If payload is an AudioChunkPayload object or similar
                samples = payload.samples
                self.logger.debug(
                    f"Extracted audio from object: {len(samples) if hasattr(samples, '__len__') else 'unknown'} bytes"
                )
            # Direct payload as audio data
            elif isinstance(payload, (bytes, bytearray)) or (hasattr(payload, 'tobytes') and callable(getattr(payload, 'tobytes'))):
                # The payload itself might be the audio data
                samples = payload if isinstance(payload, (bytes, bytearray)) else payload.tobytes()
                self.logger.debug(f"Using payload directly as audio data: {len(samples)} bytes")
            else:
                self.logger.error(f"Unknown audio chunk payload format: {type(payload)}")
                return None

            # Ensure samples is in bytes format
            if not isinstance(samples, bytes):
                self.logger.debug(f"Converting audio data from {type(samples)} to bytes")
                if hasattr(samples, 'tobytes'):
                    # If it's a numpy array-like object with tobytes method
                    samples = samples.tobytes()
                else:
                    # Last resort try to convert to bytes if it's string-like
                    try:
                        samples = bytes(samples)
                    except Exception as e:
                        self.logger.error(f"Failed to convert audio data to bytes: {e}")
                        return None
            
            return samples
            
        except Exception as e:
            self.logger.error(f"Error extracting audio samples: {e}")
            return None 