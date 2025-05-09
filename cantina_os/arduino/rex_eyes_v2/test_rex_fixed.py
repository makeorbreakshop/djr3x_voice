#!/usr/bin/env python3
"""
Fixed Test Script for DJ R3X Eyes Controller v2
Implements improved communication protocol to match Arduino fixes
"""

import json
import logging
import time
import serial
import serial.tools.list_ports
import sys
import argparse

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class REXEyesTester:
    def __init__(self, port=None, baud_rate=115200, timeout=3.0):
        self.port = port
        self.baud_rate = baud_rate
        self.timeout = timeout
        self.serial = None
        
    def connect(self):
        """Connect to the Arduino."""
        if not self.port:
            self.port = self._auto_detect_port()
            if not self.port:
                logger.error("Could not find Arduino port")
                return False
        
        try:
            logger.info(f"Connecting to Arduino at {self.port}")
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
            logger.info("Testing connection...")
            response = self.send_command({"command": "test"})
            
            if response and response.get("ack") == True:
                logger.info(f"✓ Connection successful!")
                if "free_mem" in response:
                    logger.info(f"Arduino free memory: {response['free_mem']} bytes")
                return True
            else:
                logger.error(f"Connection test failed: {response}")
                return False
        
        except Exception as e:
            logger.error(f"Connection error: {e}")
            return False
            
    def _auto_detect_port(self):
        """Auto-detect the Arduino serial port."""
        system_name = sys.platform
        ports = list(serial.tools.list_ports.comports())
        
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
        
    def send_command(self, command, retries=3):
        """Send a command to the Arduino with improved handling."""
        if not self.serial or not self.serial.is_open:
            logger.error("Not connected to Arduino")
            return None
        
        for attempt in range(retries):
            try:
                # Clear any pending data
                self.serial.reset_input_buffer()
                
                # Serialize the command with minimal whitespace
                command_json = json.dumps(command, separators=(',', ':'))
                logger.debug(f"Sending (attempt {attempt+1}/{retries}): {command_json}")
                
                # Send the command with a newline
                self.serial.write(command_json.encode() + b'\n')
                self.serial.flush()
                
                # Read response with timeout
                response = self._read_response()
                if response:
                    return response
                
                # Delay before retry with exponential backoff
                retry_delay = 0.2 * (2 ** attempt)
                logger.warning(f"No response, retrying in {retry_delay:.2f}s...")
                time.sleep(retry_delay)
                
            except Exception as e:
                logger.error(f"Command error: {e}")
                time.sleep(0.5)
        
        logger.error(f"Command failed after {retries} attempts")
        return None
    
    def _read_response(self):
        """Read response from Arduino with improved chunked reading."""
        response_data = bytearray()
        start_time = time.time()
        
        while (time.time() - start_time) < self.timeout:
            if self.serial.in_waiting:
                chunk = self.serial.read(self.serial.in_waiting)
                response_data.extend(chunk)
                
                # Try to decode what we have
                try:
                    response_text = response_data.decode('utf-8')
                    logger.debug(f"Raw response: {response_text.strip()}")
                    
                    # Split into lines
                    lines = response_text.split('\n')
                    
                    # Process complete lines
                    for line in lines:
                        line = line.strip()
                        if not line:
                            continue
                            
                        try:
                            json_response = json.loads(line)
                            
                            # Handle debug messages
                            if "debug" in json_response:
                                logger.debug(f"Arduino debug: {json_response['debug']}")
                                if "free_mem" in json_response:
                                    logger.debug(f"Arduino memory: {json_response['free_mem']} bytes")
                                continue
                                
                            # Return actual response
                            return json_response
                        except json.JSONDecodeError:
                            logger.warning(f"Invalid JSON in response: {line}")
                    
                    # If we reach here, we haven't found a valid response yet
                    
                except UnicodeDecodeError:
                    # Continue accumulating data if we can't decode yet
                    pass
                    
            # Brief pause to prevent CPU spinning
            time.sleep(0.01)
        
        logger.error(f"Response timeout after {self.timeout}s")
        return None
    
    def set_pattern(self, pattern_name, brightness=0.8):
        """Set a pattern with brightness."""
        logger.info(f"Setting pattern: {pattern_name} (brightness: {brightness:.2f})")
        
        command = {
            "command": "set_pattern",
            "params": {
                "pattern": pattern_name,
                "brightness": brightness
            }
        }
        
        response = self.send_command(command)
        
        if response and response.get("ack") == True:
            logger.info(f"✓ Pattern set successfully: {pattern_name}")
            return True
        else:
            if response and "error" in response:
                logger.error(f"✗ Failed to set pattern {pattern_name}: {response}")
            else:
                logger.error(f"✗ No valid response when setting pattern: {pattern_name}")
            return False
    
    def test_all_patterns(self):
        """Test all available eye patterns."""
        patterns = [
            "IDLE", "STARTUP", "LISTENING", "THINKING", 
            "SPEAKING", "HAPPY", "SAD", "ANGRY", 
            "SURPRISED", "ERROR"
        ]
        
        logger.info("==== Testing All Patterns ====")
        
        success_count = 0
        for pattern in patterns:
            # Small delay between patterns
            time.sleep(0.5)
            
            # Test the pattern
            success = self.set_pattern(pattern)
            if success:
                success_count += 1
                
            # Wait to observe the pattern
            time.sleep(2)
                
        # Print summary
        logger.info(f"==== Pattern Test Summary ====")
        logger.info(f"Successful: {success_count}/{len(patterns)}")
        
        if success_count == len(patterns):
            logger.info("✓ All patterns working correctly!")
        else:
            failed = len(patterns) - success_count
            logger.info(f"✗ {failed} patterns failed")
            
        # Return to IDLE
        self.set_pattern("IDLE")
        return success_count == len(patterns)
    
    def test_speaking_animation(self, duration=10):
        """Test the SPEAKING animation with varying mouth levels."""
        logger.info(f"Testing SPEAKING animation with mouth levels for {duration} seconds")
        
        # Set speaking pattern first
        if not self.set_pattern("SPEAKING"):
            logger.error("Failed to set SPEAKING pattern")
            return False
            
        # Animate for the specified duration
        start_time = time.time()
        level_index = 0
        
        try:
            while (time.time() - start_time) < duration:
                # Calculate mouth level (0-255) in a sine wave pattern
                t = (time.time() - start_time) * 2  # Speed factor
                mouth_level = int(127.5 + 127.5 * __import__('math').sin(t))
                
                # Update the mouth level every 100ms
                command = {
                    "command": "set_pattern",
                    "params": {
                        "pattern": "SPEAKING",
                        "brightness": 0.8,
                        "mouth_level": mouth_level
                    }
                }
                
                self.send_command(command)
                
                # Short delay (100ms)
                time.sleep(0.1)
                level_index += 1
                
            logger.info("SPEAKING animation test complete")
            return True
            
        except KeyboardInterrupt:
            logger.info("Test interrupted by user")
            return False
        finally:
            # Set back to IDLE
            self.set_pattern("IDLE")
    
    def disconnect(self):
        """Close the serial connection."""
        if self.serial and self.serial.is_open:
            # Set back to IDLE before disconnecting
            self.set_pattern("IDLE")
            time.sleep(0.5)
            
            self.serial.close()
            logger.info("Disconnected from Arduino")

def main():
    parser = argparse.ArgumentParser(description='DJ R3X Eyes Controller Test Script')
    parser.add_argument('--port', help='Serial port (auto-detected if not specified)')
    parser.add_argument('--timeout', type=float, default=3.0, help='Serial timeout in seconds')
    parser.add_argument('--test', choices=['all', 'speaking'], default='all',
                      help='Test to run (all patterns or speaking animation)')
    parser.add_argument('--duration', type=int, default=10,
                      help='Duration for animation tests in seconds')
    args = parser.parse_args()
    
    tester = REXEyesTester(port=args.port, timeout=args.timeout)
    
    try:
        if not tester.connect():
            logger.error("Failed to connect to Arduino. Exiting.")
            sys.exit(1)
            
        if args.test == 'all':
            tester.test_all_patterns()
        elif args.test == 'speaking':
            tester.test_speaking_animation(args.duration)
            
    except KeyboardInterrupt:
        logger.info("Test interrupted by user")
    except Exception as e:
        logger.error(f"Test error: {e}")
    finally:
        tester.disconnect()

if __name__ == "__main__":
    main() 