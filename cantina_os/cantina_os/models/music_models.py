"""
Shared models for music data between services.

This module contains Pydantic models for music track data, ensuring
consistent representation and serialization between different services.
"""

from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field


class MusicTrack(BaseModel):
    """Model representing a music track."""
    name: str = Field(description="Display name of the track")
    path: str = Field(description="Full file path to the track")
    duration: Optional[float] = Field(default=None, description="Duration of the track in seconds")
    artist: Optional[str] = Field(default=None, description="Artist name if available")
    album: Optional[str] = Field(default=None, description="Album name if available")
    genre: Optional[str] = Field(default=None, description="Genre of the track if available")
    
    def __str__(self) -> str:
        """String representation of the track."""
        return self.name
        
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MusicTrack":
        """Create a MusicTrack from a dictionary, handling both full and partial data."""
        if isinstance(data, dict):
            # Only use fields that are defined in the model
            valid_fields = {k: v for k, v in data.items() if k in cls.__annotations__}
            return cls(**valid_fields)
        else:
            raise ValueError(f"Expected dictionary, got {type(data)}")


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