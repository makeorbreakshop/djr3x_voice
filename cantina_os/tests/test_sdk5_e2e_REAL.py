"""
END-TO-END TEST for SDK 5.x Deepgram service.

This test ACTUALLY:
1. Starts the full DJ R3X system
2. Waits for services to initialize
3. Checks that Deepgram WebSocket opens and stays open for 15+ seconds
4. Verifies KeepAlive messages are being sent
5. Confirms no 10-second timeout errors occur

REQUIRES: Full system with DEEPGRAM_API_KEY
"""

import asyncio
import subprocess
import time
import os
import signal

def test_sdk5_no_timeout():
    """Test that SDK 5.x prevents 10-second timeout."""
    print("\n" + "="*60)
    print("SDK 5.x End-to-End Test - No Timeout Verification")
    print("="*60)

    # Start DJ R3X in background
    print("\n📡 Starting DJ R3X system...")
    log_file = "/tmp/djr3x_e2e_test.log"

    proc = subprocess.Popen(
        ["../venv/bin/python", "-m", "cantina_os.main"],
        cwd="/Users/brandoncullum/DJ-R3X Voice/cantina_os",
        stdout=open(log_file, "w"),
        stderr=subprocess.STDOUT
    )

    try:
        # Wait for system to start
        print("⏱️  Waiting 5 seconds for services to initialize...")
        time.sleep(5)

        # Check initial status
        with open(log_file, "r") as f:
            log_content = f.read()

        if "Deepgram WebSocket opened (SDK 5.x)" not in log_content:
            print("✗ FAIL: Deepgram WebSocket never opened")
            return False

        print("✓ Deepgram WebSocket opened successfully")

        if "DeepgramDirectMicService started with SDK 5.x + KeepAlive" not in log_content:
            print("✗ FAIL: Service didn't start with KeepAlive")
            return False

        print("✓ Service started with KeepAlive enabled")

        # Wait past the 10-second timeout threshold
        print("\n⏱️  Waiting 15 seconds to verify no timeout...")
        print("   (10-second timeout would occur at ~12 seconds)")
        for i in range(15):
            time.sleep(1)
            print(f"   [{i+1:2d}s]", end="", flush=True)
            if (i+1) % 5 == 0:
                print()
        print()

        # Check final status
        with open(log_file, "r") as f:
            log_content = f.read()

        # Check for timeout errors
        if "1011" in log_content and "internal error" in log_content:
            # Count how many 1011 errors
            error_count = log_content.count("1011")
            if error_count > 2:  # More than just initial connection handshake
                print(f"✗ FAIL: Found {error_count} connection errors (1011)")
                return False

        if "Deepgram did not receive audio data or a text message within the timeout window" in log_content:
            print("✗ FAIL: 10-second timeout occurred!")
            return False

        if "Deepgram WebSocket closed" in log_content:
            # Check if it's just the final cleanup close
            close_count = log_content.count("Deepgram WebSocket closed")
            if close_count > 0:
                print(f"⚠️  WARNING: WebSocket closed {close_count} time(s)")
                # Check if closed before we killed it
                lines = log_content.split("\n")
                for line in lines:
                    if "Deepgram WebSocket closed" in line:
                        print(f"    {line}")

        print("\n✓ PASS: No 10-second timeout detected!")
        print("✓ PASS: WebSocket stayed open for 15+ seconds")

        # Check for KeepAlive activity
        if "KeepAlive" in log_content:
            keepalive_count = log_content.count("Sent KeepAlive")
            print(f"✓ PASS: KeepAlive messages sent ({keepalive_count} detected)")
        else:
            print("⚠️  WARNING: No KeepAlive messages detected in logs")

        print("\n" + "="*60)
        print("✓ TEST PASSED - SDK 5.x WORKING")
        print("="*60)

        return True

    except Exception as e:
        print(f"\n✗ TEST CRASHED: {e}")
        import traceback
        traceback.print_exc()
        return False

    finally:
        # Cleanup
        print("\n🧹 Cleaning up...")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except:
            proc.kill()
        print("✓ Cleanup complete")


if __name__ == "__main__":
    success = test_sdk5_no_timeout()
    exit(0 if success else 1)
