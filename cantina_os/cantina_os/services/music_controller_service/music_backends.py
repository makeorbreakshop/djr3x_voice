"""
Music Backend Abstraction Layer for CantinaOS

This module provides an abstract interface for music playback backends,
allowing the system to seamlessly switch between different music sources
(local files, Spotify, etc.) without changing the core orchestration logic.
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any
import vlc

try:
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth
    from spotipy.exceptions import SpotifyException
    SPOTIPY_AVAILABLE = True
except ImportError:
    SPOTIPY_AVAILABLE = False
    spotipy = None
    SpotifyOAuth = None
    SpotifyException = None

from ...models.music_models import MusicTrack


class MusicBackend(ABC):
    """Abstract base class for music playback backends."""

    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self._current_track: Optional[MusicTrack] = None

    @abstractmethod
    async def initialize(self) -> bool:
        """Initialize the backend. Returns True if successful."""
        pass

    @abstractmethod
    async def play_track(self, track: MusicTrack) -> bool:
        """Play the specified track. Returns True if successful."""
        pass

    @abstractmethod
    async def stop_playback(self) -> bool:
        """Stop current playback. Returns True if successful."""
        pass

    @abstractmethod
    async def set_volume(self, volume: int) -> bool:
        """Set playback volume (0-100). Returns True if successful."""
        pass

    @abstractmethod
    async def get_current_position(self) -> float:
        """Get current playback position in seconds."""
        pass

    @abstractmethod
    async def get_duration(self) -> float:
        """Get total track duration in seconds."""
        pass

    @abstractmethod
    async def is_playing(self) -> bool:
        """Check if currently playing."""
        pass

    @abstractmethod
    async def load_library(self) -> Dict[str, MusicTrack]:
        """Load and return the music library for this backend."""
        pass

    @property
    def current_track(self) -> Optional[MusicTrack]:
        """Get currently playing track."""
        return self._current_track


class LocalMusicBackend(MusicBackend):
    """VLC-based backend for local music file playback."""

    def __init__(self, vlc_instance: vlc.Instance, logger: logging.Logger):
        super().__init__(logger)
        self.vlc_instance = vlc_instance
        self.player: Optional[vlc.MediaPlayer] = None
        self.secondary_player: Optional[vlc.MediaPlayer] = None

    async def initialize(self) -> bool:
        """Initialize VLC backend."""
        try:
            self.logger.info("Local music backend initialized")
            return True
        except Exception as e:
            self.logger.error(f"Failed to initialize local backend: {e}")
            return False

    async def play_track(self, track: MusicTrack) -> bool:
        """Play local audio file using VLC."""
        try:
            # Stop any existing playback
            if self.player:
                self.player.stop()

            # Create new player
            media = self.vlc_instance.media_new(track.path)
            self.player = self.vlc_instance.media_player_new()
            self.player.set_media(media)

            # Start playback
            result = self.player.play()

            if result == 0:  # VLC returns 0 on success
                self._current_track = track
                self.logger.info(f"Playing local track: {track.name}")
                return True
            else:
                self.logger.error(f"Failed to play track: {track.name}")
                return False

        except Exception as e:
            self.logger.error(f"Error playing local track: {e}")
            return False

    async def stop_playback(self) -> bool:
        """Stop VLC playback."""
        try:
            if self.player:
                self.player.stop()
                self._current_track = None
            return True
        except Exception as e:
            self.logger.error(f"Error stopping playback: {e}")
            return False

    async def set_volume(self, volume: int) -> bool:
        """Set VLC volume."""
        try:
            if self.player:
                self.player.audio_set_volume(volume)
            return True
        except Exception as e:
            self.logger.error(f"Error setting volume: {e}")
            return False

    async def get_current_position(self) -> float:
        """Get current playback position from VLC."""
        try:
            if self.player:
                return self.player.get_time() / 1000.0  # Convert ms to seconds
            return 0.0
        except Exception as e:
            self.logger.error(f"Error getting position: {e}")
            return 0.0

    async def get_duration(self) -> float:
        """Get track duration from VLC."""
        try:
            if self.player:
                return self.player.get_length() / 1000.0  # Convert ms to seconds
            return 0.0
        except Exception as e:
            self.logger.error(f"Error getting duration: {e}")
            return 0.0

    async def is_playing(self) -> bool:
        """Check if VLC is playing."""
        try:
            if self.player:
                return self.player.is_playing()
            return False
        except Exception as e:
            self.logger.error(f"Error checking playback state: {e}")
            return False

    async def load_library(self) -> Dict[str, MusicTrack]:
        """Local backend doesn't manage library - returns empty dict."""
        return {}

    async def crossfade_to_track(self, next_track: MusicTrack, duration: float) -> bool:
        """Crossfade from current track to next track."""
        try:
            if not self.player or not self.player.is_playing():
                # No current track, just play next
                return await self.play_track(next_track)

            # Prepare secondary player
            media = self.vlc_instance.media_new(next_track.path)
            self.secondary_player = self.vlc_instance.media_player_new()
            self.secondary_player.set_media(media)
            self.secondary_player.audio_set_volume(0)

            # Start secondary player
            self.secondary_player.play()

            # Crossfade loop
            steps = 50
            step_duration = duration / steps

            for step in range(steps):
                # Fade out primary, fade in secondary
                primary_vol = 100 - int((step / steps) * 100)
                secondary_vol = int((step / steps) * 100)

                if self.player:
                    self.player.audio_set_volume(primary_vol)
                if self.secondary_player:
                    self.secondary_player.audio_set_volume(secondary_vol)

                await asyncio.sleep(step_duration)

            # Stop primary player
            if self.player:
                self.player.stop()

            # Swap players
            self.player = self.secondary_player
            self.secondary_player = None
            self._current_track = next_track

            self.logger.info(f"Crossfaded to: {next_track.name}")
            return True

        except Exception as e:
            self.logger.error(f"Error during crossfade: {e}")
            return False


