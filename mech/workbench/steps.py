"""Build steps for every assembly, as the manifest carries them (SCHEMA.md "Step").

An assembly's own `steps` are kept, in order; every part and fastener they leave out is grouped
into derived steps (`derived: true`) the way a builder would put them on:

- groups: a family of like parts (a left/right pair, four posts, nine panel inserts), a servo with
  its gear, horn and the mount it sits in (the assembly's gears and linkages say which), and small
  parts with the bigger part they sit on (they touch: FCL distance, cached by mesh) - never one
  giant step, never one step per screw;
- order: the fixed frame first, then outward through what touches what is already built, each
  moving group after the link it hangs on (the joint tree), bottom up; in an assembly that has
  steps, a derived group goes in right after the step that placed what it touches;
- fasteners go in with the step that places the last part they join.

Every part (not replaced) and fastener ends up in exactly one step's `parts` / `fasteners`: a part
an authored step names again (the kit's guide names a part in each step that works on it) moves
to that step's `context`. A step is titled by what it adds ("Pan servo + 15T pan pinion"); an
authored step keeps its title unless it is a placeholder ("Step 3") or the head of its note.

Pure function of the Assembly: the suite's own `asm.steps` are not changed.
"""

from __future__ import annotations

import re
from dataclasses import replace

import numpy as np

from .model import Assembly, Step

#: Parts closer than this (mm) touch (a servo in its pocket has a running clearance).
CONTACT_MM = 2.0
#: A servo sits in the mount nearest it within this (mm); a part touching nothing goes in next to what it is nearest.
NEAR_MM = 8.0
#: A group stops growing at this many kinds of part (a family counts once).
MAX_KINDS = 4
#: A part at least this big (bounding-box diagonal, mm) is structure: it never rides another part.
STRUCTURE_MM = 160.0

_SIDE = re.compile(r"_(\d+|l|r|m|left|right|full)$")
_CODE = re.compile(r"^[A-Z0-9]+(_[A-Za-z0-9]+)+$")
_MOUNT = re.compile(r"mount|bracket|cradle|hanger|holder", re.I)


def stem(pid: str) -> str:
    """A part id without its index or side: morton_post_low_3 -> morton_post_low, visor_bearing_l -> visor_bearing."""
    s = pid.lower()
    while True:
        t = _SIDE.sub("", s)
        if t == s or not t:
            return s
        s = t


def label(p) -> str:
    """A part as a step title names it: a kit code by its stem (MS_LPI), a servo by its role (Pan servo),
    anything else by its name's description, without asides."""
    n = re.sub(r"\s+-\s*x\d+.*$|\s*#\d+$", "", p.name.strip())  # the kit's "LS_V_1 - x4 #2"
    if _CODE.match(n):
        code = n
        while True:
            t = re.sub(r"_(\d+|Full)$", "", code)
            if t == code or not t:
                return code
            code = t
    if p.cls == "servo":
        s = stem(p.id).replace("_", " ")
        return s[:1].upper() + s[1:]
    if ":" in n:
        # "base-center: turntable + lift tower" -> the description; "top-ring-servo-gear: 20T" -> the file's name
        head, desc = (x.strip() for x in n.split(":", 1))
        n = desc if len(re.sub(r"\s*\([^)]*\)", "", desc)) >= 8 else head
    n = re.sub(r"\s*\([^)]*\)", "", n).split(",")[0].strip() or p.name
    if re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)+", n):  # a file's slug: neck-rotation-servo-mount
        n = n.replace("-", " ")
    return n[:1].upper() + n[1:]


def _size(m) -> float:
    lo, hi = m.bounds
    return float(np.linalg.norm(hi - lo))


