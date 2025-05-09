#!/usr/bin/env python3
"""
Ultra-Simple DJ R3X Eyes Controller
Controls Arduino-based LED eyes using single-character command protocol
"""

import serial
import time
import argparse
import sys

class SimpleEyesController:
    def __init__(self, port, baud=115200, timeout=1.0):
        """Initialize the eyes controller with the specified serial port"""
        self.port = port
        self.baud = baud
        self.timeout = timeout
        self.serial = None
        self.connected = False
    
    def connect(self):
        """Connect to the Arduino"""
        try:
            self.serial = serial.Serial(self.port, self.baud, timeout=self.timeout)
            # Wait for Arduino to reset after serial connection
            time.sleep(2)
            
            # Instead of expecting a response, just try a simple command
            print("Sending test command to Arduino...")
            self.serial.write(b'I') # Send IDLE command
            self.serial.flush()
            
            # Read response
            response = self.serial.readline().decode('utf-8', errors='ignore').strip()
            print(f"Response: {repr(response)}")
            
            if response == '+':
                self.connected = True
                print(f"Connected to Arduino on {self.port}")
                return True
            else:
                # Try one more time
                print("Trying reset command...")
                self.serial.write(b'R') # Send RESET command
                self.serial.flush()
                response = self.serial.readline().decode('utf-8', errors='ignore').strip()
                print(f"Reset response: {repr(response)}")
                
                if response == '+':
                    self.connected = True
                    print(f"Connected to Arduino on {self.port}")
                    return True
                else:
                    print(f"Failed to connect to Arduino. Response: {repr(response)}")
                    self.serial.close()
                    return False
        except Exception as e:
            print(f"Error connecting to Arduino: {e}")
            return False
    
    def disconnect(self):
        """Disconnect from the Arduino"""
        if self.serial and self.serial.is_open:
            self.serial.close()
        self.connected = False
        print("Disconnected from Arduino")
    
    def send_command(self, cmd):
        """Send a single-character command to the Arduino"""
        if not self.connected or not self.serial or not self.serial.is_open:
            print("Not connected to Arduino")
            return False
        
        try:
            # Send the command
            self.serial.write(cmd.encode('utf-8'))
            self.serial.flush()
            
            # Wait for response
            response = self.serial.readline().decode('utf-8').strip()
            success = response == '+'
            
            if not success:
                print(f"Command failed: {cmd} (Response: {response})")
            
            return success
        except Exception as e:
            print(f"Error sending command: {e}")
            return False
    
    def set_pattern(self, pattern):
        """Set the eye pattern"""
        pattern_map = {
            'idle': 'I',
            'speaking': 'S',
            'thinking': 'T',
            'listening': 'L',
            'happy': 'H',
            'sad': 'D',
            'angry': 'A',
            'reset': 'R'
        }
        
        cmd = pattern_map.get(pattern.lower())
        if not cmd:
            print(f"Unknown pattern: {pattern}")
            print(f"Available patterns: {', '.join(pattern_map.keys())}")
            return False
        
        return self.send_command(cmd)
    
    def set_brightness(self, level):
        """Set the brightness level (0-9)"""
        if not isinstance(level, int) or level < 0 or level > 9:
            print("Brightness must be between 0 and 9")
            return False
        
        return self.send_command(str(level))

def main():
    """Main function for CLI operation"""
    parser = argparse.ArgumentParser(description='Control DJ R3X Eyes')
    parser.add_argument('--port', '-p', required=True, help='Serial port (e.g., /dev/ttyACM0, COM3)')
    parser.add_argument('--pattern', '-P', help='Set pattern: idle, speaking, thinking, listening, happy, sad, angry, reset')
    parser.add_argument('--brightness', '-b', type=int, choices=range(10), help='Set brightness (0-9)')
    parser.add_argument('--interactive', '-i', action='store_true', help='Enter interactive mode')
    
    args = parser.parse_args()
    
    controller = SimpleEyesController(args.port)
    if not controller.connect():
        sys.exit(1)
    
    try:
        if args.interactive:
            print("Interactive mode. Type 'exit' to quit.")
            print("Commands:")
            print("  pattern <name> - Set pattern (idle, speaking, thinking, listening, happy, sad, angry, reset)")
            print("  brightness <0-9> - Set brightness level")
            print("  exit - Exit the program")
            
            while True:
                cmd = input("> ").strip()
                if cmd.lower() == 'exit':
                    break
                
                parts = cmd.split()
                if not parts:
                    continue
                
                if parts[0] == 'pattern' and len(parts) > 1:
                    controller.set_pattern(parts[1])
                elif parts[0] == 'brightness' and len(parts) > 1:
                    try:
                        level = int(parts[1])
                        controller.set_brightness(level)
                    except ValueError:
                        print("Brightness must be a number between 0 and 9")
                else:
                    print("Unknown command")
        else:
            # Process command line arguments
            if args.pattern:
                controller.set_pattern(args.pattern)
            
            if args.brightness is not None:
                controller.set_brightness(args.brightness)
            
            # If no arguments provided, set default pattern
            if not args.pattern and args.brightness is None:
                controller.set_pattern('idle')
    
    finally:
        controller.disconnect()

if __name__ == "__main__":
    main() 