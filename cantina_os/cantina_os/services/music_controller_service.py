"""
Music Controller Service for CantinaOS

This service manages music playback with mode-aware behavior and audio ducking during speech.
"""

"""
SERVICE: MusicControllerService
PURPOSE: Music playback management with VLC backend, audio ducking, crossfading, and DJ mode support
EVENTS_IN: MUSIC_COMMAND, SYSTEM_MODE_CHANGE, SPEECH_SYNTHESIS_STARTED, SPEECH_SYNTHESIS_ENDED, AUDIO_DUCKING_START, AUDIO_DUCKING_STOP, DJ_MODE_CHANGED, DJ_NEXT_TRACK
EVENTS_OUT: MUSIC_PLAYBACK_STARTED, MUSIC_PLAYBACK_STOPPED, MUSIC_PLAYBACK_PAUSED, MUSIC_PLAYBACK_RESUMED, MUSIC_LIBRARY_UPDATED, TRACK_ENDING_SOON, CROSSFADE_STARTED, CROSSFADE_COMPLETE, CLI_RESPONSE
KEY_METHODS: handle_play_music, handle_stop_music, handle_list_music, _crossfade_to_track, _smart_play_track, get_track_progress, _setup_track_end_timer
DEPENDENCIES: VLC media player, music directory (configurable path), audio hardware
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

# Suppress VLC verbose logging to prevent Core Audio property listener errors
# from flooding the console output
os.environ['VLC_VERBOSE'] = '-1'  # Suppress all VLC logging
# Note: VLC_PLUGIN_PATH is set in startup scripts to point to VLC.app bundle

from ..base_service import BaseService
from ..core.event_topics import EventTopics
from ..event_payloads import (
    MusicCommandPayload,
    BaseEventPayload,
    ServiceStatusPayload,
    ServiceStatus,
    SystemModePayload,
    LogLevel,
    StandardCommandPayload,
    DJModeChangedPayload
)
from ..models.music_models import MusicTrack, MusicLibrary
from ..utils.command_decorators import compound_command, register_service_commands, validate_compound_command, command_error_handler

# Import necessary Pydantic models from event_schemas
from cantina_os.core.event_schemas import (
    TrackDataPayload,
    TrackEndingSoonPayload,
    CrossfadeCompletePayload # Assuming this will be defined or updated
)

# Use MusicTrack class from shared models instead
# class MusicTrack(BaseModel):
#     """Model representing a music track."""
#     name: str
#     path: str
#     duration: Optional[float] = None

class MusicControllerConfig(BaseModel):
    """Configuration for the music controller service."""
    music_dir: str = Field(default="assets/music", description="Directory containing music files")
    normal_volume: int = Field(default=70, description="Normal playback volume (0-100)")
    ducking_volume: int = Field(default=50, description="Volume during speech (0-100)")
    crossfade_duration_ms: int = Field(default=3000, description="Duration of crossfade between tracks in milliseconds")
    crossfade_steps: int = Field(default=50, description="Number of volume adjustment steps during crossfade")
    track_ending_threshold_sec: int = Field(default=30, description="Seconds before track end to emit TRACK_ENDING_SOON event")

class MusicControllerService(BaseService):
    """
    Service for managing music playback with mode-aware behavior and audio ducking.
    
    Features:
    - Mode-specific playback behavior (IDLE, AMBIENT, INTERACTIVE)
    - Audio ducking during speech
    - CLI command integration
    - Resource cleanup
    - Crossfade between tracks (DJ mode)
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
        self.secondary_player: Optional[vlc.MediaPlayer] = None  # For crossfade
        self.next_track: Optional[MusicTrack] = None  # For track preloading
        self.current_mode = "IDLE"
        self.normal_volume = self._config.normal_volume
        self.ducking_volume = self._config.ducking_volume
        self.is_ducking = False
        self.is_crossfading = False
        self.dj_mode_active = False
        self.track_end_timer: Optional[asyncio.Task] = None # Use Optional[asyncio.Task]
        # REMOVED: self._progress_task (Phase 3.1 - Progress tracking now client-side)
        self.is_paused = False  # Track pause state
        self.play_queue: List[MusicTrack] = []  # Simple play queue
        
        # Timer-based progress tracking (replaces unreliable VLC timing)
        self._playback_start_time: Optional[float] = None  # System time when playback started
        self._pause_start_time: Optional[float] = None  # System time when paused
        self._total_pause_duration: float = 0.0  # Total time spent paused
        self._last_known_position: float = 0.0  # Last calculated position before pause
        
        # Create VLC instance with proper configuration and fallback options
        self.vlc_instance = self._create_vlc_instance()
        
        # Set default command topic for auto-registration
        self._default_command_topic = EventTopics.MUSIC_COMMAND
        
        # Subscriptions will be set up during start()
        self._subscriptions = []
    
    def _create_vlc_instance(self):
        """
        Create VLC instance with proper configuration and fallback options.
        
        Returns:
            vlc.Instance or None: VLC instance if successful, None otherwise
        """
        # First attempt: Full configuration to reduce verbose logging
        # and prevent Core Audio property listener errors
        vlc_args_primary = [
            '--intf', 'dummy',           # No interface
            '--extraintf', '',           # No extra interfaces
            '--quiet',                   # Reduce log output
            '--no-video',                # Audio only
            '--aout', 'auhal',           # Use auhal directly
            '--no-audio-time-stretch',   # Disable time stretching
            '--no-plugins-cache',        # Don't cache plugins
            '--verbose', '0'             # Minimal verbosity
        ]
        
        try:
            print(f"DEBUG: Attempting to create VLC instance with primary configuration: {vlc_args_primary}")
            instance = vlc.Instance(vlc_args_primary)
            if instance:
                print("DEBUG: VLC instance created successfully with primary configuration")
                if hasattr(self, 'logger'):
                    self.logger.info("VLC instance created successfully with primary configuration")
                return instance
        except Exception as e:
            print(f"DEBUG: Primary VLC instance creation failed: {e}")
            if hasattr(self, 'logger'):
                self.logger.warning(f"Primary VLC instance creation failed: {e}")
        
        # Second attempt: Minimal configuration
        vlc_args_fallback = [
            '--intf', 'dummy',
            '--quiet',
            '--no-video'
        ]
        
        try:
            self.logger.debug("Attempting to create VLC instance with fallback configuration")
            instance = vlc.Instance(vlc_args_fallback)
            if instance:
                self.logger.info("VLC instance created successfully with fallback configuration")
                return instance
        except Exception as e:
            self.logger.warning(f"Fallback VLC instance creation failed: {e}")
        
        # Third attempt: Default VLC instance
        try:
            self.logger.debug("Attempting to create default VLC instance")
            instance = vlc.Instance()
            if instance:
                self.logger.info("VLC instance created successfully with default configuration")
                return instance
        except Exception as e:
            self.logger.error(f"Default VLC instance creation failed: {e}")
        
        # All attempts failed
        self.logger.error("All VLC instance creation attempts failed. Music playback will be disabled.")
        return None
        
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
        
        # Add direct audio ducking event subscriptions
        await self.subscribe(EventTopics.AUDIO_DUCKING_START, self._handle_audio_ducking_start)
        self.logger.debug("Subscribed to AUDIO_DUCKING_START events")
        
        await self.subscribe(EventTopics.AUDIO_DUCKING_STOP, self._handle_audio_ducking_stop)
        self.logger.debug("Subscribed to AUDIO_DUCKING_STOP events")

        # Add DJ mode events
        await self.subscribe(EventTopics.DJ_MODE_CHANGED, self._handle_dj_mode_changed)
        self.logger.debug("Subscribed to DJ_MODE_CHANGED events")
        
        await self.subscribe(EventTopics.DJ_NEXT_TRACK, self._handle_dj_next_track)
        self.logger.debug("Subscribed to DJ_NEXT_TRACK events")
        
        self.logger.info("Music controller event subscriptions complete")
        
    async def start(self):
        """Start the music controller service."""
        self.logger.info("Starting music controller service")
        
        # Call the parent start method first
        await super().start()
        
        # Now subscribe to events
        self.logger.debug("Setting up event subscriptions")
        await self.subscribe_to_events()
        
        # Load the music library in the background (non-blocking)
        self.logger.debug("Starting background music library loading")
        asyncio.create_task(self._load_music_library())
        
        # Auto-register compound commands using decorators
        register_service_commands(self, self._event_bus)
        self.logger.info("Auto-registered music commands using decorators")
        
        # REMOVED: Background progress tracking task (Phase 3.1 - Music Progress System Cleanup)
        # Progress tracking is now handled client-side in the web dashboard
        # Server-side progress events were causing CLI event flooding (60+ events/min)
        
        # Set service status to running
        self.logger.info(f"Music controller started with {len(self.tracks)} tracks loaded")
        await self._emit_status(ServiceStatus.RUNNING, "Music controller started")
        
    async def stop(self):
        """Stop the music controller service and cleanup resources."""
        try:
            # REMOVED: Progress tracking task cleanup (Phase 3.1 - Music Progress System Cleanup)
            # Server-side progress task was removed - progress is now calculated client-side
            
            # Cancel track end timer
            if self.track_end_timer and not self.track_end_timer.done():
                self.track_end_timer.cancel()
                try:
                    await self.track_end_timer
                except asyncio.CancelledError:
                    pass
                self.track_end_timer = None
            
            # Stop any current playback with improved cleanup
            if self.player:
                try:
                    # Stop playback first
                    self.player.stop()
                    # Wait a moment for VLC to stop
                    await asyncio.sleep(0.1)
                    # Release the player
                    self.player.release()
                except Exception as e:
                    self.logger.debug(f"Error stopping primary player: {e}")
                finally:
                    self.player = None
                    self.current_track = None
            
            # Clean up secondary player (crossfade)
            if self.secondary_player:
                try:
                    self.secondary_player.stop()
                    await asyncio.sleep(0.1)
                    self.secondary_player.release()
                except Exception as e:
                    self.logger.debug(f"Error stopping secondary player: {e}")
                finally:
                    self.secondary_player = None
            
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
            
            # Emit final playback stopped event
            try:
                await self.emit(
                    EventTopics.MUSIC_PLAYBACK_STOPPED,
                    BaseEventPayload(conversation_id=None)
                )
            except Exception as e:
                self.logger.debug(f"Error emitting final stopped event: {e}")
            
            # Release VLC instance with proper cleanup
            if self.vlc_instance:
                try:
                    # Give VLC time to clean up internal state
                    await asyncio.sleep(0.2)
                    self.vlc_instance.release()
                except Exception as e:
                    self.logger.debug(f"Error releasing VLC instance: {e}")
                finally:
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
        
    def _parse_track_metadata(self, filename: str) -> tuple[str, str]:
        """
        Parse artist and title from filename.
        Returns tuple of (artist, title).
        """
        name, _ = os.path.splitext(filename)
        
        # Check for "Artist - Title" format
        if " - " in name:
            artist, title = name.split(" - ", 1)
            return artist.strip(), title.strip()
            
        # For title-only files, use "Cantina Band" as default artist
        return "Cantina Band", name.strip()

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
                    if os.path.exists(alt_dir):
                        self.logger.info(f"Found alternative music directory: {alt_dir}")
                        self.music_dir = alt_dir
                        break
            
            # Now that we've potentially updated self.music_dir, check it exists
            if not os.path.exists(self.music_dir):
                self.logger.error(f"Could not find any valid music directory")
                return
            
            # Clear existing tracks
            self.tracks.clear()
            
            # Process .mp3, .wav, and .m4a files
            for ext in ['.mp3', '.wav', '.m4a']:
                pattern = os.path.join(self.music_dir, f'*{ext}')
                self.logger.debug(f"Searching for music files with pattern: {pattern}")
                
                for filepath in glob.glob(pattern):
                    try:
                        # Extract filename without extension for display
                        filename = os.path.basename(filepath)
                        
                        # Parse artist and title from filename
                        artist, title = self._parse_track_metadata(filename)
                        
                        # Create media to get duration, safely handled
                        try:
                            if self.vlc_instance is None:
                                self.logger.debug(f"VLC instance is None, skipping duration detection for {filepath}")
                                duration = None
                            else:
                                media = self.vlc_instance.media_new(filepath)
                                if media is None:
                                    self.logger.debug(f"VLC media creation failed for {filepath}")
                                    duration = None
                                else:
                                    # Use parse_with_options for better control
                                    media.parse_with_options(0, 0)
                                    start_time = time.time()
                                    
                                    # Poll for parsing completion with timeout
                                    while media.get_parsed_status() != vlc.MediaParsedStatus.done:
                                        await asyncio.sleep(0.01)  # Non-blocking sleep
                                        if time.time() - start_time > 5.0:  # 5-second timeout
                                            self.logger.warning(f"Timeout parsing duration for: {filepath}")
                                            break
                                    
                                    duration_ms = media.get_duration()
                                    duration = duration_ms / 1000.0 if duration_ms > 0 else None
                                    
                                    if duration is None:
                                        self.logger.warning(f"Failed to get duration for track: {filepath}. It will default to 0 in the library.")
                        except Exception as e:
                            self.logger.debug(f"Could not get duration for {filepath}: {e}")
                            duration = None
                        
                        # Create MusicTrack with unique path for consistent identification
                        # Use absolute path for reliable identification across services
                        abs_path = os.path.abspath(filepath)
                        track = MusicTrack(
                            name=title,  # Use title as name
                            path=abs_path,
                            duration=duration,
                            track_id=title,  # Use title as track_id for consistency
                            title=title,  # Add title field for BrainService
                            artist=artist  # Add artist field from parsing
                        )
                        
                        # Use title as key for consistent lookup
                        self.tracks[title] = track
                        music_files_count += 1
                        
                        self.logger.debug(f"Loaded track: {title} by {artist} ({abs_path}), duration: {duration}s")
                    except Exception as e:
                        self.logger.error(f"Error loading music track {filepath}: {e}")
            
            self.logger.info(f"Loaded {music_files_count} music tracks from {self.music_dir}")
            
            # Alert if no music found
            if music_files_count == 0:
                self.logger.warning("No music files found. Music playback will be unavailable.")
            
            # Publish a music library updated event with proper track data
            track_data = await self.get_track_list()
            await self.emit(
                EventTopics.MUSIC_LIBRARY_UPDATED,
                {
                    "track_count": music_files_count,
                    "tracks": track_data
                }
            )
            
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
        Legacy music command handler - dispatches to appropriate methods.
        """
        self.logger.info(f"[MUSIC_COMMAND] Received music command: {payload}")
        
        # Handle action-based payloads (from other services)
        if isinstance(payload, dict) and "action" in payload:
            self.logger.info(f"[MUSIC_COMMAND] Processing action: {payload['action']}")
            if payload["action"] == "play":
                self.logger.info(f"[MUSIC_COMMAND] Play request - song_query: {payload.get('song_query', 'NONE')}")
                try:
                    await self._handle_play_request(MusicCommandPayload(**payload))
                except Exception as e:
                    self.logger.error(f"[MUSIC_COMMAND] Error creating MusicCommandPayload: {e}")
                    self.logger.error(f"[MUSIC_COMMAND] Payload was: {payload}")
                    await self._send_error(f"Invalid play command: {str(e)}")
                return
            elif payload["action"] == "pause":
                await self._handle_pause_request()
                return
            elif payload["action"] == "resume":
                await self._handle_resume_request()
                return
            elif payload["action"] == "stop":
                await self._handle_stop_request(MusicCommandPayload(**payload))
                return
            elif payload["action"] == "next":
                await self._handle_next_track_request()
                return
            elif payload["action"] == "queue":
                await self._handle_queue_request(MusicCommandPayload(**payload))
                return
            elif payload["action"] == "volume":
                await self._handle_volume_request(payload)
                return
            elif payload["action"] == "crossfade":
                # Handle crossfade action from TimelineExecutorService
                try:
                    crossfade_payload = MusicCommandPayload(**payload)
                    next_track_id = crossfade_payload.song_query
                    crossfade_duration_sec = crossfade_payload.fade_duration
                    # Get the crossfade_id from the payload
                    crossfade_id = payload.get("crossfade_id")

                    next_track = self.tracks.get(next_track_id)
                    if not next_track:
                        self.logger.error(f"Crossfade failed: Next track with ID/name '{next_track_id}' not found in music library.")
                        await self._send_error(f"Crossfade failed: Track '{next_track_id}' not found.")
                        return

                    await self._crossfade_to_track(next_track, source="timeline", duration_sec=crossfade_duration_sec, crossfade_id=crossfade_id)
                    return
                except Exception as e:
                    self.logger.error(f"Error handling crossfade action: {e}", exc_info=True)
                    await self._send_error(f"Error during crossfade: {str(e)}")
                    return
        
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
            if command_pattern == "list music":
                await self.handle_list_music(payload)
            elif command_pattern == "play music":
                await self.handle_play_music(payload)
            elif command_pattern == "stop music":
                await self.handle_stop_music(payload)
            elif command_pattern == "install music":
                await self.handle_install_music(payload)
            elif command_pattern == "debug music":
                await self.handle_debug_music(payload)
            else:
                self.logger.warning(f"Unknown music command pattern: {command_pattern}")
                await self._send_error(f"Unknown music command: {command_pattern}")
            return
        
        self.logger.warning(f"Unhandled music command payload format: {payload}")

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

    async def _smart_play_track(self, track_query: str, source: str = "cli") -> None:
        """
        Smart track selection and playback
        
        Args:
            track_query: Track number, name, or search query
            source: Source of the play request (default: "cli" for CLI commands)
        """
        try:
            self.logger.info(f"[SMART_PLAY] Smart track selection for query: '{track_query}', source: {source}")
            self.logger.info(f"[SMART_PLAY] Number of tracks available: {len(self.tracks)}")
            
            # If tracks list is empty, return error
            if not self.tracks:
                self.logger.error("[SMART_PLAY] No tracks available!")
                await self._send_error("No music tracks available. Please install music first.")
                return
                
            # Check if it's a valid track number
            if track_query.isdigit():
                track_num_int = int(track_query)
                if 1 <= track_num_int <= len(self.tracks):
                    # Convert to 0-based index
                    track_index = track_num_int - 1
                    track_name = list(self.tracks.keys())[track_index]
                    track = self.tracks[track_name]
                    self.logger.info(f"Found track #{track_query}: {track.name}")
                    await self._play_track_by_name(track.name, source)
                    return
                else:
                    await self._send_error(f"Track number {track_query} out of range. Must be 1-{len(self.tracks)}.")
                    return
                    
            # Check for direct track name match
            if track_query in self.tracks:
                self.logger.info(f"[SMART_PLAY] Found exact track name match: {track_query}")
                await self._play_track_by_name(track_query, source)
                return
            else:
                self.logger.info(f"[SMART_PLAY] No exact match for '{track_query}' in tracks: {list(self.tracks.keys())[:5]}...")
                
            # Try fuzzy matching by track name
            matches = []
            track_query_lower = track_query.lower()
            
            for name in self.tracks.keys():
                name_lower = name.lower()
                # Check for substring matches
                if track_query_lower in name_lower:
                    matches.append((name, 100 - (len(name_lower) - len(track_query_lower))))
                # Check for partial word matches
                elif any(track_query_lower in word for word in name_lower.split('_')):
                    matches.append((name, 50))
                
            # Sort by match score (descending)
            matches.sort(key=lambda x: x[1], reverse=True)
            
            if matches:
                best_match = matches[0][0]
                self.logger.info(f"Found fuzzy match for '{track_query}': '{best_match}'")
                await self._play_track_by_name(best_match, source)
                return
                
            # No matches found, default to first track
            self.logger.warning(f"No matches found for '{track_query}', playing first track")
            first_track = list(self.tracks.keys())[0]
            await self._play_track_by_name(first_track, source)
                
        except Exception as e:
            self.logger.error(f"Error in smart track selection: {e}")
            await self._send_error(f"Error selecting track: {str(e)}")

    async def _play_track_by_name(self, track_name: str, source: str = "cli") -> None:
        """
        Play a track by its exact name.
        
        Args:
            track_name: The exact name of the track to play
            source: Source of the play request (cli, voice, dj)
        """
        try:
            self.logger.info(f"[PLAY_BY_NAME] Attempting to play track: '{track_name}' from source: {source}")
            
            # First, try direct lookup
            track = self.tracks.get(track_name)
            
            if not track:
                # If not found, try fuzzy matching
                track_names = list(self.tracks.keys())
                best_match = None
                best_score = 0
                
                for name in track_names:
                    # Simple case-insensitive substring match
                    if track_name.lower() in name.lower():
                        score = len(track_name) / len(name)
                        if score > best_score:
                            best_score = score
                            best_match = name
                
                if best_match:
                    track = self.tracks[best_match]
                    self.logger.info(f"[PLAY_BY_NAME] Fuzzy matched '{track_name}' to '{best_match}'")
                else:
                    self.logger.warning(f"[PLAY_BY_NAME] No track found matching '{track_name}'")
                    await self._send_error(f"No track found matching '{track_name}'")
                    return
            else:
                self.logger.info(f"[PLAY_BY_NAME] Found track: {track.name} at path: {track.path}")
            
            # If we already have a track playing and DJ mode is active, crossfade
            if self.player and self.player.is_playing() and self.dj_mode_active:
                await self._crossfade_to_track(track, source=source)
                return
            
            # Otherwise, stop any current playback and play directly
            if self.player:
                self.player.stop()
                await self._cleanup_player(self.player)
            
            # Check if VLC instance is available
            if not self.vlc_instance:
                self.logger.error("[PLAY_BY_NAME] VLC instance not available. Cannot play music.")
                await self._send_error("Music playback unavailable - VLC initialization failed")
                return
            else:
                self.logger.info(f"[PLAY_BY_NAME] VLC instance available: {self.vlc_instance}")
            
            # Create a new media player
            self.player = self.vlc_instance.media_player_new()
            
            # Load and play the track
            self.logger.info(f"Playing track: {track.name} ({track.path})")
            media = self.vlc_instance.media_new(track.path)
            self.player.set_media(media)
            
            # Set volume based on current state
            volume = self.ducking_volume if self.is_ducking else self.normal_volume
            self.player.audio_set_volume(volume)
            
            # Start playback
            self.player.play()
            
            # Initialize timer-based progress tracking
            self._playback_start_time = time.time()
            self._pause_start_time = None
            self._total_pause_duration = 0.0
            self._last_known_position = 0.0
            self.is_paused = False
            
            # Update current track
            self.current_track = track
            
            # Emit MUSIC_PLAYBACK_STARTED event with track data
            track_data = self._create_track_data_payload(track)
            payload = {
                "track": track_data.model_dump(),
                "source": source,
                "mode": self.current_mode,
                "start_timestamp": self._playback_start_time,  # Phase 2.1: Add start timestamp for client-side progress
                "duration": track.duration  # Phase 2.1: Add duration for client-side calculations
            }
            self.logger.info(f"[MusicController] Emitting MUSIC_PLAYBACK_STARTED with payload: {payload}")
            await self.emit(EventTopics.MUSIC_PLAYBACK_STARTED, payload)
            self.logger.info("[MusicController] Used await self.emit() for MUSIC_PLAYBACK_STARTED")
            
            # Emit simple coordination event for timeline services
            await self.emit(EventTopics.TRACK_PLAYING, {})
            
            # Update MemoryService with currently playing track for coordination
            if self.dj_mode_active:
                await self.emit(EventTopics.MEMORY_SET, {
                    "key": "current_track",
                    "value": track_data.model_dump()
                })
                self.logger.info(f"Updated MemoryService with current track: {track.title}")

            self.logger.info(f"Now playing: {track.title} (Mode: {self.current_mode}, Source: {source})")
            
            # Set up track end detection timer if in DJ mode
            if self.dj_mode_active and track.duration:
                await self._setup_track_end_timer(track.duration)
            
            # Success message to CLI
            await self._send_success(f"Now playing: {track.title}")
            
        except Exception as e:
            self.logger.error(f"Error playing track {track_name}: {e}")
            await self._send_error(f"Error playing music: {str(e)}")

    async def _stop_playback(self) -> None:
        """Stop music playback with improved VLC cleanup"""
        try:
            if not self.player:
                await self._send_success("No music is currently playing")
                return
            
            # Cancel track end timer
            if self.track_end_timer and not self.track_end_timer.done():
                self.track_end_timer.cancel()
                try:
                    await self.track_end_timer
                except asyncio.CancelledError:
                    pass
                self.track_end_timer = None
                
            # Get track info before cleanup
            track_name = self.current_track.name if self.current_track else "Unknown"
            
            # Stop the player with improved cleanup
            try:
                self.player.stop()
                # Give VLC time to stop cleanly
                await asyncio.sleep(0.1)
            except Exception as e:
                self.logger.debug(f"Error stopping VLC player: {e}")
            
            # Clean up the player
            await self._cleanup_player(self.player)
            self.player = None
            self.current_track = None
            
            # Reset audio ducking and pause state
            self.is_ducking = False
            self.is_paused = False
            
            # Reset timer-based progress tracking
            self._playback_start_time = None
            self._pause_start_time = None
            self._total_pause_duration = 0.0
            self._last_known_position = 0.0
            
            # Emit stopped event
            await self.emit(
                EventTopics.MUSIC_PLAYBACK_STOPPED,
                {
                    "track_name": track_name
                }
            )
            
            # Emit simple coordination event for timeline services
            await self.emit(EventTopics.TRACK_STOPPED, {})
            
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
        """Send a success response via CLI_RESPONSE event."""
        await self.emit(
            EventTopics.CLI_RESPONSE,
            {
                "message": message,
                "is_error": False,
                "service": self.service_name
            }
        )

    async def _send_error(self, message: str) -> None:
        """Send an error response via CLI_RESPONSE event."""
        await self.emit(
            EventTopics.CLI_RESPONSE,
            {
                "message": message,
                "is_error": True,
                "service": self.service_name
            }
        )

    async def _handle_pause_request(self) -> None:
        """Handle pause request - pause current playback."""
        try:
            if not self.player or not self.player.is_playing():
                await self._send_error("No music is currently playing")
                return
                
            # Calculate current position before pausing
            if self._playback_start_time and not self.is_paused:
                current_time = time.time()
                self._last_known_position = (current_time - self._playback_start_time - self._total_pause_duration)
            
            # Record pause start time for accurate tracking
            self._pause_start_time = time.time()
            self.is_paused = True
            
            self.player.pause()
            
            # Emit paused event with position tracking (Phase 2.2)
            await self.emit(
                EventTopics.MUSIC_PLAYBACK_PAUSED,
                {
                    "track_name": self.current_track.name if self.current_track else "Unknown",
                    "paused_at_position": self._last_known_position,  # Phase 2.2: Add position tracking
                    "timestamp": self._pause_start_time  # Phase 2.2: Add precise timestamp
                }
            )
            
            await self._send_success("Music paused")
            
        except Exception as e:
            self.logger.error(f"Error pausing playback: {e}")
            await self._send_error(f"Error pausing music: {str(e)}")

    async def _handle_resume_request(self) -> None:
        """Handle resume request - resume paused playback."""
        try:
            if not self.player:
                await self._send_error("No music to resume")
                return
                
            if not self.is_paused:
                await self._send_error("Music is not paused")
                return
            
            # Calculate total pause duration for accurate position tracking
            resume_timestamp = time.time()
            if self._pause_start_time:
                pause_duration = resume_timestamp - self._pause_start_time
                self._total_pause_duration += pause_duration
                self._pause_start_time = None
            
            self.is_paused = False
            self.player.play()
            
            # Emit resumed event with position tracking (Phase 2.2)
            await self.emit(
                EventTopics.MUSIC_PLAYBACK_RESUMED,
                {
                    "track_name": self.current_track.name if self.current_track else "Unknown",
                    "resumed_timestamp": resume_timestamp,  # Phase 2.2: Add resume timestamp
                    "resume_position": self._last_known_position  # Phase 2.2: Add position where resumed
                }
            )
            
            await self._send_success("Music resumed")
            
        except Exception as e:
            self.logger.error(f"Error resuming playback: {e}")
            await self._send_error(f"Error resuming music: {str(e)}")

    async def _handle_next_track_request(self) -> None:
        """Handle next track request - play next track from queue or random."""
        try:
            next_track = None
            
            # First check if there's a track in the queue
            if self.play_queue:
                next_track = self.play_queue.pop(0)
                self.logger.info(f"Playing next track from queue: {next_track.name}")
            else:
                # No queue, pick a random different track
                available_tracks = [track for track in self.tracks.values() 
                                  if track != self.current_track]
                if available_tracks:
                    import random
                    next_track = random.choice(available_tracks)
                    self.logger.info(f"Playing random next track: {next_track.name}")
                else:
                    await self._send_error("No other tracks available")
                    return
            
            # Play the next track
            if next_track:
                await self._play_track_by_name(next_track.name, source="next")
            
        except Exception as e:
            self.logger.error(f"Error playing next track: {e}")
            await self._send_error(f"Error playing next track: {str(e)}")

    async def _handle_queue_request(self, payload: MusicCommandPayload) -> None:
        """Handle queue request - add track to play queue."""
        try:
            song_query = payload.song_query
            if not song_query:
                await self._send_error("No track specified for queue")
                return
                
            success = await self.add_to_queue(song_query)
            if success:
                await self._send_success(f"Added '{song_query}' to queue")
            else:
                await self._send_error(f"Could not add '{song_query}' to queue")
                
        except Exception as e:
            self.logger.error(f"Error handling queue request: {e}")
            await self._send_error(f"Error adding to queue: {str(e)}")

    async def _handle_volume_request(self, payload: Dict[str, Any]) -> None:
        """Handle volume adjustment request."""
        try:
            volume_level = payload.get("volume")
            if volume_level is None:
                await self._send_error("No volume level specified")
                return
            
            # Convert 0.0-1.0 range to 0-100 range for VLC
            if isinstance(volume_level, float) and 0.0 <= volume_level <= 1.0:
                vlc_volume = int(volume_level * 100)
            elif isinstance(volume_level, int) and 0 <= volume_level <= 100:
                vlc_volume = volume_level
            else:
                await self._send_error("Volume level must be between 0.0 and 1.0 (float) or 0 and 100 (int)")
                return
            
            # Set volume on active player
            if self.player and self.current_track:
                self.player.audio_set_volume(vlc_volume)
                
                # Update normal_volume for future tracks (but maintain ducking_volume separate)
                if not self.is_ducking:
                    self.normal_volume = vlc_volume
                
                self.logger.info(f"Volume set to {vlc_volume}% (normalized: {volume_level})")
                await self._send_success(f"Volume set to {vlc_volume}%")
                
                # Emit volume change event for dashboard
                await self.emit(
                    EventTopics.MUSIC_VOLUME_CHANGED,
                    {
                        "volume": volume_level,  # Normalized 0.0-1.0
                        "vlc_volume": vlc_volume,  # 0-100 for VLC
                        "is_ducking": self.is_ducking
                    }
                )
            else:
                await self._send_error("No music currently playing")
                
        except Exception as e:
            self.logger.error(f"Error handling volume request: {e}")
            await self._send_error(f"Error setting volume: {str(e)}")

    async def add_to_queue(self, track_name: str) -> bool:
        """Add a track to the play queue."""
        try:
            track = self.tracks.get(track_name)
            if not track:
                self.logger.warning(f"Track not found for queue: {track_name}")
                return False
                
            self.play_queue.append(track)
            self.logger.info(f"Added to queue: {track_name}")
            
            # Emit queue updated event
            await self.emit(
                EventTopics.MUSIC_QUEUE_UPDATED,
                {
                    "queue_length": len(self.play_queue),
                    "added_track": track_name
                }
            )
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error adding track to queue: {e}")
            return False

    def _get_available_tracks(self) -> List[str]:
        """Get list of available tracks"""
        # Use the actual loaded tracks instead of hardcoded list
        return [track.name for track in self.tracks.values()]

    @compound_command("list music")
    @command_error_handler
    async def handle_list_music(self, payload: dict) -> None:
        """Handle 'list music' command - lists available tracks."""
        self.logger.info("Listing available music tracks")
        await self._list_tracks()

    @compound_command("play music")
    @validate_compound_command(min_args=1, required_args=["track_name"])
    @command_error_handler
    async def handle_play_music(self, payload: dict) -> None:
        """Handle 'play music <track>' command - plays specified track."""
        args = payload.get("args", [])
        track_query = " ".join(args)
        self.logger.info(f"Playing music track: {track_query}")
        await self._smart_play_track(track_query)

    @compound_command("stop music")
    @command_error_handler
    async def handle_stop_music(self, payload: dict) -> None:
        """Handle 'stop music' command - stops music playback."""
        self.logger.info("Stopping music playback")
        await self._stop_playback()

    @compound_command("install music")
    @command_error_handler
    async def handle_install_music(self, payload: dict) -> None:
        """Handle 'install music [directory]' command - installs music from directory."""
        args = payload.get("args", [])
        self.logger.info(f"Installing music files from: {args}")
        await self._handle_install_music_command(args)

    @compound_command("debug music")
    @command_error_handler
    async def handle_debug_music(self, payload: dict) -> None:
        """Handle 'debug music' command - shows music library debug info."""
        self.logger.info("Running music library debug")
        await self._debug_music_library()

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
            
    async def _handle_stop_request(self, payload: MusicCommandPayload):
        """
        Handle a stop music request from any source
        
        Args:
            payload: Music command payload with stop action
        """
        try:
            self.logger.info(f"Handling stop music request: {payload}")
            await self._stop_playback()
        except Exception as e:
            self.logger.error(f"Error handling stop music request: {e}")
            await self._send_error(f"Error stopping music: {str(e)}")
            
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
            
    async def get_track_list(self) -> Dict[str, Any]:
        """Get a dictionary of all tracks with their metadata."""
        track_dict = {}
        for name, track in self.tracks.items():
            # Return the full MusicTrack data for library updates
            track_dict[name] = track.dict()
        return track_dict
        
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
        
    async def _handle_play_request(self, payload: MusicCommandPayload):
        """
        Handle a play music request from any source
        
        Args:
            payload: Music command payload with play action and song_query
        """
        try:
            song_query = payload.song_query
            self.logger.info(f"[PLAY_REQUEST] Handling play music request for query: '{song_query}'")
            self.logger.info(f"[PLAY_REQUEST] Available tracks: {list(self.tracks.keys())}")
            
            # Check if this is from a conversation (voice request)
            source = "voice" if payload.conversation_id else "web"  # Changed default from 'cli' to 'web' for dashboard
            self.logger.info(f"[PLAY_REQUEST] Source: {source}")
            
            # Pass the source to ensure voice requests get proper handling
            await self._smart_play_track(song_query, source=source)
            
        except Exception as e:
            self.logger.error(f"[PLAY_REQUEST] Error handling play music request: {e}")
            import traceback
            self.logger.error(f"[PLAY_REQUEST] Traceback: {traceback.format_exc()}")
            await self._send_error(f"Error playing music: {str(e)}")
        
    async def _cleanup_player(self, player):
        """
        Clean up a VLC player instance with improved error handling.
        
        Args:
            player: The VLC player instance to clean up
            
        Returns:
            None
        """
        if player:
            try:
                # Ensure player is stopped
                if player.is_playing():
                    player.stop()
                
                # Give VLC time to clean up internal state
                await asyncio.sleep(0.1)
                
                # Release the player
                player.release()
                
                self.logger.debug(f"Successfully cleaned up VLC player {id(player)}")
            except Exception as e:
                # Don't log VLC cleanup errors as they're often harmless Core Audio issues
                self.logger.debug(f"VLC player cleanup completed with minor issues (this is normal): {e}")
        
    async def _emit_status(
        self,
        status: ServiceStatus,
        message: str,
        severity: Optional[LogLevel] = None,
        force_emit: bool = False
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

    async def _handle_audio_ducking_start(self, payload: BaseEventPayload):
        """Handle audio ducking start - reduce music volume."""
        if self.player and (self.current_mode == "INTERACTIVE" or self.dj_mode_active):
            self.is_ducking = True
            self.player.audio_set_volume(self.ducking_volume)
            self.logger.debug(f"Music ducked to volume {self.ducking_volume}")
            
    async def _handle_audio_ducking_stop(self, payload: BaseEventPayload):
        """Handle audio ducking stop - restore music volume."""
        if self.player and (self.current_mode == "INTERACTIVE" or self.dj_mode_active):
            self.is_ducking = False
            self.player.audio_set_volume(self.normal_volume)
            self.logger.debug(f"Music volume restored to {self.normal_volume}")

    async def _handle_dj_mode_changed(self, payload: Dict[str, Any]) -> None:
        """Handle DJ mode activation/deactivation."""
        try:
            # Use Pydantic model for incoming payload
            mode_change_payload = DJModeChangedPayload(**payload)
            is_active = mode_change_payload.is_active

            if is_active:
                self.logger.info("DJ Mode activated")
                self.dj_mode_active = True
                
                # Don't independently select tracks - wait for BrainService to coordinate through MemoryService
                # BrainService will send MUSIC_COMMAND with the selected track
                self.logger.info("DJ mode active - waiting for track selection from BrainService")
            else:
                self.logger.info("DJ Mode deactivated")
                self.dj_mode_active = False
                # Stop current playback
                await self._stop_playback()
                
                # Update MemoryService to clear DJ state
                await self.emit(EventTopics.MEMORY_SET, {
                    "key": "current_track",
                    "value": None
                })
        except Exception as e:
            self.logger.error(f"Error handling DJ mode change: {e}")

    async def _handle_dj_next_track(self, payload: Dict[str, Any]) -> None:
        """
        Handle DJ next track command (skip to next track)
        
        Args:
            payload: Event payload
        """
        try:
            # If next_track is already loaded, crossfade to it
            if self.next_track and self.dj_mode_active:
                await self._crossfade_to_track(self.next_track, source="dj")
            else:
                # Otherwise, just stop current track - BrainService will select next track
                await self._stop_playback()
                await self.emit(EventTopics.MUSIC_PLAYBACK_STOPPED, {})
        except Exception as e:
            self.logger.error(f"Error handling DJ next track command: {e}")

    async def _setup_track_end_timer(self, track_duration_sec: float) -> None:
        """Sets up a timer to emit TRACK_ENDING_SOON before the track ends."""
        # Cancel any existing timer
        if self.track_end_timer and not self.track_end_timer.done():
            self.track_end_timer.cancel()

        threshold_sec = self._config.track_ending_threshold_sec
        # Ensure duration is valid and greater than the threshold
        if track_duration_sec is None or track_duration_sec <= threshold_sec:
            self.logger.debug(f"Track duration ({track_duration_sec}s) not long enough or invalid for TRACK_ENDING_SOON threshold ({threshold_sec}s). Not setting timer.")
            self.track_end_timer = None
            return

        # Calculate delay until the threshold is reached
        # We want to trigger the event *at* the threshold time remaining
        delay_sec = track_duration_sec - threshold_sec

        if delay_sec > 0:
            self.logger.info(f"Setting TRACK_ENDING_SOON timer for {delay_sec:.2f} seconds (track ending in {threshold_sec}s).")
            # Create and store the timer task
            self.track_end_timer = asyncio.create_task(
                self._delayed_track_ending_event(delay_sec),
                name=f"track_end_timer_{self.current_track.track_id if self.current_track else 'unknown'}"
            )
            # Add a done callback to handle potential exceptions (optional but good practice)
            # self.track_end_timer.add_done_callback(self._handle_task_exception) # Needs _handle_task_exception in this service
        else:
             self.logger.warning(f"Calculated negative or zero delay for track end timer ({delay_sec:.2f}s). Not setting timer.")
             self.track_end_timer = None


    async def _delayed_track_ending_event(self, delay_sec: float) -> None:
        """Waits for the specified delay and then emits the TRACK_ENDING_SOON event."""
        try:
            self.logger.debug(f"Delayed track ending event waiting for {delay_sec:.2f} seconds.")
            await asyncio.sleep(delay_sec)

            # Emit the TRACK_ENDING_SOON event
            await self._emit_track_ending_soon()

        except asyncio.CancelledError:
            self.logger.info("Track ending timer task cancelled.")
        except Exception as e:
            self.logger.error(f"Error in track ending timer task: {e}", exc_info=True)
            # TODO: Add error handling/status emission
        finally:
            self.track_end_timer = None # Clear the timer task reference


    def _create_track_data_payload(self, track: MusicTrack) -> TrackDataPayload:
        """
        Create a TrackDataPayload from a MusicTrack.
        Ensures all required fields are properly populated.
        """
        return TrackDataPayload(
            track_id=track.track_id,
            title=track.title,
            artist=track.artist or "Cantina Band",  # Use default if None
            album=track.album,
            genre=track.genre,
            duration=track.duration,
            filepath=track.path
        )

    async def _emit_track_ending_soon(self) -> None:
        """Emits the TRACK_ENDING_SOON event with current track data."""
        if self.current_track:
            self.logger.info(f"Emitting TRACK_ENDING_SOON for track: {self.current_track.title}")
            try:
                # Create the payload using the Pydantic model and helper method
                track_data = self._create_track_data_payload(self.current_track)
                payload = TrackEndingSoonPayload(
                    timestamp=time.time(),
                    current_track=track_data,
                    time_remaining=self._config.track_ending_threshold_sec
                )
                
                # Emit the event
                await self.emit(
                    EventTopics.TRACK_ENDING_SOON,
                    payload.dict()
                )
                self.logger.debug(f"Successfully emitted TRACK_ENDING_SOON for {self.current_track.title}")
            except Exception as e:
                self.logger.error(f"Error emitting TRACK_ENDING_SOON: {e}")
                # Don't re-raise, as this is a non-critical error

    async def _crossfade_to_track(self, next_track: MusicTrack, source: str = "dj", duration_sec: float = None, crossfade_id: str = None) -> None:
        """
        Crossfade from current track to next track.
        
        Args:
            next_track: The track to fade to
            source: The source of the crossfade request (e.g., 'dj', 'cli').
            duration_sec: Optional override for crossfade duration in seconds
            crossfade_id: Unique ID for the crossfade operation
        """
        if self.is_crossfading:
            self.logger.warning("Already crossfading, ignoring new crossfade request.")
            return

        if not self.player or not self.current_track:
            self.logger.warning("Cannot crossfade, no current track playing.")
            await self.play_track_by_name(next_track.name, source=source)
            return

        if not next_track:
            self.logger.error("Cannot crossfade, next track is not provided.")
            return

        self.logger.info(f"Starting crossfade from '{self.current_track.title}' to '{next_track.title}'.")
        self.is_crossfading = True
        self.next_track = next_track

        # Use provided crossfade_id or generate a new one
        if crossfade_id is None:
            crossfade_id = str(uuid.uuid4())

        try:
            # Create track data payloads for both tracks
            current_track_data = self._create_track_data_payload(self.current_track)
            next_track_data = self._create_track_data_payload(next_track)

            # Emit crossfade started event with proper track data
            await self.emit(
                EventTopics.CROSSFADE_STARTED,
                {
                    "crossfade_id": crossfade_id,
                    "from_track": current_track_data.dict(),
                    "to_track": next_track_data.dict(),
                    "duration_ms": self._config.crossfade_duration_ms
                }
            )

            # Check if VLC instance is available
            if not self.vlc_instance:
                self.logger.error("VLC instance not available. Cannot perform crossfade.")
                self.is_crossfading = False
                return
            
            # Create a secondary player for the next track
            if self.secondary_player:
                self.secondary_player.stop()
                self.secondary_player.release()
            self.secondary_player = self.vlc_instance.media_player_new()
            
            # Load and prepare the next track
            media = self.vlc_instance.media_new(next_track.path)
            self.secondary_player.set_media(media)
            
            # Calculate crossfade parameters
            duration_ms = int(duration_sec * 1000) if duration_sec else self._config.crossfade_duration_ms
            step_duration = duration_ms / self._config.crossfade_steps
            
            # IMPORTANT FIX: Use current volume as target, not normal_volume
            # This respects ducked state during crossfade
            target_volume = self.ducking_volume if self.is_ducking else self.normal_volume
            volume_step = target_volume / self._config.crossfade_steps
            
            self.logger.debug(f"Crossfade targeting volume: {target_volume} (ducked: {self.is_ducking})")
            
            # Start the next track at 0 volume
            self.secondary_player.audio_set_volume(0)
            self.secondary_player.play()
            
            # Perform the crossfade
            for step in range(self._config.crossfade_steps + 1):
                if not self.is_crossfading:
                    self.logger.warning("Crossfade interrupted")
                    break
                    
                # Calculate volumes for this step
                current_vol = int(target_volume - (step * volume_step))
                next_vol = int(step * volume_step)
                
                # Set volumes
                if self.player:
                    self.player.audio_set_volume(max(0, current_vol))
                if self.secondary_player:
                    self.secondary_player.audio_set_volume(min(target_volume, next_vol))
                
                # Wait for the step duration
                await asyncio.sleep(step_duration / 1000)
            
            # Clean up old player and update state
            if self.player:
                self.player.stop()
                await self._cleanup_player(self.player)
            
            # Swap players and update track info
            self.player = self.secondary_player
            self.secondary_player = None
            self.current_track = next_track
            self.next_track = None
            
            # Reset timer-based progress tracking for new track
            self._playback_start_time = time.time()
            self._pause_start_time = None
            self._total_pause_duration = 0.0
            self._last_known_position = 0.0
            self.is_paused = False
            
            # Emit crossfade complete event
            await self.emit(
                EventTopics.CROSSFADE_COMPLETE,
                {
                    "crossfade_id": crossfade_id,
                    "status": "success",
                    "current_track": next_track_data.dict()
                }
            )
            
            # Emit simple coordination event for timeline services (new track is now playing)
            await self.emit(EventTopics.TRACK_PLAYING, {})
            
            # Set up track end timer for the new track
            if self.dj_mode_active and next_track.duration:
                await self._setup_track_end_timer(next_track.duration)
            
        except Exception as e:
            self.logger.error(f"Error during crossfade: {e}", exc_info=True)
            self.is_crossfading = False
            
            # Clean up secondary player if it exists
            if self.secondary_player:
                self.secondary_player.stop()
                await self._cleanup_player(self.secondary_player)
                self.secondary_player = None
            
            # Emit error event
            await self.emit(
                EventTopics.CROSSFADE_COMPLETE,
                {
                    "crossfade_id": crossfade_id,
                    "status": "error",
                    "message": str(e)
                }
            )
        finally:
            self.is_crossfading = False

    async def preload_next_track(self, track: MusicTrack) -> None:
        """Preloads the next track without starting playback."""
        self.logger.info(f"Preloading next track: {track.title}")
        self.next_track = track
        # TODO: Potentially create a media instance for the next track here
        # but don't assign it to a player until crossfade starts.

    async def get_track_progress(self) -> Dict[str, Any]:
        """Gets the current playback progress using timer-based tracking.

        Uses system time calculations instead of unreliable VLC timing methods.
        Returns a dictionary with track information and progress.
        """
        progress_data = {
            "is_playing": False,
            "current_track": None,
            "position_sec": 0.0,
            "duration_sec": 0.0,
            "time_remaining_sec": 0.0,
            "progress_percent": 0.0
        }

        # Check if we have an active player and current track
        if self.player and self.current_track:
            # Determine if actually playing (not paused or stopped)
            is_actively_playing = self.player.is_playing() and not self.is_paused
            progress_data['is_playing'] = is_actively_playing
            progress_data['current_track'] = self.current_track.dict() if self.current_track else None
            
            # Use known track duration from metadata
            track_duration = self.current_track.duration or 0.0
            progress_data['duration_sec'] = track_duration
            
            # Calculate current position using timer-based tracking
            if self._playback_start_time and track_duration > 0:
                self.logger.info(f"[TIMER_DEBUG] Timer calculation: start_time={self._playback_start_time}, duration={track_duration}, paused={self.is_paused}")
                
                if self.is_paused:
                    # Use last known position when paused
                    current_position = self._last_known_position
                    self.logger.info(f"[TIMER_DEBUG] Timer calculation (paused): using last_known_position={current_position}")
                else:
                    # Calculate current position: elapsed time minus total pause time
                    current_time = time.time()
                    elapsed_time = current_time - self._playback_start_time
                    current_position = elapsed_time - self._total_pause_duration
                    self.logger.info(f"[TIMER_DEBUG] Timer calculation (playing): current_time={current_time}, elapsed={elapsed_time:.1f}s, pause_time={self._total_pause_duration:.1f}s, position={current_position:.1f}s")
                
                # Ensure position doesn't exceed track duration
                current_position = max(0.0, min(current_position, track_duration))
                
                progress_data['position_sec'] = current_position
                progress_data['time_remaining_sec'] = max(0.0, track_duration - current_position)
                progress_data['progress_percent'] = (current_position / track_duration) * 100.0 if track_duration > 0 else 0.0
                
                self.logger.info(f"[TIMER_DEBUG] Timer-based progress calculated: {current_position:.1f}s / {track_duration:.1f}s ({progress_data['progress_percent']:.1f}%)")
            else:
                self.logger.info(f"[TIMER_DEBUG] Timer calculation skipped: start_time={self._playback_start_time}, duration={track_duration}")

        # Include information about the next track if preloaded
        if self.next_track:
             progress_data['next_track'] = self.next_track.dict()

        return progress_data

    # REMOVED: _progress_tracking_loop method (Phase 3.1 - Music Progress System Cleanup)
    # This method was causing CLI event flooding with 60+ events per minute
    # Progress tracking is now handled client-side in the web dashboard
    # for better performance and reduced network traffic

    async def _handle_dj_start_command(self) -> None:
        """Handle the 'dj start' CLI command to activate DJ mode"""
        try:
            # Only activate if not already active
            if self.dj_mode_active:
                await self._send_error("DJ mode is already active")
                return
                
            # Emit event to activate DJ mode
            await self.emit(
                EventTopics.DJ_MODE_CHANGED,
                DJModeChangedPayload(is_active=True).dict()
            )
            
            # Confirm to user
            await self._send_success("DJ mode activated - R3X is taking over!")
            
            # If no music is playing, start a random track
            if not self.player or not self.player.is_playing():
                # Get a random track
                available_tracks = list(self.tracks.values())
                if available_tracks:
                    import random
                    random_track = random.choice(available_tracks)
                    await self._play_track_by_name(random_track.name, source="dj")
                    
        except Exception as e:
            self.logger.error(f"Error starting DJ mode: {e}")
            await self._send_error(f"Error starting DJ mode: {str(e)}")

    async def _handle_dj_stop_command(self) -> None:
        """Handle the 'dj stop' CLI command to deactivate DJ mode"""
        try:
            # Only deactivate if active
            if not self.dj_mode_active:
                await self._send_error("DJ mode is not active")
                return
                
            # Emit event to deactivate DJ mode
            await self.emit(
                EventTopics.DJ_MODE_CHANGED,
                DJModeChangedPayload(is_active=False).dict()
            )
            
            # Clean up DJ mode resources
            if self.track_end_timer:
                self.track_end_timer.cancel()
                self.track_end_timer = None
                
            # Doesn't stop the current music - just disables auto-DJ features
            
            # Confirm to user
            await self._send_success("DJ mode deactivated - Manual control restored")
            
        except Exception as e:
            self.logger.error(f"Error stopping DJ mode: {e}")
            await self._send_error(f"Error stopping DJ mode: {str(e)}")

    async def _handle_dj_next_command(self) -> None:
        """Handle the 'dj next' CLI command to skip to the next track"""
        try:
            # Check if DJ mode is active
            if not self.dj_mode_active:
                await self._send_error("DJ mode is not active")
                return
                
            # Emit DJ_NEXT_TRACK event for BrainService to handle
            await self.emit(EventTopics.DJ_NEXT_TRACK, {})
            
            # Confirm to user
            await self._send_success("Skipping to next track...")
            
        except Exception as e:
            self.logger.error(f"Error handling next track command: {e}")
            await self._send_error(f"Error skipping track: {str(e)}")

    async def _handle_dj_queue_command(self, track_query: str) -> None:
        """Handle the 'dj queue' CLI command to queue a specific track
        
        Args:
            track_query: Search query for the track to queue
        """
        try:
            # Check if DJ mode is active
            if not self.dj_mode_active:
                await self._send_error("DJ mode is not active")
                return
                
            # Find the track by name
            available_tracks = list(self.tracks.values())
            if not available_tracks:
                await self._send_error("No tracks available")
                return
                
            # Try to find a matching track
            selected_track = None
            
            # First try exact match
            for track in available_tracks:
                if track.name.lower() == track_query.lower():
                    selected_track = track
                    break
                
            # If no exact match, try to find a track containing the query
            if not selected_track:
                for track in available_tracks:
                    if track_query.lower() in track.name.lower():
                        selected_track = track
                        break
            
            # If still no match, try matching by track number
            if not selected_track:
                try:
                    track_num = int(track_query)
                    if 1 <= track_num <= len(available_tracks):
                        # Convert to 0-based index
                        selected_track = available_tracks[track_num - 1]
                except (ValueError, IndexError):
                    pass
                
            # If we found a track, queue it
            if selected_track:
                # Emit DJ_TRACK_QUEUED event
                await self.emit(
                    EventTopics.DJ_TRACK_QUEUED,
                    {
                        "track_name": selected_track.name,
                        "track_path": selected_track.path
                    }
                )
                
                # Also set in memory
                await self.emit(
                    EventTopics.MEMORY_SET,
                    {
                        "key": "dj_next_track",
                        "value": selected_track.name
                    }
                )
                
                # Preload the track for faster transitions if supported
                if hasattr(self, 'next_track') and self.current_track:
                    self.next_track = selected_track
                    self.logger.info(f"Preloaded next track: {selected_track.name}")
                
                # Confirm to user
                await self._send_success(f"Queued '{selected_track.name}' as the next track")
            else:
                await self._send_error(f"Could not find a track matching '{track_query}'")
            
        except Exception as e:
            self.logger.error(f"Error queueing track: {e}")
            await self._send_error(f"Error queueing track: {str(e)}")

    async def play_track_by_name(self, track_name: str, source: str = "api") -> None:
        """
        Public method to play a track by name.
        This delegates to the private _play_track_by_name method.
        
        Args:
            track_name: Name of the track to play
            source: Source of the play request (e.g., 'api', 'dj', 'cli')
        """
        await self._play_track_by_name(track_name, source=source)