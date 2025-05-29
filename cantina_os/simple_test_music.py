"""
Simple test script for MusicControllerService.

This script tests the basic functionality of the MusicControllerService
in isolation, including:
- Music library loading
- Basic playback control
- Mode changes
- Audio ducking
"""

import os
import asyncio
import logging
from pathlib import Path
from pyee.asyncio import AsyncIOEventEmitter

from cantina_os.services.music_controller_service import MusicControllerService
from cantina_os.core.event_topics import EventTopics
from cantina_os.event_payloads import (
    MusicCommandPayload,
    BaseEventPayload,
    SystemModePayload
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Event handler for music state changes
async def on_music_state_change(payload):
    logger.info("Music state changed")

# Event handler for music errors
async def on_music_error(payload):
    logger.error("Music error occurred")

async def main():
    try:
        # Create event bus
        event_bus = AsyncIOEventEmitter()
        
        # Subscribe to music events
        event_bus.on(EventTopics.MUSIC_STATE_CHANGE, on_music_state_change)
        event_bus.on(EventTopics.MUSIC_ERROR, on_music_error)
        
        # Create test music directory and sample files
        music_dir = Path("test_assets/music")
        music_dir.mkdir(parents=True, exist_ok=True)
        
        # Create some test audio files (you should add real audio files here)
        test_tracks = {
            "cantina_band": "cantina_band.mp3",
            "imperial_march": "imperial_march.mp3"
        }
        for filename in test_tracks.values():
            (music_dir / filename).touch()
            
        # Initialize service
        logger.info("Initializing MusicControllerService...")
        service = MusicControllerService(
            event_bus=event_bus,
            music_dir=str(music_dir)
        )
        
        # Start service
        logger.info("Starting service...")
        await service.start()
        
        # Test track listing
        logger.info("Available tracks:")
        for track in service.get_track_list():
            logger.info(f"- {track['name']} (duration: {track['duration']}s)")
            
        # Test mode changes
        logger.info("\nTesting mode changes...")
        await service._handle_mode_change(
            SystemModePayload(
                mode="AMBIENT",
                conversation_id="test"
            )
        )
        logger.info(f"Current mode: {service.current_mode}")
        
        # Test playback
        logger.info("\nTesting playback...")
        await service._handle_play_request(
            MusicCommandPayload(
                action="play",
                song_query="cantina",
                conversation_id="test"
            )
        )
        logger.info("Waiting 3 seconds...")
        await asyncio.sleep(3)
        
        # Test audio ducking
        logger.info("\nTesting audio ducking...")
        service.current_mode = "INTERACTIVE"
        await service._handle_speech_start(
            BaseEventPayload(conversation_id="test")
        )
        logger.info("Volume ducked, waiting 2 seconds...")
        await asyncio.sleep(2)
        
        await service._handle_speech_end(
            BaseEventPayload(conversation_id="test")
        )
        logger.info("Volume restored, waiting 2 seconds...")
        await asyncio.sleep(2)
        
        # Test stop
        logger.info("\nTesting stop...")
        await service._handle_stop_request(
            MusicCommandPayload(
                action="stop",
                conversation_id="test"
            )
        )
        logger.info("Playback stopped")
        
        # Cleanup
        logger.info("\nStopping service...")
        await service.stop()
        logger.info("Test complete!")
        
    except Exception as e:
        logger.error(f"Error during test: {e}")
        raise
        
if __name__ == "__main__":
    asyncio.run(main()) 