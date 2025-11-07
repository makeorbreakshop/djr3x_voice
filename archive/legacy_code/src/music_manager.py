"""
Music Manager for DJ R3X.
Handles background music playback and volume control.
"""

import os
import asyncio
import logging
import random
import vlc
from typing import Optional, Dict, Any, List, Union
from pathlib import Path

from src.bus import EventBus, EventTypes, SystemMode

# Configure logging
logger = logging.getLogger(__name__)

class MusicManager:
    """Manages background music playback and volume control."""
    
    def __init__(self,
                 event_bus: EventBus,
                 music_dir: str = "audio/music",
                 duck_level: float = 0.3,
                 duck_fade_time: float = 0.5,
                 volume: float = 0.8,
                 disable_music: bool = False):
        """Initialize the music manager.
        
        Args:
            event_bus: Event bus instance
            music_dir: Directory containing music files
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
        
        # System mode tracking
        self.current_mode = SystemMode.STARTUP
        
        # Subscribe to events
        self._subscribe_to_events()
    
    def _subscribe_to_events(self) -> None:
        """Subscribe to relevant events on the bus."""
        self.event_bus.on(EventTypes.VOICE_SPEAKING_STARTED, self._handle_speaking_started)
        self.event_bus.on(EventTypes.VOICE_SPEAKING_FINISHED, self._handle_speaking_finished)
        self.event_bus.on(EventTypes.SYSTEM_MODE_CHANGED, self._handle_mode_changed)
        self.event_bus.on(EventTypes.MUSIC_CONTROL_COMMAND, self._handle_music_control)
    
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
            
            # Create music directory if it doesn't exist
            os.makedirs(self.music_dir, exist_ok=True)
            
            logger.info("Music Manager started successfully")
            return True
                
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
    
    async def scan_music_directory(self) -> List[Path]:
        """Scan the music directory for audio files and return the list.
        
        Returns:
            List[Path]: List of audio file paths found
        """
        try:
            # Ensure directory exists
            os.makedirs(self.music_dir, exist_ok=True)
            
            # Find all supported audio files
            extensions = [".mp3", ".wav", ".m4a", ".ogg", ".flac"]
            self.tracks = []
            
            for ext in extensions:
                self.tracks.extend(list(self.music_dir.glob(f"*{ext}")))
            
            # Sort alphabetically for consistent numbering
            self.tracks.sort()
            
            logger.info(f"Found {len(self.tracks)} music tracks")
            return self.tracks
            
        except Exception as e:
            logger.error(f"Error scanning music directory: {e}")
            self.tracks = []
            return []
    
    async def list_music(self) -> None:
        """List available music tracks with numbers."""
        tracks = await self.scan_music_directory()
        
        if not tracks:
            logger.info("No music tracks found. Add audio files to the music directory.")
            return
        
        logger.info("\nAvailable Music Tracks:")
        for i, track in enumerate(tracks, 1):
            logger.info(f"{i}: {track.name}")
    
    async def play_track(self, track_identifier: Union[int, str]) -> bool:
        """Play a specific track by number or filename.
        
        Args:
            track_identifier: Track number (1-based) or filename
            
        Returns:
            bool: True if playback started successfully
        """
        if self.disable_music or not self.tracks:
            return False
        
        # If no tracks loaded, scan directory first
        if not self.tracks:
            await self.scan_music_directory()
            if not self.tracks:
                logger.error("No music tracks available")
                return False
        
        try:
            selected_track = None
            
            # Handle track selection by number
            if isinstance(track_identifier, int) or (isinstance(track_identifier, str) and track_identifier.isdigit()):
                track_number = int(track_identifier)
                if 1 <= track_number <= len(self.tracks):
                    selected_track = self.tracks[track_number - 1]
                else:
                    logger.error(f"Invalid track number: {track_number}. Valid range: 1-{len(self.tracks)}")
                    return False
            
            # Handle track selection by name
            elif isinstance(track_identifier, str):
                # Look for exact filename match first
                exact_matches = [t for t in self.tracks if t.name.lower() == track_identifier.lower()]
                if exact_matches:
                    selected_track = exact_matches[0]
                else:
                    # Try partial match
                    matches = [t for t in self.tracks if track_identifier.lower() in t.name.lower()]
                    if matches:
                        selected_track = matches[0]
                    else:
                        logger.error(f"No track found matching: {track_identifier}")
                        return False
            
            # Stop current playback if any
            if self.player and self.player.is_playing():
                self.player.stop()
            
            # Create media and set player
            media = self.instance.media_new(str(selected_track))
            self.player.set_media(media)
            
            # Start playback
            self.player.play()
            
            # Set volume based on current mode
            if self.current_mode == SystemMode.INTERACTIVE:
                # In interactive mode, we might be speaking, so check if ducked
                if self.is_ducked:
                    await self.set_volume(self.duck_level)
                else:
                    await self.set_volume(self.normal_volume)
            else:
                # In other modes, use normal volume
                await self.set_volume(self.normal_volume)
            
            # Store current track info
            self.current_track = selected_track.name
            self.current_track_index = self.tracks.index(selected_track)
            
            # Emit event
            await self.event_bus.emit(
                EventTypes.MUSIC_TRACK_STARTED,
                {"track": selected_track.name}
            )
            
            logger.info(f"Now playing: {selected_track.name}")
            return True
            
        except Exception as e:
            logger.error(f"Error playing track: {e}")
            return False
    
    async def stop_playback(self) -> None:
        """Stop current music playback."""
        if self.player and self.player.is_playing():
            self.player.stop()
            logger.info("Music playback stopped")
    
    async def play_next(self) -> None:
        """Play the next track in the playlist."""
        if self.disable_music or not self.tracks:
            return
            
        try:
            # Increment track index
            self.current_track_index = (self.current_track_index + 1) % len(self.tracks)
            await self.play_track(self.current_track_index + 1)
            
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
        # Only duck volume if we're in interactive mode
        if self.current_mode == SystemMode.INTERACTIVE and not self.is_ducked:
            self.is_ducked = True
            await self.set_volume(self.duck_level, self.duck_fade_time)
            await self.event_bus.emit(EventTypes.MUSIC_VOLUME_DUCKED)
    
    async def _handle_speaking_finished(self, data: Dict[str, Any]) -> None:
        """Handle voice.speaking_finished event."""
        # Only restore volume if we're in interactive mode
        if self.current_mode == SystemMode.INTERACTIVE and self.is_ducked:
            self.is_ducked = False
            await self.set_volume(self.normal_volume, self.duck_fade_time)
            await self.event_bus.emit(EventTypes.MUSIC_VOLUME_RESTORED)
    
    async def _handle_mode_changed(self, data: Dict[str, Any]) -> None:
        """Handle system.mode_changed event.
        
        Args:
            data: Event data containing old_mode and new_mode
        """
        if "new_mode" not in data:
            return
            
        new_mode = data["new_mode"]
        
        # Convert string mode to enum if needed
        if isinstance(new_mode, str):
            try:
                new_mode = SystemMode(new_mode)
            except ValueError:
                logger.error(f"Invalid system mode: {new_mode}")
                return
        
        # Store current mode
        self.current_mode = new_mode
        
        # Handle mode-specific behaviors
        if new_mode == SystemMode.IDLE or new_mode == SystemMode.STARTUP:
            # Stop music when entering IDLE or STARTUP modes
            await self.stop_playback()
        elif new_mode == SystemMode.INTERACTIVE:
            # When entering INTERACTIVE, keep playing but enable ducking
            # (ducking will be activated when speech starts)
            pass
    
    async def _handle_music_control(self, data: Dict[str, Any]) -> None:
        """Handle music.control_command event.
        
        Args:
            data: Command data with action and optional parameters
        """
        if "action" not in data:
            logger.error("Missing 'action' in music control command")
            return
            
        action = data["action"].lower()
        
        if action == "list":
            await self.list_music()
            
        elif action == "play":
            # Get track identifier (number or name)
            track_id = data.get("track_name")
            if not track_id:
                logger.error("Missing track identifier in play command")
                return
                
            # Check if we need to transition to AMBIENT mode first
            if self.current_mode == SystemMode.IDLE:
                logger.info("Transitioning to AMBIENT mode for music playback")
                await self.event_bus.emit(
                    EventTypes.SYSTEM_MODE_CHANGED,
                    {"old_mode": self.current_mode.value, "new_mode": SystemMode.AMBIENT.value}
                )
            
            # Play the requested track
            await self.play_track(track_id)
            
        elif action == "stop":
            await self.stop_playback()
            
        else:
            logger.warning(f"Unknown music control action: {action}") 