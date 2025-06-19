"""
Local Music Provider for CantinaOS

This provider manages local music files and integrates with the existing
MusicControllerService for playback operations. It handles music library
scanning, search functionality, and metadata management for local files.

The LocalMusicProvider focuses on discovery and organization while delegating
actual audio playback to the MusicControllerService to avoid duplication.
"""

import os
import glob
import time
import asyncio
from typing import List, Optional, Dict, Any, TYPE_CHECKING
from pydantic import Field
from pyee.asyncio import AsyncIOEventEmitter

from .base_provider import MusicProvider, ProviderConfig
from .models import Track, TrackSearchResult
from ....core.event_topics import EventTopics

if TYPE_CHECKING:
    from ....models.music_models import MusicTrack


class LocalMusicConfig(ProviderConfig):
    """
    Configuration for the local music provider.

    Extends base provider config with local-specific settings.
    """

    music_directory: str = Field(
        default="./music", description="Directory containing local music files"
    )
    supported_formats: List[str] = Field(
        default=[".mp3", ".wav", ".m4a", ".flac"],
        description="Supported audio file formats",
    )
    recursive_scan: bool = Field(
        default=True, description="Whether to scan subdirectories"
    )
    cache_metadata: bool = Field(
        default=True, description="Whether to cache file metadata"
    )
    auto_refresh_minutes: int = Field(
        default=60,
        description="Auto-refresh library interval in minutes (0 to disable)",
    )


