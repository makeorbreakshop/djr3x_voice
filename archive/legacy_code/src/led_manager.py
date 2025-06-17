"""
LED Manager for DJ R3X.
Handles LED animations and synchronization with voice/music.
"""

import os
import asyncio
import logging
import serial
import json
from typing import Optional, Dict, Any
from enum import Enum, auto

from src.bus import EventBus, EventTypes, SystemMode
from config.app_settings import LED_SERIAL_PORT, LED_BAUD_RATE, DISABLE_EYES

# Configure logging
logger = logging.getLogger(__name__)

class AnimationPattern(Enum):
    """LED animation patterns."""
    IDLE = "idle"
    AMBIENT = "ambient"
    LISTENING = "listening"
    PROCESSING = "processing"
    SPEAKING = "speaking"
    STARTUP = "startup"
    SHUTDOWN = "shutdown"
    ERROR = "error"

class LEDManager:
    """Manages LED animations and hardware communication."""
    
    def __init__(self, event_bus: EventBus, disable_eyes: bool = DISABLE_EYES):
        """Initialize the LED manager.
        
        Args:
            event_bus: Event bus instance
            disable_eyes: Skip hardware communication if True
        """
        self.event_bus = event_bus
        self.disable_eyes = disable_eyes
        
        # Serial connection to LED controller
        self.serial: Optional[serial.Serial] = None
        self.port = LED_SERIAL_PORT
        self.baud_rate = LED_BAUD_RATE
        
        # Animation state
        self.current_pattern = AnimationPattern.IDLE
        self.current_emotion = "neutral"
        self.current_level = 0
        self.animation_task: Optional[asyncio.Task] = None
        
        # System state
        self.current_system_mode = SystemMode.STARTUP
        
        # Subscribe to events
        self._subscribe_to_events()
    
    def _subscribe_to_events(self) -> None:
        """Subscribe to relevant events on the bus."""
        self.event_bus.on(EventTypes.SYSTEM_READY, self._handle_system_ready)
        self.event_bus.on(EventTypes.SYSTEM_SHUTDOWN, self._handle_system_shutdown)
        self.event_bus.on(EventTypes.SYSTEM_MODE_CHANGED, self._handle_system_mode_changed)
        self.event_bus.on(EventTypes.VOICE_LISTENING_STARTED, self._handle_listening_started)
        self.event_bus.on(EventTypes.VOICE_LISTENING_STOPPED, self._handle_listening_stopped)
        self.event_bus.on(EventTypes.VOICE_PROCESSING_STARTED, self._handle_processing_started)
        self.event_bus.on(EventTypes.VOICE_SPEAKING_STARTED, self._handle_speaking_started)
        self.event_bus.on(EventTypes.VOICE_BEAT, self._handle_voice_beat)
        self.event_bus.on(EventTypes.VOICE_SPEAKING_FINISHED, self._handle_speaking_finished)
    
    async def start(self) -> bool:
        """Start the LED manager and initialize hardware connection.
        
        Returns:
            bool: True if started successfully
        """
        if self.disable_eyes:
            logger.info("LED Manager running in disabled mode")
            return True
            
        try:
            # Initialize serial connection
            loop = asyncio.get_running_loop()
            try:
                self.serial = serial.Serial(
                    port=self.port,
                    baudrate=self.baud_rate,
                    timeout=1
                )
                logger.info(f"Serial connection established on port {self.port}")
            except Exception as serial_ex:
                logger.error(f"Failed to connect to serial port: {serial_ex}")
                return False
            
            # Wait for ready signal with proper timeout
            ready_timeout = 30  # 30 seconds total timeout
            ready_count = 0
            ready_found = False
            
            while not ready_found and ready_count < ready_timeout * 10:  # 10 checks per second
                # Use run_in_executor for the blocking in_waiting check
                has_data = await loop.run_in_executor(None, lambda: self.serial.in_waiting > 0)
                
                if has_data:
                    # Use run_in_executor for the blocking readline
                    response_bytes = await loop.run_in_executor(None, self.serial.readline)
                    response = response_bytes.decode().strip()
                    logger.debug(f"Startup response: {response}")
                    
                    # Check for JSON ready message
                    try:
                        data = json.loads(response)
                        if "status" in data and data["status"] == "ready":
                            logger.info("Arduino ready signal received")
                            ready_found = True
                            break
                    except json.JSONDecodeError:
                        # Also check for legacy plain text READY message
                        if response == "READY":
                            logger.info("Arduino ready signal received (plain text)")
                            ready_found = True
                            break
                
                ready_count += 1            
                await asyncio.sleep(0.1)
                
            if not ready_found:
                logger.error(f"Timed out waiting for Arduino ready signal after {ready_timeout} seconds")
                return False
            
            # Send startup pattern
            await self.show_pattern(AnimationPattern.STARTUP)
            
            logger.info("LED Manager started successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start LED Manager: {e}")
            return False
    
    async def stop(self) -> None:
        """Stop the LED manager and clean up resources."""
        try:
            # Show shutdown pattern
            if not self.disable_eyes:
                await self.show_pattern(AnimationPattern.SHUTDOWN)
            
            # Cancel any running animation
            if self.animation_task and not self.animation_task.done():
                self.animation_task.cancel()
                try:
                    await self.animation_task
                except asyncio.CancelledError:
                    pass
            
            # Close serial connection
            if self.serial and self.serial.is_open:
                self.serial.close()
            
            logger.info("LED Manager stopped")
            
        except Exception as e:
            logger.error(f"Error stopping LED Manager: {e}")
    
    async def show_pattern(self, pattern: AnimationPattern, **kwargs) -> None:
        """Show an LED animation pattern.
        
        Args:
            pattern: Animation pattern to show
            **kwargs: Additional parameters for the pattern
        """
        if self.disable_eyes:
            return
            
        try:
            # Cancel current animation if running
            if self.animation_task and not self.animation_task.done():
                self.animation_task.cancel()
                try:
                    await self.animation_task
                except asyncio.CancelledError:
                    pass
            
            # Update state
            self.current_pattern = pattern
            
            # Create command
            command = {
                "pattern": pattern.value,
                "emotion": kwargs.get("emotion", self.current_emotion)
            }
            
            # Send to hardware
            await self._send_command(command)
            
            # Emit event
            await self.event_bus.emit(
                EventTypes.LED_ANIMATION_STARTED,
                {"name": pattern.value}
            )
            
        except Exception as e:
            logger.error(f"Error showing pattern {pattern}: {e}")
    
    async def update_mouth_level(self, level: int) -> None:
        """Update mouth LED level for voice synchronization.
        
        Args:
            level: LED intensity (0-255)
        """
        if self.disable_eyes or self.current_pattern != AnimationPattern.SPEAKING:
            return
            
        try:
            # Update state
            self.current_level = level
            
            # Send update command
            await self._send_command({
                "pattern": self.current_pattern.value,
                "emotion": self.current_emotion,
                "level": level
            })
            
        except Exception as e:
            logger.error(f"Error updating mouth level: {e}")
    
    async def _send_command(self, command: Dict[str, Any]) -> None:
        """Send a command to the LED hardware.
        
        Args:
            command: Command dictionary to send
        """
        if not self.serial or not self.serial.is_open:
            logger.warning("Cannot send command: serial connection is not open")
            return
            
        try:
            # Get the current event loop
            loop = asyncio.get_running_loop()
            
            # Convert to JSON and add newline
            command_str = json.dumps(command) + "\n"
            
            # Debug log the command being sent
            logger.debug(f"Sending command: {command_str.strip()}")
            
            # Use run_in_executor for blocking serial write and flush
            await loop.run_in_executor(None, lambda: self.serial.write(command_str.encode()))
            await loop.run_in_executor(None, self.serial.flush)
            
            logger.debug(f"Command sent, waiting for acknowledgment: {command['pattern']}")
            
            # Wait for acknowledgment with proper timeout
            found_ack = False
            command_pattern = command.get("pattern", "unknown")
            
            # Use asyncio.wait_for with a timeout for the acknowledgment loop
            try:
                await asyncio.wait_for(
                    self._wait_for_ack(command_pattern, loop),
                    timeout=2.0  # 2 second timeout
                )
                found_ack = True
            except asyncio.TimeoutError:
                logger.warning(f"Timed out waiting for acknowledgment for pattern: {command_pattern}")
                
                # Retry once on timeout
                logger.info(f"Retrying command for pattern: {command_pattern}")
                await loop.run_in_executor(None, lambda: self.serial.write(command_str.encode()))
                await loop.run_in_executor(None, self.serial.flush)
                
                try:
                    await asyncio.wait_for(
                        self._wait_for_ack(command_pattern, loop),
                        timeout=1.0  # Shorter timeout for retry
                    )
                    found_ack = True
                    logger.info(f"Command acknowledged on retry: {command_pattern}")
                except asyncio.TimeoutError:
                    logger.error(f"Final timeout waiting for acknowledgment: {command_pattern}")
                    # If we still don't get an ack, we'll have to proceed anyway
                    # The next command might resolve the situation
            
            if not found_ack:
                # Even if we didn't get an ack, emit event that we tried to start the animation
                # This keeps the Python side state machine moving forward
                await self.event_bus.emit(
                    EventTypes.LED_ANIMATION_ATTEMPTED,
                    {"name": command_pattern, "success": False}
                )
            
        except Exception as e:
            logger.error(f"Error sending command: {e}")
            
            # Try to reconnect on error, using run_in_executor for blocking operations
            try:
                if self.serial:
                    await asyncio.get_running_loop().run_in_executor(None, lambda: self.serial.close() if self.serial.is_open else None)
                    
                await asyncio.sleep(0.5)  # Brief pause before reconnecting
                
                # Recreate serial connection
                self.serial = await asyncio.get_running_loop().run_in_executor(
                    None,
                    lambda: serial.Serial(port=self.port, baudrate=self.baud_rate, timeout=1)
                )
                logger.info(f"Reconnected to serial port {self.port}")
            except Exception as e2:
                logger.error(f"Failed to reconnect to LED hardware: {e2}")
    
    async def _wait_for_ack(self, pattern: str, loop: asyncio.AbstractEventLoop) -> None:
        """Wait for acknowledgment from Arduino for a specific pattern.
        
        Args:
            pattern: The pattern name to wait for acknowledgment
            loop: Current event loop for executing blocking calls
        
        Raises:
            asyncio.TimeoutError: Raised by caller if this takes too long
        """
        while True:
            # Check if there's data available, using run_in_executor for the blocking check
            has_data = await loop.run_in_executor(None, lambda: self.serial.in_waiting > 0)
            
            if has_data:
                # Read the data using run_in_executor for the blocking read
                response_bytes = await loop.run_in_executor(None, self.serial.readline)
                response = response_bytes.decode().strip()
                
                if not response:
                    await asyncio.sleep(0.1)
                    continue
                    
                logger.debug(f"Received response: {response}")
                
                try:
                    data = json.loads(response)
                    
                    # Check if this is the actual acknowledgment
                    if "ack" in data and data["ack"] == pattern:
                        logger.debug(f"Found acknowledgment for pattern: {pattern}")
                        return  # Success!
                        
                    # Other responses are for debugging - just log them at debug level
                    if "received" in data or "parsed" in data or "status" in data:
                        logger.debug(f"Debug response from Arduino: {response}")
                    else:
                        logger.warning(f"Unexpected response format: {response}")
                        
                except json.JSONDecodeError:
                    logger.warning(f"Invalid JSON response: {response}")
            
            # Brief sleep to avoid tight-looping
            await asyncio.sleep(0.05)
    
    # Event handlers
    
    async def _handle_system_ready(self, data: Dict[str, Any]) -> None:
        """Handle system ready events."""
        await self.show_pattern(AnimationPattern.STARTUP)
    
    async def _handle_system_shutdown(self, data: Dict[str, Any]) -> None:
        """Handle system shutdown events."""
        await self.show_pattern(AnimationPattern.SHUTDOWN)
    
    async def _handle_system_mode_changed(self, data: Dict[str, Any]) -> None:
        """Handle system mode change events.
        
        Args:
            data: Event data containing old_mode and new_mode
        """
        if "new_mode" not in data:
            return
            
        new_mode = data["new_mode"]
        logger.info(f"LED Manager: System mode changed to {new_mode}")
        self.current_system_mode = SystemMode(new_mode)
        
        # Show appropriate animation pattern based on system mode
        if new_mode == SystemMode.STARTUP.value:
            await self.show_pattern(AnimationPattern.STARTUP)
            logger.info("LED Manager: Running startup animation")
        elif new_mode == SystemMode.IDLE.value:
            # In idle mode, show idle animations (basic idle pattern)
            await self.show_pattern(AnimationPattern.IDLE)
            logger.info("LED Manager: Running idle animation")
        elif new_mode == SystemMode.AMBIENT.value:
            # In ambient mode, use AMBIENT pattern if available
            try:
                await self.show_pattern(AnimationPattern.AMBIENT)
                logger.info("LED Manager: Running ambient animations")
            except Exception as e:
                logger.warning(f"Failed to show AMBIENT pattern, falling back to IDLE: {e}")
                await self.show_pattern(AnimationPattern.IDLE)
        elif new_mode == SystemMode.INTERACTIVE.value:
            # When entering interactive mode, start with idle pattern
            # Voice manager events will trigger appropriate LED animations
            await self.show_pattern(AnimationPattern.IDLE)
            logger.info("LED Manager: Ready for interactive mode")
    
    async def _handle_listening_started(self, data: Dict[str, Any]) -> None:
        """Handle voice listening started events."""
        # Only respond in interactive mode
        if self.current_system_mode == SystemMode.INTERACTIVE:
            await self.show_pattern(AnimationPattern.LISTENING)
    
    async def _handle_listening_stopped(self, data: Dict[str, Any]) -> None:
        """Handle voice listening stopped events."""
        # Only respond in interactive mode
        if self.current_system_mode == SystemMode.INTERACTIVE:
            await self.show_pattern(AnimationPattern.IDLE)
    
    async def _handle_processing_started(self, data: Dict[str, Any]) -> None:
        """Handle voice processing started events."""
        # Only respond in interactive mode
        if self.current_system_mode == SystemMode.INTERACTIVE:
            await self.show_pattern(AnimationPattern.PROCESSING)
    
    async def _handle_speaking_started(self, data: Dict[str, Any]) -> None:
        """Handle voice speaking started events."""
        # Only respond in interactive mode
        if self.current_system_mode == SystemMode.INTERACTIVE:
            # Extract emotion if available
            emotion = data.get("emotion", "neutral") if data else "neutral"
            await self.show_pattern(AnimationPattern.SPEAKING, emotion=emotion)
    
    async def _handle_voice_beat(self, data: Dict[str, Any]) -> None:
        """Handle voice beat events for mouth synchronization."""
        # Only respond in interactive mode
        if self.current_system_mode == SystemMode.INTERACTIVE and "level" in data:
            await self.update_mouth_level(data["level"])
    
    async def _handle_speaking_finished(self, data: Dict[str, Any]) -> None:
        """Handle voice speaking finished events."""
        # Only respond in interactive mode
        if self.current_system_mode == SystemMode.INTERACTIVE:
            await self.show_pattern(AnimationPattern.IDLE)
            
    # System ready/shutdown handlers don't need to check system mode
    async def _handle_system_ready(self, data: Dict[str, Any]) -> None:
        """Handle system ready events."""
        await self.show_pattern(AnimationPattern.STARTUP)
    
    async def _handle_system_shutdown(self, data: Dict[str, Any]) -> None:
        """Handle system shutdown events."""
        await self.show_pattern(AnimationPattern.SHUTDOWN) 