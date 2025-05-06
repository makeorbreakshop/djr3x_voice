"""
Music Manager for DJ R3X.
Handles background music playback and volume control.
"""

import os
import asyncio
import logging
import random
import vlc
from typing import Optional, Dict, Any, List
from pathlib import Path

from src.bus import EventBus, EventTypes

# Configure logging
logger = logging.getLogger(__name__)

class MusicManager:
    """Manages background music playback and volume control."""
    
    def __init__(self,
                 event_bus: EventBus,
                 music_dir: str = "audio/bgmusic",
                 duck_level: float = 0.3,
                 duck_fade_time: float = 0.5,
                 volume: float = 0.8,
                 disable_music: bool = False):
        """Initialize the music manager.
        
        Args:
            event_bus: Event bus instance
            music_dir: Directory containing background music files
            duck_level: Volume level when ducking (0-1)
            duck_fade_time: Time to fade volume in seconds
            volume: Normal playback volume (0-1)
            disable_music: Skip music playback if True
        """
        self.event_bus = event_bus
        self.music_dir = Path(music_dir)
        self.duck_level = duck_level
        self.duck_fade_time = duck_fade_time
        self.normal_volume = volume
        self.disable_music = disable_music
        
        # VLC instance and player
        self.instance: Optional[vlc.Instance] = None
        self.player: Optional[vlc.MediaPlayer] = None
        self.current_track: Optional[str] = None
        
        # Volume control
        self.current_volume = volume
        self.is_ducked = False
        self.volume_task: Optional[asyncio.Task] = None
        
        # Track list
        self.tracks: List[Path] = []
        self.current_track_index = -1
        
        # Subscribe to events
        self._subscribe_to_events()
    
    def _subscribe_to_events(self) -> None:
        """Subscribe to relevant events on the bus."""
        self.event_bus.on(EventTypes.VOICE_SPEAKING_STARTED, self._handle_speaking_started)
        self.event_bus.on(EventTypes.VOICE_SPEAKING_FINISHED, self._handle_speaking_finished)
    
    async def start(self) -> bool:
        """Start the music manager and initialize playback.
        
        Returns:
            bool: True if started successfully
        """
        if self.disable_music:
            logger.info("Music Manager running in disabled mode")
            return True
            
        try:
            # Initialize VLC
            self.instance = vlc.Instance()
            self.player = self.instance.media_player_new()
            
            # Set initial volume
            self.player.audio_set_volume(int(self.normal_volume * 100))
            
            # Load track list
            await self._load_tracks()
            
            # Start playback if tracks available
            if self.tracks:
                await self.play_next()
                logger.info("Music Manager started successfully")
                return True
            else:
                logger.warning("No music tracks found")
                return False
                
        except Exception as e:
            logger.error(f"Failed to start Music Manager: {e}")
            return False
    
    async def stop(self) -> None:
        """Stop the music manager and clean up resources."""
        try:
            # Stop any volume fade task
            if self.volume_task and not self.volume_task.done():
                self.volume_task.cancel()
                try:
                    await self.volume_task
                except asyncio.CancelledError:
                    pass
            
            # Stop playback
            if self.player:
                self.player.stop()
                self.player.release()
                self.player = None
            
            # Clean up VLC instance
            if self.instance:
                self.instance.release()
                self.instance = None
            
            logger.info("Music Manager stopped")
            
        except Exception as e:
            logger.error(f"Error stopping Music Manager: {e}")
    
    async def _load_tracks(self) -> None:
        """Load music tracks from the music directory."""
        try:
            # Create music directory if it doesn't exist
            os.makedirs(self.music_dir, exist_ok=True)
            
            # Find all MP3 files
            self.tracks = list(self.music_dir.glob("*.mp3"))
            random.shuffle(self.tracks)
            
            logger.info(f"Loaded {len(self.tracks)} music tracks")
            
        except Exception as e:
            logger.error(f"Error loading music tracks: {e}")
            self.tracks = []
    
    async def play_next(self) -> None:
        """Play the next track in the playlist."""
        if self.disable_music or not self.tracks:
            return
            
        try:
            # Increment track index
            self.current_track_index = (self.current_track_index + 1) % len(self.tracks)
            track = self.tracks[self.current_track_index]
            
            # Create media and set player
            media = self.instance.media_new(str(track))
            self.player.set_media(media)
            
            # Start playback
            self.player.play()
            
            # Store current track
            self.current_track = track.name
            
            # Emit event
            await self.event_bus.emit(
                EventTypes.MUSIC_TRACK_STARTED,
                {"track": track.name}
            )
            
            # Set up end callback to play next track
            @media.event_manager().event_attach(vlc.EventType.MediaStateChanged)
            def on_state_changed(event):
                if event.state == vlc.State.Ended:
                    asyncio.create_task(self.play_next())
            
        except Exception as e:
            logger.error(f"Error playing next track: {e}")
    
    async def set_volume(self, volume: float, fade_time: float = 0.0) -> None:
        """Set the playback volume with optional fade.
        
        Args:
            volume: Target volume (0-1)
            fade_time: Time to fade to target volume in seconds
        """
        if self.disable_music or not self.player:
            return
            
        try:
            # Cancel any running fade
            if self.volume_task and not self.volume_task.done():
                self.volume_task.cancel()
                try:
                    await self.volume_task
                except asyncio.CancelledError:
                    pass
            
            if fade_time > 0:
                # Start volume fade task
                self.volume_task = asyncio.create_task(
                    self._fade_volume(volume, fade_time)
                )
            else:
                # Set volume immediately
                self.current_volume = volume
                self.player.audio_set_volume(int(volume * 100))
            
        except Exception as e:
            logger.error(f"Error setting volume: {e}")
    
    async def _fade_volume(self, target: float, duration: float) -> None:
        """Fade volume to target level.
        
        Args:
            target: Target volume level (0-1)
            duration: Fade duration in seconds
        """
        try:
            start = self.current_volume
            steps = int(duration * 50)  # 50 steps per second
            step_time = duration / steps
            
            for i in range(steps + 1):
                # Calculate current volume
                volume = start + (target - start) * (i / steps)
                
                # Set volume
                self.current_volume = volume
                self.player.audio_set_volume(int(volume * 100))
                
                # Wait for next step
                await asyncio.sleep(step_time)
            
        except asyncio.CancelledError:
            # Task was cancelled, stop fade
            pass
        except Exception as e:
            logger.error(f"Error during volume fade: {e}")
    
    # Event handlers
    
    async def _handle_speaking_started(self, data: Dict[str, Any]) -> None:
        """Handle voice.speaking_started event."""
        if not self.is_ducked:
            self.is_ducked = True
            await self.set_volume(self.duck_level, self.duck_fade_time)
            await self.event_bus.emit(EventTypes.MUSIC_VOLUME_DUCKED)
    
    async def _handle_speaking_finished(self, data: Dict[str, Any]) -> None:
        """Handle voice.speaking_finished event."""
        if self.is_ducked:
            self.is_ducked = False
            await self.set_volume(self.normal_volume, self.duck_fade_time)
            await self.event_bus.emit(EventTypes.MUSIC_VOLUME_RESTORED) 