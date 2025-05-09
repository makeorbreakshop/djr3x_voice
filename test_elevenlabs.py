#!/usr/bin/env python3
"""
Test script for ElevenLabs service functionality
"""

import asyncio
import os
import logging
from pyee.asyncio import AsyncIOEventEmitter
from cantina_os.services.elevenlabs_service import ElevenLabsService, SpeechPlaybackMethod
from cantina_os.event_payloads import (
    SpeechGenerationRequestPayload,
    SpeechGenerationCompletePayload,
    ServiceStatus
)
from cantina_os.event_topics import EventTopics

async def test_elevenlabs():
    """Test ElevenLabs service functionality."""
    # Initialize service with API key from environment
    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        print("Error: ELEVENLABS_API_KEY environment variable not set")
        return False

    # Create event bus and logger
    event_bus = AsyncIOEventEmitter()
    logger = logging.getLogger("test_elevenlabs")
    logger.setLevel(logging.INFO)
    
    # Create service instance with required parameters
    service = ElevenLabsService(
        event_bus=event_bus,
        api_key=api_key,
        voice_id=os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM"),
        model_id=os.getenv("ELEVENLABS_MODEL_ID", "eleven_turbo_v2"),
        playback_method=SpeechPlaybackMethod.SYSTEM,  # Use system playback for testing
        name="elevenlabs_service",
        logger=logger
    )
    
    try:
        # Start the service
        print("Starting ElevenLabs service...")
        await service.start()
        print(f"Service status: {service.status}")
        
        if service.status != ServiceStatus.RUNNING:
            print("Error: Service failed to start properly")
            return False
            
        # Create a test request payload
        payload = SpeechGenerationRequestPayload(
            text="Hello! I am DJ R3X, and I'm testing my voice synthesis capabilities!",
            conversation_id="test-synthesis"
        )
        
        # Set up event tracking
        generation_complete = asyncio.Event()
        generation_success = False
        generation_error = None
        
        async def handle_generation_complete(event_data):
            nonlocal generation_success, generation_error
            try:
                # Convert dict to Pydantic model
                event_payload = SpeechGenerationCompletePayload.parse_obj(event_data)
                generation_success = event_payload.success
                generation_error = event_payload.error
                print(f"Speech generation complete: {event_payload.success}")
                if event_payload.error:
                    print(f"Error: {event_payload.error}")
            except Exception as e:
                print(f"Error handling event: {e}")
                generation_success = False
                generation_error = str(e)
            finally:
                generation_complete.set()
            
        # Subscribe to completion event
        service.subscribe(
            EventTopics.SPEECH_GENERATION_COMPLETE,
            handle_generation_complete
        )
        
        # Send speech generation request
        print("Generating speech...")
        await service._handle_speech_generation_request(payload)
        
        # Wait for completion with timeout
        try:
            await asyncio.wait_for(generation_complete.wait(), timeout=30.0)
            if generation_success:
                print("Speech generation and playback completed successfully")
            else:
                print(f"Speech generation failed: {generation_error}")
            return generation_success
            
        except asyncio.TimeoutError:
            print("Error: Speech generation timed out")
            return False
            
    except Exception as e:
        print(f"Error during test: {e}")
        return False
        
    finally:
        # Stop the service
        print("Stopping service...")
        await service.stop()
        print("Service stopped")

def main():
    """Run the test."""
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    print("Starting ElevenLabs service test...")
    success = asyncio.run(test_elevenlabs())
    print(f"\nTest {'passed' if success else 'failed'}")
    return 0 if success else 1

if __name__ == "__main__":
    exit(main()) 