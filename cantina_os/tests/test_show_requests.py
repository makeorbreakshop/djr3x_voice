"""Phase 3: CantinaOS only *requests* shows; the r3x performer (over the bus tap) plays them.

Real pyee bus, real TimelineExecutorService; the performer's replies are emitted by hand.
"""

import asyncio
import json
from unittest.mock import AsyncMock

from pyee.asyncio import AsyncIOEventEmitter

from cantina_os.core.event_topics import EventTopics
from cantina_os.services.timeline_executor_service.timeline_executor_service import TimelineExecutorService

from .test_show_tags import _show_dir


async def _timeline(tmp_path):
    bus = AsyncIOEventEmitter()
    seen = []
    for t in (EventTopics.SHOW_PERFORM, EventTopics.SHOW_STOP, EventTopics.CLI_RESPONSE):
        bus.on(t.value, lambda p, t=t: seen.append((t, p)))
    svc = TimelineExecutorService(bus, {"show_dir": str(_show_dir(tmp_path))})
    await svc.start()
    return bus, svc, seen


def _ended(bus, item_id, source, reason="done"):
    bus.emit(EventTopics.SHOW_ENDED.value, {"id": item_id, "kind": "sequence", "source": source,
                                            "run_id": "r1", "reason": reason})


async def test_plan_step_requests_the_show_and_waits_for_its_own_end(tmp_path):
    bus, svc, seen = await _timeline(tmp_path)
    try:
        step = {"step_type": "sequence", "id": "dj_intro", "wait_for_completion": True}
        task = asyncio.create_task(svc._execute_show_step(step, "plan-1"))
        await asyncio.sleep(0.01)
        assert [p for t, p in seen if t == EventTopics.SHOW_PERFORM] == [{"id": "dj_intro", "source": "timeline"}]
        _ended(bus, "dj_intro", "cli")  # someone else's run of the same id: not ours
        await asyncio.sleep(0.01)
        assert not task.done()
        _ended(bus, "dj_intro", "timeline", "interrupted")
        assert await asyncio.wait_for(task, 1) == (True, {"id": "dj_intro", "reason": "interrupted"})
    finally:
        await svc.stop()


async def test_plan_steps_that_cannot_play_are_skipped_without_a_request(tmp_path):
    bus, svc, seen = await _timeline(tmp_path)
    try:
        ok, res = await svc._execute_show_step({"step_type": "perform", "id": "nope", "optional": True}, "p")
        assert ok and res["skipped"]
        ok, res = await svc._execute_show_step({"step_type": "sequence", "id": "hype_drop"}, "p")
        assert ok and res["reason"] == "is a cue, not a sequence"
        assert not [p for t, p in seen if t == EventTopics.SHOW_PERFORM]
    finally:
        await svc.stop()


async def test_cli_show_sends_the_request_and_reports_a_refusal(tmp_path):
    bus, svc, seen = await _timeline(tmp_path)
    try:
        bus.emit(EventTopics.SHOW_COMMAND.value, {"raw_input": "show hype_drop 0.8"})
        await asyncio.sleep(0.01)
        assert [p for t, p in seen if t == EventTopics.SHOW_PERFORM] == [
            {"id": "hype_drop", "source": "cli", "params": {"intensity": 0.8, "speed": 1.0}}]
        _ended(bus, "hype_drop", "cli", "rejected")
        await asyncio.sleep(0.01)
        replies = [p for t, p in seen if t == EventTopics.CLI_RESPONSE]
        assert "r3x performer" in replies[0]["message"] and replies[-1]["is_error"]
        assert "r3x runtime" in svc._format_show_list()  # nothing has ever started
    finally:
        await svc.stop()


async def test_dj_mode_off_stops_the_show_layer(tmp_path):
    bus, svc, seen = await _timeline(tmp_path)
    try:
        bus.emit(EventTopics.DJ_MODE_CHANGED.value, {"is_active": False})
        await asyncio.sleep(0.01)
        assert [p for t, p in seen if t == EventTopics.SHOW_STOP] == [{"layer": "show"}]
    finally:
        await svc.stop()


async def test_external_body_skips_both_led_services(monkeypatch):
    import cantina_os.main as main_module

    monkeypatch.setenv("R3X_EXTERNAL_BODY", "1")
    monkeypatch.setenv("R3X_TAP_ENABLED", "0")
    app = main_module.CantinaOS()
    started = []
    app._create_service = lambda name: started.append(name) or AsyncMock()
    await app._initialize_services()
    assert "timeline_executor_service" in started
    assert "eye_light_controller" not in started and "chest_light_controller" not in started


def test_allow_list_explains_the_recorded_show_trace_without_the_python_player():
    """fixtures/smoke-show was recorded with the Python player; replaying CantinaOS with the
    performer attached must differ only by what the allow-list names (plan §8 item 6)."""
    import importlib.util
    from pathlib import Path

    root = Path(__file__).parents[1]
    spec = importlib.util.spec_from_file_location("trace_compare", root / "scripts" / "trace_compare.py")
    tc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tc)
    recorded = tc.load(root.parent / "fixtures" / "smoke-show" / "trace.jsonl")
    allow = json.loads((root / "scripts" / "trace_allowlist.json").read_text())
    moved = {"show.started", "show.ended", "show.sfx", "stage.lights", "chest.override", "eye.command",
             "tts.generate.request"}
    now = []
    for r in recorded:
        if r["topic"] == "show.motion" and r["source"] == "timeline_executor_service":
            continue  # r3x drives motion internally
        if r["topic"] in moved and r["source"] == "timeline_executor_service":
            if r["topic"] == "tts.generate.request" and not str(r["payload"].get("clip_id")).startswith("show-"):
                now.append(r)
                continue
            r = {**r, "source": "tap:r3x"}
        now.append(r)
        if r["topic"] == "plan.started":  # the dj_intro plan step now asks over the bus
            now.append({**r, "topic": "show.perform", "source": "timeline_executor_service",
                        "payload": {"id": "dj_intro", "source": "timeline"}})
    exp = tc.turns(tc.apply_drops(recorded, allow, "expected"))
    act = tc.turns(tc.apply_drops(now, allow, "actual"))
    assert tc.compare(exp, act) == []
    assert tc.compare(tc.turns(recorded), tc.turns(now)) != []  # and without it, it would not pass
