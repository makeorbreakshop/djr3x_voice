"""
Music Provider Interface Package

This package provides the abstract base provider interface and concrete implementations
for different music sources (local files, Spotify, etc.).

Available Providers:
- LocalMusicProvider: Manages local music files and integrates with MusicControllerService
- SpotifyMusicProvider: Spotify Web API integration with 30-second previews

Usage:
    from .base_provider import MusicProvider
    from .local_music_provider import LocalMusicProvider

    # Create provider instance
    local_provider = LocalMusicProvider(config=local_config)

    # Initialize provider
    await local_provider.initialize()

    # Search for tracks
    tracks = await local_provider.search("cantina band")

    # Play a track
    await local_provider.play_track(tracks[0].track_id)
"""

from .base_provider import MusicProvider
from .models import Track

__all__ = [
    "MusicProvider",
    "Track",
]

# Import concrete providers as they become available
try:
    from .local_music_provider import LocalMusicProvider, LocalMusicConfig

    __all__.extend(["LocalMusicProvider", "LocalMusicConfig"])
except ImportError:
    pass

try:
    from .spotify_music_provider import SpotifyMusicProvider, SpotifyConfig

    __all__.extend(["SpotifyMusicProvider", "SpotifyConfig"])
except ImportError:
    pass
