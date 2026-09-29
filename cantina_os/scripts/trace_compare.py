#!/usr/bin/env python
"""Normalise and compare two bus traces (JSONL session logs from the Phase 0 tap).

    trace_compare.py EXPECTED.jsonl ACTUAL.jsonl [--allow scripts/trace_allowlist.json]
    trace_compare.py --latency TRACE.jsonl          # baseline latency table (markdown)
    trace_compare.py --show TRACE.jsonl             # print the normalised trace

A trace is cut into turns at each ``voice.listening.started`` or ``cli.command``. Per turn:

* ``sequence`` - distinct topics in order of first appearance (noise topics dropped); two
  topics may swap only if they were within TIE_MS of each other in both runs;
* ``actions``  - ordered (topic, key fields) for everything that does something; a
  different interleaving passes only if every action still lands within tolerance;
* ``counts``   - how many times each counted topic fired;
* ``timing``   - ms from the turn start to the first action / reply / speech, compared
  within tolerance (max(TOL_MS, TOL_FRAC * expected)).

Free text is compared only where a fixture pins it (the final reply and the spoken line).
Ids that are minted per run (conversation ids, run ids, event ids, timestamps) never enter
the normalised form. Intended differences go in the allow-list: ``[{"match": "<regex over
the difference line>", "reason": "..."}]`` - empty in Phase 0. Exit status 1 on any
difference not allowed.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BOUNDARY = {"voice.listening.started", "cli.command"}
# Timers, health and per-sample telemetry: present in both runs, but their number and
# interleaving depend on wall-clock pacing rather than behaviour.
NOISE_PREFIXES = ("debug.", "service_status", "service.status", "speech.synthesis.amplitude", "system.heartbeat",
                  "performance.", "latency.", "nervous.", "memory.", "vision.", "log.", "music.progress",
                  "track.ending", "speech.alignment", "llm.response.chunk",
                  "chest.command")  # chest.command: the chest's own status/tempo ticker
# Driven by BrainService's free-running commentary-cache poll, not by the turn: counted, but
# their position relative to the turn's own events is left out of ``sequence``.
LOOP_TOPICS = {"dj.next_track.selected", "dj.commentary.request", "gpt.commentary.response",
               "speech.cache.request", "speech.cache.updated", "speech.cache.ready", "tts.request",
               "tts.audio.data"}
KEY_FIELDS: Dict[str, Tuple[str, ...]] = {
    "music.command": ("action", "song_query", "command", "args"),
    "eye.command": ("pattern", "color", "command", "args"),
    "dj.command": ("command", "action", "args"),
    "intent.detected": ("intent_name", "parameters", "source"),
    "show.perform": ("id", "source"),
    "show.started": ("id", "kind", "source"),
    "show.ended": ("id", "kind", "reason"),
    "show.sfx": ("id",),
    "stage.lights": ("cue", "mode"),
    "chest.override": ("command",),
    "motion.freeze": ("on",),
    "dj.mode.changed": ("is_active",),
    "system.mode.change": ("new_mode",),
    "tts.generate.request": ("text",),
}
COUNTED = {"music.command", "eye.command", "dj.command", "intent.detected", "show.perform", "show.started",
           "show.ended", "show.motion", "show.sfx", "stage.lights", "chest.override", "speech.generation.started",
           "speech.generation.complete", "music.playback.started", "tts.generate.request"}
ACTION_TOPICS = {"music.command", "eye.command", "dj.command"}
TOL_MS, TOL_FRAC = 300.0, 0.3
TIE_MS = 150.0


def load(path: Path) -> List[dict]:
    out = []
    for line in Path(path).read_text().splitlines():
        if line.strip():
            rec = json.loads(line)
            if "kind" not in rec:
                out.append(rec)
    return out


def _noise(topic: str) -> bool:
    return topic.startswith(NOISE_PREFIXES)


def _fields(topic: str, p: Any) -> Optional[dict]:
    p = p if isinstance(p, dict) else {}
    if topic == "llm.response":
        return {"text": (p.get("text") or "").strip()} if p.get("is_complete") else None
    if topic == "speech.generation.started":
        return {"text": (p.get("text") or "").strip()}
    if topic == "music.playback.started":
        t = p.get("track")
        return {"track": t.get("name") if isinstance(t, dict) else t}
    keys = KEY_FIELDS.get(topic)
    if keys is None:
        return None
    return {k: p.get(k) for k in keys if p.get(k) is not None}


def turns(records: List[dict]) -> List[dict]:
    segs: List[List[dict]] = [[]]
    for r in records:
        if r["topic"] in BOUNDARY:
            segs.append([])
        segs[-1].append(r)
    out = []
    for i, seg in enumerate(segs):
        if not seg:
            continue
        t0 = seg[0]["t_mono"]
        label = "startup" if i == 0 else seg[0]["topic"]
        sequence, first_ms, counts, actions, action_ms, timing = [], {}, {}, [], [], {}
        for r in seg:
            topic, p = r["topic"], r.get("payload")
            if isinstance(p, dict):
                if topic == "voice.listening.stopped" and p.get("transcript"):
                    label = f'say "{p["transcript"]}"'
                elif topic == "cli.command" and p.get("raw_input"):
                    label = f'cli "{p["raw_input"]}"'
            if _noise(topic):
                continue
            if topic in LOOP_TOPICS:
                counts[topic] = counts.get(topic, 0) + 1
                continue
            if topic not in sequence:
                sequence.append(topic)
                first_ms[topic] = round((r["t_mono"] - t0) * 1000, 1)
            if topic in COUNTED:
                counts[topic] = counts.get(topic, 0) + 1
            ms = round((r["t_mono"] - t0) * 1000, 1)
            f = _fields(topic, p)
            if f is not None:
                actions.append([topic, f])
                action_ms.append(ms)
            if topic in ACTION_TOPICS:
                timing.setdefault("first_action", ms)
            elif topic == "llm.response":
                timing.setdefault("first_reply_chunk", ms)
            elif topic == "speech.generation.started":
                timing.setdefault("speech_started", ms)
        out.append({"label": label, "sequence": sequence, "first_ms": first_ms, "counts": counts, "actions": actions,
                    "action_ms": action_ms, "timing": timing})
    return out


def _same_up_to_ties(e: dict, a: dict) -> bool:
    """Same topics, and every pair that swapped places was a near-tie (< TIE_MS apart) in
    both runs - e.g. the tail of the previous line's playback against the next dispatch."""
    if set(e["sequence"]) != set(a["sequence"]):
        return False
    pos = {t: i for i, t in enumerate(a["sequence"])}
    for i, x in enumerate(e["sequence"]):
        for y in e["sequence"][i + 1:]:
            if pos[x] > pos[y] and (abs(e["first_ms"][x] - e["first_ms"][y]) > TIE_MS
                                    or abs(a["first_ms"][x] - a["first_ms"][y]) > TIE_MS):
                return False
    return True


