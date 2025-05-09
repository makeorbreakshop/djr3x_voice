#!/usr/bin/env python3
"""
Test Script for DJ R3X Eyes SPEAKING Animation Pattern
This script specifically tests the SPEAKING animation with mouth level simulation
"""

import json
import logging
import sys
import time
import random
import math
import argparse
from typing import Optional, Dict, Any

import serial
import serial.tools.list_ports

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class SpeakingPatternTester:
    """Testing utility for the SPEAKING pattern with mouth level simulation."""
    
    def __init__(
        self, 
        port: Optional[str] = None,
        baud_rate: int = 115200,
        timeout: float = 2.0
    ):
        self.port = port
        self.baud_rate = baud_rate
        self.timeout = timeout
        self.serial = None
        
    def connect(self) -> bool:
        """Connect to the Arduino."""
        if not self.port:
            self.port = self._auto_detect_port()
            if not self.port:
                logger.error("Could not find Arduino port")
                return False
                
        try:
            logger.info(f"Connecting to Arduino at {self.port}...")
            self.serial = serial.Serial(
                port=self.port,
                baudrate=self.baud_rate,
                timeout=self.timeout
            )
            
            # Wait for Arduino reset and startup
            time.sleep(2)
            
            # Clear any startup messages
            self.serial.reset_input_buffer()
            self.serial.reset_output_buffer()
            
            # Test connection
            if self._test_connection():
                logger.info("Connection successful!")
                return True
            else:
                logger.error("Connection test failed")
                return False
                
        except Exception as e:
            logger.error(f"Connection failed: {e}")
            return False
    
    def _test_connection(self) -> bool:
        """Test the Arduino connection."""
        test_cmd = {"command": "test"}
        response = self._send_command(test_cmd)
        
        if response and response.get("ack") == True:
            if "free_mem" in response:
                logger.info(f"Arduino free memory: {response['free_mem']} bytes")
            return True
        return False
    
    def _auto_detect_port(self) -> Optional[str]:
        """Auto-detect the Arduino serial port."""
        ports = list(serial.tools.list_ports.comports())
        system_name = sys.platform
        
        if system_name == "darwin":  # macOS
            for port in ports:
                if "usbmodem" in port.device or "usbserial" in port.device:
                    logger.info(f"Found likely Arduino at {port.device}")
                    return port.device
        elif system_name == "linux":
            for port in ports:
                if "ttyACM" in port.device or "ttyUSB" in port.device:
                    logger.info(f"Found likely Arduino at {port.device}")
                    return port.device
        elif system_name.startswith("win"):
            for port in ports:
                if port.device.startswith("COM"):
                    logger.info(f"Found likely Arduino at {port.device}")
                    return port.device
        
        # If all else fails, return the first port
        if ports:
            logger.warning(f"Using first available port as fallback: {ports[0].device}")
            return ports[0].device
            
        return None
    
    def _send_command(self, command: Dict[str, Any], retries: int = 3) -> Optional[Dict[str, Any]]:
        """Send a command to the Arduino and get the response."""
        if not self.serial or not self.serial.is_open:
            logger.error("Not connected to Arduino")
            return None
            
        for attempt in range(retries):
            try:
                # Clear buffers
                self.serial.reset_input_buffer()
                self.serial.reset_output_buffer()
                
                # Send command
                command_json = json.dumps(command, separators=(',', ':')) + "\n"
                logger.debug(f"Sending: {command_json.strip()}")
                self.serial.write(command_json.encode())
                self.serial.flush()
                
                # Read response
                response = self._read_response()
                if response:
                    return response
                
                # Try again with delay
                logger.warning(f"Attempt {attempt+1}/{retries} failed, retrying...")
                time.sleep(0.2 * (attempt + 1))  # Increasing delay
                
            except Exception as e:
                logger.error(f"Command error: {e}")
                time.sleep(0.1)
                
        logger.error(f"Command failed after {retries} attempts")
        return None
    
    def _read_response(self) -> Optional[Dict[str, Any]]:
        """Read and parse response from Arduino."""
        buffer = bytearray()
        start_time = time.time()
        
        while (time.time() - start_time) < self.timeout:
            if self.serial.in_waiting:
                chunk = self.serial.read(self.serial.in_waiting)
                buffer.extend(chunk)
                
                try:
                    response_str = buffer.decode('utf-8')
                    lines = response_str.split('\n')
                    
                    # Process complete lines
                    for line in lines[:-1]:
                        line = line.strip()
                        if not line:
                            continue
                        
                        try:
                            data = json.loads(line)
                            # Skip debug messages but log them
                            if "debug" in data:
                                logger.debug(f"Arduino debug: {data['debug']}")
                                if "free_mem" in data:
                                    logger.debug(f"Arduino memory: {data['free_mem']} bytes")
                                continue
                            
                            # Return actual response
                            return data
                        except json.JSONDecodeError as e:
                            logger.warning(f"JSON parse error: {e}")
                    
                    # Keep incomplete data in buffer
                    if lines[-1]:
                        buffer = bytearray(lines[-1].encode('utf-8'))
                    else:
                        buffer = bytearray()
                        
                except UnicodeDecodeError:
                    pass
                    
            time.sleep(0.01)
            
        logger.error("Response timeout")
        return None
    
    def set_pattern(self, pattern: str, brightness: float = 0.8) -> bool:
        """Set a specific eye pattern."""
        command = {
            "command": "set_pattern",
            "params": {
                "pattern": pattern,
                "brightness": brightness
            }
        }
        
        response = self._send_command(command)
        
        if response and response.get("ack") == True and response.get("pattern") == pattern:
            logger.info(f"Pattern {pattern} set successfully")
            return True
        else:
            logger.error(f"Failed to set pattern {pattern}: {response}")
            return False
    
    def get_status(self) -> Optional[Dict[str, Any]]:
        """Get the current status of the Arduino."""
        command = {"command": "status"}
        return self._send_command(command)
    
    def reset(self) -> bool:
        """Reset the Arduino to default state."""
        command = {"command": "reset"}
        response = self._send_command(command)
        
        if response and response.get("ack") == True:
            logger.info("Reset successful")
            return True
        else:
            logger.error(f"Reset failed: {response}")
            return False

    def test_speaking_pattern(self, duration: float = 10.0, animation_style: str = "sine") -> bool:
        """
        Test the SPEAKING pattern with simulated mouth levels.
        
        Args:
            duration: Test duration in seconds
            animation_style: Animation style to use ("sine", "random", "pulse", "wave")
            
        Returns:
            True if test completed successfully
        """
        # First reset device
        self.reset()
        
        # Set speaking pattern
        if not self.set_pattern("SPEAKING"):
            return False
            
        logger.info(f"Testing SPEAKING pattern with {animation_style} animation for {duration} seconds")
        
        # Simulate mouth levels according to specified style
        start_time = time.time()
        interval = 0.1  # 100ms updates
        
        i = 0
        try:
            while (time.time() - start_time) < duration:
                # Generate mouth level based on animation style
                if animation_style == "sine":
                    # Smooth sine wave
                    mouth_level = int(127.5 + 127.5 * math.sin(i * 0.2))
                elif animation_style == "random":
                    # Random values
                    mouth_level = random.randint(0, 255)
                elif animation_style == "pulse":
                    # Pulsing pattern
                    mouth_level = 255 if (i % 10) < 5 else 50
                elif animation_style == "wave":
                    # Sawtooth wave
                    mouth_level = int((i % 20) * 12.75)  # 0-255 over 20 steps
                else:
                    mouth_level = 127  # Default mid-level
                
                # Send mouth level command
                self._send_mouth_level(mouth_level)
                
                # Brief pause
                time.sleep(interval)
                i += 1
                
            logger.info("SPEAKING pattern test completed")
            return True
            
        except KeyboardInterrupt:
            logger.info("Test interrupted by user")
            return False
        finally:
            # Reset to IDLE
            self.set_pattern("IDLE")
    
    def _send_mouth_level(self, level: int) -> None:
        """
        Send mouth level to Arduino for SPEAKING animation.
        
        Args:
            level: Mouth level (0-255)
        """
        # Currently the Arduino code doesn't support a direct mouth level command
        # So we simulate it by rapidly toggling the pattern on off with different brightness
        # This is a workaround until proper mouth level support is added
        
        # Map level to brightness (0.3-1.0)
        brightness = 0.3 + (level / 255.0) * 0.7
        
        # Update pattern with new brightness
        command = {
            "command": "set_pattern",
            "params": {
                "pattern": "SPEAKING",
                "brightness": brightness
            }
        }
        
        # Send without waiting for response to maintain speed
        if self.serial and self.serial.is_open:
            command_json = json.dumps(command, separators=(',', ':')) + "\n"
            self.serial.write(command_json.encode())
            self.serial.flush()
            
            # Read and discard any responses to keep buffer clear
            if self.serial.in_waiting:
                self.serial.read(self.serial.in_waiting)
    
    def diagnose_speaking_pattern(self) -> None:
        """Run diagnostic tests for the SPEAKING pattern."""
        logger.info("==== SPEAKING Pattern Diagnostics ====")
        
        # 1. Test basic pattern setting
        logger.info("1. Testing basic pattern setting...")
        if not self.set_pattern("SPEAKING"):
            logger.error("Failed to set SPEAKING pattern - check JSON communication")
            return
            
        # 2. Check if animation is active
        logger.info("2. Checking animation activity (observe the eyes for 2 seconds)...")
        time.sleep(2)
        
        # 3. Test brightness control
        logger.info("3. Testing brightness control...")
        for brightness in [0.2, 0.5, 0.8, 1.0]:
            logger.info(f"Setting brightness to {brightness}")
            self.set_pattern("SPEAKING", brightness=brightness)
            time.sleep(1)
            
        # 4. Test animation with mouth levels
        logger.info("4. Testing animation with simulated mouth levels (observe for 3 seconds)...")
        for _ in range(30):  # 3 seconds with 100ms intervals
            mouth_level = random.randint(0, 255)
            self._send_mouth_level(mouth_level)
            time.sleep(0.1)
            
        # 5. Test pattern switching
        logger.info("5. Testing pattern switching...")
        self.set_pattern("IDLE")
        time.sleep(0.5)
        self.set_pattern("SPEAKING")
        time.sleep(0.5)
        
        logger.info("SPEAKING pattern diagnostics completed")
    
    def disconnect(self) -> None:
        """Disconnect from the Arduino."""
        if self.serial and self.serial.is_open:
            # Set back to IDLE before disconnecting
            self.set_pattern("IDLE")
            time.sleep(0.2)
            self.serial.close()
            logger.info("Disconnected from Arduino")

