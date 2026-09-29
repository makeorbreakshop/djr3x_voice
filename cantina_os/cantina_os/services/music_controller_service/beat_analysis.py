"""
Offline beat analysis for local tracks: tempo (bpm) plus beat times, cached on disk.

## Why

Beat-clock show sequences, the chest's music-tempo program and the sim's stage-light desk
all read ``track.bpm`` from ``MUSIC_PLAYBACK_STARTED``. Until 2026-09-29 no track carried
one, so every consumer silently fell back to a default (the sequence's own ``bpm``,
``CHEST_DEFAULT_BPM``, the sim's slider). This module is the producer.

## Rules

- **Never on the playback path.** Analysis runs in a separate, niced Python process
  (``python .../music_controller_service/beat_analysis.py FILE...``), so librosa's
  import cost, numba JIT and decoding never contend with the voice loop for the GIL.
  ``MusicControllerService`` reads only the cache when it loads the library.
- **Fail open.** A missing, stale, unreadable or failed analysis means "no bpm", never an
  error in the music path. Consumers already fall back when ``bpm`` is absent.
- **Cache key = absolute path + mtime + size + analyser version.** Editing or replacing a
  file invalidates it; bumping ``ANALYZER_VERSION`` invalidates everything.

## Method and its known limits

librosa 0.11 ``beat.beat_track`` (onset-strength autocorrelation + dynamic-programming beat
tracker, Ellis 2007) on a 22.05 kHz mono decode. It is the right cheap default, but a
classical tracker is prone to *octave errors* (reporting half or double the felt tempo) on
syncopated or sparse material. The documented upgrade path is a learned tracker:

- **Beat This!** (Foscarin et al., ISMIR 2024, CPJKU ``beat_this``): transformer beat and
  downbeat tracker, no DBN post-processing, state of the art across genres.
- **All-In-One** (Kim & Nam 2023, ``allin1``): beats, downbeats, tempo *and* section labels
  (intro/verse/chorus) in one pass - the sections would let DJ transitions land on phrase
  boundaries.

Both pull in torch-scale dependencies, so they are deliberately *not* added now. Either one
drops in behind ``analyze_file`` with an ``ANALYZER_VERSION`` bump; nothing else changes.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

#: Bump when the method changes; every cached result from an older version is ignored.
ANALYZER_VERSION = "librosa-beat_track-3"
DEFAULT_CACHE_DIR = "~/.cache/dj-r3x/beats"
#: Decode rate. librosa's default; beat tracking gains nothing from higher.
SAMPLE_RATE = 22050
#: Plausible dance-music range. Anything outside is folded by octaves into it.
BPM_MIN, BPM_MAX = 70.0, 180.0


@dataclass
class BeatInfo:
    """What the analysis knows about one file."""

    bpm: float
    #: Phase of the beat grid: seconds from the start of the file to beat 0, so beat ``n``
    #: falls at ``first_beat_s + n * 60 / bpm`` (see ``grid_phase``).
    first_beat_s: float
    beats: List[float] = field(default_factory=list)

    def summary(self) -> Dict[str, float]:
        """The small part that goes on the bus (beat lists stay in the cache)."""
        return {"bpm": self.bpm, "first_beat_s": self.first_beat_s}


def fold_bpm(bpm: float) -> float:
    """Fold an octave error into [BPM_MIN, BPM_MAX): 60 -> 120, 240 -> 120."""
    if not bpm or bpm <= 0:
        return 0.0
    while bpm < BPM_MIN:
        bpm *= 2.0
    while bpm >= BPM_MAX:
        bpm /= 2.0
    return bpm


def refine_bpm(beat_times: List[float], fallback: float) -> float:
    """Tempo from the tracked beats, at better than frame resolution.

    Both ``beat_track``'s own tempo and any single inter-beat interval are quantised to the
    analysis hop (512 samples = 23.2 ms at 22.05 kHz), which near 120 bpm is a ~3 bpm grid:
    a median IBI can only ever be 117.4, 123.0, 129.2 ... The *mean* of the intervals that
    agree with the median (within 15%, so skipped or doubled beats do not pull it) averages
    that quantisation away over a whole track.
    """
    if len(beat_times) < 8:
        return fallback
    ibis = [b - a for a, b in zip(beat_times, beat_times[1:]) if b > a]
    if not ibis:
        return fallback
    med = sorted(ibis)[len(ibis) // 2]
    steady = [x for x in ibis if abs(x - med) <= 0.15 * med]
    if len(steady) < 4:
        return fallback
    return 60.0 / (sum(steady) / len(steady))


# ---------------------------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------------------------

def _stat_key(path: str) -> Optional[Dict[str, object]]:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return {"path": os.path.abspath(path), "mtime": st.st_mtime, "size": st.st_size,
            "version": ANALYZER_VERSION}


class BeatCache:
    """One JSON sidecar per file under ``cache_dir``, named by a hash of its absolute path."""

    def __init__(self, cache_dir: Optional[str] = None) -> None:
        self.dir = Path(os.path.expanduser(cache_dir or DEFAULT_CACHE_DIR))

    def _file_for(self, path: str) -> Path:
        digest = hashlib.sha1(os.path.abspath(path).encode("utf-8")).hexdigest()[:20]
        return self.dir / f"{digest}.json"

    def get(self, path: str) -> Optional[BeatInfo]:
        """A still-valid cached result, or None. Never raises."""
        key = _stat_key(path)
        if key is None:
            return None
        try:
            data = json.loads(self._file_for(path).read_text())
            if any(data.get(k) != v for k, v in key.items()):
                return None  # file changed, or analysed by another method
            if data.get("error"):
                return None
            bpm = float(data["bpm"])
            if bpm <= 0:
                return None
            return BeatInfo(bpm=bpm, first_beat_s=float(data.get("first_beat_s", 0.0)),
                            beats=[float(b) for b in data.get("beats", [])])
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def has_entry(self, path: str) -> bool:
        """True when this exact file version was already attempted (success *or* failure),
        so a file librosa cannot decode is not re-analysed on every start."""
        key = _stat_key(path)
        if key is None:
            return False
        try:
            data = json.loads(self._file_for(path).read_text())
        except (OSError, ValueError):
            return False
        return all(data.get(k) == v for k, v in key.items())

    def put(self, path: str, info: Optional[BeatInfo], error: Optional[str] = None) -> None:
        key = _stat_key(path)
        if key is None:
            return
        record: Dict[str, object] = dict(key)
        if info is not None:
            record.update(asdict(info))
        if error:
            record["error"] = error[:300]
        self.dir.mkdir(parents=True, exist_ok=True)
        target = self._file_for(path)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(record))
        os.replace(tmp, target)  # atomic: a reader never sees half a file


# ---------------------------------------------------------------------------------------------
# Analysis (runs in the worker process)
# ---------------------------------------------------------------------------------------------

def analyze_file(path: str) -> BeatInfo:
    """Tempo and beat times for one audio file. Heavy: call from the worker process only."""
    import librosa  # deferred: ~1 s import plus numba JIT, never paid by CantinaOS itself
    import numpy as np

    y, sr = librosa.load(path, sr=SAMPLE_RATE, mono=True)
    if y.size == 0:
        raise ValueError("empty audio")
    tempo, beats = librosa.beat.beat_track(y=y, sr=sr, units="time")
    raw_bpm = float(np.atleast_1d(tempo)[0])
    beat_times = [round(float(b), 3) for b in np.atleast_1d(beats)]
    raw_bpm = refine_bpm(beat_times, raw_bpm)
    bpm = round(fold_bpm(raw_bpm), 1)
    if bpm <= 0:
        raise ValueError(f"no tempo found (raw {raw_bpm})")
    return BeatInfo(bpm=bpm, first_beat_s=grid_phase(beat_times, raw_bpm), beats=beat_times)


def grid_phase(beat_times: List[float], bpm: float) -> float:
    """Where the beat grid starts: the first tracked beat, stepped back whole beats towards 0.

    librosa trims weak beats at the edges, so its first beat is often the second or third
    real one. What a follower needs is the grid's *phase* (``beat n`` is at
    ``first_beat_s + n * 60 / bpm``), and stepping back by whole periods keeps that exact.
    """
    if not beat_times or bpm <= 0:
        return 0.0
    period = 60.0 / bpm
    return round(beat_times[0] % period, 3)


def _worker_main(argv: Optional[List[str]] = None) -> int:
    """``python beat_analysis.py [--cache-dir D] [--force] FILE...``: analyse, cache, print JSONL."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--cache-dir", default=None)
    parser.add_argument("--force", action="store_true", help="re-analyse even when cached")
    parser.add_argument("files", nargs="+")
    args = parser.parse_args(argv)
    try:
        os.nice(10)  # background work: yield the CPU to the voice loop
    except (AttributeError, OSError):
        pass
    cache = BeatCache(args.cache_dir)
    for path in args.files:
        out: Dict[str, object] = {"path": os.path.abspath(path)}
        cached = None if args.force else cache.get(path)
        if cached is not None:
            out.update(cached.summary(), cached=True)
        else:
            try:
                info = analyze_file(path)
                cache.put(path, info)
                out.update(info.summary(), cached=False)
            except Exception as exc:  # noqa: BLE001 - any failure is "no bpm", recorded once
                cache.put(path, None, error=f"{type(exc).__name__}: {exc}")
                out["error"] = f"{type(exc).__name__}: {exc}"
        print(json.dumps(out), flush=True)
    return 0


