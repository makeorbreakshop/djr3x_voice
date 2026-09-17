"""
Test sending KeepAlive IMMEDIATELY after connection opens.

Hypothesis: Must send KeepAlive within first second to prevent timeout.
"""

import asyncio
import os
from deepgram import DeepgramClient
from deepgram.core.events import EventType
from deepgram.extensions.types.sockets import ListenV1ControlMessage


async def test_immediate_keepalive():
    """Send KeepAlive immediately on connection open."""
    api_key = os.getenv("DEEPGRAM_API_KEY")
    if not api_key:
        print("✗ DEEPGRAM_API_KEY not found")
        return False

    client = DeepgramClient(api_key=api_key)

    connection_ref = {"conn": None}
    error_occurred = {"error": None}

    async def on_open(_):
        print("✓ Connection opened - sending KeepAlive IMMEDIATELY")
        try:
            # Send KeepAlive as soon as connection opens
            control_msg = ListenV1ControlMessage(type="KeepAlive")
            connection_ref["conn"].send_control(control_msg)
            print("  ✓ Sent first KeepAlive")
        except Exception as e:
            print(f"  ✗ Failed to send KeepAlive: {e}")

    def on_message(message):
        msg_type = getattr(message, "type", "Unknown")
        print(f"✓ Received: {msg_type}")

    def on_close(_):
        print("✓ Connection closed")

    def on_error(error):
        error_occurred["error"] = error
        print(f"✗ Error: {error}")

    print("\n=== Testing IMMEDIATE KeepAlive ===\n")

    try:
        with client.listen.v1.connect(model="nova-3") as connection:
            connection_ref["conn"] = connection

            # Set up handlers
            connection.on(EventType.OPEN, on_open)
            connection.on(EventType.MESSAGE, on_message)
            connection.on(EventType.CLOSE, on_close)
            connection.on(EventType.ERROR, on_error)

            # Start listening
            connection.start_listening()

            # Send KeepAlive every 3 seconds for 15 seconds
            print("⏱️  Sending KeepAlive every 3 seconds...\n")

            for i in range(5):
                await asyncio.sleep(3)

                if error_occurred["error"]:
                    print(f"\n✗ Error occurred after {i*3} seconds")
                    return False

                try:
                    control_msg = ListenV1ControlMessage(type="KeepAlive")
                    connection.send_control(control_msg)
                    print(f"   [{(i+1)*3:2d}s] Sent KeepAlive #{i+2}")
                except Exception as e:
                    print(f"   ✗ Failed: {e}")
                    return False

            print(f"\n✓ SUCCESS: Connection survived 15+ seconds with KeepAlive!")
            return True

    except Exception as e:
        print(f"\n✗ Test crashed: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = asyncio.run(test_immediate_keepalive())
    exit(0 if success else 1)
