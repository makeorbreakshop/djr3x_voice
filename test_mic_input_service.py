#!/usr/bin/env python3
"""
Test script for MicInputService to verify audio capture and event emission.
This standalone script will create a minimal version of the event system and
MicInputService to test if audio is being captured correctly.
"""

import asyncio
import logging
import numpy as np
import sounddevice as sd
import time
import os
import sys
from dataclasses import dataclass
from typing import Dict, Any, Optional, List

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("test_mic_input")

# Mock EventBus for testing
class MockEventBus:
    def __init__(self):
        self.events = {}
        self.received_events = []
        self.logger = logging.getLogger("mock_event_bus")
        
    async def on(self, topic, handler):
        if topic not in self.events:
            self.events[topic] = []
        self.events[topic].append(handler)
        self.logger.debug(f"Registered handler for topic: {topic}")
        return True
        
    def emit(self, topic, payload):
        self.logger.debug(f"Event emitted: {topic}")
        self.received_events.append((topic, payload))
        if topic in self.events:
            for handler in self.events[topic]:
                asyncio.create_task(handler(payload))
        
    async def remove_listener(self, topic, handler):
        if topic in self.events and handler in self.events[topic]:
            self.events[topic].remove(handler)
            self.logger.debug(f"Removed handler for topic: {topic}")
        
    def get_event_count(self, topic=None):
        if topic:
            return len([e for e, _ in self.received_events if e == topic])
        return len(self.received_events)

# EventTopics constants
class EventTopics:
    AUDIO_RAW_CHUNK = "/audio/raw/chunk"
    VOICE_LISTENING_STARTED = "/voice/listening/started"
    VOICE_LISTENING_STOPPED = "/voice/listening/stopped"
    SERVICE_STATUS_UPDATE = "/service/status/update"

# Event payload base class
@dataclass
class BaseEventPayload:
    pass

# Audio chunk payload
@dataclass
class AudioChunkPayload(BaseEventPayload):
    samples: bytes  # Raw audio samples as bytes
    timestamp: float  # Capture timestamp
    sample_rate: int  # Sample rate in Hz
    channels: int  # Number of channels
    dtype: str  # NumPy dtype string

# Audio configuration
@dataclass
class AudioConfig:
    device_index: int = None
    sample_rate: int = 16000
    channels: int = 1
    dtype: np.dtype = np.int16
    blocksize: int = 1024
    latency: float = 0.1

