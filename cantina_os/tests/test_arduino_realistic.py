#!/usr/bin/env python3
"""Realistic test that matches actual CantinaOS mouth command patterns."""

import serial
import time
import sys

def test_realistic_mouth_updates():
    """Test with realistic 10Hz mouth updates like CantinaOS does."""

    print("=" * 60)
    print("Realistic Mouth Command Test (10Hz updates)")
    print("=" * 60)

    port = "/dev/cu.usbmodem833301"
    baud = 115200

    print(f"Connecting to Arduino at {port}...")

    try:
        ser = serial.Serial(port, baud, timeout=1)
        time.sleep(2)  # Wait for Arduino reset

        # Clear startup messages
        while ser.in_waiting:
            line = ser.readline().decode('utf-8', errors='ignore')
            if "Ready for commands" in line:
                break

        print("\nTest 1: Burst of 10 commands (simulating speech start)")
        print("-" * 40)

        # Send a burst of 10 mouth commands quickly
        for i in range(10):
            amplitude = int(i * 25.5)  # 0 to 255
            cmd = f"M{amplitude:03d}\n"
            ser.write(cmd.encode())
            print(f"Sent: {cmd.strip()}")
            time.sleep(0.01)  # 10ms between commands

        # Check for any responses
        time.sleep(0.5)
        while ser.in_waiting:
            line = ser.readline().decode('utf-8', errors='ignore').strip()
            if line:
                print(f"Arduino: {line}")

        print("\nTest 2: Continuous 10Hz updates for 2 seconds")
        print("-" * 40)

        # Send at 10Hz for 2 seconds (20 commands)
        start = time.time()
        count = 0

        while time.time() - start < 2.0:
            amplitude = int((count % 10) * 25.5)
            cmd = f"M{amplitude:03d}\n"
            ser.write(cmd.encode())
            count += 1

            # Print every 5th command to avoid spam
            if count % 5 == 0:
                print(f"Sent {count} commands...")

            time.sleep(0.1)  # 100ms = 10Hz

        print(f"\nTotal sent: {count} mouth commands at 10Hz")

        # Send CHECK command
        ser.write(b"CHECK\n")
        time.sleep(0.5)

        while ser.in_waiting:
            line = ser.readline().decode('utf-8', errors='ignore').strip()
            if line:
                print(f"Arduino: {line}")

        print("\nTest 3: Worst case - 20Hz updates (stressed system)")
        print("-" * 40)

        # Clear buffer first
        while ser.in_waiting:
            ser.read()

        # Send AUTO command for automated test
        ser.write(b"AUTO\n")

        # Wait for SEND_NOW signal
        while True:
            if ser.in_waiting:
                line = ser.readline().decode('utf-8', errors='ignore').strip()
                print(f"Arduino: {line}")

                if "SEND_NOW" in line:
                    print("\n🚀 Sending at 20Hz (50ms intervals)...")

                    # Send at 20Hz for the 500ms window
                    for i in range(10):  # 10 x 50ms = 500ms
                        cmd = f"M{i:03d}\n"
                        ser.write(cmd.encode())
                        time.sleep(0.05)  # 50ms = 20Hz

                    print("Sent 10 commands at 20Hz")

                if "bytes while busy" in line:
                    # Test complete
                    time.sleep(0.5)
                    while ser.in_waiting:
                        line = ser.readline().decode('utf-8', errors='ignore').strip()
                        if line:
                            print(f"Arduino: {line}")
                    break

        print("\n" + "=" * 60)
        print("Realistic testing complete!")
        print("\nSummary:")
        print("- 10Hz updates (normal): Should work fine")
        print("- 20Hz updates (stressed): Tests buffer limits")
        print("- Fire-and-forget prevents blocking")
        print("=" * 60)

        ser.close()
        return True

    except serial.SerialException as e:
        print(f"❌ Serial error: {e}")
        return False
    except KeyboardInterrupt:
        print("\nTest interrupted")
        if 'ser' in locals():
            ser.close()
        return False

if __name__ == "__main__":
    success = test_realistic_mouth_updates()
    sys.exit(0 if success else 1)