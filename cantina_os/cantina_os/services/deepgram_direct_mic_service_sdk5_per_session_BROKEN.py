"""
DeepgramDirectMicService - SDK 5.x with pyaudio and KeepAlive.

This service uses SDK 5.x with:
- Persistent WebSocket connection (stays open across recordings)
- Automatic KeepAlive messages every 5 seconds to prevent 10s timeout
- Manual audio capture using pyaudio (SDK 5.x removed Microphone class)
"""

import logging
from typing import Optional, Dict, Any
import asyncio
import time
import os
import uuid
import threading
from dotenv import load_dotenv
import pyaudio
from deepgram import DeepgramClient
from deepgram.core.events import EventType
from deepgram.extensions.types.sockets import ListenV1ControlMessage

from cantina_os.base_service import BaseService
from cantina_os.core.event_bus import EventBus
from cantina_os.core.event_topics import EventTopics
from cantina_os.event_payloads import (
    TranscriptionTextPayload,
    ServiceStatusPayload,
    LogLevel,
    ServiceStatus,
    PerformanceMetricPayload
)


class DeepgramDirectMicService(BaseService):
    """
    Service that captures microphone audio and streams to Deepgram.

    SDK 5.x Features:
    - Persistent WebSocket with KeepAlive (prevents 10s timeout)
    - Manual audio capture with pyaudio
    - Streaming transcription with interim and final results
    """

    def __init__(
        self,
        event_bus: EventBus,
        config: Optional[Dict[str, Any]] = None,
        logger: Optional[logging.Logger] = None
    ):
        """Initialize the service."""
        super().__init__(service_name="deepgram_direct_mic", event_bus=event_bus, logger=logger)

        self._config = config or {}
        self._event_loop = asyncio.get_event_loop()

        load_dotenv()
        self._api_key = os.getenv("DEEPGRAM_API_KEY")
        if not self._api_key:
            raise ValueError("DEEPGRAM_API_KEY environment variable is not set")

        # Deepgram connection (SDK 5.x per-session pattern)
        self._deepgram: Optional[DeepgramClient] = None

        # PyAudio for manual microphone capture
        self._pyaudio: Optional[pyaudio.PyAudio] = None
        self._audio_thread: Optional[threading.Thread] = None  # Session thread (connection + audio)
        self._audio_running = False

        # State tracking
        self._is_listening = False
        self._current_transcription = ""
        self._start_time = None
        self._current_conversation_id = None

        # Metrics
        self._metrics = {
            "transcripts_processed": 0,
            "errors_count": 0,
            "total_latency": 0.0,
            "transcripts_for_latency": 0
        }

        # Audio parameters
        self._sample_rate = 16000
        self._channels = 1
        self._chunk_size = 8000  # Send 0.5 seconds of audio at a time

        # Connection parameters (SDK 5.x)
        self._connection_params = {
            "model": "nova-3",
            "punctuate": "true",
            "language": "en-US",
            "encoding": "linear16",
            "channels": "1",
            "sample_rate": "16000",
            "interim_results": "true",
            "utterance_end_ms": "1000",
            "vad_events": "true",
            "smart_format": "true",
            "endpointing": "1000"
        }

        self._metrics_task = None
        self._metrics_interval = config.get("METRICS_INTERVAL", 1.0)

    async def _setup_subscriptions(self) -> None:
        """Set up event subscriptions."""
        asyncio.create_task(self.subscribe(
            EventTopics.MIC_RECORDING_START,
            self._handle_mic_recording_start
        ))

        asyncio.create_task(self.subscribe(
            EventTopics.MIC_RECORDING_STOP,
            self._handle_mic_recording_stop
        ))

    async def _start(self) -> None:
        """Initialize Deepgram client (connections created per recording session)."""
        try:
            self._event_loop = asyncio.get_running_loop()

            # Initialize PyAudio
            self._pyaudio = pyaudio.PyAudio()

            # Initialize Deepgram client (SDK 5.x requires explicit api_key)
            self._deepgram = DeepgramClient(api_key=self._api_key)

            # Set up event subscriptions
            await self._setup_subscriptions()

            # Start metrics collection
            self._start_time = time.time()
            self._metrics_task = asyncio.create_task(self._collect_metrics())

            if self._logger:
                self._logger.info("DeepgramDirectMicService started (SDK 5.x - per-session connections)")

        except Exception as e:
            if self._logger:
                self._logger.error(f"Failed to start: {str(e)}")
            raise

    async def _stop(self) -> None:
        """Clean up resources."""
        try:
            # Stop metrics
            if self._metrics_task:
                self._metrics_task.cancel()
                try:
                    await self._metrics_task
                except asyncio.CancelledError:
                    pass

            # Stop audio capture if running
            if self._is_listening:
                await self._stop_listening()

            # Clean up PyAudio
            if self._pyaudio:
                self._pyaudio.terminate()
                self._pyaudio = None

            if self._logger:
                self._logger.info("DeepgramDirectMicService stopped")

        except Exception as e:
            if self._logger:
                self._logger.error(f"Error stopping: {str(e)}")


    def _deepgram_session_thread(self):
        """
        Run complete Deepgram connection lifecycle in a thread.
        SDK 5.x requires 'with' statement for connection management.
        This thread:
        1. Opens Deepgram WebSocket connection (with statement)
        2. Opens PyAudio stream
        3. Captures and streams audio until _audio_running = False
        4. Cleans up resources automatically via context manager
        """
        audio_stream = None
        try:
            if self._logger:
                self._logger.info("Creating Deepgram WebSocket connection...")
                self._logger.info(f"📋 Connection params: {self._connection_params}")

            # SDK 5.x: Use context manager for proper lifecycle management
            with self._deepgram.listen.v1.connect(**self._connection_params) as connection:
                if self._logger:
                    self._logger.info("✓ Deepgram WebSocket opened")

                # Set up event handlers with debugging
                if self._logger:
                    self._logger.info(f"🔧 Registering event handlers: OPEN, CLOSE, MESSAGE, ERROR")

                connection.on(EventType.OPEN, self._on_connection_open)
                connection.on(EventType.CLOSE, self._on_connection_close)
                connection.on(EventType.MESSAGE, self._on_transcript)
                connection.on(EventType.ERROR, self._on_error)

                if self._logger:
                    self._logger.info(f"✓ Event handlers registered")

                # Start listener in background (start_listening() is blocking)
                listener_thread = threading.Thread(
                    target=connection.start_listening,
                    daemon=True,
                    name="DeepgramListener"
                )
                listener_thread.start()

                # Wait for connection to stabilize
                time.sleep(0.3)

                # Open PyAudio stream
                if self._logger:
                    self._logger.info("Opening microphone...")

                audio_stream = self._pyaudio.open(
                    format=pyaudio.paInt16,
                    channels=self._channels,
                    rate=self._sample_rate,
                    input=True,
                    frames_per_buffer=self._chunk_size
                )

                if self._logger:
                    self._logger.info(f"✓ Microphone opened: format=paInt16, channels={self._channels}, rate={self._sample_rate}, chunk_size={self._chunk_size}")
                    self._logger.info("✓ Starting audio streaming to Deepgram...")

                # Capture and stream audio
                chunks_sent = 0
                while self._audio_running:
                    try:
                        # Read audio chunk
                        data = audio_stream.read(self._chunk_size, exception_on_overflow=False)

                        # Debug first chunk
                        if chunks_sent == 0 and self._logger:
                            self._logger.info(f"📤 First audio chunk: {len(data)} bytes, type={type(data)}")

                        # Send to Deepgram
                        connection.send_media(data)
                        chunks_sent += 1

                        if chunks_sent % 10 == 0 and self._logger:
                            self._logger.info(f"📤 Sent {chunks_sent} audio chunks ({chunks_sent * len(data)} bytes total)")

                    except OSError as e:
                        if self._logger:
                            self._logger.error(f"Audio read error: {e}")
                        break

                if self._logger:
                    self._logger.info(f"✓ Audio streaming ended ({chunks_sent} chunks sent)")

        except Exception as e:
            if self._logger:
                self._logger.error(f"Deepgram session thread error: {e}")
            import traceback
            if self._logger:
                self._logger.error(traceback.format_exc())

        finally:
            # Clean up audio stream
            if audio_stream:
                try:
                    audio_stream.stop_stream()
                    audio_stream.close()
                except Exception:
                    pass

            if self._logger:
                self._logger.info("✓ Deepgram session closed")

    def is_active(self) -> bool:
        """Check if actively listening."""
        return self._is_listening and self._audio_running

    async def _collect_metrics(self) -> None:
        """Collect and emit metrics."""
        while True:
            try:
                await asyncio.sleep(self._metrics_interval)

                uptime = time.time() - self._start_time
                average_latency = (
                    self._metrics["total_latency"] / self._metrics["transcripts_for_latency"]
                    if self._metrics["transcripts_for_latency"] > 0
                    else 0.0
                )

                metrics = [
                    PerformanceMetricPayload(
                        metric_name="transcription_latency",
                        value=average_latency,
                        unit="seconds",
                        component="deepgram_direct_mic",
                        details={
                            "uptime": uptime,
                            "transcripts_processed": self._metrics["transcripts_processed"],
                            "errors_count": self._metrics["errors_count"]
                        }
                    ).model_dump(),

                    PerformanceMetricPayload(
                        metric_name="transcription_error_rate",
                        value=self._metrics["errors_count"] / max(1, self._metrics["transcripts_processed"]),
                        unit="ratio",
                        component="deepgram_direct_mic"
                    ).model_dump(),

                    PerformanceMetricPayload(
                        metric_name="transcription_throughput",
                        value=self._metrics["transcripts_processed"] / max(1, uptime),
                        unit="transcripts/second",
                        component="deepgram_direct_mic"
                    ).model_dump()
                ]

                for metric in metrics:
                    await self.emit(EventTopics.TRANSCRIPTION_METRICS, metric)

            except asyncio.CancelledError:
                break
            except Exception as e:
                if self._logger:
                    self._logger.error(f"Error collecting metrics: {str(e)}")

    def _on_connection_open(self, open_event) -> None:
        """Handle WebSocket connection opening."""
        if self._logger:
            self._logger.info("Deepgram WebSocket opened (SDK 5.x)")

    def _on_connection_close(self, close_event) -> None:
        """Handle WebSocket connection closing."""
        if self._logger:
            self._logger.warning("Deepgram WebSocket closed")
        self._is_listening = False

    def _on_transcript(self, message_event) -> None:
        """Handle incoming transcripts."""
        if self._logger:
            self._logger.info(f"🔵🔵🔵 _on_transcript CALLBACK FIRED! Message type: {type(message_event)}, Message: {message_event}")
        try:
            result = message_event

            text = ""
            is_final = False
            confidence = 0.0
            processed_words = []
            duration = 0.0

            # SDK 5.x uses ListenV1ResultsEvent instead of LiveResultResponse
            if type(result).__name__ in ['ListenV1ResultsEvent', 'LiveResultResponse']:
                if hasattr(result, 'channel') and result.channel and \
                   hasattr(result.channel, 'alternatives') and result.channel.alternatives:
                    alternatives = result.channel.alternatives
                    if alternatives and len(alternatives) > 0:
                        alt = alternatives[0]
                        text = getattr(alt, 'transcript', "")
                        confidence = getattr(alt, 'confidence', 0.0)

                        # Process words
                        raw_words = getattr(alt, 'words', [])
                        if raw_words:
                            for word_obj in raw_words:
                                if hasattr(word_obj, 'word'):
                                    processed_words.append({
                                        "word": getattr(word_obj, 'word', None),
                                        "start": getattr(word_obj, 'start', None),
                                        "end": getattr(word_obj, 'end', None),
                                        "confidence": getattr(word_obj, 'confidence', None),
                                        "punctuated_word": getattr(word_obj, 'punctuated_word', getattr(word_obj, 'word', None))
                                    })

                is_final = getattr(result, 'is_final', False)
                if hasattr(result, 'duration'):
                    duration = float(getattr(result, 'duration', 0.0))
            else:
                return

            text = str(text) if text is not None else ""

            self._metrics["transcripts_processed"] += 1
            if duration > 0:
                self._metrics["total_latency"] += duration
                self._metrics["transcripts_for_latency"] += 1

            event_topic = EventTopics.TRANSCRIPTION_FINAL if is_final else EventTopics.TRANSCRIPTION_INTERIM

            payload = TranscriptionTextPayload(
                text=text,
                source="deepgram",
                is_final=is_final,
                confidence=confidence,
                words=processed_words or None,
                conversation_id=self._current_conversation_id
            ).model_dump()

            self._event_loop.call_soon_threadsafe(
                lambda data=payload, topic=event_topic: asyncio.create_task(
                    self.emit(topic, data)
                )
            )

            # Update current transcription for final results
            if is_final and text:
                if self._current_transcription:
                    self._current_transcription += " " + text.strip()
                else:
                    self._current_transcription = text.strip()
                if self._logger:
                    self._logger.info(f"Final transcript: {self._current_transcription}")

        except Exception as e:
            if self._logger:
                self._logger.error(f"Error processing transcript: {str(e)}")
            self._metrics["errors_count"] += 1

    def _on_error(self, error_event) -> None:
        """Handle Deepgram errors."""
        if self._logger:
            self._logger.error(f"Deepgram error: {error_event}")
        self._metrics["errors_count"] += 1

    async def _handle_mic_recording_start(self, event: Dict[str, Any]) -> None:
        """Handle recording start event."""
        if self._logger:
            self._logger.info("Recording start event received")

        conversation_id = str(uuid.uuid4())
        self._current_conversation_id = conversation_id

        await self.emit(EventTopics.VOICE_LISTENING_STARTED, {
            "conversation_id": conversation_id,
            "timestamp": time.time()
        })

        if not self._is_listening:
            await self._start_listening()

    async def _handle_mic_recording_stop(self, event: Dict[str, Any]) -> None:
        """Handle recording stop event."""
        if self._logger:
            self._logger.info("Recording stop event received")

        if not self._is_listening:
            return

        try:
            # Stop the Deepgram session thread (audio capture + connection)
            if self._audio_running:
                self._audio_running = False

                # Wait for thread to clean up (it handles closing audio stream and connection)
                if self._audio_thread and self._audio_thread.is_alive():
                    self._audio_thread.join(timeout=2)

            # Short delay for final transcript to arrive
            await asyncio.sleep(0.1)

            transcript = self._current_transcription.strip()

            if self._logger:
                self._logger.info(f"Final transcript: {transcript}")

            await self.emit(EventTopics.VOICE_LISTENING_STOPPED, {"transcript": transcript})

            self._is_listening = False

        except Exception as e:
            if self._logger:
                self._logger.error(f"Error stopping recording: {str(e)}")

    async def _start_listening(self) -> None:
        """Start Deepgram connection and audio streaming for this recording session."""
        try:
            # Reset transcription
            self._current_transcription = ""

            if self._logger:
                self._logger.info("Creating new Deepgram connection for this session...")

            # SDK 5.x: Create a fresh connection for this recording session
            # This runs in a background thread to handle the blocking context manager
            self._audio_running = True
            self._audio_thread = threading.Thread(
                target=self._deepgram_session_thread,
                daemon=True,
                name="DeepgramSession"
            )
            self._audio_thread.start()

            # Wait for connection to open
            await asyncio.sleep(0.5)

            self._is_listening = True

            if self._logger:
                self._logger.info("✓ Deepgram streaming session started (SDK 5.x per-session)")

        except Exception as e:
            if self._logger:
                self._logger.error(f"Failed to start listening: {str(e)}")
            import traceback
            if self._logger:
                self._logger.error(traceback.format_exc())
            raise

    async def _stop_listening(self) -> None:
        """Stop audio capture and close Deepgram connection."""
        try:
            if self._audio_running:
                self._audio_running = False

                # Wait for session thread to clean up (handles audio + connection)
                if self._audio_thread and self._audio_thread.is_alive():
                    self._audio_thread.join(timeout=2)

            self._is_listening = False

            if self._logger:
                self._logger.info("Stopped Deepgram streaming session")

        except Exception as e:
            if self._logger:
                self._logger.error(f"Error stopping listening: {str(e)}")
