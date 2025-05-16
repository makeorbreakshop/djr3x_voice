"""
Models package for Cantina OS.

This package contains shared Pydantic data models used across different services.
"""

from .music_models import MusicTrack, MusicLibrary

__all__ = [
    "MusicTrack",
    "MusicLibrary",
] 