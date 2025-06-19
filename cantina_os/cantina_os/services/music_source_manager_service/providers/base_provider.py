"""
Abstract Base Provider for Music Sources

This module defines the abstract interface that all music providers must implement.
It provides a consistent API for music discovery, playback control, and library management
across different sources (local files, Spotify, etc.).

The MusicProvider abstract base class ensures all providers implement the required
methods for seamless integration with the MusicSourceManagerService.
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import List, Optional, Dict
from pydantic import BaseModel, Field

from .models import Track, TrackSearchResult


class ProviderConfig(BaseModel):
    """
    Base configuration model for music providers.

    Each provider can extend this with provider-specific configuration fields.
    """

    provider_name: str = Field(description="Name of the music provider")
    enabled: bool = Field(default=True, description="Whether this provider is enabled")
    timeout_seconds: int = Field(default=30, description="Operation timeout in seconds")
    max_retries: int = Field(
        default=3, description="Maximum retry attempts for failed operations"
    )
    cache_enabled: bool = Field(
        default=True, description="Whether to cache search results and library data"
    )


class ProviderStatus(BaseModel):
    """
    Status information for a music provider.
    """

    provider_name: str = Field(description="Name of the provider")
    is_available: bool = Field(description="Whether provider is available")
    is_initialized: bool = Field(description="Whether provider is initialized")
    library_size: int = Field(default=0, description="Number of tracks in library")
    last_error: Optional[str] = Field(default=None, description="Last error message")
    uptime_seconds: Optional[float] = Field(default=None, description="Provider uptime")


class MusicProvider(ABC):
    """
    Abstract base class for music providers.

    This class defines the interface that all music providers must implement,
    ensuring consistent behavior across different music sources.

    Providers handle:
    - Music library discovery and caching
    - Track search functionality
    - Playback control delegation
    - Provider health monitoring
    - Error handling and retry logic

    Providers do NOT handle:
    - Actual audio playback (delegated to MusicControllerService)
    - Audio processing or effects
    - Hardware audio routing
    """

    def __init__(self, config: ProviderConfig):
        """
        Initialize the music provider.

        Args:
            config: Provider configuration
        """
        self.config = config
        self.logger = logging.getLogger(f"{__name__}.{config.provider_name}")
        self._initialized = False
        self._available = False
        self._library: List[Track] = []
        self._library_cache: Dict[str, Track] = {}
        self._current_track: Optional[Track] = None
        self._start_time: Optional[float] = None

    @property
    def name(self) -> str:
        """Get the provider name."""
        return self.config.provider_name

    @property
    def is_initialized(self) -> bool:
        """Check if the provider is initialized."""
        return self._initialized

    @property
    def is_available(self) -> bool:
        """Check if the provider is available."""
        return self._available

    @abstractmethod
    async def initialize(self) -> bool:
        """
        Initialize the provider.

        Performs any necessary setup operations such as:
        - Validating configuration
        - Establishing connections
        - Loading music library
        - Setting up caches

        Returns:
            bool: True if initialization succeeded, False otherwise
        """
        pass

    @abstractmethod
    async def search(self, query: str) -> TrackSearchResult:
        """
        Search for tracks matching the given query.

        Performs fuzzy matching against track metadata (title, artist, album).
        Results should be sorted by relevance.

        Args:
            query: Search query string

        Returns:
            TrackSearchResult: Search results with metadata
        """
        pass

    @abstractmethod
    async def play_track(self, track_id: str) -> bool:
        """
        Initiate playback of a specific track.

        This method should delegate actual playback to the MusicControllerService
        while handling provider-specific track resolution.

        Args:
            track_id: Unique identifier for the track to play

        Returns:
            bool: True if playback started successfully, False otherwise
        """
        pass

    @abstractmethod
    async def stop(self) -> bool:
        """
        Stop current playback.

        Delegates to MusicControllerService for actual playback control.

        Returns:
            bool: True if stop succeeded, False otherwise
        """
        pass

    @abstractmethod
    async def get_current_track(self) -> Optional[Track]:
        """
        Get the currently playing track.

        Returns:
            Optional[Track]: Currently playing track or None
        """
        pass

    @abstractmethod
    async def is_provider_available(self) -> bool:
        """
        Check if the provider is currently available and functional.

        This may involve:
        - Checking network connectivity (for streaming providers)
        - Verifying file system access (for local providers)
        - Testing API endpoints (for web services)

        Returns:
            bool: True if provider is available and functional
        """
        pass

    @abstractmethod
    async def get_library(self) -> List[Track]:
        """
        Get all available tracks from this provider.

        Returns cached library if available, otherwise performs fresh scan.

        Returns:
            List[Track]: All tracks available from this provider
        """
        pass

    async def get_status(self) -> ProviderStatus:
        """
        Get current provider status.

        Returns:
            ProviderStatus: Current status information
        """
        uptime = None
        if self._start_time:
            uptime = asyncio.get_event_loop().time() - self._start_time

        return ProviderStatus(
            provider_name=self.name,
            is_available=self._available,
            is_initialized=self._initialized,
            library_size=len(self._library),
            uptime_seconds=uptime,
        )

    async def refresh_library(self) -> bool:
        """
        Force refresh of the music library.

        This method should reload the library from the source,
        bypassing any cached data.

        Returns:
            bool: True if refresh succeeded, False otherwise
        """
        try:
            self.logger.info(f"Refreshing library for provider: {self.name}")

            # Clear existing cache
            self._library.clear()
            self._library_cache.clear()

            # Reload library
            self._library = await self.get_library()

            # Rebuild cache
            for track in self._library:
                self._library_cache[track.track_id] = track

            self.logger.info(f"Library refreshed: {len(self._library)} tracks")
            return True

        except Exception as e:
            self.logger.error(f"Failed to refresh library: {e}")
            return False

    async def get_track_by_id(self, track_id: str) -> Optional[Track]:
        """
        Get a specific track by its ID.

        Args:
            track_id: Unique track identifier

        Returns:
            Optional[Track]: Track if found, None otherwise
        """
        # Check cache first
        if track_id in self._library_cache:
            return self._library_cache[track_id]

        # Search through library
        for track in self._library:
            if track.track_id == track_id:
                # Cache for future lookups
                self._library_cache[track_id] = track
                return track

        return None

    async def search_by_artist(self, artist: str) -> List[Track]:
        """
        Search for tracks by a specific artist.

        Args:
            artist: Artist name to search for

        Returns:
            List[Track]: Tracks by the specified artist
        """
        result = await self.search(artist)
        return result.filter_by_artist(artist)

    def _mark_initialized(self, success: bool = True) -> None:
        """
        Mark the provider as initialized.

        Args:
            success: Whether initialization was successful
        """
        self._initialized = success
        if success and self._start_time is None:
            self._start_time = asyncio.get_event_loop().time()

    def _mark_available(self, available: bool = True) -> None:
        """
        Mark the provider as available.

        Args:
            available: Whether provider is available
        """
        self._available = available

    async def _retry_operation(self, operation, *args, **kwargs):
        """
        Retry an operation with exponential backoff.

        Args:
            operation: Async function to retry
            *args: Arguments for the operation
            **kwargs: Keyword arguments for the operation

        Returns:
            Any: Result of the operation

        Raises:
            Exception: Last exception if all retries failed
        """
        last_exception = None

        for attempt in range(self.config.max_retries):
            try:
                return await operation(*args, **kwargs)
            except Exception as e:
                last_exception = e
                if attempt < self.config.max_retries - 1:
                    # Exponential backoff: 1s, 2s, 4s, etc.
                    delay = 2**attempt
                    self.logger.warning(
                        f"Operation failed (attempt {attempt + 1}/{self.config.max_retries}), "
                        f"retrying in {delay}s: {e}"
                    )
                    await asyncio.sleep(delay)
                else:
                    self.logger.error(
                        f"Operation failed after {self.config.max_retries} attempts: {e}"
                    )

        # Re-raise the last exception if all retries failed
        if last_exception:
            raise last_exception

    async def cleanup(self) -> None:
        """
        Clean up provider resources.

        This method should be called when the provider is being shut down.
        Subclasses can override this to perform provider-specific cleanup.
        """
        self.logger.info(f"Cleaning up provider: {self.name}")
        self._initialized = False
        self._available = False
        self._library.clear()
        self._library_cache.clear()
        self._current_track = None
