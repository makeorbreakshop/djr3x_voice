#!/usr/bin/env python3
"""
Diagnostic script for Arduino JSON communication
"""

import json
import serial
import time
import sys

# Configure serial connection
PORT = '/dev/cu.usbmodem833301'  # Update with your Arduino port
BAUD_RATE = 115200
TIMEOUT = 2.0

def main():
    print(f"Opening serial port {PORT} at {BAUD_RATE} baud...")
    try:
        ser = serial.Serial(PORT, BAUD_RATE, timeout=TIMEOUT)
        print("Serial port opened successfully")
        
        # Wait for Arduino to reset
        time.sleep(2)
        
        # Clear any startup messages
        ser.reset_input_buffer()
        ser.reset_output_buffer()
        
        # Send a simple test command
        test_cmd = json.dumps({"command": "test"}) + "\n"
        print(f"Sending command: {test_cmd.strip()}")
        ser.write(test_cmd.encode())
        ser.flush()
        
        # Read raw response with detailed debug
        print("\nReading response (raw bytes):")
        time.sleep(0.5)  # Wait for response
        
        while True:
            if ser.in_waiting > 0:
                # Read raw bytes
                raw_data = ser.read(ser.in_waiting)
                hex_data = ' '.join([f"{b:02x}" for b in raw_data])
                print(f"Raw bytes: {hex_data}")
                
                # Try to decode as UTF-8
                try:
                    text_data = raw_data.decode('utf-8')
                    print(f"Decoded text: {repr(text_data)}")
                    
                    # Look for newlines
                    lines = text_data.split('\n')
                    print(f"Lines found: {len(lines)}")
                    for i, line in enumerate(lines):
                        if line.strip():
                            print(f"Line {i}: {repr(line)}")
                            try:
                                # Try to parse as JSON
                                json_data = json.loads(line)
                                print(f"Valid JSON: {json_data}")
                            except json.JSONDecodeError as e:
                                print(f"Invalid JSON: {e}")
                except UnicodeDecodeError as e:
                    print(f"Decode error: {e}")
                    
                time.sleep(0.5)
                
                # Check if more data
                if ser.in_waiting == 0:
                    print("\nNo more data, waiting 1 second...")
                    time.sleep(1)
                    if ser.in_waiting == 0:
                        break
            else:
                print("No data received yet...")
                time.sleep(0.5)
                if ser.in_waiting == 0:
                    print("No response after 1.5 seconds, exiting")
                    break
        
        # Now send a pattern command
        print("\n\nSending set_pattern command...")
        pattern_cmd = json.dumps({
            "command": "set_pattern",
            "params": {
                "pattern": "IDLE",
                "brightness": 0.8
            }
        }) + "\n"
        print(f"Command: {pattern_cmd.strip()}")
        ser.write(pattern_cmd.encode())
        ser.flush()
        
        # Read raw response again
        print("\nReading response (raw bytes):")
        time.sleep(0.5)  # Wait for response
        
        while True:
            if ser.in_waiting > 0:
                # Read raw bytes
                raw_data = ser.read(ser.in_waiting)
                hex_data = ' '.join([f"{b:02x}" for b in raw_data])
                print(f"Raw bytes: {hex_data}")
                
                # Try to decode as UTF-8
                try:
                    text_data = raw_data.decode('utf-8')
                    print(f"Decoded text: {repr(text_data)}")
                    
                    # Look for newlines
                    lines = text_data.split('\n')
                    print(f"Lines found: {len(lines)}")
                    for i, line in enumerate(lines):
                        if line.strip():
                            print(f"Line {i}: {repr(line)}")
                            try:
                                # Try to parse as JSON
                                json_data = json.loads(line)
                                print(f"Valid JSON: {json_data}")
                            except json.JSONDecodeError as e:
                                print(f"Invalid JSON: {e}")
                except UnicodeDecodeError as e:
                    print(f"Decode error: {e}")
                    
                time.sleep(0.5)
                
                # Check if more data
                if ser.in_waiting == 0:
                    print("\nNo more data, waiting 1 second...")
                    time.sleep(1)
                    if ser.in_waiting == 0:
                        break
            else:
                print("No data received yet...")
                time.sleep(0.5)
                if ser.in_waiting == 0:
                    print("No response after 1.5 seconds, exiting")
                    break
                    
    except serial.SerialException as e:
        print(f"Serial error: {e}")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        if 'ser' in locals() and ser.is_open:
            ser.close()
            print("Serial port closed")

if __name__ == "__main__":
    main() 