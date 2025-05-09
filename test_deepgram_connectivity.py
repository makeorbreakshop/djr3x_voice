#!/usr/bin/env python3
"""
Deepgram Connectivity Test

This script tests if the Deepgram transcription service is properly connected
and receiving audio events from the mic input service.

Usage:
python test_deepgram_connectivity.py
"""

import asyncio
import logging
import sys
import os
from dotenv import load_dotenv
from pyee.asyncio import AsyncIOEventEmitter

# Set up logging
logging.basicConfig(level=logging.INFO,
                   format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("deepgram_test")

# Import from cantina_os
sys.path.append(".")
from cantina_os.event_topics import EventTopics
from cantina_os.services.mic_input_service import MicInputService
from cantina_os.services.deepgram_transcription_service import DeepgramTranscriptionService
from cantina_os.event_payloads import AudioChunkPayload

async def main():
    """Main test function."""
    # Load environment variables
    load_dotenv()
    
    # Check for Deepgram API key
    api_key = os.getenv("DEEPGRAM_API_KEY")
    if not api_key or api_key == "your_api_key_here":
        logger.error(
            "\nDeepgram API key not found!\n"
            "Please follow these steps:\n"
            "1. Get your API key from https://console.deepgram.com\n"
            "2. Create a .env file in the project root\n"
            "3. Add the following line to .env:\n"
            "   DEEPGRAM_API_KEY=your_api_key_here\n"
            "   (replace your_api_key_here with your actual API key)\n"
        )
        return
    
    # Create event bus
    event_bus = AsyncIOEventEmitter()
    
    # Create services
    config = {
        "DEEPGRAM_API_KEY": api_key,
        "SAMPLE_RATE": 16000,
        "CHANNELS": 1,
        "DTYPE": "int16",
        "BLOCKSIZE": 1024,
        "LATENCY": 0.1
    }
    
    logger.info("Creating services...")
    mic_service = MicInputService(event_bus, config)
    deepgram_service = DeepgramTranscriptionService(event_bus, config)
    
    # Start services
    logger.info("Starting services...")
    await mic_service.start()
    await deepgram_service.start()
    
    # Subscribe to transcription results
    async def handle_transcript(payload):
        text = payload.get("text", "")
        is_final = payload.get("is_final", False)
        logger.info(f"Transcript {'(final)' if is_final else '(interim)'}: {text}")
    
    event_bus.on(EventTopics.TRANSCRIPTION_FINAL, handle_transcript)
    event_bus.on(EventTopics.TRANSCRIPTION_INTERIM, handle_transcript)
    
    try:
        # Run for 30 seconds
        logger.info("\nStarting test - will run for 30 seconds...")
        logger.info("Speak into your microphone to test transcription\n")
        await asyncio.sleep(30)
        
    except KeyboardInterrupt:
        logger.info("\nTest interrupted by user")
        
    finally:
        # Stop services
        logger.info("\nStopping services...")
        await mic_service.stop()
        await deepgram_service.stop()
        logger.info("Test completed")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("\nTest interrupted by user")
    except Exception as e:
        logger.error(f"Test failed: {e}", exc_info=True) 