#!/usr/bin/env python3
"""
Improved Test Script for DJ R3X Eyes Controller v2
Tests the robustness of the JSON communication with the Arduino
"""

import asyncio
import json
import logging
import sys
import time
import random
from typing import Optional, Dict, Any, List

import serial
import serial.tools.list_ports

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class ImprovedEyesTester:
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
            
            if isinstance(response, dict) and response.get("ack") == True:
                logger.info("Successfully connected to Arduino")
                if "free_mem" in response:
                    logger.info(f"Arduino free memory: {response['free_mem']} bytes")
                return True
            else:
                logger.error(f"Failed to get valid response from Arduino: {response}")
                return False
                
        except Exception as e:
            logger.error(f"Failed to connect: {e}")
            return False
            
    def _auto_detect_port(self) -> Optional[str]:
        """Auto-detect Arduino serial port."""
        logger.info("Auto-detecting Arduino port")
        
        # Common Arduino identifiers
        arduino_ids = [
            "2341:0043",  # Arduino Uno
            "2341:0001",  # Arduino Mega
            "2341:0010",  # Arduino Mega 2560
            "2341:0042",  # Arduino Mega 2560 R3
            "1A86:7523"   # CH340 USB-to-Serial (common in clones)
        ]
        
        # Try to find by device patterns first
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
                    try:
                        port_num = int(port.device.replace("COM", ""))
                        if port_num > 3:  # Usually COM1-3 are reserved for built-in ports
                            logger.info(f"Found likely Arduino at {port.device}")
                            return port.device
                    except ValueError:
                        pass
        
        # If we couldn't find by pattern, try VID:PID
        for port in ports:
            if port.vid is not None and port.pid is not None:
                vid_pid = f"{port.vid:04X}:{port.pid:04X}"
                if vid_pid.lower() in [aid.lower() for aid in arduino_ids]:
                    logger.info(f"Found Arduino at {port.device} ({vid_pid})")
                    return port.device
        
        # If all else fails, return the first port as a last resort
        if ports:
            logger.warning(f"Using first available port as fallback: {ports[0].device}")
            return ports[0].device
            
        return None
            
    def send_command(self, command: Dict[str, Any], retries: int = 3) -> Optional[Dict[str, Any]]:
        """Send a command to the Arduino with retries and improved parsing."""
        if not self.serial or not self.serial.is_open:
            raise RuntimeError("Not connected to Arduino")
            
        # Implement retry loop with exponential backoff
        for attempt in range(retries):
            try:
                # Clear any pending data
                self.serial.reset_input_buffer()
                self.serial.reset_output_buffer()
                
                # Serialize command with minimal structure
                command_json = json.dumps(command, separators=(',', ':')) + "\n"
                logger.debug(f"Sending (attempt {attempt+1}/{retries}): {command_json.strip()}")
                self.serial.write(command_json.encode())
                self.serial.flush()
                
                # Chunk-based reading approach
                timeout_time = time.time() + self.timeout
                response_buffer = bytearray()
                
                while time.time() < timeout_time:
                    if self.serial.in_waiting:
                        # Read available bytes in chunks
                        chunk = self.serial.read(self.serial.in_waiting)
                        response_buffer.extend(chunk)
                        
                        try:
                            # Try to find complete JSON messages
                            response_str = response_buffer.decode("utf-8")
                            lines = response_str.split('\n')
                            
                            for line in lines[:-1]:  # Process all complete lines
                                line = line.strip()
                                if not line:
                                    continue
                                    
                                try:
                                    response_json = json.loads(line)
                                    # Handle debug messages
                                    if "debug" in response_json:
                                        logger.debug(f"Debug: {response_json['debug']}")
                                        if "free_mem" in response_json:
                                            logger.debug(f"Arduino free memory: {response_json['free_mem']} bytes")
                                        continue
                                    # Return actual response
                                    return response_json
                                except json.JSONDecodeError as e:
                                    logger.debug(f"Incomplete JSON: {line} - {e}")
                                    
                            # Keep any incomplete message in the buffer
                            if lines[-1]:
                                response_buffer = bytearray(lines[-1].encode("utf-8"))
                            else:
                                response_buffer = bytearray()
                                
                        except UnicodeDecodeError:
                            # Keep accumulating if we can't decode yet
                            logger.warning("Unicode decode error, continuing to accumulate data")
                            continue
                            
                    time.sleep(0.01)  # Prevent CPU spinning
                
                logger.error(f"Command timed out after {self.timeout}s")
                
            except Exception as e:
                logger.error(f"Error sending command: {e}")
                
            # Calculate backoff delay for next retry
            if attempt < retries - 1:
                delay = 0.5 * (2 ** attempt) * (0.5 + 0.5 * random.random())
                logger.warning(f"Retrying in {delay:.2f}s...")
                time.sleep(delay)
                
        return None  # All retries failed
        
    def test_pattern_sequence(self, patterns: List[str], repeat: int = 1) -> bool:
        """Test a sequence of patterns with error tracking."""
        success_count = 0
        total_count = 0
        
        for _ in range(repeat):
            for pattern in patterns:
                total_count += 1
                logger.info(f"Setting pattern to: {pattern}")
                
                command = {
                    "command": "set_pattern",
                    "params": {
                        "pattern": pattern,
                        "brightness": random.uniform(0.6, 1.0)  # Random brightness for stress testing
                    }
                }
                
                response = self.send_command(command)
                
                if response and response.get("ack") == True:
                    logger.info(f"Pattern '{pattern}' set successfully!")
                    success_count += 1
                else:
                    logger.error(f"Failed to set pattern '{pattern}'")
                    
                # Randomized delay between commands
                delay = random.uniform(0.1, 0.5)
                time.sleep(delay)
                
        success_rate = (success_count / total_count) * 100 if total_count > 0 else 0
        logger.info(f"Pattern test complete - Success rate: {success_rate:.1f}% ({success_count}/{total_count})")
        return success_count == total_count
        
    def test_stress(self, iterations: int = 20) -> bool:
        """Stress test with rapid commands and error recovery."""
        patterns = ["IDLE", "LISTENING", "THINKING", "SPEAKING", "HAPPY", "SAD", "ANGRY", "ERROR"]
        
        logger.info(f"Starting stress test with {iterations} iterations")
        
        # Alternating between valid and invalid patterns for error handling test
        valid_commands = 0
        invalid_commands = 0
        successful_commands = 0
        
        for i in range(iterations):
            # Every third command, send an intentionally malformed command
            if i % 3 == 0:
                invalid_commands += 1
                # Test with intentionally malformed JSON
                if i % 6 == 0:
                    logger.info("Sending malformed JSON")
                    self.serial.write(b"{'bad_json': true}\n")
                else:
                    logger.info("Sending command with missing parameters")
                    command = {"command": "set_pattern"}  # Missing params
                    self.send_command(command)
            else:
                valid_commands += 1
                # Send valid command
                pattern = random.choice(patterns)
                brightness = random.uniform(0.3, 1.0)
                
                logger.info(f"Sending valid command: pattern={pattern}, brightness={brightness:.2f}")
                command = {
                    "command": "set_pattern",
                    "params": {
                        "pattern": pattern,
                        "brightness": brightness
                    }
                }
                
                response = self.send_command(command)
                if response and response.get("ack") == True:
                    successful_commands += 1
                    
            # Short random delay between commands
            delay = random.uniform(0.05, 0.2)
            time.sleep(delay)
            
        # Reset to IDLE state after stress test
        logger.info("Resetting to IDLE state")
        command = {
            "command": "set_pattern",
            "params": {
                "pattern": "IDLE",
                "brightness": 0.8
            }
        }
        self.send_command(command)
        
        success_rate = (successful_commands / valid_commands) * 100 if valid_commands > 0 else 0
        logger.info(f"Stress test complete:")
        logger.info(f"  Valid commands: {valid_commands}")
        logger.info(f"  Invalid commands: {invalid_commands}")
        logger.info(f"  Successful commands: {successful_commands}")
        logger.info(f"  Success rate: {success_rate:.1f}%")
        
        return success_rate > 90  # Consider stress test passed if >90% success
        
    def disconnect(self):
        """Close the serial connection."""
        if self.serial and self.serial.is_open:
            # Reset to IDLE before disconnecting
            command = {
                "command": "set_pattern",
                "params": {
                    "pattern": "IDLE",
                    "brightness": 0.8
                }
            }
            self.send_command(command)
            time.sleep(0.5)
            
            self.serial.close()
            logger.info("Disconnected from Arduino")


def main():
    """Main test routine."""
    logger.info("DJ R3X Eyes Controller - Improved Test Script")
    
    tester = ImprovedEyesTester()
    
    try:
        if not tester.connect():
            logger.error("Failed to connect to Arduino. Tests aborted.")
            return
            
        # Test basic patterns
        patterns = ["IDLE", "LISTENING", "THINKING", "SPEAKING", "HAPPY", "SAD", "ANGRY", "ERROR"]
        logger.info("Running basic pattern test")
        tester.test_pattern_sequence(patterns)
        
        # Ask user if they want to continue with stress test
        print("\nWould you like to run the stress test? (y/n)")
        response = input().lower()
        if response.startswith('y'):
            tester.test_stress(iterations=30)
            
    except KeyboardInterrupt:
        logger.info("Test interrupted by user")
    except Exception as e:
        logger.error(f"Test error: {e}")
    finally:
        if tester:
            tester.disconnect()


if __name__ == "__main__":
    main() 