"""How each thing goes in, without passing through anything already there (SCHEMA.md "Step" `sequence`).

For every step, in the order its items go in (the viewer's rules, ported: parts one at a time - a like
group with no fastener of its own together - each followed by the fasteners it completes, a batch of
fasteners inserts first, crosswise, each nut or washer with its screw), each item gets an approach
path: way points (mm, assembly frame, offsets from its seated pose; the first is where it starts, it
ends seated). The path is swept at ~1 mm against everything placed by then (FCL on the meshes):

- a part: straight in along its way in - a mate's axis or normal with something built, else the screws
  that hold it to something built, else straight out from what is built - from the end of that axis
  that is clear, further out when the start is blocked; else a two-leg path (come in level from the
  side, then along the axis); else the clearest of the six principal directions;
- a fastener: along its own axis only - a screw or a washer from the head side, a nut from the far
  side, an insert or a nut in a trap from far enough out to start outside the part it goes into.

Contact at the seat is not a crossing: what the item already touches (or overlaps) where it seats may
be touched on the last leg when that leg runs along the item's own axis (a bearing sliding down its
bore, a screw down its thread), and otherwise only in the last 3 mm. An item no path clears keeps the
least colliding one, `clean: false`, with what it hits.
"""

from __future__ import annotations

import math
import re

import numpy as np

from . import geom
from .collide import mesh_hash
from .model import Fastener, Step

STEP_MM = 1.0
SEAT_SLACK_MM = 3.0
PART_PULL = 0.55  # as the viewer: x the part's arrival distance (50-120 mm by size)


def _unit(v):
    v = np.asarray(v, float)
    n = np.linalg.norm(v)
    return v / n if n > 1e-12 else v


def _kind(spec: dict) -> str:
    t = str(spec.get("type", "")).lower()
    if t == "insert":
        return "insert"
    if t == "t_nut":
        return "other"
    if "nut" in t:
        return "nut"
    if "washer" in t:
        return "washer"
    if t == "pin" or "dowel" in t:
        return "pin"
    if re.search(r"shcs|bhcs|fhcs|screw|bolt|grub|threaded", t):
        return "screw"
    return "other"


def _travel(spec: dict) -> tuple[float, int]:
    """(mm, side): as sequence.ts fastenerTravel; side +1 from the head side (against +Z), -1 the far side."""
    k = _kind(spec)
    ln = float(spec.get("length_mm") or 8)
    if k == "nut":
        return 16.0, -1
    if k == "screw":
        return max(18.0, ln * 1.6), 1
    if k == "washer":
        return 14.0, 1
    if k == "insert":
        return 12.0, 1
    return max(12.0, ln * 1.2), 1


# ------------------------------------------------------------------ order (sequence.ts, ported)

def _stem(pid: str) -> str:
    s = pid.lower()
    while True:
        t = re.sub(r"_(\d+|l|r|m|left|right|full)$", "", s)
        if t == s or not t:
            return s
        s = t


def _crosswise(items: list, at, axis) -> list:
    if len(items) < 3:
        return items
    c = np.mean([at(i) for i in items], axis=0)
    n = _unit(np.sum([axis(i) for i in items], axis=0))
    if np.linalg.norm(n) < 0.5:
        n = np.array([0, 1.0, 0])
    u = np.array([1.0, 0, 0]) if abs(n[0]) < 0.9 else np.array([0, 0, 1.0])
    u = _unit(u - n * (u @ n))
    v = np.cross(n, u)
    ring = sorted(items, key=lambda i: math.atan2((at(i) - c) @ v, (at(i) - c) @ u))
    half = (len(ring) + 1) // 2
    out = []
    for k in range(half):
        out.append(ring[k])
        if k + half < len(ring):
            out.append(ring[k + half])
    return out


def _fastener_order(batch: list[Fastener]) -> list[Fastener]:
    at = lambda f: f.matrix[:3, 3]
    ax = lambda f: f.matrix[:3, 2]
    kind = lambda f: _kind(f.spec)
    inserts = _crosswise([f for f in batch if kind(f) == "insert"], at, ax)
    screws = _crosswise([f for f in batch if kind(f) in ("screw", "pin", "other")], at, ax)
    left = [f for f in batch if kind(f) in ("nut", "washer")]
    out = list(inserts)

    def off(p, s):
        d = p - at(s)
        return np.linalg.norm(d - ax(s) * (d @ ax(s)))

    for s in screws:
        mine = [f for f in left if off(at(f), s) < 1.5]
        for f in mine:
            left.remove(f)
        out += [f for f in mine if kind(f) == "washer"] + [s] + [f for f in mine if kind(f) != "washer"]
    out += _crosswise(left, at, ax)
    return out