def compare(exp: List[dict], act: List[dict], startup: bool = False) -> List[str]:
    diffs = []
    if len(exp) != len(act):
        diffs.append(f"turn count: expected {len(exp)}, got {len(act)}")
    for i, (e, a) in enumerate(zip(exp, act)):
        name = f'turn {i} {e["label"]}'
        if e["label"] != a["label"]:
            diffs.append(f"{name}: label differs: {a['label']}")
            continue
        if e["label"] == "startup" and not startup:  # service start-up races (to_thread, device probes)
            if set(e["sequence"]) != set(a["sequence"]):
                diffs.append(f"{name}: topics differ: missing {sorted(set(e['sequence']) - set(a['sequence']))} "
                             f"extra {sorted(set(a['sequence']) - set(e['sequence']))}")
            continue
        if e["sequence"] != a["sequence"] and not _same_up_to_ties(e, a):
            diffs.append(f"{name}: sequence differs:\n    expected {e['sequence']}\n    actual   {a['sequence']}")
        for topic in sorted(set(e["counts"]) | set(a["counts"])):
            if e["counts"].get(topic) != a["counts"].get(topic):
                diffs.append(f"{name}: count {topic}: expected {e['counts'].get(topic)}, got {a['counts'].get(topic)}")
        if e["actions"] != a["actions"]:
            ea = [json.dumps(x, sort_keys=True) for x in e["actions"]]
            aa = [json.dumps(x, sort_keys=True) for x in a["actions"]]
            if sorted(ea) != sorted(aa):
                diffs.append(f"{name}: actions differ: missing {[x for x in ea if x not in aa]} "
                             f"extra {[x for x in aa if x not in ea]}")
            else:
                # Same actions, different interleaving: fine when it is only concurrent runs
                # racing, i.e. every action still lands within tolerance of its recorded time.
                pool: Dict[str, List[float]] = {}
                for x, ms in zip(aa, a["action_ms"]):
                    pool.setdefault(x, []).append(ms)
                for x, ms in zip(ea, e["action_ms"]):
                    got = pool[x].pop(0)
                    if abs(got - ms) > max(TOL_MS, TOL_FRAC * ms):
                        diffs.append(f"{name}: action reordered beyond tolerance: {x} expected {ms} ms, got {got} ms")
        for k, ev in e["timing"].items():
            av = a["timing"].get(k)
            if av is None:
                diffs.append(f"{name}: timing {k}: missing (expected {ev} ms)")
            elif abs(av - ev) > max(TOL_MS, TOL_FRAC * ev):
                diffs.append(f"{name}: timing {k}: expected {ev} ms, got {av} ms")
    return diffs


