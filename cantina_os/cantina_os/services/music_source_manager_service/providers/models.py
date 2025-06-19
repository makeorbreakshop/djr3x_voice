"""
Unified Track Model for Music Provider Interface

This module contains the unified Track model that standardizes track representation
across different music providers (local files, Spotify, etc.).

The Track model provides a common interface for all providers, enabling seamless
switching between different music sources while maintaining consistent data structure.
"""

from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from ....models.music_models import MusicTrack


class Track(BaseModel):
    """
    Unified track representation for all music providers.

    This model standardizes track data across different sources, providing
    a consistent interface for the MusicSourceManagerService.

    Attributes:
        track_id: Unique identifier for the track (local filename or Spotify URI)
        title: Display title of the track
        artist: Artist name
        album: Album name (optional)
        duration: Duration in seconds (optional)
        provider: Source provider name ("local" or "spotify")
        source_path: Local file path or Spotify URI
        metadata: Additional provider-specific metadata
    """

    track_id: str = Field(
        description="Unique identifier for the track within the provider"
    )
    title: str = Field(description="Display title of the track")
    artist: str = Field(description="Artist name")
    album: Optional[str] = Field(default=None, description="Album name if available")
    duration: Optional[int] = Field(default=None, description="Duration in seconds")
    provider: str = Field(description="Provider name (local, spotify, etc.)")
    source_path: Optional[str] = Field(
        default=None, description="Local file path or streaming URI"
    )
    metadata: Optional[Dict[str, Any]] = Field(
        default_factory=dict, description="Additional provider-specific metadata"
    )

    def __str__(self) -> str:
        """String representation of the track."""
        return f"{self.artist} - {self.title}"

    def __repr__(self) -> str:
        """Detailed string representation."""
        return (
            f"Track(track_id='{self.track_id}', title='{self.title}', "
            f"artist='{self.artist}', provider='{self.provider}')"
        )

    @classmethod
    def from_music_track(
        cls, music_track: MusicTrack, provider: str = "local"
    ) -> "Track":
        """
        Create a Track from a legacy MusicTrack model.

        This enables compatibility with existing MusicControllerService data structures.

        Args:
            music_track: Legacy MusicTrack instance
            provider: Provider name to associate with the track

        Returns:
            Track: Unified track representation
        """
        return cls(
            track_id=music_track.track_id or music_track.name,
            title=music_track.title or music_track.name,
            artist=music_track.artist or "Unknown Artist",
            album=music_track.album,
            duration=int(music_track.duration) if music_track.duration else None,
            provider=provider,
            source_path=music_track.path,
            metadata={
                "genre": music_track.genre,
                "name": music_track.name,  # Keep original name for compatibility
            },
        )

    def to_music_track(self) -> MusicTrack:
        """
        Convert this Track to a legacy MusicTrack for compatibility.

        This enables integration with existing MusicControllerService methods.

        Returns:
            MusicTrack: Legacy music track representation
        """
        return MusicTrack(
            name=self.metadata.get("name", self.title),
            path=self.source_path or "",
            duration=float(self.duration) if self.duration else None,
            track_id=self.track_id,
            title=self.title,
            artist=self.artist,
            album=self.album,
            genre=self.metadata.get("genre"),
        )

    def matches_query(self, query: str) -> bool:
        """
        Check if this track matches a search query.

        Performs case-insensitive matching against title, artist, and album.

        Args:
            query: Search query string

        Returns:
            bool: True if track matches query
        """
        query_lower = query.lower()

        # Check title match
        if query_lower in self.title.lower():
            return True

        # Check artist match
        if query_lower in self.artist.lower():
            return True

        # Check album match
        if self.album and query_lower in self.album.lower():
            return True

        # Check combined artist - title match
        combined = f"{self.artist} - {self.title}".lower()
        if query_lower in combined:
            return True

        return False

    def get_display_name(self) -> str:
        """
        Get a formatted display name for UI purposes.

        Returns:
            str: Formatted display name
        """
        if self.album:
            return f"{self.artist} - {self.title} ({self.album})"
        return f"{self.artist} - {self.title}"

    def get_duration_formatted(self) -> str:
        """
        Get duration formatted as MM:SS.

        Returns:
            str: Formatted duration or "Unknown" if not available
        """
        if self.duration is None:
            return "Unknown"

        minutes = self.duration // 60
        seconds = self.duration % 60
        return f"{minutes:02d}:{seconds:02d}"


class TrackSearchResult(BaseModel):
    """
    Container for track search results from providers.

    Provides additional context about the search operation and results.
    """

    tracks: list[Track] = Field(
        default_factory=list, description="List of tracks matching the search query"
    )
    query: str = Field(description="Original search query")
    provider: str = Field(description="Provider that performed the search")
    total_results: int = Field(description="Total number of results found")
    search_duration_ms: Optional[int] = Field(
        default=None, description="Time taken to perform search in milliseconds"
    )

    def get_best_match(self) -> Optional[Track]:
        """
        Get the best matching track from search results.

        Returns the first track if available, or None if no results.

        Returns:
            Optional[Track]: Best matching track or None
        """
        return self.tracks[0] if self.tracks else None

    def filter_by_artist(self, artist: str) -> list[Track]:
        """
        Filter results by artist name.

        Args:
            artist: Artist name to filter by

        Returns:
            list[Track]: Tracks by the specified artist
        """
        return [
            track for track in self.tracks if artist.lower() in track.artist.lower()
        ]