class LocalMusicProvider(MusicProvider):
    """
    Local music file provider.

    This provider manages local music files and integrates with the existing
    MusicControllerService architecture. It handles:

    - Music library scanning and indexing
    - Fuzzy search across local tracks
    - Metadata extraction and caching
    - Integration with MusicControllerService for playback
    - Automatic library refresh

    The provider delegates actual audio playback to MusicControllerService
    to maintain compatibility with existing DJ mode, crossfading, and
    audio ducking functionality.
    """

    def __init__(self, config: Dict[str, Any], event_bus: AsyncIOEventEmitter, music_controller=None):
        """
        Initialize the local music provider.

        Args:
            config: Provider configuration dictionary
            event_bus: Event bus for service communication
            music_controller: MusicControllerService instance for direct method calls
        """
        # Create typed config
        local_config = LocalMusicConfig(provider_name="local", **config)

        super().__init__(local_config)

        self.config: LocalMusicConfig = local_config
        self.event_bus = event_bus
        self.music_controller = music_controller  # Store reference for direct calls
        self._metadata_cache: Dict[str, Dict[str, Any]] = {}
        self._last_scan_time: Optional[float] = None
        self._auto_refresh_task: Optional[asyncio.Task] = None

        # Track MusicControllerService state
        self._music_controller_available = False
        self._current_music_track: Optional["MusicTrack"] = None

    async def initialize(self) -> bool:
        """
        Initialize the local music provider.

        Performs:
        - Directory validation
        - Initial library scan
        - Event subscription setup
        - Auto-refresh task creation

        Returns:
            bool: True if initialization succeeded
        """
        try:
            self.logger.info(
                f"Initializing local music provider: {self.config.music_directory}"
            )

            # Resolve and validate music directory
            music_dir = self._resolve_music_directory()
            if not music_dir:
                self.logger.error("Could not find valid music directory")
                return False

            self.config.music_directory = music_dir
            self.logger.info(f"Using music directory: {music_dir}")

            # Subscribe to events for integration with MusicControllerService
            await self._setup_event_subscriptions()

            # Perform initial library scan
            await self._scan_library()

            # Start auto-refresh if enabled
            if self.config.auto_refresh_minutes > 0:
                self._start_auto_refresh()

            # Mark as initialized and available
            self._mark_initialized(True)
            self._mark_available(True)

            self.logger.info(
                f"Local music provider initialized: {len(self._library)} tracks"
            )
            return True

        except Exception as e:
            self.logger.error(f"Failed to initialize local music provider: {e}")
            self._mark_initialized(False)
            self._mark_available(False)
            return False

    async def search(self, query: str) -> TrackSearchResult:
        """
        Search local tracks matching the query.

        Performs fuzzy matching against track metadata with scoring.

        Args:
            query: Search query string

        Returns:
            TrackSearchResult: Search results with metadata
        """
        start_time = time.time()

        try:
            matching_tracks = []
            query_lower = query.lower()

            # Search through all tracks
            for track in self._library:
                score = self._calculate_match_score(track, query_lower)
                if score > 0:
                    matching_tracks.append((track, score))

            # Sort by score (highest first)
            matching_tracks.sort(key=lambda x: x[1], reverse=True)
            tracks = [track for track, score in matching_tracks]

            # Calculate search duration
            duration_ms = int((time.time() - start_time) * 1000)

            return TrackSearchResult(
                tracks=tracks,
                query=query,
                provider=self.name,
                total_results=len(tracks),
                search_duration_ms=duration_ms,
            )

        except Exception as e:
            self.logger.error(f"Search failed: {e}")
            return TrackSearchResult(
                tracks=[], query=query, provider=self.name, total_results=0
            )

    async def play_track(self, track_id: str) -> bool:
        """
        Play a track by delegating to MusicControllerService.

        Args:
            track_id: Unique identifier for the track to play

        Returns:
            bool: True if playback started successfully
        """
        try:
            track = await self.get_track_by_id(track_id)
            if not track:
                self.logger.error(f"Track not found: {track_id}")
                return False

            # Check if MusicControllerService is available
            if not self.music_controller:
                self.logger.error("MusicControllerService not available for direct calls")
                return False

            # Use direct method call instead of event emission
            success = await self.music_controller.play_track_by_name(track.title, source="local_provider")
            
            if success:
                self.logger.info(f"Successfully started playback of track: {track.title}")
                # Update current track reference
                self._current_track = track
                return True
            else:
                self.logger.error(f"Failed to start playback of track: {track.title}")
                return False

        except Exception as e:
            self.logger.error(f"Failed to play track {track_id}: {e}")
            return False

    async def stop(self) -> bool:
        """
        Stop playback by delegating to MusicControllerService.

        Returns:
            bool: True if stop command was sent successfully
        """
        try:
            # Check if MusicControllerService is available
            if not self.music_controller:
                self.logger.error("MusicControllerService not available for direct calls")
                return False

            # Use direct method call instead of event emission
            success = await self.music_controller.stop_playback()
            if not success:
                self.logger.error("Failed to stop playback via direct method call")
                return False
            self.logger.info("Successfully requested music stop")

            self._current_track = None
            return True

        except Exception as e:
            self.logger.error(f"Failed to stop playback: {e}")
            return False

    async def get_current_track(self) -> Optional[Track]:
        """
        Get the currently playing track.

        Returns:
            Optional[Track]: Currently playing track or None
        """
        return self._current_track

    async def is_provider_available(self) -> bool:
        """
        Check if the local provider is available.

        Verifies:
        - Music directory exists and is readable
        - At least one track is available

        Returns:
            bool: True if provider is available
        """
        try:
            if not os.path.exists(self.config.music_directory):
                return False

            if not os.access(self.config.music_directory, os.R_OK):
                return False

            # Check if we have any tracks
            return len(self._library) > 0

        except Exception as e:
            self.logger.error(f"Availability check failed: {e}")
            return False

    async def get_library(self) -> List[Track]:
        """
        Get all local tracks.

        Returns cached library or performs fresh scan if needed.

        Returns:
            List[Track]: All available local tracks
        """
        # Return cached library if available and recent
        if self._library and self._last_scan_time:
            age_minutes = (time.time() - self._last_scan_time) / 60
            if age_minutes < self.config.auto_refresh_minutes:
                return self._library.copy()

        # Perform fresh scan
        await self._scan_library()
        return self._library.copy()

    def _resolve_music_directory(self) -> Optional[str]:
        """
        Resolve the music directory path using multiple search strategies.

        Returns:
            Optional[str]: Absolute path to music directory if found
        """
        base_path = self.config.music_directory

        # Check if path is already absolute and exists
        if os.path.isabs(base_path) and os.path.exists(base_path):
            return base_path

        # Check relative to current working directory
        if os.path.exists(base_path):
            return os.path.abspath(base_path)

        # Get the project root (several levels up from this file)
        current_file = os.path.abspath(__file__)
        # providers/ -> music_source_manager_service/ -> services/ -> cantina_os/ -> cantina_os/
        cantina_os_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.dirname(current_file)))
        )
        project_root = os.path.dirname(cantina_os_root)

        # Try standard locations relative to project root
        potential_dirs = [
            os.path.join(project_root, "audio", "music"),
            os.path.join(project_root, "assets", "music"),
            os.path.join(project_root, "assets", "audio", "music"),
            os.path.join(project_root, "music"),
            os.path.join(cantina_os_root, "assets", "music"),
            os.path.join(cantina_os_root, base_path.lstrip("./")),
        ]

        # Check each potential directory
        for potential_dir in potential_dirs:
            if os.path.exists(potential_dir):
                self.logger.info(f"Found music directory: {potential_dir}")
                return potential_dir

        self.logger.warning(f"Could not resolve music directory: {base_path}")
        return None

    async def _scan_library(self) -> None:
        """
        Scan the music directory and build track library.
        """
        try:
            self.logger.info(f"Scanning music library: {self.config.music_directory}")
            start_time = time.time()

            tracks = []
            scanned_files = 0

            # Build file pattern
            if self.config.recursive_scan:
                patterns = [
                    os.path.join(self.config.music_directory, "**", f"*{ext}")
                    for ext in self.config.supported_formats
                ]
            else:
                patterns = [
                    os.path.join(self.config.music_directory, f"*{ext}")
                    for ext in self.config.supported_formats
                ]

            # Scan files
            for pattern in patterns:
                for filepath in glob.glob(
                    pattern, recursive=self.config.recursive_scan
                ):
                    try:
                        track = await self._create_track_from_file(filepath)
                        if track:
                            tracks.append(track)
                        scanned_files += 1
                    except Exception as e:
                        self.logger.warning(f"Error processing file {filepath}: {e}")

            # Update library
            self._library = tracks
            self._last_scan_time = time.time()

            # Rebuild cache
            self._library_cache.clear()
            for track in tracks:
                self._library_cache[track.track_id] = track

            scan_duration = time.time() - start_time
            self.logger.info(
                f"Library scan complete: {len(tracks)} tracks from {scanned_files} files "
                f"in {scan_duration:.2f}s"
            )

        except Exception as e:
            self.logger.error(f"Library scan failed: {e}")

    async def _create_track_from_file(self, filepath: str) -> Optional[Track]:
        """
        Create a Track from a local music file.

        Args:
            filepath: Path to the music file

        Returns:
            Optional[Track]: Track object or None if creation failed
        """
        try:
            filename = os.path.basename(filepath)
            name, ext = os.path.splitext(filename)

            # Parse metadata from filename
            artist, title = self._parse_filename_metadata(name)

            # Get cached metadata if available
            metadata = self._metadata_cache.get(filepath, {})

            # Create track
            track = Track(
                track_id=name,  # Use filename (without extension) as ID
                title=title,
                artist=artist,
                album=metadata.get("album"),
                duration=metadata.get("duration"),
                provider=self.name,
                source_path=os.path.abspath(filepath),
                metadata={
                    "filename": filename,
                    "extension": ext,
                    "file_size": os.path.getsize(filepath),
                    "modified_time": os.path.getmtime(filepath),
                },
            )

            return track

        except Exception as e:
            self.logger.error(f"Failed to create track from file {filepath}: {e}")
            return None

    def _parse_filename_metadata(self, filename: str) -> tuple[str, str]:
        """
        Parse artist and title from filename.

        Supports formats like:
        - "Artist - Title"
        - "Title"

        Args:
            filename: Filename without extension

        Returns:
            tuple[str, str]: (artist, title)
        """
        # Check for "Artist - Title" format
        if " - " in filename:
            parts = filename.split(" - ", 1)
            return parts[0].strip(), parts[1].strip()

        # Default to "Cantina Band" for title-only files
        return "Cantina Band", filename.strip()

    def _calculate_match_score(self, track: Track, query_lower: str) -> int:
        """
        Calculate match score for a track against a query.

        Higher scores indicate better matches.

        Args:
            track: Track to score
            query_lower: Lowercase search query

        Returns:
            int: Match score (0 = no match, higher = better match)
        """
        score = 0

        # Exact title match (highest score)
        if query_lower == track.title.lower():
            score += 100

        # Title contains query
        elif query_lower in track.title.lower():
            # Higher score for matches at the beginning
            if track.title.lower().startswith(query_lower):
                score += 80
            else:
                score += 60

        # Artist exact match
        if query_lower == track.artist.lower():
            score += 90

        # Artist contains query
        elif query_lower in track.artist.lower():
            if track.artist.lower().startswith(query_lower):
                score += 70
            else:
                score += 50

        # Album match (if available)
        if track.album and query_lower in track.album.lower():
            score += 30

        # Combined "artist - title" match
        combined = f"{track.artist} - {track.title}".lower()
        if query_lower in combined:
            score += 40

        # Word boundary matches (higher relevance)
        words = query_lower.split()
        for word in words:
            if word in track.title.lower().split():
                score += 20
            if word in track.artist.lower().split():
                score += 15

        return score

    async def _setup_event_subscriptions(self) -> None:
        """
        Set up event subscriptions for MusicControllerService integration.
        """
        try:
            # Subscribe to music playback events to track state
            self.event_bus.on(
                EventTopics.MUSIC_PLAYBACK_STARTED, self._handle_playback_started
            )
            self.event_bus.on(
                EventTopics.MUSIC_PLAYBACK_STOPPED, self._handle_playback_stopped
            )

            self.logger.debug("Event subscriptions set up")

        except Exception as e:
            self.logger.error(f"Failed to set up event subscriptions: {e}")

    async def _handle_playback_started(self, payload: Any) -> None:
        """
        Handle music playback started event.

        Args:
            payload: Event payload
        """
        try:
            if isinstance(payload, dict) and "track" in payload:
                track_data = payload["track"]
                # Update current track if it matches one of ours
                for track in self._library:
                    if track.title == track_data.get(
                        "title"
                    ) or track.source_path == track_data.get("filepath"):
                        self._current_track = track
                        break

        except Exception as e:
            self.logger.debug(f"Error handling playback started: {e}")

    async def _handle_playback_stopped(self, payload: Any) -> None:
        """
        Handle music playback stopped event.

        Args:
            payload: Event payload
        """
        self._current_track = None

    def _start_auto_refresh(self) -> None:
        """
        Start the auto-refresh background task.
        """
        if self._auto_refresh_task:
            return

        async def auto_refresh_loop():
            while True:
                try:
                    await asyncio.sleep(self.config.auto_refresh_minutes * 60)
                    await self._scan_library()
                    self.logger.debug("Auto-refresh completed")
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    self.logger.error(f"Auto-refresh failed: {e}")

        self._auto_refresh_task = asyncio.create_task(auto_refresh_loop())
        self.logger.info(
            f"Auto-refresh started: every {self.config.auto_refresh_minutes} minutes"
        )

    async def cleanup(self) -> None:
        """
        Clean up local provider resources.
        """
        # Cancel auto-refresh task
        if self._auto_refresh_task:
            self._auto_refresh_task.cancel()
            try:
                await self._auto_refresh_task
            except asyncio.CancelledError:
                pass
            self._auto_refresh_task = None

        # Clear caches
        self._metadata_cache.clear()

        # Call parent cleanup
        await super().cleanup()

        self.logger.info("Local music provider cleaned up")