# Simplified MicInputService for testing
class TestMicInputService:
    def __init__(self, event_bus, config: Optional[Dict[str, Any]] = None):
        self.event_bus = event_bus
        self.logger = logging.getLogger("test_mic_input_service")
        
        # Audio configuration
        self._config = self._load_config(config or {})
        
        # Audio capture state
        self._stream = None
        self._audio_queue = asyncio.Queue(maxsize=100)
        self._is_capturing = False
        self._processing_task = None
        
        # Debug stats
        self._chunk_count = 0
        self._max_amplitude = 0
        self._rms_levels = []
        
        # Store loop reference for audio callback
        self._loop = asyncio.get_event_loop()
        
    def _load_config(self, config: Dict[str, Any]) -> AudioConfig:
        return AudioConfig(
            device_index=config.get("AUDIO_DEVICE_INDEX", None),
            sample_rate=config.get("AUDIO_SAMPLE_RATE", 16000),
            channels=config.get("AUDIO_CHANNELS", 1),
            blocksize=config.get("AUDIO_BLOCKSIZE", 1024),
            latency=config.get("AUDIO_LATENCY", 0.1)
        )
        
    async def initialize(self):
        """Initialize audio resources."""
        try:
            # Verify audio device is available
            devices = sd.query_devices()
            self.logger.info(f"Available audio devices: {len(devices)}")
            
            for i, device in enumerate(devices):
                max_input = device.get('max_input_channels', 0)
                if max_input > 0:
                    self.logger.info(f"Input device {i}: {device['name']} ({max_input} channels)")
            
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
            
            # Create debug file if needed
            self._debug_file = None
            if os.environ.get("SAVE_DEBUG_AUDIO", "0") == "1":
                self._debug_file = open("debug_audio_raw.bin", "wb")
                self.logger.info("Debug audio file enabled: debug_audio_raw.bin")
                
            return True
                
        except Exception as e:
            self.logger.error(f"Failed to initialize audio: {str(e)}")
            return False
            
    async def cleanup(self):
        """Clean up audio resources."""
        try:
            await self.stop_capture()
            
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
                
            if self._debug_file:
                self._debug_file.close()
                self._debug_file = None
                
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
                rms = np.sqrt(np.mean(indata**2))
                
                if max_amp > self._max_amplitude:
                    self._max_amplitude = max_amp
                
                self._rms_levels.append(rms)
                
                # Log occasionally (every ~20 chunks)
                if self._chunk_count % 20 == 0:
                    self.logger.debug(f"Audio chunk {self._chunk_count}: max_amp={max_amp:.6f}, rms={rms:.6f}")
                
                self._chunk_count += 1
                
                # Get time from PortAudio timeinfo structure
                current_time = time.inputBufferAdcTime if hasattr(time, 'inputBufferAdcTime') else time.currentTime
                
                # Store raw audio if debug file enabled
                if self._debug_file:
                    self._debug_file.write(indata.tobytes())
                    self._debug_file.flush()
                
                # Use the stored event loop
                if self._loop and not self._loop.is_closed():
                    asyncio.run_coroutine_threadsafe(
                        self._audio_queue.put((indata.copy(), current_time)),
                        self._loop
                    )
                else:
                    self.logger.warning("Event loop is not available or closed")
                    
        except Exception as e:
            self.logger.error(f"Error in audio callback: {str(e)}")

    async def _process_audio(self):
        """Process audio data from the queue and emit events."""
        self.logger.info("Starting audio processing task")
        chunks_processed = 0
        
        try:
            while self._is_capturing:
                try:
                    # Use wait_for to make queue.get cancellable
                    data, timestamp = await asyncio.wait_for(
                        self._audio_queue.get(),
                        timeout=0.1
                    )
                    
                    # Convert numpy array to bytes
                    samples_bytes = data.tobytes()
                    
                    # Create and emit event
                    payload = AudioChunkPayload(
                        samples=samples_bytes,
                        timestamp=timestamp,
                        sample_rate=self._config.sample_rate,
                        channels=self._config.channels,
                        dtype=str(self._config.dtype)
                    )
                    
                    # Emit event
                    self.event_bus.emit(EventTopics.AUDIO_RAW_CHUNK, vars(payload))
                    
                    chunks_processed += 1
                    if chunks_processed % 20 == 0:
                        self.logger.debug(f"Processed {chunks_processed} audio chunks")
                    
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
            self.logger.info(f"Audio processing task stopped. Processed {chunks_processed} chunks.")
        
    async def start_capture(self):
        """Start audio capture."""
        if self._is_capturing:
            self.logger.warning("Audio capture already active")
            return
            
        try:
            if not self._stream:
                self.logger.error("Audio stream not initialized")
                return
                
            self._is_capturing = True
            self._chunk_count = 0
            self._max_amplitude = 0
            self._rms_levels = []
            
            self._stream.start()
            
            # Start processing task
            self._processing_task = asyncio.create_task(self._process_audio())
            
            self.logger.info("Started audio capture")
            
        except Exception as e:
            self._is_capturing = False
            self.logger.error(f"Failed to start audio capture: {str(e)}")
            
    async def stop_capture(self):
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
                
            # Log audio statistics
            if self._rms_levels:
                avg_rms = sum(self._rms_levels) / len(self._rms_levels)
                self.logger.info(f"Audio capture summary:")
                self.logger.info(f"  Chunks processed: {self._chunk_count}")
                self.logger.info(f"  Max amplitude: {self._max_amplitude:.6f}")
                self.logger.info(f"  Average RMS: {avg_rms:.6f}")
                self.logger.info(f"  Is signal present: {'YES' if self._max_amplitude > 0.01 else 'NO - very quiet'}")
            
            self.logger.info("Stopped audio capture")
            
        except Exception as e:
            self.logger.error(f"Error stopping audio capture: {str(e)}")

    async def handle_listening_started(self, payload):
        """Handle voice listening started event."""
        self.logger.info("Voice listening started event received")
        await self.start_capture()
        
    async def handle_listening_stopped(self, payload):
        """Handle voice listening stopped event."""
        self.logger.info("Voice listening stopped event received")
        await self.stop_capture()

# Audio chunk handler for testing
async def handle_audio_chunk(payload):
    global audio_chunks_received
    audio_chunks_received += 1
    if audio_chunks_received % 20 == 0:
        logger.debug(f"Received {audio_chunks_received} audio chunks")

# Main test function
async def main():
    global audio_chunks_received
    audio_chunks_received = 0
    
    logger.info("Starting MicInputService test")
    
    # Create event bus
    event_bus = MockEventBus()
    
    # Register audio chunk handler
    await event_bus.on(EventTopics.AUDIO_RAW_CHUNK, handle_audio_chunk)
    
    # Create MicInputService
    mic_service = TestMicInputService(event_bus)
    
    # Initialize
    if not await mic_service.initialize():
        logger.error("Failed to initialize MicInputService")
        return
        
    try:
        # Register event handlers
        await event_bus.on(EventTopics.VOICE_LISTENING_STARTED, mic_service.handle_listening_started)
        await event_bus.on(EventTopics.VOICE_LISTENING_STOPPED, mic_service.handle_listening_stopped)
        
        # Start recording
        logger.info("Initiating recording...")
        event_bus.emit(EventTopics.VOICE_LISTENING_STARTED, {})
        
        # Record for 5 seconds
        logger.info("Recording for 5 seconds. Please speak into the microphone...")
        await asyncio.sleep(5)
        
        # Stop recording
        logger.info("Stopping recording...")
        event_bus.emit(EventTopics.VOICE_LISTENING_STOPPED, {})
        
        # Wait a moment for processing to finish
        await asyncio.sleep(1)
        
        # Report results
        logger.info(f"Test completed. Audio chunks received: {audio_chunks_received}")
        logger.info(f"Event statistics: {event_bus.get_event_count()} total events")
        logger.info(f"Audio chunks events: {event_bus.get_event_count(EventTopics.AUDIO_RAW_CHUNK)}")
        
    finally:
        # Clean up
        await mic_service.cleanup()
        logger.info("Test completed")

if __name__ == "__main__":
    # Run the test
    asyncio.run(main()) 