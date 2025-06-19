"""
Music Source Manager Service for CantinaOS

Provides unified music provider management with support for multiple sources
including local files and streaming services.
"""

from .music_source_manager_service import MusicSourceManagerService
from .providers import MusicProvider, Track, LocalMusicProvider

__all__ = ["MusicSourceManagerService", "MusicProvider", "Track", "LocalMusicProvider"]
