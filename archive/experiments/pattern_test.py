import serial
import json
import time

PORT = '/dev/cu.usbmodem833301'
BAUD_RATE = 115200
TIMEOUT = 2.0

def main():
    print("Opening serial port...")
    with serial.Serial(PORT, BAUD_RATE, timeout=TIMEOUT) as ser:
        time.sleep(2)  # Wait for Arduino reset
        
        # Clear buffers
        ser.reset_input_buffer()
        ser.reset_output_buffer()
        
        # Test pattern command
        pattern_cmd = {
            "command": "set_pattern",
            "params": {
                "pattern": "IDLE",
                "brightness": 0.8
            }
        }
        
        print(f"Sending command: {json.dumps(pattern_cmd)}")
        ser.write((json.dumps(pattern_cmd) + "\n").encode())
        ser.flush()
        
        # Read with longer timeout
        print("\nWaiting for response...")
        time.sleep(0.5)
        
        start_time = time.time()
        while (time.time() - start_time) < 5:  # 5 second timeout
            if ser.in_waiting > 0:
                try:
                    line = ser.readline().decode('utf-8').strip()
                    print(f"Received: {line}")
                    try:
                        json_data = json.loads(line)
                        print(f"Parsed JSON: {json_data}")
                    except json.JSONDecodeError as e:
                        print(f"Not valid JSON: {e}")
                except UnicodeDecodeError as e:
                    print(f"Decode error: {e}")
            time.sleep(0.1)

if __name__ == "__main__":
    main() 