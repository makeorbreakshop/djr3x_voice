"""
Spotify Music Provider for CantinaOS

This provider integrates with the Spotify Web API to provide music search,
metadata access, and 30-second preview playback functionality. It handles
OAuth 2.0 authentication with automatic token refresh and integrates with
the existing MusicControllerService for audio playback.

Key Features:
- OAuth 2.0 authorization code flow with automatic token refresh
- Spotify catalog search with relevance scoring
- 30-second track previews (Spotify API limitation)
- User library access (playlists, saved tracks)
- Graceful degradation when Spotify is unavailable
- Full integration with CantinaOS event system

Limitations:
- Only 30-second preview URLs available via Web API
- Full track streaming requires Spotify Premium and Web Playback SDK
- Rate limiting applies (429 errors on exceeded limits)
"""

import os
import time
from typing import List, Optional, Dict, Any, TYPE_CHECKING
from pydantic import Field
from pyee.asyncio import AsyncIOEventEmitter

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


from .base_provider import MusicProvider, ProviderConfig
from .models import Track, TrackSearchResult
from ....core.event_topics import EventTopics
from ....event_payloads import MusicCommandPayload

if TYPE_CHECKING:
    pass


class SpotifyConfig(ProviderConfig):
    """
    Configuration for the Spotify music provider.

    Extends base provider config with Spotify-specific settings.
    """

    client_id: str = Field(description="Spotify application client ID")
    client_secret: str = Field(description="Spotify application client secret")
    redirect_uri: str = Field(
        default="http://localhost:8080/callback",
        description="OAuth redirect URI for authorization",
    )
    scopes: List[str] = Field(
        default=[
            "user-library-read",
            "playlist-read-private",
            "playlist-read-collaborative",
            "user-read-playback-state",
        ],
        description="Spotify OAuth scopes to request",
    )
    cache_directory: str = Field(
        default=".spotify_cache", description="Directory to store OAuth token cache"
    )
    search_limit: int = Field(
        default=50, description="Maximum number of search results to return"
    )
    library_cache_minutes: int = Field(
        default=30, description="Cache duration for user library data in minutes"
    )
    preview_only: bool = Field(
        default=True,
        description="Only use 30-second previews (set to False for full playback if implementing Web Playback SDK)",
    )


