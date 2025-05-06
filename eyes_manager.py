"""
DJ R3X Eyes Manager
Handles communication with Arduino-controlled LED matrix eyes
"""

import asyncio
import serial_asyncio
import logging
from typing import Optional
from enum import Enum
from config.app_settings import DEBUG_MODE

# Configure logging
logging.basicConfig(level=logging.DEBUG if DEBUG_MODE else logging.INFO)
logger = logging.getLogger(__name__)

class EyeState(Enum):
    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    SPEAKING = "speaking"
    ERROR = "error"

class EyesManager:
    def __init__(self, port: str = '/dev/tty.usbmodem833301', baudrate: int = 115200):
        """Initialize the eyes manager.
        
        Args:
            port: Serial port for Arduino communication
            baudrate: Serial communication speed (must match Arduino)
        """
        self.port = port
        self.baudrate = baudrate
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self.current_state: Optional[EyeState] = None
        self._ready = False
        self._connected = False

    async def connect(self) -> bool:
        """Establish connection with Arduino."""
        try:
            # Open serial connection
            self.reader, self.writer = await serial_asyncio.open_serial_connection(
                url=self.port,
                baudrate=self.baudrate
            )
            self._connected = True
            
            # Wait for ready signal
            while True:
                if self.reader is None:
                    break
                    
                response = await self.reader.readline()
                if response.decode().strip() == "READY":
                    self._ready = True
                    logger.info("Arduino eyes ready!")
                    break
                    
            # Run initial test pattern
            await self.test_pattern()
            return True
            
        except Exception as e:
            logger.error(f"Failed to connect to Arduino: {e}")
            self._connected = False
            return False

    async def disconnect(self):
        """Close the serial connection."""
        if self.writer:
            self.writer.close()
            await self.writer.wait_closed()
            self._connected = False
            self._ready = False

    async def send_command(self, command: str) -> bool:
        """Send a command to Arduino and wait for acknowledgment.
        
        Args:
            command: Command string to send
            
        Returns:
            bool: True if command was acknowledged
        """
        if not self._connected or not self.writer:
            logger.error("Not connected to Arduino")
            return False

        try:
            # Send command
            self.writer.write(f"{command}\n".encode())
            await self.writer.drain()
            
            # Wait for acknowledgment
            if self.reader:
                response = await self.reader.readline()
                ack = response.decode().strip()
                expected_ack = f"ACK:{command.split(':')[0]}"
                return ack == expected_ack
                
            return False
            
        except Exception as e:
            logger.error(f"Failed to send command: {e}")
            return False

    async def set_state(self, state: EyeState) -> bool:
        """Set the eye state.
        
        Args:
            state: New state to set
            
        Returns:
            bool: True if state was set successfully
        """
        if state == self.current_state:
            return True
            
        success = await self.send_command(f"STATE:{state.value}")
        if success:
            self.current_state = state
            logger.debug(f"Eye state changed to: {state.value}")
        return success

    async def set_brightness(self, level: int) -> bool:
        """Set eye brightness level (0-15)."""
        level = max(0, min(15, level))
        return await self.send_command(f"BRIGHTNESS:{level}")

    async def test_pattern(self) -> bool:
        """Run the test pattern animation."""
        return await self.send_command("TEST")

    async def clear(self) -> bool:
        """Clear all LEDs."""
        return await self.send_command("CLEAR")

    @property
    def is_ready(self) -> bool:
        """Check if eyes are ready for commands."""
        return self._ready and self._connected

    async def __aenter__(self):
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.disconnect()

async def main():
    """Interactive test function for the eyes."""
    print("DJ R3X Eyes Test Sequence")
    print("------------------------")
    
    # Create eyes manager
    eyes = EyesManager()
    
    try:
        print("Connecting to Arduino...")
        if await eyes.connect():
            print("✅ Connected successfully!")
            
            # Test sequence
            tests = [
                ("1. Testing Clear", eyes.clear, 2),
                ("2. Running Test Pattern", eyes.test_pattern, 3),
                ("3. Setting Idle State", lambda: eyes.set_state(EyeState.IDLE), 2),
                ("4. Setting Listening State", lambda: eyes.set_state(EyeState.LISTENING), 2),
                ("5. Setting Processing State", lambda: eyes.set_state(EyeState.PROCESSING), 3),
                ("6. Setting Speaking State", lambda: eyes.set_state(EyeState.SPEAKING), 2),
                ("7. Setting Error State", lambda: eyes.set_state(EyeState.ERROR), 2),
                ("8. Testing Brightness Levels", None, 0),
            ]
            
            for test_name, test_func, delay in tests:
                print(f"\n{test_name}")
                if test_func:
                    success = await test_func()
                    print("✅ Success" if success else "❌ Failed")
                    await asyncio.sleep(delay)
                elif test_name.startswith("8."):
                    # Brightness test sequence
                    print("Testing brightness levels (0, 5, 10, 15)")
                    for level in [0, 5, 10, 15]:
                        print(f"Setting brightness to {level}")
                        await eyes.set_brightness(level)
                        await asyncio.sleep(1)
            
            # Final test: rapid state changes
            print("\n9. Testing Rapid State Changes")
            states = [EyeState.IDLE, EyeState.LISTENING, EyeState.PROCESSING, 
                     EyeState.SPEAKING, EyeState.ERROR]
            for _ in range(2):  # Do the sequence twice
                for state in states:
                    await eyes.set_state(state)
                    await asyncio.sleep(0.5)
            
            # Return to idle state
            print("\nReturning to idle state...")
            await eyes.set_state(EyeState.IDLE)
            
            print("\n✅ Test sequence completed!")
            
        else:
            print("❌ Failed to connect to Arduino")
    except Exception as e:
        print(f"❌ Error during test: {e}")
    finally:
        # Clean up
        print("\nDisconnecting...")
        await eyes.disconnect()
        print("Done!")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nTest interrupted by user")
    except Exception as e:
        print(f"\nTest failed with error: {e}") 