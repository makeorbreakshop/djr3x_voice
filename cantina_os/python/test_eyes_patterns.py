#!/usr/bin/env python3
"""
DJ R3X Eyes Pattern Test
Cycles through all eye patterns to verify they work correctly
"""

import time
import argparse
import sys
from simple_eyes_control import SimpleEyesController

def test_patterns(controller):
    """Test all patterns in sequence"""
    
    patterns = [
        ('idle', 'IDLE pattern - 3x3 grid'),
        ('speaking', 'SPEAKING pattern - Vertical line with animation'),
        ('thinking', 'THINKING pattern - Rotating dot'),
        ('listening', 'LISTENING pattern - Pulsing 3x3 grid'),
        ('happy', 'HAPPY pattern - Upward curves'),
        ('sad', 'SAD pattern - Downward curves'),
        ('angry', 'ANGRY pattern - Angled lines'),
    ]
    
    # Test each pattern
    for pattern, description in patterns:
        print(f"\nTesting: {description}")
        controller.set_pattern(pattern)
        # Display each pattern for 3 seconds
        time.sleep(3)
    
    # Test brightness levels
    print("\nTesting brightness levels...")
    controller.set_pattern('idle')
    
    for level in range(0, 10, 2):
        print(f"Setting brightness to {level}")
        controller.set_brightness(level)
        time.sleep(1)
    
    # Reset to default brightness
    controller.set_brightness(8)
    
    # Final reset
    print("\nResetting eyes...")
    controller.set_pattern('reset')
    
    print("\nTest complete!")

def main():
    """Main function"""
    parser = argparse.ArgumentParser(description='Test DJ R3X eye patterns')
    parser.add_argument('--port', '-p', required=True, help='Serial port (e.g., /dev/ttyACM0, COM3)')
    parser.add_argument('--debug', '-d', action='store_true', help='Enable debug mode')
    
    args = parser.parse_args()
    
    print("DJ R3X Eyes Pattern Test")
    print("------------------------")
    print(f"Connecting to Arduino on {args.port}...")
    
    # Set up serial port manually first to test connection
    try:
        import serial
        ser = serial.Serial(args.port, 115200, timeout=2)
        print(f"Serial port opened successfully: {args.port}")
        print("Reading initial data from Arduino...")
        initial_data = ser.read(100)
        print(f"Initial data: {repr(initial_data)}")
        ser.close()
        print("Closed test connection")
    except Exception as e:
        print(f"Error during initial serial test: {e}")
        if "No such file or directory" in str(e):
            print("\nAvailable ports:")
            import serial.tools.list_ports
            ports = serial.tools.list_ports.comports()
            if ports:
                for port in ports:
                    print(f"- {port.device} ({port.description})")
            else:
                print("No serial ports found")
        return
    
    print("\nTesting with SimpleEyesController...")
    controller = SimpleEyesController(args.port)
    if not controller.connect():
        sys.exit(1)
    
    try:
        print("\nStarting pattern tests...")
        test_patterns(controller)
    except Exception as e:
        print(f"Error during testing: {e}")
    finally:
        controller.disconnect()
        print("Test completed (with or without errors)")

if __name__ == "__main__":
    main() 