def latency(records: List[dict]) -> str:
    """Per spoken turn, attributed by conversation_id where the event carries one (replies
    queue behind each other on one voice, so "the next event after the stop" is wrong)."""
    rows: List[dict] = []
    by_cid: Dict[str, dict] = {}
    last = None
    for r in records:
        t = r["topic"]
        p = r.get("payload") if isinstance(r.get("payload"), dict) else {}
        cid = p.get("conversation_id")
        if t == "voice.listening.stopped" and p.get("transcript"):
            last = {"label": p["transcript"], "t": r["t_mono"], "wall": r["t_wall"]}
            rows.append(last)
            if cid:
                by_cid[cid] = last
            continue
        row = by_cid.get(cid) if cid else last
        if row is None:
            continue
        ms = (r["t_mono"] - row["t"]) * 1000
        if t == "intent.detected":
            row.setdefault("intent", ms)
        elif t in ACTION_TOPICS:
            row.setdefault("action", ms)
        elif t == "llm.response" and cid:
            row.setdefault("first_chunk", ms)
            if p.get("is_complete"):
                row.setdefault("final", ms)
        elif t == "speech.generation.started" and cid and "audible" not in row and p.get("audio_t0"):
            row["audible"] = (float(p["audio_t0"]) - row["wall"]) * 1000
    cols = [("intent", "stop→intent"), ("action", "stop→action"), ("first_chunk", "stop→Claude 1st chunk"),
            ("final", "stop→Claude done"), ("tts", "Claude done→1st sample"), ("audible", "stop→1st audible sample")]
    for r in rows:
        if "audible" in r and "final" in r:
            r["tts"] = r["audible"] - r["final"]
    fmt = lambda v: "-" if v is None else f"{v:,.0f}"
    lines = ["| Turn | " + " | ".join(c[1] for c in cols) + " |", "|---|" + "---:|" * len(cols)]
    for r in rows:
        lines.append(f'| "{r["label"]}" | ' + " | ".join(fmt(r.get(k)) for k, _ in cols) + " |")
    meds = []
    for k, _ in cols:
        vals = [r[k] for r in rows if r.get(k) is not None]
        meds.append(statistics.median(vals) if vals else None)
    lines.append("| **median** | " + " | ".join(f"**{fmt(m)}**" if m is not None else "-" for m in meds) + " |")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("traces", nargs="+", type=Path)
    ap.add_argument("--allow", type=Path, default=Path(__file__).with_name("trace_allowlist.json"))
    ap.add_argument("--latency", action="store_true")
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--startup", action="store_true", help="compare start-up order strictly too")
    args = ap.parse_args()
    if args.latency:
        print(latency(load(args.traces[0])))
        return 0
    if args.show:
        print(json.dumps(turns(load(args.traces[0])), indent=1))
        return 0
    if len(args.traces) != 2:
        ap.error("need EXPECTED and ACTUAL")
    exp, act = (turns(load(p)) for p in args.traces)
    allow = json.loads(args.allow.read_text()) if args.allow.exists() else []
    diffs = compare(exp, act, args.startup)
    unexplained = [d for d in diffs if not any(re.search(a["match"], d) for a in allow)]
    for d in diffs:
        print(("ALLOWED " if d not in unexplained else "DIFF    ") + d)
    print(f"{len(exp)} turns compared; {len(diffs)} difference(s), {len(unexplained)} not allow-listed")
    return 1 if unexplained else 0


if __name__ == "__main__":
    sys.exit(main())
