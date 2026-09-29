"""The show player inside TimelineExecutorService, on a real pyee bus with real time.

Timing assertions use tight tolerances rather than a fake clock: what matters is that the
scheduling is anchored to the monotonic clock, and that is only observable in real time.
"""

import asyncio
import time

import pytest
from pyee.asyncio import AsyncIOEventEmitter

from cantina_os.core.event_topics import EventTopics
from cantina_os.models.music_models import MusicTrack
from cantina_os.services.brain_service import BrainService
from cantina_os.services.timeline_executor_service.timeline_executor_service import (
    TimelineExecutorService,
)
from cantina_os.show import ShowLibrary

TOL = 0.04  # seconds

TOPICS = [
    "SHOW_STARTED", "SHOW_ENDED", "SHOW_MOTION", "SHOW_SFX", "STAGE_LIGHTS", "CHEST_OVERRIDE",
    "EYE_COMMAND", "TTS_GENERATE_REQUEST", "AUDIO_DUCKING_START", "AUDIO_DUCKING_STOP",
    "PLAN_ENDED", "CLI_RESPONSE", "SHOW_PERFORM", "MOTION_FREEZE", "SHOW_STOP",
]


class Recorder:
    def __init__(self, bus):
        self.events = []
        self.t0 = time.monotonic()
        for name in TOPICS:
            topic = getattr(EventTopics, name).value
            bus.on(topic, lambda p, n=name: self.events.append((time.monotonic() - self.t0, n, p)))

    def reset(self):
        self.events.clear()
        self.t0 = time.monotonic()

    def of(self, name):
        return [(t, p) for t, n, p in self.events if n == name]

    def times(self, name, key=None, value=None):
        return [t for t, p in self.of(name) if key is None or p.get(key) == value]


@pytest.fixture
async def rig():
    bus = AsyncIOEventEmitter()
    svc = TimelineExecutorService(bus)
    rec = Recorder(bus)
    await svc.start()
    yield bus, svc, rec
    await svc.stop()


def use(svc, items, validate=True):
    svc.show_player.library.set(ShowLibrary.from_items(items, validate=validate))


def perform(bus, item_id, source="cli", **kw):
    bus.emit(EventTopics.SHOW_PERFORM.value, {"id": item_id, "source": source, **kw})


def sfx(at, name):
    return {"at": at, "do": "sfx", "id": name}


def seq(id, track, tier="show", **kw):
    return {"id": id, "kind": "sequence", "tier": tier, "description": id, "track": track, **kw}


def clip(id, tier="free", duration=0.2, **kw):
    return {"id": id, "kind": "clip", "tier": tier, "description": id, "duration": duration,
            "tracks": {"visor": {"mode": "override", "keys": [[0, 0], [duration, 10]]}}, **kw}


def cue(id, actions, tier="cheap"):
    return {"id": id, "kind": "cue", "tier": tier, "description": id, "actions": actions}