class SpotifyMusicProvider(MusicProvider):
    """
    Spotify Web API music provider.

    This provider integrates with Spotify's Web API to provide:

    - OAuth 2.0 authentication with automatic token refresh
    - Music catalog search functionality
    - User library access (playlists, saved tracks)
    - 30-second preview playback via preview URLs
    - Metadata retrieval and normalization

    The provider focuses on discovery and metadata while delegating
    actual audio playback to the MusicControllerService to maintain
    compatibility with existing audio ducking, crossfading, and
    DJ mode functionality.

    Authentication Flow:
    1. Initial OAuth authorization (manual approval required)
    2. Token caching for subsequent sessions
    3. Automatic refresh token handling
    4. Graceful fallback when authentication fails
    """

    def __init__(self, config: Dict[str, Any], event_bus: AsyncIOEventEmitter, music_controller=None):
        """
        Initialize the Spotify music provider.

        Args:
            config: Provider configuration dictionary
            event_bus: Event bus for service communication
            music_controller: Optional reference to the MusicControllerService for direct method calls
        """
        if not SPOTIPY_AVAILABLE:
            raise ImportError(
                "spotipy library is required for Spotify integration. "
                "Install with: pip install spotipy>=2.23.0"
            )

        # Create typed config
        spotify_config = SpotifyConfig(provider_name="spotify", **config)

        super().__init__(spotify_config)

        self.config: SpotifyConfig = spotify_config
        self.event_bus = event_bus
        self.music_controller = music_controller

        # Spotify client and authentication
        self._spotify_client: Optional[spotipy.Spotify] = None
        self._auth_manager: Optional[SpotifyOAuth] = None
        self._last_auth_check: Optional[float] = None

        # Library caching
        self._user_playlists: List[Dict[str, Any]] = []
        self._saved_tracks: List[Dict[str, Any]] = []
        self._library_last_updated: Optional[float] = None

        # Current playback state
        self._current_preview_url: Optional[str] = None

        # Rate limiting
        self._rate_limit_reset: Optional[float] = None
        self._rate_limited = False

    async def initialize(self) -> bool:
        """
        Initialize the Spotify provider.

        Performs:
        - OAuth authentication setup
        - Initial token acquisition or refresh
        - Spotify API connectivity test
        - User library cache initialization

        Returns:
            bool: True if initialization succeeded
        """
        try:
            self.logger.info("Initializing Spotify music provider")

            # Validate configuration
            if not self.config.client_id or not self.config.client_secret:
                self.logger.error("Spotify client ID and secret are required")
                return False

            # Setup OAuth authentication
            if not await self._setup_authentication():
                self.logger.error("Failed to setup Spotify authentication")
                return False

            # Test API connectivity
            if not await self._test_api_connectivity():
                self.logger.error("Failed to connect to Spotify API")
                return False

            # Initialize user library cache
            await self._refresh_user_library()

            # Mark as initialized and available
            self._mark_initialized(True)
            self._mark_available(True)

            self.logger.info("Spotify music provider initialized successfully")
            return True

        except Exception as e:
            self.logger.error(f"Failed to initialize Spotify provider: {e}")
            self._mark_initialized(False)
            self._mark_available(False)
            return False

    async def search(self, query: str) -> TrackSearchResult:
        """
        Search Spotify catalog for tracks matching the query.

        Performs fuzzy matching against Spotify's search API with scoring.

        Args:
            query: Search query string

        Returns:
            TrackSearchResult: Search results with metadata
        """
        start_time = time.time()

        try:
            # Check authentication and rate limits
            if not await self._ensure_authenticated():
                return TrackSearchResult(
                    tracks=[], query=query, provider=self.name, total_results=0
                )

            if self._rate_limited:
                self.logger.warning("Spotify API rate limited, skipping search")
                return TrackSearchResult(
                    tracks=[], query=query, provider=self.name, total_results=0
                )

            # Perform Spotify search
            results = await self._retry_operation(
                self._search_spotify, query, limit=self.config.search_limit
            )

            if not results or "tracks" not in results:
                return TrackSearchResult(
                    tracks=[], query=query, provider=self.name, total_results=0
                )

            # Convert Spotify tracks to unified Track objects
            tracks = []
            for item in results["tracks"]["items"]:
                track = self._spotify_track_to_track(item)
                if track:
                    tracks.append(track)

            # Calculate search duration
            duration_ms = int((time.time() - start_time) * 1000)

            return TrackSearchResult(
                tracks=tracks,
                query=query,
                provider=self.name,
                total_results=results["tracks"]["total"],
                search_duration_ms=duration_ms,
            )

        except SpotifyException as e:
            self.logger.error(f"Spotify API error during search: {e}")
            if e.http_status == 429:  # Rate limited
                self._handle_rate_limit(e)
            return TrackSearchResult(
                tracks=[], query=query, provider=self.name, total_results=0
            )
        except Exception as e:
            self.logger.error(f"Search failed: {e}")
            return TrackSearchResult(
                tracks=[], query=query, provider=self.name, total_results=0
            )

    async def play_track(self, track_id: str) -> bool:
        """
        Play a Spotify track (30-second preview) by delegating to MusicControllerService.

        Args:
            track_id: Spotify track ID or URI

        Returns:
            bool: True if playback started successfully
        """
        try:
            # Get track details
            track = await self.get_track_by_id(track_id)
            if not track:
                self.logger.error(f"Spotify track not found: {track_id}")
                return False

            # Check if track has preview URL
            if not track.source_path:
                self.logger.warning(
                    f"No preview URL available for track: {track.title}"
                )
                return False

            # Delegate to MusicControllerService via direct method call or event bus fallback
            if self.music_controller:
                # Use direct method call to avoid feedback loops
                success = await self.music_controller.play_track_url(track.source_path, track.title)
                if not success:
                    self.logger.error(f"Failed to play Spotify track via direct method call: {track.title}")
                    return False
                self.logger.info(f"Successfully requested Spotify preview playback via direct call: {track.title}")
            else:
                # Fallback to event bus if no direct reference available
                payload = MusicCommandPayload(
                    action="play",
                    song_query=track.source_path,  # Use preview URL directly
                    conversation_id=None,
                )
                self.event_bus.emit(EventTopics.MUSIC_COMMAND, payload.model_dump())
                self.logger.info(f"Requested Spotify preview playback via event bus: {track.title}")

            # Update current track and preview URL
            self._current_track = track
            self._current_preview_url = track.source_path

            return True

        except Exception as e:
            self.logger.error(f"Failed to play Spotify track {track_id}: {e}")
            return False

    async def stop(self) -> bool:
        """
        Stop Spotify preview playback by delegating to MusicControllerService.

        Returns:
            bool: True if stop command was sent successfully
        """
        try:
            # Delegate to MusicControllerService via direct method call or event bus fallback
            if self.music_controller:
                # Use direct method call to avoid feedback loops
                success = await self.music_controller.stop_playback()
                if not success:
                    self.logger.error("Failed to stop Spotify playback via direct method call")
                    return False
                self.logger.info("Successfully requested Spotify preview stop via direct call")
            else:
                # Fallback to event bus if no direct reference available
                payload = MusicCommandPayload(action="stop", conversation_id=None)
                self.event_bus.emit(EventTopics.MUSIC_COMMAND, payload.model_dump())
                self.logger.info("Requested Spotify preview stop via event bus")

            # Clear current state
            self._current_track = None
            self._current_preview_url = None

            return True

        except Exception as e:
            self.logger.error(f"Failed to stop Spotify playback: {e}")
            return False

    async def get_current_track(self) -> Optional[Track]:
        """
        Get the currently playing Spotify track.

        Returns:
            Optional[Track]: Currently playing track or None
        """
        return self._current_track

    async def is_provider_available(self) -> bool:
        """
        Check if the Spotify provider is available.

        Verifies:
        - Authentication is valid
        - API connectivity
        - Not rate limited

        Returns:
            bool: True if provider is available
        """
        try:
            if self._rate_limited:
                # Check if rate limit has expired
                if self._rate_limit_reset and time.time() > self._rate_limit_reset:
                    self._rate_limited = False
                    self._rate_limit_reset = None
                else:
                    return False

            # Test authentication and connectivity
            return await self._ensure_authenticated()

        except Exception as e:
            self.logger.error(f"Availability check failed: {e}")
            return False

    async def get_library(self) -> List[Track]:
        """
        Get user's Spotify library (playlists and saved tracks).

        Returns cached library if recent, otherwise performs fresh fetch.

        Returns:
            List[Track]: All tracks from user's Spotify library
        """
        try:
            # Check if cache is still valid
            if (
                self._library
                and self._library_last_updated
                and (time.time() - self._library_last_updated)
                < (self.config.library_cache_minutes * 60)
            ):
                return self._library.copy()

            # Refresh library from Spotify
            await self._refresh_user_library()
            return self._library.copy()

        except Exception as e:
            self.logger.error(f"Failed to get Spotify library: {e}")
            return []

    async def _setup_authentication(self) -> bool:
        """
        Setup OAuth authentication for Spotify API.

        Returns:
            bool: True if authentication was setup successfully
        """
        try:
            # Create cache directory if it doesn't exist
            cache_dir = self.config.cache_directory
            if not os.path.exists(cache_dir):
                os.makedirs(cache_dir, exist_ok=True)

            # Setup OAuth manager
            self._auth_manager = SpotifyOAuth(
                client_id=self.config.client_id,
                client_secret=self.config.client_secret,
                redirect_uri=self.config.redirect_uri,
                scope=" ".join(self.config.scopes),
                cache_path=os.path.join(cache_dir, ".cache-spotify"),
            )

            # Try to get cached token
            token_info = self._auth_manager.get_cached_token()

            if not token_info:
                # Need to perform initial authorization
                self.logger.warning(
                    "No cached Spotify token found. Manual authorization required."
                )
                auth_url = self._auth_manager.get_authorize_url()

                # In a real deployment, this would need to be handled differently
                # For now, log the URL and expect manual setup
                self.logger.info(f"Please authorize the application at: {auth_url}")
                self.logger.info(
                    "After authorization, restart the service to complete setup."
                )
                return False

            # Create Spotify client
            self._spotify_client = spotipy.Spotify(auth_manager=self._auth_manager)

            return True

        except Exception as e:
            self.logger.error(f"Failed to setup Spotify authentication: {e}")
            return False

    async def _ensure_authenticated(self) -> bool:
        """
        Ensure Spotify authentication is valid, refreshing if needed.

        Returns:
            bool: True if authentication is valid
        """
        try:
            if not self._auth_manager or not self._spotify_client:
                return False

            # Check if token needs refresh (check every 5 minutes)
            now = time.time()
            if (
                not self._last_auth_check or now - self._last_auth_check > 300
            ):  # 5 minutes

                token_info = self._auth_manager.get_cached_token()
                if not token_info:
                    return False

                # Refresh token if expired
                if self._auth_manager.is_token_expired(token_info):
                    self.logger.debug("Refreshing Spotify access token")
                    token_info = self._auth_manager.refresh_access_token(
                        token_info["refresh_token"]
                    )
                    if not token_info:
                        return False

                self._last_auth_check = now

            return True

        except Exception as e:
            self.logger.error(f"Authentication check failed: {e}")
            return False

    async def _test_api_connectivity(self) -> bool:
        """
        Test connectivity to Spotify API.

        Returns:
            bool: True if API is reachable
        """
        try:
            if not self._spotify_client:
                return False

            # Simple API test - get current user
            user = await self._retry_operation(
                lambda: self._spotify_client.current_user()
            )

            if user and "id" in user:
                self.logger.info(f"Connected to Spotify API as user: {user['id']}")
                return True

            return False

        except Exception as e:
            self.logger.error(f"Spotify API connectivity test failed: {e}")
            return False

    async def _search_spotify(self, query: str, limit: int = 50) -> Dict[str, Any]:
        """
        Perform search against Spotify API.

        Args:
            query: Search query
            limit: Maximum results to return

        Returns:
            Dict[str, Any]: Spotify search results
        """
        if not self._spotify_client:
            raise Exception("Spotify client not initialized")

        return self._spotify_client.search(q=query, type="track", limit=limit)

    async def _refresh_user_library(self) -> None:
        """
        Refresh user's library from Spotify (playlists and saved tracks).
        """
        try:
            if not await self._ensure_authenticated():
                return

            self.logger.debug("Refreshing Spotify user library")

            # Get user's playlists
            playlists = await self._retry_operation(
                lambda: self._spotify_client.current_user_playlists(limit=50)
            )

            # Get saved tracks
            saved_tracks = await self._retry_operation(
                lambda: self._spotify_client.current_user_saved_tracks(limit=50)
            )

            # Convert to unified Track objects
            tracks = []

            # Add saved tracks
            if saved_tracks and "items" in saved_tracks:
                for item in saved_tracks["items"]:
                    if "track" in item:
                        track = self._spotify_track_to_track(item["track"])
                        if track:
                            tracks.append(track)

            # Add tracks from playlists (first 20 tracks from each playlist)
            if playlists and "items" in playlists:
                for playlist in playlists["items"]:
                    if (
                        playlist["owner"]["id"]
                        == self._spotify_client.current_user()["id"]
                    ):
                        # Only include owned playlists to avoid excessive API calls
                        playlist_tracks = await self._retry_operation(
                            lambda: self._spotify_client.playlist_tracks(
                                playlist["id"], limit=20
                            )
                        )

                        if playlist_tracks and "items" in playlist_tracks:
                            for item in playlist_tracks["items"]:
                                if "track" in item and item["track"]:
                                    track = self._spotify_track_to_track(item["track"])
                                    if track:
                                        tracks.append(track)

            # Update library
            self._library = tracks
            self._library_last_updated = time.time()

            # Rebuild cache
            self._library_cache.clear()
            for track in tracks:
                self._library_cache[track.track_id] = track

            self.logger.info(f"Spotify library refreshed: {len(tracks)} tracks")

        except Exception as e:
            self.logger.error(f"Failed to refresh Spotify library: {e}")

    def _spotify_track_to_track(self, spotify_track: Dict[str, Any]) -> Optional[Track]:
        """
        Convert a Spotify track object to unified Track format.

        Args:
            spotify_track: Spotify track object from API

        Returns:
            Optional[Track]: Unified track object or None if conversion failed
        """
        try:
            # Extract basic metadata
            track_id = spotify_track.get("id", "")
            title = spotify_track.get("name", "Unknown Title")

            # Get primary artist
            artists = spotify_track.get("artists", [])
            artist = artists[0]["name"] if artists else "Unknown Artist"

            # Get album info
            album_info = spotify_track.get("album", {})
            album = album_info.get("name")

            # Get duration (convert from milliseconds to seconds)
            duration_ms = spotify_track.get("duration_ms")
            duration = int(duration_ms / 1000) if duration_ms else None

            # Get preview URL (30-second preview)
            preview_url = spotify_track.get("preview_url")

            # Build additional metadata
            metadata = {
                "spotify_uri": spotify_track.get("uri"),
                "spotify_url": spotify_track.get("external_urls", {}).get("spotify"),
                "popularity": spotify_track.get("popularity"),
                "explicit": spotify_track.get("explicit", False),
                "all_artists": [a["name"] for a in artists],
                "album_type": album_info.get("album_type"),
                "release_date": album_info.get("release_date"),
                "preview_available": preview_url is not None,
            }

            # Create unified Track object
            track = Track(
                track_id=track_id,
                title=title,
                artist=artist,
                album=album,
                duration=duration,
                provider=self.name,
                source_path=preview_url,  # Use preview URL as source path
                metadata=metadata,
            )

            return track

        except Exception as e:
            self.logger.error(f"Failed to convert Spotify track: {e}")
            return None

    def _handle_rate_limit(self, exception: SpotifyException) -> None:
        """
        Handle Spotify API rate limiting.

        Args:
            exception: Spotify exception with rate limit info
        """
        self._rate_limited = True

        # Extract retry-after header if available
        retry_after = getattr(exception, "retry_after", None)
        if retry_after:
            self._rate_limit_reset = time.time() + retry_after
            self.logger.warning(
                f"Spotify API rate limited. Retry after {retry_after} seconds"
            )
        else:
            # Default to 60 seconds if no retry-after header
            self._rate_limit_reset = time.time() + 60
            self.logger.warning("Spotify API rate limited. Retry after 60 seconds")

    async def cleanup(self) -> None:
        """
        Clean up Spotify provider resources.
        """
        # Clear authentication
        self._spotify_client = None
        self._auth_manager = None

        # Clear caches
        self._user_playlists.clear()
        self._saved_tracks.clear()

        # Clear current state
        self._current_track = None
        self._current_preview_url = None

        # Call parent cleanup
        await super().cleanup()

        self.logger.info("Spotify music provider cleaned up")
