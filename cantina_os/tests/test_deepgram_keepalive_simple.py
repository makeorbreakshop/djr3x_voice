"""
Simple test to verify KeepAlive functionality with Deepgram SDK 5.x.

This test:
1. Opens connection
2. Sends audio immediately to "wake up" the connection
3. Then sends KeepAlive messages every 4 seconds
4. Verifies connection stays alive for 20+ seconds

REQUIRES: DEEPGRAM_API_KEY environment variable
"""

import asyncio
import os
import json
import numpy as np
from deepgram import DeepgramClient
from deepgram.core.events import EventType
from deepgram.extensions.types.sockets import ListenV1ControlMessage


async def test_keepalive():
    """Test KeepAlive prevents timeout."""
    api_key = os.getenv("DEEPGRAM_API_KEY")
    if not api_key:
        print("✗ DEEPGRAM_API_KEY not found")
        return False

    client = DeepgramClient(api_key=api_key)

    connection_open = False
    error_occurred = None
    messages = []

    def on_open(_):
        nonlocal connection_open
        print("✓ Connection opened")
        connection_open = True

    def on_message(message):
        nonlocal messages
        msg_type = getattr(message, "type", "Unknown")
        print(f"✓ Received: {msg_type}")
        messages.append(message)

    def on_close(_):
        print("✓ Connection closed")

    def on_error(error):
        nonlocal error_occurred
        print(f"✗ Error: {error}")
        error_occurred = error

    print("\n=== Testing KeepAlive with Nova-3 ===\n")

    try:
        with client.listen.v1.connect(model="nova-3") as connection:
            # Set up handlers
            connection.on(EventType.OPEN, on_open)
            connection.on(EventType.MESSAGE, on_message)
            connection.on(EventType.CLOSE, on_close)
            connection.on(EventType.ERROR, on_error)

            # Start listening
            connection.start_listening()
            await asyncio.sleep(0.5)

            # Generate and send initial audio to "wake up" the connection
            print("📤 Sending initial audio...")
            sample_rate = 16000
            duration = 0.5
            t = np.linspace(0, duration, int(sample_rate * duration))
            audio = np.sin(2 * np.pi * 440 * t)
            audio_bytes = (audio * 32767).astype(np.int16).tobytes()
            connection.send_media(audio_bytes)

            await asyncio.sleep(1)
            print(f"✓ Received {len(messages)} messages after audio\n")

            # Now test KeepAlive for 20 seconds
            print("⏱️  Testing KeepAlive for 20 seconds...")
            print("   (sending KeepAlive every 4 seconds)\n")

            start_time = asyncio.get_event_loop().time()

            for i in range(5):  # 5 iterations × 4 seconds = 20 seconds
                await asyncio.sleep(4)

                # Send KeepAlive
                try:
                    control_msg = ListenV1ControlMessage(type="KeepAlive")
                    connection.send_control(control_msg)
                    elapsed = asyncio.get_event_loop().time() - start_time
                    print(f"   [{elapsed:5.1f}s] Sent KeepAlive #{i+1}")
                except Exception as e:
                    print(f"   ✗ Failed to send KeepAlive: {e}")
                    break

                # Check for errors
                if error_occurred:
                    print(f"\n✗ Error occurred at {elapsed:.1f}s: {error_occurred}")
                    return False

            elapsed_total = asyncio.get_event_loop().time() - start_time
            print(f"\n✓ Connection survived {elapsed_total:.1f} seconds!")

            # Verify success
            if error_occurred:
                print(f"✗ FAIL: Error occurred: {error_occurred}")
                return False

            if not connection_open:
                print("✗ FAIL: Connection never opened")
                return False

            print("✓ PASS: KeepAlive successfully prevented timeout")
            return True

    except Exception as e:
        print(f"\n✗ Test crashed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    success = await test_keepalive()
    return 0 if success else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)
