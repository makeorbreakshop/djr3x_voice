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
import random
import re
from difflib import SequenceMatcher

# Suppress VLC verbose logging to prevent Core Audio property listener errors
# from flooding the console output
os.environ['VLC_VERBOSE'] = '-1'  # Suppress all VLC logging
# NOTE: Do NOT set VLC_PLUGIN_PATH to empty string - it breaks VLC initialization!

from cantina_os.base_service import BaseService
from cantina_os.tap import fixtures as tap_fixtures
from cantina_os.core.event_topics import EventTopics
from cantina_os.event_payloads import (
    MusicCommandPayload,
    BaseEventPayload,
    ServiceStatusPayload,
    ServiceStatus,
    SystemModePayload,
    LogLevel,
    StandardCommandPayload,
    DJModeChangedPayload,
    MusicSourceChangedPayload
)
from cantina_os.models.music_models import MusicTrack, MusicLibrary
from cantina_os.core.music_search import (
    looks_like_semantic_music_request,
    parse_semantic_request,
)
from cantina_os.utils.command_decorators import compound_command, register_service_commands, validate_compound_command, command_error_handler

# Import necessary Pydantic models from event_schemas
from cantina_os.core.event_schemas import (
    TrackDataPayload,
    TrackEndingSoonPayload,
    CrossfadeCompletePayload # Assuming this will be defined or updated
)

