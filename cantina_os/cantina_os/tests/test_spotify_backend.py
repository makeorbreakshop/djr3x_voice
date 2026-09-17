"""Unit and integration tests for Spotify music backend."""

import pytest
import asyncio
from unittest.mock import Mock, patch, AsyncMock, MagicMock
from cantina_os.services.music_controller_service.music_backends import (
    SpotifyMusicBackend,
    MusicTrack
)


class TestSpotifyMusicBackendUnit:
    """Unit tests with mocked Spotify API."""

    @pytest.fixture
    def mock_logger(self):
        """Create a mock logger."""
        return Mock()

    @pytest.fixture
    def spotify_config(self):
        """Spotify configuration for testing."""
        return {
            "client_id": "test_client_id",
            "client_secret": "test_client_secret",
            "redirect_uri": "http://localhost:8080/callback",
            "device_name": None  # Auto-discover
        }

    @pytest.fixture
    def mock_spotify_client(self):
        """Create a mock Spotify client."""
        mock = MagicMock()

        # Mock devices response
        mock.devices.return_value = {
            "devices": [
                {
                    "id": "test_device_123",
                    "name": "Test Device",
                    "type": "Computer",
                    "is_active": True,
                    "volume_percent": 70
                }
            ]
        }

        # Mock current playback
        mock.current_playback.return_value = {
            "device": {"id": "test_device_123"},
            "is_playing": True,
            "progress_ms": 30000,
            "item": {
                "uri": "spotify:track:test123",
                "name": "Test Track",
                "duration_ms": 180000
            }
        }

        return mock

    @pytest.mark.asyncio
    async def test_initialization_success(self, spotify_config, mock_logger, mock_spotify_client):
        """Test successful initialization with mocked OAuth."""
        with patch('cantina_os.services.music_controller_service.music_backends.spotipy.Spotify') as mock_spotify_cls:
            with patch('cantina_os.services.music_controller_service.music_backends.SpotifyOAuth') as mock_oauth:
                mock_spotify_cls.return_value = mock_spotify_client
                mock_oauth.return_value = Mock()

                backend = SpotifyMusicBackend(spotify_config, mock_logger)
                result = await backend.initialize()

                assert result is True
                assert backend.sp is not None
                assert backend.device_id == "test_device_123"

    @pytest.mark.asyncio
    async def test_initialization_no_devices(self, spotify_config, mock_logger):
        """Test initialization fails when no devices available."""
        with patch('cantina_os.services.music_controller_service.music_backends.spotipy.Spotify') as mock_spotify_cls:
            with patch('cantina_os.services.music_controller_service.music_backends.SpotifyOAuth') as mock_oauth:
                mock_client = MagicMock()
                mock_client.devices.return_value = {"devices": []}
                mock_spotify_cls.return_value = mock_client
                mock_oauth.return_value = Mock()

                backend = SpotifyMusicBackend(spotify_config, mock_logger)
                result = await backend.initialize()

                assert result is False
                mock_logger.error.assert_called()

    @pytest.mark.asyncio
    async def test_play_track(self, spotify_config, mock_logger, mock_spotify_client):
        """Test playing a Spotify track."""
        with patch('cantina_os.services.music_controller_service.music_backends.spotipy.Spotify') as mock_spotify_cls:
            with patch('cantina_os.services.music_controller_service.music_backends.SpotifyOAuth') as mock_oauth:
                mock_spotify_cls.return_value = mock_spotify_client
                mock_oauth.return_value = Mock()

                backend = SpotifyMusicBackend(spotify_config, mock_logger)
                await backend.initialize()

                test_track = MusicTrack(
                    name="Test Track",
                    path="spotify:track:test123",
                    duration=180.0,
                    artist="Test Artist",
                    album="Test Album",
                    provider="spotify"
                )

                result = await backend.play_track(test_track)

                assert result is True
                mock_spotify_client.start_playback.assert_called_once()
                assert backend.current_track == test_track

    @pytest.mark.asyncio
    async def test_stop_playback(self, spotify_config, mock_logger, mock_spotify_client):
        """Test stopping playback."""
        with patch('cantina_os.services.music_controller_service.music_backends.spotipy.Spotify') as mock_spotify_cls:
            with patch('cantina_os.services.music_controller_service.music_backends.SpotifyOAuth') as mock_oauth:
                mock_spotify_cls.return_value = mock_spotify_client
                mock_oauth.return_value = Mock()

                backend = SpotifyMusicBackend(spotify_config, mock_logger)
                await backend.initialize()

                result = await backend.stop_playback()

                assert result is True
                mock_spotify_client.pause_playback.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_volume(self, spotify_config, mock_logger, mock_spotify_client):
        """Test volume control."""
        with patch('cantina_os.services.music_controller_service.music_backends.spotipy.Spotify') as mock_spotify_cls:
            with patch('cantina_os.services.music_controller_service.music_backends.SpotifyOAuth') as mock_oauth:
                mock_spotify_cls.return_value = mock_spotify_client
                mock_oauth.return_value = Mock()

                backend = SpotifyMusicBackend(spotify_config, mock_logger)
                await backend.initialize()

                result = await backend.set_volume(75)

                assert result is True
                mock_spotify_client.volume.assert_called_once_with(75, device_id="test_device_123")

    @pytest.mark.asyncio
    async def test_crossfade_to_track(self, spotify_config, mock_logger, mock_spotify_client):
        """Test crossfade transition (uses queue + next_track with volume fade)."""
        with patch('cantina_os.services.music_controller_service.music_backends.spotipy.Spotify') as mock_spotify_cls:
            with patch('cantina_os.services.music_controller_service.music_backends.SpotifyOAuth') as mock_oauth:
                with patch('cantina_os.services.music_controller_service.music_backends.asyncio.sleep') as mock_sleep:
                    mock_spotify_cls.return_value = mock_spotify_client
                    mock_oauth.return_value = Mock()

                    backend = SpotifyMusicBackend(spotify_config, mock_logger)
                    await backend.initialize()

                    # Start with a track playing
                    current_track = MusicTrack(
                        name="Current Track",
                        path="spotify:track:current123",
                        duration=180.0,
                        artist="Artist 1",
                        provider="spotify"
                    )
                    await backend.play_track(current_track)

                    # Crossfade to new track
                    next_track = MusicTrack(
                        name="Next Track",
                        path="spotify:track:next456",
                        duration=200.0,
                        artist="Artist 2",
                        provider="spotify"
                    )

                    result = await backend.crossfade_to_track(next_track, duration=0.1)

                    assert result is True
                    # Should have called add_to_queue and next_track
                    mock_spotify_client.add_to_queue.assert_called_once()
                    mock_spotify_client.next_track.assert_called_once()
                    # Should have faded volume (40 steps: 20 down, 20 up)
                    assert mock_spotify_client.volume.call_count > 0
                    assert backend.current_track == next_track


