"""
Eye Light Controller Service for CantinaOS

This service controls the LED patterns for DJ R3X's eyes through Arduino communication.
It translates abstract pattern commands into specific LED instructions and manages the
serial communication with the Arduino hardware.
"""

import asyncio
import json
import logging
import os
import platform
import time
import random
from enum import Enum
from typing import Dict, List, Optional, Union, Any

import serial
import serial.tools.list_ports
from pydantic import ValidationError

from cantina_os.base_service import BaseService
from cantina_os.event_payloads import (
    BaseEventPayload,
    EyeCommandPayload,
    SentimentPayload,
    LLMResponsePayload,
    ServiceStatus
)
from cantina_os.event_topics import EventTopics


class EyePattern(str, Enum):
    """Enumeration of available eye LED patterns."""
    IDLE = "idle"
    STARTUP = "startup"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    HAPPY = "happy"
    SAD = "sad"
    ANGRY = "angry"
    SURPRISED = "surprised"
    ERROR = "error"
    CUSTOM = "custom"  # For custom patterns with specific colors/sequences


class ArduinoCommand(str, Enum):
    """Enumeration of commands that can be sent to the Arduino."""
    SET_PATTERN = "set_pattern"
    SET_COLOR = "set_color"
    SET_BRIGHTNESS = "set_brightness"
    RESET = "reset"
    TEST = "test"
    STATUS = "status"


