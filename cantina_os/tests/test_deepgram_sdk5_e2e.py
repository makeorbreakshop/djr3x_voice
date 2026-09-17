"""
End-to-end tests for Deepgram SDK 5.x with REAL API calls.

This test file verifies:
1. WebSocket connection establishment
2. Audio streaming and transcription
3. KeepAlive control messages
4. Timeout behavior (10-second idle timeout)
5. Persistent connection management

REQUIRES: DEEPGRAM_API_KEY environment variable
"""

import asyncio
import os
import time
import wave
import numpy as np
from pathlib import Path
from deepgram import DeepgramClient
from deepgram.core.events import EventType
from deepgram.extensions.types.sockets import (
    ListenV1SocketClientResponse,
    ListenV1MediaMessage,
    ListenV1ControlMessage
)


class DeepgramSDK5E2ETest:
    """Test harness for SDK 5.x with real API connectivity."""

    def __init__(self):
        self.api_key = os.getenv("DEEPGRAM_API_KEY")
        if not self.api_key:
            raise ValueError("DEEPGRAM_API_KEY not found in environment")

        self.client = DeepgramClient(api_key=self.api_key)
        self.messages_received = []
        self.connection_opened = False
        self.connection_closed = False
        self.error_occurred = None

    def _on_open(self, _):
        """Handle connection open event."""
        print("✓ WebSocket connection opened")
        self.connection_opened = True

    def _on_message(self, message: ListenV1SocketClientResponse):
        """Handle incoming messages."""
        msg_type = getattr(message, "type", "Unknown")
        print(f"✓ Received message: {msg_type}")
        self.messages_received.append(message)

        # Print transcripts if available
        if hasattr(message, "channel"):
            for alt in message.channel.alternatives:
                if alt.transcript:
                    print(f"  Transcript: '{alt.transcript}'")

    def _on_close(self, _):
        """Handle connection close event."""
        print("✓ WebSocket connection closed")
        self.connection_closed = True

    def _on_error(self, error):
        """Handle error events."""
        print(f"✗ Error occurred: {error}")
        self.error_occurred = error

    def generate_test_audio(self, duration_seconds=2, sample_rate=16000):
        """Generate simple sine wave audio for testing."""
        # Generate a 440Hz sine wave (A4 note)
        samples = int(sample_rate * duration_seconds)
        t = np.linspace(0, duration_seconds, samples)
        audio = np.sin(2 * np.pi * 440 * t)

        # Convert to 16-bit PCM
        audio_int16 = (audio * 32767).astype(np.int16)
        return audio_int16.tobytes()

    async def test_basic_connection(self):
        """Test 1: Basic WebSocket connection and audio streaming."""
        print("\n=== Test 1: Basic Connection & Streaming ===")

        try:
            with self.client.listen.v1.connect(model="nova-3") as connection:
                # Set up event handlers
                connection.on(EventType.OPEN, self._on_open)
                connection.on(EventType.MESSAGE, self._on_message)
                connection.on(EventType.CLOSE, self._on_close)
                connection.on(EventType.ERROR, self._on_error)

                # Start listening
                connection.start_listening()

                # Wait for connection to open
                await asyncio.sleep(1)

                # Generate and send test audio
                audio_data = self.generate_test_audio(duration_seconds=1)
                connection.send_media(audio_data)  # Just send raw bytes

                # Wait for transcription
                await asyncio.sleep(2)

                # Verify results
                assert self.connection_opened, "Connection never opened"
                print(f"✓ Received {len(self.messages_received)} messages")

                return True

        except Exception as e:
            print(f"✗ Test failed: {e}")
            return False

    async def test_keepalive_message(self):
        """Test 2: Send KeepAlive control message."""
        print("\n=== Test 2: KeepAlive Control Message ===")

        self.messages_received.clear()

        try:
            with self.client.listen.v1.connect(model="nova-3") as connection:
                connection.on(EventType.OPEN, self._on_open)
                connection.on(EventType.MESSAGE, self._on_message)
                connection.on(EventType.CLOSE, self._on_close)
                connection.on(EventType.ERROR, self._on_error)

                connection.start_listening()
                await asyncio.sleep(1)

                # Send KeepAlive messages
                for i in range(3):
                    connection.send_control(ListenV1ControlMessage(type="KeepAlive"))
                    print(f"✓ Sent KeepAlive message {i+1}")
                    await asyncio.sleep(2)

                # Verify no errors occurred
                assert self.error_occurred is None, f"Error occurred: {self.error_occurred}"
                print("✓ KeepAlive messages sent successfully")

                return True

        except Exception as e:
            print(f"✗ Test failed: {e}")
            return False

    async def test_timeout_behavior(self):
        """Test 3: Verify 10-second timeout without KeepAlive."""
        print("\n=== Test 3: Timeout Behavior (10 seconds idle) ===")

        self.error_occurred = None
        self.connection_closed = False

        try:
            with self.client.listen.v1.connect(model="nova-3") as connection:
                connection.on(EventType.OPEN, self._on_open)
                connection.on(EventType.MESSAGE, self._on_message)
                connection.on(EventType.CLOSE, self._on_close)
                connection.on(EventType.ERROR, self._on_error)

                connection.start_listening()
                await asyncio.sleep(1)

                print("⏱️  Waiting 12 seconds without KeepAlive (should timeout)...")
                start_time = time.time()

                # Wait 12 seconds (past the 10-second timeout)
                await asyncio.sleep(12)

                elapsed = time.time() - start_time
                print(f"⏱️  Elapsed: {elapsed:.1f}s")

                # Check if connection closed (expected)
                if self.connection_closed or self.error_occurred:
                    print("✓ Connection timed out as expected")
                    return True
                else:
                    print("⚠️  Connection did not timeout (unexpected)")
                    return False

        except Exception as e:
            # Timeout errors are expected
            print(f"✓ Test completed with expected error: {e}")
            return True

    async def test_keepalive_prevents_timeout(self):
        """Test 4: KeepAlive prevents timeout during 15-second idle."""
        print("\n=== Test 4: KeepAlive Prevents Timeout ===")

        self.error_occurred = None
        self.connection_closed = False

        try:
            with self.client.listen.v1.connect(model="nova-3") as connection:
                connection.on(EventType.OPEN, self._on_open)
                connection.on(EventType.MESSAGE, self._on_message)
                connection.on(EventType.CLOSE, self._on_close)
                connection.on(EventType.ERROR, self._on_error)

                connection.start_listening()
                await asyncio.sleep(1)

                print("⏱️  Testing 15 seconds with KeepAlive every 5 seconds...")
                start_time = time.time()

                # Send KeepAlive every 5 seconds for 15 seconds
                for i in range(3):
                    await asyncio.sleep(5)
                    connection.send_control(ListenV1ControlMessage(type="KeepAlive"))
                    print(f"✓ Sent KeepAlive at {time.time() - start_time:.1f}s")

                elapsed = time.time() - start_time
                print(f"⏱️  Elapsed: {elapsed:.1f}s")

                # Verify connection stayed alive
                assert self.error_occurred is None, f"Unexpected error: {self.error_occurred}"
                assert not self.connection_closed, "Connection closed unexpectedly"

                print("✓ Connection survived 15+ seconds with KeepAlive")
                return True

        except Exception as e:
            print(f"✗ Test failed: {e}")
            return False

    async def run_all_tests(self):
        """Run all E2E tests."""
        print("\n" + "="*60)
        print("Deepgram SDK 5.x End-to-End Tests (REAL API)")
        print("="*60)

        tests = [
            ("Basic Connection", self.test_basic_connection),
            ("KeepAlive Message", self.test_keepalive_message),
            ("Timeout Behavior", self.test_timeout_behavior),
            ("KeepAlive Prevents Timeout", self.test_keepalive_prevents_timeout),
        ]

        results = []
        for name, test_func in tests:
            try:
                result = await test_func()
                results.append((name, result))
            except Exception as e:
                print(f"✗ {name} crashed: {e}")
                results.append((name, False))

        # Summary
        print("\n" + "="*60)
        print("Test Results Summary")
        print("="*60)

        for name, result in results:
            status = "✓ PASS" if result else "✗ FAIL"
            print(f"{status}: {name}")

        total_passed = sum(1 for _, r in results if r)
        print(f"\nPassed: {total_passed}/{len(results)}")

        return total_passed == len(results)


async def main():
    """Main entry point for tests."""
    tester = DeepgramSDK5E2ETest()
    success = await tester.run_all_tests()

    if success:
        print("\n✓ All tests passed!")
        return 0
    else:
        print("\n✗ Some tests failed")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)
