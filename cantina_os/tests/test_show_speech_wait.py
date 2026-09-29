"""
A show's own ``speak`` line queued behind Claude's reply must not deadlock ``wait for speech_end``.

ElevenLabs is a single FIFO: a show line requested while R3X is answering plays after the
answer. That is accepted behaviour (not redesigned here). What must hold:

* while the line is **queued** behind other speech, the wait holds (the queue time is not
  counted against the line's own budget, or the run would resume mid-sentence);
* once the line **plays**, the wait ends when it completes;
* if **nothing** is speaking and the line never starts (TTS down, request dropped), the wait
  gives up after ``NO_SPEECH_GRACE_S`` (1.5 s, the sim's ``waitGraceS``) instead of the full
  text budget;
* a reply whose completion never arrives is still bounded by the hard ceiling.

Real TimelineExecutorService on a real pyee bus; the test plays ElevenLabs' STARTED/COMPLETE.
"""

import asyncio
import time

import cantina_os.services.timeline_executor_service.show_player as show_player_mod
from cantina_os.core.event_topics import EventTopics

from .test_show_player import TOL, complete_speech, perform, rig, seq, sfx, until, use  # noqa: F401


def started(bus, *, conversation_id=None, clip_id=None, text="x"):
    """As ElevenLabsService emits it (clip_id added 2026-09-29 so show lines are visible)."""
    bus.emit(EventTopics.SPEECH_GENERATION_STARTED.value,
             {"conversation_id": conversation_id, "text": text, "clip_id": clip_id})


def show_seq():
    return seq("s", [
        {"at": 0, "do": "speak", "text": "Time to boogie!"},
        {"at": 0, "do": "wait", "for": "speech_end"},
        sfx(0.05, "after"),
    ])


async def test_a_line_queued_behind_claudes_reply_waits_for_its_own_end(rig):
    bus, svc, rec = rig
    use(svc, [show_seq()])
    started(bus, conversation_id="turn-1", clip_id="turn-1")  # Claude's reply is playing
    rec.reset()
    perform(bus, "s")
    assert await until(lambda: rec.of("TTS_GENERATE_REQUEST"))
    line = rec.of("TTS_GENERATE_REQUEST")[0][1]["clip_id"]

    await asyncio.sleep(2.0)  # longer than the no-speech grace: queue time must not count
    assert not rec.of("SHOW_SFX"), "the run resumed while its line was still queued"

    complete_speech(bus, "turn-1")          # reply ends; ElevenLabs dequeues the show line
    await asyncio.sleep(0.05)
    started(bus, clip_id=line)
    await asyncio.sleep(0.3)
    assert not rec.of("SHOW_SFX"), "the run resumed while its own line was playing"
    t_done = time.monotonic() - rec.t0
    complete_speech(bus, line)

    assert await until(lambda: rec.of("SHOW_SFX"))
    assert abs(rec.times("SHOW_SFX", "id", "after")[0] - (t_done + 0.05)) < TOL


async def test_a_line_that_never_starts_costs_the_grace_not_the_budget(rig):
    """TTS unavailable: nothing is speaking and the request goes nowhere."""
    bus, svc, rec = rig
    use(svc, [show_seq()])
    rec.reset()
    perform(bus, "s")
    assert await until(lambda: rec.of("SHOW_SFX"), timeout=5.0)
    t = rec.times("SHOW_SFX", "id", "after")[0]
    grace = show_player_mod.NO_SPEECH_GRACE_S
    assert grace + 0.05 - TOL < t < grace + 0.05 + 0.2, t


async def test_a_reply_that_never_completes_is_still_bounded(rig, monkeypatch):
    bus, svc, rec = rig
    monkeypatch.setattr(show_player_mod, "FOREIGN_SPEECH_WAIT_S", 0.5)
    svc.show_player._speech_budget = lambda text: 0.2
    use(svc, [show_seq()])
    started(bus, conversation_id="turn-lost", clip_id="turn-lost")  # completion never comes
    rec.reset()
    perform(bus, "s")
    assert await until(lambda: rec.of("SHOW_SFX"), timeout=3.0), "the wait deadlocked"
    assert abs(rec.times("SHOW_SFX", "id", "after")[0] - (0.5 + 0.2 + 0.05)) < 0.1