# Import music backends
from .music_backends import MusicBackend, LocalMusicBackend, SpotifyMusicBackend
from .semantic_music_search import SemanticMusicSearch
from .beat_analysis import BackgroundBeatAnalyzer, BeatCache, BeatInfo

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

    # Spotify configuration
    enable_spotify: bool = Field(default=False, description="Enable Spotify integration")
    spotify_client_id: Optional[str] = Field(default=None, description="Spotify application client ID")
    spotify_client_secret: Optional[str] = Field(default=None, description="Spotify application client secret")
    spotify_redirect_uri: str = Field(default="http://127.0.0.1:8888", description="OAuth redirect URI")
    spotify_device_name: Optional[str] = Field(default=None, description="Preferred Spotify device name")
    default_source: str = Field(default="local", description="Default music source: 'local' or 'spotify'")

    # Local semantic search configuration
    # Main enables this by default from ENABLE_SEMANTIC_MUSIC_SEARCH. Keeping the class-level
    # fallback off prevents isolated service tests and embedded consumers from downloading a
    # model merely because they constructed MusicControllerService directly.
    enable_semantic_search: bool = Field(default=False, description="Enable CLAP search over local audio")
    semantic_model: str = Field(default="laion/clap-htsat-unfused", description="Hugging Face CLAP model")
    semantic_device: str = Field(default="cpu", description="Torch device for CLAP text/audio encoding")
    semantic_cache_path: Optional[str] = Field(default=None, description="Persistent local embedding cache")
    semantic_negative_weight: float = Field(default=0.5, ge=0.0, le=2.0)

    # Offline beat analysis (beat_analysis.py): tempo for beat clocks, the chest and the sim.
    # Off at class level for the same reason as semantic search - a directly constructed
    # service in a test must not spawn an analysis process. Main turns it on
    # (ENABLE_BEAT_ANALYSIS, default true).
    enable_beat_analysis: bool = Field(default=False, description="Attach cached bpm and analyse new local tracks in the background")
    beat_cache_dir: Optional[str] = Field(default=None, description="Beat cache directory (default ~/.cache/dj-r3x/beats)")

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

        # Backend system for multiple music sources
        self.backends: Dict[str, Any] = {}  # Will hold LocalMusicBackend, SpotifyMusicBackend
        self.active_source = self._config.default_source  # "local" or "spotify"
        self.libraries: Dict[str, Dict[str, MusicTrack]] = {
            "local": {},
            "spotify": {}
        }
        self._semantic_search: Optional[SemanticMusicSearch] = None
        self._semantic_search_task: Optional[asyncio.Task[None]] = None
        self._last_semantic_candidates: List[str] = []
        self._beat_cache = BeatCache(self._config.beat_cache_dir)
        self._beat_task: Optional[asyncio.Task[None]] = None
        
        # Create VLC instance with proper configuration to reduce verbose logging
        # and prevent Core Audio property listener errors
        vlc_args = [
            '--intf', 'dummy',           # No interface
            '--extraintf', '',           # No extra interfaces
            '--quiet',                   # Reduce log output
            '--no-video',                # Audio only
            '--aout', 'auhal',           # Use auhal directly
            '--no-audio-time-stretch',   # Disable time stretching
            '--no-plugins-cache',        # Don't cache plugins
            '--verbose', '0'             # Minimal verbosity
        ]
        self.vlc_instance = vlc.Instance(vlc_args)
        
        # Set default command topic for auto-registration
        self._default_command_topic = EventTopics.MUSIC_COMMAND
        
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
        
        # Add direct audio ducking event subscriptions
        await self.subscribe(EventTopics.AUDIO_DUCKING_START, self._handle_audio_ducking_start)
        self.logger.debug("Subscribed to AUDIO_DUCKING_START events")
        
        await self.subscribe(EventTopics.AUDIO_DUCKING_STOP, self._handle_audio_ducking_stop)
        self.logger.debug("Subscribed to AUDIO_DUCKING_STOP events")

        # Add DJ mode events
        await self.subscribe(EventTopics.DJ_MODE_CHANGED, self._handle_dj_mode_changed)
        self.logger.debug("Subscribed to DJ_MODE_CHANGED events")

        await self.subscribe(EventTopics.DJ_NEXT_TRACK, self._handle_dj_next_track)

        # FIX 1: Subscribe to cached speech completion for immediate unduck
        await self.subscribe(EventTopics.SPEECH_CACHE_PLAYBACK_COMPLETED, self._handle_cached_speech_completed)
        self.logger.debug("Subscribed to SPEECH_CACHE_PLAYBACK_COMPLETED events")
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

        # Initialize music backends
        await self._initialize_backends()

        # Load the music library
        self.logger.debug("Loading music library")
        await self._load_music_library()

        # Warm CLAP as soon as the local library exists. CantinaOS awaits this task before the
        # startup chime, so the chime is a true ready signal and cannot crackle under model load.
        self._start_semantic_search_initialization()

        # Auto-register compound commands using decorators
        register_service_commands(self, self._event_bus)
        self.logger.info("Auto-registered music commands using decorators")

        # Set service status to running
        total_tracks = sum(len(lib) for lib in self.libraries.values())
        self.logger.info(f"Music controller started with {total_tracks} tracks loaded across {len(self.backends)} source(s)")
        await self._emit_status(ServiceStatus.RUNNING, "Music controller started")
        
    async def stop(self):
        """Stop the music controller service and cleanup resources."""
        try:
            beat_task = getattr(self, "_beat_task", None)
            if beat_task is not None and not beat_task.done():
                beat_task.cancel()  # kills the worker process (BackgroundBeatAnalyzer.run)
                try:
                    await beat_task
                except (asyncio.CancelledError, Exception):
                    pass
            self._beat_task = None

            semantic_task = getattr(self, "_semantic_search_task", None)
            if semantic_task is not None:
                if not semantic_task.done():
                    semantic_task.cancel()
                try:
                    await semantic_task
                except asyncio.CancelledError:
                    pass
                self._semantic_search_task = None

            if self._semantic_search is not None:
                await asyncio.to_thread(self._semantic_search.close)

            # Cancel track end timer first
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

    # ------------------------------------------------------------------
    # Beat analysis (tempo for show beat clocks, chest and sim; see beat_analysis.py)
    # ------------------------------------------------------------------
    def _local_tracks_by_path(self) -> Dict[str, MusicTrack]:
        return {os.path.abspath(t.path): t for t in self.libraries.get("local", {}).values() if t.path}

    def _apply_beat_info(self, path: str, info: BeatInfo) -> None:
        track = self._local_tracks_by_path().get(os.path.abspath(path))
        if track is None:
            return
        track.bpm = info.bpm
        track.first_beat_s = info.first_beat_s
        self.logger.debug(f"Tempo for {track.name}: {info.bpm} bpm (first beat {info.first_beat_s}s)")

    def _beats_enabled(self) -> bool:
        config = getattr(self, "_config", None)  # absent on __new__-built test doubles
        return bool(config is not None and config.enable_beat_analysis)

    def _attach_cached_beats(self) -> None:
        """Attach every still-valid cached tempo. Fail-open: a miss is simply no bpm."""
        if not self._beats_enabled():
            return
        attached = 0
        for path, track in self._local_tracks_by_path().items():
            info = self._beat_cache.get(path)
            if info is not None:
                track.bpm, track.first_beat_s = info.bpm, info.first_beat_s
                attached += 1
        self.logger.info(f"Beat cache: {attached}/{len(self.libraries.get('local', {}))} local track(s) have a tempo")

    def _start_beat_analysis(self) -> None:
        """Analyse unknown tracks in a background process; never on the playback path."""
        if not self._beats_enabled():
            return
        if getattr(self, "_beat_task", None) is not None and not self._beat_task.done():
            return  # already running; a reload's new files are picked up next start
        paths = list(self._local_tracks_by_path())
        analyzer = BackgroundBeatAnalyzer(self._beat_cache, logger_=self.logger)
        if not analyzer.pending(paths):
            return
        try:
            self._beat_task = asyncio.get_running_loop().create_task(
                analyzer.run(paths, self._apply_beat_info), name="beat-analysis"
            )
        except RuntimeError:
            self._beat_task = None  # no running loop (sync caller): cached tempos only

    def _start_semantic_search_initialization(self) -> None:
        """Schedule semantic initialization without delaying service startup."""
        if not self._config.enable_semantic_search:
            self.logger.info("Local semantic music search disabled")
            return
        semantic_search = getattr(self, "_semantic_search", None)
        if semantic_search is not None and semantic_search.ready:
            return
        existing_task = getattr(self, "_semantic_search_task", None)
        if existing_task is not None and not existing_task.done():
            return
        self._semantic_search_task = asyncio.create_task(
            self._initialize_semantic_search(),
            name="semantic-music-initialization",
        )
        self.logger.info("Semantic music search warming in background")

    async def wait_until_ready(self) -> None:
        """Wait until optional semantic search is ready or has explicitly degraded."""
        task = getattr(self, "_semantic_search_task", None)
        if task is not None:
            await asyncio.shield(task)

    async def _initialize_semantic_search(self) -> None:
        """Load the local CLAP index without blocking the CantinaOS event loop."""
        if not self._config.enable_semantic_search:
            self.logger.info("Local semantic music search disabled")
            return
        search = None
        try:
            search = SemanticMusicSearch(
                model_id=self._config.semantic_model,
                cache_path=self._config.semantic_cache_path,
                device=self._config.semantic_device,
                negative_weight=self._config.semantic_negative_weight,
                logger=self.logger,
            )
            self._semantic_search = search
            metrics = await asyncio.to_thread(search.initialize, self.libraries["local"])
            source = "built" if metrics["indexed"] else "cached"
            self.logger.info(
                "Semantic music search ready: %d tracks, %s index, %.2fs",
                int(metrics["track_count"]),
                source,
                metrics["total_seconds"],
            )
        except Exception as exc:
            if self._semantic_search is search:
                self._semantic_search = None
            self.logger.warning("Semantic music search unavailable: %s", exc)
        
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

    async def _initialize_backends(self):
        """Initialize music playback backends (local and Spotify)."""
        self.logger.info("Initializing music backends...")

        # Always initialize local backend (VLC-based)
        try:
            local_backend = LocalMusicBackend(self.vlc_instance, self.logger)
            await local_backend.initialize()
            self.backends["local"] = local_backend
            self.logger.info("✓ Local music backend initialized")
        except Exception as e:
            self.logger.error(f"Failed to initialize local backend: {e}")

        # Initialize Spotify backend if enabled
        if self._config.enable_spotify:
            try:
                if not self._config.spotify_client_id or not self._config.spotify_client_secret:
                    self.logger.warning("Spotify enabled but credentials not configured")
                else:
                    spotify_config = {
                        "client_id": self._config.spotify_client_id,
                        "client_secret": self._config.spotify_client_secret,
                        "redirect_uri": self._config.spotify_redirect_uri,
                        "device_name": self._config.spotify_device_name
                    }
                    spotify_backend = SpotifyMusicBackend(spotify_config, self.logger)
                    if await spotify_backend.initialize():
                        self.backends["spotify"] = spotify_backend
                        self.logger.info("✓ Spotify backend initialized")

                        # Load Spotify library
                        spotify_library = await spotify_backend.load_library()
                        self.libraries["spotify"] = spotify_library
                        self.logger.info(f"✓ Loaded {len(spotify_library)} Spotify tracks")
                    else:
                        # The backend logs the precise cause (revoked token, missing
                        # device, network failure). Do not obscure it with a second,
                        # speculative warning here.
                        self.logger.debug("Spotify backend was not initialized")
            except Exception as e:
                self.logger.warning(f"Spotify backend initialization failed: {e}")
        else:
            self.logger.debug("Spotify integration disabled")

        # Set active source to default
        if self.active_source not in self.backends:
            self.logger.warning(f"Default source '{self.active_source}' not available, using 'local'")
            self.active_source = "local"

    async def _switch_source(self, new_source: str) -> bool:
        """Switch to different music source."""
        if new_source not in self.backends:
            self.logger.error(f"Music source '{new_source}' not available")
            return False

        # Stop current backend
        old_source = self.active_source
        if old_source in self.backends:
            await self.backends[old_source].stop_playback()

        # Switch
        self.active_source = new_source
        self.tracks = self.libraries[new_source]  # Update tracks alias

        # Emit source changed event
        payload = MusicSourceChangedPayload(
            previous_source=old_source,
            current_source=new_source,
            available_sources=list(self.backends.keys())
        )
        await self.emit(EventTopics.MUSIC_SOURCE_CHANGED, payload.model_dump())

        # Emit library updated event so BrainService gets the new library for DJ mode
        track_data = await self.get_track_list()
        await self.emit(
            EventTopics.MUSIC_LIBRARY_UPDATED,
            {
                "track_count": len(self.tracks),
                "tracks": track_data
            }
        )

        self.logger.info(f"Switched music source: {old_source} → {new_source}")
        return True

    async def _show_source_status(self):
        """Show current music source status."""
        status_lines = [
            f"Current source: {self.active_source}",
            f"Available sources: {', '.join(self.backends.keys())}",
            ""
        ]

        for source_name, library in self.libraries.items():
            status = "✓" if source_name in self.backends else "✗"
            track_count = len(library)
            status_lines.append(f"  [{status}] {source_name}: {track_count} tracks")

        status_msg = "\n".join(status_lines)
        self.logger.info(status_msg)
        await self._send_success(status_msg)

    async def _load_music_library(self) -> int:
        """Load available music tracks from the music directory.

        Returns:
            The number of tracks in the library - which is the number of *playable* tracks,
            not the number of files found. Those differ when filenames parse to the same
            title; see the duplicate warning below.
        """
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
                return 0
            
            # Clear existing local tracks
            self.libraries["local"].clear()

            # Process .mp3, .wav, and .m4a files
            for ext in ['.mp3', '.wav', '.m4a']:
                pattern = os.path.join(self.music_dir, f'*{ext}')
                self.logger.debug(f"Searching for music files with pattern: {pattern}")

                for filepath in sorted(glob.glob(pattern)):
                    try:
                        # Extract filename without extension for display
                        filename = os.path.basename(filepath)

                        # Parse artist and title from filename
                        artist, title = self._parse_track_metadata(filename)

                        # Create media to get duration, safely handled
                        try:
                            media = self.vlc_instance.media_new(filepath)
                            media.parse()
                            duration_ms = media.get_duration()
                            duration = duration_ms / 1000.0 if duration_ms > 0 else None
                        except Exception as e:
                            self.logger.warning(f"Could not get duration for {filepath}: {e}")
                            duration = None

                        # Most commands address tracks by title. If two files share a
                        # title, preserve the plain title for the generic "Cantina Band"
                        # file and use an artist-qualified key for the other recording.
                        library_key = title
                        existing = self.libraries["local"].get(title)
                        if existing is not None:
                            disambiguated_key = library_key
                            if artist == "Cantina Band" and existing.artist != "Cantina Band":
                                existing_key = f"{existing.artist} - {title}"
                                suffix = 2
                                base_key = existing_key
                                while existing_key in self.libraries["local"]:
                                    existing_key = f"{base_key} ({suffix})"
                                    suffix += 1
                                del self.libraries["local"][title]
                                existing.name = existing_key
                                existing.track_id = existing_key
                                self.libraries["local"][existing_key] = existing
                                disambiguated_key = existing_key
                            else:
                                library_key = f"{artist} - {title}"
                                base_key = library_key
                                suffix = 2
                                while library_key in self.libraries["local"]:
                                    library_key = f"{base_key} ({suffix})"
                                    suffix += 1
                                disambiguated_key = library_key

                            self.logger.info(
                                "Disambiguated duplicate track title '%s' as '%s'",
                                title,
                                disambiguated_key,
                            )

                        # Use the unique library key consistently for commands and IDs.
                        abs_path = os.path.abspath(filepath)
                        track = MusicTrack(
                            name=library_key,
                            path=abs_path,
                            duration=duration,
                            track_id=library_key,
                            title=title,
                            artist=artist,
                            provider="local"
                        )

                        self.libraries["local"][library_key] = track
                        music_files_count += 1

                        self.logger.debug(f"Loaded track: {title} by {artist} ({abs_path}), duration: {duration}s")
                    except Exception as e:
                        self.logger.error(f"Error loading music track {filepath}: {e}")

            # Update tracks alias to point to active source library
            self.tracks = self.libraries[self.active_source]

            track_count = len(self.libraries["local"])
            self.logger.info(f"Loaded {track_count} tracks from {self.music_dir}")

            # Alert if no music found
            if music_files_count == 0:
                self.logger.warning("No music files found. Music playback will be unavailable.")

            # Tempo: cached results attach now (small JSON reads); anything new or changed is
            # analysed in a background process and attaches as it finishes.
            # Fail-open: a tempo problem must never cost the library.
            try:
                self._attach_cached_beats()
                self._start_beat_analysis()
            except Exception as e:
                self.logger.warning(f"Beat analysis unavailable: {e}")

            # Publish a music library updated event with proper track data
            track_data = await self.get_track_list()
            await self.emit(
                EventTopics.MUSIC_LIBRARY_UPDATED,
                {
                    "track_count": track_count,
                    "tracks": track_data
                }
            )

            return track_count

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
        self.logger.debug(f"Legacy music handler received: {payload}")
        
        # Handle action-based payloads (from other services)
        if isinstance(payload, dict) and "action" in payload:
            if payload["action"] == "play":
                await self._handle_play_request(MusicCommandPayload(**payload))
                return
            elif payload["action"] == "stop":
                await self._handle_stop_request(MusicCommandPayload(**payload))
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
            elif command_pattern == "source music":
                await self.handle_source_music(payload)
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
            self.logger.info(f"Smart track selection for query: '{track_query}'")

            semantic_request = parse_semantic_request(track_query)
            if semantic_request is not None:
                if await self._search_local_semantic_and_play(
                    semantic_request.query,
                    source,
                    negative_query=semantic_request.negative_query,
                ):
                    return
                if await self._search_spotify_catalog_and_play(semantic_request.query, source):
                    return
                await self._send_error(
                    f"No music found matching '{semantic_request.query}'"
                )
                return
            
            # An empty loaded library can still be satisfied by a provider catalog.
            if not self.tracks:
                if await self._search_spotify_catalog_and_play(track_query, source):
                    return
                await self._send_error(
                    f"No music found matching '{track_query}'"
                )
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
                self.logger.info(f"Found exact track name match: {track_query}")
                await self._play_track_by_name(track_query, source)
                return
                
            # Named requests stay deterministic. This includes conservative phonetic tolerance
            # for ordinary STT errors such as "Java" -> "Jawas".
            best_match = self._find_named_track(track_query)
            if best_match:
                self.logger.info(f"Found fuzzy match for '{track_query}': '{best_match}'")
                await self._play_track_by_name(best_match, source)
                return

            # Typed commands and Claude tool calls do not carry Jev's structured marker. Route
            # recognizable mood/style language through the same local semantic index.
            if looks_like_semantic_music_request(track_query):
                if await self._search_local_semantic_and_play(track_query, source):
                    return
                
            # The loaded library has no match. Search the provider catalog rather than
            # silently playing the first unrelated local file.
            if await self._search_spotify_catalog_and_play(track_query, source):
                return

            self.logger.warning("No music found matching %r", track_query)
            await self._send_error(f"No music found matching '{track_query}'")
                
        except Exception as e:
            self.logger.error(f"Error in smart track selection: {e}")
            await self._send_error(f"Error selecting track: {str(e)}")

    @staticmethod
    def _normalized_music_words(value: str) -> List[str]:
        if not isinstance(value, str):
            return []
        return re.findall(r"[a-z0-9]+", value.lower())

    def _find_named_track(self, query: str) -> Optional[str]:
        """Return a confident title/artist match without guessing from musical semantics."""
        query_words = self._normalized_music_words(query)
        if not query_words:
            return None
        normalized_query = " ".join(query_words)
        best_name: Optional[str] = None
        best_score = 0.0

        for name, track in self.tracks.items():
            candidates = [name, track.title or "", track.artist or ""]
            for candidate in candidates:
                candidate_words = self._normalized_music_words(candidate)
                if not candidate_words:
                    continue
                normalized_candidate = " ".join(candidate_words)
                if normalized_query in normalized_candidate:
                    score = 1.0 + len(normalized_query) / max(len(normalized_candidate), 1)
                elif len(query_words) == 1 and len(query_words[0]) >= 4:
                    score = max(
                        SequenceMatcher(None, query_words[0], word).ratio()
                        for word in candidate_words
                    )
                    if score < 0.66:
                        continue
                else:
                    score = SequenceMatcher(None, normalized_query, normalized_candidate).ratio()
                    if score < 0.72:
                        continue
                if score > best_score:
                    best_name = name
                    best_score = score

        return best_name

    async def _search_local_semantic_and_play(
        self,
        query: str,
        source: str,
        *,
        negative_query: Optional[str] = None,
    ) -> bool:
        """Search the cached local CLAP vectors and play the highest-ranked valid track."""
        # A command can arrive while startup is still warming. Waiting here preserves the
        # contract that requests made just before the ready chime still resolve correctly.
        await self.wait_until_ready()
        search = getattr(self, "_semantic_search", None)
        if search is None or not search.ready:
            self.logger.info("Local semantic music search unavailable for %r", query)
            return False

        matches = await asyncio.to_thread(
            search.search,
            query,
            negative_query=negative_query,
            limit=5,
        )
        if not matches:
            return False

        local_library = self.libraries.get("local", {})
        self._last_semantic_candidates = [
            match.track_name for match in matches if match.track_name in local_library
        ]
        current_track = getattr(self, "current_track", None)
        current_name = current_track.name if current_track else None
        winner = next(
            (
                match
                for match in matches
                if match.track_name in local_library and match.track_name != current_name
            ),
            None,
        )
        if winner is None:
            winner = next(
                (match for match in matches if match.track_name in local_library), None
            )
        if winner is None:
            self.logger.warning("Semantic index returned no currently loaded local track")
            return False

        self.logger.info(
            "Semantic music search selected %s for %r (score=%.3f; top=%s)",
            winner.track_name,
            query,
            winner.score,
            ", ".join(f"{match.track_name}:{match.score:.3f}" for match in matches[:3]),
        )
        if self.active_source != "local":
            if not await self._switch_source("local"):
                return False
        else:
            self.tracks = local_library
        await self._play_track_by_name(winner.track_name, source)
        return True

    async def _search_spotify_catalog_and_play(
        self,
        query: str,
        source: str,
    ) -> bool:
        """Search Spotify for ``query`` and play the highest-ranked result."""
        backend = self.backends.get("spotify")
        if backend is None:
            self.logger.info("Spotify catalog search unavailable for %r", query)
            return False

        results = await backend.search_tracks(query, limit=5)
        if not results:
            return False

        track = results[0]
        spotify_library = self.libraries.setdefault("spotify", {})
        spotify_library[track.name] = track

        if self.active_source != "spotify":
            if not await self._switch_source("spotify"):
                return False
        else:
            self.tracks = spotify_library

        self.logger.info(
            "Catalog search selected %s for %r", track.name, query
        )
        await self._play_track_by_name(track.name, source)
        return True

    async def _play_track_by_name(self, track_name: str, source: str = "cli") -> None:
        """
        Play a track by its exact name.
        
        Args:
            track_name: The exact name of the track to play
            source: Source of the play request (cli, voice, dj)
        """
        try:
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
                    self.logger.info(f"Fuzzy matched '{track_name}' to '{best_match}'")
                else:
                    self.logger.warning(f"No track found matching '{track_name}'")
                    await self._send_error(f"No track found matching '{track_name}'")
                    return
            
            # If we already have a track playing and DJ mode is active, crossfade
            if self.player and self.player.is_playing() and self.dj_mode_active:
                await self._crossfade_to_track(track, source=source)
                return
            
            # Otherwise, stop any current playback and play directly
            await self._stop_playback(notify=False)

            # Use the active backend to play the track
            self.logger.info(f"Playing track: {track.name} ({track.path})")
            backend = self.backends.get(self.active_source)

            if not backend:
                self.logger.error(f"No backend available for source: {self.active_source}")
                await self._send_error(f"Music source '{self.active_source}' not available")
                return

            # Play track using backend
            success = await backend.play_track(track)

            if not success:
                self.logger.error(f"Failed to play track: {track.name}")
                await self._send_error(f"Failed to play track: {track.name}")
                return

            # Update current track
            self.current_track = track

            # Set volume based on current state (for VLC backend)
            if self.active_source == "local":
                volume = self.ducking_volume if self.is_ducking else self.normal_volume
                await backend.set_volume(volume)
            
            # Emit MUSIC_PLAYBACK_STARTED event with track data
            track_data = self._create_track_data_payload(track)
            await self.emit(
                EventTopics.MUSIC_PLAYBACK_STARTED,
                {
                    "track": track_data.model_dump(),
                    "source": source,
                    "mode": self.current_mode
                }
            )
            
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

    async def _stop_playback(self, *, notify: bool = True) -> None:
        """Stop music playback through the active backend.

        This used to gate on ``self.player`` and drive VLC directly. Playback moved to the
        pluggable backends (``LocalMusicBackend`` owns its own VLC player), and nothing sets
        ``self.player`` any more - so the gate was always true and *every* stop returned
        "No music is currently playing" without touching the audio. Found by
        ``scripts/claude_live_verify.py``: Claude's ``stop_music`` tool call arrived, reached
        MUSIC_COMMAND, and the track kept playing.

        ``self.current_track`` is the service's own record of what is playing, so it is the
        correct gate. ``self.player`` is still cleaned up for the crossfade path, which does
        assign it.
        """
        try:
            if not self.current_track and not self.player:
                if notify:
                    await self.emit(
                        EventTopics.MUSIC_PLAYBACK_STOPPED,
                        {"track_name": None, "already_stopped": True},
                    )
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

            # Stop through the backend that actually owns the playback.
            backend = self.backends.get(self.active_source)
            if backend:
                try:
                    await backend.stop_playback()
                except Exception as e:
                    self.logger.debug(f"Error stopping {self.active_source} backend: {e}")

            # The crossfade path assigns self.player directly; clean that up too.
            if self.player:
                try:
                    self.player.stop()
                    await asyncio.sleep(0.1)
                except Exception as e:
                    self.logger.debug(f"Error stopping VLC player: {e}")
                await self._cleanup_player(self.player)
            self.player = None
            self.current_track = None
            
            # Reset audio ducking state
            self.is_ducking = False
            
            # Emit stopped event
            await self.emit(
                EventTopics.MUSIC_PLAYBACK_STOPPED,
                {
                    "track_name": track_name
                }
            )
            
            # Emit simple coordination event for timeline services
            await self.emit(EventTopics.TRACK_STOPPED, {})
            
            if notify:
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
    # min_args=0 since 2026-09-17: "play some music" names no track, and neither does typing
    # the `p` shortcut at the prompt. Requiring one argument meant the fast router's generic
    # dispatch was rejected at the CLI arg check after landing in 483 ms - the same class of
    # bug as the `eye pattern` arg-count rejection, and again only visible in
    # scripts/system_smoke_run.py, because the command was emitted correctly and discarded
    # afterwards.
    @validate_compound_command(min_args=0, required_args=["track_name"])
    @command_error_handler
    async def handle_play_music(self, payload: dict) -> None:
        """Handle 'play music [track]' command - plays the named track, or any track."""
        args = payload.get("args", [])
        track_query = " ".join(args).strip()

        if not track_query:
            await self._play_any_track()
            return

        self.logger.info(f"Playing music track: {track_query}")
        await self._smart_play_track(track_query)

    async def _play_any_track(self, source: str = "cli") -> None:
        """Play something. For when the request names no track.

        Random rather than "the first one": a DJ asked for "some music" twice in a row should
        not play Bai Tee Tee both times. Whichever track starts is reported back on
        MUSIC_PLAYBACK_STARTED, which is what the spoken confirmation is built from, so the
        randomness cannot desynchronise what R3X says from what is playing.
        """
        if not self.tracks:
            await self._send_error("No music tracks available. Please install music first.")
            return

        choices = list(self.tracks.keys())
        current_track = getattr(self, "current_track", None)
        if current_track and len(choices) > 1:
            choices = [name for name in choices if name != current_track.name]
        track_name = tap_fixtures.choice("music.random_track", choices)
        self.logger.info(f"No track named; playing {track_name}")
        await self._play_track_by_name(track_name, source)

    @compound_command("stop music")
    @command_error_handler
    async def handle_stop_music(self, payload: dict) -> None:
        """Handle 'stop music' command - stops music playback."""
        self.logger.info("Stopping music playback")
        await self._stop_playback()

    @compound_command("next music")
    @command_error_handler
    async def handle_next_music(self, payload: dict) -> None:
        """Play the next ranked semantic result, or the next local library track."""
        current_name = self.current_track.name if self.current_track else None
        candidates = [
            name
            for name in getattr(self, "_last_semantic_candidates", [])
            if name in self.tracks and name != current_name
        ]
        if not candidates:
            names = list(self.tracks)
            if not names:
                await self._send_error("No music tracks available. Please install music first.")
                return
            if current_name in names and len(names) > 1:
                index = (names.index(current_name) + 1) % len(names)
                candidates = [names[index]]
            else:
                candidates = [next((name for name in names if name != current_name), names[0])]
        await self._play_track_by_name(candidates[0], "cli")

    @compound_command("source music")
    @command_error_handler
    async def handle_source_music(self, payload: dict) -> None:
        """Handle 'source music [local|spotify|status]' command - switch music source or show status."""
        args = payload.get("args", [])

        if not args or args[0] == "status":
            # Show current source and available sources
            await self._show_source_status()
        else:
            source = args[0].lower()
            if source in ["local", "spotify"]:
                success = await self._switch_source(source)
                if success:
                    total_tracks = len(self.tracks)
                    await self._send_success(f"Switched to {source} source ({total_tracks} tracks available)")
                else:
                    await self._send_error(f"Failed to switch to {source} source (not available)")
            else:
                await self._send_error(f"Invalid source '{source}'. Use: local, spotify, or status")

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
            self.logger.info(f"Handling play music request for query: {song_query}")
            
            # Check if this is from a conversation (voice request)
            source = "voice" if payload.conversation_id else "cli"
            
            # Pass the source to ensure voice requests get proper handling
            await self._smart_play_track(song_query, source=source)
            
        except Exception as e:
            self.logger.error(f"Error handling play music request: {e}")
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

    async def _handle_audio_ducking_start(self, payload: BaseEventPayload):
        """Handle audio ducking start - reduce music volume."""
        if (self.current_mode == "INTERACTIVE" or self.dj_mode_active):
            self.is_ducking = True
            # Use backend abstraction for cross-platform support (VLC/Spotify)
            backend = self.backends.get(self.active_source)
            if backend:
                await backend.set_volume(self.ducking_volume)
                self.logger.debug(f"Music ducked to volume {self.ducking_volume}")

    async def _handle_audio_ducking_stop(self, payload: BaseEventPayload):
        """Handle audio ducking stop - restore music volume."""
        if (self.current_mode == "INTERACTIVE" or self.dj_mode_active):
            self.is_ducking = False
            # Use backend abstraction for cross-platform support (VLC/Spotify)
            backend = self.backends.get(self.active_source)
            if backend:
                await backend.set_volume(self.normal_volume)
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

    async def _handle_cached_speech_completed(self, payload: Dict[str, Any]) -> None:
        """FIX 1: Handle cached speech completion to immediately unduck music.

        This provides instant unduck response when DJ commentary finishes,
        rather than waiting for both speech AND crossfade to complete.
        Event-driven pattern allows crossfade to continue independently.

        Args:
            payload: SPEECH_CACHE_PLAYBACK_COMPLETED event payload
        """
        try:
            # Only unduck if we're in DJ mode and currently ducking
            if self.dj_mode_active and self.is_ducking:
                completion_status = payload.get('completion_status', 'completed')
                cache_key = payload.get('cache_key', 'unknown')

                if completion_status == 'completed':
                    self.logger.info(f"FIX 1: Cached speech completed (cache_key: {cache_key}), unducking music immediately")

                    # Unduck music by restoring volume
                    if self.player:
                        self.is_ducking = False
                        self.player.audio_set_volume(self.normal_volume)
                        self.logger.info(f"FIX 1: Music volume restored to {self.normal_volume}")
                else:
                    self.logger.warning(f"Cached speech completed with status '{completion_status}', not unducking")
        except Exception as e:
            self.logger.error(f"Error handling cached speech completion: {e}")

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
            bpm=track.bpm,
            first_beat_s=track.first_beat_s,
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

            # FIX 3: Set up TRACK_ENDING_SOON timer when track STARTS (during crossfade)
            # Timer should count from when secondary player starts, not when crossfade completes
            if self.dj_mode_active and next_track.duration:
                await self._setup_track_end_timer(next_track.duration)
                self.logger.debug(f"FIX 3: Timer set for next track '{next_track.title}' during crossfade start")

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
            
            # CRITICAL FIX: Check if DJ mode is still active before completing crossfade
            # This prevents music from restarting after "dj stop" command during crossfade
            if source == "dj" and not self.dj_mode_active:
                self.logger.info(f"Crossfade {crossfade_id} cancelled - DJ mode deactivated during crossfade")
                # Stop both players
                if self.player:
                    self.player.stop()
                    await self._cleanup_player(self.player)
                if self.secondary_player:
                    self.secondary_player.stop()
                    await self._cleanup_player(self.secondary_player)

                # Clear state
                self.player = None
                self.secondary_player = None
                self.current_track = None
                self.next_track = None
                self.is_crossfading = False

                # Emit cancelled crossfade event
                await self.emit(
                    EventTopics.CROSSFADE_COMPLETE,
                    {
                        "crossfade_id": crossfade_id,
                        "status": "cancelled_dj_stop",
                        "reason": "DJ mode deactivated"
                    }
                )
                return

            # Clean up old player and update state
            if self.player:
                self.player.stop()
                await self._cleanup_player(self.player)

            # Swap players and update track info
            self.player = self.secondary_player
            self.secondary_player = None
            self.current_track = next_track
            self.next_track = None
            
            # Emit crossfade complete event
            await self.emit(
                EventTopics.CROSSFADE_COMPLETE,
                {
                    "crossfade_id": crossfade_id,
                    "status": "success",
                    "current_track": next_track_data.dict()
                }
            )
            
            # A crossfade starts a new track as surely as play does. Announce it the same way,
            # so every tempo follower (show beat clocks, chest, sim light desk) picks up the
            # new track's bpm; before 2026-09-29 only the first track of a DJ set was announced.
            await self.emit(
                EventTopics.MUSIC_PLAYBACK_STARTED,
                {
                    "track": next_track_data.model_dump(),
                    "source": source,
                    "mode": self.current_mode,
                },
            )

            # Emit simple coordination event for timeline services (new track is now playing)
            await self.emit(EventTopics.TRACK_PLAYING, {})

            # FIX 3: DO NOT set timer here - already set when secondary_player started (line 1319)
            # Removing duplicate timer setup that was causing rapid transitions
            # Old code: if self.dj_mode_active and next_track.duration: await self._setup_track_end_timer(next_track.duration)

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
        """Gets the current playback progress of the current track.

        Returns a dictionary with track information and progress.
        """
        progress_data = {
            "is_playing": False,
            "current_track": None,
            "position_sec": 0.0,
            "duration_sec": 0.0,
            "time_remaining_sec": 0.0
        }

        if self.player and self.player.is_playing():
            progress_data['is_playing'] = True
            progress_data['current_track'] = self.current_track.dict() if self.current_track else None

            # Get player state and position
            # VLC player.get_time() returns milliseconds
            # VLC player.get_length() returns milliseconds
            current_time_ms = self.player.get_time()
            total_length_ms = self.player.get_length()

            if total_length_ms > 0:
                progress_data['position_sec'] = current_time_ms / 1000.0
                progress_data['duration_sec'] = total_length_ms / 1000.0
                progress_data['time_remaining_sec'] = (total_length_ms - current_time_ms) / 1000.0

        # Include information about the next track if preloaded
        if self.next_track:
             progress_data['next_track'] = self.next_track.dict()

        return progress_data

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
                    random_track = tap_fixtures.choice("music.dj_random_track", available_tracks, lambda t: t.name)
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
