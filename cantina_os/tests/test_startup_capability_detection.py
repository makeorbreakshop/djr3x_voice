import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from cantina_os.services.eye_light_controller_service import EyeLightControllerService
from cantina_os.services.mouse_input_service import MouseInputService
from cantina_os.services.vision_service import VisionService
from pyee.asyncio import AsyncIOEventEmitter

FFMPEG_CAMERA_LIST = """\
[AVFoundation indev @ 0x1] AVFoundation video devices:
[AVFoundation indev @ 0x1] [0] Studio Display Camera
[AVFoundation indev @ 0x1] [1] Brandon’s iPhone (2) Camera
[AVFoundation indev @ 0x1] [2] Brandon’s iPhone (2) Desk View Camera
[AVFoundation indev @ 0x1] [3] Capture screen 0
[AVFoundation indev @ 0x1] AVFoundation audio devices:
[AVFoundation indev @ 0x1] [0] Studio Display Microphone
"""


def bare_vision() -> VisionService:
    service = VisionService.__new__(VisionService)
    service._service_name = "vision"
    service._logger = logging.getLogger("cantina_os.vision")
    return service


def test_camera_discovery_uses_avfoundation_names_without_opening_devices(monkeypatch) -> None:
    service = bare_vision()
    completed = MagicMock(stdout="", stderr=FFMPEG_CAMERA_LIST)
    monkeypatch.setattr("cantina_os.services.vision_service.subprocess.run", lambda *a, **k: completed)
    video_capture = MagicMock(side_effect=AssertionError("camera probing is not discovery"))
    monkeypatch.setattr("cantina_os.services.vision_service.cv2.VideoCapture", video_capture)

    assert service._get_camera_names() == {
        0: "Studio Display Camera",
        1: "Brandon’s iPhone (2) Camera",
        2: "Brandon’s iPhone (2) Desk View Camera",
    }
    video_capture.assert_not_called()


def test_camera_selection_does_not_fall_back_to_a_rejected_iphone(monkeypatch) -> None:
    service = bare_vision()
    monkeypatch.setattr(
        service,
        "_get_camera_names",
        lambda: {0: "Brandon’s iPhone Camera", 1: "Capture screen 0"},
    )

    assert service._find_best_camera() is None


@pytest.mark.asyncio
async def test_mouse_listener_is_attempted_without_accessibility_preflight(monkeypatch) -> None:
    service = MouseInputService(AsyncIOEventEmitter())
    monkeypatch.setattr(service, "_input_monitoring_available", lambda: False, raising=False)
    listener_instance = MagicMock()
    listener = MagicMock(return_value=listener_instance)
    monkeypatch.setattr(
        "cantina_os.services.mouse_input_service.mouse.Listener",
        listener,
    )

    await service._setup_mouse_listener()

    listener.assert_called_once()
    listener_instance.start.assert_called_once_with()


@pytest.mark.asyncio
async def test_missing_configured_arduino_port_skips_connection_retries(monkeypatch, caplog) -> None:
    service = EyeLightControllerService(
        AsyncIOEventEmitter(), serial_port="/dev/cu.usbmodem-missing"
    )
    service.subscribe = AsyncMock()
    service.emit = AsyncMock()
    service._auto_detect_arduino = AsyncMock(return_value=None)
    service._connect_to_arduino = AsyncMock()
    monkeypatch.setattr("cantina_os.services.eye_light_controller_service.os.path.exists", lambda p: False)

    with caplog.at_level(logging.WARNING, logger=service.logger.name):
        await service._start()

    service._connect_to_arduino.assert_not_awaited()
    assert service.mock_mode is True
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "mock mode" in warnings[0].lower()