class EyeLightControllerService(BaseService):
    """
    Service to control the LED patterns for DJ R3X's eyes.
    
    This service manages communication with an Arduino over serial connection
    to control LED patterns and animations based on the robot's state and mood.
    """

    def __init__(
        self,
        event_bus,
        serial_port: Optional[str] = None,
        baud_rate: int = 115200,
        connection_timeout: float = 3.0,
        command_timeout: float = 1.0,
        retry_attempts: int = 3,
        retry_delay: float = 0.5,
        mock_mode: bool = False,
        name: str = "eye_light_controller"
    ):
        """
        Initialize the EyeLightControllerService.
        
        Args:
            event_bus: Event bus for inter-service communication
            serial_port: Serial port for Arduino connection. If None, auto-detection will be attempted.
            baud_rate: Baud rate for serial communication.
            connection_timeout: Timeout for initial connection in seconds.
            command_timeout: Timeout for individual commands in seconds.
            retry_attempts: Number of times to retry failed commands.
            retry_delay: Delay between retry attempts in seconds.
            mock_mode: If True, operate in mock mode without real hardware.
            name: Service name.
        """
        super().__init__(service_name=name, event_bus=event_bus)
        
        # Serial configuration
        self.serial_port = serial_port
        self.baud_rate = baud_rate
        self.connection_timeout = connection_timeout
        self.command_timeout = command_timeout
        self.retry_attempts = retry_attempts
        self.retry_delay = retry_delay
        self.mock_mode = mock_mode
        
        # Runtime variables
        self.serial_connection = None
        self.command_lock = asyncio.Lock()
        self.current_pattern = None
        self.current_color = None
        self.current_brightness = 0.8  # Default brightness
        self.connected = False
        self.last_command_time = 0
        
    async def _start(self) -> None:
        """Start the service and connect to the Arduino."""
        self.logger.info("Starting EyeLightControllerService")
        
        # Subscribe to events
        await self.subscribe(EventTopics.LED_COMMAND, self._handle_eye_command)
        await self.subscribe(EventTopics.LLM_SENTIMENT_ANALYZED, self._handle_sentiment)
        await self.subscribe(EventTopics.SPEECH_SYNTHESIS_STARTED, self._handle_speech_started)
        await self.subscribe(EventTopics.SPEECH_SYNTHESIS_ENDED, self._handle_speech_ended)
        await self.subscribe(EventTopics.SPEECH_GENERATION_STARTED, self._handle_speech_started)
        await self.subscribe(EventTopics.SPEECH_GENERATION_COMPLETE, self._handle_speech_ended)
        await self.subscribe(EventTopics.AUDIO_TRANSCRIPTION_FINAL, self._handle_listening_ended)
        await self.subscribe(EventTopics.AUDIO_TRANSCRIPTION_INTERIM, self._handle_listening_active)
        
        # Add handlers for text-based recording mode
        await self.subscribe(EventTopics.VOICE_LISTENING_STARTED, self._handle_voice_listening_started)
        await self.subscribe(EventTopics.VOICE_LISTENING_STOPPED, self._handle_voice_listening_ended)
        await self.subscribe(EventTopics.VOICE_PROCESSING_STARTED, self._handle_voice_processing_started)
        
        if self.mock_mode:
            self.logger.info("Running in mock mode - no hardware connection will be made")
            self.connected = True
            self._status = ServiceStatus.RUNNING
            return
        
        try:
            # Attempt to auto-detect Arduino if port not specified
            if not self.serial_port:
                self.serial_port = await self._auto_detect_arduino()
                if not self.serial_port:
                    self.logger.warning("Could not auto-detect Arduino, will retry periodically")
                    self._status = ServiceStatus.DEGRADED
                    return
            
            # Connect to Arduino
            await self._connect_to_arduino()
            
            if self.connected:
                # Set initial pattern
                await self.set_pattern(EyePattern.STARTUP)
                await asyncio.sleep(2.0)  # Allow startup animation to play
                await self.set_pattern(EyePattern.IDLE)
                self._status = ServiceStatus.RUNNING
            else:
                self.logger.error("Failed to connect to Arduino")
                self._status = ServiceStatus.ERROR
        
        except Exception as e:
            self.logger.error(f"Error starting EyeLightControllerService: {e}")
            self._status = ServiceStatus.ERROR
    
    async def _stop(self) -> None:
        """Stop the service and disconnect from the Arduino."""
        self.logger.info("Stopping EyeLightControllerService")
        
        if not self.mock_mode and self.connected:
            try:
                # Set LEDs to off/idle before disconnecting
                await self.set_pattern(EyePattern.IDLE)
                await asyncio.sleep(0.5)  # Give time for command to process
                
                # Close serial connection
                if self.serial_connection:
                    self.serial_connection.close()
                    self.serial_connection = None
            except Exception as e:
                self.logger.error(f"Error during shutdown: {e}")
        
        self.connected = False
        self._status = ServiceStatus.STOPPED
    
    async def _auto_detect_arduino(self) -> Optional[str]:
        """
        Auto-detect the Arduino serial port.
        
        Returns:
            The detected serial port or None if not found.
        """
        self.logger.info("Auto-detecting Arduino serial port")
        
        # Common Arduino identifiers
        arduino_ids = [
            # USB VID:PID pairs for various Arduino models
            "2341:0043",  # Arduino Uno
            "2341:0001",  # Arduino Mega
            "2341:0010",  # Arduino Mega 2560
            "2341:0042",  # Arduino Mega 2560 R3
            "2341:0036",  # Arduino Leonardo
            "2341:8036",  # Arduino Leonardo (bootloader)
            "2341:0037",  # Arduino Micro
            "2341:8037",  # Arduino Micro (bootloader)
            "2341:0041",  # Arduino Yun
            "2341:8041",  # Arduino Yun (bootloader)
            "2341:0044",  # Arduino Pro Micro
            "2341:0045",  # Arduino Pro Micro (different version)
            "2A03:0043",  # Arduino Uno (clone)
        ]
        
        # Get list of all serial ports
        ports = list(serial.tools.list_ports.comports())
        
        # First, try to find an Arduino by VID:PID
        for port in ports:
            # Skip ports that don't have vid/pid information
            if port.vid is None or port.pid is None:
                continue
                
            vid_pid = f"{port.vid:04X}:{port.pid:04X}"
            if vid_pid in arduino_ids:
                self.logger.info(f"Found Arduino at {port.device} with ID {vid_pid}")
                return port.device
                
        # If not found by VID:PID, check for common Arduino names
        arduino_names = ["arduino", "ttyACM", "ttyUSB", "usbmodem"]
        for port in ports:
            for name in arduino_names:
                if name.lower() in port.device.lower():
                    self.logger.info(f"Found possible Arduino at {port.device} (name match)")
                    return port.device
                    
        # Check manufacturer string if VID:PID and name don't match
        for port in ports:
            if port.manufacturer and "arduino" in port.manufacturer.lower():
                self.logger.info(f"Found Arduino at {port.device} (manufacturer match)")
                return port.device
                
        # If on macOS, look for cu.usbmodem devices as a fallback
        if platform.system() == "Darwin":
            for port in ports:
                if "cu.usbmodem" in port.device:
                    self.logger.info(f"Found possible Arduino at {port.device} (macOS usbmodem)")
                    return port.device
        
        self.logger.warning("No Arduino device detected")
        return None
    
    async def _connect_to_arduino(self) -> None:
        """Establish connection to the Arduino board."""
        if not self.serial_port:
            self.logger.error("No serial port specified for Arduino connection")
            self.connected = False
            return
        
        try:
            self.logger.info(f"Connecting to Arduino at {self.serial_port}")
            
            # Open serial connection
            self.serial_connection = serial.Serial(
                port=self.serial_port,
                baudrate=self.baud_rate,
                timeout=self.connection_timeout
            )
            
            # Clear any buffered data
            self.serial_connection.reset_input_buffer()
            self.serial_connection.reset_output_buffer()
            
            # Wait for Arduino to initialize (it often resets on connection)
            await asyncio.sleep(2.0)
            
            # Send test command to verify connection
            success = await self._send_command({
                "command": ArduinoCommand.TEST,
                "params": {}
            })
            
            if success:
                self.logger.info("Successfully connected to Arduino")
                self.connected = True
            else:
                self.logger.error("Failed to communicate with Arduino")
                self.connected = False
                if self.serial_connection:
                    self.serial_connection.close()
                    self.serial_connection = None
        
        except serial.SerialException as e:
            self.logger.error(f"Serial connection error: {e}")
            self.connected = False
            if self.serial_connection:
                self.serial_connection.close()
                self.serial_connection = None
    
    async def _send_command(self, command_obj: Dict[str, Any]) -> bool:
        """
        Send a command to the Arduino and wait for acknowledgment.
        
        Args:
            command_obj: The command object to send to the Arduino.
            
        Returns:
            True if command was successful, False otherwise.
        """
        if self.mock_mode:
            # In mock mode, just log the command and return success
            self.logger.info(f"MOCK MODE: Would send command: {command_obj}")
            self.last_command_time = time.time()
            return True
            
        if not self.connected or not self.serial_connection:
            self.logger.error("Cannot send command: Not connected to Arduino")
            return False
        
        # Implement exponential backoff retry mechanism
        for attempt in range(self.retry_attempts):
            # Use lock to ensure only one command is sent at a time
            async with self.command_lock:
                try:
                    # Serialize command to JSON with minimal structure
                    command_json = json.dumps(command_obj, separators=(',', ':')) + "\n"
                    command_bytes = command_json.encode("utf-8")
                    
                    # Record command time
                    self.last_command_time = time.time()
                    
                    # Clear any buffered data before sending command
                    self.serial_connection.reset_input_buffer()
                    self.serial_connection.reset_output_buffer()
                    
                    # Send the command
                    self.logger.debug(f"Sending command (attempt {attempt+1}/{self.retry_attempts}): {command_obj}")
                    self.serial_connection.write(command_bytes)
                    self.serial_connection.flush()
                    
                    # Wait for acknowledgment using chunk-based reading
                    start_time = time.time()
                    response_buffer = bytearray()
                    
                    while (time.time() - start_time) < self.command_timeout:
                        if self.serial_connection.in_waiting > 0:
                            # Read available bytes
                            chunk = self.serial_connection.read(self.serial_connection.in_waiting)
                            response_buffer.extend(chunk)
                            
                            try:
                                # Try to parse complete JSON messages
                                response_str = response_buffer.decode("utf-8")
                                lines = response_str.split('\n')
                                
                                # Process all complete lines
                                for line in lines[:-1]:
                                    line = line.strip()
                                    if not line:
                                        continue
                                        
                                    try:
                                        response = json.loads(line)
                                        
                                        # Check for debug messages
                                        if "debug" in response:
                                            self.logger.debug(f"Arduino debug: {response['debug']}")
                                            if "free_mem" in response:
                                                self.logger.debug(f"Arduino free memory: {response['free_mem']} bytes")
                                            continue
                                            
                                        # Check for success acknowledgment
                                        if "ack" in response and response["ack"] == True:
                                            return True
                                            
                                        # Check for error response
                                        elif "error" in response:
                                            error_code = response["error"]
                                            details = response.get("details", "")
                                            self.logger.error(f"Arduino error: {error_code} - {details}")
                                            
                                            # Check if error is recoverable
                                            if error_code in ["json_parse", "cmd_missing", "params_missing"]:
                                                # These are likely to be recoverable with a retry
                                                break
                                            else:
                                                # Non-recoverable error - don't retry
                                                return False
                                    except json.JSONDecodeError as e:
                                        self.logger.warning(f"Invalid JSON response: {line} - {e}")
                                
                                # Keep any incomplete message in the buffer
                                if lines[-1]:
                                    response_buffer = bytearray(lines[-1].encode("utf-8"))
                                else:
                                    response_buffer = bytearray()
                                    
                            except UnicodeDecodeError:
                                self.logger.warning("Failed to decode response as UTF-8")
                        
                        # Small delay to prevent CPU overload
                        await asyncio.sleep(0.01)
                    
                    self.logger.error(f"Command timed out after {self.command_timeout} seconds")
                    
                except Exception as e:
                    self.logger.error(f"Error sending command: {e}")
            
            # If we get here, the command failed
            if attempt < self.retry_attempts - 1:
                # Calculate backoff delay (exponential with jitter)
                delay = self.retry_delay * (2 ** attempt) * (0.5 + 0.5 * random.random())
                self.logger.warning(f"Command failed, retrying in {delay:.2f}s (attempt {attempt+1}/{self.retry_attempts})")
                await asyncio.sleep(delay)
        
        return False  # All retries failed
    
    async def set_pattern(
        self, 
        pattern: EyePattern, 
        color: Optional[str] = None, 
        brightness: Optional[float] = None,
        duration: Optional[float] = None
    ) -> bool:
        """
        Set the LED pattern for the eyes.
        
        Args:
            pattern: The pattern to display
            color: Optional color for the pattern (hex format, e.g., "#FF0000")
            brightness: Optional brightness level (0.0-1.0)
            duration: Optional duration for the pattern in seconds
            
        Returns:
            True if successful, False otherwise
        """
        # Create a flat params structure - avoid deep nesting that can cause JSON issues
        params = {"pattern": pattern}
        
        if color:
            params["color"] = color
            self.current_color = color
            
        if brightness is not None:
            # Ensure brightness is within valid range
            brightness = max(0.0, min(1.0, brightness))
            params["brightness"] = brightness
            self.current_brightness = brightness
            
        if duration:
            params["duration"] = duration
            
        # Send the command to the Arduino
        success = await self._send_command({
            "command": ArduinoCommand.SET_PATTERN,
            "params": params
        })
        
        if success:
            self.current_pattern = pattern
            self.logger.info(f"Set eye pattern to {pattern}")
            
        return success
    
    async def set_color(self, color: str) -> bool:
        """
        Set the LED color.
        
        Args:
            color: Color in hex format (e.g., "#FF0000")
            
        Returns:
            True if successful, False otherwise
        """
        success = await self._send_command({
            "command": ArduinoCommand.SET_COLOR,
            "params": {"color": color}
        })
        
        if success:
            self.current_color = color
            self.logger.info(f"Set eye color to {color}")
            
        return success
    
    async def set_brightness(self, brightness: float) -> bool:
        """
        Set the LED brightness.
        
        Args:
            brightness: Brightness level (0.0-1.0)
            
        Returns:
            True if successful, False otherwise
        """
        # Ensure brightness is within valid range
        brightness = max(0.0, min(1.0, brightness))
        
        success = await self._send_command({
            "command": ArduinoCommand.SET_BRIGHTNESS,
            "params": {"brightness": brightness}
        })
        
        if success:
            self.current_brightness = brightness
            self.logger.info(f"Set eye brightness to {brightness}")
            
        return success
    
    async def _handle_eye_command(self, event_payload: Union[BaseEventPayload, EyeCommandPayload]) -> None:
        """
        Handle eye control commands.
        
        Args:
            event_payload: The payload containing the eye command.
        """
        if not isinstance(event_payload, EyeCommandPayload):
            try:
                # Try to convert from dict or BaseEventPayload
                event_payload = EyeCommandPayload.model_validate(event_payload.model_dump())
            except (ValidationError, AttributeError) as e:
                self.logger.error(f"Invalid event payload type: {type(event_payload)}, {str(e)}")
                return
        
        try:
            pattern = EyePattern(event_payload.pattern)
        except ValueError:
            self.logger.error(f"Invalid eye pattern: {event_payload.pattern}")
            return
            
        await self.set_pattern(
            pattern=pattern,
            color=event_payload.color,
            brightness=event_payload.intensity,
            duration=event_payload.duration
        )
    
    async def _handle_sentiment(self, event_payload: Union[BaseEventPayload, SentimentPayload]) -> None:
        """
        Handle sentiment analysis results.
        
        Args:
            event_payload: The payload containing sentiment information.
        """
        if not isinstance(event_payload, SentimentPayload):
            try:
                # Try to convert from dict or BaseEventPayload
                event_payload = SentimentPayload.model_validate(event_payload.model_dump())
            except (ValidationError, AttributeError) as e:
                self.logger.error(f"Invalid event payload type: {type(event_payload)}, {str(e)}")
                return
        
        # Map sentiment to eye pattern
        if event_payload.label == "positive":
            pattern = EyePattern.HAPPY
            color = "#00FF00"  # Green
        elif event_payload.label == "negative":
            pattern = EyePattern.SAD
            color = "#0000FF"  # Blue
        elif event_payload.label == "angry":
            pattern = EyePattern.ANGRY
            color = "#FF0000"  # Red
        elif event_payload.label == "surprised":
            pattern = EyePattern.SURPRISED
            color = "#FFFF00"  # Yellow
        else:
            # Default to speaking pattern if sentiment is neutral or unknown
            return
        
        # Only change the pattern if we're not in a critical state (like listening or processing)
        if self.current_pattern not in [EyePattern.LISTENING, EyePattern.THINKING]:
            await self.set_pattern(pattern=pattern, color=color, duration=2.0)
            # After the emotion display, return to current activity pattern
            if self.current_pattern == EyePattern.SPEAKING:
                await asyncio.sleep(2.0)
                await self.set_pattern(EyePattern.SPEAKING)
    
    async def _handle_speech_started(self, event_payload: BaseEventPayload) -> None:
        """
        Handle speech synthesis/generation started events.
        
        Args:
            event_payload: The event payload.
        """
        await self.set_pattern(EyePattern.SPEAKING)
    
    async def _handle_speech_ended(self, event_payload: BaseEventPayload) -> None:
        """
        Handle speech synthesis/generation ended events.
        
        Args:
            event_payload: The event payload.
        """
        await self.set_pattern(EyePattern.IDLE)
    
    async def _handle_listening_active(self, event_payload: BaseEventPayload) -> None:
        """
        Handle active listening events.
        
        Args:
            event_payload: The event payload.
        """
        if self.current_pattern != EyePattern.LISTENING:
            await self.set_pattern(EyePattern.LISTENING)
    
    async def _handle_listening_ended(self, event_payload: BaseEventPayload) -> None:
        """
        Handle end of listening events.
        
        Args:
            event_payload: The event payload.
        """
        if self.current_pattern == EyePattern.LISTENING:
            await self.set_pattern(EyePattern.THINKING)
    
    async def _handle_voice_listening_started(self, event_payload: BaseEventPayload) -> None:
        """Handle voice listening started events (text recording mode)."""
        self.logger.info("Voice recording started, setting LED pattern to LISTENING")
        await self.set_pattern(EyePattern.LISTENING)
    
    async def _handle_voice_listening_ended(self, event_payload: BaseEventPayload) -> None:
        """Handle voice listening ended events (text recording mode)."""
        self.logger.info("Voice recording ended")
        # No need to set pattern here as we'll immediately get a processing started event
    
    async def _handle_voice_processing_started(self, event_payload: BaseEventPayload) -> None:
        """Handle voice processing started events (text recording mode)."""
        self.logger.info("Voice processing started, setting LED pattern to THINKING")
        await self.set_pattern(EyePattern.THINKING) 