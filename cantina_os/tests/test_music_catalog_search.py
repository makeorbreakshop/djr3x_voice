import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from pyee.asyncio import AsyncIOEventEmitter

from cantina_os.core.event_topics import EventTopics
from cantina_os.llm.command_functions import (
    function_name_to_model_map,
    get_all_function_definitions,
)
from cantina_os.models.music_models import MusicTrack
from cantina_os.services.intent_router_service import IntentRouterService
from cantina_os.services.music_controller_service.music_backends import (
    SpotifyMusicBackend,
)
from cantina_os.services.music_controller_service.music_controller_service import (
    MusicControllerService,
)


@pytest.fixture(autouse=True)
def mock_external_apis():
    """Override the suite fixture; these tests supply their own Spotify client."""
    yield {}


def spotify_track(
    *,
    track_id: str = "track-123",
    title: str = "Space Funk",
    artist: str = "The Asteroids",
) -> MusicTrack:
    return MusicTrack(
        name=f"{artist} - {title}",
        path=f"spotify:track:{track_id}",
        duration=242.0,
        track_id=track_id,
        title=title,
        artist=artist,
        album="Orbital Grooves",
    )


@pytest.mark.asyncio
async def test_spotify_catalog_search_returns_playable_tracks() -> None:
    backend = SpotifyMusicBackend.__new__(SpotifyMusicBackend)
    backend.logger = logging.getLogger("test.spotify")
    backend.sp = MagicMock()
    backend.sp.search.return_value = {
        "tracks": {
            "items": [
                {
                    "id": "track-123",
                    "name": "Space Funk",
                    "uri": "spotify:track:track-123",
                    "duration_ms": 242000,
                    "artists": [{"name": "The Asteroids"}],
                    "album": {"name": "Orbital Grooves"},
                }
            ]
        }
    }

    results = await backend.search_tracks("upbeat space funk", limit=5)

    backend.sp.search.assert_called_once_with(
        q="upbeat space funk", type="track", limit=5
    )
    assert results == [spotify_track()]


@pytest.mark.asyncio
async def test_unknown_description_searches_spotify_and_plays_the_result() -> None:
    service = MusicControllerService.__new__(MusicControllerService)
    service._service_name = "music_controller_test"
    service._logger = logging.getLogger("test.music")
    local_track = MusicTrack(
        name="Cantina Song",
        path="/music/cantina.mp3",
        track_id="cantina",
        title="Cantina Song",
        artist="Cantina Band",
    )
    found = spotify_track()
    spotify = MagicMock()
    spotify.search_tracks = AsyncMock(return_value=[found])
    service.backends = {"local": MagicMock(), "spotify": spotify}
    service.libraries = {"local": {local_track.name: local_track}, "spotify": {}}
    service.active_source = "local"
    service.tracks = service.libraries["local"]
    service._send_error = AsyncMock()
    service._play_track_by_name = AsyncMock()

    async def switch_source(source: str) -> bool:
        service.active_source = source
        service.tracks = service.libraries[source]
        return True

    service._switch_source = AsyncMock(side_effect=switch_source)

    await service._smart_play_track("upbeat space funk")

    spotify.search_tracks.assert_awaited_once_with("upbeat space funk", limit=5)
    assert service.libraries["spotify"][found.name] == found
    service._switch_source.assert_awaited_once_with("spotify")
    service._play_track_by_name.assert_awaited_once_with(found.name, "cli")
    service._send_error.assert_not_awaited()


@pytest.mark.asyncio
async def test_known_local_track_does_not_search_remote_catalog() -> None:
    service = MusicControllerService.__new__(MusicControllerService)
    service._service_name = "music_controller_test"
    service._logger = logging.getLogger("test.music")
    local_track = MusicTrack(
        name="Cantina Song",
        path="/music/cantina.mp3",
        track_id="cantina",
        title="Cantina Song",
        artist="Cantina Band",
    )
    spotify = MagicMock()
    spotify.search_tracks = AsyncMock()
    service.backends = {"spotify": spotify}
    service.libraries = {"local": {local_track.name: local_track}, "spotify": {}}
    service.active_source = "local"
    service.tracks = service.libraries["local"]
    service._send_error = AsyncMock()
    service._play_track_by_name = AsyncMock()

    await service._smart_play_track("cantina")

    spotify.search_tracks.assert_not_awaited()
    service._play_track_by_name.assert_awaited_once_with("Cantina Song", "cli")


