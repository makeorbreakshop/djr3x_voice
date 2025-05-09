#!/usr/bin/env python3
"""
Minimal test script for DJ R3X Eyes Controller v2
Sends the absolute simplest pattern command possible
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
    for port in ports:
        if "usbmodem" in port.device:
            logger.info(f"Found Arduino at {port.device}")
            return port.device
    return None

def main():
    port = find_arduino_port()
    if not port:
        logger.error("Could not find Arduino port")
        return
    
    try:
        # Connect with longer timeout
        logger.info(f"Connecting to {port}")
        ser = serial.Serial(port=port, baudrate=115200, timeout=5.0)
        
        # Wait for Arduino reset
        time.sleep(2)
        ser.reset_input_buffer()
        ser.reset_output_buffer()
        
        # The absolute simplest pattern command
        logger.info("Sending minimal pattern command")
        
        # Version 1: Minimal command
        cmd = {"command":"set_pattern","params":{"pattern":"IDLE"}}
        
        cmd_json = json.dumps(cmd) + "\n"
        logger.debug(f"Sending: {cmd_json.strip()}")
        ser.write(cmd_json.encode())
        ser.flush()
        
        # Read with longer timeout and detailed logging
        logger.info("Reading response...")
        time.sleep(3)
        
        # Read everything available for debugging
        raw_data = ""
        responses = []
        
        # Loop until nothing left to read
        while True:
            if ser.in_waiting:
                chunk = ser.read(ser.in_waiting).decode()
                raw_data += chunk
                logger.debug(f"Read chunk: {chunk!r}")
                
                # Process any complete lines
                lines = raw_data.split("\n")
                raw_data = lines.pop()  # Keep incomplete line
                
                for line in lines:
                    line = line.strip()
                    if line:
                        logger.info(f"Complete line: {line}")
                        responses.append(line)
                        try:
                            json_obj = json.loads(line)
                            logger.info(f"Parsed JSON: {json_obj}")
                        except json.JSONDecodeError as e:
                            logger.error(f"JSON parse error: {e}")
                
                time.sleep(0.1)
            else:
                break
                
        # Log remaining data if any
        if raw_data:
            logger.debug(f"Remaining data: {raw_data!r}")
            
        # Check if we got any successful response
        success = False
        for resp in responses:
            try:
                json_resp = json.loads(resp)
                if json_resp.get("ack") == True:
                    success = True
                    logger.info("Command succeeded!")
                    break
            except:
                pass
                
        if not success:
            logger.error("No success acknowledgment received")
        
    except Exception as e:
        logger.error(f"Error: {e}")
    finally:
        if 'ser' in locals() and ser.is_open:
            ser.close()
            logger.info("Disconnected from Arduino")

if __name__ == "__main__":
    main() 