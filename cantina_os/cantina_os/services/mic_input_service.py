"""
Microphone Input Service

This service is responsible for capturing raw audio input from the system microphone
and emitting it as events on the bus. It supports both continuous and push-to-talk modes.
"""

import asyncio
import logging
import queue
import sounddevice as sd
import numpy as np
import threading
import time
from typing import Optional, Dict, Any
from dataclasses import dataclass

from ..base_service import BaseService
from ..event_topics import EventTopics
from ..event_payloads import (
    BaseEventPayload,
    ServiceStatus,
    AudioChunkPayload
)

@dataclass
class AudioConfig:
    """Configuration for audio capture."""
    device_index: int
    sample_rate: int = 16000
    channels: int = 1
    dtype: np.dtype = np.int16
    blocksize: int = 1024  # Samples per block
    latency: float = 0.1   # Device latency in seconds


class MicInputService(BaseService):
    """
    Service for capturing audio input from the system microphone.
    
    Features:
    - Configurable audio parameters (sample rate, channels, etc.)
    - Push-to-talk and continuous modes
    - Non-blocking audio capture using sounddevice
    - Automatic resource cleanup
    """
    
    def __init__(
        self,
        event_bus,
        config: Optional[Dict[str, Any]] = None,
        logger: Optional[logging.Logger] = None
    ):
        """Initialize the microphone input service."""
        super().__init__("mic_input", event_bus, logger)
        
        # Audio configuration
        self._config = self._load_config(config or {})
        
        # Audio capture state
        self._stream: Optional[sd.InputStream] = None
        self._audio_queue = asyncio.Queue(maxsize=100)
        self._stop_flag = threading.Event()
        self._is_capturing = False
        self._processing_task: Optional[asyncio.Task] = None
        
        # Store the event loop for thread-safe operations
        self._event_loop = asyncio.get_event_loop()
        
        # Error tracking
        self._errors = 0
        self._max_errors = 10
        self._paused_due_to_errors = False
        
        # Statistics
        self._chunks_processed = 0
        self._processing_start_time = 0
        
    def _load_config(self, config: Dict[str, Any]) -> AudioConfig:
        """Load audio configuration from provided dict or environment."""
        return AudioConfig(
            device_index=config.get("AUDIO_DEVICE_INDEX", 1),
            sample_rate=config.get("AUDIO_SAMPLE_RATE", 16000),
            channels=config.get("AUDIO_CHANNELS", 1),
            blocksize=config.get("AUDIO_BLOCKSIZE", 1024),
            latency=config.get("AUDIO_LATENCY", 0.1)
        )
        
    async def _start(self) -> None:
        """Initialize audio resources on service start."""
        await self._initialize()
        self._setup_subscriptions()
        self.logger.info("MicInputService started and subscribed to voice events")
        
    async def _initialize(self) -> None:
        """Initialize audio resources."""
        try:
            # Reset error counter
            self._errors = 0
            self._paused_due_to_errors = False
            
            # Verify audio device is available
            devices = sd.query_devices()
            if not devices:
                error_msg = "No audio devices found"
                self.logger.error(error_msg)
                await self._emit_status(ServiceStatus.ERROR, error_msg)
                raise ValueError(error_msg)
            
            if self._config.device_index >= len(devices):
                error_msg = f"Audio device index {self._config.device_index} not found"
                self.logger.error(error_msg)
                await self._emit_status(ServiceStatus.ERROR, error_msg)
                raise ValueError(error_msg)
                
            # Create audio stream
            self._stream = sd.InputStream(
                device=self._config.device_index,
                samplerate=self._config.sample_rate,
                channels=self._config.channels,
                dtype=self._config.dtype,
                blocksize=self._config.blocksize,
                latency=self._config.latency,
                callback=self._audio_callback
            )
            
            self.logger.info(
                f"Initialized audio capture: {self._config.sample_rate}Hz, "
                f"{self._config.channels} channel(s), block size: {self._config.blocksize}"
            )
            
        except Exception as e:
            error_msg = f"Failed to initialize audio: {str(e)}"
            self.logger.error(error_msg)
            await self._emit_status(ServiceStatus.ERROR, error_msg)
            self._errors += 1
            raise
            
    async def _cleanup(self) -> None:
        """Clean up audio resources."""
        try:
            # Stop capture first (this will also cancel the processing task)
            await self.stop_capture()
            
            # Ensure processing task is cancelled and cleaned up
            if self._processing_task is not None:
                if not self._processing_task.done():
                    self._processing_task.cancel()
                    try:
                        await self._processing_task
                    except asyncio.CancelledError:
                        pass
                self._processing_task = None
            
            if self._stream is not None:
                self._stream.close()
                self._stream = None
            
            self.logger.info("Cleaned up audio resources")
            
        except Exception as e:
            self.logger.error(f"Error cleaning up audio resources: {str(e)}")
            
    def _audio_callback(self, indata, frames, time_info, status):
        """Handle incoming audio data from sounddevice.
        This runs in a separate thread - keep it minimal and thread-safe."""
        if status:
            self._event_loop.call_soon_threadsafe(
                lambda: self.logger.warning(f"Audio callback status: {status}")
            )
            return
            
        try:
            if self._is_capturing and not self._paused_due_to_errors:
                # Create a copy of the data and get timestamp
                data_copy = indata.copy()
                current_time = getattr(time_info, 'inputBufferAdcTime', 
                                     getattr(time_info, 'currentTime', time.time()))
                
                # Single thread crossing with timeout
                if self._event_loop and not self._event_loop.is_closed():
                    future = asyncio.run_coroutine_threadsafe(
                        self._audio_queue.put((data_copy, current_time)),
                        self._event_loop
                    )
                    # Wait with timeout to avoid blocking
                    try:
                        future.result(timeout=0.1)
                    except Exception as e:
                        self._event_loop.call_soon_threadsafe(
                            lambda: self.logger.error(f"Queue put failed: {e}")
                        )
                        
        except Exception as e:
            self._event_loop.call_soon_threadsafe(
                lambda: self.logger.error(f"Error in audio callback: {e}")
            )

    async def _process_audio_queue(self):
        """Process audio data in the event loop context."""
        self.logger.info("Starting audio queue processing")
        self._chunks_processed = 0
        self._processing_start_time = time.time()
        
        try:
            while self._is_capturing and not self._paused_due_to_errors:
                try:
                    # Get data from queue with timeout
                    data, timestamp = await asyncio.wait_for(
                        self._audio_queue.get(),
                        timeout=0.1
                    )
                    
                    # Process in event loop context
                    await self._process_audio_chunk(data, timestamp)
                    self._audio_queue.task_done()
                    
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    self.logger.error(f"Error processing audio: {e}")
                    await asyncio.sleep(0.1)  # Prevent tight error loop
                    
        except asyncio.CancelledError:
            self.logger.info("Audio processing cancelled")
            raise
        finally:
            # Log statistics
            elapsed = time.time() - self._processing_start_time
            rate = self._chunks_processed / elapsed if elapsed > 0 else 0
            self.logger.info(f"Audio processing stopped after {self._chunks_processed} chunks "
                           f"({rate:.2f} chunks/sec)")

    async def _process_audio_chunk(self, data: np.ndarray, timestamp: float):
        """Process a single audio chunk in the event loop context."""
        try:
            # Convert to bytes
            samples_bytes = data.tobytes()
            
            # Calculate audio levels for monitoring
            max_amp = np.max(np.abs(data))
            rms = np.sqrt(np.mean(data**2)) if np.mean(data**2) > 0 else 0
            
            # Log occasionally
            if self._chunks_processed % 50 == 0:
                self.logger.debug(f"Audio chunk {self._chunks_processed}: "
                                f"max_amp={max_amp:.4f}, rms={rms:.4f}")
            
            # Emit audio chunk event
            await self.emit(EventTopics.AUDIO_RAW_CHUNK, {
                "samples": samples_bytes,
                "timestamp": timestamp,
                "sample_rate": self._config.sample_rate,
                "channels": self._config.channels,
                "dtype": str(self._config.dtype)
            })
            
            self._chunks_processed += 1
            
        except Exception as e:
            self.logger.error(f"Error processing audio chunk: {e}")
            raise

    async def start_capture(self):
        """Start audio capture."""
        if self._is_capturing:
            return
            
        try:
            # Initialize if needed
            if self._stream is None:
                await self._initialize()
                
            # Reset state
            self._is_capturing = True
            self._paused_due_to_errors = False
            self._stop_flag.clear()
            self._chunks_processed = 0
            self._processing_start_time = time.time()
            
            # Start stream
            self._stream.start()
            
            # Start processing in event loop
            self._processing_task = asyncio.create_task(self._process_audio_queue())
            
            self.logger.info("Audio capture started")
            await self._emit_status(ServiceStatus.RUNNING, "Audio capture started")
            
        except Exception as e:
            self._is_capturing = False
            error_msg = f"Failed to start audio capture: {e}"
            self.logger.error(error_msg)
            await self._emit_status(ServiceStatus.ERROR, error_msg)

    async def stop_capture(self):
        """Stop audio capture."""
        if not self._is_capturing:
            return
            
        try:
            # Signal stop
            self._is_capturing = False
            self._stop_flag.set()
            
            # Stop stream
            if self._stream and self._stream.active:
                self._stream.stop()
            
            # Cancel processing task
            if self._processing_task:
                self._processing_task.cancel()
                try:
                    await self._processing_task
                except asyncio.CancelledError:
                    pass
                self._processing_task = None
            
            # Clear queue
            while not self._audio_queue.empty():
                try:
                    self._audio_queue.get_nowait()
                    self._audio_queue.task_done()
                except asyncio.QueueEmpty:
                    break
            
            self.logger.info("Audio capture stopped")
            await self._emit_status(ServiceStatus.STOPPED, "Audio capture stopped")
            
        except Exception as e:
            self.logger.error(f"Error stopping audio capture: {e}")

    def _setup_subscriptions(self) -> None:
        """Set up event subscriptions."""
        # Subscribe to voice control events
        asyncio.create_task(self.subscribe(EventTopics.VOICE_LISTENING_STARTED, self._handle_voice_listening_started))
        asyncio.create_task(self.subscribe(EventTopics.VOICE_LISTENING_STOPPED, self._handle_voice_listening_stopped))
        asyncio.create_task(self.subscribe(EventTopics.MIC_RECORDING_START, self._handle_recording_start))
        asyncio.create_task(self.subscribe(EventTopics.MIC_RECORDING_STOP, self._handle_recording_stop))
        self.logger.info("Set up subscriptions for voice events")
        
    async def _handle_voice_listening_started(self, payload: Any) -> None:
        """Handle voice listening started event."""
        self.logger.info("Received voice listening started event")
        await self.start_capture()
        
    async def _handle_voice_listening_stopped(self, payload: Any) -> None:
        """Handle voice listening stopped event."""
        self.logger.info("Received voice listening stopped event")
        await self.stop_capture()
        
    async def _handle_recording_start(self, payload: Any) -> None:
        """Handle recording start event."""
        self.logger.info("Received recording start event")
        await self.start_capture()
        
    async def _handle_recording_stop(self, payload: Any) -> None:
        """Handle recording stop event."""
        self.logger.info("Received recording stop event")
        await self.stop_capture()
    
    async def _stop(self) -> None:
        """Service-specific cleanup logic."""
        await self.stop_capture()
        await self._cleanup()
        self.logger.info("MicInputService stopped") 