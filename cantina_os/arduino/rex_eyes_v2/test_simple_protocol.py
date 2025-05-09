#!/usr/bin/env python3
"""
Ultra-Simple Test Script for DJ R3X Eyes Controller v2
Uses a text-based protocol for maximum reliability
"""

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

class SimpleProtocolTester:
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
            
            # Wait for READY message with timeout
            start_time = time.time()
            ready_received = False
            
            while (time.time() - start_time) < self.timeout:
                if self.serial.in_waiting:
                    line = self.serial.readline().decode('utf-8').strip()
                    logger.debug(f"Received: {line}")
                    
                    if line.startswith("READY:"):
                        logger.info("Arduino is ready")
                        ready_received = True
                        break
                time.sleep(0.1)
                
            if not ready_received:
                logger.warning("Did not receive READY message, attempting to continue anyway")
            
            # Test connection
            logger.info("Testing connection...")
            response = self.send_command("TEST")
            
            if response and response.startswith("OK:TEST"):
                logger.info("✓ Connection successful!")
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
        """Send a command to the Arduino with retries."""
        if not self.serial or not self.serial.is_open:
            logger.error("Not connected to Arduino")
            return None
        
        for attempt in range(retries):
            try:
                # Clear any pending data
                self.serial.reset_input_buffer()
                
                # Send the command with a newline
                logger.debug(f"Sending (attempt {attempt+1}/{retries}): {command}")
                self.serial.write(f"{command}\n".encode())
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
        """Read response from Arduino."""
        try:
            line = self.serial.readline().decode('utf-8').strip()
            if line:
                logger.debug(f"Response: {line}")
                return line
        except Exception as e:
            logger.error(f"Error reading response: {e}")
        
        return None
    
    def set_pattern(self, pattern_name):
        """Set a pattern."""
        logger.info(f"Setting pattern: {pattern_name}")
        response = self.send_command(f"PATTERN:{pattern_name}")
        
        if response and response.startswith("OK:PATTERN"):
            logger.info(f"✓ Pattern set successfully: {pattern_name}")
            return True
        else:
            logger.error(f"✗ Failed to set pattern {pattern_name}: {response}")
            return False
    
    def set_brightness(self, brightness):
        """Set brightness (0-15)."""
        logger.info(f"Setting brightness: {brightness}")
        response = self.send_command(f"BRIGHTNESS:{brightness}")
        
        if response and response.startswith("OK:BRIGHTNESS"):
            logger.info(f"✓ Brightness set successfully: {brightness}")
            return True
        else:
            logger.error(f"✗ Failed to set brightness {brightness}: {response}")
            return False
    
    def set_mouth_level(self, level):
        """Set mouth level (0-255)."""
        logger.info(f"Setting mouth level: {level}")
        response = self.send_command(f"MOUTH:{level}")
        
        if response and response.startswith("OK:MOUTH"):
            logger.info(f"✓ Mouth level set successfully: {level}")
            return True
        else:
            logger.error(f"✗ Failed to set mouth level {level}: {response}")
            return False
    
    def get_status(self):
        """Get current status from Arduino."""
        logger.info("Getting status")
        response = self.send_command("STATUS")
        
        if response and response.startswith("OK:STATUS"):
            logger.info(f"Current status: {response}")
            return response
        else:
            logger.error(f"Failed to get status: {response}")
            return None
    
    def reset(self):
        """Reset the Arduino to default state."""
        logger.info("Resetting Arduino")
        response = self.send_command("RESET")
        
        if response and response.startswith("OK:RESET"):
            logger.info("✓ Reset successful")
            return True
        else:
            logger.error(f"✗ Failed to reset: {response}")
            return False
    
    def test_all_patterns(self):
        """Test all available eye patterns."""
        patterns = [
            "IDLE", "STARTUP", "LISTENING", "THINKING", 
            "SPEAKING", "HAPPY", "SAD", "ANGRY", 
            "SURPRISED", "ERROR"
        ]
        
        logger.info("==== Testing All Patterns ====")
        
        # Start with a reset
        self.reset()
        time.sleep(0.5)
        
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
        
        try:
            while (time.time() - start_time) < duration:
                # Calculate mouth level (0-255) using a sine wave pattern
                t = (time.time() - start_time) * 2  # Speed factor
                mouth_level = int(127.5 + 127.5 * __import__('math').sin(t))
                
                # Update the mouth level (don't wait for response to keep animation smooth)
                self.send_command(f"MOUTH:{mouth_level}")
                
                # Short delay (100ms)
                time.sleep(0.1)
                
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
    parser = argparse.ArgumentParser(description='DJ R3X Eyes Controller Simple Protocol Tester')
    parser.add_argument('--port', help='Serial port (auto-detected if not specified)')
    parser.add_argument('--timeout', type=float, default=3.0, help='Serial timeout in seconds')
    parser.add_argument('--test', choices=['all', 'speaking'], default='all',
                      help='Test to run (all patterns or speaking animation)')
    parser.add_argument('--duration', type=int, default=10,
                      help='Duration for animation tests in seconds')
    args = parser.parse_args()
    
    tester = SimpleProtocolTester(port=args.port, timeout=args.timeout)
    
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