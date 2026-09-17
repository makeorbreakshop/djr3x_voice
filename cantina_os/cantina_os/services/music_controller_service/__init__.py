"""
Music Controller Service Package

Provides music playback with support for multiple backends (local files, Spotify, etc.)
"""

from .music_backends import MusicBackend, LocalMusicBackend, SpotifyMusicBackend
from .music_controller_service import MusicControllerService, MusicTrack

__all__ = [
    "MusicBackend",
    "LocalMusicBackend",
    "SpotifyMusicBackend",
    "MusicControllerService",
    # ADDED 2026-09-17: MusicTrack was defined in the live package module but never
    # re-exported, so importers silently fell back to the dead flat
    # services/music_controller_service.py shadowed by this package.
    "MusicTrack",
]
