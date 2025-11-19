"""
Eye Light Controller Service for CantinaOS

This service controls the LED patterns for DJ R3X's eyes through Arduino communication.
It translates abstract pattern commands into specific LED instructions and manages the
serial communication with the Arduino hardware.
"""

"""
SERVICE: EyeLightControllerService
PURPOSE: LED pattern control for DJ R3X's eyes via Arduino communication with mode-aware pattern switching
EVENTS_IN: LED_COMMAND, EYE_COMMAND, LLM_SENTIMENT_ANALYZED, SPEECH_SYNTHESIS_STARTED, SPEECH_SYNTHESIS_ENDED, TRANSCRIPTION_FINAL, VOICE_LISTENING_STARTED, SYSTEM_MODE_CHANGE, LLM_RESPONSE_CHUNK
EVENTS_OUT: CLI_RESPONSE, DEBUG_LOG, SERVICE_STATUS_UPDATE
KEY_METHODS: set_pattern, set_brightness, handle_eye_test, handle_eye_status, handle_eye_pattern, _auto_detect_arduino, _connect_to_arduino
DEPENDENCIES: Arduino hardware (optional, falls back to mock mode), serial communication, SimpleEyeAdapter
"""

import asyncio
import json
import logging
import os
import platform
import time
import random
from enum import Enum
from typing import Dict, List, Optional, Union, Any, Literal

import serial
import serial.tools.list_ports
from pydantic import ValidationError, BaseModel, Field

from ..base_service import BaseService, RealtimeService
from cantina_os.event_payloads import (
    BaseEventPayload,
    EyeCommandPayload,
    SentimentPayload,
    LLMResponsePayload,
    ServiceStatus,
    LogLevel,
    SystemModeChangePayload,
    StandardCommandPayload
)
from cantina_os.core.event_topics import EventTopics
from cantina_os.utils.command_decorators import compound_command, register_service_commands, validate_compound_command, command_error_handler
from .simple_eye_adapter import SimpleEyeAdapter


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
    CUSTOM = "custom"  # For custom patterns with specific colors/sequences


class ArduinoCommand(str, Enum):
    """Enumeration of commands that can be sent to the Arduino."""
    SET_PATTERN = "set_pattern"
    SET_COLOR = "set_color"
    SET_BRIGHTNESS = "set_brightness"
    RESET = "reset"
    TEST = "test"
    STATUS = "status"


class EyeCommandType(str, Enum):
    """Enumeration of available eye command types."""
    TEST = "test"
    STATUS = "status"
    PATTERN = "pattern"


class EyeCliCommandPayload(BaseModel):
    """Payload for CLI eye commands."""
    command: str = Field(..., description="The base command ('eye')")
    subcommand: Optional[EyeCommandType] = Field(None, description="The subcommand type (test, status, pattern)")
    pattern_name: Optional[str] = Field(None, description="Pattern name when using 'pattern' subcommand")
    
    @classmethod
    def from_cli_payload(cls, payload: Dict[str, Any]) -> 'EyeCliCommandPayload':
        """Create an eye command payload from a CLI command payload."""
        # Log the exact payload for debugging
        raw_command = payload.get("raw_command", "")
        command = payload.get("command", "").lower()
        
        # Get args - handle both string and list formats
        args_data = payload.get("args", [])
        if isinstance(args_data, str):
            args = args_data.lower().split()
        elif isinstance(args_data, list):
            args = [str(arg).lower() for arg in args_data]
        else:
            args = []
        
        # Critical fix: If command contains spaces like "eye status", parse it
        # The command should be just "eye", but CommandDispatcher might be sending "eye status" as command
        if " " in command:
            parts = command.split()
            if len(parts) >= 1 and parts[0] == "eye":
                if len(parts) >= 2 and parts[1] in [e.value for e in EyeCommandType]:
                    subcommand = EyeCommandType(parts[1])
                    # Handle "eye pattern X" format
                    if subcommand == EyeCommandType.PATTERN and len(parts) > 2:
                        pattern_name = parts[2]
                    else:
                        pattern_name = None
                        
                    return cls(
                        command="eye",
                        subcommand=subcommand,
                        pattern_name=pattern_name
                    )

        # Standard case: command is just "eye" and args contains the subcommand
        if command == "eye" and args:
            if args[0] in [e.value for e in EyeCommandType]:
                subcommand = EyeCommandType(args[0])
                # Handle "eye pattern X" format
                if subcommand == EyeCommandType.PATTERN and len(args) > 1:
                    pattern_name = args[1]
                else:
                    pattern_name = None
                    
                return cls(
                    command="eye",
                    subcommand=subcommand,
                    pattern_name=pattern_name
                )
                
        # Default/fallback: just return the base command
        return cls(
            command=command,
            subcommand=None,
            pattern_name=None
        )
        
    def is_valid(self) -> bool:
        """Check if this is a valid eye command."""
        return (
            self.command == "eye" and
            self.subcommand is not None
        )
        
    def __str__(self) -> str:
        """Get string representation of the command."""
        result = self.command
        if self.subcommand:
            result += f" {self.subcommand}"
            if self.subcommand == EyeCommandType.PATTERN and self.pattern_name:
                result += f" {self.pattern_name}"
        return result


