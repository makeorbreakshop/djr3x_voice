#!/usr/bin/env python3
"""
Comprehensive Pattern Test for DJ R3X Eyes Controller
This script systematically tests all animation patterns with validation
and debugging capabilities to ensure proper functionality.
"""

import json
import logging
import sys
import time
import argparse
from typing import Optional, Dict, Any, List, Tuple

import serial
import serial.tools.list_ports

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class EyePatternTester:
    """Comprehensive tester for DJ R3X eye animation patterns."""
    
    def __init__(
        self,
        port: Optional[str] = None,
        baud_rate: int = 115200,
        timeout: float = 2.0,
        debug: bool = True
    ):
        self.port = port
        self.baud_rate = baud_rate
        self.timeout = timeout
        self.debug = debug
        self.serial = None
        self.results = {}
        
    def connect(self) -> bool:
        """Connect to the Arduino with auto-detection if port not specified."""
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
            
            # Check if connected
            return self._test_connection()
                
        except Exception as e:
            logger.error(f"Connection failed: {e}")
            return False
    
    def _test_connection(self) -> bool:
        """Test the connection to ensure the Arduino is responding properly."""
        logger.info("Testing connection...")
        test_cmd = {"command": "test"}
        response = self.send_command(test_cmd)
        
        if response and response.get("ack") == True:
            logger.info("Connected successfully!")
            if "free_mem" in response:
                logger.info(f"Arduino free memory: {response['free_mem']} bytes")
            return True
        else:
            logger.error(f"Connection test failed: {response}")
            return False
            
    def _auto_detect_port(self) -> Optional[str]:
        """Auto-detect the Arduino serial port."""
        logger.info("Auto-detecting Arduino port...")
        ports = list(serial.tools.list_ports.comports())
        
        # Try to find by common Arduino identifiers
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
        
    def send_command(self, command: Dict[str, Any], retries: int = 3, delay: float = 0.1) -> Optional[Dict[str, Any]]:
        """
        Send a command to the Arduino and get the response with retry mechanism.
        
        Args:
            command: The command to send as a dictionary
            retries: Number of retry attempts
            delay: Delay between retries in seconds
            
        Returns:
            The parsed JSON response or None on failure
        """
        if not self.serial or not self.serial.is_open:
            logger.error("Not connected to Arduino")
            return None
            
        for attempt in range(retries):
            try:
                # Ensure buffers are clear
                self.serial.reset_input_buffer()
                self.serial.reset_output_buffer()
                
                # Send command
                command_json = json.dumps(command, separators=(',', ':')) + "\n"
                logger.debug(f"Sending (attempt {attempt+1}/{retries}): {command_json.strip()}")
                self.serial.write(command_json.encode())
                self.serial.flush()
                
                # Read response with timeout
                response = self._read_response()
                
                if response:
                    return response
                    
                logger.warning(f"Attempt {attempt+1}/{retries} failed, retrying...")
                time.sleep(delay * (2 ** attempt))  # Exponential backoff
                
            except Exception as e:
                logger.error(f"Command error (attempt {attempt+1}): {e}")
                time.sleep(delay)
                
        logger.error(f"Command failed after {retries} attempts")
        return None
        
    def _read_response(self) -> Optional[Dict[str, Any]]:
        """Read and parse response from Arduino with robust handling of partial data."""
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
                            logger.warning(f"JSON parse error: {e} - Data: {line}")
                    
                    # Keep incomplete data in buffer
                    if lines[-1]:
                        buffer = bytearray(lines[-1].encode('utf-8'))
                    else:
                        buffer = bytearray()
                        
                except UnicodeDecodeError:
                    # Continue collecting data
                    pass
                    
            time.sleep(0.01)  # Prevent CPU spinning
            
        logger.error("Response timeout")
        return None
        
    def test_all_patterns(self, duration: float = 1.5) -> Dict[str, bool]:
        """
        Test all available animation patterns and report success/failure.
        
        Args:
            duration: How long to observe each pattern in seconds
            
        Returns:
            Dictionary of pattern names with success/failure status
        """
        patterns = [
            "IDLE",
            "STARTUP", 
            "LISTENING",
            "THINKING",
            "SPEAKING",
            "HAPPY",
            "SAD",
            "ANGRY",
            "SURPRISED",
            "ERROR"
        ]
        
        results = {}
        logger.info("==== Testing All Patterns ====")
        
        # First, reset to ensure clean state
        reset_result = self.send_command({"command": "reset"})
        if not reset_result or reset_result.get("ack") != True:
            logger.error("Failed to reset device, test may be unreliable")
        
        # Test each pattern
        for pattern in patterns:
            logger.info(f"Testing pattern: {pattern}")
            success = self.test_pattern(pattern, duration=duration)
            results[pattern] = success
            time.sleep(0.5)  # Brief pause between patterns
        
        # Save results
        self.results = results
        
        # Print summary
        self._print_test_summary(results)
        
        return results
    
    def test_pattern(self, pattern: str, brightness: float = 0.8, duration: float = 1.5) -> bool:
        """
        Test a specific eye pattern.
        
        Args:
            pattern: The pattern name to test
            brightness: Brightness level (0.0-1.0)
            duration: How long to observe the pattern in seconds
            
        Returns:
            True if pattern was set successfully, False otherwise
        """
        command = {
            "command": "set_pattern",
            "params": {
                "pattern": pattern,
                "brightness": brightness
            }
        }
        
        # Debug output
        if self.debug:
            logger.debug(f"Pattern command: {json.dumps(command, indent=2)}")
        
        # Send command
        response = self.send_command(command)
        
        if not response:
            logger.error(f"No response when setting pattern: {pattern}")
            return False
            
        success = response.get("ack") == True and response.get("pattern") == pattern
        
        if success:
            logger.info(f"✓ Pattern {pattern} set successfully")
            # Observe animation
            time.sleep(duration)
            return True
        else:
            logger.error(f"✗ Failed to set pattern {pattern}: {response}")
            return False
    
    def test_animation_sequence(self, cycles: int = 2) -> bool:
        """
        Test dynamic animation patterns (those with animation steps) more thoroughly.
        
        Args:
            cycles: Number of animation cycles to observe
            
        Returns:
            True if all animations appear to work correctly
        """
        animated_patterns = [
            ("THINKING", 2.0),   # Rotating animation
            ("SPEAKING", 2.0),   # Vertical animation
            ("LISTENING", 2.0),  # Pulsing animation
            ("ERROR", 2.0)       # Blinking animation
        ]
        
        logger.info("==== Testing Animation Sequences ====")
        success = True
        
        for pattern, duration in animated_patterns:
            logger.info(f"Testing animation sequence for {pattern} ({cycles} cycles)")
            # Set pattern
            pattern_result = self.test_pattern(pattern, duration=duration * cycles)
            
            if not pattern_result:
                success = False
                
            # Brief pause between animations
            time.sleep(0.5)
            
        return success
    
    def test_stress(self, iterations: int = 10, delay: float = 0.2) -> bool:
        """
        Stress test by rapidly changing between patterns.
        
        Args:
            iterations: Number of pattern changes to perform
            delay: Delay between pattern changes in seconds
            
        Returns:
            True if stress test completes without errors
        """
        patterns = ["IDLE", "HAPPY", "SAD", "ANGRY", "SURPRISED", "ERROR"]
        logger.info(f"==== Running Stress Test ({iterations} iterations) ====")
        
        failures = 0
        for i in range(iterations):
            pattern = patterns[i % len(patterns)]
            logger.info(f"Stress test iteration {i+1}/{iterations}: {pattern}")
            
            success = self.test_pattern(pattern, duration=0.1)
            if not success:
                failures += 1
                if failures > 3:  # Abort after 3 failures
                    logger.error("Too many failures, aborting stress test")
                    return False
                    
            time.sleep(delay)
            
        return failures == 0
            
    def _print_test_summary(self, results: Dict[str, bool]) -> None:
        """Print a summary of test results."""
        logger.info("\n==== Pattern Test Summary ====")
        
        success_count = sum(1 for success in results.values() if success)
        total = len(results)
        
        logger.info(f"Total patterns tested: {total}")
        logger.info(f"Successful: {success_count}")
        logger.info(f"Failed: {total - success_count}")
        
        if success_count == total:
            logger.info("✅ All patterns working correctly!")
        else:
            logger.info("❌ Some patterns failed:")
            for pattern, success in results.items():
                if not success:
                    logger.info(f"  - {pattern}")
    
    def diagnose_common_issues(self) -> List[str]:
        """Check for common issues with the LED system."""
        issues = []
        
        # Test connection and check memory
        status_response = self.send_command({"command": "status"})
        
        if not status_response:
            issues.append("Cannot get status from Arduino, communication problem")
        elif "free_mem" in status_response and status_response["free_mem"] < 1000:
            issues.append(f"Low memory: {status_response['free_mem']} bytes - may cause instability")
            
        # Check for pattern transitions
        if not self.test_pattern("HAPPY", duration=0.5) or not self.test_pattern("SAD", duration=0.5):
            issues.append("Pattern transition failure - possible animation loop issue")
            
        # Check reset functionality
        reset_response = self.send_command({"command": "reset"})
        if not reset_response or reset_response.get("ack") != True:
            issues.append("Reset functionality not working properly")
            
        return issues
    
    def disconnect(self) -> None:
        """Disconnect from Arduino."""
        if self.serial and self.serial.is_open:
            # Set back to IDLE before disconnecting
            self.test_pattern("IDLE", duration=0.2)
            self.serial.close()
            logger.info("Disconnected from Arduino")

