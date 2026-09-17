#!/usr/bin/env python3
"""
Test script to demonstrate serial buffer overflow on Arduino.
This simulates what happens when Python sends commands faster than Arduino can process.
"""

import serial
import time
import sys

def test_buffer_overflow(port="/dev/cu.usbmodem833301", baud=115200):
    """Test Arduino serial buffer capacity and overflow behavior."""

    print("=" * 60)
    print("Arduino MEGA Serial Buffer Overflow Test")
    print("=" * 60)

    try:
        # Connect to Arduino
        print(f"\nConnecting to Arduino on {port} at {baud} baud...")
        ser = serial.Serial(port, baud, timeout=1)
        time.sleep(2)  # Wait for Arduino to initialize

        # Clear any startup messages
        ser.reset_input_buffer()
        ser.reset_output_buffer()

        print("Connected! Waiting for Arduino to be ready...\n")
        time.sleep(1)

        # Test 1: Show normal operation
        print("TEST 1: Normal Operation")
        print("-" * 40)
        print("Sending a single command and waiting for response...")

        ser.write(b"CHECK\n")
        time.sleep(0.1)
        response = ser.read(ser.in_waiting).decode('utf-8', errors='ignore')
        print(f"Arduino says:\n{response}")

        # Test 2: Simulate your actual problem
        print("\nTEST 2: Simulating Your LED Control Problem")
        print("-" * 40)
        print("This simulates Python sending mouth commands at 10Hz")
        print("while Arduino is busy updating LEDs...\n")

        # Tell Arduino to prepare for flood test
        ser.write(b"FLOOD\n")
        time.sleep(0.1)

        # Now actually flood it with commands like your system does
        print("Sending 20 mouth commands in rapid succession (like 10Hz updates):")
        commands_sent = []

        for i in range(20):
            # Simulate mouth amplitude commands
            amplitude = 255 - (i * 10)
            if amplitude < 0:
                amplitude = 0
            command = f"M{amplitude:03d}\n"
            commands_sent.append(command.strip())

            # Send command
            ser.write(command.encode())
            print(f"  Sent: {command.strip()} ({len(command)} bytes)")

            # Simulate 10Hz rate (100ms between commands)
            # But Arduino might be busy and not reading!
            time.sleep(0.01)  # Send quickly to simulate the problem

        print(f"\nTotal sent: {sum(len(c) + 1 for c in commands_sent)} bytes")
        print(f"With 64-byte buffer: Would overflow after ~12 commands")
        print(f"With 256-byte buffer: Can handle all {len(commands_sent)} commands\n")

        # Wait for Arduino to process and respond
        time.sleep(2)

        # Read Arduino's response
        response = ser.read(ser.in_waiting).decode('utf-8', errors='ignore')
        print("Arduino response after flood:")
        print(response)

        # Test 3: Test actual buffer capacity
        print("\nTEST 3: Testing Actual Buffer Capacity")
        print("-" * 40)
        print("Sending TEST command to measure buffer...\n")

        ser.reset_input_buffer()
        ser.write(b"TEST\n")
        time.sleep(0.5)

        print("Now flooding with data while Arduino waits...")
        test_data = b"X" * 300  # Send 300 bytes
        ser.write(test_data)
        print(f"Sent {len(test_data)} bytes of test data")

        # Wait for Arduino to check buffer
        time.sleep(3)

        # Read result
        response = ser.read(ser.in_waiting).decode('utf-8', errors='ignore')
        print("\nArduino buffer test result:")
        for line in response.split('\n'):
            if 'buffer' in line.lower() or 'Buffer' in line:
                print(f"  >>> {line}")

        # Test 4: Demonstrate the fix
        print("\nTEST 4: With Fire-and-Forget (Our Fix)")
        print("-" * 40)
        print("Sending commands WITHOUT waiting for responses...")

        start_time = time.time()
        for i in range(50):
            command = f"M{i*5:03d}\n"
            ser.write(command.encode())
            # Don't wait for response (fire-and-forget)

        elapsed = time.time() - start_time
        print(f"Sent 50 commands in {elapsed:.2f} seconds")
        print("With fire-and-forget, commands don't back up!\n")

        # Cleanup
        ser.close()
        print("\n✅ Test complete! Connection closed.")

    except serial.SerialException as e:
        print(f"\n❌ Error: Could not connect to Arduino")
        print(f"   {e}")
        print("\nMake sure:")
        print("1. Arduino is connected via USB")
        print("2. Correct port is specified")
        print("3. Arduino IDE Serial Monitor is closed")
        print(f"\nTry: ls /dev/cu.* to find your Arduino port")
        return False

    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")
        if 'ser' in locals():
            ser.close()
        return False

    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        if 'ser' in locals():
            ser.close()
        return False

    return True

if __name__ == "__main__":
    # You can specify port as command line argument
    port = sys.argv[1] if len(sys.argv) > 1 else "/dev/cu.usbmodem833301"

    # Make sure Arduino has the test sketch loaded!
    print("\n⚠️  Make sure MEGA_TEST_SERIAL sketch is uploaded to Arduino!")
    print("⚠️  Close Arduino IDE Serial Monitor if it's open!")
    input("\nPress Enter to start test...")

    test_buffer_overflow(port)