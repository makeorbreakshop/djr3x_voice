"""
Tests for the MusicControllerService.

These tests verify the functionality of the music controller including:
- Music library loading
- Playback control
- Mode-specific behavior
- Audio ducking during speech
"""

import os
import pytest
import asyncio
from unittest.mock import Mock, patch, AsyncMock, ANY
from typing import Dict, List
from pyee.asyncio import AsyncIOEventEmitter

from cantina_os.services.music_controller_service import MusicControllerService, MusicTrack
from cantina_os.event_bus import EventBus
from cantina_os.event_topics import EventTopics
from cantina_os.event_payloads import (
    MusicCommandPayload,
    BaseEventPayload,
    ServiceStatusPayload,
    ServiceStatus,
    SystemModePayload
)

# Test data
TEST_MUSIC_DIR = "test_assets/music"
TEST_TRACKS = {
    "cantina_band": "cantina_band.mp3",
    "imperial_march": "imperial_march.mp3"
}

@pytest.fixture
def mock_vlc():
    """Mock VLC instance and media player."""
    with patch("vlc.Instance") as mock_instance:
        mock_player = Mock()
        mock_player.audio_set_volume = Mock()
        mock_player.play = Mock()
        mock_player.stop = Mock()
        mock_player.release = Mock()
        
        mock_media = Mock()
        mock_media.get_duration = Mock(return_value=180000)  # 3 minutes in ms
        mock_media.parse = Mock()
        
        mock_instance.return_value.media_player_new = Mock(return_value=mock_player)
        mock_instance.return_value.media_new = Mock(return_value=mock_media)
        
        yield {
            "instance": mock_instance,
            "player": mock_player,
            "media": mock_media
        }

@pytest.fixture
async def music_service(mock_vlc, tmp_path):
    """Create a MusicControllerService instance with mocked dependencies."""
    # Create test music directory
    music_dir = tmp_path / "music"
    music_dir.mkdir()
    for track_name, filename in TEST_TRACKS.items():
        (music_dir / filename).touch()
    
    # Create event bus
    event_bus = EventBus()
    
    # Create service
    service = MusicControllerService(event_bus=event_bus, music_dir=str(music_dir))
    service.emit = AsyncMock()  # Mock the emit method
    
    await service.start()
    yield service
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

@pytest.mark.asyncio
async def test_play_request(music_service, mock_vlc):
    """Test handling of play requests."""
    # Create play request payload
    payload = MusicCommandPayload(
        action="play",
        song_query="cantina",
        conversation_id="test-convo"
    )
    
    # Send play request
    await music_service._handle_play_request(payload)
    
    # Verify player creation and playback
    mock_vlc["instance"].return_value.media_player_new.assert_called_once()
    mock_vlc["player"].play.assert_called_once()
    mock_vlc["player"].audio_set_volume.assert_called_once_with(music_service.normal_volume)
    
    # Verify state change event
    music_service.emit.assert_called_with(
        EventTopics.MUSIC_STATE_CHANGE,
        BaseEventPayload(
            conversation_id="test-convo",
            timestamp=ANY,
            event_id=ANY,
            schema_version="1.0"
        )
    )

@pytest.mark.asyncio
async def test_stop_request(music_service, mock_vlc):
    """Test handling of stop requests."""
    # First play something
    await music_service._handle_play_request(
        MusicCommandPayload(
            action="play",
            song_query="cantina",
            conversation_id="test-convo"
        )
    )
    
    # Send stop request
    await music_service._handle_stop_request(
        MusicCommandPayload(
            action="stop",
            conversation_id="test-convo"
        )
    )
    
    # Verify player stopped and released
    mock_vlc["player"].stop.assert_called_once()
    mock_vlc["player"].release.assert_called_once()
    assert music_service.player is None
    assert music_service.current_track is None

@pytest.mark.asyncio
async def test_mode_change(music_service, mock_vlc):
    """Test mode-specific behavior."""
    # First play something
    await music_service._handle_play_request(
        MusicCommandPayload(
            action="play",
            song_query="cantina",
            conversation_id="test-convo"
        )
    )
    
    # Change to IDLE mode
    await music_service._handle_mode_change(
        SystemModePayload(
            mode="IDLE",
            conversation_id="test-convo"
        )
    )
    
    # Verify playback stopped
    mock_vlc["player"].stop.assert_called_once()
    assert music_service.current_mode == "IDLE"

@pytest.mark.asyncio
async def test_speech_ducking(music_service, mock_vlc):
    """Test audio ducking during speech."""
    # Set interactive mode
    music_service.current_mode = "INTERACTIVE"
    
    # First play something
    await music_service._handle_play_request(
        MusicCommandPayload(
            action="play",
            song_query="cantina",
            conversation_id="test-convo"
        )
    )
    
    # Start speech
    await music_service._handle_speech_start(
        BaseEventPayload(conversation_id="test-convo")
    )
    
    # Verify volume ducked
    mock_vlc["player"].audio_set_volume.assert_called_with(music_service.ducking_volume)
    assert music_service.is_ducking is True
    
    # End speech
    await music_service._handle_speech_end(
        BaseEventPayload(conversation_id="test-convo")
    )
    
    # Verify volume restored
    mock_vlc["player"].audio_set_volume.assert_called_with(music_service.normal_volume)
    assert music_service.is_ducking is False

@pytest.mark.asyncio
async def test_get_track_list(music_service):
    """Test retrieving track list."""
    tracks = music_service.get_track_list()
    assert len(tracks) == len(TEST_TRACKS)
    for track in tracks:
        assert "name" in track
        assert "duration" in track
        assert track["duration"] == 180.0  # 3 minutes

@pytest.mark.asyncio
async def test_error_handling(music_service, mock_vlc):
    """Test error handling during playback."""
    # Make player.play raise an exception
    mock_vlc["player"].play.side_effect = Exception("Playback error")
    
    # Try to play
    await music_service._handle_play_request(
        MusicCommandPayload(
            action="play",
            song_query="cantina",
            conversation_id="test-convo"
        )
    )
    
    # Verify error event emitted
    music_service.emit.assert_called_with(
        EventTopics.MUSIC_ERROR,
        BaseEventPayload(
            conversation_id="test-convo",
            timestamp=ANY,
            event_id=ANY,
            schema_version="1.0"
        )
    )

@pytest.mark.asyncio
async def test_service_cleanup(music_service, mock_vlc):
    """Test proper cleanup during service stop."""
    # First play something to create resources
    await music_service._handle_play_request(
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
    music_service.emit.assert_any_call(
        EventTopics.MUSIC_STATE_CHANGE,
        BaseEventPayload(
            conversation_id=None,
            timestamp=ANY,
            event_id=ANY,
            schema_version="1.0"
        )
    )
    
    # Verify status update
    music_service.emit.assert_any_call(
        EventTopics.SERVICE_STATUS_UPDATE,
        ServiceStatusPayload(
            service_name=music_service.service_name,
            status=ServiceStatus.STOPPED,
            message="Music controller stopped",
            timestamp=ANY,
            event_id=ANY,
            schema_version="1.0"
        )
    ) 