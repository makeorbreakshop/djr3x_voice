"""
ElevenLabs must shut down cleanly: no work posted onto a closed loop, no un-awaited coroutines.

## The defect this pins (seen at the end of `system_smoke_run.py --show --with-tts`)

``ElevenLabsService`` put its teardown in ``_cleanup()``, but ``BaseService.stop()`` calls
``_stop()``, which it never overrode - so the audio worker thread was never stopped. When the
process's loop closed, the still-running thread's next ``asyncio.run_coroutine_threadsafe``
raised ``RuntimeError: Event loop is closed`` (traceback from ``_audio_worker_loop``), and the
coroutine objects it had already built were garbage-collected as "never awaited".

These tests drive the real worker thread with a fake ElevenLabs stream and a fake audio
device, inside a real ``asyncio.run`` so the loop genuinely closes afterwards.
"""

import asyncio
import gc
import threading
import time
import warnings
from typing import Any, Dict, List

import pytest
from pyee.asyncio import AsyncIOEventEmitter

import cantina_os.services.elevenlabs_service as el
from cantina_os.core.event_topics import EventTopics


@pytest.fixture(autouse=True)
def mock_external_apis():
    """Override conftest's autouse fixture (it stubs the elevenlabs module we need here)."""
    yield {}


class FakeStreamClient:
    """Endless PCM chunks, 20 ms apart: a line that is still playing at shutdown."""

    def __init__(self, *a, **k) -> None:
        self.text_to_speech = self

    def stream(self, **kwargs):
        def gen():
            while True:
                time.sleep(0.02)
                yield b"\x10\x00" * 480
        return gen()


class FakeOutputStream:
    def __init__(self, *a, **k) -> None: ...
    def start(self) -> None: ...
    def write(self, samples) -> None: ...
    def stop(self) -> None: ...
    def close(self) -> None: ...


@pytest.fixture
def fakes(monkeypatch):
    import sounddevice

    monkeypatch.setattr(el, "ElevenLabs", FakeStreamClient)
    monkeypatch.setattr(sounddevice, "OutputStream", FakeOutputStream)


def _request(text: str = "Time to boogie!") -> Dict[str, Any]:
    return {"text": text, "conversation_id": "c1", "voice_id": "v", "model_id": "eleven_flash_v2_5",
            "stability": 0.5, "similarity_boost": 0.75, "speed": 1.0, "clip_id": "clip-1"}


def _service(bus) -> "el.ElevenLabsService":
    return el.ElevenLabsService(bus, {"ELEVENLABS_API_KEY": "test-key-not-used"})


def test_stop_ends_a_mid_speech_worker_before_the_loop_closes(fakes):
    seen: List[str] = []
    state: Dict[str, Any] = {}

    async def main() -> None:
        bus = AsyncIOEventEmitter()
        bus.on(EventTopics.SPEECH_GENERATION_STARTED.value, lambda p: seen.append("started"))
        bus.on(EventTopics.SPEECH_SYNTHESIS_AMPLITUDE.value, lambda p: seen.append("amp"))
        bus.on(EventTopics.SPEECH_GENERATION_COMPLETE.value, lambda p: seen.append("complete"))
        svc = _service(bus)
        await svc.start()
        state["thread"] = svc._audio_thread
        svc._speech_request_queue.put(_request())
        for _ in range(200):
            if "amp" in seen:
                break
            await asyncio.sleep(0.01)
        assert "amp" in seen, "the fake line never started playing"
        t0 = time.monotonic()
        await svc.stop()
        state["stop_s"] = time.monotonic() - t0

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        errors: List[BaseException] = []
        old_hook = threading.excepthook
        threading.excepthook = lambda a: errors.append(a.exc_value)
        try:
            asyncio.run(main())
            time.sleep(0.2)  # anything the thread still does happens after the loop closed
            gc.collect()
        finally:
            threading.excepthook = old_hook

    assert not state["thread"].is_alive(), "the audio worker outlived stop()"
    assert state["stop_s"] < 2.0, f"stop() took {state['stop_s']:.2f}s to end a playing line"
    assert not errors, f"worker thread raised: {errors}"
    never_awaited = [w for w in caught if "never awaited" in str(w.message)]
    assert not never_awaited, [str(w.message) for w in never_awaited]
    assert "complete" in seen, "the aborted line's completion never reached the bus"


def test_posting_after_shutdown_creates_no_coroutine(fakes):
    """The guard takes a coroutine *function*: with the loop gone, nothing is ever built."""
    calls: List[int] = []

    async def main():
        svc = _service(AsyncIOEventEmitter())
        await svc.start()
        await svc.stop()
        return svc

    svc = asyncio.run(main())

    async def would_emit():
        calls.append(1)

    def factory():
        calls.append(0)
        return would_emit()

    assert svc._post_to_loop(factory) is False
    assert calls == []


def test_posting_to_a_live_loop_still_works(fakes):
    delivered: List[str] = []

    async def main():
        svc = _service(AsyncIOEventEmitter())
        await svc.start()
        try:
            async def mark():
                delivered.append("ran")

            ok = await asyncio.to_thread(svc._post_to_loop, mark)  # from another thread, as in prod
            await asyncio.sleep(0.05)
            return ok
        finally:
            await svc.stop()

    assert asyncio.run(main()) is True
    assert delivered == ["ran"]


# =========================================================================================
# Found by the same smoke run: back-to-back lines must keep their own ids
# =========================================================================================

class ShortStreamClient(FakeStreamClient):
    def stream(self, **kwargs):
        def gen():
            for _ in range(5):
                time.sleep(0.01)
                yield b"\x10\x00" * 480
        return gen()


def test_back_to_back_lines_complete_with_their_own_clip_ids(monkeypatch):
    """A show line queued right behind Claude's reply (the item-5 case).

    The worker's emit closures used to read ``clip_id``/``conversation_id`` from the worker
    loop's variables when they *ran* on the event loop - after the thread had already taken
    the next request. Live, 2026-09-29: the show line "Make some noise!" completed carrying
    the reply's clip_id, 0.3 ms before the reply had even started.
    """
    import sounddevice

    monkeypatch.setattr(el, "ElevenLabs", ShortStreamClient)
    monkeypatch.setattr(sounddevice, "OutputStream", FakeOutputStream)
    events: List[tuple] = []

    async def main():
        bus = AsyncIOEventEmitter()
        bus.on(EventTopics.SPEECH_GENERATION_STARTED.value, lambda p: events.append(("start", p.get("clip_id"))))
        bus.on(EventTopics.SPEECH_GENERATION_COMPLETE.value, lambda p: events.append(("done", p.get("clip_id"))))
        svc = _service(bus)
        await svc.start()
        try:
            show_line = {**_request("Make some noise!"), "clip_id": "show-1", "conversation_id": "show-1"}
            reply = {**_request("Hey hey HEY!"), "clip_id": "turn-1", "conversation_id": "turn-1"}
            svc._speech_request_queue.put(show_line)
            svc._speech_request_queue.put(reply)
            for _ in range(300):
                if sum(1 for kind, _ in events if kind == "done") == 2:
                    break
                await asyncio.sleep(0.01)
        finally:
            await svc.stop()

    asyncio.run(main())
    assert events == [("start", "show-1"), ("done", "show-1"), ("start", "turn-1"), ("done", "turn-1")], events