@pytest.mark.asyncio
async def test_no_catalog_result_does_not_play_an_unrelated_track() -> None:
    service = MusicControllerService.__new__(MusicControllerService)
    service._service_name = "music_controller_test"
    service._logger = logging.getLogger("test.music")
    spotify = MagicMock()
    spotify.search_tracks = AsyncMock(return_value=[])
    service.backends = {"spotify": spotify}
    service.libraries = {"local": {}, "spotify": {}}
    service.active_source = "local"
    service.tracks = {
        "Bai Tee Tee": MusicTrack(
            name="Bai Tee Tee",
            path="/music/bai.mp3",
            track_id="bai",
            title="Bai Tee Tee",
        )
    }
    service._send_error = AsyncMock()
    service._play_track_by_name = AsyncMock()

    await service._smart_play_track("medieval synthwave polka")

    service._play_track_by_name.assert_not_awaited()
    service._send_error.assert_awaited_once()
    assert "medieval synthwave polka" in service._send_error.await_args.args[0]


def test_search_music_is_a_validated_jev_tool() -> None:
    definitions = {
        item["function"]["name"]: item["function"]
        for item in get_all_function_definitions()
    }

    assert "search_music" in definitions
    assert "search" in definitions["search_music"]["description"].lower()
    model = function_name_to_model_map()["search_music"]
    assert model(query="upbeat 1970s funk").model_dump() == {
        "query": "upbeat 1970s funk"
    }


def test_search_music_intent_is_routable() -> None:
    router = IntentRouterService(AsyncIOEventEmitter())
    assert router._intent_handlers["search_music"] == router._handle_search_music_intent


@pytest.mark.asyncio
async def test_search_music_tool_confirms_the_track_that_started() -> None:
    router = IntentRouterService(
        AsyncIOEventEmitter(), {"PLAYBACK_CONFIRM_WAIT_S": 0.1}
    )

    async def emit(topic, payload):
        if topic == EventTopics.CLI_COMMAND:
            assert payload["raw_input"] == "play music upbeat space funk"
            await router._handle_music_playback_started(
                {"track": {"title": "Space Funk"}}
            )

    router.emit = AsyncMock(side_effect=emit)

    result = await router._handle_search_music_intent(
        {"query": "upbeat space funk"}, "conversation-1"
    )

    assert result["success"] is True
    assert result["track"] == "Space Funk"
    assert result["message"] == "Now playing: Space Funk"


@pytest.mark.asyncio
async def test_search_tool_reaches_catalog_playback_over_the_event_path() -> None:
    bus = AsyncIOEventEmitter()
    router = IntentRouterService(bus, {"PLAYBACK_CONFIRM_WAIT_S": 0.5})

    controller = MusicControllerService.__new__(MusicControllerService)
    controller._service_name = "MusicController"
    controller._logger = logging.getLogger("test.music.integration")
    controller._event_bus = bus
    controller._event_handlers = {}
    controller.current_track = None
    controller.player = None
    controller.secondary_player = None
    controller.track_end_timer = None
    controller.dj_mode_active = False
    controller.is_ducking = False
    controller.normal_volume = 70
    controller.ducking_volume = 50
    controller.current_mode = "IDLE"

    found = spotify_track()
    local_backend = MagicMock()
    local_backend.stop_playback = AsyncMock(return_value=True)
    spotify_backend = MagicMock()
    spotify_backend.search_tracks = AsyncMock(return_value=[found])
    spotify_backend.stop_playback = AsyncMock(return_value=True)
    spotify_backend.play_track = AsyncMock(return_value=True)
    controller.backends = {
        "local": local_backend,
        "spotify": spotify_backend,
    }
    controller.libraries = {"local": {}, "spotify": {}}
    controller.active_source = "local"
    controller.tracks = controller.libraries["local"]

    async def dispatch_play_command(payload):
        await controller.handle_play_music(payload)

    bus.on(EventTopics.CLI_COMMAND.value, dispatch_play_command)
    bus.on(
        EventTopics.MUSIC_PLAYBACK_STARTED.value,
        router._handle_music_playback_started,
    )

    result = await router._handle_search_music_intent(
        {"query": "upbeat space funk"}, "conversation-1"
    )

    spotify_backend.search_tracks.assert_awaited_once_with(
        "upbeat space funk", limit=5
    )
    spotify_backend.play_track.assert_awaited_once_with(found)
    assert controller.active_source == "spotify"
    assert controller.current_track == found
    assert result["track"] == "Space Funk"
