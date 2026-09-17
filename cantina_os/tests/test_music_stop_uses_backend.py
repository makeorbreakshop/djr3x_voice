"""The stop path must reach the backend that actually owns the audio.

Regression test for a bug that ``scripts/claude_live_verify.py`` found and no unit test
could have: playback moved to pluggable backends (``LocalMusicBackend`` owns its own VLC
player) but ``_stop_playback`` still gated on ``MusicControllerService.player``, which
nothing assigns any more. Every stop therefore short-circuited on "No music is currently
playing" and the track kept playing - while ``MUSIC_COMMAND`` landed on the bus and every
existing assertion passed.

The tests drive the real ``_stop_playback`` with a recording backend, so the thing under
test is the production method, not a re-implementation.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from cantina_os.services.music_controller_service.music_controller_service import (
    MusicControllerService,
)


class RecordingBackend:
    def __init__(self) -> None:
        self.stop_calls = 0

    async def stop_playback(self) -> bool:
        self.stop_calls += 1
        return True


@pytest.fixture
def service():
    """A MusicControllerService with the bus and VLC stubbed, nothing else."""
    svc = MusicControllerService.__new__(MusicControllerService)
    svc._service_name = "music_controller_test"
    svc._logger = MagicMock()
    svc.emit = AsyncMock()
    svc._send_success = AsyncMock()
    svc._send_error = AsyncMock()
    svc._cleanup_player = AsyncMock()
    svc.player = None
    svc.secondary_player = None
    svc.current_track = None
    svc.track_end_timer = None
    svc.is_ducking = True
    svc.active_source = "local"
    svc.backends = {"local": RecordingBackend()}
    return svc


def _track(name="Huttuk Cheeka"):
    t = MagicMock()
    t.name = name
    t.title = name
    return t


class TestStopReachesTheBackend:
    async def test_stop_calls_the_active_backend(self, service):
        """The bug: this was zero because self.player was None."""
        service.current_track = _track()
        await service._stop_playback()
        assert service.backends["local"].stop_calls == 1

    async def test_stop_emits_playback_stopped(self, service):
        service.current_track = _track()
        await service._stop_playback()
        topics = [c.args[0] for c in service.emit.await_args_list]
        assert any("stopped" in str(t).lower() for t in topics)

    async def test_stop_clears_the_current_track(self, service):
        service.current_track = _track()
        await service._stop_playback()
        assert service.current_track is None

    async def test_stop_resets_ducking(self, service):
        service.current_track = _track()
        await service._stop_playback()
        assert service.is_ducking is False

    async def test_stop_reports_success_not_nothing_playing(self, service):
        service.current_track = _track()
        await service._stop_playback()
        messages = [c.args[0] for c in service._send_success.await_args_list]
        assert not any("No music is currently playing" in m for m in messages)
        assert any("Stopped" in m for m in messages)


class TestNothingPlaying:
    async def test_no_track_and_no_player_short_circuits(self, service):
        await service._stop_playback()
        assert service.backends["local"].stop_calls == 0
        messages = [c.args[0] for c in service._send_success.await_args_list]
        assert messages == ["No music is currently playing"]

    async def test_a_crossfade_player_alone_still_stops(self, service):
        """The crossfade path assigns self.player directly and may leave current_track unset."""
        player = MagicMock()
        service.player = player
        await service._stop_playback()
        player.stop.assert_called_once()
        assert service.player is None
        service._cleanup_player.assert_awaited_once()

    async def test_a_missing_backend_does_not_raise(self, service):
        service.current_track = _track()
        service.active_source = "spotify"  # no backend registered
        await service._stop_playback()
        assert service.current_track is None
