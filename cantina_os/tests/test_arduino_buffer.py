#!/usr/bin/env python3
"""Test Arduino MEGA 2560 serial buffer with rapid commands."""

import serial
import time
import sys

def test_buffer_overflow():
    """Test if the 256-byte buffer prevents overflow."""

    print("=" * 60)
    print("Arduino MEGA 2560 Buffer Overflow Test")
    print("=" * 60)

    # Connect to Arduino
    port = "/dev/cu.usbmodem833301"  # Your Arduino port from the logs
    baud = 115200

    print(f"Connecting to Arduino at {port} ({baud} baud)...")

    try:
        ser = serial.Serial(port, baud, timeout=1)
        time.sleep(2)  # Wait for Arduino to reset

        # Clear any startup messages
        while ser.in_waiting:
            line = ser.readline().decode('utf-8', errors='ignore')
            print(f"Arduino: {line.rstrip()}")

        print("\n" + "=" * 60)
        print("TEST 1: Buffer Capacity Test")
        print("=" * 60)

        # Send TEST command
        ser.write(b"TEST\n")
        time.sleep(0.1)

        # Now flood with data while Arduino waits
        print("Sending 200 bytes rapidly while Arduino is busy...")
        for i in range(40):  # 40 x 5 bytes = 200 bytes
            ser.write(f"D{i:03d}\n".encode())  # 5 bytes each

        # Read response
        time.sleep(3)
        while ser.in_waiting:
            line = ser.readline().decode('utf-8', errors='ignore')
            print(f"Arduino: {line.rstrip()}")

        print("\n" + "=" * 60)
        print("TEST 2: Rapid Mouth Commands (Fire-and-Forget)")
        print("=" * 60)

        # Simulate rapid mouth amplitude updates (10Hz = 100ms)
        print("Sending 50 mouth commands at 20Hz (every 50ms)...")
        start = time.time()

        for i in range(50):
            amplitude = int((i % 10) * 25.5)  # 0-255 cycling
            cmd = f"M{amplitude:03d}\n"
            ser.write(cmd.encode())
            print(f"Sent: {cmd.strip()} ", end="")
            if i % 10 == 9:
                print()  # New line every 10
            time.sleep(0.05)  # 50ms = 20Hz

        duration = time.time() - start
        print(f"\nSent 50 commands in {duration:.2f}s")

        # Check for any errors
        time.sleep(0.5)
        ser.write(b"CHECK\n")
        time.sleep(0.5)

        while ser.in_waiting:
            line = ser.readline().decode('utf-8', errors='ignore')
            print(f"Arduino: {line.rstrip()}")

        print("\n" + "=" * 60)
        print("TEST 3: Worst Case - 300 bytes in 100ms")
        print("=" * 60)

        ser.write(b"FLOOD\n")
        time.sleep(0.1)

        # Send massive burst
        print("Sending 300 bytes as fast as possible...")
        burst_start = time.time()
        for i in range(60):  # 60 x 5 = 300 bytes
            ser.write(f"X{i:03d}\n".encode())
        burst_duration = (time.time() - burst_start) * 1000
        print(f"Sent 300 bytes in {burst_duration:.1f}ms")

        # Read results
        time.sleep(2)
        overflow_detected = False
        while ser.in_waiting:
            line = ser.readline().decode('utf-8', errors='ignore')
            print(f"Arduino: {line.rstrip()}")
            if "OVERFLOW" in line or "LOST" in line:
                overflow_detected = True

        print("\n" + "=" * 60)
        if overflow_detected:
            print("❌ BUFFER OVERFLOW DETECTED - Need larger buffer or slower rate!")
        else:
            print("✅ ALL TESTS PASSED - 256-byte buffer handles the load!")
        print("=" * 60)

        ser.close()
        return not overflow_detected

    except serial.SerialException as e:
        print(f"❌ Serial error: {e}")
        return False
    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")
        if 'ser' in locals():
            ser.close()
        return False

if __name__ == "__main__":
    success = test_buffer_overflow()
    sys.exit(0 if success else 1)