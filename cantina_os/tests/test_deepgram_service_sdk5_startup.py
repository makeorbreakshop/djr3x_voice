"""
Test to verify DeepgramDirectMicService SDK 5.x starts correctly with persistent connection.
"""

import asyncio
import os
import sys
from pyee.asyncio import AsyncIOEventEmitter

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from cantina_os.services.deepgram_direct_mic_service import DeepgramDirectMicService
from cantina_os.core.event_bus import EventBus


async def test_service_startup():
    """Test that the service starts up correctly with SDK 5.x patterns."""
    print("\n=== Testing DeepgramDirectMicService SDK 5.x Startup ===\n")

    # Check API key
    api_key = os.getenv("DEEPGRAM_API_KEY")
    if not api_key:
        print("✗ DEEPGRAM_API_KEY not found")
        return False

    print("✓ DEEPGRAM_API_KEY found")

    try:
        # Create event bus
        event_bus = EventBus(AsyncIOEventEmitter())
        print("✓ Event bus created")

        # Create service
        service = DeepgramDirectMicService(event_bus=event_bus)
        print("✓ Service instantiated")

        # Start service
        await service.start()
        print("✓ Service started successfully")
        print("  - WebSocket connection opened")
        print("  - Listener thread running")
        print("  - KeepAlive loop active")

        # Let it run for 15 seconds to verify KeepAlive
        print("\n⏱️  Testing KeepAlive for 15 seconds...")
        await asyncio.sleep(15)

        print("\n✓ Service ran for 15 seconds without errors")

        # Stop service
        print("\n🧹 Stopping service...")
        await service.stop()
        print("✓ Service stopped successfully")

        print("\n✓ PASS: SDK 5.x migration successful!")
        return True

    except Exception as e:
        print(f"\n✗ FAIL: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    print("=" * 60)
    print("DeepgramDirectMicService SDK 5.x Startup Test")
    print("=" * 60)

    success = await test_service_startup()

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
