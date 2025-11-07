"""
DJ R3X Eyes Integration

This module handles the integration between the voice system and Arduino-controlled LED eyes.
It manages state synchronization and provides a clean interface for the main application.
"""

import asyncio
import logging
from enum import Enum
from typing import Optional
from eyes_manager import EyesManager, EyeState

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class VoiceState(Enum):
    """Voice system states that map to eye states."""
    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    SPEAKING = "speaking"
    ERROR = "error"

class EyesIntegration:
    """Handles integration between voice system and LED eyes."""
    
    def __init__(self, port: str = '/dev/tty.usbmodem833301', baudrate: int = 115200):
        """Initialize the eyes integration.
        
        Args:
            port: Serial port for Arduino communication
            baudrate: Serial communication speed
        """
        self.eyes_manager = EyesManager(port=port, baudrate=baudrate)
        self._current_voice_state: Optional[VoiceState] = None
        self._speaking_pattern = 0  # Track current speaking pattern
        
    async def connect(self) -> bool:
        """Connect to the Arduino eyes."""
        try:
            success = await self.eyes_manager.connect()
            if success:
                logger.info("Successfully connected to Arduino eyes")
                # Set initial state
                await self.set_voice_state(VoiceState.IDLE)
            return success
        except Exception as e:
            logger.error(f"Failed to connect to Arduino eyes: {e}")
            return False
            
    async def disconnect(self):
        """Disconnect from the Arduino eyes."""
        await self.eyes_manager.disconnect()
        
    async def set_voice_state(self, state: VoiceState) -> bool:
        """Set the voice state and update eye animations accordingly.
        
        Args:
            state: New voice state to set
            
        Returns:
            bool: True if state was set successfully
        """
        if state == self._current_voice_state:
            return True
            
        logger.debug(f"Setting voice state to: {state.value}")
        self._current_voice_state = state
        
        # Map voice state to eye state
        eye_state = self._map_voice_to_eye_state(state)
        if not eye_state:
            return False
            
        # Update eye state
        return await self.eyes_manager.set_state(eye_state)
        
    def _map_voice_to_eye_state(self, voice_state: VoiceState) -> Optional[EyeState]:
        """Map voice system state to eye state.
        
        Args:
            voice_state: Current voice system state
            
        Returns:
            Optional[EyeState]: Corresponding eye state or None if mapping fails
        """
        state_map = {
            VoiceState.IDLE: EyeState.IDLE,
            VoiceState.LISTENING: EyeState.LISTENING,
            VoiceState.PROCESSING: EyeState.PROCESSING,
            VoiceState.SPEAKING: EyeState.SPEAKING,
            VoiceState.ERROR: EyeState.ERROR
        }
        return state_map.get(voice_state)
        
    async def start_speaking(self, pattern: Optional[int] = None) -> bool:
        """Start speaking animation with optional pattern selection.
        
        Args:
            pattern: Speaking animation pattern (0-3, None for default)
            
        Returns:
            bool: True if animation started successfully
        """
        if pattern is not None:
            self._speaking_pattern = pattern
            return await self.eyes_manager.send_command(f"SPEAKING:{pattern}")
        return await self.set_voice_state(VoiceState.SPEAKING)
        
    async def handle_error(self, error_msg: str) -> bool:
        """Handle error state and log the error.
        
        Args:
            error_msg: Error message to log
            
        Returns:
            bool: True if error state was set successfully
        """
        logger.error(f"Voice system error: {error_msg}")
        return await self.set_voice_state(VoiceState.ERROR)
        
    @property
    def is_ready(self) -> bool:
        """Check if eyes are ready for commands."""
        return self.eyes_manager.is_ready
        
    async def __aenter__(self):
        """Async context manager entry."""
        await self.connect()
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.disconnect()

# Example usage:
async def example_usage():
    async with EyesIntegration() as eyes:
        if not eyes.is_ready:
            logger.error("Failed to initialize eyes")
            return
            
        # Example state transitions
        await eyes.set_voice_state(VoiceState.LISTENING)
        await asyncio.sleep(2)
        
        await eyes.set_voice_state(VoiceState.PROCESSING)
        await asyncio.sleep(2)
        
        # Try different speaking patterns
        for pattern in range(4):
            await eyes.start_speaking(pattern)
            await asyncio.sleep(3)
            
        await eyes.set_voice_state(VoiceState.IDLE)

if __name__ == "__main__":
    # Run the example
    asyncio.run(example_usage()) 