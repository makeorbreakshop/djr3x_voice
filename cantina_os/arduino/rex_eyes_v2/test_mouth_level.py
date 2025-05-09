#!/usr/bin/env python3
"""
Test Script for DJ R3X Eyes SPEAKING Pattern with Direct Mouth Level Control
This script tests the enhanced SPEAKING animation with direct mouth_level parameter
"""

import json
import logging
import sys
import time
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

class MouthLevelTester:
    """Testing utility for the enhanced SPEAKING pattern with direct mouth level control."""
    
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
    
    def set_pattern_with_mouth_level(self, mouth_level: int, brightness: float = 0.8) -> bool:
        """Set the SPEAKING pattern with direct mouth level parameter."""
        command = {
            "command": "set_pattern",
            "params": {
                "pattern": "SPEAKING",
                "brightness": brightness,
                "mouth_level": mouth_level
            }
        }
        
        response = self._send_command(command)
        
        if response and response.get("ack") == True:
            logger.info(f"Set SPEAKING pattern with mouth_level {mouth_level}")
            return True
        else:
            logger.error(f"Failed to set pattern: {response}")
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

    def test_mouth_levels(self, duration: float = 5.0, test_type: str = "sweep") -> bool:
        """
        Test different mouth levels with the SPEAKING pattern.
        
        Args:
            duration: Test duration in seconds
            test_type: Test type ("sweep", "steps", "random", "pulse")
            
        Returns:
            True if test completed successfully
        """
        # First reset device
        self.reset()
        
        logger.info(f"Testing direct mouth level control: {test_type} for {duration} seconds")
        
        # Prepare test
        start_time = time.time()
        interval = 0.1  # 100ms updates
        success = True
        
        try:
            if test_type == "sweep":
                # Continuously sweep mouth level from 0-255 and back
                total_steps = int(duration / interval)
                for i in range(total_steps):
                    # Generate sine wave pattern
                    progress = i / total_steps * 2 * math.pi
                    mouth_level = int(127.5 + 127.5 * math.sin(progress))
                    
                    # Set pattern with mouth level
                    if not self.set_pattern_with_mouth_level(mouth_level):
                        success = False
                        break
                        
                    # Get status to verify mouth level
                    if i % 10 == 0:  # Only check every 10 steps to avoid slowing down
                        status = self.get_status()
                        if status and status.get("mouth_level") != mouth_level:
                            logger.warning(f"Mouth level mismatch: sent {mouth_level}, status reports {status.get('mouth_level')}")
                    
                    time.sleep(interval)
                    
            elif test_type == "steps":
                # Test specific mouth level steps
                levels = [0, 50, 100, 150, 200, 255]
                step_duration = duration / len(levels)
                
                for level in levels:
                    logger.info(f"Testing mouth level: {level}")
                    if not self.set_pattern_with_mouth_level(level):
                        success = False
                        break
                        
                    # Check status
                    status = self.get_status()
                    if status and status.get("mouth_level") != level:
                        logger.warning(f"Mouth level mismatch: sent {level}, status reports {status.get('mouth_level')}")
                    
                    time.sleep(step_duration)
                    
            elif test_type == "random":
                # Random mouth levels
                import random
                end_time = time.time() + duration
                
                while time.time() < end_time:
                    level = random.randint(0, 255)
                    logger.info(f"Setting random mouth level: {level}")
                    
                    if not self.set_pattern_with_mouth_level(level):
                        success = False
                        break
                        
                    time.sleep(interval)
                    
            elif test_type == "pulse":
                # Pulsing pattern (alternating high/low)
                end_time = time.time() + duration
                i = 0
                
                while time.time() < end_time:
                    level = 255 if i % 2 == 0 else 50
                    logger.info(f"Setting pulse mouth level: {level}")
                    
                    if not self.set_pattern_with_mouth_level(level):
                        success = False
                        break
                        
                    time.sleep(0.5)  # Longer interval for pulse
                    i += 1
            
            logger.info("Mouth level test completed")
            return success
            
        except KeyboardInterrupt:
            logger.info("Test interrupted by user")
            return False
        finally:
            # Reset to IDLE
            self.reset()
    
    def run_comprehensive_test(self) -> bool:
        """Run a comprehensive test of all mouth level controls."""
        logger.info("===== Running comprehensive mouth level test =====")
        
        # 1. Verify mouth_level parameter is accepted
        logger.info("1. Testing basic mouth level parameter acceptance...")
        if not self.set_pattern_with_mouth_level(127):
            logger.error("Failed basic mouth level parameter test")
            return False
            
        # 2. Verify status returns mouth_level
        logger.info("2. Verifying status includes mouth_level...")
        status = self.get_status()
        if not status or "mouth_level" not in status:
            logger.error("Status does not include mouth_level parameter")
            return False
        logger.info(f"Current status: {status}")
        
        # 3. Test boundary values
        logger.info("3. Testing boundary values...")
        for level in [0, 255]:
            logger.info(f"Testing boundary level: {level}")
            if not self.set_pattern_with_mouth_level(level):
                logger.error(f"Failed to set boundary level {level}")
                return False
            time.sleep(1)
            
        # 4. Test step pattern
        logger.info("4. Testing step pattern...")
        if not self.test_mouth_levels(3.0, "steps"):
            return False
            
        # 5. Test sweep pattern
        logger.info("5. Testing sweep pattern...")
        if not self.test_mouth_levels(3.0, "sweep"):
            return False
        
        logger.info("✅ All mouth level tests passed!")
        return True
    
    def disconnect(self) -> None:
        """Disconnect from the Arduino."""
        if self.serial and self.serial.is_open:
            # Set back to IDLE before disconnecting
            self.reset()
            time.sleep(0.2)
            self.serial.close()
            logger.info("Disconnected from Arduino")

def main():
    """Main entry point with command line argument handling."""
    parser = argparse.ArgumentParser(description='DJ R3X SPEAKING Pattern Mouth Level Tester')
    parser.add_argument('--port', help='Serial port (auto-detected if not specified)')
    parser.add_argument('--timeout', type=float, default=2.0, help='Serial timeout in seconds')
    parser.add_argument('--test', choices=['sweep', 'steps', 'random', 'pulse', 'comprehensive'], 
                     default='comprehensive', help='Test type to run')
    parser.add_argument('--duration', type=float, default=5.0, help='Test duration in seconds')
    args = parser.parse_args()
    
    # Create tester instance
    tester = MouthLevelTester(port=args.port, timeout=args.timeout)
    
    try:
        # Connect
        if not tester.connect():
            logger.error("Failed to connect to Arduino, exiting")
            sys.exit(1)
            
        # Run selected test
        if args.test == 'comprehensive':
            tester.run_comprehensive_test()
        else:
            tester.test_mouth_levels(args.duration, args.test)
            
    except KeyboardInterrupt:
        logger.info("Test interrupted by user")
    except Exception as e:
        logger.error(f"Test error: {e}")
    finally:
        tester.disconnect()

if __name__ == "__main__":
    main() 