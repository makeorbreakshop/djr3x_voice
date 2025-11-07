"""
Mode Change Sound Service for CantinaOS

This service plays a sound effect when system mode changes.
"""

"""
SERVICE: ModeChangeSoundService
PURPOSE: Plays audio feedback sounds during system mode transitions
EVENTS_IN: SYSTEM_MODE_CHANGE
EVENTS_OUT: SERVICE_STATUS_UPDATE
KEY_METHODS: _handle_mode_change, _find_sound_file
DEPENDENCIES: Audio utilities for sound playback, StarTours audio files
"""

import logging
import os
from typing import Dict, Optional, Any

from pyee.asyncio import AsyncIOEventEmitter

from ..base_service import BaseService
from ..core.event_topics import EventTopics
from ..event_payloads import SystemModeChangePayload, ServiceStatus
from ..utils.audio_utils import play_audio_file

class ModeChangeSoundService(BaseService):
    """
    Service that plays sounds during mode transitions.
    
    Features:
    - Plays configurable sounds when system mode changes
    - Event-driven architecture using EventBus
    """
    
    def __init__(
        self,
        event_bus: AsyncIOEventEmitter,
        config: Optional[Dict[str, Any]] = None,
        logger: Optional[logging.Logger] = None
    ):
        """Initialize the mode change sound service.
        
        Args:
            event_bus: Event bus instance
            config: Optional configuration dictionary
            logger: Optional logger instance
        """
        super().__init__("mode_change_sound", event_bus, logger)
        self.config = config or {}
        
        # Set sound file paths
        self.sounds = {
            "mode_change": self._find_sound_file("startours_ding.mp3"),
            "startup": self._find_sound_file("startours_ding.mp3"),
            "shutdown": self._find_sound_file("startours_ding.mp3"),
        }
        
        self.logger.debug("ModeChangeSoundService initialized")
        
    def _find_sound_file(self, filename: str) -> Optional[str]:
        """Find a sound file in various possible locations.
        
        Args:
            filename: The sound file to find
            
        Returns:
            Path to the sound file or None if not found
        """
        # Define possible paths
        paths = [
            # CantinaOS audio directory
            os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 
                "audio", 
                "startours_audio",
                filename
            ),
            # Project root audio directory
            os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                "audio",
                "startours_audio",
                filename
            ),
            # Main project audio directory
            os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
                "audio",
                "startours_audio",
                filename
            )
        ]
        
        # Find the first path that exists
        for path in paths:
            if os.path.exists(path):
                self.logger.debug(f"Found sound file at: {path}")
                return path
                
        self.logger.warning(f"Sound file not found: {filename}")
        return None
        
    async def _start(self) -> None:
        """Initialize the service and subscribe to events."""
        self.logger.info("Starting mode change sound service")
        
        # Subscribe to mode change events
        await self.subscribe(EventTopics.SYSTEM_MODE_CHANGE, self._handle_mode_change)
        
        # Emit status update
        await self._emit_status(
            ServiceStatus.RUNNING,
            "Service started"
        )
        
    async def _stop(self) -> None:
        """Clean up resources."""
        self.logger.info("Stopping mode change sound service")
        
    async def _handle_mode_change(self, payload) -> None:
        """
        Handle mode change events.
        
        Args:
            payload: The mode change event payload
        """
        try:
            # Extract mode information
            old_mode = payload.get("old_mode", "UNKNOWN")
            new_mode = payload.get("new_mode", "UNKNOWN")
            
            self.logger.info(f"Mode change detected: {old_mode} → {new_mode}")
            
            # Select the sound file
            sound_file = self.sounds.get("mode_change")
            
            # Play the sound if file exists
            if sound_file:
                self.logger.debug(f"Playing mode change sound: {sound_file}")
                await play_audio_file(sound_file, blocking=False)
            else:
                self.logger.warning("Mode change sound file not found")
                
        except Exception as e:
            self.logger.error(f"Error playing mode change sound: {e}")
            # Don't raise exception to avoid disrupting other services 