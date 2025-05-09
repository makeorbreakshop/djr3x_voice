#!/usr/bin/env python3
"""
Simple test script for DJ R3X Eyes Controller v2
Tests a single pattern to diagnose JSON parsing issues
"""

import json
import logging
import time
import serial
import serial.tools.list_ports

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def find_arduino_port():
    """Find the Arduino serial port."""
    ports = list(serial.tools.list_ports.comports())
    
    logger.info("Available ports:")
    for port in ports:
        logger.info(f"  {port.device}: {port.description}")
        
    # On macOS, look for usbmodem devices first
    for port in ports:
        if "usbmodem" in port.device:
            logger.info(f"Found likely Arduino at {port.device} (usbmodem)")
            return port.device
            
    return None

def main():
    port = find_arduino_port()
    if not port:
        logger.error("Could not find Arduino port")
        return
    
    try:
        logger.info(f"Connecting to {port}")
        ser = serial.Serial(port=port, baudrate=115200, timeout=2.0)
        
        # Wait for Arduino reset
        time.sleep(2)
        
        # Clear buffers
        ser.reset_input_buffer()
        ser.reset_output_buffer()
        
        # First test the basic test command
        logger.info("STEP 1: Testing basic 'test' command")
        test_cmd = {"command": "test"}
        cmd_json = json.dumps(test_cmd) + "\n"
        logger.debug(f"Sending: {cmd_json.strip()}")
        ser.write(cmd_json.encode())
        ser.flush()
        
        # Read response(s)
        time.sleep(1)
        responses = []
        while ser.in_waiting:
            line = ser.readline().decode().strip()
            if line:
                logger.debug(f"Received: {line}")
                responses.append(line)
        
        # Now test a simple IDLE pattern
        logger.info("\nSTEP 2: Testing 'set_pattern' with IDLE")
        ser.reset_input_buffer()
        ser.reset_output_buffer()
        time.sleep(0.5)
        
        pattern_cmd = {
            "command": "set_pattern", 
            "params": {
                "pattern": "IDLE",
                "brightness": 0.5
            }
        }
        cmd_json = json.dumps(pattern_cmd) + "\n"
        logger.debug(f"Sending: {cmd_json.strip()}")
        ser.write(cmd_json.encode())
        ser.flush()
        
        # Read response(s) with longer timeout
        time.sleep(3)
        responses = []
        while ser.in_waiting:
            line = ser.readline().decode().strip()
            if line:
                logger.debug(f"Received: {line}")
                try:
                    # Try to parse as JSON for better logging
                    json_resp = json.loads(line)
                    if "ack" in json_resp and json_resp.get("ack") == True:
                        logger.info("Pattern set successfully!")
                except:
                    pass
                responses.append(line)
                
        # Test status command
        logger.info("\nSTEP 3: Testing 'status' command")
        ser.reset_input_buffer()
        ser.reset_output_buffer()
        time.sleep(0.5)
        
        status_cmd = {"command": "status"}
        cmd_json = json.dumps(status_cmd) + "\n"
        logger.debug(f"Sending: {cmd_json.strip()}")
        ser.write(cmd_json.encode())
        ser.flush()
        
        # Read response(s)
        time.sleep(1)
        responses = []
        while ser.in_waiting:
            line = ser.readline().decode().strip()
            if line:
                logger.debug(f"Received: {line}")
                responses.append(line)
                
    except Exception as e:
        logger.error(f"Error: {e}")
    finally:
        if 'ser' in locals() and ser.is_open:
            ser.close()
            logger.info("Disconnected from Arduino")

if __name__ == "__main__":
    main() 