def order(step: Step, fasts: dict[str, Fastener], seated_before: set[str], parts: list[str] | None = None,
          riders: dict[str, list[str]] | None = None) -> list[dict]:
    """The step's items in the order they go in: [{"ids", "kind"}]. `riders`: hardware put into a part
    before the part itself goes in (the inserts of step 1), seated when that part is."""
    parts = list(parts if parts is not None else step.parts)
    riders = riders or {}
    floating = {f for v in riders.values() for f in v}
    fs = [fasts[i] for i in step.fasteners if i in fasts and fasts[i].matrix is not None]
    own = lambda ids: any(any(j in ids for j in f.joins) for f in fs)
    groups: dict[str, list[str]] = {}
    for p in parts:
        groups.setdefault(_stem(p), []).append(p)
    units, done = [], set()
    for p in parts:
        if p in done:
            continue
        g = groups[_stem(p)]
        tog = g if len(g) > 1 and not own(g) else [p]
        done.update(tog)
        units.append(tog)
    in_step = set(parts)
    seated: set[str] = set()
    queued: set[str] = set()
    out: list[dict] = []

    def flush():
        ready = [f for f in fs if f.id not in queued
                 and all(j in seated or j in seated_before or (j not in in_step and j not in floating) for j in f.joins)]
        for f in _fastener_order(ready):
            queued.add(f.id)
            out.append({"ids": [f.id], "kind": "fastener"})

    flush()
    for u in units:
        out.append({"ids": u, "kind": "part"})
        seated.update(u)
        for p in u:
            seated.update(riders.get(p, []))
        flush()
    for f in _fastener_order([f for f in fs if f.id not in queued]):
        out.append({"ids": [f.id], "kind": "fastener"})
    return out


# ------------------------------------------------------------------ the sweep

class _World:
    """FCL bodies: every part and fastener at its seat, and what is placed so far."""

    def __init__(self, asm, fasteners: list[Fastener]):
        import fcl

        self.fcl = fcl
        self.mesh: dict[str, object] = {}
        for p in asm.parts:
            if p.mesh is not None and len(p.mesh.faces) and not p.replaced_by:
                self.mesh[p.id] = p.mesh
        for f in fasteners:
            if f.matrix is None:
                continue
            m = f.mesh if f.mesh is not None else _safe_fastener_mesh(f.spec)
            if m is None:
                continue
            mm = m.copy()
            mm.apply_transform(f.matrix)
            self.mesh[f.id] = mm
        self.obj = {}
        self.box = {k: np.asarray(m.bounds) for k, m in self.mesh.items()}
        self.placed: list[str] = []

    def body(self, k):
        if k not in self.obj:
            m = self.mesh[k]
            g = self.fcl.BVHModel()
            g.beginModel(len(m.vertices), len(m.faces))
            g.addSubModel(np.asarray(m.vertices, float), np.asarray(m.faces, np.int32))
            g.endModel()
            self.obj[k] = self.fcl.CollisionObject(g, self.fcl.Transform())
        return self.obj[k]

    def hits(self, k: str, off: np.ndarray, among: list[str]) -> list[str]:
        """What `k` moved by `off` collides with among `among`."""
        if k not in self.mesh:
            return []
        lo, hi = self.box[k][0] + off, self.box[k][1] + off
        out = []
        o = self.body(k)
        o.setTransform(self.fcl.Transform(np.eye(3), off.astype(float)))
        for b in among:
            if b == k or b not in self.mesh:
                continue
            blo, bhi = self.box[b]
            if np.any(hi < blo - 0.01) or np.any(lo > bhi + 0.01):
                continue
            ob = self.body(b)
            ob.setTransform(self.fcl.Transform())
            if self.fcl.collide(o, ob, self.fcl.CollisionRequest(), self.fcl.CollisionResult()) > 0:
                out.append(b)
        return out