# ---------------------------------------------------------------------------------------------
# CantinaOS side: schedule the worker without touching the event loop's thread
# ---------------------------------------------------------------------------------------------

class BackgroundBeatAnalyzer:
    """Runs the worker process over files the cache does not know, one process per batch.

    ``on_result(path, BeatInfo)`` is called on the event loop as each file finishes, so the
    caller can attach ``bpm`` to its track model. Cancelling the task kills the worker.
    """

    def __init__(self, cache: BeatCache, *, python: Optional[str] = None,
                 logger_: Optional[logging.Logger] = None) -> None:
        self.cache = cache
        self.python = python or sys.executable
        self.log = logger_ or logger
        self._proc: Optional[asyncio.subprocess.Process] = None

    def pending(self, paths: Iterable[str]) -> List[str]:
        return [p for p in paths if not self.cache.has_entry(p)]

    async def run(self, paths: Iterable[str],
                  on_result: Callable[[str, BeatInfo], None]) -> int:
        """Analyse ``paths`` (already-cached ones are skipped). Returns how many succeeded."""
        todo = self.pending(paths)
        if not todo:
            return 0
        self.log.info(f"Beat analysis: {len(todo)} track(s) queued in a background process")
        ok = 0
        try:
            # Run this file by path, not ``-m``: importing the package would import
            # cantina_os.services.__init__ and with it every service. The worker needs only
            # the stdlib and librosa.
            self._proc = await asyncio.create_subprocess_exec(
                self.python, str(Path(__file__).resolve()),
                "--cache-dir", str(self.cache.dir), *todo,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            )
            assert self._proc.stdout is not None
            async for raw in self._proc.stdout:
                try:
                    row = json.loads(raw)
                except ValueError:
                    continue
                if row.get("error") or not row.get("bpm"):
                    self.log.info(f"Beat analysis: no tempo for {Path(row.get('path', '?')).name}: "
                                  f"{row.get('error', 'no bpm')}")
                    continue
                info = BeatInfo(bpm=float(row["bpm"]), first_beat_s=float(row.get("first_beat_s", 0.0)))
                ok += 1
                try:
                    on_result(str(row["path"]), info)
                except Exception as exc:  # noqa: BLE001 - a consumer bug must not stop analysis
                    self.log.warning(f"Beat analysis consumer failed: {exc}")
            await self._proc.wait()
        except asyncio.CancelledError:
            self._kill()
            raise
        except Exception as exc:  # noqa: BLE001 - fail open: no bpm, music unaffected
            self.log.warning(f"Beat analysis unavailable: {exc}")
            self._kill()
        finally:
            self._proc = None
        self.log.info(f"Beat analysis finished: {ok}/{len(todo)} track(s) have a tempo")
        return ok

    def _kill(self) -> None:
        proc = self._proc
        if proc is not None and proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass


if __name__ == "__main__":
    sys.exit(_worker_main())