def main():
    """Main entry point with command line argument handling."""
    parser = argparse.ArgumentParser(description='DJ R3X Eyes Pattern Tester')
    parser.add_argument('--port', help='Serial port (auto-detected if not specified)')
    parser.add_argument('--timeout', type=float, default=2.0, help='Serial timeout in seconds')
    parser.add_argument('--all', action='store_true', help='Run all tests')
    parser.add_argument('--pattern', help='Test a specific pattern')
    parser.add_argument('--sequence', action='store_true', help='Test animation sequences')
    parser.add_argument('--stress', action='store_true', help='Run stress test')
    parser.add_argument('--diagnose', action='store_true', help='Diagnose common issues')
    args = parser.parse_args()
    
    # Create tester instance
    tester = EyePatternTester(port=args.port, timeout=args.timeout)
    
    try:
        # Connect
        if not tester.connect():
            logger.error("Failed to connect to Arduino, exiting")
            sys.exit(1)
            
        # Determine tests to run
        if args.all or (not args.pattern and not args.sequence and not args.stress and not args.diagnose):
            # Run all tests if --all specified or no specific test selected
            logger.info("Running all tests")
            tester.test_all_patterns()
            tester.test_animation_sequence()
            tester.test_stress()
            issues = tester.diagnose_common_issues()
            if issues:
                logger.warning("Issues detected:")
                for issue in issues:
                    logger.warning(f"  - {issue}")
        else:
            # Run specific tests as requested
            if args.pattern:
                tester.test_pattern(args.pattern, duration=3.0)
                
            if args.sequence:
                tester.test_animation_sequence()
                
            if args.stress:
                tester.test_stress()
                
            if args.diagnose:
                issues = tester.diagnose_common_issues()
                if issues:
                    logger.warning("Issues detected:")
                    for issue in issues:
                        logger.warning(f"  - {issue}")
                else:
                    logger.info("No common issues detected")
    
    except KeyboardInterrupt:
        logger.info("Test interrupted by user")
    except Exception as e:
        logger.error(f"Test error: {e}")
    finally:
        tester.disconnect()

if __name__ == "__main__":
    main() 