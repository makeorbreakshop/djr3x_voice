"""Music controller service for CantinaOS."""
from typing import Optional
import vlc

from ..base_service import BaseService
from ..event_topics import EventTopics

class MusicControllerService(BaseService):
    """Service for controlling music playback."""
    
    def __init__(self, event_bus):
        """Initialize the music controller service."""
        super().__init__(event_bus)
        self._instance = vlc.Instance()
        self._player = self._instance.media_player_new()
        self._current_track_id: Optional[str] = None
        self._volume = 1.0
        
    async def start(self):
        """Start the music controller service."""
        await super().start()
        self.event_bus.subscribe(EventTopics.MUSIC_PLAYBACK_REQUESTED, self._handle_playback_request)
        self.event_bus.subscribe(EventTopics.MUSIC_PLAYBACK_STOP_REQUESTED, self._handle_stop_request)
        self.event_bus.subscribe(EventTopics.SPEECH_SYNTHESIS_STARTED, self._handle_speech_start)
        self.event_bus.subscribe(EventTopics.SPEECH_SYNTHESIS_COMPLETED, self._handle_speech_end)
        
    async def stop(self):
        """Stop the music controller service."""
        await self._cleanup()
        await super().stop()
        
    async def _cleanup(self):
        """Clean up music playback resources."""
        if self._player.is_playing():
            self._player.stop()
        if self._current_track_id:
            await self.event_bus.emit(EventTopics.MUSIC_PLAYBACK_STOPPED, {
                "track_id": self._current_track_id
            })
            self._current_track_id = None
        
    async def _handle_playback_request(self, topic: str, payload: dict):
        """Handle a music playback request."""
        track_id = payload.get("track_id")
        volume = payload.get("volume", 1.0)
        
        # In a real implementation, we would load the track file
        # For now, we just simulate playback
        self._current_track_id = track_id
        self._volume = volume
        
        await self.event_bus.emit(EventTopics.MUSIC_PLAYBACK_STARTED, {
            "track_id": track_id,
            "volume": volume
        })
        
    async def _handle_stop_request(self, topic: str, payload: dict):
        """Handle a stop playback request."""
        await self._cleanup()
            
    async def _handle_speech_start(self, topic: str, payload: dict):
        """Handle speech synthesis start - duck the music."""
        if self._current_track_id:
            self._volume = 0.3  # Reduce volume during speech
            await self.event_bus.emit(EventTopics.MUSIC_VOLUME_CHANGED, {
                "volume": self._volume
            })
            
    async def _handle_speech_end(self, topic: str, payload: dict):
        """Handle speech synthesis end - restore music volume."""
        if self._current_track_id:
            self._volume = 1.0  # Restore full volume
            await self.event_bus.emit(EventTopics.MUSIC_VOLUME_CHANGED, {
                "volume": self._volume
            }) 