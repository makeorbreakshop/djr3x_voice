"""
Working test for Deepgram SDK 5.x persistent connection with KeepAlive.

This test demonstrates the correct pattern:
1. Connection must have start_listening() running in background
2. Send KeepAlive messages every 5 seconds while connection is active
3. Connection stays alive for 30+ seconds without audio

REQUIRES: DEEPGRAM_API_KEY environment variable
"""

import asyncio
import os
import threading
from deepgram import DeepgramClient
from deepgram.core.events import EventType
from deepgram.extensions.types.sockets import ListenV1ControlMessage


async def test_persistent_connection_with_keepalive():
    """Test that demonstrates persistent connection with KeepAlive in SDK 5.x."""
    api_key = os.getenv("DEEPGRAM_API_KEY")
    if not api_key:
        print("✗ DEEPGRAM_API_KEY not found")
        return False

    client = DeepgramClient(api_key=api_key)

    connection_open = False
    error_occurred = None
    messages_received = []
    test_complete = False

    def on_open(open_event):
        nonlocal connection_open
        print("✓ Connection opened")
        connection_open = True

    def on_message(message_event):
        nonlocal messages_received
        msg_type = getattr(message_event, "type", "Unknown")
        print(f"✓ Received message: {msg_type}")
        messages_received.append(message_event)

    def on_close(close_event):
        print("✓ Connection closed")

    def on_error(error_event):
        nonlocal error_occurred
        print(f"✗ Error: {error_event}")
        error_occurred = error_event

    print("\n=== Testing Persistent Connection with KeepAlive (SDK 5.x) ===\n")

    try:
        # Create connection using context manager
        with client.listen.v1.connect(model="nova-3") as connection:
            # Set up handlers
            connection.on(EventType.OPEN, on_open)
            connection.on(EventType.MESSAGE, on_message)
            connection.on(EventType.CLOSE, on_close)
            connection.on(EventType.ERROR, on_error)

            # CRITICAL: start_listening() is BLOCKING, must run in background thread
            print("📡 Starting listener thread...")
            listener_thread = threading.Thread(
                target=connection.start_listening,
                daemon=True
            )
            listener_thread.start()

            # Wait for connection to open
            await asyncio.sleep(1)

            if not connection_open:
                print("✗ FAIL: Connection never opened")
                return False

            print("✓ Connection established\n")
            print("⏱️  Testing KeepAlive for 30 seconds...")
            print("   (sending KeepAlive every 5 seconds)\n")

            start_time = asyncio.get_event_loop().time()

            # Send KeepAlive messages every 5 seconds for 30 seconds
            for i in range(6):  # 6 iterations × 5 seconds = 30 seconds
                await asyncio.sleep(5)

                # Send KeepAlive control message
                try:
                    control_msg = ListenV1ControlMessage(type="KeepAlive")
                    connection.send_control(control_msg)
                    elapsed = asyncio.get_event_loop().time() - start_time
                    print(f"   [{elapsed:5.1f}s] Sent KeepAlive #{i+1}")
                except Exception as e:
                    print(f"   ✗ Failed to send KeepAlive: {e}")
                    error_occurred = e
                    break

                # Check for errors
                if error_occurred:
                    elapsed = asyncio.get_event_loop().time() - start_time
                    print(f"\n✗ Error occurred at {elapsed:.1f}s: {error_occurred}")
                    return False

            elapsed_total = asyncio.get_event_loop().time() - start_time
            print(f"\n✓ Connection survived {elapsed_total:.1f} seconds with KeepAlive!")

            # Clean up
            print("\n🧹 Cleaning up...")
            test_complete = True

            # Wait for thread to finish
            listener_thread.join(timeout=2)

            # Verify success
            if error_occurred:
                print(f"✗ FAIL: Error occurred: {error_occurred}")
                return False

            if not connection_open:
                print("✗ FAIL: Connection never opened")
                return False

            print("✓ PASS: Persistent connection with KeepAlive works!\n")
            print(f"📊 Stats:")
            print(f"   - Connection uptime: {elapsed_total:.1f}s")
            print(f"   - KeepAlive messages sent: 6")
            print(f"   - Messages received: {len(messages_received)}")
            print(f"   - Errors: 0")

            return True

    except Exception as e:
        print(f"\n✗ Test crashed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    print("=" * 60)
    print("Deepgram SDK 5.x Persistent Connection Test")
    print("=" * 60)

    success = await test_persistent_connection_with_keepalive()

    print("\n" + "=" * 60)
    if success:
        print("✓ TEST PASSED")
    else:
        print("✗ TEST FAILED")
    print("=" * 60)

    return 0 if success else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)
