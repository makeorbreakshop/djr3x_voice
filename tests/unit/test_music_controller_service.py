"""
Unit tests for the MusicControllerService.

These tests verify the functionality of the music controller including:
- Service lifecycle (initialization, start, stop)
- Music library loading and management
- Playback control
- Mode-specific behavior
- Audio ducking during speech
"""

import os
import pytest
import pytest_asyncio
import asyncio
from unittest.mock import Mock, patch, AsyncMock, ANY, call
from pathlib import Path
from pyee.asyncio import AsyncIOEventEmitter

from cantina_os.services.music_controller_service import MusicControllerService, MusicTrack
from cantina_os.event_topics import EventTopics
from cantina_os.event_payloads import (
    MusicCommandPayload,
    BaseEventPayload,
    ServiceStatusPayload,
    ServiceStatus,
    SystemModePayload,
    LogLevel
)

# Test data
TEST_TRACKS = {
    "cantina_band": "cantina_band.mp3",
    "imperial_march": "imperial_march.mp3",
    "yoda_theme": "yoda_theme.mp3"
}

@pytest.fixture
def mock_vlc():
    """Mock VLC instance and media player."""
    with patch("vlc.Instance") as mock_instance:
        # Create mock player
        mock_player = Mock()
        mock_player.audio_set_volume = Mock()
        mock_player.play = Mock()
        mock_player.stop = Mock()
        mock_player.release = Mock()
        mock_player.is_playing = Mock(return_value=False)
        
        # Create mock media
        mock_media = Mock()
        mock_media.get_duration = Mock(return_value=180000)  # 3 minutes in ms
        mock_media.parse = Mock()
        
        # Configure instance
        mock_instance.return_value.media_player_new = Mock(return_value=mock_player)
        mock_instance.return_value.media_new = Mock(return_value=mock_media)
        mock_instance.return_value.release = Mock()
        
        yield {
            "instance": mock_instance,
            "player": mock_player,
            "media": mock_media
        }

@pytest_asyncio.fixture
async def music_service(mock_vlc, tmp_path):
    """Create a MusicControllerService instance with mocked dependencies."""
    # Create test music directory with sample files
    music_dir = tmp_path / "music"
    music_dir.mkdir()
    for track_name, filename in TEST_TRACKS.items():
        (music_dir / filename).touch()
    
    # Create event bus
    event_bus = AsyncIOEventEmitter()
    
    # Create service
    service = MusicControllerService(
        event_bus=event_bus,
        music_dir=str(music_dir)
    )
    
    # Mock emit method for verification
    service.emit = AsyncMock()
    service._emit_status = AsyncMock()
    
    # Start service
    await service.start()
    
    # Important: Return the service instance directly
    yield service
    
    # Cleanup after tests
    await service.stop()

@pytest.mark.asyncio
async def test_service_initialization(music_service, mock_vlc):
    """Test service initialization and music library loading."""
    # Verify VLC instance creation
    mock_vlc["instance"].assert_called_once()
    
    # Verify tracks were loaded
    assert len(music_service.tracks) == len(TEST_TRACKS)
    for track_name in TEST_TRACKS.keys():
        assert any(track_name in track.name for track in music_service.tracks.values())
    
    # Verify initial state
    assert music_service.current_track is None
    assert music_service.player is None
    assert music_service.current_mode == "IDLE"
    assert music_service.normal_volume == 70
    assert music_service.ducking_volume == 30
    assert not music_service.is_ducking
    
    # Verify status events
    music_service._emit_status.assert_called_with(
        ServiceStatus.RUNNING,
        "Music controller started"
    )

@pytest.mark.asyncio
async def test_play_request(music_service, mock_vlc):
    """Test handling of play requests."""
    # Reset mock counts after initialization
    mock_vlc["instance"].return_value.media_new.reset_mock()
    
    # Send play request
    await music_service._handle_music_command(
        MusicCommandPayload(
            action="play",
            song_query="cantina",
            conversation_id="test-convo"
        )
    )
    
    # Verify player setup
    mock_vlc["instance"].return_value.media_player_new.assert_called_once()
    mock_vlc["instance"].return_value.media_new.assert_called_once()
    mock_vlc["player"].play.assert_called_once()
    
    # Verify volume setting
    mock_vlc["player"].audio_set_volume.assert_called_with(music_service.normal_volume)
    
    # Verify event emission
    assert music_service.emit.call_args is not None
    args = music_service.emit.call_args[0]
    assert args[0] == EventTopics.MUSIC_PLAYBACK_STARTED
    assert isinstance(args[1], BaseEventPayload)
    assert args[1].conversation_id == "test-convo"
    assert args[1].schema_version == "1.0"

@pytest.mark.asyncio
async def test_stop_request(music_service, mock_vlc):
    """Test handling of stop requests."""
    # First play something
    await music_service._handle_music_command(
        MusicCommandPayload(
            action="play",
            song_query="cantina",
            conversation_id="test-convo"
        )
    )
    
    # Send stop request
    await music_service._handle_music_command(
        MusicCommandPayload(
            action="stop",
            conversation_id="test-convo"
        )
    )
    
    # Verify player cleanup
    mock_vlc["player"].stop.assert_called_once()
    mock_vlc["player"].release.assert_called_once()
    assert music_service.player is None
    assert music_service.current_track is None
    
    # Verify event emission
    assert music_service.emit.call_args is not None
    args = music_service.emit.call_args[0]
    assert args[0] == EventTopics.MUSIC_PLAYBACK_STOPPED
    assert isinstance(args[1], BaseEventPayload)
    assert args[1].conversation_id == "test-convo"
    assert args[1].schema_version == "1.0"