def contacts(asm: Assembly, ids: list[str]) -> dict[frozenset, float]:
    """Pairs of these parts within NEAR_MM of each other at the zero pose, and how far apart (0: touching)."""
    from . import geom
    from .collide import Scene, mesh_hash

    parts = {p.id: p for p in asm.parts}
    meshes = {i: parts[i].mesh for i in ids if parts[i].mesh is not None and len(parts[i].mesh.faces)}
    key = geom._cache_key("step-contacts-v2", NEAR_MM, sorted((i, mesh_hash(m)) for i, m in meshes.items()))

    def run():
        sc = Scene(meshes)
        eye = np.eye(4)
        boxes = sc.aabbs({k: eye for k in sc.ids})
        out = []
        n = len(sc.ids)
        for a in range(n):
            for b in range(a + 1, n):
                if sc.gaps(boxes, np.array([a]), np.array([b]))[0] > NEAR_MM:
                    continue
                d = sc.distance(sc.ids[a], eye, sc.ids[b], eye)
                if d <= NEAR_MM:
                    out.append((sc.ids[a], sc.ids[b], d))
        return out

    return {frozenset((a, b)): d for a, b, d in geom.cached(key, run)}


def _link_depth(asm: Assembly) -> dict[str, int]:
    depth: dict[str, int] = {}
    child = {j.child_link: j.parent_link for j in asm.joints if j.type != "fixed"}

    def d(link: str, seen=()) -> int:
        if link in depth:
            return depth[link]
        p = child.get(link)
        v = 0 if p is None or p in seen else d(p, seen + (link,)) + 1
        depth[link] = v
        return v

    for l in asm.links:
        d(l.id)
    return depth


class _Groups:
    """Union-find over part ids."""

    def __init__(self, ids):
        self.up = {i: i for i in ids}

    def find(self, i):
        while self.up[i] != i:
            self.up[i] = self.up[self.up[i]]
            i = self.up[i]
        return i

    def join(self, a, b):
        a, b = self.find(a), self.find(b)
        if a != b:
            self.up[b] = a
        return a

    def sets(self):
        out: dict[str, list[str]] = {}
        for i in self.up:
            out.setdefault(self.find(i), []).append(i)
        return list(out.values())


def _kinds(ids, parts) -> int:
    return len({(stem(i), parts[i].link) for i in ids})


def group(asm: Assembly, ids: list[str], near: dict[frozenset, float]) -> list[list[str]]:
    """The builder's groups of these (unstepped) parts."""
    parts = {p.id: p for p in asm.parts}
    touch = {k for k, d in near.items() if d <= CONTACT_MM}
    size = {i: _size(parts[i].mesh) if parts[i].mesh is not None else 0.0 for i in ids}
    g = _Groups(ids)
    have = set(ids)
    # families: like parts on one link (a left/right pair, a row of posts)
    fam: dict[tuple, str] = {}
    for i in ids:
        k = (stem(i), parts[i].link)
        if k in fam:
            g.join(fam[k], i)
        else:
            fam[k] = i
    # drives: a gear's parts with its servo, a linkage's with its servo, the mount the servo sits in
    for gr in asm.gears:
        # the servo's side only (a pinion and its servo, not the sector it meshes on another link)
        link = parts[gr.servo].link if gr.servo in parts else gr.link
        mem = [x for x in [*gr.parts, gr.servo] if x and x in have and parts[x].link == link]
        for x in mem[1:]:
            g.join(mem[0], x)
    for lk in asm.linkages:
        mem = [x for x in [*lk.parts, lk.servo] if x and x in have]
        for x in mem[1:]:
            g.join(mem[0], x)
    for i in ids:
        if parts[i].cls != "servo":
            continue
        mounts = sorted((near[frozenset((i, j))], j) for j in ids
                        if j != i and _MOUNT.search(f"{j} {parts[j].name}") and frozenset((i, j)) in near and size[j] < STRUCTURE_MM * 1.5)
        if mounts:
            g.join(mounts[0][1], i)
    # small parts ride the smallest bigger group they touch (a horn on its servo, a cap on its post);
    # structure never rides, and a group that has taken riders does not ride in turn
    hosts: set[str] = set()
    sets = sorted(g.sets(), key=lambda s: max(size[x] for x in s))
    for s in sets:
        r = g.find(s[0])
        mine = [x for x in ids if g.find(x) == r]
        big = max(size[x] for x in mine)
        if big >= STRUCTURE_MM or r in hosts:
            continue
        best = None
        for t in {g.find(x) for x in ids} - {r}:
            other = [x for x in ids if g.find(x) == t]
            ob = max(size[x] for x in other)
            if ob < 1.15 * big or parts[other[0]].link != parts[mine[0]].link \
                    or not any(frozenset((a, b)) in touch for a in mine for b in other):
                continue
            if _kinds(mine + other, parts) > MAX_KINDS:
                continue
            if best is None or ob < best[0]:
                best = (ob, t)
        if best:
            hosts.add(g.join(best[1], r))
    return [sub for s in g.sets() for sub in _split(s, parts)]


