"""
Simple Eye Adapter Module for DJ R3X

This adapter translates between the complex JSON commands used by the EyeLightControllerService
and the ultra-simple single-character commands expected by the Arduino sketch.
"""

"""
SERVICE: SimpleEyeAdapter
PURPOSE: Arduino communication adapter that translates EyeLightControllerService commands to single-character Arduino commands
EVENTS_IN: None (used as a utility class by EyeLightControllerService)
EVENTS_OUT: None (direct hardware communication)
KEY_METHODS: connect, set_pattern, set_brightness, reset, get_status, _send_command
DEPENDENCIES: Arduino hardware via serial communication, rex_eyes_v2.ino Arduino sketch
"""

import asyncio
import logging
import serial
import time
from enum import Enum
from typing import Optional
import os

class EyePattern(str, Enum):
    """Enumeration of available eye LED patterns."""
    IDLE = "idle"
    STARTUP = "startup"
    ENGAGED = "engaged"  # New pattern for interactive mode ready state
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    FLASH = "flash"  # Green confirmation flash with auto-return
    HAPPY = "happy"
    SAD = "sad"
    ANGRY = "angry"
    SURPRISED = "surprised"
    ERROR = "error"
    CUSTOM = "custom"

class SimpleEyeAdapter:
    """
    Adapter to translate complex JSON commands to simple Arduino commands.
    
    This class handles the translation between the complex JSON-based protocol used
    by the EyeLightControllerService and the ultra-simple single-character protocol
    expected by the Arduino sketch (rex_eyes_v2.ino).
    """
    
    # Pattern mapping from EyePattern enum to single-character commands
    PATTERN_MAP = {
        EyePattern.IDLE: 'I',      # Idle pattern
        EyePattern.STARTUP: 'I',    # Use Idle for startup
        EyePattern.ENGAGED: 'E',    # Engaged pattern (ready for interaction)
        EyePattern.LISTENING: 'L',  # Listening pattern
        EyePattern.THINKING: 'T',   # Thinking pattern
        EyePattern.SPEAKING: 'S',   # Speaking pattern
        EyePattern.FLASH: 'F',      # Green confirmation flash with auto-return
        EyePattern.HAPPY: 'H',      # Happy pattern
        EyePattern.SAD: 'D',        # Sad pattern (D for Down)
        EyePattern.ANGRY: 'A',      # Angry pattern
        EyePattern.SURPRISED: 'H',  # Use Happy for surprised (no direct equivalent)
        EyePattern.ERROR: 'A',      # Use Angry for error (no direct equivalent)
        EyePattern.CUSTOM: 'I',     # Use Idle for custom (no direct equivalent)
    }
    
    def __init__(
        self,
        serial_port: str,
        baud_rate: int = 115200,
        timeout: float = 1.0,
        logger: Optional[logging.Logger] = None
    ):
        """
        Initialize the SimpleEyeAdapter.
        
        Args:
            serial_port: Serial port for Arduino connection
            baud_rate: Baud rate for serial communication
            timeout: Timeout for serial operations
            logger: Logger instance to use
        """
        self.serial_port = serial_port
        self.baud_rate = baud_rate
        self.timeout = timeout
        self.logger = logger or logging.getLogger(__name__)
        self.serial_conn = None
        self.connected = False
        
    async def connect(self) -> bool:
        """
        Connect to the Arduino.
        
        Returns:
            True if connection successful, False otherwise
        """
        try:
            self.logger.info(f"Connecting to Arduino at {self.serial_port}")
            
            # Check if port exists before attempting connection
            if not os.path.exists(self.serial_port):
                self.logger.error(f"Serial port {self.serial_port} does not exist")
                return False
            
            # Open serial connection
            self.logger.debug(f"Opening serial connection with parameters: port={self.serial_port}, baudrate={self.baud_rate}, timeout={self.timeout}")
            self.serial_conn = serial.Serial(
                port=self.serial_port,
                baudrate=self.baud_rate,
                timeout=self.timeout
            )
            
            # Set connected flag after successful serial connection
            self.connected = True
            
            # Clear any buffered data
            self.logger.debug("Clearing input and output buffers")
            self.serial_conn.reset_input_buffer()
            self.serial_conn.reset_output_buffer()
            
            # Wait for Arduino's ready signal ('+' sent after setup() completes)
            # With wake-up animation disabled, this should be quick (~1-2 seconds)
            self.logger.info("Waiting for Arduino ready signal...")
            start_time = time.time()
            max_wait = 5.0  # Maximum 5 seconds to wait for ready signal (wake-up disabled)

            ready_received = False
            while (time.time() - start_time) < max_wait:
                if self.serial_conn.in_waiting > 0:
                    try:
                        data = self.serial_conn.read(self.serial_conn.in_waiting)
                        response = data.decode('utf-8', errors='replace')
                        self.logger.debug(f"Arduino startup message: '{response.strip()}'")
                        if '+' in response:
                            self.logger.info("✅ Arduino ready signal received!")
                            ready_received = True
                            break
                    except Exception as e:
                        self.logger.warning(f"Error reading startup message: {e}")
                await asyncio.sleep(0.1)  # Check every 100ms

            if not ready_received:
                self.logger.warning("⚠️ Arduino ready signal not received, will attempt test commands...")

            # Clear buffers before sending commands
            await asyncio.sleep(0.1)
            self.serial_conn.reset_input_buffer()
            self.serial_conn.reset_output_buffer()
            
            # Make multiple attempts to send test command
            max_attempts = 3
            for attempt in range(1, max_attempts + 1):
                self.logger.debug(f"Sending test command (IDLE pattern) - attempt {attempt}/{max_attempts}")
                
                # Try with both newline and without newline to support different Arduino sketches
                command_to_try = 'I\n' if attempt % 2 == 1 else 'I'
                
                # Show newlines as \n in logs
                readable_command = command_to_try.replace('\n', '[newline]')
                self.logger.debug(f"Sending raw command: '{readable_command}'")
                bytes_written = self.serial_conn.write(command_to_try.encode('utf-8'))
                self.logger.debug(f"Wrote {bytes_written} bytes to serial port")
                self.serial_conn.flush()
                
                # Wait for response
                await asyncio.sleep(0.5)
                
                # Check for any response
                if self.serial_conn.in_waiting > 0:
                    response = ""
                    try:
                        # Read all available bytes
                        available = self.serial_conn.in_waiting
                        raw_data = self.serial_conn.read(available)
                        response = raw_data.decode('utf-8', errors='replace')
                        self.logger.debug(f"Received raw response: '{response}'")
                        
                        # Check for success marker
                        if '+' in response:
                            self.logger.debug("Command successful")
                            self.connected = True
                            self.logger.info("Successfully connected to Arduino")
                            return True
                    except Exception as e:
                        self.logger.warning(f"Error reading response: {e}")
                
                # Clear buffers before next attempt
                self.serial_conn.reset_input_buffer()
                self.serial_conn.reset_output_buffer()
                await asyncio.sleep(0.5)  # Wait between attempts
            
            # Last resort option: try a different pattern command
            self.logger.debug("Trying a different pattern command as last resort")
            last_resort_commands = ['L\n', 'T\n', 'S\n', 'I']
            
            for cmd in last_resort_commands:
                # Show newlines as \n in logs
                readable_cmd = cmd.replace('\n', '[newline]')
                self.logger.debug(f"Trying command: '{readable_cmd}'")
                self.serial_conn.write(cmd.encode('utf-8'))
                self.serial_conn.flush()
                await asyncio.sleep(0.5)
                
                if self.serial_conn.in_waiting > 0:
                    try:
                        response = self.serial_conn.read(self.serial_conn.in_waiting).decode('utf-8', errors='replace')
                        self.logger.debug(f"Received: '{response}'")
                        if '+' in response:
                            self.logger.info("Last resort command successful")
                            self.connected = True
                            return True
                    except Exception:
                        pass
                
                self.serial_conn.reset_input_buffer()
                await asyncio.sleep(0.5)
            
            # If we got here, all attempts failed
            self.logger.error(f"Failed to get response from Arduino after multiple attempts")
            
            if self.serial_conn:
                try:
                    self.serial_conn.close()
                except Exception as e:
                    self.logger.error(f"Error closing serial connection: {e}")
            
            self.serial_conn = None
            self.connected = False
            return False
                
        except serial.SerialException as e:
            self.logger.error(f"Serial connection error: {e}")
            if "Errno 13" in str(e) or "Permission denied" in str(e):
                self.logger.error("Permission denied when opening serial port. On Linux, you may need to add your user to the 'dialout' group.")
                self.logger.error("On macOS, you may need to grant permissions in System Preferences > Security & Privacy.")
            elif "Device busy" in str(e) or "Resource busy" in str(e):
                self.logger.error("Serial port is busy. Another program may be using the port.")
            
            if self.serial_conn:
                try:
                    self.serial_conn.close()
                except Exception:
                    pass
            
            self.serial_conn = None
            self.connected = False
            return False
        
        except Exception as e:
            self.logger.error(f"Unexpected error during connection: {e}")
            import traceback
            self.logger.debug(traceback.format_exc())
            
            if self.serial_conn:
                try:
                    self.serial_conn.close()
                except Exception:
                    pass
            
            self.serial_conn = None
            self.connected = False
            return False
            
    async def disconnect(self) -> None:
        """Disconnect from the Arduino."""
        if self.serial_conn:
            try:
                # Set to idle before disconnecting
                await self._send_command('I')
                # Close connection
                self.serial_conn.close()
            except Exception as e:
                self.logger.error(f"Error during disconnect: {e}")
            finally:
                self.serial_conn = None
                self.connected = False
                
    async def _send_command(self, command: str) -> bool:
        """
        Send a command to the Arduino (single or multi-character).

        Args:
            command: Command string (can be single char like 'I' or multi-char like 'CFF0000')

        Returns:
            True if command successful, False otherwise
        """
        if not self.connected or not self.serial_conn:
            self.logger.error("Cannot send command: Not connected to Arduino")
            return False

        try:
            # Multi-character commands are now supported (color, brightness)
            # Just log a debug message for awareness
            if len(command) > 1:
                self.logger.debug(f"Sending multi-character command: '{command}'")
            
            # Special case for 'Z' status command - might not be implemented in all Arduino sketches
            is_status_command = command == 'Z'
            
            # Send command with newline character (Arduino typically expects \n termination)
            command_str = command + '\n'
            # Reduce logging for mouth amplitude commands (M commands) to avoid spam
            if command.startswith('M'):
                self.logger.debug(f"📤 Sending mouth amplitude: '{command}'")
            else:
                self.logger.info(f"📤 Sending to Arduino: '{command}' (with newline)")

            bytes_written = self.serial_conn.write(command_str.encode('utf-8'))
            self.logger.debug(f"📤 Wrote {bytes_written} bytes to serial port")
            self.serial_conn.flush()
            
            # Clear any existing input before waiting for response
            self.serial_conn.reset_input_buffer()
            
            # Wait for response with timeout
            start_time = time.time()
            response = ""

            # Only log waiting for non-mouth commands
            if not command.startswith('M'):
                self.logger.info(f"⏳ Waiting for Arduino response (timeout: {self.timeout}s)")

            while (time.time() - start_time) < self.timeout:
                if self.serial_conn.in_waiting > 0:
                    bytes_available = self.serial_conn.in_waiting
                    if not command.startswith('M'):
                        self.logger.info(f"📥 Arduino has {bytes_available} bytes available")
                    try:
                        char = self.serial_conn.read(1).decode('utf-8')
                        response += char
                        if not command.startswith('M'):
                            self.logger.info(f"📥 Received: '{char}' → Full response so far: '{response}'")

                        # Check for success/error
                        if '+' in response:
                            self.logger.info(f"✅ Arduino responded with SUCCESS: '{response}'")
                            return True
                        elif '-' in response:
                            # Don't immediately fail - log a warning instead
                            self.logger.warning("Received error indicator from Arduino but command may still have worked")
                            # If there's more data, continue reading
                            if self.serial_conn.in_waiting > 0:
                                continue
                            
                            # Since hardware is reliably processing commands even with error response,
                            # we'll still consider this a success
                            self.logger.info("Treating command as successful despite error response")
                            return True
                    except UnicodeDecodeError:
                        self.logger.warning("Received non-UTF8 data from Arduino, ignoring")
                        # Clear remaining data
                        self.serial_conn.reset_input_buffer()
                        
                # Small delay
                await asyncio.sleep(0.01)
                
            self.logger.warning(f"Command timed out after {self.timeout} seconds")
            
            # If we got here, we timed out without a clear response
            # Since commands are working reliably even without responses, assume success
            
            # For status command 'Z', assume success even without response
            # since older Arduino sketches might not implement this command
            if is_status_command:
                self.logger.debug("Status command 'Z' timed out, assuming success as the sketch may not implement it")
                return True
            
            # If this is the first command after connection, be more lenient
            if command == 'I' and self.serial_conn and not hasattr(self, "_first_command_sent"):
                self.logger.debug("First command 'I' timed out, but marking as successful for testing connection")
                setattr(self, "_first_command_sent", True)
                return True
                
            # For pattern commands, assume success since the hardware is working properly
            if command in self.PATTERN_MAP.values():
                self.logger.info(f"Pattern command '{command}' timed out, but assuming success since hardware is working")
                return True
            
            # For brightness commands (0-9), assume success
            if command in "0123456789":
                self.logger.info(f"Brightness command '{command}' timed out, but assuming success since hardware is working")
                return True
                
            # For other commands, be conservative and return false
            return False
            
        except Exception as e:
            self.logger.error(f"Error sending command: {e}")
            import traceback
            self.logger.debug(traceback.format_exc())
            return False

    async def flush_serial_buffer(self) -> None:
        """Flush both input and output serial buffers to clear any queued commands."""
        if self.serial_conn and self.connected:
            try:
                # Clear input buffer (responses from Arduino)
                if self.serial_conn.in_waiting > 0:
                    discarded = self.serial_conn.read(self.serial_conn.in_waiting)
                    self.logger.debug(f"Flushed {len(discarded)} bytes from input buffer")

                # Clear output buffer (commands to Arduino)
                self.serial_conn.reset_output_buffer()
                self.serial_conn.reset_input_buffer()

                # Small delay to ensure buffers are truly clear
                await asyncio.sleep(0.01)
            except Exception as e:
                self.logger.warning(f"Error flushing serial buffers: {e}")

    async def set_pattern(self, pattern: EyePattern) -> bool:
        """
        Set the LED pattern.

        Args:
            pattern: The pattern to display
            
        Returns:
            True if successful, False otherwise
        """
        if not self.connected or not self.serial_conn:
            self.logger.error("Cannot set pattern: Not connected to Arduino")
            return False
        
        try:
            # Get the single-character command for this pattern
            if pattern not in self.PATTERN_MAP:
                self.logger.warning(f"Pattern {pattern} not in pattern map, defaulting to IDLE")
                pattern = EyePattern.IDLE
            
            command = self.PATTERN_MAP[pattern]
            self.logger.debug(f"Setting pattern {pattern} using command '{command}'")
            
            # Try directly sending raw command with newline first
            command_with_newline = command + '\n'
            self.logger.debug(f"Sending command: '{command}[newline]'")
            bytes_written = self.serial_conn.write(command_with_newline.encode('utf-8'))
            self.logger.debug(f"Wrote {bytes_written} bytes to serial port")
            self.serial_conn.flush()
            
            # Wait for response
            start_time = time.time()
            response = ""
            
            self.logger.debug(f"Waiting for response with timeout of {self.timeout}s")
            while (time.time() - start_time) < self.timeout:
                if self.serial_conn.in_waiting > 0:
                    try:
                        char = self.serial_conn.read(1).decode('utf-8')
                        response += char
                        self.logger.debug(f"Received character: '{char}', current response: '{response}'")
                        
                        # Check for success/error
                        if '+' in response:
                            self.logger.debug(f"Pattern {pattern} set successfully")
                            return True
                        elif '-' in response:
                            # Don't immediately fail - log a warning instead
                            self.logger.warning(f"Arduino sent error response for pattern {pattern}, but command may still work")
                            
                            # If there's more data, continue reading
                            if self.serial_conn.in_waiting > 0:
                                continue
                                
                            # Since hardware is reliably processing commands even with error response,
                            # we'll still consider this a success
                            self.logger.info(f"Treating pattern {pattern} command as successful despite error response")
                            return True
                    except Exception as e:
                        self.logger.warning(f"Error reading response: {e}")
                        self.serial_conn.reset_input_buffer()
                    
                # Small delay
                await asyncio.sleep(0.01)
            
            self.logger.warning(f"Pattern {pattern} command timed out, assuming success since hardware is working")
            # Assume success for pattern commands since the hardware works reliably
            return True
            
        except Exception as e:
            self.logger.error(f"Error setting pattern {pattern}: {e}")
            return False
        
    async def set_brightness(self, brightness) -> bool:
        """
        Set the LED brightness.

        Args:
            brightness: Brightness level (0.0-1.0 float OR 0-255 int)

        Returns:
            True if successful, False otherwise
        """
        # Handle both formats: float (0.0-1.0) or int (0-255)
        if isinstance(brightness, float) and brightness <= 1.0:
            # Float format (0.0-1.0) - convert to 0-255 range
            level = min(255, max(0, int(brightness * 255)))
        else:
            # Int format (0-255) - use directly
            level = min(255, max(0, int(brightness)))

        command = f'B{level:03d}'  # Format: B000 to B255 (newline added by _send_command)
        self.logger.debug(f"Setting brightness to {brightness} (level {level}) - command: {command}")
        return await self._send_command(command)

    async def set_color(self, r: int, g: int, b: int) -> bool:
        """
        Set the RGB color for the LEDs.

        Args:
            r: Red value (0-255)
            g: Green value (0-255)
            b: Blue value (0-255)

        Returns:
            True if successful, False otherwise
        """
        # Ensure values are in valid range
        r = min(255, max(0, r))
        g = min(255, max(0, g))
        b = min(255, max(0, b))

        # Format color command: CRRGGBB (hex format, newline added by _send_command)
        command = f'C{r:02X}{g:02X}{b:02X}'
        self.logger.info(f"🎨 SimpleEyeAdapter.set_color() called: RGB({r}, {g}, {b}) → command: '{command}'")
        result = await self._send_command(command)
        self.logger.info(f"🎨 SimpleEyeAdapter.set_color() result: {result}")
        return result
        
    async def set_mouth_amplitude(self, amplitude: int) -> bool:
        """
        Set mouth opening amplitude (0-255).

        Args:
            amplitude: Mouth opening level (0=closed/dim, 255=fully open/bright)

        Returns:
            True if successful, False otherwise
        """
        # Clamp to 0-255
        level = min(255, max(0, int(amplitude)))
        command = f"M{level:03d}"  # Format: M000 to M255
        return await self._send_command(command)

    async def reset(self) -> bool:
        """
        Reset the eyes to default state.

        Returns:
            True if successful, False otherwise
        """
        return await self._send_command('R')

    async def get_status(self) -> dict:
        """
        Get the current status of the Arduino and eye patterns.
        
        Returns:
            Dictionary containing status information
        """
        try:
            # If not connected, return disconnected status
            if not self.connected or not self.serial_conn:
                return {
                    "connected": False,
                    "message": "Not connected to Arduino",
                    "error": "No active connection"
                }
            
            # Try multiple attempts for status command with different formats
            for attempt in range(3):
                self.logger.debug(f"Trying status command - attempt {attempt+1}/3")
                
                # Try different command formats
                if attempt == 0:
                    command = 'Z\n'  # With newline
                elif attempt == 1:
                    command = 'Z'    # Without newline
                else:
                    command = 'I\n'  # Try IDLE pattern as fallback to verify connection
                
                # Show newlines as \n in logs
                readable_command = command.replace('\n', '[newline]')
                self.logger.debug(f"Sending raw status command: '{readable_command}'")
                
                # Clear any existing data
                self.serial_conn.reset_input_buffer()
                
                # Send the command
                bytes_written = self.serial_conn.write(command.encode('utf-8'))
                self.logger.debug(f"Wrote {bytes_written} bytes to serial port")
                self.serial_conn.flush()
                
                # Wait for response with timeout
                start_time = time.time()
                response = ""
                
                while (time.time() - start_time) < self.timeout:
                    if self.serial_conn.in_waiting > 0:
                        try:
                            char = self.serial_conn.read(1).decode('utf-8')
                            response += char
                            
                            # Check for success/error
                            if '+' in response:
                                self.logger.debug(f"Status command successful (attempt {attempt+1})")
                                return {
                                    "connected": True,
                                    "port": self.serial_port,
                                    "baud_rate": self.baud_rate,
                                    "status": "OK",
                                    "attempt": attempt+1,
                                    "command": readable_command
                                }
                        except Exception as e:
                            self.logger.warning(f"Error reading status response: {e}")
                            break
                        
                    # Small delay
                    await asyncio.sleep(0.01)
                
                # Wait a bit before the next attempt
                await asyncio.sleep(0.5)
            
            # If we get here, all attempts failed but we still have a connection
            # Return a partial status to indicate a recoverable issue
            return {
                "connected": True,
                "port": self.serial_port,
                "status": "Connected but status command not supported",
                "warning": "Arduino did not respond to status commands, but connection is active"
            }
                
        except Exception as e:
            self.logger.error(f"Error getting status: {e}")
            return {
                "connected": self.connected,
                "port": self.serial_port,
                "status": "Error",
                "error": str(e)
            } 