@pytest.mark.asyncio
async def test_mode_change_behavior(music_service, mock_vlc):
    """Test mode-specific behavior."""
    # Start in AMBIENT mode with music
    await music_service._handle_mode_change(
        SystemModePayload(
            mode="AMBIENT",
            conversation_id="test-convo"
        )
    )
    await music_service._handle_music_command(
        MusicCommandPayload(
            action="play",
            song_query="cantina",
            conversation_id="test-convo"
        )
    )
    
    # Switch to IDLE mode - should stop music
    await music_service._handle_mode_change(
        SystemModePayload(
            mode="IDLE",
            conversation_id="test-convo"
        )
    )
    
    # Verify music stopped
    mock_vlc["player"].stop.assert_called_once()
    assert music_service.player is None
    assert music_service.current_track is None

@pytest.mark.asyncio
async def test_audio_ducking(music_service, mock_vlc):
    """Test audio ducking during speech."""
    # Set up initial playback in INTERACTIVE mode
    await music_service._handle_mode_change(
        SystemModePayload(
            mode="INTERACTIVE",
            conversation_id="test-convo"
        )
    )
    await music_service._handle_music_command(
        MusicCommandPayload(
            action="play",
            song_query="cantina",
            conversation_id="test-convo"
        )
    )
    
    # Simulate speech start
    await music_service._handle_speech_start(
        BaseEventPayload(
            conversation_id="test-convo",
            timestamp=1234567890.0,  # Use a fixed timestamp for testing
            event_id="test-event-id",  # Use a fixed event ID for testing
            schema_version="1.0"
        )
    )
    
    # Verify volume reduction
    assert music_service.is_ducking
    mock_vlc["player"].audio_set_volume.assert_called_with(music_service.ducking_volume)
    
    # Simulate speech end
    await music_service._handle_speech_end(
        BaseEventPayload(
            conversation_id="test-convo",
            timestamp=1234567891.0,  # Use a fixed timestamp for testing
            event_id="test-event-id-2",  # Use a fixed event ID for testing
            schema_version="1.0"
        )
    )
    
    # Verify volume restoration
    assert not music_service.is_ducking
    mock_vlc["player"].audio_set_volume.assert_called_with(music_service.normal_volume)

@pytest.mark.asyncio
async def test_track_listing(music_service):
    """Test track listing functionality."""
    track_list = music_service.get_track_list()
    
    # Verify track list contents
    assert len(track_list) == len(TEST_TRACKS)
    for track in track_list:
        assert isinstance(track, dict)
        assert "name" in track
        assert "duration" in track
        assert any(track["name"] in filename for filename in TEST_TRACKS.values())
        assert track["duration"] == 180.0  # 3 minutes

@pytest.mark.asyncio
async def test_service_cleanup(music_service, mock_vlc):
    """Test proper cleanup during service stop."""
    # First play something to create resources
    await music_service._handle_music_command(
        MusicCommandPayload(
            action="play",
            song_query="cantina",
            conversation_id="test-convo"
        )
    )
    
    # Stop the service
    await music_service.stop()
    
    # Verify player cleanup
    mock_vlc["player"].stop.assert_called_once()
    mock_vlc["player"].release.assert_called_once()
    assert music_service.player is None
    assert music_service.current_track is None
    
    # Verify VLC instance cleanup
    mock_vlc["instance"].return_value.release.assert_called_once()
    assert music_service.vlc_instance is None
    
    # Verify events were emitted
    assert any(
        call[0][0] == EventTopics.MUSIC_PLAYBACK_STOPPED and
        isinstance(call[0][1], BaseEventPayload) and
        call[0][1].conversation_id is None and
        call[0][1].schema_version == "1.0"
        for call in music_service.emit.call_args_list
    )
    
    # Verify status update
    music_service._emit_status.assert_called_with(
        ServiceStatus.STOPPED,
        "MusicController stopped"
    )

@pytest.mark.asyncio
async def test_error_handling(music_service, mock_vlc):
    """Test error handling during playback operations."""
    # Make player.play raise an exception
    mock_vlc["player"].play.side_effect = Exception("VLC Error")
    
    # Try to play a track
    await music_service._handle_music_command(
        MusicCommandPayload(
            action="play",
            song_query="cantina",
            conversation_id="test-convo"
        )
    )
    
    # Verify error event emission
    assert music_service.emit.call_args is not None
    args = music_service.emit.call_args[0]
    assert args[0] == EventTopics.MUSIC_ERROR
    assert isinstance(args[1], BaseEventPayload)
    assert args[1].conversation_id == "test-convo"
    assert args[1].schema_version == "1.0"
    
    # Verify service remains in a valid state
    assert music_service.player is None
    assert music_service.current_track is None 