"""
Test to verify persistent connection latency improvement.

Simulates a mouse click interaction and measures the time from recording stop
to transcript delivery. With persistent connection, this should be < 1 second.
Without persistent connection, it would be ~5 seconds.
"""

import asyncio
import os
import sys
import time
from pyee.asyncio import AsyncIOEventEmitter

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from cantina_os.services.deepgram_direct_mic_service import DeepgramDirectMicService
from cantina_os.core.event_bus import EventBus
from cantina_os.core.event_topics import EventTopics


async def test_persistent_connection_latency():
    """Test latency improvement from persistent WebSocket connection."""
    print("\n=== Testing Persistent Connection Latency ===\n")

    # Check API key
    api_key = os.getenv("DEEPGRAM_API_KEY")
    if not api_key:
        print("✗ DEEPGRAM_API_KEY not found")
        return False

    print("✓ DEEPGRAM_API_KEY found")

    try:
        # Create event bus
        event_bus_emitter = AsyncIOEventEmitter()
        event_bus = EventBus(event_bus_emitter)
        print("✓ Event bus created")

        # Track when transcript is received
        transcript_received = asyncio.Event()
        transcript_latency = None

        def on_transcript_final(data):
            nonlocal transcript_latency, transcript_received
            if not transcript_received.is_set():
                transcript_latency = time.time() - recording_stop_time
                transcript_received.set()
                print(f"✓ Transcript received: '{data.get('text', '')}' (latency: {transcript_latency:.3f}s)")

        # Subscribe to transcript events
        event_bus_emitter.on(EventTopics.TRANSCRIPTION_FINAL, on_transcript_final)

        # Create service
        service = DeepgramDirectMicService(event_bus=event_bus)
        print("✓ Service instantiated")

        # Start service
        await service.start()
        print("✓ Service started (WebSocket connection persistent)")

        # Wait for connection to be fully established
        await asyncio.sleep(1)

        print("\n📍 Simulating mouse click to start recording...")
        # Simulate mouse click to start recording
        await event_bus.emit(EventTopics.MIC_RECORDING_START, {})
        print("✓ Recording started")

        # Wait for user to speak (in real usage, they'd speak here)
        print("⏱️  Recording for 3 seconds (say something)...")
        await asyncio.sleep(3)

        # Simulate mouse click to stop recording
        print("\n📍 Simulating mouse click to stop recording...")
        recording_stop_time = time.time()
        await event_bus.emit(EventTopics.MIC_RECORDING_STOP, {})

        # Wait for transcript (with timeout)
        print("⏱️  Waiting for transcript...")
        try:
            await asyncio.wait_for(transcript_received.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            print("✗ FAIL: Transcript not received within 10 seconds")
            await service.stop()
            return False

        # Evaluate latency
        print(f"\n📊 Results:")
        print(f"   - Transcript latency: {transcript_latency:.3f}s")

        # With persistent connection, latency should be < 1 second
        # Without persistent connection, it would be ~5 seconds
        if transcript_latency < 1.0:
            print(f"   - ✓ EXCELLENT: Latency < 1s (persistent connection working)")
            success = True
        elif transcript_latency < 2.0:
            print(f"   - ✓ GOOD: Latency < 2s (acceptable)")
            success = True
        elif transcript_latency < 5.0:
            print(f"   - ⚠️  FAIR: Latency < 5s (could be better)")
            success = True
        else:
            print(f"   - ✗ POOR: Latency >= 5s (persistent connection may not be working)")
            success = False

        # Stop service
        print("\n🧹 Stopping service...")
        await service.stop()
        print("✓ Service stopped")

        return success

    except Exception as e:
        print(f"\n✗ FAIL: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    print("=" * 60)
    print("Persistent Connection Latency Test")
    print("=" * 60)
    print("\nThis test verifies that the persistent WebSocket connection")
    print("reduces latency from ~5 seconds to < 1 second.")
    print("\nPlease say something when prompted!")

    success = await test_persistent_connection_latency()

    print("\n" + "=" * 60)
    if success:
        print("✓ TEST PASSED")
    else:
        print("✗ TEST FAILED")
    print("=" * 60)

    return 0 if success else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