class SpotifyMusicBackend(MusicBackend):
    """Spotify Connect backend for streaming music."""

    def __init__(self, config: Dict[str, Any], logger: logging.Logger):
        super().__init__(logger)

        if not SPOTIPY_AVAILABLE:
            raise ImportError("spotipy library required for Spotify backend")

        self.config = config
        self.sp: Optional[spotipy.Spotify] = None
        self.device_id: Optional[str] = None
        self._current_volume = 70

    async def initialize(self) -> bool:
        """Initialize Spotify Connect backend."""
        try:
            self.logger.info("Initializing Spotify Connect backend...")

            # Setup OAuth with open_browser=True to automatically open auth URL
            auth_manager = SpotifyOAuth(
                client_id=self.config["client_id"],
                client_secret=self.config["client_secret"],
                redirect_uri=self.config.get("redirect_uri", "http://127.0.0.1:3000/redirect"),
                scope="user-read-playback-state user-modify-playback-state user-library-read playlist-read-private",
                open_browser=True,  # Automatically open browser for auth
                cache_path=".spotify_cache"  # Cache tokens
            )

            self.sp = spotipy.Spotify(auth_manager=auth_manager)

            # Discover device
            device_name = self.config.get("device_name")
            self.device_id = await self._discover_device(device_name)

            if not self.device_id:
                self.logger.error("No Spotify device found. Make sure Spotify app is running.")
                return False

            self.logger.info(f"Spotify backend initialized (device: {self.device_id})")
            return True

        except Exception as e:
            self.logger.error(f"Failed to initialize Spotify backend: {e}")
            return False

    async def _discover_device(self, device_name: Optional[str] = None) -> Optional[str]:
        """Discover Spotify Connect device."""
        try:
            devices = await asyncio.to_thread(self.sp.devices)

            if not devices or not devices.get("devices"):
                self.logger.warning("No Spotify devices found - is Spotify app running?")
                return None

            # If device name specified, find exact match
            if device_name:
                for device in devices["devices"]:
                    if device["name"] == device_name:
                        self.logger.info(f"Selected device by name: {device['name']} ({device['id'][:8]}...)")
                        return device["id"]
                self.logger.warning(f"Preferred device '{device_name}' not found, falling back...")

            # Otherwise, return first active device
            for device in devices["devices"]:
                if device["is_active"]:
                    self.logger.info(f"Selected active device: {device['name']} ({device['id'][:8]}...)")
                    return device["id"]

            # If no active device, return first available
            first_device = devices["devices"][0]
            self.logger.warning(f"No active device, using first available: {first_device['name']} ({first_device['id'][:8]}...)")
            return first_device["id"]

        except Exception as e:
            self.logger.error(f"Error discovering Spotify device: {e}")
            return None

    async def play_track(self, track: MusicTrack) -> bool:
        """Play track using Spotify Connect."""
        try:
            if not self.sp:
                self.logger.error("Spotify backend not initialized")
                return False

            # Track path should be Spotify URI
            if not track.path.startswith("spotify:"):
                self.logger.error(f"Invalid Spotify URI: {track.path}")
                return False

            # Refresh device discovery before playback (device may have changed)
            device_name = self.config.get("device_name")
            self.device_id = await self._discover_device(device_name)

            if not self.device_id:
                self.logger.error("No active Spotify device found. Please open Spotify app.")
                return False

            # Transfer playback to this device first to ensure it's active
            try:
                await asyncio.to_thread(
                    self.sp.transfer_playback,
                    device_id=self.device_id,
                    force_play=False  # Don't auto-play yet
                )
            except SpotifyException as e:
                # Continue anyway - device might already be active
                pass

            # Start playback
            await asyncio.to_thread(
                self.sp.start_playback,
                device_id=self.device_id,
                uris=[track.path]
            )

            self._current_track = track
            self.logger.info(f"✓ Playing Spotify track: {track.name}")
            return True

        except SpotifyException as e:
            self.logger.error(f"Spotify API error: {e.http_status} - {e.msg}")
            # Try to rediscover device on error
            self.device_id = await self._discover_device(self.config.get("device_name"))
            return False
        except Exception as e:
            self.logger.error(f"Error playing Spotify track: {e}")
            self.device_id = await self._discover_device(self.config.get("device_name"))
            return False

    async def stop_playback(self) -> bool:
        """Stop Spotify playback."""
        try:
            if self.sp and self.device_id:
                await asyncio.to_thread(
                    self.sp.pause_playback,
                    device_id=self.device_id
                )
            self._current_track = None
            return True
        except Exception as e:
            self.logger.error(f"Error stopping Spotify playback: {e}")
            return False

    async def set_volume(self, volume: int) -> bool:
        """Set Spotify playback volume."""
        try:
            if self.sp and self.device_id:
                await asyncio.to_thread(
                    self.sp.volume,
                    volume,
                    device_id=self.device_id
                )
                self._current_volume = volume
            return True
        except Exception as e:
            self.logger.error(f"Error setting Spotify volume: {e}")
            return False

    async def get_current_position(self) -> float:
        """Get current playback position from Spotify."""
        try:
            if self.sp:
                playback = await asyncio.to_thread(self.sp.current_playback)
                if playback and playback.get("progress_ms"):
                    return playback["progress_ms"] / 1000.0
            return 0.0
        except Exception as e:
            self.logger.error(f"Error getting Spotify position: {e}")
            return 0.0

    async def get_duration(self) -> float:
        """Get track duration from Spotify."""
        try:
            if self.sp:
                playback = await asyncio.to_thread(self.sp.current_playback)
                if playback and playback.get("item", {}).get("duration_ms"):
                    return playback["item"]["duration_ms"] / 1000.0
            return 0.0
        except Exception as e:
            self.logger.error(f"Error getting Spotify duration: {e}")
            return 0.0

    async def is_playing(self) -> bool:
        """Check if Spotify is playing."""
        try:
            if self.sp:
                playback = await asyncio.to_thread(self.sp.current_playback)
                return playback and playback.get("is_playing", False)
            return False
        except Exception as e:
            self.logger.error(f"Error checking Spotify playback state: {e}")
            return False

    async def load_library(self) -> Dict[str, MusicTrack]:
        """Load user's Spotify library (Liked Songs + Playlists)."""
        library = {}

        try:
            if not self.sp:
                self.logger.error("Spotify client not initialized")
                return library

            self.logger.info("[Spotify] Loading Spotify library (Liked Songs + Playlists)...")

            # 1. Load Liked Songs
            offset = 0
            limit = 50
            total_tracks = 0

            while True:
                # Get batch of saved tracks
                results = await asyncio.to_thread(
                    self.sp.current_user_saved_tracks,
                    limit=limit,
                    offset=offset
                )

                if not results or not results.get("items"):
                    break

                # Process each track
                for item in results["items"]:
                    track_data = item["track"]

                    # Extract track info
                    track_id = track_data["id"]
                    track_name = track_data["name"]
                    artist_name = track_data["artists"][0]["name"] if track_data.get("artists") else "Unknown"
                    album_name = track_data.get("album", {}).get("name", "Unknown")
                    duration_ms = track_data.get("duration_ms", 0)
                    duration_sec = duration_ms / 1000.0 if duration_ms else None
                    spotify_uri = track_data["uri"]

                    # Create MusicTrack with unique key (avoid duplicates)
                    unique_key = f"{artist_name} - {track_name}"
                    track = MusicTrack(
                        name=unique_key,
                        path=spotify_uri,  # Spotify URI for playback
                        duration=duration_sec,
                        track_id=track_id,
                        title=track_name,
                        artist=artist_name,
                        album=album_name,
                        provider="spotify"
                    )

                    library[unique_key] = track
                    total_tracks += 1

                # Check if more pages exist
                if not results.get("next"):
                    break

                offset += limit

            self.logger.info(f"[Spotify] Loaded {total_tracks} tracks from Liked Songs")

            # 2. Load tracks from all playlists
            playlist_tracks = 0
            playlists = await asyncio.to_thread(self.sp.current_user_playlists, limit=50)

            if playlists and playlists.get("items"):
                for playlist in playlists["items"]:
                    playlist_name = playlist["name"]
                    playlist_id = playlist["id"]

                    # Fetch playlist tracks
                    offset = 0
                    while True:
                        playlist_tracks_batch = await asyncio.to_thread(
                            self.sp.playlist_tracks,
                            playlist_id,
                            limit=100,
                            offset=offset
                        )

                        if not playlist_tracks_batch or not playlist_tracks_batch.get("items"):
                            break

                        for item in playlist_tracks_batch["items"]:
                            if not item.get("track"):  # Skip None tracks
                                continue

                            track_data = item["track"]

                            # Extract track info
                            track_id = track_data.get("id")
                            if not track_id:  # Skip local files
                                continue

                            track_name = track_data["name"]
                            artist_name = track_data["artists"][0]["name"] if track_data.get("artists") else "Unknown"
                            album_name = track_data.get("album", {}).get("name", "Unknown")
                            duration_ms = track_data.get("duration_ms", 0)
                            duration_sec = duration_ms / 1000.0 if duration_ms else None
                            spotify_uri = track_data["uri"]

                            # Create unique key
                            unique_key = f"{artist_name} - {track_name}"

                            # Only add if not already in library (avoid duplicates)
                            if unique_key not in library:
                                track = MusicTrack(
                                    name=unique_key,
                                    path=spotify_uri,
                                    duration=duration_sec,
                                    track_id=track_id,
                                    title=track_name,
                                    artist=artist_name,
                                    album=album_name,
                                    provider="spotify"
                                )

                                library[unique_key] = track
                                playlist_tracks += 1

                        # Check if more pages exist
                        if not playlist_tracks_batch.get("next"):
                            break

                        offset += 100

            self.logger.info(f"[Spotify] Loaded {playlist_tracks} additional tracks from playlists")
            self.logger.info(f"[Spotify] Total library: {len(library)} unique tracks")
            return library

        except Exception as e:
            self.logger.error(f"Error loading Spotify library: {e}")
            return library

    async def crossfade_to_track(self, next_track: MusicTrack, duration: float) -> bool:
        """Crossfade to next track on Spotify (manual volume-based fade)."""
        try:
            if not self.sp or not self.device_id:
                return False

            # Queue next track
            await asyncio.to_thread(
                self.sp.add_to_queue,
                next_track.path,
                device_id=self.device_id
            )

            # Fade out current track
            steps = 20
            step_duration = duration / (steps * 2)  # Split duration between fade out and fade in

            for step in range(steps):
                vol = self._current_volume - int((step / steps) * self._current_volume)
                await self.set_volume(vol)
                await asyncio.sleep(step_duration)

            # Skip to next track
            await asyncio.to_thread(
                self.sp.next_track,
                device_id=self.device_id
            )

            # Fade in next track
            for step in range(steps):
                vol = int((step / steps) * self._current_volume)
                await self.set_volume(vol)
                await asyncio.sleep(step_duration)

            # Restore normal volume
            await self.set_volume(self._current_volume)

            self._current_track = next_track
            self.logger.info(f"Crossfaded to Spotify track: {next_track.name}")
            return True

        except Exception as e:
            self.logger.error(f"Error during Spotify crossfade: {e}")
            return False
