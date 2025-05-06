"""
Test script for Arduino Eyes state management
"""
import serial
import time
from enum import Enum

class EyeState(Enum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    PROCESSING = "PROCESSING"
    SPEAKING = "SPEAKING"
    SPEAKING_PULSE = "SPEAKING:1"
    SPEAKING_SQUARE = "SPEAKING:2"
    SPEAKING_WAVE = "SPEAKING:3"
    ERROR = "ERROR"
    TEST = "TEST"

def test_all_leds(ser):
    """Test function to turn on all LEDs"""
    print("\nTesting all LEDs...")
    ser.write(f"{EyeState.TEST.value}\n".encode())
    time.sleep(0.5)
    
    while ser.in_waiting:
        response = ser.readline().decode().strip()
        print(f"Arduino response: {response}")
    
    print("All LEDs should now be ON. Press Enter to continue...")
    input()

def test_eye_states():
    # Configure the serial connection
    port = '/dev/tty.usbmodem833301'
    baud_rate = 115200
    
    print(f"Attempting to connect to Arduino on {port}...")
    
    try:
        # Open serial connection
        ser = serial.Serial(port, baud_rate, timeout=1)
        print("Serial port opened successfully")
        
        # Wait for Arduino to reset
        time.sleep(2)
        
        # Read initial messages
        while ser.in_waiting:
            message = ser.readline().decode().strip()
            print(f"Arduino says: {message}")
        
        # Option to test all LEDs first
        print("\nWould you like to test all LEDs first? (y/n)")
        if input().lower().startswith('y'):
            test_all_leds(ser)
        
        # Test all states
        print("\nTesting all eye states...")
        
        for state in EyeState:
            if state == EyeState.TEST:  # Skip TEST state in normal rotation
                continue
                
            print(f"\nSetting state to: {state.value}")
            ser.write(f"{state.value}\n".encode())
            time.sleep(0.5)
            
            # Read response
            while ser.in_waiting:
                response = ser.readline().decode().strip()
                print(f"Arduino response: {response}")
            
            # Wait longer for states with animations
            if state in [EyeState.PROCESSING, EyeState.ERROR] or state.value.startswith("SPEAKING"):
                print("Watching animation...")
                time.sleep(3)
        
        # Return to IDLE
        print("\nReturning to IDLE state...")
        ser.write(f"{EyeState.IDLE.value}\n".encode())
        time.sleep(0.5)
        
        while ser.in_waiting:
            response = ser.readline().decode().strip()
            print(f"Arduino response: {response}")
            
    except serial.SerialException as e:
        print(f"Error: Could not open serial port: {e}")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        try:
            ser.close()
            print("\nSerial port closed")
        except:
            pass

if __name__ == "__main__":
    test_eye_states() 