"""
Live BPM source: offline beat analysis -> MusicTrack.bpm -> MUSIC_PLAYBACK_STARTED -> followers.

Until 2026-09-29 no track carried a tempo, so every beat-clock show sequence ran at its own
``bpm``, the chest always pulsed at ``CHEST_DEFAULT_BPM`` and the sim at its slider. These
tests pin each link, with the real librosa analysis on a synthetic click track whose tempo
is known exactly, and the real consumers on a real pyee bus.
"""

import asyncio
import json
import os
import time
from pathlib import Path
from unittest.mock import AsyncMock

import numpy as np
import pytest
import soundfile as sf
from pyee.asyncio import AsyncIOEventEmitter

from cantina_os.core.event_topics import EventTopics
from cantina_os.models.music_models import MusicTrack
from cantina_os.services.music_controller_service.beat_analysis import (
    ANALYZER_VERSION,
    BackgroundBeatAnalyzer,
    BeatCache,
    BeatInfo,
    analyze_file,
    fold_bpm,
    refine_bpm,
)


@pytest.fixture(autouse=True)
def mock_external_apis():
    """Override conftest's autouse fixture; nothing external is touched here."""
    yield {}


CLICK_BPM = 128.0


def write_click_track(path: Path, bpm: float = CLICK_BPM, seconds: float = 20.0,
                      offset_s: float = 0.25, sr: int = 22050) -> Path:
    """A kick-like click on every beat: an unambiguous, exactly known tempo."""
    y = np.zeros(int(seconds * sr), dtype=np.float32)
    rng = np.random.default_rng(0)
    click = (rng.standard_normal(int(0.03 * sr)) * np.exp(-np.linspace(0, 8, int(0.03 * sr)))).astype(np.float32)
    t = offset_s
    while t < seconds - 0.05:
        i = int(t * sr)
        y[i:i + click.size] += click[: max(0, min(click.size, y.size - i))]
        t += 60.0 / bpm
    sf.write(str(path), 0.5 * y / np.abs(y).max(), sr)
    return path


@pytest.fixture(scope="module")
def click_wav(tmp_path_factory):
    return write_click_track(tmp_path_factory.mktemp("beats") / "click128.wav")


# =========================================================================================
# Pure helpers
# =========================================================================================

class TestTempoArithmetic:
    def test_octave_errors_fold_into_the_dance_range(self):
        assert fold_bpm(64) == 128
        assert fold_bpm(256) == 128
        assert fold_bpm(120) == 120
        assert fold_bpm(0) == 0

    def test_refine_averages_away_frame_quantisation(self):
        """Frame-quantised intervals alternate 0.4644/0.4876 s; the mean is the true tempo."""
        period = 60 / 126.0
        beats = [round(i * period / 0.02322) * 0.02322 for i in range(64)]  # snap to frames
        assert abs(refine_bpm(beats, 0.0) - 126.0) < 0.3

    def test_refine_ignores_a_skipped_beat(self):
        beats = [i * 0.5 for i in range(40)]
        del beats[20]  # the tracker missed one: a single 1.0 s interval
        assert abs(refine_bpm(beats, 0.0) - 120.0) < 0.01

    def test_refine_falls_back_with_too_few_beats(self):
        assert refine_bpm([0.0, 0.5, 1.0], 99.0) == 99.0


# =========================================================================================
# Real analysis
# =========================================================================================

def test_analysis_finds_the_known_tempo(click_wav):
    info = analyze_file(str(click_wav))
    assert abs(info.bpm - CLICK_BPM) < 1.0, info.bpm
    assert abs(info.first_beat_s - 0.25) < 0.06, info.first_beat_s
    assert len(info.beats) > 30


# =========================================================================================
# Cache
# =========================================================================================

