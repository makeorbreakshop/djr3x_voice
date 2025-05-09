"""
Audio Pipeline Test Utility

This script tests the event flow between MicInputService and DeepgramTranscriptionService
to verify audio chunks are being properly sent and received.

Usage:
python test_audio_pipeline.py [--use-deepgram]
"""

import asyncio
import logging
from pyee.asyncio import AsyncIOEventEmitter
import time
import os
import argparse

# CantinaOS imports
from cantina_os.services.mic_input_service import MicInputService
from cantina_os.services.deepgram_transcription_service import DeepgramTranscriptionService
from cantina_os.event_topics import EventTopics
from cantina_os.event_payloads import (
    BaseEventPayload,
    ServiceStatus
)

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("audio_pipeline_test")

# Counter for events
class EventCounter:
    def __init__(self):
        self.audio_chunks_emitted = 0
        self.audio_chunks_received = 0
        self.transcription_events = 0
        
    def __str__(self):
        return (
            f"Audio Chunks Emitted: {self.audio_chunks_emitted}\n"
            f"Audio Chunks Received: {self.audio_chunks_received}\n"
            f"Transcription Events: {self.transcription_events}"
        )

async def main(use_deepgram=False):
    """Run the audio pipeline test."""
    logger.info("Starting audio pipeline test")
    
    # Create event bus
    event_bus = AsyncIOEventEmitter()
    counter = EventCounter()
    
    # Set up event tracking
    @event_bus.on(EventTopics.AUDIO_RAW_CHUNK)
    async def on_audio_chunk(payload):
        counter.audio_chunks_emitted += 1
        if counter.audio_chunks_emitted % 10 == 0:
            logger.info(f"Audio chunks emitted: {counter.audio_chunks_emitted}")
    
    @event_bus.on(EventTopics.TRANSCRIPTION_INTERIM)
    async def on_transcription_interim(payload):
        counter.transcription_events += 1
        logger.info(f"Interim transcript: {payload}")
        
    @event_bus.on(EventTopics.TRANSCRIPTION_FINAL)
    async def on_transcription_final(payload):
        counter.transcription_events += 1
        logger.info(f"Final transcript: {payload}")
    
    @event_bus.on(EventTopics.SERVICE_STATUS_UPDATE)
    async def on_service_status(payload):
        logger.info(f"Service status: {payload}")
        
    # Initialize services
    config = {
        "AUDIO_SAMPLE_RATE": 16000,
        "AUDIO_CHANNELS": 1,
    }
    
    # Add Deepgram API key if using real transcription
    if use_deepgram:
        deepgram_api_key = os.environ.get("DEEPGRAM_API_KEY")
        if not deepgram_api_key:
            logger.error("DEEPGRAM_API_KEY environment variable not set")
            return None
        config["DEEPGRAM_API_KEY"] = deepgram_api_key
        logger.info("Using real Deepgram transcription")
    else:
        config["DEEPGRAM_API_KEY"] = "dummy_key_for_testing"
        logger.info("Using mock Deepgram (won't connect to API)")
    
    mic_service = MicInputService(event_bus, config)
    deepgram_service = DeepgramTranscriptionService(event_bus, config)
    
    # Track audio chunks received by DeepgramTranscriptionService
    original_handle_audio_chunk = deepgram_service._handle_audio_chunk
    
    async def wrapped_handle_audio_chunk(payload):
        counter.audio_chunks_received += 1
        if counter.audio_chunks_received % 10 == 0:
            logger.info(f"Audio chunks received: {counter.audio_chunks_received}")
        await original_handle_audio_chunk(payload)
        
    deepgram_service._handle_audio_chunk = wrapped_handle_audio_chunk
    
    # Start services
    await mic_service.start()
    await deepgram_service.start()
    
    # Start voice listening
    logger.info("Starting voice listening")
    await event_bus.emit(EventTopics.VOICE_LISTENING_STARTED, {"source": "test"})
    
    # Run for duration based on whether using real Deepgram or not
    duration = 20 if use_deepgram else 10
    for i in range(duration):
        logger.info(f"Test running... {i+1}/{duration} seconds")
        await asyncio.sleep(1)
        logger.info(str(counter))
    
    # Stop voice listening
    logger.info("Stopping voice listening")
    await event_bus.emit(EventTopics.VOICE_LISTENING_STOPPED, {"source": "test"})
    
    # Allow time for processing to complete
    await asyncio.sleep(1)
    
    # Stop services
    await deepgram_service.stop()
    await mic_service.stop()
    
    # Print final results
    logger.info("Test completed!")
    logger.info(str(counter))
    
    if counter.audio_chunks_emitted > 0 and counter.audio_chunks_received > 0:
        logger.info("✅ SUCCESS: Audio chunks are being correctly passed between services")
    else:
        logger.error("❌ FAILURE: Audio chunks are NOT being correctly passed between services")
        
    return counter

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test audio pipeline event flow")
    parser.add_argument('--use-deepgram', action='store_true', help='Use real Deepgram API for transcription')
    args = parser.parse_args()
    
    asyncio.run(main(args.use_deepgram)) 