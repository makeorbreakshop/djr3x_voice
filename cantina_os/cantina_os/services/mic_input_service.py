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
from typing import Optional, Dict, Any
from dataclasses import dataclass
from threading import Thread, Event
import time

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

class AudioChunkPayload(BaseEventPayload):
    """Payload for raw audio chunk events."""
    samples: bytes  # Raw audio samples as bytes
    timestamp: float  # Capture timestamp
    sample_rate: int  # Sample rate in Hz
    channels: int  # Number of channels
    dtype: str  # NumPy dtype string

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
        self._audio_queue = asyncio.Queue(maxsize=100)  # Use asyncio.Queue instead of threading.Queue
        self._stop_event = Event()
        self._capture_thread: Optional[Thread] = None
        self._is_capturing = False
        self._processing_task: Optional[asyncio.Task] = None
        
        # Store the current event loop for use in the audio callback
        self._loop = asyncio.get_event_loop()
        
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
        # Update loop reference to ensure we're using the correct loop
        self._loop = asyncio.get_running_loop()
        await self._initialize()
        self._setup_subscriptions()
        self.logger.info("MicInputService started and subscribed to voice events")
        
    async def _initialize(self) -> None:
        """Initialize audio resources."""
        try:
            # Verify audio device is available
            devices = sd.query_devices()
            if not devices:
                error_msg = "No audio devices found"
                self.logger.error(error_msg)
                self._emit_status(ServiceStatus.ERROR, error_msg)
                raise ValueError(error_msg)
            
            if self._config.device_index >= len(devices):
                error_msg = f"Audio device index {self._config.device_index} not found"
                self.logger.error(error_msg)
                self._emit_status(ServiceStatus.ERROR, error_msg)
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
                f"{self._config.channels} channel(s)"
            )
            
        except Exception as e:
            error_msg = f"Failed to initialize audio: {str(e)}"
            self.logger.error(error_msg)
            self._emit_status(ServiceStatus.ERROR, error_msg)
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
            
    def _audio_callback(self, indata, frames, time, status):
        """Handle incoming audio data from sounddevice."""
        if status:
            self.logger.warning(f"Audio callback status: {status}")
            return
        
        try:
            # Add audio data to queue with timestamp
            if self._is_capturing:
                # Calculate and log audio levels
                max_amp = np.max(np.abs(indata))
                
                # Fix for NaN RMS - ensure we have non-zero values before sqrt
                squared = np.mean(indata**2)
                rms = np.sqrt(squared) if squared > 0 else 0
                
                # Track stats
                if not hasattr(self, '_max_amplitude'):
                    self._max_amplitude = 0
                    self._rms_levels = []
                    self._chunk_count = 0
                
                self._chunk_count += 1
                if max_amp > self._max_amplitude:
                    self._max_amplitude = max_amp
                self._rms_levels.append(rms)
                
                # Log occasionally for debugging
                if self._chunk_count % 20 == 0:
                    self.logger.debug(f"Audio chunk {self._chunk_count}: max_amp={max_amp:.4f}, rms={rms:.4f}")
                
                # Use the proper time field from PortAudio timeinfo structure
                # The 'time' parameter is a PaStreamCallbackTimeInfo struct, which has:
                # - inputBufferAdcTime: the time when the first sample was captured
                # - currentTime: the time when the callback was invoked
                # - outputBufferDacTime: the time when the first sample will be output
                
                current_time = time.inputBufferAdcTime if hasattr(time, 'inputBufferAdcTime') else time.currentTime
                
                # Use the stored event loop instead of trying to get the current one
                if self._loop and not self._loop.is_closed():
                    asyncio.run_coroutine_threadsafe(
                        self._audio_queue.put((indata.copy(), current_time)),
                        self._loop
                    )
                    if self._chunk_count % 100 == 0:
                        self.logger.debug(f"Queue size: {self._audio_queue.qsize()}")
                else:
                    self.logger.warning("Event loop is not available or closed")
        except Exception as e:
            self.logger.error(f"Error in audio callback: {str(e)}")

    async def _process_audio(self) -> None:
        """Process audio data from the queue and emit events."""
        self.logger.info("Starting audio processing task")
        chunks_emitted = 0
        
        try:
            while self._is_capturing:
                try:
                    # Use wait_for to make queue.get cancellable
                    data, timestamp = await asyncio.wait_for(
                        self._audio_queue.get(),
                        timeout=0.1
                    )
                    
                    # Convert NumPy array to bytes
                    samples_bytes = data.tobytes()
                    
                    # Create and emit event
                    payload = AudioChunkPayload(
                        samples=samples_bytes,
                        timestamp=timestamp,
                        sample_rate=self._config.sample_rate,
                        channels=self._config.channels,
                        dtype=str(self._config.dtype)
                    )
                    
                    # Log sample size occasionally
                    if chunks_emitted == 0 or chunks_emitted % 50 == 0:
                        self.logger.info(f"Emitting audio chunk: {len(samples_bytes)} bytes, format: {self._config.dtype}, rate: {self._config.sample_rate}Hz")
                        
                    await self.emit(EventTopics.AUDIO_RAW_CHUNK, payload)
                    chunks_emitted += 1
                    
                    # Log emission progress
                    if chunks_emitted % 20 == 0:
                        self.logger.debug(f"Emitted {chunks_emitted} audio chunks")
                    
                except asyncio.TimeoutError:
                    # Check if we should continue processing
                    if not self._is_capturing:
                        break
                    continue
                except Exception as e:
                    self.logger.error(f"Error processing audio: {str(e)}")
                    if not self._is_capturing:
                        break
                    
        except asyncio.CancelledError:
            self.logger.info("Audio processing task cancelled")
            raise
        finally:
            # Clear the queue
            while not self._audio_queue.empty():
                try:
                    self._audio_queue.get_nowait()
                except:
                    pass
            self.logger.info(f"Audio processing task stopped. Emitted {chunks_emitted} chunks.")
        
    async def start_capture(self) -> None:
        """Start audio capture."""
        if self._is_capturing:
            self.logger.warning("Audio capture already active")
            return
            
        try:
            if not self._stream:
                error_msg = "Audio stream not initialized"
                self.logger.error(error_msg)
                await self._emit_status(ServiceStatus.ERROR, error_msg)
                raise RuntimeError(error_msg)
                
            self._is_capturing = True
            self._stream.start()
            
            # Start processing task
            self._processing_task = asyncio.create_task(self._process_audio())
            
            self.logger.info("Started audio capture")
            await self._emit_status(ServiceStatus.RUNNING, "Audio capture started")
            
        except Exception as e:
            self._is_capturing = False
            error_msg = f"Failed to start audio capture: {str(e)}"
            self.logger.error(error_msg)
            await self._emit_status(ServiceStatus.ERROR, error_msg)
            raise
            
    async def stop_capture(self) -> None:
        """Stop audio capture."""
        if not self._is_capturing:
            return
            
        try:
            self._is_capturing = False
            
            if self._stream is not None and self._stream.active:
                self._stream.stop()
                
            # Cancel and wait for processing task
            if self._processing_task is not None:
                self._processing_task.cancel()
                try:
                    await asyncio.wait_for(self._processing_task, timeout=1.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
                self._processing_task = None
            
            # Log audio statistics if available    
            if hasattr(self, '_rms_levels') and self._rms_levels:
                avg_rms = sum(self._rms_levels) / len(self._rms_levels)
                self.logger.info(f"Audio capture summary:")
                self.logger.info(f"  Chunks processed: {self._chunk_count}")
                self.logger.info(f"  Max amplitude: {self._max_amplitude:.4f}")
                self.logger.info(f"  Average RMS: {avg_rms:.4f}")
                self.logger.info(f"  Is signal present: {'YES' if self._max_amplitude > 0.01 else 'NO - very quiet'}")
                
            self.logger.info("Stopped audio capture")
            await self._emit_status(ServiceStatus.RUNNING, "Audio capture stopped")
            
        except Exception as e:
            error_msg = f"Error stopping audio capture: {str(e)}"
            self.logger.error(error_msg)
            await self._emit_status(ServiceStatus.ERROR, error_msg)
            raise
            
    def _setup_subscriptions(self) -> None:
        """Set up event subscriptions."""
        # Subscribe to voice control events
        asyncio.create_task(self.subscribe(EventTopics.VOICE_LISTENING_STARTED, self._handle_voice_listening_started))
        asyncio.create_task(self.subscribe(EventTopics.VOICE_LISTENING_STOPPED, self._handle_voice_listening_stopped))
        self.logger.info("Set up subscriptions for voice events")
        
    async def _handle_voice_listening_started(self, payload: Any) -> None:
        """
        Handle voice listening started event.
        
        Args:
            payload: Event payload (not used)
        """
        self.logger.info("Voice listening started event received, starting audio capture")
        await self.start_capture()
        
    async def _handle_voice_listening_stopped(self, payload: Any) -> None:
        """
        Handle voice listening stopped event.
        
        Args:
            payload: Event payload (may contain transcript)
        """
        self.logger.info("Voice listening stopped event received, stopping audio capture")
        await self.stop_capture() 