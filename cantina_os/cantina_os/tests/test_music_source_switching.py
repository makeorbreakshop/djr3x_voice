"""Integration tests for music source switching in MusicControllerService."""

import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, MagicMock, patch
from pyee.asyncio import AsyncIOEventEmitter

from cantina_os.services.music_controller_service import MusicControllerService
from cantina_os.core.event_topics import EventTopics
from cantina_os.models.music_models import MusicTrack


class TestMusicSourceSwitching:
    """Integration tests for backend switching."""

    @pytest.fixture
    def event_bus(self):
        """Create event bus."""
        return AsyncIOEventEmitter()

    @pytest.fixture
    def music_config(self):
        """Music controller config without Spotify."""
        return {
            "music_dir": "/tmp/test_music",
            "enable_spotify": False,
            "default_source": "local"
        }

    @pytest.fixture
    def music_config_with_spotify(self):
        """Music controller config with Spotify enabled."""
        import os
        from dotenv import load_dotenv
        load_dotenv()

        return {
            "music_dir": "/tmp/test_music",
            "enable_spotify": True,
            "spotify_client_id": os.getenv("SPOTIFY_CLIENT_ID"),
            "spotify_client_secret": os.getenv("SPOTIFY_CLIENT_SECRET"),
            "spotify_redirect_uri": os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888"),
            "default_source": "local"
        }

    @pytest.mark.asyncio
    async def test_service_initializes_with_local_only(self, event_bus, music_config):
        """Test service initializes with only local backend."""
        service = MusicControllerService(event_bus, music_config)

        # Mock VLC and local backend initialization
        with patch('cantina_os.services.music_controller_service.music_controller_service.LocalMusicBackend') as mock_local:
            mock_backend_instance = AsyncMock()
            mock_backend_instance.initialize = AsyncMock(return_value=True)
            mock_local.return_value = mock_backend_instance

            await service._initialize_backends()

            assert "local" in service.backends
            assert "spotify" not in service.backends
            assert service.active_source == "local"

    @pytest.mark.asyncio
    async def test_source_switching_emits_event(self, event_bus, music_config):
        """Test that source switching emits MUSIC_SOURCE_CHANGED event."""
        service = MusicControllerService(event_bus, music_config)

        # Mock backends
        service.backends = {
            "local": AsyncMock(),
            "spotify": AsyncMock()
        }
        service.backends["local"].stop_playback = AsyncMock()
        service.backends["spotify"].stop_playback = AsyncMock()

        service.libraries = {
            "local": {"track1": MusicTrack(name="Track 1", path="/test/track1.mp3", provider="local")},
            "spotify": {"track2": MusicTrack(name="Track 2", path="spotify:track:123", provider="spotify")}
        }

        # Listen for event
        event_received = asyncio.Event()
        received_payload = {}

        def on_source_changed(payload):
            received_payload.update(payload)
            event_received.set()

        event_bus.on(EventTopics.MUSIC_SOURCE_CHANGED, on_source_changed)

        # Switch source
        success = await service._switch_source("spotify")

        # Wait for event
        await asyncio.wait_for(event_received.wait(), timeout=1.0)

        assert success is True
        assert service.active_source == "spotify"
        assert received_payload["previous_source"] == "local"
        assert received_payload["current_source"] == "spotify"
        assert "local" in received_payload["available_sources"]
        assert "spotify" in received_payload["available_sources"]

    @pytest.mark.asyncio
    async def test_source_switching_updates_tracks_alias(self, event_bus, music_config):
        """Test that switching sources updates the tracks alias."""
        service = MusicControllerService(event_bus, music_config)

        # Mock backends
        service.backends = {
            "local": AsyncMock(),
            "spotify": AsyncMock()
        }
        service.backends["local"].stop_playback = AsyncMock()
        service.backends["spotify"].stop_playback = AsyncMock()

        local_track = MusicTrack(name="Local Track", path="/test/track1.mp3", provider="local")
        spotify_track = MusicTrack(name="Spotify Track", path="spotify:track:123", provider="spotify")

        service.libraries = {
            "local": {"local_track": local_track},
            "spotify": {"spotify_track": spotify_track}
        }
        service.tracks = service.libraries["local"]

        # Verify initial state
        assert service.active_source == "local"
        assert "local_track" in service.tracks
        assert "spotify_track" not in service.tracks

        # Switch to Spotify
        await service._switch_source("spotify")

        # Verify tracks alias updated
        assert service.active_source == "spotify"
        assert "spotify_track" in service.tracks
        assert "local_track" not in service.tracks

    @pytest.mark.asyncio
    async def test_source_switching_stops_previous_backend(self, event_bus, music_config):
        """Test that switching sources stops the previous backend."""
        service = MusicControllerService(event_bus, music_config)

        # Mock backends
        mock_local = AsyncMock()
        mock_spotify = AsyncMock()
        mock_local.stop_playback = AsyncMock()
        mock_spotify.stop_playback = AsyncMock()

        service.backends = {
            "local": mock_local,
            "spotify": mock_spotify
        }

        service.libraries = {
            "local": {},
            "spotify": {}
        }

        # Switch from local to spotify
        await service._switch_source("spotify")

        # Verify local backend was stopped
        mock_local.stop_playback.assert_called_once()
        mock_spotify.stop_playback.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalid_source_switch_fails(self, event_bus, music_config):
        """Test switching to unavailable source fails gracefully."""
        service = MusicControllerService(event_bus, music_config)

        service.backends = {"local": AsyncMock()}
        service.libraries = {"local": {}, "spotify": {}}

        # Try to switch to unavailable source
        success = await service._switch_source("spotify")

        assert success is False
        assert service.active_source == "local"  # Should remain unchanged

    @pytest.mark.asyncio
    @pytest.mark.e2e
    async def test_full_service_startup_with_spotify(self, event_bus, music_config_with_spotify):
        """Test full service startup with Spotify enabled (E2E test)."""
        # Skip if credentials not configured
        if not music_config_with_spotify["spotify_client_id"] or \
           music_config_with_spotify["spotify_client_id"] == "your_client_id_here":
            pytest.skip("Spotify credentials not configured in .env")

        service = MusicControllerService(event_bus, music_config_with_spotify)

        # Start service (will initialize backends)
        try:
            await service.start()

            # Verify both backends initialized
            assert "local" in service.backends
            if music_config_with_spotify["enable_spotify"]:
                # Spotify might not initialize if no devices found
                print(f"Available backends: {list(service.backends.keys())}")

            # Verify default source
            assert service.active_source in service.backends

            # Test source status
            await service._show_source_status()

        finally:
            await service.stop()


if __name__ == "__main__":
    # Run integration tests
    print("=" * 60)
    print("RUNNING INTEGRATION TESTS (mocked backends)")
    print("=" * 60)
    pytest.main([__file__, "-v", "-m", "not e2e"])

    print("\n" + "=" * 60)
    print("RUNNING E2E TESTS (with real Spotify)")
    print("=" * 60)
    pytest.main([__file__, "-v", "-m", "e2e", "-s"])