def main():
    """Main entry point with command line argument handling."""
    parser = argparse.ArgumentParser(description='DJ R3X Eyes SPEAKING Pattern Tester')
    parser.add_argument('--port', help='Serial port (auto-detected if not specified)')
    parser.add_argument('--timeout', type=float, default=2.0, help='Serial timeout in seconds')
    parser.add_argument('--duration', type=float, default=10.0, help='Test duration in seconds')
    parser.add_argument('--style', choices=['sine', 'random', 'pulse', 'wave'], default='sine',
                      help='Animation style for mouth level simulation')
    parser.add_argument('--diagnose', action='store_true', help='Run diagnostics instead of animation')
    args = parser.parse_args()
    
    # Create tester instance
    tester = SpeakingPatternTester(port=args.port, timeout=args.timeout)
    
    try:
        # Connect
        if not tester.connect():
            logger.error("Failed to connect to Arduino, exiting")
            sys.exit(1)
            
        # Run test or diagnostics
        if args.diagnose:
            tester.diagnose_speaking_pattern()
        else:
            tester.test_speaking_pattern(duration=args.duration, animation_style=args.style)
            
    except KeyboardInterrupt:
        logger.info("Test interrupted by user")
    except Exception as e:
        logger.error(f"Test error: {e}")
    finally:
        tester.disconnect()

if __name__ == "__main__":
    main() 