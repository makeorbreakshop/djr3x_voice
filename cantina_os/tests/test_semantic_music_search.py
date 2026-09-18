import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest
from pyee.asyncio import AsyncIOEventEmitter

from cantina_os.base_service import BaseService
from cantina_os.core.event_topics import EventTopics
from cantina_os.core.music_search import (
    encode_semantic_request,
    parse_semantic_request,
)
from cantina_os.models.music_models import MusicTrack
from cantina_os.services.intent_router_service import IntentRouterService
from cantina_os.services.music_controller_service import (
    music_controller_service as music_controller_module,
)
from cantina_os.services.music_controller_service.music_controller_service import (
    MusicControllerService,
)
from cantina_os.services.music_controller_service.semantic_music_search import (
    SemanticMatch,
    rank_semantic_embeddings,
)


@pytest.fixture(autouse=True)
def mock_external_apis():
    yield {}


def local_track(name: str) -> MusicTrack:
    return MusicTrack(
        name=name,
        path=f"/music/{name}.mp3",
        track_id=name,
        title=name,
        artist="Cantina Band",
        provider="local",
    )


def controller_with_tracks(*names: str) -> MusicControllerService:
    service = MusicControllerService.__new__(MusicControllerService)
    service._service_name = "music_controller_test"
    service._logger = logging.getLogger("test.semantic_music")
    service.tracks = {name: local_track(name) for name in names}
    service.backends = {}
    service.libraries = {"local": service.tracks, "spotify": {}}
    service.active_source = "local"
    service._semantic_search = None
    service._send_error = AsyncMock()
    service._play_track_by_name = AsyncMock()
    return service


def test_semantic_request_protocol_round_trips_positive_and_negative_constraints():
    encoded = encode_semantic_request(
        "fun upbeat playful party music",
        "heavy aggressive dark intense music",
    )

    parsed = parse_semantic_request(encoded)

    assert parsed is not None
    assert parsed.query == "fun upbeat playful party music"
    assert parsed.negative_query == "heavy aggressive dark intense music"


def test_negative_similarity_changes_the_winner():
    names = ["fun-but-heavy", "fun-and-light"]
    track_embeddings = np.asarray([[0.9, 0.9], [0.8, 0.1]], dtype=np.float32)
    positive = np.asarray([1.0, 0.0], dtype=np.float32)
    negative = np.asarray([0.0, 1.0], dtype=np.float32)

    matches = rank_semantic_embeddings(
        names,
        track_embeddings,
        positive,
        negative_embedding=negative,
        negative_weight=0.5,
        limit=2,
    )

    assert [match.track_name for match in matches] == ["fun-and-light", "fun-but-heavy"]
    assert matches[0].positive_score == pytest.approx(0.8)
    assert matches[0].negative_score == pytest.approx(0.1)


@pytest.mark.asyncio
async def test_semantic_model_waits_until_the_startup_chime_finishes(monkeypatch):
    """CLAP must not compete with the startup chime for CPU or audio resources."""
    service = controller_with_tracks("Modal Notes")
    service._event_bus = AsyncIOEventEmitter()
    service._config = MagicMock(enable_semantic_search=True)
    service._subscriptions = []
    service.backends = {}

    monkeypatch.setattr(BaseService, "start", AsyncMock())
    service.subscribe_to_events = AsyncMock()
    service._initialize_backends = AsyncMock()
    service._load_music_library = AsyncMock()
    service._emit_status = AsyncMock()
    monkeypatch.setattr(
        music_controller_module,
        "register_service_commands",
        MagicMock(),
    )

    initialization_started = asyncio.Event()
    release_initialization = asyncio.Event()

    async def slow_semantic_initialization() -> None:
        initialization_started.set()
        await release_initialization.wait()

    service._initialize_semantic_search = slow_semantic_initialization

    await asyncio.wait_for(service.start(), timeout=0.05)
    assert not initialization_started.is_set()

    await service._handle_system_startup({})
    await asyncio.wait_for(initialization_started.wait(), timeout=0.1)
    try:
        assert not service._semantic_search_task.done()
    finally:
        release_initialization.set()
        await service._semantic_search_task

    assert service._semantic_search_task.done()


@pytest.mark.asyncio
async def test_java_resolves_to_the_dusty_jawas_before_semantic_search():
    service = controller_with_tracks(
        "Modal Notes",
        "The Dusty Jawas - Utinni",
        "Utinni",
    )

    await service._smart_play_track("java")

    service._play_track_by_name.assert_awaited_once_with(
        "The Dusty Jawas - Utinni", "cli"
    )
    service._send_error.assert_not_awaited()


@pytest.mark.asyncio
async def test_structured_vibe_request_plays_the_local_semantic_winner():
    service = controller_with_tracks("Modal Notes", "Mus Kat & Nalpak - Bright Suns")
    semantic = MagicMock()
    semantic.ready = True
    semantic.search.return_value = [
        SemanticMatch(
            track_name="Mus Kat & Nalpak - Bright Suns",
            score=0.44,
            positive_score=0.57,
            negative_score=0.26,
        )
    ]
    service._semantic_search = semantic
    request = encode_semantic_request(
        "fun upbeat playful party music",
        "heavy aggressive dark intense music",
    )

    await service._smart_play_track(request)

    semantic.search.assert_called_once_with(
        "fun upbeat playful party music",
        negative_query="heavy aggressive dark intense music",
        limit=5,
    )
    service._play_track_by_name.assert_awaited_once_with(
        "Mus Kat & Nalpak - Bright Suns", "cli"
    )
    service._send_error.assert_not_awaited()


@pytest.mark.asyncio
async def test_voice_semantic_request_reports_the_track_that_really_started():
    bus = AsyncIOEventEmitter()
    router = IntentRouterService(bus, {"PLAYBACK_CONFIRM_WAIT_S": 0.5})
    controller = controller_with_tracks("Modal Notes", "Mus Kat & Nalpak - Bright Suns")
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
    controller._send_success = AsyncMock()
    controller._play_track_by_name = MusicControllerService._play_track_by_name.__get__(
        controller
    )

    semantic = MagicMock()
    semantic.ready = True
    semantic.search.return_value = [
        SemanticMatch(
            track_name="Mus Kat & Nalpak - Bright Suns",
            score=0.44,
            positive_score=0.57,
            negative_score=0.26,
        )
    ]
    controller._semantic_search = semantic
    local_backend = MagicMock()
    local_backend.stop_playback = AsyncMock(return_value=True)
    local_backend.play_track = AsyncMock(return_value=True)
    local_backend.set_volume = AsyncMock(return_value=True)
    controller.backends = {"local": local_backend}

    async def dispatch_play_command(payload):
        await controller.handle_play_music(payload)

    bus.on(EventTopics.CLI_COMMAND.value, dispatch_play_command)
    bus.on(
        EventTopics.MUSIC_PLAYBACK_STARTED.value,
        router._handle_music_playback_started,
    )
    encoded = encode_semantic_request(
        "fun upbeat playful party music",
        "heavy aggressive dark intense music",
    )

    result = await router._handle_play_music_intent(
        {"track": encoded}, "conversation-1"
    )

    assert result["success"] is True
    assert result["track"] == "Mus Kat & Nalpak - Bright Suns"
    assert result["message"] == "Now playing: Mus Kat & Nalpak - Bright Suns"
    assert controller.current_track.name == "Mus Kat & Nalpak - Bright Suns"
