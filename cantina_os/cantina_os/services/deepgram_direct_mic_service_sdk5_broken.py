"""
DeepgramDirectMicService - SDK 5.x version with persistent connection + KeepAlive.

This service uses Deepgram SDK 5.x patterns for improved latency:
- Persistent WebSocket connection (stays open across recordings)
- Automatic KeepAlive messages every 5 seconds to prevent 10s timeout
- Direct microphone integration using Deepgram's Microphone class
"""

import logging
from typing import Optional, Dict, Any
import asyncio
import time
import os
import uuid
import threading
from dotenv import load_dotenv
from deepgram import DeepgramClient, Microphone
from deepgram.core.events import EventType
from deepgram.extensions.types.sockets import ListenV1ControlMessage

from cantina_os.base_service import BaseService
from cantina_os.core.event_bus import EventBus
from cantina_os.core.event_topics import EventTopics
from cantina_os.event_payloads import (
    TranscriptionTextPayload,
    CliResponsePayload,
    ServiceStatusPayload,
    LogLevel,
    ServiceStatus,
    PerformanceMetricPayload
)


class DeepgramDirectMicService(BaseService):
    """
    Service that directly captures and streams microphone audio to Deepgram.

    SDK 5.x Features:
    - Persistent WebSocket connection (reduces latency by ~5 seconds)
    - Automatic KeepAlive every 5 seconds (prevents 10-second timeout)
    - Direct microphone integration using Deepgram's Microphone class
    - Streaming transcription with interim and final results
    - Performance metrics collection and reporting
    """

    def __init__(
        self,
        event_bus: EventBus,
        config: Optional[Dict[str, Any]] = None,
        logger: Optional[logging.Logger] = None
    ):
        """Initialize the service following BaseService pattern."""
        super().__init__(service_name="deepgram_direct_mic", event_bus=event_bus, logger=logger)

        # Store config
        self._config = config or {}

        # Store event loop for thread-safe operations
        self._event_loop = asyncio.get_event_loop()

        # Ensure environment variables are loaded
        load_dotenv()

        # Validate API key
        if not os.getenv("DEEPGRAM_API_KEY"):
            raise ValueError("DEEPGRAM_API_KEY environment variable is not set")

        # Deepgram client and connection
        self._deepgram: Optional[DeepgramClient] = None
        self._dg_connection = None
        self._microphone: Optional[Microphone] = None
        self._listener_thread: Optional[threading.Thread] = None

        # KeepAlive task
        self._keepalive_task: Optional[asyncio.Task] = None
        self._keepalive_interval = 5  # seconds

        # State tracking
        self._is_listening = False
        self._current_transcription = ""
        self._start_time = None
        self._current_conversation_id = None

        # Metrics tracking
        self._metrics = {
            "transcripts_processed": 0,
            "errors_count": 0,
            "total_latency": 0.0,
            "transcripts_for_latency": 0
        }

        # Connection parameters (SDK 5.x - passed to connect())
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
            "endpointing": "1000"  # SDK 5.x feature - 1 second endpointing
        }

        # Start metrics collection task
        self._metrics_task = None
        self._metrics_interval = config.get("METRICS_INTERVAL", 1.0)

    async def _setup_subscriptions(self) -> None:
        """Set up event subscriptions following architecture standards."""
        # Subscribe to mouse click events
        asyncio.create_task(self.subscribe(
            EventTopics.MIC_RECORDING_START,
            self._handle_mic_recording_start
        ))

        asyncio.create_task(self.subscribe(
            EventTopics.MIC_RECORDING_STOP,
            self._handle_mic_recording_stop
        ))

    async def _start(self) -> None:
        """Initialize the Deepgram client and set up persistent connection."""
        try:
            # Update loop reference to ensure we're using the running loop
            self._event_loop = asyncio.get_running_loop()

            # Initialize Deepgram client
            self._deepgram = DeepgramClient()

            # SDK 5.x: Create persistent WebSocket connection using context manager
            # This connection stays open for the entire service lifetime
            self._dg_connection = self._deepgram.listen.v1.connect(**self._connection_params).__enter__()

            # Set up event handlers (SDK 5.x uses EventType enum)
            self._dg_connection.on(EventType.OPEN, self._on_connection_open)
            self._dg_connection.on(EventType.CLOSE, self._on_connection_close)
            self._dg_connection.on(EventType.MESSAGE, self._on_transcript)
            self._dg_connection.on(EventType.ERROR, self._on_error)

            # Start listener thread (start_listening() is blocking in SDK 5.x)
            self._listener_thread = threading.Thread(
                target=self._dg_connection.start_listening,
                daemon=True,
                name="DeepgramListener"
            )
            self._listener_thread.start()

            # Wait for connection to open
            await asyncio.sleep(0.5)

            # Start KeepAlive loop to prevent 10-second timeout
            self._keepalive_task = asyncio.create_task(self._keepalive_loop())

            # Set up event subscriptions
            await self._setup_subscriptions()

            # Start metrics collection
            self._start_time = time.time()
            self._metrics_task = asyncio.create_task(self._collect_metrics())

            if self._logger:
                self._logger.info("DeepgramDirectMicService started with persistent WebSocket + KeepAlive")

        except Exception as e:
            if self._logger:
                self._logger.error(f"Failed to start DeepgramDirectMicService: {str(e)}")
            raise

    async def _stop(self) -> None:
        """Clean up resources and close connections."""
        try:
            # Stop KeepAlive task
            if self._keepalive_task:
                self._keepalive_task.cancel()
                try:
                    await self._keepalive_task
                except asyncio.CancelledError:
                    pass
                self._keepalive_task = None

            # Stop metrics task
            if self._metrics_task:
                self._metrics_task.cancel()
                try:
                    await self._metrics_task
                except asyncio.CancelledError:
                    pass
                self._metrics_task = None

            if self._is_listening:
                await self._stop_listening()

            # Close WebSocket connection
            if self._dg_connection:
                try:
                    self._dg_connection.__exit__(None, None, None)
                except Exception as e:
                    if self._logger:
                        self._logger.warning(f"Error closing WebSocket: {e}")
                self._dg_connection = None

            # Wait for listener thread to finish
            if self._listener_thread and self._listener_thread.is_alive():
                self._listener_thread.join(timeout=2)
                self._listener_thread = None

            if self._deepgram:
                self._deepgram = None

            if self._logger:
                self._logger.info("DeepgramDirectMicService stopped successfully")

        except Exception as e:
            if self._logger:
                self._logger.error(f"Error stopping DeepgramDirectMicService: {str(e)}")
            raise

    async def _keepalive_loop(self) -> None:
        """Send KeepAlive messages every 5 seconds to prevent 10-second timeout."""
        while True:
            try:
                await asyncio.sleep(self._keepalive_interval)

                if self._dg_connection:
                    control_msg = ListenV1ControlMessage(type="KeepAlive")
                    self._dg_connection.send_control(control_msg)
                    if self._logger:
                        self._logger.debug("Sent KeepAlive to Deepgram")

            except asyncio.CancelledError:
                break
            except Exception as e:
                if self._logger:
                    self._logger.warning(f"KeepAlive failed: {e}")

    def is_active(self) -> bool:
        """Check if the service is actively listening and streaming."""
        return self._is_listening and bool(self._microphone)

    async def _collect_metrics(self) -> None:
        """Collect and emit performance metrics periodically."""
        while True:
            try:
                await asyncio.sleep(self._metrics_interval)

                # Calculate metrics
                uptime = time.time() - self._start_time
                average_latency = (
                    self._metrics["total_latency"] / self._metrics["transcripts_for_latency"]
                    if self._metrics["transcripts_for_latency"] > 0
                    else 0.0
                )

                # Create metrics payloads
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

                # Emit metrics
                for metric in metrics:
                    await self.emit(EventTopics.TRANSCRIPTION_METRICS, metric)

            except asyncio.CancelledError:
                break
            except Exception as e:
                if self._logger:
                    self._logger.error(f"Error collecting metrics: {str(e)}")

    def _on_connection_open(self, open_event) -> None:
        """Handle websocket connection opening (SDK 5.x callback)."""
        if self._logger:
            self._logger.info("Deepgram WebSocket connection opened")

    def _on_connection_close(self, close_event) -> None:
        """Handle websocket connection closing (SDK 5.x callback)."""
        if self._logger:
            self._logger.info("Deepgram WebSocket connection closed")
        self._is_listening = False

    def _on_transcript(self, message_event) -> None:
        """Handle incoming transcripts from Deepgram (SDK 5.x callback)."""
        if self._logger:
            self._logger.debug(f"Deepgram message type: {type(message_event)}")

        try:
            # SDK 5.x returns typed message events
            result = message_event

            text = ""
            is_final = False
            confidence = 0.0
            processed_words = []
            duration = 0.0

            # Check if it's the SDK's LiveResultResponse object
            if type(result).__name__ == 'LiveResultResponse':
                if hasattr(result, 'channel') and result.channel and \
                   hasattr(result.channel, 'alternatives') and result.channel.alternatives:
                    alternatives_list = result.channel.alternatives
                    if alternatives_list and len(alternatives_list) > 0:
                        first_alternative = alternatives_list[0]
                        text = getattr(first_alternative, 'transcript', "")
                        confidence = getattr(first_alternative, 'confidence', 0.0)

                        # Process words
                        raw_words = getattr(first_alternative, 'words', [])
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
                                elif isinstance(word_obj, dict):
                                    processed_words.append(word_obj)

                is_final = getattr(result, 'is_final', False)
                if hasattr(result, 'duration'):
                    duration = float(getattr(result, 'duration', 0.0))
            else:
                if self._logger:
                    self._logger.warning(f"Unexpected message type: {type(result)}")
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

            if self._logger:
                self._logger.debug(f"Emitting {event_topic}: {str(payload)[:200]}...")

            self._event_loop.call_soon_threadsafe(
                lambda data=payload, topic=event_topic: asyncio.create_task(
                    self.emit(topic, data)
                )
            )

            # Update current transcription for final results
            if is_final:
                if self._logger:
                    self._logger.info(f"Final transcript segment: {text}")
                if text:
                    if self._current_transcription:
                        self._current_transcription += " " + text.strip()
                    else:
                        self._current_transcription = text.strip()
                    if self._logger:
                        self._logger.info(f"Updated accumulated transcript: {self._current_transcription}")

        except Exception as e:
            error_msg = f"Error processing transcript: {str(e)}"
            if self._logger:
                self._logger.error(error_msg)
            self._metrics["errors_count"] += 1

            error_payload_dict = {
                "error": error_msg,
                "source": "deepgram_transcript_processing"
            }

            self._event_loop.call_soon_threadsafe(
                lambda msg=error_payload_dict: asyncio.create_task(
                    self.emit(EventTopics.TRANSCRIPTION_ERROR, msg)
                )
            )

    def _on_error(self, error_event) -> None:
        """Handle Deepgram errors (SDK 5.x callback)."""
        if self._logger:
            self._logger.error(f"Deepgram error: {error_event}")
        self._metrics["errors_count"] += 1

        # Create and emit error payload
        error_message = str(error_event)

        # Emit error status
        status_payload = ServiceStatusPayload(
            service_name="deepgram_direct_mic",
            status=ServiceStatus.ERROR,
            message=f"Deepgram WebSocket error: {error_message}",
            severity=LogLevel.ERROR
        ).model_dump()

        # Emit using thread-safe method
        self._event_loop.call_soon_threadsafe(
            lambda data=status_payload: asyncio.create_task(
                self.emit(EventTopics.SERVICE_STATUS_UPDATE, data)
            )
        )

    async def _handle_mic_recording_start(self, event: Dict[str, Any]) -> None:
        """Handle the MIC_RECORDING_START event (triggered by mouse clicks)."""
        if self._logger:
            self._logger.info("Received mouse-triggered recording start event")

        # Generate a conversation ID for mouse-triggered interactions
        conversation_id = str(uuid.uuid4())
        self._current_conversation_id = conversation_id

        # Emit the voice listening started event to maintain compatibility
        await self.emit(EventTopics.VOICE_LISTENING_STARTED, {
            "conversation_id": conversation_id,
            "timestamp": time.time()
        })

        # Start listening directly
        if not self._is_listening:
            await self._start_listening()

    async def _handle_mic_recording_stop(self, event: Dict[str, Any]) -> None:
        """Handle the MIC_RECORDING_STOP event (triggered by mouse clicks)."""
        if self._logger:
            self._logger.info("Received mouse-triggered recording stop event")

        if not self._is_listening:
            if self._logger:
                self._logger.warning("MIC_RECORDING_STOP received but not currently listening")
            return

        try:
            # Stop ONLY the microphone, NOT the WebSocket connection
            if self._microphone:
                self._microphone.finish()
                self._microphone = None
                if self._logger:
                    self._logger.info("Microphone stopped (WebSocket stays open)")

            # Short delay to allow any in-flight audio data to be transcribed
            await asyncio.sleep(0.05)

            # Get the final accumulated transcript
            accumulated_transcript = self._current_transcription.strip()

            if self._logger:
                self._logger.info(f"Final accumulated transcript: {accumulated_transcript}")

            # Emit the voice listening stopped event with the full transcript
            await self.emit(EventTopics.VOICE_LISTENING_STOPPED, {"transcript": accumulated_transcript})
            if self._logger:
                self._logger.info("Emitted VOICE_LISTENING_STOPPED with final transcript")

            # Mark as not listening
            self._is_listening = False

        except Exception as e:
            error_msg = f"Error in _handle_mic_recording_stop: {str(e)}"
            if self._logger:
                self._logger.error(error_msg)

            await self.emit(
                EventTopics.SERVICE_STATUS_UPDATE,
                ServiceStatusPayload(
                    service_name=self.name,
                    status=ServiceStatus.ERROR,
                    message=error_msg,
                    severity=LogLevel.ERROR
                ).model_dump()
            )

    async def _start_listening(self) -> None:
        """Start the microphone and begin streaming to Deepgram."""
        try:
            # Reset transcription for the new session
            self._current_transcription = ""
            if self._logger:
                self._logger.info("Reset _current_transcription for new listening session")

            # Emit status update
            await self.emit(
                EventTopics.SERVICE_STATUS_UPDATE,
                ServiceStatusPayload(
                    service_name="deepgram_direct_mic",
                    status=ServiceStatus.RUNNING,
                    message="Starting audio capture",
                    severity=LogLevel.INFO
                ).model_dump()
            )

            # WebSocket connection is already open from _start()
            # Just create and start the microphone to stream audio
            self._microphone = Microphone(self._dg_connection.send)
            self._microphone.start()

            self._is_listening = True
            if self._logger:
                self._logger.info("Started microphone streaming (persistent connection)")

        except Exception as e:
            error_msg = f"Failed to start listening: {str(e)}"
            if self._logger:
                self._logger.error(error_msg)

            await self.emit(
                EventTopics.SERVICE_STATUS_UPDATE,
                ServiceStatusPayload(
                    service_name="deepgram_direct_mic",
                    status=ServiceStatus.ERROR,
                    message=error_msg,
                    severity=LogLevel.ERROR
                ).model_dump()
            )
            raise

    async def _stop_listening(self) -> None:
        """Stop the microphone (but keep WebSocket connection open)."""
        try:
            await self.emit(
                EventTopics.SERVICE_STATUS_UPDATE,
                ServiceStatusPayload(
                    service_name="deepgram_direct_mic",
                    status=ServiceStatus.RUNNING,
                    message="Stopping audio capture",
                    severity=LogLevel.INFO
                ).model_dump()
            )

            if self._microphone:
                self._microphone.finish()
                self._microphone = None

            self._is_listening = False
            if self._logger:
                self._logger.info("Stopped microphone (WebSocket stays open)")

        except Exception as e:
            error_msg = f"Error stopping listening: {str(e)}"
            if self._logger:
                self._logger.error(error_msg)

            await self.emit(
                EventTopics.SERVICE_STATUS_UPDATE,
                ServiceStatusPayload(
                    service_name="deepgram_direct_mic",
                    status=ServiceStatus.ERROR,
                    message=error_msg,
                    severity=LogLevel.ERROR
                ).model_dump()
            )
            raise
