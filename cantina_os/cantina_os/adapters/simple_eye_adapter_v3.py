"""
Simple Eye Adapter V3 - Clean Protocol Implementation

Simplified command protocol:
- State changes: SI, SE, SL, ST, SS, SF
- Mouth amplitude: Mnnn (fire-and-forget)
- No color commands (Arduino owns colors)
"""

import asyncio
import logging
import serial
import time
from typing import Optional, Dict, Any


class SimpleEyeAdapterV3:
    """Clean adapter for V3 Arduino protocol."""

    def __init__(
        self,
        serial_port: str,
        baud_rate: int = 115200,
        timeout: float = 1.0,
        logger: Optional[logging.Logger] = None
    ):
        """Initialize the adapter."""
        self.serial_port = serial_port
        self.baud_rate = baud_rate
        self.timeout = timeout
        self.logger = logger or logging.getLogger(__name__)

        self.connection = None
        self.connected = False
        self._last_state = None
        self._last_mouth_time = 0
        self._mouth_update_interval = 0.1  # 10Hz max

    async def connect(self) -> bool:
        """Connect to Arduino."""
        try:
            self.connection = serial.Serial(
                port=self.serial_port,
                baudrate=self.baud_rate,
                timeout=self.timeout
            )

            # Clear buffers
            self.connection.reset_input_buffer()
            self.connection.reset_output_buffer()

            # Wait for Arduino to initialize
            await asyncio.sleep(2)

            # Look for READY signal
            start_time = time.time()
            while time.time() - start_time < 5:
                if self.connection.in_waiting > 0:
                    response = self.connection.readline().decode().strip()
                    if response == "READY":
                        self.logger.info("Arduino ready signal received")
                        self.connected = True
                        return True
                await asyncio.sleep(0.1)

            # No ready signal, but might still work
            self.logger.warning("No READY signal, attempting test command")

            # Test with reset command
            self.connection.write(b"R\n")
            await asyncio.sleep(0.5)

            if self.connection.in_waiting > 0:
                response = self.connection.readline().decode().strip()
                if response == "+":
                    self.logger.info("Arduino responding to commands")
                    self.connected = True
                    return True

            self.logger.error("Arduino not responding")
            return False

        except Exception as e:
            self.logger.error(f"Connection failed: {e}")
            self.connected = False
            return False

    async def disconnect(self) -> None:
        """Disconnect from Arduino."""
        if self.connection:
            try:
                # Reset to idle before disconnecting
                await self.set_state("idle")
                self.connection.close()
            except:
                pass
            finally:
                self.connection = None
                self.connected = False

    async def set_state(self, state: str) -> bool:
        """
        Set the system state.

        States: idle, engaged, listening, thinking, speaking, flash
        """
        if not self.connected or not self.connection:
            return False

        state_commands = {
            "idle": "SI",
            "engaged": "SE",
            "listening": "SL",
            "thinking": "ST",
            "speaking": "SS",
            "flash": "SF"
        }

        command = state_commands.get(state.lower())
        if not command:
            self.logger.error(f"Invalid state: {state}")
            return False

        try:
            # Send state command
            self.logger.info(f"Setting state to {state} (command: {command})")
            self.connection.write(f"{command}\n".encode())

            # Wait for response
            start_time = time.time()
            while time.time() - start_time < self.timeout:
                if self.connection.in_waiting > 0:
                    response = self.connection.readline().decode().strip()
                    if response == "+":
                        self._last_state = state
                        return True
                    elif response == "-":
                        self.logger.error(f"Arduino rejected state: {state}")
                        return False
                await asyncio.sleep(0.01)

            self.logger.warning(f"No response for state: {state}")
            return False

        except Exception as e:
            self.logger.error(f"Failed to set state: {e}")
            return False

    async def set_mouth_amplitude(self, amplitude: int) -> None:
        """
        Set mouth amplitude (fire-and-forget).

        Amplitude: 0-255
        """
        if not self.connected or not self.connection:
            return

        # Throttle updates to 10Hz
        now = time.time()
        if now - self._last_mouth_time < self._mouth_update_interval:
            return
        self._last_mouth_time = now

        # Clamp amplitude
        amplitude = max(0, min(255, amplitude))

        try:
            # Send mouth command (no response expected)
            command = f"M{amplitude:03d}"
            self.connection.write(f"{command}\n".encode())

        except Exception as e:
            self.logger.error(f"Failed to set mouth amplitude: {e}")

    async def trigger_flash(self) -> bool:
        """Trigger flash confirmation."""
        return await self.set_state("flash")

    async def reset(self) -> bool:
        """Reset system to idle."""
        if not self.connected or not self.connection:
            return False

        try:
            self.connection.write(b"R\n")

            # Wait for response
            start_time = time.time()
            while time.time() - start_time < self.timeout:
                if self.connection.in_waiting > 0:
                    response = self.connection.readline().decode().strip()
                    if response == "+":
                        self._last_state = "idle"
                        return True
                await asyncio.sleep(0.01)

            return False

        except Exception as e:
            self.logger.error(f"Failed to reset: {e}")
            return False

    async def get_status(self) -> Dict[str, Any]:
        """Get current status."""
        return {
            "connected": self.connected,
            "port": self.serial_port,
            "last_state": self._last_state,
            "protocol": "V3"
        }

    # Legacy method mapping for compatibility
    async def set_pattern(self, pattern: str) -> bool:
        """Map old pattern names to new states."""
        pattern_to_state = {
            "idle": "idle",
            "engaged": "engaged",
            "listening": "listening",
            "thinking": "thinking",
            "speaking": "speaking",
            "flash": "flash"
        }

        state = pattern_to_state.get(pattern.lower(), "idle")
        return await self.set_state(state)

    # Removed methods (Arduino owns colors now)
    async def set_color(self, r: int, g: int, b: int) -> bool:
        """Deprecated - Arduino owns colors."""
        self.logger.debug("Color commands disabled in V3 - Arduino owns colors")
        return True

    async def set_brightness(self, brightness: int) -> bool:
        """Deprecated - using fixed brightness."""
        self.logger.debug("Brightness commands disabled in V3")
        return True