#: A step adds at most this many parts; a bigger group splits into its kinds, then rows.
MAX_PARTS = 6


def _split(ids: list[str], parts: dict) -> list[list[str]]:
    """A group too big to take in at once: one step per kind of part (a symmetric pair, a row of like parts);
    a kind still too big by its rows (the kit's MS_LPI_L/_M/_R panel inserts)."""
    if len(ids) <= MAX_PARTS:
        return [ids]
    by: dict[tuple, list[str]] = {}
    for i in ids:
        by.setdefault((stem(i), parts[i].link), []).append(i)
    out: list[list[str]] = []
    for fam in by.values():
        if len(fam) <= MAX_PARTS:
            out.append(fam)
            continue
        rows: dict[str, list[str]] = {}
        for i in fam:
            rows.setdefault(re.sub(r"_\d+$", "", i.lower()), []).append(i)
        for row in rows.values():
            out.extend(row[k:k + MAX_PARTS] for k in range(0, len(row), MAX_PARTS))
    return out


def title(ids: list[str], parts: dict, extra: str = "") -> str:
    """"Pan servo mount + Pan servo + 15T pan pinion", "2020 extrusion 121 mm ×4"."""
    by: dict[str, list[str]] = {}
    for i in sorted(ids, key=lambda x: -_size(parts[x].mesh) if parts[x].mesh is not None else 0):
        by.setdefault(label(parts[i]), []).append(i)
    bits = [f"{k} ×{len(v)}" if len(v) > 1 else k for k, v in by.items()]
    t = " + ".join(bits[:3]) + (f" + {len(bits) - 3} more" if len(bits) > 3 else "")
    if extra:
        t = f"{t} + {extra}" if t else extra[:1].upper() + extra[1:]
    return t


def _hardware_word(unplaced: list) -> str:
    """"12 inserts", "8 screws": what an authored step's listed hardware is, in one phrase."""
    count: dict[str, int | None] = {}
    for u in unplaced:
        s = f"{(u.get('spec') or {}).get('type', '')} {(u.get('spec') or {}).get('desc', '')} {u.get('key', '')}".lower()
        noun = next((w for k, w in [("insert", "inserts"), ("magnet", "magnets"), ("standoff", "standoffs"), ("washer", "washers"),
                                     ("nut", "nuts"), ("screw", "screws"), ("bolt", "bolts"), ("pin", "pins")] if k in s), "hardware")
        n = u.get("count")
        count[noun] = (count[noun] or 0) + n if noun in count and count[noun] is not None and n else (n if noun not in count else None)
    return " + ".join(f"{n} {w}" if n else w for w, n in list(count.items())[:2])


def _generic(s: Step) -> bool:
    """A title that is not a name: a placeholder ("Step 3") or the step's note itself."""
    t = (s.title or "").strip()
    return not t or bool(re.fullmatch(r"Step \d+", t)) or bool(s.text and (s.title or "") == s.text[:80])


def plan(asm: Assembly) -> list[Step]:
    """The assembly's steps: its own, cleaned, with every part and fastener they leave out grouped in."""
    parts = {p.id: p for p in asm.parts}
    live = [p.id for p in asm.parts if not p.replaced_by]
    fast_ids = [f.id for f in asm.fasteners]
    placed: set[str] = set()
    used_f: set[str] = set()
    steps: list[Step] = []
    for s in asm.steps:
        new = [p for p in s.parts if p in parts and p not in placed and not parts[p].replaced_by]
        again = [p for p in s.parts if p in placed]
        fs = [f for f in s.fasteners if f in fast_ids and f not in used_f]
        if not new and not again and not fs and (s.parts or not (s.unplaced or s.context)):
            continue  # about parts our build replaced (the kit's hero arm under Anderson's), or about nothing
        placed.update(new)
        used_f.update(fs)
        ctx = list(dict.fromkeys([*s.context, *again]))
        t = (title(new or again, parts, _hardware_word(s.unplaced)) or s.title) if _generic(s) else s.title
        steps.append(replace(s, parts=new, fasteners=fs, context=ctx, title=t))
    left = [i for i in live if i not in placed]
    if left:
        near = contacts(asm, live)
        groups = group(asm, left, near)
        steps = _insert(asm, steps, groups, near, placed)
    # fasteners nobody names: with the step that places the last part they join (else the last step)
    where = {p: k for k, s in enumerate(steps) for p in s.parts}
    for f in asm.fasteners:
        if f.id in used_f:
            continue
        at = max((where[j] for j in f.joins if j in where), default=len(steps) - 1)
        if at < 0:
            steps.append(Step("d_hardware", "Hardware", derived=True))
            at = 0
        steps[at].fasteners.append(f.id)
        used_f.add(f.id)
    return steps


