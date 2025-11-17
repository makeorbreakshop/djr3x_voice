"""
Music Controller Service Package

Provides music playback with support for multiple backends (local files, Spotify, etc.)
"""

from .music_backends import MusicBackend, LocalMusicBackend, SpotifyMusicBackend
from .music_controller_service import MusicControllerService

__all__ = ["MusicBackend", "LocalMusicBackend", "SpotifyMusicBackend", "MusicControllerService"]