def _safe_fastener_mesh(spec):
    try:
        return geom.fastener_mesh(spec)
    except Exception:
        return None


def _samples(path: list[np.ndarray]) -> list[np.ndarray]:
    """The path (start -> ... -> seat) every ~STEP_MM, seat last."""
    pts = [np.asarray(p, float) for p in path] + [np.zeros(3)]
    out = []
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        n = max(1, int(np.ceil(np.linalg.norm(b - a) / STEP_MM)))
        out += [a + (b - a) * (s / n) for s in range(n)]
    out.append(pts[-1])
    return out


def _sweep(w: _World, ids: list[str], path: list[np.ndarray], axis: np.ndarray | None, placed: list[str]) -> tuple[int, set[str]]:
    """Count the samples of the path that cross something placed. Seat contact is not a crossing: what an item
    touches where it seats it may keep touching on the way in for as long as it touches it without a break
    from the seat back (a bearing down its bore, a glued part on its face), and anything in the last
    SEAT_SLACK_MM."""
    pts = _samples(path)
    bad, what = 0, set()
    for k in ids:
        hits = [set(w.hits(k, off, placed)) for off in pts]
        seat_run: dict[str, int] = {}
        for b in hits[-1]:
            n = 0
            for h in reversed(hits):
                if b not in h:
                    break
                n += 1
            seat_run[b] = n  # samples from the seat back that keep touching it
        dist_left = [np.linalg.norm(p) for p in pts]  # (offset from the seat: near enough on a straight leg)

        def deeper(b, off):
            """Moving further into it than where it seats (boxes' shared volume grows): pushing through, not sliding out."""
            return _shared(w, k, off, b) > _shared(w, k, np.zeros(3), b) * 1.05 + 1.0

        for i, h in enumerate(hits):
            from_end = len(hits) - 1 - i
            cross = {b for b in h if not ((from_end < seat_run.get(b, 0) and not deeper(b, pts[i])) or dist_left[i] <= SEAT_SLACK_MM)}
            if cross:
                bad += 1
                what |= cross
    return bad, what


def _part_axis(asm, pid: str, built: set[str], fasts: list[Fastener]) -> np.ndarray | None:
    p = next(x for x in asm.parts if x.id == pid)
    feats = p.features or {}
    best = None
    for m in asm.mates:
        mine = m.a if m.a[0] == pid else m.b if m.b[0] == pid else None
        if mine is None:
            continue
        other = m.b if mine is m.a else m.a
        if other[0] not in built:
            continue
        f = feats.get(mine[1]) or {}
        d = f.get("d") or f.get("n")
        if d is None:
            continue
        rank = 0 if f.get("type") == "axis" else 1
        if best is None or rank < best[0]:
            best = (rank, _unit(d))
    if best:
        return best[1]
    for f in fasts:
        if f.matrix is not None and pid in f.joins and any(j != pid and j in built for j in f.joins):
            return _unit(f.matrix[:3, 2])
    return None


def _size(w: _World, k: str) -> float:
    b = w.box.get(k)
    return float(np.linalg.norm(b[1] - b[0])) if b is not None else 20.0


def plan(asm, steps: list[Step], fasteners: list[Fastener]) -> list[dict]:
    """Each step's `sequence` (items with their paths), in place; returns the items no path clears."""
    key = geom._cache_key(
        "paths-v4", STEP_MM, SEAT_SLACK_MM,
        sorted((p.id, mesh_hash(p.mesh)) for p in asm.parts if p.mesh is not None),
        sorted((f.id, np.round(f.matrix, 3).tobytes().hex() if f.matrix is not None else "", str(sorted(f.spec.items()))) for f in fasteners),
        [(s.id, s.parts, s.fasteners) for s in steps], [(m.a, m.b) for m in asm.mates],
    )
    res = geom.cached(key, lambda: _plan(asm, steps, fasteners))
    for s in steps:
        s.sequence = res["seq"].get(s.id, [])
    return res["unclean"]