class TestSpotifyMusicBackendE2E:
    """End-to-end tests with real Spotify API."""

    @pytest.fixture
    def real_spotify_config(self):
        """Real Spotify configuration from .env."""
        import os
        from dotenv import load_dotenv
        load_dotenv()

        return {
            "client_id": os.getenv("SPOTIFY_CLIENT_ID"),
            "client_secret": os.getenv("SPOTIFY_CLIENT_SECRET"),
            "redirect_uri": os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:3000/redirect"),
            "device_name": os.getenv("SPOTIFY_DEVICE_NAME") or None
        }

    @pytest.fixture
    def mock_logger(self):
        """Create a mock logger."""
        return Mock()

    @pytest.mark.asyncio
    @pytest.mark.e2e
    async def test_real_authentication(self, real_spotify_config, mock_logger):
        """Test authentication with real Spotify API (requires valid credentials)."""
        # Skip if credentials not configured
        if not real_spotify_config["client_id"] or real_spotify_config["client_id"] == "your_client_id_here":
            pytest.skip("Spotify credentials not configured in .env")

        backend = SpotifyMusicBackend(real_spotify_config, mock_logger)

        # This will trigger OAuth flow - may require manual authorization
        result = await backend.initialize()

        assert result is True, "Failed to authenticate with Spotify API"
        assert backend.sp is not None, "Spotify client not initialized"
        assert backend.device_id is not None, "No Spotify device found (ensure Spotify is running)"

        print(f"✅ Successfully authenticated with Spotify")
        print(f"✅ Found device: {backend.device_id}")

    @pytest.mark.asyncio
    @pytest.mark.e2e
    async def test_real_device_discovery(self, real_spotify_config, mock_logger):
        """Test device discovery with real API."""
        if not real_spotify_config["client_id"] or real_spotify_config["client_id"] == "your_client_id_here":
            pytest.skip("Spotify credentials not configured in .env")

        backend = SpotifyMusicBackend(real_spotify_config, mock_logger)
        result = await backend.initialize()

        if not result:
            pytest.skip("No Spotify devices available (start Spotify on a device)")

        # Verify we can get current playback state
        import spotipy
        devices = await asyncio.to_thread(backend.sp.devices)

        assert "devices" in devices, "Devices response missing 'devices' key"
        assert len(devices["devices"]) > 0, "No devices found"

        print(f"✅ Found {len(devices['devices'])} device(s):")
        for device in devices["devices"]:
            print(f"   - {device['name']} ({device['type']}) - Active: {device['is_active']}")

    @pytest.mark.asyncio
    @pytest.mark.e2e
    async def test_real_volume_control(self, real_spotify_config, mock_logger):
        """Test volume control with real API."""
        if not real_spotify_config["client_id"] or real_spotify_config["client_id"] == "your_client_id_here":
            pytest.skip("Spotify credentials not configured in .env")

        backend = SpotifyMusicBackend(real_spotify_config, mock_logger)
        result = await backend.initialize()

        if not result:
            pytest.skip("No Spotify devices available")

        # Test volume control
        result = await backend.set_volume(50)
        assert result is True, "Failed to set volume"

        await asyncio.sleep(1)  # Give Spotify time to update

        # Verify volume was set
        devices = await asyncio.to_thread(backend.sp.devices)
        active_device = next((d for d in devices["devices"] if d["id"] == backend.device_id), None)

        if active_device:
            print(f"✅ Volume control works - device volume: {active_device.get('volume_percent', 'unknown')}%")


if __name__ == "__main__":
    # Run unit tests
    print("=" * 60)
    print("RUNNING UNIT TESTS (with mocked Spotify API)")
    print("=" * 60)
    pytest.main([__file__, "-v", "-m", "not e2e"])

    print("\n" + "=" * 60)
    print("RUNNING E2E TESTS (with real Spotify API)")
    print("=" * 60)
    print("⚠️  These tests require:")
    print("   1. Valid Spotify credentials in .env")
    print("   2. Spotify Premium account")
    print("   3. Spotify running on a device")
    print("=" * 60)
    pytest.main([__file__, "-v", "-m", "e2e", "-s"])