class TestCache:
    def test_round_trip(self, tmp_path, click_wav):
        cache = BeatCache(str(tmp_path / "c"))
        cache.put(str(click_wav), BeatInfo(bpm=128.0, first_beat_s=0.25, beats=[0.25, 0.72]))
        got = cache.get(str(click_wav))
        assert got is not None and got.bpm == 128.0 and got.first_beat_s == 0.25

    def test_a_changed_file_invalidates_its_entry(self, tmp_path):
        track = write_click_track(tmp_path / "t.wav", seconds=2.0)
        cache = BeatCache(str(tmp_path / "c"))
        cache.put(str(track), BeatInfo(bpm=128.0, first_beat_s=0.25))
        st = os.stat(track)
        os.utime(track, (st.st_atime, st.st_mtime + 10))
        assert cache.get(str(track)) is None
        assert not cache.has_entry(str(track)), "a stale entry must be re-analysed"

    def test_another_method_version_is_ignored(self, tmp_path, click_wav):
        cache = BeatCache(str(tmp_path / "c"))
        cache.put(str(click_wav), BeatInfo(bpm=128.0, first_beat_s=0.25))
        f = cache._file_for(str(click_wav))
        data = json.loads(f.read_text())
        assert data["version"] == ANALYZER_VERSION
        data["version"] = "some-older-method"
        f.write_text(json.dumps(data))
        assert cache.get(str(click_wav)) is None

    def test_a_failure_is_remembered_but_yields_no_bpm(self, tmp_path, click_wav):
        cache = BeatCache(str(tmp_path / "c"))
        cache.put(str(click_wav), None, error="ValueError: no tempo")
        assert cache.get(str(click_wav)) is None
        assert cache.has_entry(str(click_wav)), "an undecodable file would be retried every start"

    def test_garbage_and_missing_files_fail_open(self, tmp_path, click_wav):
        cache = BeatCache(str(tmp_path / "c"))
        assert cache.get(str(tmp_path / "nope.mp3")) is None
        cache.dir.mkdir(parents=True)
        cache._file_for(str(click_wav)).write_text("{not json")
        assert cache.get(str(click_wav)) is None


# =========================================================================================
# Background worker (a real subprocess, as in production)
# =========================================================================================

async def test_background_worker_analyses_then_caches(tmp_path, click_wav):
    cache = BeatCache(str(tmp_path / "c"))
    analyzer = BackgroundBeatAnalyzer(cache)
    got = {}
    ok = await asyncio.wait_for(
        analyzer.run([str(click_wav)], lambda p, info: got.setdefault(p, info)), timeout=120
    )
    assert ok == 1
    assert abs(got[os.path.abspath(click_wav)].bpm - CLICK_BPM) < 1.0
    assert cache.get(str(click_wav)) is not None
    assert analyzer.pending([str(click_wav)]) == [], "a cached file must not be re-analysed"


async def test_background_worker_failure_is_no_bpm_not_an_error(tmp_path):
    bogus = tmp_path / "not_audio.mp3"
    bogus.write_bytes(b"this is not an mp3")
    analyzer = BackgroundBeatAnalyzer(BeatCache(str(tmp_path / "c")))
    got = {}
    ok = await asyncio.wait_for(analyzer.run([str(bogus)], lambda p, i: got.setdefault(p, i)), timeout=120)
    assert ok == 0 and got == {}


async def test_the_event_loop_keeps_ticking_during_analysis(tmp_path, click_wav):
    """The point of the separate process: analysis must not stall the voice loop."""
    analyzer = BackgroundBeatAnalyzer(BeatCache(str(tmp_path / "c")))
    task = asyncio.create_task(analyzer.run([str(click_wav)], lambda p, i: None))
    worst = 0.0
    while not task.done():
        t0 = time.perf_counter()
        await asyncio.sleep(0.01)
        worst = max(worst, time.perf_counter() - t0)
    await task
    assert worst < 0.25, f"the event loop stalled for {worst * 1000:.0f} ms during analysis"


# =========================================================================================
# MusicControllerService: the track model and the playback-started payload carry bpm
# =========================================================================================

class _Backend:
    async def play_track(self, track):
        return True

    async def set_volume(self, volume):
        return True

    async def stop_playback(self):
        return True