def _plan(asm, steps: list[Step], fasteners: list[Fastener]) -> dict:
    w = _World(asm, fasteners)
    fasts = {f.id: f for f in fasteners}
    part_ids = {p.id for p in asm.parts}
    parts_by = {p.id: p for p in asm.parts}
    built: set[str] = set()
    seqs: dict[str, list] = {}
    unclean = []

    def floating(placed_parts: set[str]) -> dict[str, list[str]]:
        """Hardware already in that rides a part not yet in: part -> its hardware."""
        out: dict[str, list[str]] = {}
        for f in fasteners:
            if f.id not in built:
                continue
            pj = [j for j in f.joins if j in part_ids]
            if pj and all(j not in placed_parts for j in pj):
                out.setdefault(pj[0], []).append(f.id)
        return out

    def run(st: Step, parts: list[str]):
        """The step with its parts in this order: its items, and what it leaves placed."""
        placed = list(w.placed)
        done = set(built)
        placed_parts = {k for k in done if k in part_ids}
        rid = floating(placed_parts)
        rider_ids = {f for v in rid.values() for f in v}
        out, bad = [], []
        for it in order(st, fasts, {k for k in done if k not in rider_ids}, parts, rid):
            ids = it["ids"]
            obstacles = [k for k in placed if k not in rider_ids]
            if it["kind"] == "part":
                carry = {pid: rid.get(pid, []) for pid in ids}
                paths, ok, what = _part_paths(asm, w, ids, done, fasteners, obstacles, carry)
            else:
                paths, ok, what = _fastener_path(w, fasts[ids[0]], obstacles, parts_by)
            out.append({"ids": ids, "kind": it["kind"], "paths": {k: [[round(float(x), 2) for x in p] for p in v] for k, v in paths.items()},
                        "clean": ok, **({"hits": sorted(what)} if not ok else {})})
            if not ok:
                bad.append({"step": st.id, "ids": ids, "hits": sorted(what)})
            for k in ids:
                if k in w.mesh:
                    placed.append(k)
                done.add(k)
            if it["kind"] == "part":
                for pid in ids:
                    rider_ids -= set(rid.get(pid, []))
        return out, bad, placed, done

    for st in steps:
        out, bad, placed, done = run(st, list(st.parts))
        # an item that crosses a part of its own step goes in before that part instead (a bearing into its
        # bracket before the bracket goes on), when that clears it
        for b in list(bad):
            mine = [p for p in b["ids"] if p in st.parts]
            hit = [h for h in b["hits"] if h in st.parts]
            if not mine or not hit:
                continue
            order_ = [p for p in st.parts if p not in mine]
            k = min(order_.index(h) for h in hit if h in order_)
            order_[k:k] = mine
            out2, bad2, placed2, done2 = run(st, order_)
            if len(bad2) < len(bad):
                out, bad, placed, done = out2, bad2, placed2, done2
        seqs[st.id] = out
        unclean += bad
        w.placed = placed
        built.clear()
        built.update(done)
    return {"seq": seqs, "unclean": unclean}


