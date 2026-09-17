"""
Three small lies in the startup log, each of which sends someone looking in the wrong place.

All three observed in the 2026-09-17 10:55 run:

1.  10:55:16,016  Loaded 22 music tracks from .../audio/music
    10:55:16,016  Music controller started with 21 tracks loaded across 1 source(s)

    Both numbers are printed 0 ms apart and they disagree. There are 22 files and 21 tracks:
    "Utinni.mp3" and "The Dusty Jawas - Utinni.mp3" both parse to the title "Utinni" and the
    library is keyed by title, so one silently replaces the other. The count that matters is
    the library's, and the collision is worth one line rather than a discrepancy to work out.

2.  10:57:18,719  WARNING Cannot reset mouth - adapter not initialized

    Once per reply, in mock mode, where having no adapter is the whole point of mock mode.
    On real hardware it is a genuine problem and must stay a warning.

3.  10:55:15,933  ERROR Error discovering Spotify device: error: invalid_grant, ... revoked
    10:55:15,933  ERROR No Spotify device found. Make sure Spotify app is running.

    Two ERROR lines for one optional, disabled-by-absence integration - and the second one is
    misleading advice, because the Spotify app being closed is not why it failed. The caller
    already logs a WARNING for the failed backend.
"""

import glob
import logging
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from cantina_os.services.music_controller_service.music_controller_service import (
    MusicControllerService,
)

MUSIC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "audio",
    "music",
)


@pytest.fixture(autouse=True)
def mock_external_apis():
    """Override conftest's autouse fixture, which raises on deepgram-sdk 5.x."""
    yield {}


# =========================================================================================
# 1. One track count, not two
# =========================================================================================

@pytest.fixture
def loader():
    """The real `_load_music_library` over the real music directory, without VLC."""
    svc = MusicControllerService.__new__(MusicControllerService)
    svc._service_name = "music_controller_test"
    svc._logger = logging.getLogger("cantina_os.MusicController")
    svc.emit = AsyncMock()
    svc.get_track_list = AsyncMock(return_value=[])
    svc.vlc_instance = MagicMock()
    svc.music_dir = MUSIC_DIR
    svc.libraries = {"local": {}, "spotify": {}}
    svc.active_source = "local"
    svc.tracks = {}
    return svc


class TestTrackCount:
    def test_the_library_really_does_have_a_title_collision(self):
        """Guards the premise: 22 files, two of which are both "Utinni"."""
        files = glob.glob(os.path.join(MUSIC_DIR, "*.mp3"))
        assert len(files) == 22
        titles = [os.path.basename(f).rsplit(".", 1)[0].split(" - ", 1)[-1] for f in files]
        assert titles.count("Utinni") == 2

    async def test_the_reported_count_is_the_library_count(self, loader):
        loaded = await loader._load_music_library()
        assert loaded == len(loader.libraries["local"]) == 21, (
            f"reported {loaded} but the library holds {len(loader.libraries['local'])}"
        )

    async def test_the_collision_is_reported_once_and_names_the_title(self, loader, caplog):
        with caplog.at_level(logging.DEBUG, logger="cantina_os.MusicController"):
            await loader._load_music_library()

        collision_lines = [
            r.getMessage()
            for r in caplog.records
            if "duplicate" in r.getMessage().lower() and "Utinni" in r.getMessage()
        ]
        assert collision_lines, (
            "the dropped duplicate is not mentioned anywhere; the only clue was two "
            "disagreeing counts"
        )

    async def test_no_line_claims_22_tracks(self, loader, caplog):
        with caplog.at_level(logging.DEBUG, logger="cantina_os.MusicController"):
            await loader._load_music_library()

        assert not [
            r.getMessage() for r in caplog.records if "22 music tracks" in r.getMessage()
        ], "the file count is still being reported as a track count"


# =========================================================================================
# 2. A missing adapter is expected in mock mode
# =========================================================================================

@pytest.fixture
def eyes():
    from cantina_os.services.eye_light_controller_service import (
        EyeLightControllerService,
    )

    svc = EyeLightControllerService.__new__(EyeLightControllerService)
    svc._service_name = "eye_light_controller_test"
    svc._logger = logging.getLogger("cantina_os.eye_light_controller")
    svc.emit = AsyncMock()
    svc.adapter = None
    svc.mock_mode = True
    svc._amplitude_modulation = 0.0
    svc._last_mouth_level = -1
    svc._is_in_interactive_mode = lambda: True
    return svc


class TestEyeMouthReset:
    async def test_mock_mode_does_not_warn(self, eyes, caplog):
        with caplog.at_level(logging.DEBUG, logger="cantina_os.eye_light_controller"):
            await eyes._handle_speech_ended({})

        warnings = [
            r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING
        ]
        assert not warnings, (
            f"mock mode still warns about the thing mock mode means: {warnings}"
        )

    async def test_real_hardware_without_an_adapter_still_warns(self, eyes, caplog):
        """Don't silence the case that is genuinely broken."""
        eyes.mock_mode = False
        with caplog.at_level(logging.DEBUG, logger="cantina_os.eye_light_controller"):
            await eyes._handle_speech_ended({})

        warnings = [
            r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING
        ]
        assert warnings, "a missing adapter on real hardware must stay visible"


# =========================================================================================
# 3. One WARN for an unavailable optional integration
# =========================================================================================

@pytest.fixture
def spotify():
    from cantina_os.services.music_controller_service import music_backends

    if not music_backends.SPOTIPY_AVAILABLE:
        pytest.skip("spotipy not installed")

    backend = music_backends.SpotifyMusicBackend.__new__(
        music_backends.SpotifyMusicBackend
    )
    backend.logger = logging.getLogger("cantina_os.MusicController")
    backend.config = {"client_id": "x", "client_secret": "y"}
    backend.device_id = None
    backend._current_volume = 70

    class RevokedTokenSpotify:
        def devices(self):
            raise Exception(
                "error: invalid_grant, error_description: Refresh token revoked"
            )

    backend.sp = RevokedTokenSpotify()
    return backend


class TestSpotifyUnavailable:
    async def test_a_revoked_token_is_one_warning_not_two_errors(self, spotify, caplog):
        with caplog.at_level(logging.DEBUG, logger="cantina_os.MusicController"):
            assert await spotify._discover_device() is None

        errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
        warnings = [
            r.getMessage() for r in caplog.records if r.levelno == logging.WARNING
        ]
        assert not errors, f"still logged at ERROR: {errors}"
        assert len(warnings) == 1, f"expected exactly one WARNING, got {warnings}"
        assert "revoked" in warnings[0].lower(), (
            f"the warning does not say what actually went wrong: {warnings[0]}"
        )
