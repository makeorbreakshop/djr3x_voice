from unittest.mock import AsyncMock

import pytest
from pyee.asyncio import AsyncIOEventEmitter

from cantina_os.core.event_topics import EventTopics
from cantina_os.services.mouse_input_service import MouseInputService
from cantina_os.services.yoda_mode_manager_service import SystemMode


@pytest.mark.asyncio
async def test_click_does_not_claim_recording_until_microphone_acknowledges_start():
    service = MouseInputService(
        AsyncIOEventEmitter(), {"enabled": False, "dashboard_aware": False}
    )
    service._current_mode = SystemMode.INTERACTIVE
    service.emit = AsyncMock()
    service._emit_status = AsyncMock()

    await service._handle_mouse_click()

    assert service._is_recording is False
    service.emit.assert_awaited_once_with(EventTopics.MIC_RECORDING_START, {})


@pytest.mark.asyncio
async def test_microphone_acknowledgements_are_the_recording_source_of_truth():
    service = MouseInputService(
        AsyncIOEventEmitter(), {"enabled": False, "dashboard_aware": False}
    )

    await service._handle_voice_listening_started({"conversation_id": "turn-1"})
    assert service._is_recording is True

    await service._handle_voice_listening_stopped({"conversation_id": "turn-1"})
    assert service._is_recording is False