def _part_paths(asm, w: _World, ids: list[str], built: set[str], fasteners, placed, carry=None):
    """Each of the item's parts (a like pair moves together, each along its own way out), with the hardware
    it already carries."""
    carry = carry or {}
    out, all_ok, what_all = {}, True, set()
    for pid in ids:
        riding = [pid] + [f for f in carry.get(pid, []) if f in w.mesh]
        if pid not in w.mesh:
            out[pid] = [[0.0, 50.0, 0.0]]
            continue
        ax = _part_axis(asm, pid, built, fasteners)
        L = min(120.0, max(50.0, _size(w, pid) * 0.7)) * PART_PULL
        c = w.box[pid].mean(axis=0)
        bb = [w.box[k] for k in placed if k in w.box]
        away = np.zeros(3)
        if bb:
            lo = np.min([b[0] for b in bb], axis=0)
            hi = np.max([b[1] for b in bb], axis=0)
            away = c - np.clip(c, lo, hi)
            if np.linalg.norm(away) < 1e-3:
                away = c - (lo + hi) / 2
        cands: list[tuple[list[np.ndarray], np.ndarray | None]] = []
        dirs = []
        if ax is not None:
            s = 1.0 if ax @ away >= 0 else -1.0
            dirs += [ax * s, -ax * s]
        if np.linalg.norm(away) > 1e-3:
            dirs.append(_unit(away))
        dirs += [np.array(v, float) for v in ([0, 1, 0], [0, -1, 0], [1, 0, 0], [-1, 0, 0], [0, 0, 1], [0, 0, -1])]
        for d in dirs[:2] if ax is not None else []:
            for k in (1.0, 1.6, 2.4):
                cands.append(([d * L * k], ax))
            # two legs: in level from the side, then along the axis
            perp = [v for v in ([1, 0, 0], [0, 0, 1], [0, 1, 0]) if abs(np.dot(v, d)) < 0.5]
            for p in perp:
                for sg in (1, -1):
                    side = _unit(np.asarray(p, float) - d * (np.dot(p, d))) * sg
                    cands.append(([d * L + side * max(L, _size(w, pid) * 0.7), d * L], ax))
        for d in dirs[(2 if ax is not None else 0):]:
            for k in (1.0, 1.6, 2.4, 3.5):
                cands.append(([d * L * k], ax))
        # last: two legs on the principal directions (round something, then in)
        prin = [np.array(v, float) for v in ([0, 1, 0], [0, -1, 0], [1, 0, 0], [-1, 0, 0], [0, 0, 1], [0, 0, -1])]
        for d in prin:
            for e in prin:
                if abs(d @ e) < 0.5:
                    cands.append(([d * L * 1.6 + e * L * 1.6, d * L * 1.6], None))
        best = None
        for path, axis in cands:
            start = [h for k in riding for h in w.hits(k, path[0], placed)]
            if start:
                score = 10_000 + len(start)
                if best is None or score < best[0]:
                    best = (score, path, set(start))
                continue
            bad, what = _sweep(w, riding, path, axis, placed)
            if bad == 0:
                best = (0, path, set())
                break
            if best is None or bad < best[0]:
                best = (bad, path, what)
        out[pid] = best[1]
        if best[0]:
            all_ok = False
            what_all |= best[2]
    return out, all_ok, what_all


def _fastener_path(w: _World, f: Fastener, placed, parts_by):
    """Along its own axis: from its side, far enough out to start clear (outside the part it goes into
    for an insert or a nut in a trap)."""
    if f.matrix is None or f.id not in w.mesh:
        return {f.id: [[0.0, 0.0, 0.0]]}, True, set()
    mm, side = _travel(f.spec)
    z = _unit(f.matrix[:3, 2])
    out_dir = -z * side  # where it comes from
    kind = _kind(f.spec)
    best = None
    for k in range(0, 12):
        L = mm + k * 6.0
        start = w.hits(f.id, out_dir * L, placed)
        # an insert or a nut starts outside what it goes into, so it is seen coming in
        inside = kind == "nut" and any(_inside(w, f.id, out_dir * L, j) for j in f.joins if j in w.box)
        if start or inside:
            continue
        bad, what = _sweep(w, [f.id], [out_dir * L], z, placed)
        if bad == 0:
            return {f.id: [out_dir * L]}, True, set()
        if best is None or bad < best[0]:
            best = (bad, out_dir * L, what)
        if kind != "nut":
            break
    # a washer or a nut that goes into a gap (a spacer between a bearing and the cross): in from the side
    if kind in ("washer", "nut", "other"):
        x = _unit(f.matrix[:3, 0])
        y = _unit(f.matrix[:3, 1])
        for d in (x, -x, y, -y, (x + y) / math.sqrt(2), -(x + y) / math.sqrt(2), (x - y) / math.sqrt(2), -(x - y) / math.sqrt(2)):
            for L in (mm, mm * 1.8):
                if w.hits(f.id, d * L, placed):
                    continue
                bad, what = _sweep(w, [f.id], [d * L], None, placed)
                if bad == 0:
                    return {f.id: [d * L]}, True, set()
    if best is None:
        bad, what = _sweep(w, [f.id], [out_dir * mm], z, placed)
        return {f.id: [out_dir * mm]}, bad == 0, what
    return {f.id: [best[1]]}, False, best[2]


def _shared(w: _World, k: str, off: np.ndarray, b: str) -> float:
    lo = np.maximum(w.box[k][0] + off, w.box[b][0])
    hi = np.minimum(w.box[k][1] + off, w.box[b][1])
    return float(np.prod(np.maximum(hi - lo, 0)))


def _inside(w: _World, k: str, off: np.ndarray, part: str) -> bool:
    lo, hi = w.box[k][0] + off, w.box[k][1] + off
    blo, bhi = w.box[part]
    return bool(np.all(hi > blo) and np.all(lo < bhi))
