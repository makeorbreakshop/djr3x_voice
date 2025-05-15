"""
Music Controller Service for CantinaOS

This service manages music playback with mode-aware behavior and audio ducking during speech.
"""

import os
import asyncio
import logging
from typing import Dict, Optional, List, Any
import vlc
from pydantic import BaseModel, Field
from pyee.asyncio import AsyncIOEventEmitter
import time
import uuid
import glob
import math

from ..base_service import BaseService
from ..event_topics import EventTopics
from ..event_payloads import (
    MusicCommandPayload,
    BaseEventPayload,
    ServiceStatusPayload,
    ServiceStatus,
    SystemModePayload,
    LogLevel,
    StandardCommandPayload
)

class MusicTrack(BaseModel):
    """Model representing a music track."""
    name: str
    path: str
    duration: Optional[float] = None

class MusicControllerConfig(BaseModel):
    """Configuration for the music controller service."""
    music_dir: str = Field(default="assets/music", description="Directory containing music files")
    normal_volume: int = Field(default=70, description="Normal playback volume (0-100)")
    ducking_volume: int = Field(default=30, description="Volume during speech (0-100)")

class MusicControllerService(BaseService):
    """
    Service for managing music playback with mode-aware behavior and audio ducking.
    
    Features:
    - Mode-specific playback behavior (IDLE, AMBIENT, INTERACTIVE)
    - Audio ducking during speech
    - CLI command integration
    - Resource cleanup
    """
    
    def __init__(self, event_bus: AsyncIOEventEmitter, config: Dict[str, Any] = None):
        """Initialize the music controller service."""
        super().__init__(service_name="MusicController", event_bus=event_bus)
        
        # Configure using proper pattern
        config_dict = config or {}
        self._config = MusicControllerConfig(**config_dict)
        
        # Initialize service attributes
        self.music_dir = self._config.music_dir
        self.tracks: Dict[str, MusicTrack] = {}
        self.current_track: Optional[MusicTrack] = None
        self.player: Optional[vlc.MediaPlayer] = None
        self.current_mode = "IDLE"
        self.normal_volume = self._config.normal_volume
        self.ducking_volume = self._config.ducking_volume
        self.is_ducking = False
        
        # Create VLC instance
        self.vlc_instance = vlc.Instance()
        
        # Subscriptions will be set up during start()
        self._subscriptions = []
        
    async def subscribe_to_events(self):
        """Subscribe to relevant system events."""
        # Add debugging for subscriptions
        self.logger.debug("Setting up music controller event subscriptions")
        
        # Use proper subscription using BaseService.subscribe
        await self.subscribe(EventTopics.MUSIC_COMMAND, self._handle_music_command)
        self.logger.debug("Subscribed to MUSIC_COMMAND events")
        
        await self.subscribe(EventTopics.SYSTEM_MODE_CHANGE, self._handle_mode_change)
        self.logger.debug("Subscribed to SYSTEM_MODE_CHANGE events")
        
        await self.subscribe(EventTopics.SPEECH_SYNTHESIS_STARTED, self._handle_speech_start)
        self.logger.debug("Subscribed to SPEECH_SYNTHESIS_STARTED events")
        
        await self.subscribe(EventTopics.SPEECH_SYNTHESIS_ENDED, self._handle_speech_end)
        self.logger.debug("Subscribed to SPEECH_SYNTHESIS_ENDED events")
        
        self.logger.info("Music controller event subscriptions complete")
        
    async def start(self):
        """Start the music controller service."""
        self.logger.info("Starting music controller service")
        
        # Call the parent start method first
        await super().start()
        
        # Now subscribe to events
        self.logger.debug("Setting up event subscriptions")
        await self.subscribe_to_events()
        
        # Load the music library
        self.logger.debug("Loading music library")
        await self._load_music_library()
        
        # Set service status to running
        self.logger.info(f"Music controller started with {len(self.tracks)} tracks loaded")
        await self._emit_status(ServiceStatus.RUNNING, "Music controller started")
        
    async def stop(self):
        """Stop the music controller service and cleanup resources."""
        try:
            # Stop any current playback
            if self.player:
                self.player.stop()
                self.player.release()
                self.player = None
                self.current_track = None
                
                # Emit playback stopped event
                await self.emit(
                    EventTopics.MUSIC_PLAYBACK_STOPPED,
                    BaseEventPayload(conversation_id=None)
                )
            
            # Remove event subscriptions
            for topic, handler in self._subscriptions:
                try:
                    # Check if event_bus exists and only then try to remove the listener
                    if self.event_bus is not None:
                        try:
                            await self.event_bus.remove_listener(topic, handler)
                        except Exception as e:
                            self.logger.debug(f"Error removing event listener for {topic}: {e}")
                    else:
                        self.logger.debug(f"Skipping listener removal for {topic}: event_bus is None")
                except Exception as e:
                    self.logger.debug(f"Error accessing event bus for {topic}: {e}")
            
            # Clear subscriptions list
            self._subscriptions.clear()
            
            # Release VLC instance
            if self.vlc_instance:
                self.vlc_instance.release()
                self.vlc_instance = None
            
            # Call parent stop method
            await super().stop()
            
        except Exception as e:
            self.logger.error(f"Error during MusicControllerService cleanup: {e}")
            await self._emit_status(
                ServiceStatus.ERROR,
                f"Cleanup error: {e}",
                severity=LogLevel.ERROR
            )
            raise
        
    async def _load_music_library(self):
        """Load available music tracks from the music directory."""
        try:
            # Get the absolute path to log where we're looking
            abs_music_dir = os.path.abspath(self.music_dir)
            self.logger.info(f"Loading music from directory: {abs_music_dir}")
            
            # Track how many music files we find
            music_files_count = 0
            
            # Check if the music_dir exists
            if not os.path.exists(self.music_dir):
                self.logger.warning(f"Music directory not found: {self.music_dir}")
                
                # Try multiple standard locations for music files
                potential_dirs = [
                    # Relative to project root
                    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "audio", "music"),
                    # Relative to cantina_os package
                    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "music"),
                    # Project audio subdirectories
                    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "assets", "audio"),
                    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "audio"),
                ]
                
                # Try each potential directory
                for alt_dir in potential_dirs:
                    self.logger.info(f"Trying alternate music directory: {alt_dir}")
                    if os.path.exists(alt_dir):
                        self.music_dir = alt_dir
                        self.logger.info(f"Using alternate music directory: {alt_dir}")
                        break
                
                # If we still don't have a valid directory
                if not os.path.exists(self.music_dir):
                    self.logger.warning(f"No valid music directory found after checking alternatives")
                    return
            
            # List all files in the directory
            files = os.listdir(self.music_dir)
            self.logger.debug(f"Files found in music directory: {files}")
            
            # Track the number of music files found
            music_files_count = 0
            
            for filename in files:
                if filename.endswith(('.mp3', '.wav', '.m4a')):
                    music_files_count += 1
                    path = os.path.join(self.music_dir, filename)
                    name = os.path.splitext(filename)[0]
                    
                    # Create media to get duration
                    media = self.vlc_instance.media_new(path)
                    media.parse()
                    duration = media.get_duration() / 1000.0  # Convert ms to seconds
                    
                    self.tracks[name] = MusicTrack(
                        name=name,
                        path=path,
                        duration=duration
                    )
                    self.logger.debug(f"Loaded track: {name} ({duration:.1f}s)")
            
            if music_files_count == 0:
                self.logger.warning(f"No music files (.mp3, .wav, .m4a) found in directory: {self.music_dir}")
                # Log a helpful hint about using install_music_files
                self.logger.info("To add music files, use the 'install_music_files' command or copy files directly to the music directory")
                
            self.logger.info(f"Loaded {len(self.tracks)} music tracks")
            
        except Exception as e:
            self.logger.error(f"Error loading music library: {e}")
            await self._emit_status(
                ServiceStatus.ERROR,
                f"Failed to load music library: {e}",
                severity=LogLevel.ERROR
            )
            
    async def install_music_files(self, source_dir: str) -> bool:
        """
        Copy music files from a source directory into the music directory.
        
        Args:
            source_dir: Source directory containing music files
            
        Returns:
            True if files were copied successfully, False otherwise
        """
        try:
            self.logger.info(f"Installing music files from {source_dir} to {self.music_dir}")
            
            # Ensure destination directory exists
            os.makedirs(self.music_dir, exist_ok=True)
            
            # Get list of music files in source directory
            if not os.path.exists(source_dir):
                self.logger.error(f"Source directory not found: {source_dir}")
                return False
                
            # Count files copied
            files_copied = 0
            
            # Copy music files
            for filename in os.listdir(source_dir):
                if filename.endswith(('.mp3', '.wav', '.m4a')):
                    source_path = os.path.join(source_dir, filename)
                    dest_path = os.path.join(self.music_dir, filename)
                    
                    # Copy file if it doesn't exist
                    if not os.path.exists(dest_path):
                        import shutil
                        shutil.copy2(source_path, dest_path)
                        self.logger.info(f"Copied music file: {filename}")
                        files_copied += 1
                    else:
                        self.logger.debug(f"Music file already exists: {filename}")
            
            self.logger.info(f"Installed {files_copied} music files to {self.music_dir}")
            
            # Reload music library if files were copied
            if files_copied > 0:
                await self._load_music_library()
                return True
                
            return files_copied > 0
            
        except Exception as e:
            self.logger.error(f"Error installing music files: {e}")
            return False

    async def _handle_music_command(self, payload):
        """
        Handle music commands from CLI or other sources
        
        Args:
            payload: Command payload from the dispatcher
        """
        try:
            self.logger.debug(f"Received music command payload: {payload}")
            
            # Always convert to StandardCommandPayload for consistent processing
            command = StandardCommandPayload.from_legacy_format(payload)
            self.logger.debug(f"Standardized command: {command}")
            
            # Get the full compound command if available
            full_command = command.get_full_command()
            
            # Handle different command patterns
            if full_command == "list music" or command.command == "list":
                # List available tracks
                await self._list_tracks()
                
            elif full_command == "play music" or command.command == "play":
                # Check for track number in args
                if not command.validate_arg_count(1):
                    # Try extracting from raw_input as fallback for "play music N" pattern
                    raw_input = command.raw_input
                    if raw_input and raw_input.strip().lower().startswith("play music "):
                        parts = raw_input.strip().split()
                        if len(parts) >= 3:
                            track_num = parts[2]
                            self.logger.info(f"Extracted track number from raw_input: {track_num}")
                            await self._play_track(track_num)
                            return
                            
                    # No track number found
                    await self._send_error("Track number required. Usage: play music <track_number>")
                    return
                    
                # Get track number from args and play
                track_num = command.args[0]
                self.logger.info(f"Playing track number: {track_num}")
                await self._play_track(track_num)
                
            elif full_command == "install music" or command.command == "install":
                # Install sample music files
                await self._handle_install_music_command(command.args)
                
            elif full_command == "stop music" or command.command == "stop":
                # Stop playback
                await self._stop_playback()
                
            elif full_command == "debug music" or (command.command == "debug" and command.subcommand == "music"):
                # Debug music library
                await self._debug_music_library()
                
            # Handle legacy "music" command without subcommand
            elif command.command == "music" and not command.subcommand:
                await self._send_error("Music command requires a subcommand: play, stop, list, install, or debug")
                
            else:
                # Unknown command
                await self._send_error(f"Unknown music command: {command}")
                
        except Exception as e:
            self.logger.error(f"Error handling music command: {e}", exc_info=True)
            await self._send_error(f"Error processing music command: {str(e)}")

    async def _handle_install_music_command(self, args):
        """
        Handle 'install music' command
        
        Args:
            args: Command arguments (optional source directory)
        """
        try:
            # Check if source directory is provided
            if args and len(args) > 0:
                source_dir = args[0]
                success = await self.install_music_files(source_dir)
                if success:
                    await self._send_success(f"Successfully installed music files from {source_dir}")
                else:
                    await self._send_error(f"Failed to install music files from {source_dir}")
                return
            
            # Use the actual directory where we know music files exist
            root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
            actual_music_dir = os.path.join(root_dir, "audio", "music")
            
            if os.path.exists(actual_music_dir):
                self.logger.info(f"Found actual music directory: {actual_music_dir}")
                success = await self.install_music_files(actual_music_dir)
                if success:
                    await self._send_success(f"Successfully installed music files from {actual_music_dir}")
                    return
            
            # Fall back to other potential locations
            potential_dirs = [
                os.path.join(root_dir, "audio", "music"),
                os.path.join(root_dir, "audio"),
                os.path.join(root_dir, "assets", "audio", "music"),
                "audio/music",
                "assets/audio/music",
                "audio/samples",
            ]
            
            # Add path relative to the current file
            current_dir = os.path.dirname(os.path.abspath(__file__))
            for rel_path in ["../../audio/music", "../audio/music", "../../assets/audio/music"]:
                potential_dirs.append(os.path.normpath(os.path.join(current_dir, rel_path)))
            
            # Try each directory
            for sample_dir in potential_dirs:
                if os.path.exists(sample_dir):
                    success = await self.install_music_files(sample_dir)
                    if success:
                        await self._send_success(f"Successfully installed sample music from {sample_dir}")
                        return
            
            # No sample music found
            await self._send_error("No sample music found. Specify the source directory: install music <directory>")
                
        except Exception as e:
            self.logger.error(f"Error handling install music command: {e}")
            await self._send_error(f"Error installing music: {str(e)}")

    async def _play_track(self, track_num: str) -> None:
        """Play a specific track"""
        try:
            # Better input validation with detailed logging
            self.logger.info(f"Attempting to play track number: '{track_num}'")
            
            # First check if track_num is None or empty
            if not track_num:
                await self._send_error("Track number is required")
                return
                
            # Check if track_num is a valid number
            if not isinstance(track_num, str) or not track_num.isdigit():
                await self._send_error(f"Invalid track number: '{track_num}'. Must be a number between 1 and {len(self.tracks)}.")
                return
                
            # Check if the track number is in range
            track_num_int = int(track_num)
            if track_num_int < 1 or track_num_int > len(self.tracks):
                await self._send_error(f"Track number out of range: {track_num}. Must be between 1 and {len(self.tracks)}.")
                return
                
            # Get the actual track from the loaded tracks
            track_index = track_num_int - 1  # Convert to 0-based index
            track_name = list(self.tracks.keys())[track_index]
            track = self.tracks[track_name]
            
            self.logger.info(f"Playing track: {track.name} (path: {track.path})")
            
            # Create and configure a new player if needed
            if self.player:
                self.player.stop()
                await self._cleanup_player(self.player)
                
            # Create a new player for this track
            self.player = self.vlc_instance.media_player_new()
            media = self.vlc_instance.media_new(track.path)
            self.player.set_media(media)
            
            # Set volume based on current mode and ducking state
            volume = self.ducking_volume if self.is_ducking else self.normal_volume
            self.player.audio_set_volume(volume)
            
            # Start playback
            self.player.play()
            self.current_track = track
            
            # Send play command event
            await self.emit(
                EventTopics.MUSIC_PLAYBACK_STARTED,
                {
                    "track_name": track.name,
                    "track_index": track_index,
                    "duration": track.duration
                }
            )
            
            await self._send_success(f"Playing track {track_num}: {track.name}")
            
        except Exception as e:
            self.logger.error(f"Error playing track: {str(e)}", exc_info=True)
            await self._send_error(f"Error playing track: {str(e)}")

    async def _stop_playback(self) -> None:
        """Stop music playback"""
        try:
            if not self.player:
                await self._send_success("No music is currently playing")
                return
                
            # Stop the player
            self.player.stop()
            
            # Get track info before cleanup
            track_name = self.current_track.name if self.current_track else "Unknown"
            
            # Clean up the player
            await self._cleanup_player(self.player)
            self.player = None
            self.current_track = None
            
            # Emit stopped event
            await self.emit(
                EventTopics.MUSIC_PLAYBACK_STOPPED,
                {
                    "track_name": track_name
                }
            )
            
            await self._send_success("Stopped music playback")
            
        except Exception as e:
            self.logger.error(f"Error stopping playback: {str(e)}", exc_info=True)
            await self._send_error(f"Error stopping playback: {str(e)}")

    async def _list_tracks(self) -> None:
        """List available tracks"""
        try:
            tracks = self._get_available_tracks()
            track_list = "\n".join([f"{i+1}. {track}" for i, track in enumerate(tracks)])
            await self._send_success(f"Available tracks:\n{track_list}")
        except Exception as e:
            await self._send_error(f"Error listing tracks: {str(e)}")

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

    def _get_available_tracks(self) -> List[str]:
        """Get list of available tracks"""
        # Use the actual loaded tracks instead of hardcoded list
        return [track.name for track in self.tracks.values()]
        
    async def _handle_mode_change(self, payload: Dict[str, Any]):
        """Handle system mode changes."""
        # Use new_mode field from the payload (not SystemModePayload.mode)
        if not isinstance(payload, dict) or "new_mode" not in payload:
            self.logger.error(f"Invalid mode change payload: {payload}")
            return
            
        new_mode = payload["new_mode"]
        self.current_mode = new_mode
        
        # Get conversation ID if available
        conversation_id = payload.get("conversation_id")
        
        # Stop music in IDLE mode
        if new_mode == "IDLE" and self.player:
            await self._handle_stop_request(
                MusicCommandPayload(
                    action="stop",
                    conversation_id=conversation_id
                )
            )
            
    async def _handle_speech_start(self, payload: BaseEventPayload):
        """Handle speech synthesis start - reduce music volume."""
        if self.player and self.current_mode == "INTERACTIVE":
            self.is_ducking = True
            self.player.audio_set_volume(self.ducking_volume)
            
    async def _handle_speech_end(self, payload: BaseEventPayload):
        """Handle speech synthesis end - restore music volume."""
        if self.player and self.current_mode == "INTERACTIVE":
            self.is_ducking = False
            self.player.audio_set_volume(self.normal_volume)
            
    async def get_track_list(self) -> List[Dict[str, any]]:
        """Get a list of available music tracks."""
        track_info = []
        for track in self.tracks.values():
            track_info.append({
                "name": track.name,
                "duration": track.duration
            })
        return track_info
        
    async def play_music(self, track_index=None, track_name=None):
        """
        Play a music track by index or name.
        
        Args:
            track_index: Index of the track to play (optional)
            track_name: Name of the track to play (optional)
            
        Returns:
            True if playback started, False otherwise
        """
        try:
            # Get track by index if provided
            if track_index is not None and 0 <= track_index < len(self.tracks):
                track_name = list(self.tracks.keys())[track_index]
                
            # Get track by name if provided or derived from index
            if track_name:
                # Create a payload to pass to _handle_play_request
                payload = MusicCommandPayload(
                    action="play",
                    song_query=track_name,
                    conversation_id=None
                )
                await self._handle_play_request(payload)
                return True
                
            return False
        except Exception as e:
            self.logger.error(f"Error in play_music: {e}")
            return False
        
    async def _cleanup_player(self, player):
        """
        Clean up a VLC player instance.
        
        Args:
            player: The VLC player instance to clean up
            
        Returns:
            None
        """
        if player:
            try:
                player.stop()
                player.release()
                self.logger.debug(f"Successfully cleaned up VLC player {id(player)}")
            except Exception as e:
                self.logger.error(f"Error cleaning up VLC player: {e}")
        
    async def _emit_status(
        self,
        status: ServiceStatus,
        message: str,
        severity: Optional[LogLevel] = None
    ) -> None:
        """Report service status change."""
        payload = ServiceStatusPayload(
            service_name=self.service_name,
            status=status,
            message=message,
            severity=severity
        )
        await self.emit(EventTopics.SERVICE_STATUS_UPDATE, payload)

    async def _debug_music_library(self):
        """Debug the music library and path issues"""
        try:
            # Get all relevant paths for debugging
            current_dir = os.path.dirname(os.path.abspath(__file__))
            package_dir = os.path.dirname(current_dir)
            project_dir = os.path.dirname(package_dir)
            root_dir = os.path.dirname(project_dir)
            
            # Report the current working directory
            cwd = os.getcwd()
            
            # Potential music locations to check
            potential_dirs = [
                os.path.join(root_dir, "audio", "music"),
                os.path.join(project_dir, "assets", "music"),
                os.path.join(root_dir, "assets", "music"),
                os.path.join(root_dir, "assets", "audio"),
                os.path.join(root_dir, "audio"),
            ]
            
            # Build debug report
            debug_info = [
                f"Music library debug information:",
                f"",
                f"Current music directory: {self.music_dir}",
                f"Directory exists: {os.path.exists(self.music_dir)}",
                f"Number of tracks loaded: {len(self.tracks)}",
                f"",
                f"Current working directory: {cwd}",
                f"Service file location: {__file__}",
                f"",
                f"Checking potential music directories:",
            ]
            
            # Check each potential directory
            for path in potential_dirs:
                exists = os.path.exists(path)
                if exists:
                    try:
                        files = os.listdir(path)
                        music_files = [f for f in files if f.endswith(('.mp3', '.wav', '.m4a'))]
                        debug_info.append(f"  ✓ {path} (Found: {len(music_files)} music files)")
                    except Exception as e:
                        debug_info.append(f"  ! {path} (Error listing files: {e})")
                else:
                    debug_info.append(f"  ✗ {path} (Directory not found)")
            
            # Send debug report
            debug_report = "\n".join(debug_info)
            await self._send_success(debug_report)
            
            # Reload the music library
            self.logger.info("Attempting to reload music library...")
            await self._load_music_library()
            
            # Report results
            if len(self.tracks) > 0:
                await self._send_success(f"Successfully reloaded library with {len(self.tracks)} tracks")
            else:
                await self._send_error("Failed to load any music tracks after reload")
            
        except Exception as e:
            self.logger.error(f"Error in debug_music_library: {e}")
            await self._send_error(f"Error debugging music library: {str(e)}") 