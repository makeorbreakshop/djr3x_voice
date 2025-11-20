#!/usr/bin/env python3
"""
Test script to verify vision context is passed to Claude

This test works differently than the main CantinaOS flow:
1. Initializes services directly without running the full event loop
2. Manually triggers vision capture
3. Simulates a user query via event emission
4. Monitors for Claude's response containing scene description
"""

import asyncio
import sys
import time
import os
import logging

# Add the parent directory to the path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from cantina_os.main import CantinaOS
from cantina_os.core.event_topics import EventTopics

async def test_vision_context():
    """Test that vision context gets to Claude"""

    # Create the system (but don't run it - we'll control manually)
    system = CantinaOS()

    # Flag to track if we got a response
    got_response = False

    def on_llm_response(payload):
        nonlocal got_response
        got_response = True
        print(f"\n🤖 Claude Response: {payload.get('text', 'N/A')[:200]}...")

    def on_vision_captured(payload):
        print(f"\n📷 Vision captured: {payload.get('description', 'N/A')[:100]}...")

    try:
        # Initialize services manually
        print("\n🚀 Initializing services...")
        await system._initialize_services()

        # Check which services are loaded
        print(f"\n📋 Loaded services: {list(system._services.keys())}")

        # Check if Claude service is present
        if "claude" in system._services:
            print("✅ Claude service is loaded")
        else:
            print("⚠️  Claude service NOT loaded - using GPT instead?")

        # Subscribe to events we care about
        system._event_bus.on(EventTopics.VISION_SCENE_CAPTURED, on_vision_captured)
        system._event_bus.on(EventTopics.LLM_RESPONSE_TEXT, on_llm_response)

        # Trigger vision capture
        print("\n📸 Triggering vision startup capture...")
        system._event_bus.emit(EventTopics.VISION_STARTUP_CAPTURE, {})

        # Wait for vision to capture and analyze
        await asyncio.sleep(5)

        print("\n🎤 Simulating user query: 'What do you see around you?'")

        # Emit a transcription event targeting Claude service
        system._event_bus.emit(
            EventTopics.TRANSCRIPTION_FINAL,
            {
                "text": "What do you see around you?",
                "confidence": 0.95,
                "timestamp": time.time(),
                "conversation_id": "test-vision-context-123"
            }
        )

        # Wait for Claude to process and respond
        print("\n⏳ Waiting for Claude's response...")
        await asyncio.sleep(10)

        if got_response:
            print("\n✅ Test complete! Claude responded to the query.")
            print("Check above to see if the response mentions the scene.")
        else:
            print("\n⚠️  No response from Claude detected.")
            print("Check logs to see if events were processed correctly.")

    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")
    except Exception as e:
        print(f"\n❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Cleanup services (skip log_listener since we didn't start it)
        print("\n🧹 Cleaning up services...")
        try:
            # Stop services in reverse order
            for service_name in reversed(list(system._services.keys())):
                service = system._services[service_name]
                print(f"Stopping {service_name}...")
                await service.stop()
        except Exception as cleanup_error:
            print(f"Error during cleanup: {cleanup_error}")
        print("\n🔚 Test complete")

if __name__ == "__main__":
    # Set up better logging to see what's happening
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s.%(msecs)03d %(name)-20s %(levelname)-8s %(message)s',
        datefmt='%H:%M:%S'
    )

    asyncio.run(test_vision_context())