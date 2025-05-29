"""
Shared models for music data between services.

This module contains Pydantic models for music track data, ensuring
consistent representation and serialization between different services.
"""

from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field


class MusicTrack(BaseModel):
    """Model representing a music track."""
    name: str = Field(description="Name of the track (filename without extension)")
    path: str = Field(description="Full path to the track file")
    duration: Optional[float] = Field(default=None, description="Duration of the track in seconds")
    track_id: str = Field(default="", description="Unique identifier for the track (defaults to name)")
    title: str = Field(default="", description="Display title for the track (defaults to name)")
    artist: Optional[str] = Field(default=None, description="Artist name if available")
    album: Optional[str] = Field(default=None, description="Album name if available")
    genre: Optional[str] = Field(default=None, description="Genre of the track if available")
    
    def __str__(self) -> str:
        """String representation of the track."""
        return self.name
        
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MusicTrack":
        """Create a MusicTrack from a dictionary."""
        # Set defaults for backward compatibility
        if "track_id" not in data:
            data["track_id"] = data.get("name", "")
        if "title" not in data:
            data["title"] = data.get("name", "")
        return cls(**data)


class MusicLibrary(BaseModel):
    """Model representing a collection of music tracks."""
    tracks: Dict[str, MusicTrack] = Field(default_factory=dict, description="Map of track names to track data")
    
    def get_track_names(self) -> List[str]:
        """Get a list of track names."""
        return list(self.tracks.keys())
    
    def get_track_by_name(self, name: str) -> Optional[MusicTrack]:
        """Get a track by its name."""
        return self.tracks.get(name)
    
    @classmethod
    def from_dict_list(cls, track_list: List[Dict[str, Any]]) -> "MusicLibrary":
        """Create a MusicLibrary from a list of track dictionaries."""
        tracks = {}
        for track_data in track_list:
            try:
                track = MusicTrack.from_dict(track_data)
                tracks[track.name] = track
            except Exception as e:
                # Skip invalid tracks rather than failing
                print(f"Error parsing track data: {e}")
        return cls(tracks=tracks) 