"""
DeepgramDirectMicService - Direct microphone streaming to Deepgram transcription service.

This service uses Deepgram's built-in Microphone class to handle audio capture and streaming,
eliminating the need for intermediate audio handling and reducing complexity.
"""

import logging
from typing import Optional, Dict, Any
import asyncio
import time
import os
from dotenv import load_dotenv
from deepgram import DeepgramClient, LiveOptions, LiveTranscriptionEvents, Microphone

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

# Replace string constants with EventTopics enum references
# from cantina_os.core.events import (
#     VOICE_LISTENING_STARTED,
#     VOICE_LISTENING_STOPPED,
#     TRANSCRIPTION_INTERIM,
#     TRANSCRIPTION_FINAL,
#     TRANSCRIPTION_ERROR,
#     TRANSCRIPTION_METRICS
# )

class DeepgramDirectMicService(BaseService):
    """
    Service that directly captures and streams microphone audio to Deepgram.
    
    Features:
    - Direct microphone integration using Deepgram's Microphone class
    - Streaming transcription with interim and final results
    - Configurable Deepgram model and language options
    - Compatible with existing event system for GPT and ElevenLabs integration
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
        
        # State tracking
        self._is_listening = False
        self._current_transcription = ""
        self._start_time = None
        
        # Thread-safe queue for audio data
        self._audio_queue = asyncio.Queue(maxsize=100)
        
        # Metrics tracking
        self._metrics = {
            "transcripts_processed": 0,
            "errors_count": 0,
            "total_latency": 0.0,
            "transcripts_for_latency": 0
        }
        
        # Configure Deepgram options
        self._dg_options = LiveOptions(
            model="nova-3",  # Latest model
            punctuate=True,
            language="en-US",
            encoding="linear16",
            channels=1,
            sample_rate=16000,
            interim_results=True,
            utterance_end_ms="1000",
            vad_events=True,
            smart_format=True
        )
        
        # Start metrics collection task
        self._metrics_task = None
        self._metrics_interval = config.get("METRICS_INTERVAL", 1.0)

    async def _setup_subscriptions(self) -> None:
        """Set up event subscriptions following architecture standards."""
        # Subscribe to voice control events
        asyncio.create_task(self.subscribe(
            EventTopics.VOICE_LISTENING_STARTED,
            self._handle_listening_started
        ))
        
        asyncio.create_task(self.subscribe(
            EventTopics.VOICE_LISTENING_STOPPED,
            self._handle_listening_stopped
        ))
        
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
        """Initialize the Deepgram client and set up event handlers."""
        try:
            # Update loop reference to ensure we're using the running loop
            self._event_loop = asyncio.get_running_loop()
            
            # Initialize Deepgram client
            self._deepgram = DeepgramClient()
            self._dg_connection = self._deepgram.listen.websocket.v("1")
            
            # Set up event handlers
            self._setup_deepgram_handlers()
            
            # Set up event subscriptions
            await self._setup_subscriptions()
            
            # Start metrics collection
            self._start_time = time.time()
            self._metrics_task = asyncio.create_task(self._collect_metrics())
            
            if self._logger:
                self._logger.info("DeepgramDirectMicService started successfully")
            
        except Exception as e:
            if self._logger:
                self._logger.error(f"Failed to start DeepgramDirectMicService: {str(e)}")
            raise

    async def _stop(self) -> None:
        """Clean up resources and close connections."""
        try:
            if self._metrics_task:
                self._metrics_task.cancel()
                try:
                    await self._metrics_task
                except asyncio.CancelledError:
                    pass
                self._metrics_task = None
            
            if self._is_listening:
                await self._stop_listening()
            
            if self._dg_connection:
                self._dg_connection.finish()  # Not awaitable
                self._dg_connection = None
            
            if self._deepgram:
                self._deepgram = None
                
            if self._logger:
                self._logger.info("DeepgramDirectMicService stopped successfully")
            
        except Exception as e:
            if self._logger:
                self._logger.error(f"Error stopping DeepgramDirectMicService: {str(e)}")
            raise

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
                        component="deepgram_direct_mic",  # Use static service name
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
                        component="deepgram_direct_mic"  # Use static service name
                    ).model_dump(),
                    
                    PerformanceMetricPayload(
                        metric_name="transcription_throughput",
                        value=self._metrics["transcripts_processed"] / max(1, uptime),
                        unit="transcripts/second",
                        component="deepgram_direct_mic"  # Use static service name
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
                    
                try:
                    # Emit error status
                    await self.emit(
                        EventTopics.SERVICE_STATUS_UPDATE,
                        ServiceStatusPayload(
                            service_name="deepgram_direct_mic",  # Use static service name
                            status=ServiceStatus.DEGRADED,
                            message=f"Error collecting metrics: {str(e)}",
                            severity=LogLevel.WARNING
                        ).model_dump()
                    )
                except Exception:
                    pass  # Prevent cascading errors

    def _setup_deepgram_handlers(self) -> None:
        """Set up handlers for Deepgram websocket events."""
        # Following Deepgram SDK's expected callback signatures
        self._dg_connection.on(LiveTranscriptionEvents.Open, self._on_connection_open)
        self._dg_connection.on(LiveTranscriptionEvents.Close, self._on_connection_close)
        self._dg_connection.on(LiveTranscriptionEvents.Transcript, self._on_transcript)
        self._dg_connection.on(LiveTranscriptionEvents.Error, self._on_error)
        self._dg_connection.on(LiveTranscriptionEvents.UtteranceEnd, self._on_utterance_end)

    def _on_connection_open(self, client, *args) -> None:
        """Handle websocket connection opening."""
        if self._logger:
            self._logger.info("Deepgram connection opened")

    def _on_connection_close(self, client, *args) -> None:
        """Handle websocket connection closing."""
        if self._logger:
            self._logger.info("Deepgram connection closed")
        self._is_listening = False

    def _on_transcript(self, client, result) -> None:
        """Handle incoming transcripts from Deepgram.
        
        Args:
            client: The Deepgram websocket client instance
            result: The transcript result from Deepgram (can be various types)
        """
        if self._logger:
            self._logger.debug(f"Deepgram _on_transcript raw result type: {type(result)}, content: {str(result)[:500]}...")

        try:
            text = ""
            is_final = False
            confidence = 0.0
            processed_words = [] # Changed variable name for clarity
            duration = 0.0 # For metrics
            source_type = "unknown"
            
            # Check if it's the Deepgram SDK's LiveResultResponse object
            if type(result).__name__ == 'LiveResultResponse':
                source_type = "LiveResultResponse"
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
                                # Check if word_obj is an object with attributes, not already a dict
                                if hasattr(word_obj, 'word'): 
                                    processed_words.append({
                                        "word": getattr(word_obj, 'word', None),
                                        "start": getattr(word_obj, 'start', None),
                                        "end": getattr(word_obj, 'end', None),
                                        "confidence": getattr(word_obj, 'confidence', None),
                                        "punctuated_word": getattr(word_obj, 'punctuated_word', getattr(word_obj, 'word', None))
                                    })
                                elif isinstance(word_obj, dict): # If it somehow already is a dict
                                    processed_words.append(word_obj)
                else:
                    if self._logger:
                        self._logger.warning(f"LiveResultResponse missing expected channel/alternatives: {str(result)[:500]}...")
                
                is_final = getattr(result, 'is_final', False)
                if hasattr(result, 'duration'):
                    duration = float(getattr(result, 'duration', 0.0))

            elif isinstance(result, dict):
                source_type = "dict"
                if "channel" not in result:
                    if self._logger:
                        self._logger.warning(f"Deepgram transcript dict missing 'channel': {result}")
                    return
                alternatives_list = result.get("channel", {}).get("alternatives", [])
                if not alternatives_list:
                    if self._logger:
                        self._logger.warning(f"Deepgram transcript dict missing 'alternatives': {result}")
                    return
                
                first_alternative = alternatives_list[0]
                text = first_alternative.get("transcript", "")
                confidence = first_alternative.get("confidence", 0.0)
                # Assuming words from dict are already in correct dict format
                processed_words = first_alternative.get("words", []) 
                is_final = result.get("is_final", False)
                if "duration" in result:
                     duration = float(result.get("duration", 0.0))

            elif isinstance(result, str):
                source_type = "str"
                if self._logger:
                    self._logger.debug(f"Result was a plain string, treating as final transcript: {result}")
                text = result
                is_final = True
                confidence = 1.0
            
            else:
                if self._logger:
                    self._logger.error(f"Unhandled Deepgram transcript result type: {type(result)}, content: {str(result)[:500]}...")
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
                words=processed_words or None  # Use processed_words
            ).model_dump()
            
            if self._logger:
                self._logger.debug(f"Emitting {event_topic} from {source_type}: {str(payload)[:200]}...")
                # Enhanced event topic logging for debugging
                self._logger.info(f"EMIT DEBUG: Using event topic '{str(event_topic)}', type: {type(event_topic)}, id: {id(event_topic)}")
                self._logger.info(f"EMIT DEBUG: EventTopics.TRANSCRIPTION_FINAL value: '{str(EventTopics.TRANSCRIPTION_FINAL)}', id: {id(EventTopics.TRANSCRIPTION_FINAL)}")
                
                # Log the actual string value of the event_topic
                self._logger.debug(f"DeepgramDirectMicService emitting on actual topic string: '{str(event_topic)}'")

            self._event_loop.call_soon_threadsafe(
                lambda data=payload, topic=event_topic: asyncio.create_task(
                    self.emit(topic, data)
                )
            )
            
            # Update the current transcription for both interim and final results
            # This ensures we have the latest transcription when the mouse click stop event comes
            if is_final:
                if self._logger:
                    self._logger.info(f"Final transcript segment: {text}")
                if text:  # Only append if there's actual text
                    if self._current_transcription: # If not empty, add a space before appending
                        self._current_transcription += " " + text.strip()
                    else:
                        self._current_transcription = text.strip()
                    if self._logger: # Log the fully accumulated transcript so far
                        self._logger.info(f"Updated accumulated transcript: {self._current_transcription}")
            # Interim results are emitted as events but do not update self._current_transcription
            # which is meant to hold the full final utterance for GPT.
            # else:
            #     if text: # For debugging, you might want to see interim segments
            #         if self._logger:
            #             self._logger.debug(f"Interim transcript segment (not accumulated): {text}")
                
        except Exception as e:
            error_msg = f"Error processing transcript: {str(e)}. Original result type: {type(result)}, content: {str(result)[:500]}..."
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

    def _on_error(self, client, error: Dict[str, Any], *args) -> None:
        """Handle Deepgram errors.
        
        Args:
            client: The Deepgram websocket client instance
            error: The error information from Deepgram
            args: Additional arguments that may be passed
        """
        if self._logger:
            self._logger.error(f"Deepgram error: {error}")
        self._metrics["errors_count"] += 1
        
        # Create and emit error payload
        error_message = str(error) if isinstance(error, (str, Exception)) else f"Deepgram error: {error}"
        
        # Emit error status - using service status update for critical errors
        status_payload = ServiceStatusPayload(
            service_name="deepgram_direct_mic",  # Use static service name
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
        
        # Also emit transcription error for handlers that expect that
        error_payload = {
            "error": error_message,
            "source": "deepgram_websocket",
            "raw_error": error if isinstance(error, dict) else None
        }
        
        self._event_loop.call_soon_threadsafe(
            lambda data=error_payload: asyncio.create_task(
                self.emit(EventTopics.TRANSCRIPTION_ERROR, data)
            )
        )

    def _on_utterance_end(self, client, utterance_end: Optional[Dict[str, Any]] = None, *args) -> None:
        """Handle end of utterance events.
        
        Args:
            client: The Deepgram websocket client instance
            utterance_end: Optional utterance end data from Deepgram
            args: Additional arguments that may be passed
        """
        if self._current_transcription:
            if self._logger:
                self._logger.debug(f"Utterance ended: {self._current_transcription}")

    async def _handle_listening_started(self, event: Dict[str, Any]) -> None:
        """Handle the VOICE_LISTENING_STARTED event."""
        if not self._is_listening:
            await self._start_listening()

    async def _handle_listening_stopped(self, event: Dict[str, Any]) -> None:
        """Handle the VOICE_LISTENING_STOPPED event."""
        if self._is_listening:
            await self._stop_listening()

    async def _handle_mic_recording_start(self, event: Dict[str, Any]) -> None:
        """Handle the MIC_RECORDING_START event (triggered by mouse clicks)."""
        if self._logger:
            self._logger.info("Received mouse-triggered recording start event")
        
        # Emit the voice listening started event to maintain compatibility with existing flow
        await self.emit(EventTopics.VOICE_LISTENING_STARTED, {})
        
        # Start listening directly
        if not self._is_listening:
            await self._start_listening()

    async def _handle_mic_recording_stop(self, event: Dict[str, Any]) -> None:
        """Handle the MIC_RECORDING_STOP event (triggered by mouse clicks).
        This method now fully handles stopping the recording and emitting the final transcript.
        """
        if self._logger:
            self._logger.info("Received mouse-triggered recording stop event. Stopping media streams first.")

        if not self._is_listening:
            if self._logger:
                self._logger.warning("MIC_RECORDING_STOP received but not currently listening.")
            return

        try:
            # Stop microphone and Deepgram connection first to ensure all data is flushed
            # This should prevent new transcript data from arriving and modifying _current_transcription
            # while we are trying to finalize it.
            if self._microphone:
                self._microphone.finish()  # Synchronous
                self._microphone = None # Ensure it's not reused until re-init
                if self._logger:
                    self._logger.info("Microphone.finish() called.")
            
            if self._dg_connection:
                self._dg_connection.finish()  # Synchronous, signals to close
                if self._logger:
                    self._logger.info("DeepgramConnection.finish() called.")

            # Short delay to allow any in-flight SDK processing from finish() to settle.
            # This helps ensure _on_transcript has a chance to process final data chunks.
            await asyncio.sleep(0.25) # 250ms, adjust if needed based on testing.
            
            # Now that media input is stopped and SDK had a moment to process, get the final accumulated transcript
            accumulated_transcript = self._current_transcription.strip()

            if self._logger:
                self._logger.info(f"Final accumulated transcript after stopping media and delay: {accumulated_transcript}")
            
            # Emit the voice listening stopped event with the full transcript for GPTService
            await self.emit(EventTopics.VOICE_LISTENING_STOPPED, {"transcript": accumulated_transcript})
            if self._logger:
                self._logger.info("Emitted VOICE_LISTENING_STOPPED with final transcript.")

            # Mark as not listening. _current_transcription will be reset by _start_listening on the next recording session.
            self._is_listening = False 
            
            if self._logger:
                self._logger.info("Mic recording stop processed, final transcript emitted, and state updated.")

        except Exception as e:
            error_msg = f"Error in _handle_mic_recording_stop: {str(e)}"
            if self._logger:
                self._logger.error(error_msg)
            
            # Emit error status
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
                self._logger.info("Reset _current_transcription for new listening session.")

            # Emit status update
            await self.emit(
                EventTopics.SERVICE_STATUS_UPDATE,
                ServiceStatusPayload(
                    service_name="deepgram_direct_mic",  # Use static service name
                    status=ServiceStatus.RUNNING,
                    message="Starting audio capture and Deepgram streaming",
                    severity=LogLevel.INFO
                ).model_dump()
            )
            
            # Start Deepgram connection with options
            self._dg_connection.start(self._dg_options)  # Not awaitable
            
            # Create and start microphone
            self._microphone = Microphone(self._dg_connection.send)
            self._microphone.start()
            
            self._is_listening = True
            if self._logger:
                self._logger.info("Started listening and streaming to Deepgram")
            
        except Exception as e:
            error_msg = f"Failed to start listening: {str(e)}"
            if self._logger:
                self._logger.error(error_msg)
                
            # Emit error status
            await self.emit(
                EventTopics.SERVICE_STATUS_UPDATE,
                ServiceStatusPayload(
                    service_name="deepgram_direct_mic",  # Use static service name
                    status=ServiceStatus.ERROR,
                    message=error_msg,
                    severity=LogLevel.ERROR
                ).model_dump()
            )
            
            # Also emit transcription error
            await self.emit(
                EventTopics.TRANSCRIPTION_ERROR, 
                {
                    "error": error_msg,
                    "source": "deepgram_start_listening"
                }
            )
            raise

    async def _stop_listening(self) -> None:
        """Stop the microphone and clean up the streaming connection."""
        try:
            # Emit status update
            await self.emit(
                EventTopics.SERVICE_STATUS_UPDATE,
                ServiceStatusPayload(
                    service_name="deepgram_direct_mic",  # Use static service name
                    status=ServiceStatus.RUNNING,
                    message="Stopping audio capture and Deepgram streaming",
                    severity=LogLevel.INFO
                ).model_dump()
            )
            
            if self._microphone:
                self._microphone.finish()
                self._microphone = None
            
            if self._dg_connection:
                self._dg_connection.finish()  # Not awaitable
            
            self._is_listening = False
            if self._logger:
                self._logger.info("Stopped listening and streaming")
            
        except Exception as e:
            error_msg = f"Error stopping listening: {str(e)}"
            if self._logger:
                self._logger.error(error_msg)
                
            # Emit error status
            await self.emit(
                EventTopics.SERVICE_STATUS_UPDATE,
                ServiceStatusPayload(
                    service_name="deepgram_direct_mic",  # Use static service name
                    status=ServiceStatus.ERROR,
                    message=error_msg,
                    severity=LogLevel.ERROR
                ).model_dump()
            )
            raise 