async def until(pred, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        await asyncio.sleep(0.005)
    return False


def complete_speech(bus, clip_id):
    bus.emit(EventTopics.SPEECH_GENERATION_COMPLETE.value,
             {"text": "x", "audio_length_seconds": 0.0, "success": True, "clip_id": clip_id})


# ---------------------------------------------------------------------------- clocks

async def test_time_clock_is_anchored_not_accumulated(rig):
    """A slow department handler must not push every later item back (no sleep drift)."""
    bus, svc, rec = rig
    use(svc, [seq("s", [sfx(round(0.1 * i, 1), f"s{i}") for i in range(6)])])
    bus.on(EventTopics.SHOW_SFX.value, lambda p: time.sleep(0.03))  # blocks the loop 30 ms
    rec.reset()
    perform(bus, "s")
    assert await until(lambda: rec.of("SHOW_ENDED"))
    times = rec.times("SHOW_SFX")
    assert len(times) == 6
    for i, t in enumerate(times):
        assert abs(t - 0.1 * i) < TOL, (i, t)


async def test_beat_clock_chases_the_live_tempo(rig):
    bus, svc, rec = rig
    use(svc, [seq("b", [sfx(b, f"b{b}") for b in range(4)], clock="beat", bpm=120)])
    rec.reset()
    perform(bus, "b")
    assert await until(lambda: len(rec.of("SHOW_SFX")) >= 2)  # beat 1 at 0.5 s
    await asyncio.sleep(0.05)  # now at beat ~1.1
    t_change = time.monotonic() - rec.t0
    bus.emit(EventTopics.MUSIC_PLAYBACK_STARTED.value, {"track": {"title": "x", "bpm": 240}})
    assert await until(lambda: rec.of("SHOW_ENDED"))
    t = rec.times("SHOW_SFX")
    assert abs(t[1] - 0.5) < TOL
    beat_at_change = 1 + (t_change - t[1]) * 2  # 2 beats/s before the change
    for beat, got in ((2, t[2]), (3, t[3])):
        want = t_change + (beat - beat_at_change) * 0.25  # 4 beats/s after it
        assert abs(got - want) < TOL, (beat, got, want)


async def test_beat_clock_uses_known_tempo_from_the_start(rig):
    bus, svc, rec = rig
    bus.emit(EventTopics.MUSIC_PLAYBACK_STARTED.value, {"track": {"bpm": 300}})
    use(svc, [seq("b", [sfx(0, "a"), sfx(2, "b")], clock="beat", bpm=60)])
    await asyncio.sleep(0.01)
    rec.reset()
    perform(bus, "b")
    assert await until(lambda: rec.of("SHOW_ENDED"))
    assert abs(rec.times("SHOW_SFX")[1] - 0.4) < TOL  # 2 beats at 300 bpm, not 60


# ---------------------------------------------------------------------------- wait

async def test_wait_pauses_the_whole_run_including_nested(rig):
    bus, svc, rec = rig
    use(svc, [
        seq("inner", [sfx(0.3, "inner")], tier="cheap"),
        seq("outer", [
            {"at": 0, "sequence": "inner"},       # started BEFORE the wait
            {"at": 0.1, "do": "speak", "text": "Time to boogie!"},
            {"at": 0.1, "do": "wait", "for": "speech_end"},
            sfx(0.2, "after"),
        ]),
    ])
    rec.reset()
    perform(bus, "outer")
    assert await until(lambda: rec.of("TTS_GENERATE_REQUEST"))
    speech_id = rec.of("TTS_GENERATE_REQUEST")[0][1]["clip_id"]
    await asyncio.sleep(0.5 - (time.monotonic() - rec.t0))
    complete_speech(bus, speech_id)
    assert await until(lambda: rec.of("SHOW_ENDED"))
    # paused from 0.1 to 0.5: everything later slides by 0.4 s
    assert abs(rec.times("SHOW_SFX", "id", "after")[0] - 0.6) < TOL
    assert abs(rec.times("SHOW_SFX", "id", "inner")[0] - 0.7) < TOL


async def test_wait_with_no_speech_does_not_hold(rig):
    bus, svc, rec = rig
    use(svc, [seq("s", [{"at": 0, "do": "wait", "for": "speech_end"}, sfx(0.05, "x")])])
    rec.reset()
    perform(bus, "s")
    assert await until(lambda: rec.of("SHOW_ENDED"))
    assert abs(rec.times("SHOW_SFX")[0] - 0.05) < TOL


# ---------------------------------------------------------------------------- nesting, loops

async def test_nesting_runs_three_levels_and_refuses_a_fourth(rig):
    bus, svc, rec = rig
    items = [seq(f"n{i}", [sfx(0.01, f"n{i}"), {"at": 0.02, "sequence": f"n{i+1}"}]) for i in range(4)]
    items.append(seq("n4", [sfx(0.01, "n4")]))
    # Unvalidated, so the runtime guard (not the linter) is what is exercised.
    use(svc, items, validate=False)
    rec.reset()
    perform(bus, "n0")
    assert await until(lambda: rec.of("SHOW_ENDED"))
    assert [p["id"] for _, p in rec.of("SHOW_SFX")] == ["n0", "n1", "n2", "n3"]
    # and the linter refuses the same library outright
    use(svc, items)
    rec.reset()
    perform(bus, "n0")
    assert await until(lambda: rec.of("SHOW_ENDED"))
    assert rec.of("SHOW_ENDED")[0][1]["reason"] == "rejected"


async def test_loop_repeats_on_the_clock_until_stopped(rig):
    bus, svc, rec = rig
    use(svc, [seq("l", [sfx(0, "tick")], loop=True, length=0.1)])
    rec.reset()
    perform(bus, "l")
    await asyncio.sleep(0.35)
    bus.emit(EventTopics.SHOW_STOP.value, {"id": "l"})
    assert await until(lambda: rec.of("SHOW_ENDED"))
    ticks = rec.times("SHOW_SFX")
    assert len(ticks) == 4
    for i, t in enumerate(ticks):
        assert abs(t - 0.1 * i) < TOL
    assert rec.of("SHOW_ENDED")[0][1]["reason"] == "interrupted"


# ---------------------------------------------------------------------------- interruption

async def test_new_sequence_on_the_same_layer_ends_the_current_one(rig):
    bus, svc, rec = rig
    use(svc, [
        seq("a", [sfx(0, "a0"), sfx(0.5, "a_late")]),
        seq("b", [sfx(0, "b0")]),
        seq("g", [sfx(0.3, "g")], layer="gesture", tier="cheap"),
    ])
    rec.reset()
    perform(bus, "a")
    perform(bus, "g")
    await asyncio.sleep(0.15)
    perform(bus, "b")
    await asyncio.sleep(0.6)
    ended = {p["id"]: p["reason"] for _, p in rec.of("SHOW_ENDED")}
    assert ended == {"a": "interrupted", "b": "done", "g": "done"}
    ids = [p["id"] for _, p in rec.of("SHOW_SFX")]
    assert "a_late" not in ids and "b0" in ids and "g" in ids  # the gesture layer is untouched


async def test_a_clip_inside_its_interruptible_window_queues_the_next_request(rig):
    bus, svc, rec = rig
    use(svc, [clip("big", duration=0.6, interruptible_after=0.3), clip("small", duration=0.1)])
    rec.reset()
    perform(bus, "big")
    await asyncio.sleep(0.1)
    perform(bus, "small")
    assert await until(lambda: len(rec.of("SHOW_ENDED")) == 2)
    started = {p["id"]: t for t, p in rec.of("SHOW_STARTED")}
    assert abs(started["small"] - 0.3) < TOL
    ended = {p["id"]: p["reason"] for _, p in rec.of("SHOW_ENDED")}
    assert ended == {"big": "interrupted", "small": "done"}


async def test_show_stop_by_layer_and_all(rig):
    bus, svc, rec = rig
    use(svc, [seq("s", [sfx(1, "late")]), seq("g", [sfx(1, "late")], layer="gesture", tier="cheap")])
    perform(bus, "s")
    perform(bus, "g")
    await asyncio.sleep(0.05)
    bus.emit(EventTopics.SHOW_STOP.value, {"layer": "gesture"})
    assert await until(lambda: len(rec.of("SHOW_ENDED")) == 1)
    assert rec.of("SHOW_ENDED")[0][1]["id"] == "g"
    bus.emit(EventTopics.SHOW_STOP.value, {"all": True})
    assert await until(lambda: len(rec.of("SHOW_ENDED")) == 2)
    assert not rec.of("SHOW_SFX")


# ---------------------------------------------------------------------------- tiers & freeze

async def test_tier_violations_are_rejected_and_do_nothing_else(rig):
    bus, svc, rec = rig
    use(svc, [seq("dj_intro", [sfx(0, "x")], tier="show"),
              cue("hype", [sfx(0, "y")], tier="cheap"),
              cue("blink", [sfx(0, "z")], tier="free")])
    rec.reset()
    perform(bus, "dj_intro", source="jev")
    perform(bus, "hype", source="idle")
    perform(bus, "nope", source="cli")
    await asyncio.sleep(0.1)
    assert [p["reason"] for _, p in rec.of("SHOW_ENDED")] == ["rejected"] * 3
    assert not rec.of("SHOW_STARTED") and not rec.of("SHOW_SFX")
    rec.reset()
    perform(bus, "hype", source="jev")     # cheap: Jev may
    perform(bus, "blink", source="idle")   # free: anyone may
    perform(bus, "dj_intro", source="claude")  # show: Claude's tool may
    assert await until(lambda: len(rec.of("SHOW_ENDED")) == 3)
    assert sorted(p["id"] for _, p in rec.of("SHOW_STARTED")) == ["blink", "dj_intro", "hype"]


async def test_freeze_stops_everything_and_refuses_new_runs(rig):
    bus, svc, rec = rig
    use(svc, [seq("s", [sfx(1, "late")]), clip("nod")])
    perform(bus, "s")
    await asyncio.sleep(0.05)
    bus.emit(EventTopics.MOTION_FREEZE.value, {"on": True})
    await asyncio.sleep(0.01)
    perform(bus, "nod", source="claude")
    await asyncio.sleep(0.05)
    assert [(p["id"], p["reason"]) for _, p in rec.of("SHOW_ENDED")] == [("s", "interrupted"), ("nod", "rejected")]
    bus.emit(EventTopics.MOTION_FREEZE.value, {"on": False})
    await asyncio.sleep(0.01)
    perform(bus, "nod", source="claude")
    assert await until(lambda: any(p["id"] == "nod" for _, p in rec.of("SHOW_STARTED")))


# ---------------------------------------------------------------------------- departments

async def test_department_events_and_payloads(rig):
    bus, svc, rec = rig
    use(svc, [clip("arm", duration=0.3), cue("hype", [
        {"at": 0, "do": "clip", "id": "arm", "intensity": 1.2},
        {"at": 0, "do": "eyes", "pattern": "happy", "color": "#ffb000", "duration": 1.5},
        {"at": 0, "do": "chest", "command": "X2", "hold": 1.5},
        {"at": 0, "do": "lights", "cue": "red_flash"},
        {"at": 0, "do": "duck"},
        {"at": 0.05, "do": "sfx", "id": "airhorn"},
        {"at": 0.05, "do": "unduck"},
    ])])
    rec.reset()
    perform(bus, "hype", params={"intensity": 0.5, "speed": 2.0}, conversation_id="turn-1")
    assert await until(lambda: rec.of("SHOW_ENDED"))
    (_, motion), = rec.of("SHOW_MOTION")
    run_id = rec.of("SHOW_STARTED")[0][1]["run_id"]
    assert motion["run_id"] == run_id and motion["clip"] == "arm" and motion["layer"] == "gesture"
    assert motion["intensity"] == pytest.approx(0.6) and motion["speed"] == 2.0
    assert abs(motion["start_at"] - time.time()) < 5
    eye = rec.of("EYE_COMMAND")[0][1]
    assert (eye["pattern"], eye["color"], eye["duration"], eye["conversation_id"]) == ("happy", "#ffb000", 1.5, "turn-1")
    assert rec.of("CHEST_OVERRIDE")[0][1] == {"command": "X2", "hold": 1.5}
    assert rec.of("STAGE_LIGHTS")[0][1] == {"cue": "red_flash", "mode": None, "fade": 0.0, "hold": 0.0, "rig": None}
    assert rec.of("SHOW_SFX")[0][1] == {"id": "airhorn"}
    assert rec.of("AUDIO_DUCKING_START") and rec.of("AUDIO_DUCKING_STOP")
    ended = rec.of("SHOW_ENDED")[0]
    assert ended[1]["reason"] == "done" and ended[1]["conversation_id"] == "turn-1"
    assert ended[0] >= 0.15 - TOL  # the run lasts until its clip (0.3 s at speed 2) is done


# ---------------------------------------------------------------------------- plans, DJ, CLI

async def test_plan_steps_perform_and_optional_missing_sequence(rig):
    bus, svc, rec = rig
    use(svc, [cue("hype", [sfx(0, "x")])])
    rec.reset()
    bus.emit(EventTopics.PLAN_READY.value, {"plan_id": "p1", "plan": {"plan_id": "p1", "layer": "show", "steps": [
        {"step_type": "perform", "id": "hype", "wait_for_completion": True},
        {"step_type": "sequence", "id": "dj_intro", "optional": True},
    ]}})
    assert await until(lambda: rec.of("PLAN_ENDED"))
    assert rec.of("PLAN_ENDED")[-1][1]["status"] == "completed"  # a missing show never fails a plan
    assert [p["source"] for _, p in rec.of("SHOW_STARTED")] == ["timeline"]


async def test_dj_mode_start_runs_dj_intro_and_dj_stop_ends_it(rig):
    bus, svc, rec = rig
    use(svc, [seq("dj_intro", [sfx(0, "intro"), sfx(2, "late")])])
    brain = BrainService(bus)
    brain._music_library = {"song": MusicTrack(name="song", path="/tmp/song.mp3", title="Song")}
    rec.reset()
    await brain._handle_dj_mode_changed({"is_active": True})
    assert await until(lambda: rec.of("SHOW_SFX"))
    assert rec.of("SHOW_STARTED")[0][1]["id"] == "dj_intro"
    bus.emit(EventTopics.DJ_MODE_CHANGED.value, {"is_active": False})
    assert await until(lambda: rec.of("SHOW_ENDED"))
    assert rec.of("SHOW_ENDED")[0][1]["reason"] == "interrupted"
    for t in brain._tasks:
        t.cancel()


async def test_cli_show_commands(rig):
    bus, svc, rec = rig
    use(svc, [clip("nod"), seq("s", [sfx(1, "late")])])

    def cli(raw):
        bus.emit(EventTopics.SHOW_COMMAND.value, {"command": raw.split()[0], "args": raw.split()[1:], "raw_input": raw})

    cli("show list")
    assert await until(lambda: rec.of("CLI_RESPONSE"))
    listing = rec.of("CLI_RESPONSE")[0][1]["message"]
    assert "nod" in listing and "s " in listing
    cli("show nod 0.5 1.5")
    assert await until(lambda: rec.of("SHOW_MOTION"))
    assert rec.of("SHOW_PERFORM")[0][1] == {"id": "nod", "params": {"intensity": 0.5, "speed": 1.5}, "source": "cli"}
    assert rec.of("SHOW_MOTION")[0][1]["intensity"] == 0.5
    cli("show s")
    await asyncio.sleep(0.05)
    cli("show stop s")
    assert await until(lambda: any(p["id"] == "s" for _, p in rec.of("SHOW_ENDED")))
    cli("freeze")
    assert await until(lambda: rec.of("MOTION_FREEZE"))
    assert rec.of("MOTION_FREEZE")[0][1] == {"on": True}
    cli("show nod")
    assert await until(lambda: any("refused" in p["message"] for _, p in rec.of("CLI_RESPONSE")))
    cli("unfreeze")
    assert await until(lambda: len(rec.of("MOTION_FREEZE")) == 2)
    assert not svc.show_player.frozen


async def test_cli_show_reaches_the_timeline_through_the_real_dispatcher(rig):
    from cantina_os.services.command_dispatcher_service import CommandDispatcherService

    bus, svc, rec = rig
    use(svc, [clip("listen_up")])
    dispatcher = CommandDispatcherService(bus)
    await dispatcher.start()
    for cmd in ("show", "freeze", "unfreeze"):
        dispatcher.register_command(cmd, "timeline_executor_service", EventTopics.SHOW_COMMAND)
    try:
        bus.emit(EventTopics.CLI_COMMAND.value, {"command": "show", "args": ["listen_up"], "raw_input": "show listen_up"})
        assert await until(lambda: rec.of("SHOW_MOTION"))
        assert rec.of("SHOW_MOTION")[0][1]["clip"] == "listen_up"
    finally:
        await dispatcher.stop()
