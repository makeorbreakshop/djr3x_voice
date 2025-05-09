#!/usr/bin/env python3
"""
Test script for DJ R3X Eyes Controller v2
This script tests the communication with the Arduino code
"""

import asyncio
import json
import logging
import sys
import time
from typing import Optional

import serial
import serial.tools.list_ports

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,  # Changed to DEBUG for more verbose output
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class EyeControllerTester:
    def __init__(
        self,
        port: Optional[str] = None,
        baud_rate: int = 115200,
        timeout: float = 2.0  # Increased default timeout
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
            logger.info(f"Attempting to connect to {self.port}")
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
            
            # Test the connection
            logger.info("Testing connection...")
            test_cmd = {"command": "test"}
            response = self.send_command(test_cmd)
            
            if response.get("ack") == True and response.get("status") == "test_complete":
                logger.info("Successfully connected to Arduino")
                return True
            else:
                logger.error(f"Failed to get valid response from Arduino: {response}")
                return False
                
        except Exception as e:
            logger.error(f"Failed to connect: {e}")
            return False
            
    def _auto_detect_port(self) -> Optional[str]:
        """Auto-detect the Arduino serial port."""
        ports = list(serial.tools.list_ports.comports())
        
        logger.info("Available ports:")
        for port in ports:
            logger.info(f"  {port.device}: {port.description}")
            
        # On macOS, look for usbmodem devices first
        for port in ports:
            if "usbmodem" in port.device:
                logger.info(f"Found likely Arduino at {port.device} (usbmodem)")
                return port.device
                
        # Then try Arduino or CH340 identifiers
        for port in ports:
            if ("Arduino" in port.description or 
                "CH340" in port.description or 
                "USB" in port.description):
                logger.info(f"Found likely Arduino at {port.device}")
                return port.device
                
        # If still not found, try to use any available port
        if ports:
            logger.warning("No Arduino identifiers found, using first available port")
            return ports[0].device
            
        return None
        
    def disconnect(self):
        """Disconnect from the Arduino."""
        if self.serial and self.serial.is_open:
            self.serial.close()
            logger.info("Disconnected from Arduino")
            
    def send_command(self, command: dict) -> dict:
        """Send a command to the Arduino and get the response using chunk-based reading."""
        if not self.serial or not self.serial.is_open:
            raise RuntimeError("Not connected to Arduino")
            
        try:
            # Clear any pending data
            self.serial.reset_input_buffer()
            self.serial.reset_output_buffer()
            
            # Send command with minimal JSON structure
            command_json = json.dumps(command, separators=(',', ':')) + "\n"
            logger.debug(f"Sending: {command_json.strip()}")
            self.serial.write(command_json.encode())
            self.serial.flush()
            
            # Chunk-based reading approach
            timeout = time.time() + self.timeout
            response_buffer = []  # Changed from bytearray to list of bytes
            
            while time.time() < timeout:
                if self.serial.in_waiting:
                    # Read available bytes in chunks
                    chunk = self.serial.read(self.serial.in_waiting)
                    response_buffer.extend(list(chunk))  # Convert chunk to list before extending
                    
                    try:
                        # Try to find complete JSON messages
                        response_str = bytes(response_buffer).decode()
                        messages = response_str.split('\n')
                        
                        for msg in messages[:-1]:  # Process all complete messages
                            msg = msg.strip()
                            if not msg:
                                continue
                                
                            try:
                                response_json = json.loads(msg)
                                # Handle debug messages
                                if "debug" in response_json:
                                    logger.debug(f"Debug: {response_json['debug']}")
                                    continue
                                # Return actual response
                                return response_json
                            except json.JSONDecodeError:
                                logger.debug(f"Incomplete JSON: {msg}")
                                
                        # Keep any incomplete message in the buffer
                        if messages[-1].strip():
                            response_buffer = list(messages[-1].encode())
                        else:
                            response_buffer = []
                            
                    except UnicodeDecodeError:
                        # Keep accumulating if we can't decode yet
                        continue
                        
                time.sleep(0.01)  # Prevent CPU spinning
                
            logger.error("Command timeout")
            return {"error": "Command timeout"}
                    
        except Exception as e:
            logger.error(f"Error in send_command: {e}")
            return {"error": str(e)}
            
    def test_pattern(self, pattern: str, brightness: float = 0.8, duration: float = 2.0):
        """Test a specific eye pattern."""
        command = {
            "command": "set_pattern",
            "params": {
                "pattern": pattern,
                "brightness": round(brightness, 2)  # Round to 2 decimal places
            }
        }
        
        # Debug: Print exact JSON being sent
        command_str = json.dumps(command, separators=(',', ':'))
        logger.debug(f"Sending command as exact string: {command_str}")
        
        logger.info(f"Testing pattern: {pattern}")
        response = self.send_command(command)
        
        if response.get("ack") == True and response.get("pattern") == pattern:
            logger.info(f"Pattern {pattern} set successfully")
        else:
            logger.error(f"Failed to set pattern: {response}")
            
        # Wait for animation to play
        time.sleep(duration)
        
        # Small delay between commands
        time.sleep(0.1)
        
    def run_test_sequence(self):
        """Run a sequence of test patterns."""
        patterns = [
            ("IDLE", 0.5),
            ("STARTUP", 0.8),
            ("LISTENING", 0.6),
            ("THINKING", 0.7),
            ("SPEAKING", 1.0),
            ("HAPPY", 0.8),
            ("SAD", 0.4),
            ("ANGRY", 0.6),
            ("SURPRISED", 0.5),
            ("ERROR", 0.7)
        ]
        
        for pattern, brightness in patterns:
            self.test_pattern(pattern, brightness)
            
def main():
    # Parse command line arguments
    import argparse
    parser = argparse.ArgumentParser(description='Test DJ R3X Eyes Controller')
    parser.add_argument('--port', help='Serial port to use')
    parser.add_argument('--timeout', type=float, default=2.0,
                      help='Serial timeout in seconds')
    args = parser.parse_args()
    
    # Create and run tester
    tester = EyeControllerTester(port=args.port, timeout=args.timeout)
    
    try:
        if tester.connect():
            tester.run_test_sequence()
        else:
            sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Test interrupted by user")
    except Exception as e:
        logger.error(f"Test failed: {e}")
        sys.exit(1)
    finally:
        tester.disconnect()
        
if __name__ == "__main__":
    main() 