def _controller(tmp_path, track_path: Path, enable: bool = True):
    from cantina_os.services.music_controller_service.music_controller_service import (
        MusicControllerService,
    )

    svc = MusicControllerService(
        AsyncIOEventEmitter(),
        {"music_dir": str(tmp_path), "enable_beat_analysis": enable,
         "beat_cache_dir": str(tmp_path / "beats")},
    )
    track = MusicTrack(name="Click", path=str(track_path), track_id="Click", title="Click")
    svc.libraries["local"] = {"Click": track}
    svc.tracks = svc.libraries["local"]
    svc.backends = {"local": _Backend()}
    svc.emit = AsyncMock()
    return svc, track


def _started_payloads(svc):
    return [c.args[1] for c in svc.emit.await_args_list
            if c.args and c.args[0] == EventTopics.MUSIC_PLAYBACK_STARTED]


async def test_a_cached_tempo_reaches_the_playback_started_payload(tmp_path, click_wav):
    svc, track = _controller(tmp_path, click_wav)
    svc._beat_cache.put(str(click_wav), BeatInfo(bpm=128.0, first_beat_s=0.25))

    svc._attach_cached_beats()
    assert track.bpm == 128.0 and track.first_beat_s == 0.25

    await svc._play_track_by_name("Click", source="cli")
    payloads = _started_payloads(svc)
    assert payloads, "MUSIC_PLAYBACK_STARTED was not emitted"
    assert payloads[-1]["track"]["bpm"] == 128.0
    assert payloads[-1]["track"]["first_beat_s"] == 0.25


async def test_an_unanalysed_track_plays_with_no_bpm(tmp_path, click_wav):
    """Fail open: no analysis is 'no bpm', and the music path does not care."""
    svc, track = _controller(tmp_path, click_wav)
    svc._attach_cached_beats()
    await svc._play_track_by_name("Click", source="cli")
    assert _started_payloads(svc)[-1]["track"]["bpm"] is None


async def test_disabled_analysis_neither_reads_the_cache_nor_spawns(tmp_path, click_wav):
    svc, track = _controller(tmp_path, click_wav, enable=False)
    svc._beat_cache.put(str(click_wav), BeatInfo(bpm=128.0, first_beat_s=0.25))
    svc._attach_cached_beats()
    svc._start_beat_analysis()
    assert track.bpm is None and svc._beat_task is None


async def test_background_results_attach_to_the_library(tmp_path, click_wav):
    svc, track = _controller(tmp_path, click_wav)
    svc._start_beat_analysis()
    assert svc._beat_task is not None
    await asyncio.wait_for(svc._beat_task, timeout=120)
    assert track.bpm is not None and abs(track.bpm - CLICK_BPM) < 1.0


# =========================================================================================
# Followers: chest and the show player use the real tempo when known
# =========================================================================================

async def test_chest_pulses_at_the_track_tempo_and_falls_back_without_one():
    from cantina_os.services.chest_light_controller_service import ChestLightControllerService

    bus = AsyncIOEventEmitter()
    sent = []
    bus.on(EventTopics.CHEST_COMMAND.value, lambda p: sent.append(p["command"]))
    svc = ChestLightControllerService(bus, {"FORCE_MOCK_CHEST": "true", "CHEST_DEFAULT_BPM": "120"})
    await svc.start()
    svc._task.cancel()
    try:
        bus.emit(EventTopics.MUSIC_PLAYBACK_STARTED.value, {"track": {"title": "x", "bpm": 131.6}})
        await asyncio.sleep(0.01)
        svc.tick()
        assert "B132" in sent, sent
        bus.emit(EventTopics.MUSIC_PLAYBACK_STARTED.value, {"track": {"title": "y", "bpm": None}})
        await asyncio.sleep(0.01)
        svc.tick()
        assert sent[-1] == "B120" or "B120" in sent[-3:], sent
    finally:
        await svc.stop()
