from unittest.mock import AsyncMock

import pytest

import cantina_os.main as main_module


@pytest.mark.asyncio
async def test_startup_readiness_waits_for_music_semantic_index():
    app = main_module.CantinaOS()
    music = AsyncMock()
    app._services = {"music_controller": music}

    await app._wait_for_startup_readiness()

    music.wait_until_ready.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_ready_chime_plays_only_after_readiness_barrier(monkeypatch):
    app = main_module.CantinaOS()
    order = []

    async def initialize_services():
        app._services["command_dispatcher"] = object()

    async def wait_until_ready():
        order.append("ready")

    async def play_chime(*args, **kwargs):
        order.append("chime")

    app._initialize_services = AsyncMock(side_effect=initialize_services)
    app._register_commands = AsyncMock()
    app._wait_for_startup_readiness = AsyncMock(side_effect=wait_until_ready)
    app._cleanup_services = AsyncMock()
    app._setup_signal_handlers = lambda: None
    app._shutdown_event.set()
    monkeypatch.setattr(main_module.log_listener, "start", lambda: None)
    monkeypatch.setattr(main_module.os.path, "exists", lambda _path: True)
    monkeypatch.setattr(main_module, "play_audio_file", play_chime)

    await app.run()

    assert order == ["ready", "chime"]