def _insert(asm: Assembly, steps: list[Step], groups: list[list[str]], near: dict[frozenset, float], placed: set[str]) -> list[Step]:
    """Order the derived groups (frame first, outward through contact, parents before what hangs on
    them, bottom up) and put each after the authored step that placed what it touches."""
    parts = {p.id: p for p in asm.parts}
    touch = {k for k, d in near.items() if d <= CONTACT_MM}
    depth = _link_depth(asm)
    built = set(placed)
    where = {p: k for k, s in enumerate(steps) for p in s.parts}

    def info(gr):
        lo = min(float(parts[i].mesh.bounds[0][1]) for i in gr if parts[i].mesh is not None) if gr else 0.0
        return (min(depth.get(parts[i].link, 0) for i in gr), round(lo / 40.0), -max(_size(parts[i].mesh) for i in gr))

    def touches(gr, have):
        return any(frozenset((a, b)) in touch for a in gr for b in have)

    todo = list(groups)
    order: list[list[str]] = []
    while todo:
        # the link nearest the frame first (a moving group after the link it hangs on); in it, what touches what
        # was just put on, else what touches anything built, else the next by height
        d0 = min(info(gr)[0] for gr in todo)
        here = [gr for gr in todo if info(gr)[0] == d0]
        same = bool(order) and info(order[-1])[0] == d0  # entering a new link: its lowest, biggest group first
        # (a new link starts at what carries it: touching the built frame, not through a cosmetic shell)
        solid = {b for b in built if parts[b].cls != "shell"}
        cand = ([gr for gr in here if touches(gr, order[-1])] or [gr for gr in here if built and touches(gr, built)]) if same \
            else [gr for gr in here if touches(gr, solid)]
        pick = min(cand or here, key=info)
        if not built and not cand:  # the first: the biggest of the frame
            frame = [gr for gr in todo if info(gr)[0] == min(info(x)[0] for x in todo)]
            pick = min(frame, key=lambda gr: info(gr)[2])
        todo.remove(pick)
        order.append(pick)
        built.update(pick)
    out = list(steps)
    after: dict[int, list[list[str]]] = {}
    # each group after the authored step that placed what it touches (or, through other groups, is near);
    # repeated until nothing more anchors, so a group touching only a later-anchored group follows it there
    left = list(order)
    while True:
        moved = False
        for gr in list(left):
            hosts = [where[b] for a in gr for b in where if frozenset((a, b)) in touch] \
                or [where[b] for a in gr for b in where if frozenset((a, b)) in near]
            if hosts:
                k = max(hosts)
                after.setdefault(k, []).append(gr)
                where.update({p: k for p in gr})
                left.remove(gr)
                moved = True
        if not moved:
            break
    tail = left
    rank = {id(gr): i for i, gr in enumerate(order)}
    for k in after:
        after[k].sort(key=lambda gr: rank[id(gr)])
    res: list[Step] = []
    n = 0

    def emit(gr):
        nonlocal n
        n += 1
        return Step(f"d{n:02d}", title(gr, parts), parts=sorted(gr, key=live_order(asm)), derived=True)

    for k, s in enumerate(out):
        res.append(s)
        for gr in after.get(k, []):
            res.append(emit(gr))
    for gr in tail:
        res.append(emit(gr))
    return res


def live_order(asm: Assembly):
    idx = {p.id: k for k, p in enumerate(asm.parts)}
    return lambda i: idx.get(i, 0)
