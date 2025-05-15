#!/usr/bin/env python3
"""
Arduino Connection Test Tool

This script tests the connection to the Arduino and verifies that LED patterns
can be sent and received correctly.
"""

import argparse
import asyncio
import logging
import os
import sys
import time
import traceback

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Now we can import project modules
from cantina_os.services.simple_eye_adapter import SimpleEyeAdapter, EyePattern

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("arduino_test")

# Fix SimpleEyeAdapter by adding workaround for connection issue
class FixedEyeAdapter(SimpleEyeAdapter):
    async def connect(self) -> bool:
        """
        Connect to the Arduino with better error handling.
        
        Returns:
            True if connection successful, False otherwise
        """
        try:
            logger.info(f"Connecting to Arduino at {self.serial_port}")
            
            # Open serial connection
            logger.debug(f"Opening serial connection with parameters: port={self.serial_port}, baudrate={self.baud_rate}, timeout={self.timeout}")
            self.serial_conn = serial.Serial(
                port=self.serial_port,
                baudrate=self.baud_rate,
                timeout=self.timeout
            )
            
            # Mark as connected after successful open
            self.connected = True
            
            # Clear any buffered data
            logger.debug("Clearing input and output buffers")
            self.serial_conn.reset_input_buffer()
            self.serial_conn.reset_output_buffer()
            
            # Wait for Arduino to reset (common when serial connection established)
            logger.debug("Waiting for Arduino initialization (2 seconds)")
            await asyncio.sleep(2.0)
            
            # Check if we get a response to a test command
            logger.debug("Sending test command (IDLE pattern)")
            if await self._send_command('I'):  # Send IDLE command as a test
                logger.info("Successfully connected to Arduino")
                return True
            else:
                logger.error("Failed to get response from Arduino")
                self.serial_conn.close()
                self.serial_conn = None
                self.connected = False
                return False
                
        except serial.SerialException as e:
            logger.error(f"Serial connection error: {e}")
            if self.serial_conn:
                try:
                    self.serial_conn.close()
                except Exception:
                    pass
            self.serial_conn = None
            self.connected = False
            return False
        except Exception as e:
            logger.error(f"Unexpected error during connection: {e}")
            logger.debug(traceback.format_exc())
            self.connected = False
            return False

async def test_connection(port, baud_rate=115200, timeout=2.0):
    """Test connection to Arduino."""
    logger.info(f"Testing connection to Arduino at {port} with baud rate {baud_rate}")
    
    adapter = FixedEyeAdapter(
        serial_port=port,
        baud_rate=baud_rate,
        timeout=timeout,
        logger=logger
    )
    
    try:
        # Connect to Arduino
        logger.info("Attempting to connect...")
        connected = await adapter.connect()
        
        if not connected:
            logger.error("Failed to connect to Arduino")
            return False
            
        logger.info("Successfully connected to Arduino!")
        
        # Test patterns
        patterns = [
            (EyePattern.IDLE, "IDLE"),
            (EyePattern.LISTENING, "LISTENING"),
            (EyePattern.THINKING, "THINKING"),
            (EyePattern.SPEAKING, "SPEAKING"),
            (EyePattern.HAPPY, "HAPPY"),
            (EyePattern.SAD, "SAD"),
            (EyePattern.ANGRY, "ANGRY")
        ]
        
        for pattern, name in patterns:
            logger.info(f"Setting pattern to {name}")
            success = await adapter.set_pattern(pattern)
            if success:
                logger.info(f"Successfully set {name} pattern")
            else:
                logger.error(f"Failed to set {name} pattern")
            # Pause to see the pattern
            await asyncio.sleep(2.0)
            
        # Reset to idle
        logger.info("Setting pattern back to IDLE")
        await adapter.set_pattern(EyePattern.IDLE)
        
        # Test brightness
        logger.info("Testing brightness levels")
        for level in [0.2, 0.5, 0.8, 1.0]:
            logger.info(f"Setting brightness to {level}")
            success = await adapter.set_brightness(level)
            if success:
                logger.info(f"Successfully set brightness to {level}")
            else:
                logger.error(f"Failed to set brightness to {level}")
            await asyncio.sleep(1.0)
            
        # Reset
        logger.info("Resetting Arduino")
        success = await adapter.reset()
        if success:
            logger.info("Reset successful")
        else:
            logger.error("Failed to reset Arduino")
            
        # Disconnect
        logger.info("Disconnecting from Arduino")
        await adapter.disconnect()
        
        return True
        
    except Exception as e:
        logger.error(f"Error during Arduino test: {e}")
        logger.debug(traceback.format_exc())
        return False
        
def list_available_ports():
    """List available serial ports."""
    import serial.tools.list_ports
    
    logger.info("Available serial ports:")
    ports = list(serial.tools.list_ports.comports())
    
    if not ports:
        logger.info("  No serial ports found")
        return
        
    for port in ports:
        logger.info(f"  {port.device} - {port.description}")
        if port.manufacturer:
            logger.info(f"    Manufacturer: {port.manufacturer}")
        if port.vid is not None and port.pid is not None:
            logger.info(f"    VID:PID: {port.vid:04X}:{port.pid:04X}")
            
async def main():
    parser = argparse.ArgumentParser(description="Test Arduino connection and patterns")
    parser.add_argument("--port", "-p", help="Serial port for Arduino", 
                        default="/dev/cu.usbmodem833301")
    parser.add_argument("--baud", "-b", type=int, help="Baud rate", default=115200)
    parser.add_argument("--list", "-l", action="store_true", help="List available ports")
    parser.add_argument("--timeout", "-t", type=float, help="Timeout in seconds", default=5.0)
    
    args = parser.parse_args()
    
    if args.list:
        list_available_ports()
        return
        
    success = await test_connection(args.port, args.baud, args.timeout)
    if success:
        logger.info("Arduino test completed successfully!")
    else:
        logger.error("Arduino test failed.")
        sys.exit(1)  # Return non-zero status on failure

if __name__ == "__main__":
    # Import inside function to avoid circular imports
    import serial
    asyncio.run(main()) 