class EyeLightControllerService(RealtimeService):
    """
    Service to control the LED patterns for DJ R3X's eyes with real-time control loop.

    This service manages communication with an Arduino over serial connection
    to control LED patterns and animations based on the robot's state and mood.

    Architecture:
    - Event handlers SET STATE (pattern, color, brightness targets)
    - 60Hz control loop EXECUTES STATE (sends commands to Arduino)
    - Future: Amplitude pulsing, smooth transitions, beat sync
    """

    # RGB Color mappings for different modes and sentiments
    MODE_COLORS = {
        "IDLE": (255, 60, 0),          # Deep orange-red (matches Arduino default)
        "AMBIENT": (128, 0, 255),      # Purple (will cycle in animation)
        "INTERACTIVE": (0, 255, 255),  # Cyan (alert, ready)
        "SLEEPING": (255, 68, 0),      # Dim orange
    }

    SENTIMENT_COLORS = {
        "positive": (0, 255, 0),       # Green
        "negative": (65, 105, 225),    # Royal blue
        "angry": (255, 0, 0),          # Red
        "surprised": (255, 255, 0),    # Yellow
        "neutral": (255, 255, 255),    # White
    }

    def __init__(
        self,
        event_bus,
        serial_port: Optional[str] = None,
        baud_rate: int = 115200,
        connection_timeout: float = 3.0,
        command_timeout: float = 1.0,
        retry_attempts: int = 3,
        retry_delay: float = 0.5,
        mock_mode: bool = False,  # Changed default from None to False to prefer hardware
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
                       Default is False to prefer hardware connection.
            name: Service name.
        """
        # Initialize RealtimeService with 60Hz control loop
        super().__init__(service_name=name, event_bus=event_bus, loop_rate_hz=60)

        # Control loop state (set by event handlers, read by _control_update)
        self._target_pattern = EyePattern.IDLE
        self._target_color = self.MODE_COLORS["IDLE"]  # Warm yellow default
        self._target_brightness = 128  # 0-255 range
        self._amplitude_modulation = 0.0  # 0.0 to 1.0 (for future TTS pulsing)

        # Current hardware state (what Arduino currently has)
        self._current_pattern = None
        self._current_color = None
        self._current_brightness = None

        # Mouth amplitude throttling (to prevent 60Hz spam to Arduino)
        self._last_mouth_command_time = 0.0
        self.MOUTH_UPDATE_INTERVAL = 0.05  # 20Hz max (50ms between commands)

        # Serial configuration
        self.serial_port = serial_port
        self.baud_rate = baud_rate
        self.connection_timeout = connection_timeout
        self.command_timeout = command_timeout
        self.retry_attempts = retry_attempts
        self.retry_delay = retry_delay
        
        # Use explicit mock_mode value
        self.mock_mode = mock_mode
        
        # Track current system mode
        self._current_system_mode = "IDLE"
        
        # Override settings from environment if needed for development/testing
        if os.environ.get("FORCE_MOCK_LED_CONTROLLER", "").lower() in ("true", "1", "yes"):
            self.logger.info("Forcing mock mode due to FORCE_MOCK_LED_CONTROLLER environment variable")
            self.mock_mode = True
            
        # Override serial port from environment if provided
        env_port = os.environ.get("ARDUINO_SERIAL_PORT")
        if env_port:
            self.serial_port = env_port
            
        # Override baud rate from environment if provided
        env_baud = os.environ.get("ARDUINO_BAUD_RATE")
        if env_baud:
            try:
                self.baud_rate = int(env_baud)
            except ValueError:
                self.logger.warning(f"Invalid baud rate in environment: {env_baud}, using default {self.baud_rate}")
        
        # Runtime variables
        self.adapter = None
        self.command_lock = asyncio.Lock()
        self.current_pattern = None
        self.current_color = None
        self.current_brightness = 0.8  # Default brightness
        self.connected = False
        self.last_command_time = 0
        
        # Set default command topic for auto-registration
        self._default_command_topic = EventTopics.EYE_COMMAND
        
    async def _start(self) -> None:
        """Start the service and connect to the Arduino."""
        self.logger.info("Starting EyeLightControllerService")
        
        # Subscribe to events
        await self.subscribe(EventTopics.LED_COMMAND, self._handle_eye_command)
        await self.subscribe(EventTopics.LLM_SENTIMENT_ANALYZED, self._handle_sentiment)
        await self.subscribe(EventTopics.SPEECH_SYNTHESIS_STARTED, self._handle_speech_started)
        await self.subscribe(EventTopics.SPEECH_SYNTHESIS_AMPLITUDE, self._handle_amplitude)
        await self.subscribe(EventTopics.SPEECH_SYNTHESIS_ENDED, self._handle_speech_ended)
        await self.subscribe(EventTopics.SPEECH_GENERATION_STARTED, self._handle_speech_started)
        await self.subscribe(EventTopics.SPEECH_GENERATION_COMPLETE, self._handle_speech_ended)

        # LISTENING/THINKING triggers - use recording state, NOT transcript events
        # This ensures eyes show LISTENING immediately when user clicks, even during pauses
        await self.subscribe(EventTopics.VOICE_LISTENING_STARTED, self._handle_voice_listening_started)
        await self.subscribe(EventTopics.VOICE_LISTENING_STOPPED, self._handle_voice_listening_ended)
        await self.subscribe(EventTopics.VOICE_PROCESSING_STARTED, self._handle_voice_processing_started)

        # Add handler for mouse recording events for more immediate response
        await self.subscribe(EventTopics.MOUSE_RECORDING_STOPPED, self._handle_mouse_recording_stopped)
        
        # Add handler for LLM responses to switch to speaking mode as soon as text generation starts
        await self.subscribe(EventTopics.LLM_RESPONSE_CHUNK, self._handle_llm_response_chunk)
        
        # Add handler for system mode changes
        await self.subscribe(EventTopics.SYSTEM_MODE_CHANGE, self._handle_mode_change)
        
        # Add handler for CLI commands
        await self.subscribe(EventTopics.EYE_COMMAND, self._handle_cli_command)

        # Auto-register compound commands using decorators
        register_service_commands(self, self._event_bus)
        self.logger.info("Auto-registered eye commands using decorators")
        
        # Emit configuration info to debug service
        await self.emit(EventTopics.DEBUG_LOG, {
            "level": LogLevel.INFO,
            "message": f"EyeLightController starting with config: mock_mode={self.mock_mode}, serial_port={self.serial_port}, baud_rate={self.baud_rate}"
        })
        
        if self.mock_mode:
            self.logger.info("Running in mock mode - no hardware connection will be made")
            await self.emit(EventTopics.DEBUG_LOG, {
                "component": "eye_light_controller",
                "level": LogLevel.WARNING,
                "message": "Eye lights running in MOCK MODE - no Arduino connection will be attempted"
            })
            self.connected = True
            self._status = ServiceStatus.RUNNING
            return
        
        # Hardware mode setup
        self.logger.info("Attempting to connect to Arduino hardware...")
        
        try:
            # Attempt to auto-detect Arduino if port not specified
            if not self.serial_port:
                self.logger.info("No serial port specified, attempting auto-detection")
                self.serial_port = await self._auto_detect_arduino()
                if not self.serial_port:
                    self.logger.warning("Could not auto-detect Arduino, falling back to mock mode")
                    await self.emit(EventTopics.DEBUG_LOG, {
                        "component": "eye_light_controller",
                        "level": LogLevel.WARNING,
                        "message": "Failed to auto-detect Arduino device. Falling back to mock mode."
                    })
                    # Switch to mock mode instead of failing
                    self.mock_mode = True
                    self.connected = True
                    self._status = ServiceStatus.RUNNING
                    return
            
            # Connect to Arduino - with retry logic
            self.logger.info(f"Attempting to connect to Arduino at {self.serial_port}")
            for attempt in range(self.retry_attempts):
                try:
                    await self._connect_to_arduino()
                    if self.connected:
                        break
                    else:
                        self.logger.warning(f"Connection attempt {attempt+1}/{self.retry_attempts} failed, retrying...")
                        await asyncio.sleep(self.retry_delay)
                except Exception as e:
                    self.logger.error(f"Error in connection attempt {attempt+1}/{self.retry_attempts}: {e}")
                    if attempt < self.retry_attempts - 1:
                        await asyncio.sleep(self.retry_delay)
            
            if self.connected:
                # Set initial pattern
                self.logger.info(f"Successfully connected to Arduino at {self.serial_port}")
                await self.emit(EventTopics.DEBUG_LOG, {
                    "component": "eye_light_controller",
                    "level": LogLevel.INFO,
                    "message": f"Successfully connected to Arduino at {self.serial_port}"
                })
                
                # Try to set initial idle pattern with retry
                initial_pattern_set = False
                for attempt in range(3):  # Try up to 3 times
                    try:
                        # Small delay between attempts
                        # Arduino ready signal is now handled in SimpleEyeAdapter
                        if attempt > 0:
                            await asyncio.sleep(0.5)

                        # Set IDLE color first, then pattern
                        self.logger.info(f"Setting initial IDLE color and pattern (attempt {attempt+1}/3)")

                        idle_color = self.MODE_COLORS["IDLE"]  # (255, 60, 0) - deep orange-red
                        if self.adapter and hasattr(self.adapter, 'set_color'):
                            self.logger.info(f"Setting initial color to RGB{idle_color}")
                            await self.adapter.set_color(*idle_color)

                        if await self.set_pattern(EyePattern.IDLE):
                            initial_pattern_set = True
                            self.logger.info("Initial IDLE color and pattern set successfully")
                            break
                        else:
                            self.logger.warning(f"Failed to set initial IDLE pattern (attempt {attempt+1}/3)")
                    except Exception as e:
                        self.logger.error(f"Error setting initial color/pattern (attempt {attempt+1}/3): {e}")

                if not initial_pattern_set:
                    self.logger.warning("Could not set initial color/pattern, will continue anyway")
                
                self._status = ServiceStatus.RUNNING
                self.logger.info("Arduino setup complete - EyeLightControllerService running in hardware mode")
            else:
                self.logger.warning("Failed to connect to Arduino after multiple attempts, falling back to mock mode")
                await self.emit(EventTopics.DEBUG_LOG, {
                    "component": "eye_light_controller",
                    "level": LogLevel.WARNING,
                    "message": f"Failed to connect to Arduino at {self.serial_port} after {self.retry_attempts} attempts. Falling back to mock mode."
                })
                # Switch to mock mode instead of failing
                self.mock_mode = True
                self.connected = True
                self._status = ServiceStatus.RUNNING
        
        except Exception as e:
            self.logger.error(f"Error starting EyeLightControllerService: {e}")
            await self.emit(EventTopics.DEBUG_LOG, {
                "component": "eye_light_controller",
                "level": LogLevel.ERROR,
                "message": f"Exception during Arduino connection: {str(e)}. Falling back to mock mode."
            })
            # Switch to mock mode instead of failing
            self.mock_mode = True  
            self.connected = True
            self._status = ServiceStatus.RUNNING
    
    async def _stop(self) -> None:
        """Stop the service and disconnect from the Arduino."""
        self.logger.info("Stopping EyeLightControllerService")
        
        if not self.mock_mode and self.connected:
            try:
                # Set LEDs to off/idle before disconnecting
                await self.set_pattern(EyePattern.IDLE)
                await asyncio.sleep(0.5)  # Give time for command to process
                
                # Disconnect from Arduino
                if self.adapter:
                    await self.adapter.disconnect()
                    self.adapter = None
            except Exception as e:
                self.logger.error(f"Error during shutdown: {e}")
        
        self.connected = False
        self._status = ServiceStatus.STOPPED

    async def _control_update(self) -> None:
        """
        Real-time control loop update (called 60 times per second).

        This method reads the target state (set by event handlers) and sends
        commands to the Arduino only when state changes. This prevents spamming
        the Arduino with redundant commands.

        Architecture:
        - Event handlers SET: self._target_pattern, self._target_color, self._target_brightness
        - This method READS targets and SENDS to Arduino if changed
        - Future: Will add smooth transitions and amplitude pulsing
        """
        # Skip if in mock mode or not connected to hardware
        if self.mock_mode or not self.connected or not self.adapter:
            # Only log once per startup to avoid spam
            if not hasattr(self, '_control_loop_skip_logged'):
                self.logger.warning(
                    f"Control loop skipping: mock_mode={self.mock_mode}, "
                    f"connected={self.connected}, adapter={self.adapter is not None}"
                )
                self._control_loop_skip_logged = True
            return

        # Log the first successful control loop execution to verify it's running
        if not hasattr(self, '_control_loop_started_logged'):
            self.logger.info(
                f"Control loop ACTIVE: target_pattern={self._target_pattern}, "
                f"current_pattern={self._current_pattern}, "
                f"target_color={self._target_color}, "
                f"current_color={self._current_color}"
            )
            self._control_loop_started_logged = True

        try:
            # CRITICAL: Send color BEFORE pattern!
            # Arduino uses currentColor when executing patterns, so color must be set first

            # Check if color changed
            if self._target_color != self._current_color:
                self.logger.info(
                    f"Control loop: Color changed {self._current_color} -> {self._target_color}"
                )
                await self._send_color_to_arduino(self._target_color)
                self._current_color = self._target_color

            # Check if pattern changed
            if self._target_pattern != self._current_pattern:
                self.logger.info(
                    f"Control loop: Pattern changed {self._current_pattern} -> {self._target_pattern}"
                )
                await self._send_pattern_to_arduino(self._target_pattern)
                self._current_pattern = self._target_pattern

            # Calculate final brightness (base + amplitude modulation for future)
            final_brightness = self._target_brightness
            if self._amplitude_modulation > 0:
                # Future: Pulse brightness based on speech amplitude
                # Scale modulation 0.0-1.0 to ±30% brightness variation
                modulation_factor = 1.0 + (self._amplitude_modulation * 0.3)
                final_brightness = int(self._target_brightness * modulation_factor)
                final_brightness = max(0, min(255, final_brightness))  # Clamp to 0-255

            # Check if brightness changed (only send if different to avoid spam)
            if final_brightness != self._current_brightness:
                self.logger.info(
                    f"Control loop: Brightness changed {self._current_brightness} -> {final_brightness}"
                )
                await self._send_brightness_to_arduino(final_brightness)
                self._current_brightness = final_brightness

        except Exception as e:
            # Don't let control loop crash the service
            self.logger.error(f"Error in control loop: {e}")

    async def _send_pattern_to_arduino(self, pattern: EyePattern) -> None:
        """Send pattern command to Arduino (helper for control loop)."""
        if self.adapter:
            try:
                await self.adapter.set_pattern(pattern.value)
            except Exception as e:
                self.logger.error(f"Failed to send pattern to Arduino: {e}")

    async def _send_color_to_arduino(self, color: tuple) -> None:
        """Send color command to Arduino (helper for control loop)."""
        if self.adapter and hasattr(self.adapter, 'set_color'):
            try:
                await self.adapter.set_color(*color)
            except Exception as e:
                self.logger.error(f"Failed to send color to Arduino: {e}")

    async def _send_brightness_to_arduino(self, brightness: int) -> None:
        """Send brightness command to Arduino (helper for control loop)."""
        if self.adapter and hasattr(self.adapter, 'set_brightness'):
            try:
                await self.adapter.set_brightness(brightness)
            except Exception as e:
                self.logger.error(f"Failed to send brightness to Arduino: {e}")

    async def _auto_detect_arduino(self) -> Optional[str]:
        """
        Auto-detect the Arduino serial port.
        
        Returns:
            The detected serial port or None if not found.
        """
        self.logger.info("Auto-detecting Arduino serial port")
        await self.emit(EventTopics.DEBUG_LOG, {
            "component": "eye_light_controller",
            "level": LogLevel.INFO, 
            "message": "Attempting to auto-detect Arduino device"
        })
        
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
            "1A86:7523",  # CH340 USB-to-Serial (common in clones)
            "0403:6001",  # FTDI USB-to-Serial (common in older boards)
        ]
        
        # Get list of all serial ports
        ports = list(serial.tools.list_ports.comports())
        
        if not ports:
            self.logger.warning("No serial ports found at all")
            await self.emit(EventTopics.DEBUG_LOG, {
                "component": "eye_light_controller",
                "level": LogLevel.WARNING,
                "message": "No serial ports found on this system"
            })
            return None
            
        # Log all available ports for debugging
        self.logger.info(f"Found {len(ports)} serial ports")
        for i, port in enumerate(ports):
            port_info = f"{port.device} - {port.description}"
            if port.manufacturer:
                port_info += f" (Manufacturer: {port.manufacturer})"
            if port.vid is not None and port.pid is not None:
                port_info += f" [VID:PID {port.vid:04X}:{port.pid:04X}]"
                
            self.logger.debug(f"Port {i+1}: {port_info}")
            await self.emit(EventTopics.DEBUG_LOG, {
                "component": "eye_light_controller",
                "level": LogLevel.DEBUG,
                "message": f"Available port: {port_info}"
            })
        
        # First, try to find an Arduino by VID:PID
        for port in ports:
            # Skip ports that don't have vid/pid information
            if port.vid is None or port.pid is None:
                continue
                
            vid_pid = f"{port.vid:04X}:{port.pid:04X}"
            if vid_pid in arduino_ids:
                self.logger.info(f"Found Arduino at {port.device} with ID {vid_pid}")
                await self.emit(EventTopics.DEBUG_LOG, {
                    "component": "eye_light_controller",
                    "level": LogLevel.INFO,
                    "message": f"Found Arduino at {port.device} with ID {vid_pid}"
                })
                return port.device
                
        # If not found by VID:PID, check for common Arduino names
        arduino_names = ["arduino", "ttyACM", "ttyUSB", "usbmodem", "cu.usbmodem"]
        for port in ports:
            for name in arduino_names:
                if name.lower() in port.device.lower():
                    self.logger.info(f"Found possible Arduino at {port.device} (name match)")
                    await self.emit(EventTopics.DEBUG_LOG, {
                        "component": "eye_light_controller",
                        "level": LogLevel.INFO,
                        "message": f"Found possible Arduino at {port.device} (name match)"
                    })
                    return port.device
                    
        # Check manufacturer string if VID:PID and name don't match
        for port in ports:
            if port.manufacturer and "arduino" in port.manufacturer.lower():
                self.logger.info(f"Found Arduino at {port.device} (manufacturer match)")
                await self.emit(EventTopics.DEBUG_LOG, {
                    "component": "eye_light_controller",
                    "level": LogLevel.INFO,
                    "message": f"Found Arduino at {port.device} (manufacturer match)"
                })
                return port.device
                
        # If on macOS, look for cu.usbmodem devices as a fallback
        if platform.system() == "Darwin":
            for port in ports:
                if "cu.usbmodem" in port.device:
                    self.logger.info(f"Found possible Arduino at {port.device} (macOS usbmodem)")
                    await self.emit(EventTopics.DEBUG_LOG, {
                        "component": "eye_light_controller",
                        "level": LogLevel.INFO,
                        "message": f"Found possible Arduino at {port.device} (macOS usbmodem)"
                    })
                    return port.device
                    
        # Last resort - if there's only one port available, use that
        if len(ports) == 1:
            self.logger.info(f"Only one serial port found, using it: {ports[0].device}")
            await self.emit(EventTopics.DEBUG_LOG, {
                "component": "eye_light_controller",
                "level": LogLevel.INFO,
                "message": f"Only one serial port found, using it: {ports[0].device}"
            })
            return ports[0].device
        
        self.logger.warning("No Arduino device detected")
        await self.emit(EventTopics.DEBUG_LOG, {
            "component": "eye_light_controller",
            "level": LogLevel.WARNING,
            "message": "No Arduino device detected. If Arduino is connected, try specifying the port manually."
        })
        return None
    
    async def _connect_to_arduino(self) -> None:
        """Establish connection to the Arduino board using the SimpleEyeAdapter."""
        if not self.serial_port:
            self.logger.error("No serial port specified for Arduino connection")
            self.connected = False
            return
        
        try:
            self.logger.info(f"Connecting to Arduino at {self.serial_port}")
            
            # Create the adapter
            self.adapter = SimpleEyeAdapter(
                serial_port=self.serial_port,
                baud_rate=self.baud_rate,
                timeout=self.command_timeout,
                logger=self.logger
            )
            
            # Connect to the Arduino - this should return a boolean success value
            connection_success = await self.adapter.connect()
            self.connected = connection_success
            
            if not self.connected:
                self.logger.error(f"Failed to connect to Arduino at {self.serial_port}")
                self.adapter = None
                return
                
            # If we got here, connection was successful
            self.logger.info(f"Successfully connected to Arduino at {self.serial_port}")
            
            # Verify connection with a simple status request
            try:
                status = await self.adapter.get_status()
                self.logger.info(f"Arduino status: {status}")
                
                # If status shows not connected, but we think we are, handle the inconsistency
                if status.get("connected") is False and self.connected:
                    self.logger.warning("Status check indicates not connected but initial connection succeeded. Will continue anyway.")
                    # We'll still consider ourselves connected since the initial connection worked
            except Exception as e:
                self.logger.warning(f"Connected but could not get status: {e}")
                # We'll still consider ourselves connected since the exception was only in status check
                # and the initial connection succeeded
        
        except Exception as e:
            self.logger.error(f"Error connecting to Arduino: {e}")
            self.connected = False
            self.adapter = None
    
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
        if self.mock_mode:
            # In mock mode, just log the command and return success
            self.logger.info(f"MOCK MODE: Setting pattern to {pattern}")
            self.current_pattern = pattern
            if brightness is not None:
                self.current_brightness = brightness
            self.last_command_time = time.time()
            return True
            
        if not self.connected or not self.adapter:
            self.logger.error("Cannot set pattern: Not connected to Arduino")
            return False
            
        # Use lock to ensure only one command is sent at a time
        async with self.command_lock:
            # Set brightness if provided
            if brightness is not None:
                success = await self.adapter.set_brightness(brightness)
                if success:
                    self.current_brightness = brightness
                    self.logger.info(f"Set eye brightness to {brightness}")
                    
            # Set the pattern
            self.logger.info(f"Setting eye pattern to {pattern}")
            success = await self.adapter.set_pattern(pattern)
            
            if success:
                self.current_pattern = pattern
                self.last_command_time = time.time()
                
                # If duration is specified, schedule a reset to idle after the duration
                if duration and duration > 0:
                    asyncio.create_task(self._reset_after_duration(duration))
                    
            return success
    
    async def _reset_after_duration(self, duration: float) -> None:
        """
        Reset to idle pattern after a specified duration.
        
        Args:
            duration: Duration in seconds before resetting
        """
        await asyncio.sleep(duration)
        # Only reset if no other commands have been sent during the wait
        if (time.time() - self.last_command_time) >= duration * 0.9:
            await self.set_pattern(EyePattern.IDLE)
    
    async def set_brightness(self, brightness: float) -> bool:
        """
        Set the LED brightness.
        
        Args:
            brightness: Brightness level (0.0-1.0)
            
        Returns:
            True if successful, False otherwise
        """
        if self.mock_mode:
            self.logger.info(f"MOCK MODE: Setting brightness to {brightness}")
            self.current_brightness = max(0.0, min(1.0, brightness))
            return True
            
        if not self.connected or not self.adapter:
            self.logger.error("Cannot set brightness: Not connected to Arduino")
            return False
            
        async with self.command_lock:
            success = await self.adapter.set_brightness(brightness)
            
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
        try:
            # If it's already a properly formatted EyeCommandPayload, use it directly
            if isinstance(event_payload, EyeCommandPayload):
                pattern_name = event_payload.pattern
                color = event_payload.color
                intensity = event_payload.intensity
                duration = event_payload.duration
            
            # If it's a dict, convert it
            elif isinstance(event_payload, dict):
                pattern_name = event_payload.get("pattern", EyePattern.IDLE)
                color = event_payload.get("color")
                intensity = event_payload.get("intensity")
                duration = event_payload.get("duration")
                
            # If it has a model_dump method (BaseEventPayload), convert it
            elif hasattr(event_payload, 'model_dump'):
                payload_dict = event_payload.model_dump()
                pattern_name = payload_dict.get("pattern", EyePattern.IDLE)
                color = payload_dict.get("color")
                intensity = payload_dict.get("intensity") 
                duration = payload_dict.get("duration")
            else:
                self.logger.error(f"Invalid event payload type: {type(event_payload)}, cannot handle")
                return
                
            # Convert string pattern to enum if needed
            if isinstance(pattern_name, str):
                try:
                    pattern = EyePattern(pattern_name)
                except ValueError:
                    self.logger.error(f"Invalid eye pattern: {pattern_name}")
                    return
            else:
                pattern = pattern_name
                
            # Set the pattern
            await self.set_pattern(
                pattern=pattern,
                color=color,
                brightness=intensity,
                duration=duration
            )
                
        except Exception as e:
            self.logger.error(f"Error in eye command handler: {e}")
            # Don't raise - just log the error
    
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

        # Map sentiment to eye pattern and color
        if event_payload.label == "positive":
            pattern = EyePattern.HAPPY
            color = self.SENTIMENT_COLORS["positive"]
        elif event_payload.label == "negative":
            pattern = EyePattern.SAD
            color = self.SENTIMENT_COLORS["negative"]
        elif event_payload.label == "angry":
            pattern = EyePattern.ANGRY
            color = self.SENTIMENT_COLORS["angry"]
        elif event_payload.label == "surprised":
            pattern = EyePattern.SURPRISED
            color = self.SENTIMENT_COLORS["surprised"]
        else:
            # Default to speaking pattern if sentiment is neutral or unknown
            return

        # Only change the pattern if we're not in a critical state (like listening or processing)
        if self._target_pattern not in [EyePattern.LISTENING, EyePattern.THINKING]:
            # Set target state (control loop will execute)
            self._target_color = color
            self._target_pattern = pattern
            self.logger.info(f"Sentiment detected: {event_payload.label} -> pattern={pattern}, color={color}")

            # TODO: Add timer to return to previous pattern after 2 seconds
            # For now, sentiment changes persist until next event
    
    async def _handle_speech_started(self, event_payload: BaseEventPayload) -> None:
        """
        Handle speech synthesis/generation started events.

        Args:
            event_payload: The event payload.
        """
        # Only change pattern if in interactive mode
        if self._is_in_interactive_mode():
            self.logger.info("Speech started, setting target pattern to SPEAKING")
            self._target_pattern = EyePattern.SPEAKING
    
    async def _handle_speech_ended(self, event_payload: BaseEventPayload) -> None:
        """
        Handle speech synthesis/generation ended events.

        Args:
            event_payload: The event payload.
        """
        # Only change pattern if in interactive mode
        if self._is_in_interactive_mode():
            self.logger.info("Speech ended, triggering green confirmation flash")
            # Reset amplitude modulation
            self._amplitude_modulation = 0.0
            # Explicitly close mouth when speech ends
            if self.adapter:
                await self.adapter.set_mouth_amplitude(0)
                self.logger.debug("Sent M000 to close mouth after speech")
            # Trigger flash pattern (will auto-return to IDLE in Arduino)
            self._target_pattern = EyePattern.FLASH

    async def _handle_amplitude(self, event_payload: dict) -> None:
        """
        Handle real-time audio amplitude events during speech synthesis.

        Updates amplitude modulation for pupil brightness pulsing AND mouth opening.
        Uses exponential moving average (EMA) for smooth transitions.

        Args:
            event_payload: Dict containing amplitude (0.0-1.0) and timestamp_offset
        """
        try:
            from cantina_os.core.event_payloads import SpeechAmplitudePayload

            # Validate payload
            payload = SpeechAmplitudePayload.model_validate(event_payload)

            # Only apply amplitude modulation during SPEAKING pattern
            if self._target_pattern == EyePattern.SPEAKING:
                # Apply exponential moving average for smoothing
                # Alpha = 0.3 means 30% new value, 70% old value (smooth but responsive)
                alpha = 0.3
                new_amplitude = payload.amplitude
                self._amplitude_modulation = (
                    alpha * new_amplitude + (1 - alpha) * self._amplitude_modulation
                )

                # Send mouth amplitude command (throttled to 20Hz to avoid Arduino spam)
                now = time.time()
                if now - self._last_mouth_command_time >= self.MOUTH_UPDATE_INTERVAL:
                    # Scale amplitude (0.0-1.0) to mouth level (0-255)
                    mouth_level = int(self._amplitude_modulation * 255)
                    if self.adapter:
                        await self.adapter.set_mouth_amplitude(mouth_level)
                    self._last_mouth_command_time = now

                    # Log occasionally for debugging (every 10th event to avoid spam)
                    if not hasattr(self, '_amplitude_log_counter'):
                        self._amplitude_log_counter = 0
                    self._amplitude_log_counter += 1
                    if self._amplitude_log_counter % 10 == 0:
                        self.logger.debug(
                            f"Amplitude: {self._amplitude_modulation:.3f}, Mouth: {mouth_level}"
                        )

        except Exception as e:
            # Don't crash the service if amplitude handling fails
            self.logger.error(f"Error handling amplitude event: {e}")


    async def _handle_voice_listening_started(self, event_payload: BaseEventPayload) -> None:
        """Handle voice listening started events (text recording mode)."""
        # Only change pattern if in interactive mode
        if self._is_in_interactive_mode():
            self.logger.info("Voice recording started, setting target pattern to LISTENING")
            self._target_pattern = EyePattern.LISTENING

    async def _handle_voice_listening_ended(self, event_payload: BaseEventPayload) -> None:
        """Handle voice listening ended events (text recording mode)."""
        # Only change pattern if in interactive mode
        if self._is_in_interactive_mode():
            self.logger.info("Voice recording ended, setting target pattern to THINKING")
            self._target_pattern = EyePattern.THINKING

    async def _handle_voice_processing_started(self, event_payload: BaseEventPayload) -> None:
        """Handle voice processing started events (text recording mode)."""
        # Only change pattern if in interactive mode
        if self._is_in_interactive_mode():
            self.logger.info("Voice processing started, setting target pattern to THINKING")
            self._target_pattern = EyePattern.THINKING

    async def _handle_mouse_recording_stopped(self, event_payload: BaseEventPayload) -> None:
        """
        Handle mouse recording stopped events - triggers immediately when mouse is clicked
        to stop recording, before transcript processing completes.

        Args:
            event_payload: The event payload.
        """
        # Only change pattern if in interactive mode
        if self._is_in_interactive_mode():
            self.logger.info("Mouse recording stopped, setting target pattern to THINKING")
            self._target_pattern = EyePattern.THINKING
    
    @compound_command("eye test")
    @command_error_handler
    async def handle_eye_test(self, payload: dict) -> None:
        """Handle 'eye test' command - runs test sequence through all patterns."""
        self.logger.info("Running eye test sequence")
        await self._run_test_sequence()

    @compound_command("eye status")
    @command_error_handler
    async def handle_eye_status(self, payload: dict) -> None:
        """Handle 'eye status' command - returns current eye status."""
        self.logger.info("Getting eye status")
        await self._get_status()

    @compound_command("eye pattern")
    @validate_compound_command(min_args=1, max_args=1, required_args=["pattern_name"])
    @command_error_handler
    async def handle_eye_pattern(self, payload: dict) -> None:
        """Handle 'eye pattern <pattern_name>' command - sets eye pattern."""
        args = payload.get("args", [])
        pattern_name = args[0].lower()
        self.logger.info(f"Setting eye pattern to: {pattern_name}")
        await self._set_pattern(pattern_name)

    async def _handle_cli_command(self, payload: dict) -> None:
        """
        Legacy CLI command handler - dispatches to appropriate methods.
        """
        self.logger.debug(f"Legacy CLI handler received: {payload}")
        
        # Handle CLI command payloads - dispatch to decorated methods
        if isinstance(payload, dict) and "command" in payload:
            command = payload.get("command", "")
            subcommand = payload.get("subcommand", "")
            
            # Create command pattern for matching
            if subcommand:
                command_pattern = f"{command} {subcommand}"
            else:
                command_pattern = command
            
            self.logger.debug(f"Dispatching CLI command: {command_pattern}")
            
            # Dispatch to appropriate decorated method
            if command_pattern == "eye test":
                await self.handle_eye_test(payload)
            elif command_pattern == "eye status":
                await self.handle_eye_status(payload)
            elif command_pattern == "eye pattern":
                await self.handle_eye_pattern(payload)
            else:
                self.logger.warning(f"Unknown eye command pattern: {command_pattern}")
                await self._send_error(f"Unknown eye command: {command_pattern}")
            return
        
        self.logger.warning(f"Unhandled CLI command payload format: {payload}")

    async def _set_pattern(self, pattern_name: str) -> None:
        """Set the eye pattern via CLI command."""
        try:
            # Validate pattern name
            if pattern_name.lower() not in [p.value for p in EyePattern]:
                valid_patterns_str = ", ".join([p.value for p in EyePattern])
                await self._send_error(f"Invalid pattern: {pattern_name}. Valid patterns are: {valid_patterns_str}")
                return

            eye_pattern_enum = EyePattern(pattern_name.lower())
            
            # Use the main set_pattern method which handles self.adapter
            success = await self.set_pattern(pattern=eye_pattern_enum)
            
            if success:
                await self._send_success(f"Set eye pattern to: {pattern_name}")
            else:
                await self._send_error(f"Failed to set eye pattern to: {pattern_name}")
            
        except ValueError:
            valid_patterns_str = ", ".join([p.value for p in EyePattern])
            await self._send_error(f"Invalid pattern enum value: {pattern_name}. Valid patterns are: {valid_patterns_str}")
        except Exception as e:
            self.logger.error(f"Error in _set_pattern (CLI): {e}", exc_info=True)
            await self._send_error(f"Error setting pattern: {str(e)}")

    async def _run_test_sequence(self) -> None:
        """Run a test sequence through all eye patterns."""
        if self.mock_mode:
            await self._send_success("Running test sequence (MOCK MODE)")
            return

        if not self.connected or not self.adapter:
            await self._send_error("Cannot run test sequence: Not connected to Arduino.")
            return
            
        try:
            # Store original pattern to restore later
            original_pattern = self.current_pattern or EyePattern.IDLE
            
            # Test sequence of patterns
            test_patterns = [
                (EyePattern.IDLE, "IDLE"),
                (EyePattern.LISTENING, "LISTENING"),
                (EyePattern.THINKING, "THINKING"),
                (EyePattern.SPEAKING, "SPEAKING"),
                (EyePattern.HAPPY, "HAPPY"),
                (EyePattern.SAD, "SAD"),
                (EyePattern.ANGRY, "ANGRY"),
                (EyePattern.SURPRISED, "SURPRISED")
            ]
            
            # Start the test
            await self._send_success("Starting eye pattern test sequence...")
            
            for pattern, name in test_patterns:
                self.logger.info(f"Testing pattern: {name}")
                success = await self.set_pattern(pattern)
                if success:
                    await self._send_success(f"Set pattern: {name}")
                else:
                    await self._send_error(f"Failed to set pattern: {name}")
                await asyncio.sleep(1.0)  # Show each pattern for 1 second
            
            # Restore original pattern
            await self.set_pattern(original_pattern)
            await self._send_success("Test sequence complete. Restored original pattern.")
            
        except Exception as e:
            self.logger.error(f"Error running test sequence: {e}", exc_info=True)
            await self._send_error(f"Error during test sequence: {str(e)}")
            # Try to restore to IDLE pattern
            await self.set_pattern(EyePattern.IDLE)

    async def _get_status(self) -> None:
        """Get the current status of the eye lights via CLI command."""
        if self.mock_mode:
            status_info = {
                "mock_mode": True,
                "current_pattern": self.current_pattern.value if self.current_pattern else "N/A",
                "current_brightness": self.current_brightness,
                "connected": self.connected
            }
            await self._send_success(f"Eye status (MOCK MODE): {status_info}")
            self.logger.info(f"MOCK MODE: Status requested: {status_info}")
            return

        if not self.connected or not self.adapter:
            await self._send_error("Cannot get status: Not connected to Arduino.")
            return
            
        try:
            async with self.command_lock:
                # Directly use the adapter's get_status method
                status_response = await self.adapter.get_status()
            
            if status_response:
                # Add more info if available
                current_status_info = {
                    "arduino_reported_status": status_response,
                    "service_current_pattern": self.current_pattern.value if self.current_pattern else "N/A",
                    "service_current_brightness": self.current_brightness,
                    "service_connected_state": self.connected
                }
                await self._send_success(f"Eye status: {current_status_info}")
            else:
                await self._send_error("Failed to get status from Arduino adapter.")
                
        except Exception as e:
            self.logger.error(f"Error getting Arduino status (CLI): {e}", exc_info=True)
            await self._send_error(f"Error getting status: {str(e)}")

    async def _send_success(self, message: str) -> None:
        """Send a success response"""
        await self.emit(
            EventTopics.CLI_RESPONSE,
            {
                "message": message,
                "is_error": False
            }
        )

    async def _send_error(self, message: str) -> None:
        """Send an error response"""
        await self.emit(
            EventTopics.CLI_RESPONSE,
            {
                "message": message,
                "is_error": True
            }
        )

    @property
    def _valid_patterns(self) -> List[str]:
        """List of valid eye patterns"""
        return ["happy", "sad", "angry", "neutral", "excited", "sleepy"]

    async def _handle_mode_change(self, event_payload: Union[BaseEventPayload, SystemModeChangePayload]) -> None:
        """
        Handle system mode change events.

        Args:
            event_payload: The event payload containing mode information.
        """
        try:
            # Extract the new mode from the payload
            if isinstance(event_payload, dict):
                new_mode = event_payload.get("new_mode", "").upper()
            elif hasattr(event_payload, "new_mode"):
                new_mode = event_payload.new_mode.upper()
            else:
                self.logger.warning(f"Invalid mode change payload format: {type(event_payload)}")
                return

            if not new_mode:
                self.logger.warning(f"Invalid mode change payload: {event_payload}")
                return

            # Store the current system mode
            self._current_system_mode = new_mode

            self.logger.info(f"System mode changed to: {new_mode}, updating eye pattern and color")

            # Map system modes to eye patterns and colors (set target state)
            if new_mode == "IDLE":
                self._target_color = self.MODE_COLORS["IDLE"]
                self._target_pattern = EyePattern.IDLE
                self._target_brightness = 128
                self.logger.info(f"Mode IDLE: target color={self._target_color}, pattern={self._target_pattern}")

            elif new_mode == "AMBIENT":
                self._target_color = self.MODE_COLORS["AMBIENT"]
                self._target_pattern = EyePattern.ENGAGED
                self._target_brightness = 128
                self.logger.info(f"Mode AMBIENT: target color={self._target_color}, pattern={self._target_pattern}")

            elif new_mode == "INTERACTIVE":
                self._target_color = self.MODE_COLORS["INTERACTIVE"]
                self._target_pattern = EyePattern.ENGAGED
                self._target_brightness = 128
                self.logger.info(f"Mode INTERACTIVE: target color={self._target_color}, pattern={self._target_pattern}")

            elif new_mode == "SLEEPING":
                self._target_color = self.MODE_COLORS["SLEEPING"]
                self._target_pattern = EyePattern.IDLE
                self._target_brightness = int(255 * 0.3)  # 30% brightness
                self.logger.info(f"Mode SLEEPING: target color={self._target_color}, pattern={self._target_pattern}, brightness={self._target_brightness}")

            else:
                # Unknown mode, use idle defaults
                self.logger.warning(f"Unknown mode: {new_mode}, using default IDLE")
                self._target_color = self.MODE_COLORS["IDLE"]
                self._target_pattern = EyePattern.IDLE
                self._target_brightness = 128

        except Exception as e:
            self.logger.error(f"Error handling mode change: {e}")
            # Fallback to idle defaults
            self._target_color = self.MODE_COLORS["IDLE"]
            self._target_pattern = EyePattern.IDLE
            self._target_brightness = 128
            
    def _is_in_interactive_mode(self) -> bool:
        """Check if we're in interactive voice mode."""
        return self._current_system_mode == "INTERACTIVE"

    async def _handle_llm_response_chunk(self, event_payload: BaseEventPayload) -> None:
        """
        Handle LLM response chunks to switch to speaking mode as soon as 
        the model starts generating text, before audio begins playing.
        
        Args:
            event_payload: The event payload with response chunk.
        """
        # Only change pattern if in interactive mode and we're currently in THINKING mode
        if self._is_in_interactive_mode() and self.current_pattern == EyePattern.THINKING:
            self.logger.info("LLM response started, immediately setting eye pattern to SPEAKING")
            await self.set_pattern(EyePattern.SPEAKING) 