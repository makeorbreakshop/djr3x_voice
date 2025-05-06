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

from src.bus import EventBus, EventTypes
from config.app_settings import LED_SERIAL_PORT, LED_BAUD_RATE, DISABLE_EYES

# Configure logging
logger = logging.getLogger(__name__)

class AnimationPattern(Enum):
    """LED animation patterns."""
    IDLE = auto()
    LISTENING = auto()
    PROCESSING = auto()
    SPEAKING = auto()
    STARTUP = auto()
    SHUTDOWN = auto()

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
        
        # Subscribe to events
        self._subscribe_to_events()
    
    def _subscribe_to_events(self) -> None:
        """Subscribe to relevant events on the bus."""
        self.event_bus.on(EventTypes.SYSTEM_READY, self._handle_system_ready)
        self.event_bus.on(EventTypes.SYSTEM_SHUTDOWN, self._handle_system_shutdown)
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
            self.serial = serial.Serial(
                port=self.port,
                baudrate=self.baud_rate,
                timeout=1
            )
            
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
                "pattern": pattern.name.lower(),
                **kwargs
            }
            
            # Send to hardware
            await self._send_command(command)
            
            # Emit event
            await self.event_bus.emit(
                EventTypes.LED_ANIMATION_STARTED,
                {"name": pattern.name.lower()}
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
                "pattern": "mouth",
                "level": level,
                "emotion": self.current_emotion
            })
            
        except Exception as e:
            logger.error(f"Error updating mouth level: {e}")
    
    async def _send_command(self, command: Dict[str, Any]) -> None:
        """Send a command to the LED hardware.
        
        Args:
            command: Command dictionary to send
        """
        if not self.serial or not self.serial.is_open:
            return
            
        try:
            # Convert to JSON and add newline
            command_str = json.dumps(command) + "\n"
            
            # Send over serial
            self.serial.write(command_str.encode())
            self.serial.flush()
            
        except Exception as e:
            logger.error(f"Error sending command: {e}")
            
            # Try to reconnect
            try:
                if self.serial:
                    self.serial.close()
                self.serial = serial.Serial(
                    port=self.port,
                    baudrate=self.baud_rate,
                    timeout=1
                )
            except Exception as e2:
                logger.error(f"Failed to reconnect to LED hardware: {e2}")
    
    # Event handlers
    
    async def _handle_system_ready(self, data: Dict[str, Any]) -> None:
        """Handle system.ready event."""
        await self.show_pattern(AnimationPattern.IDLE)
    
    async def _handle_system_shutdown(self, data: Dict[str, Any]) -> None:
        """Handle system.shutdown event."""
        await self.show_pattern(AnimationPattern.SHUTDOWN)
    
    async def _handle_listening_started(self, data: Dict[str, Any]) -> None:
        """Handle voice.listening_started event."""
        await self.show_pattern(AnimationPattern.LISTENING)
    
    async def _handle_listening_stopped(self, data: Dict[str, Any]) -> None:
        """Handle voice.listening_stopped event."""
        await self.show_pattern(AnimationPattern.PROCESSING)
    
    async def _handle_processing_started(self, data: Dict[str, Any]) -> None:
        """Handle voice.processing_started event."""
        await self.show_pattern(AnimationPattern.PROCESSING)
    
    async def _handle_speaking_started(self, data: Dict[str, Any]) -> None:
        """Handle voice.speaking_started event."""
        self.current_emotion = data.get("emotion", "neutral")
        await self.show_pattern(
            AnimationPattern.SPEAKING,
            emotion=self.current_emotion
        )
    
    async def _handle_voice_beat(self, data: Dict[str, Any]) -> None:
        """Handle voice.beat event."""
        await self.update_mouth_level(data.get("level", 0))
    
    async def _handle_speaking_finished(self, data: Dict[str, Any]) -> None:
        """Handle voice.speaking_finished event."""
        await self.show_pattern(AnimationPattern.IDLE)
        await self.event_bus.emit(
            EventTypes.LED_ANIMATION_STOPPED,
            {"name": "speaking"}
        ) 