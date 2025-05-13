#!/usr/bin/env python3
"""
Deepgram Debug Test

This script adds additional logging to test if audio is being captured 
and properly sent to Deepgram for transcription.

Usage:
python test_deepgram_debug.py
"""

import asyncio
import logging
import sys
import os
import time
from dotenv import load_dotenv
from pyee.asyncio import AsyncIOEventEmitter

# Set up logging with DEBUG level for more verbose output
logging.basicConfig(level=logging.DEBUG,
                   format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("deepgram_debug")

# Set specific loggers to DEBUG
logging.getLogger('cantina_os.mic_input').setLevel(logging.DEBUG)
logging.getLogger('cantina_os.deepgram_transcription').setLevel(logging.DEBUG)

# Import from cantina_os
sys.path.append(".")
from cantina_os.event_topics import EventTopics
from cantina_os.services.mic_input_service import MicInputService
from cantina_os.services.deepgram_transcription_service import DeepgramTranscriptionService
from cantina_os.event_payloads import AudioChunkPayload

class AudioDebugCounter:
    def __init__(self):
        self.audio_chunks_emitted = 0
        self.audio_chunks_received_by_deepgram = 0
        self.transcription_events = 0
        self.last_log_time = time.time()
        
    def log_status(self):
        logger.info(f"Audio stats: Emitted={self.audio_chunks_emitted}, Received by Deepgram={self.audio_chunks_received_by_deepgram}, Transcriptions={self.transcription_events}")

async def main():
    """Main test function with enhanced debugging."""
    # Load environment variables
    load_dotenv()
    
    # Check for Deepgram API key
    api_key = os.getenv("DEEPGRAM_API_KEY")
    if not api_key:
        logger.error("\nDeepgram API key not found in .env file!")
        return
        
    logger.info(f"Using Deepgram API key: {api_key[:5]}...{api_key[-5:]}")
    
    # Create event bus
    event_bus = AsyncIOEventEmitter()
    counter = AudioDebugCounter()
    
    # Create services
    config = {
        "DEEPGRAM_API_KEY": api_key,
        "SAMPLE_RATE": 16000,
        "CHANNELS": 1,
        "DTYPE": "int16",
        "BLOCKSIZE": 1024,
        "LATENCY": 0.1,
        "DEEP_DEBUG": True  # Enable deep debugging
    }
    
    logger.info("Creating services...")
    mic_service = MicInputService(event_bus, config)
    deepgram_service = DeepgramTranscriptionService(event_bus, config)
    
    # Add tracking for audio chunks
    @event_bus.on(EventTopics.AUDIO_RAW_CHUNK)
    async def on_audio_chunk(payload):
        counter.audio_chunks_emitted += 1
        if counter.audio_chunks_emitted % 10 == 0:
            logger.debug(f"Audio chunks emitted: {counter.audio_chunks_emitted}")
            counter.log_status()
    
    # Monkey patch the Deepgram service to track audio chunk reception
    original_handle_audio_chunk = deepgram_service._handle_audio_chunk
    
    async def wrapped_handle_audio_chunk(payload):
        counter.audio_chunks_received_by_deepgram += 1
        if counter.audio_chunks_received_by_deepgram % 10 == 0:
            logger.debug(f"Deepgram received chunk #{counter.audio_chunks_received_by_deepgram}")
        return await original_handle_audio_chunk(payload)
        
    deepgram_service._handle_audio_chunk = wrapped_handle_audio_chunk
    
    # Start services
    logger.info("Starting services...")
    await mic_service.start()
    await deepgram_service.start()
    
    # Subscribe to transcription results
    async def handle_transcript(payload):
        counter.transcription_events += 1
        text = payload.get("text", "")
        is_final = payload.get("is_final", False)
        logger.info(f"Transcript {'(final)' if is_final else '(interim)'}: {text}")
    
    event_bus.on(EventTopics.TRANSCRIPTION_FINAL, handle_transcript)
    event_bus.on(EventTopics.TRANSCRIPTION_INTERIM, handle_transcript)
    
    # Start audio capture explicitly
    logger.info("Starting audio capture...")
    await event_bus.emit(EventTopics.VOICE_LISTENING_STARTED, {"source": "test"})
    
    try:
        # Run for 30 seconds with periodic status updates
        logger.info("\nStarting test - will run for 30 seconds...")
        logger.info("Speak into your microphone to test transcription\n")
        
        for i in range(30):
            await asyncio.sleep(1)
            if i % 5 == 0:  # Log stats every 5 seconds
                counter.log_status()
                logger.info(f"Test running... {i+1}/30 seconds")
        
    except KeyboardInterrupt:
        logger.info("\nTest interrupted by user")
        
    finally:
        # Stop audio capture explicitly
        logger.info("Stopping audio capture...")
        await event_bus.emit(EventTopics.VOICE_LISTENING_STOPPED, {"source": "test"})
        
        # Allow time for processing to complete
        await asyncio.sleep(1)
        
        # Stop services
        logger.info("\nStopping services...")
        await mic_service.stop()
        await deepgram_service.stop()
        
        # Report final stats
        logger.info("\nFinal Audio Pipeline Statistics:")
        counter.log_status()
        
        # Analyze results
        if counter.audio_chunks_emitted == 0:
            logger.error("❌ No audio chunks were emitted - microphone may not be capturing audio")
        elif counter.audio_chunks_received_by_deepgram == 0:
            logger.error("❌ Audio chunks were emitted but not received by Deepgram - check event subscriptions")
        elif counter.transcription_events == 0:
            logger.error("❌ Audio was sent to Deepgram but no transcriptions were returned - check audio quality or Deepgram connection")
        else:
            logger.info("✅ Audio pipeline appears to be working correctly")
            
        logger.info("Test completed")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("\nTest interrupted by user")
    except Exception as e:
        logger.error(f"Test failed: {e}", exc_info=True) 