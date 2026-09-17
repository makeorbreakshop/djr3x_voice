#!/usr/bin/env python3
"""Automated test for Arduino MEGA buffer - works with AUTO command."""

import serial
import time
import sys

def test_automated():
    """Test Arduino buffer with automated handshake."""

    print("=" * 60)
    print("Arduino MEGA 2560 Automated Buffer Test")
    print("=" * 60)

    port = "/dev/cu.usbmodem833301"  # Your Arduino port
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

        print("Connected! Sending AUTO command...")

        # Send AUTO command
        ser.write(b"AUTO\n")

        # Listen for SEND_NOW signal
        while True:
            if ser.in_waiting:
                line = ser.readline().decode('utf-8', errors='ignore').strip()
                print(f"Arduino: {line}")

                if "SEND_NOW" in line:
                    print("\n🚀 SIGNAL RECEIVED! Flooding with data...")

                    # Send 200 bytes as fast as possible
                    for i in range(40):  # 40 x 5 bytes = 200 bytes
                        cmd = f"M{i:03d}\n"  # Simulating mouth commands
                        ser.write(cmd.encode())

                    print("Sent 200 bytes of mouth commands!")

                if "bytes while busy" in line:
                    # Test is complete, read remaining output
                    time.sleep(0.5)
                    while ser.in_waiting:
                        line = ser.readline().decode('utf-8', errors='ignore').strip()
                        print(f"Arduino: {line}")
                    break

        print("\n" + "=" * 60)
        print("Test complete!")
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
    success = test_automated()
    sys.exit(0 if success else 1)