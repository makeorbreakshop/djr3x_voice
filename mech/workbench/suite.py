"""The assembly test suite (TESTS.md): does it connect, hold, fit, move and print?

    cd mech && .venv/bin/python -m workbench test hunter_head [--json out.json]

Every result is a Check (kind "test") that names the parts and features and carries a pose that
shows it; they land in the manifest's checks, so Build's Checks tab lists them. A failure an
assembly module has explained (`EXPLAINED` in the module: cause + proposed fix) is reported as
`explained` instead of `fail`, never hidden.
"""

from __future__ import annotations

import itertools
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import trimesh
from scipy.spatial import cKDTree

from .geom import sample
from .kinematics import link_matrices, servo_jacobian, solve_linkages, apply, horn_basis
from .mates import moved, residual, unit
from .model import Assembly, Check

REPO = Path(__file__).resolve().parents[2]

TOL = {
    "axis_offset_mm": 0.3,      # concentric: axis-to-axis distance
    "axis_angle_deg": 1.0,
    "seat_gap_mm": 0.3,         # seated faces
    "contact_gap_mm": 0.5,      # glued / pressed contacts must touch
    "ball_offset_mm": 0.2,
    "press_mm": 0.45,           # mated parts may overlap this deep: press fits, and tapped holes modelled at
                                # the thread's minor diameter (M4: 3.24 mm, 0.38 mm under a nominal shank)
    "overlap_mm": 0.1,          # unmated parts: any penetration deeper than this fails
    "clearance_mm": 1.0,        # print tolerance: moving gaps must stay at least this
    "engage_insert_d": 1.5,     # thread engagement, x nominal diameter
    "engage_plastic_d": 2.0,
    "engage_metal_d": 1.0,
    "engage_nut_d": 1.05,       # through a lock nut: every thread plus the nylon ring
    "tool_reach_mm": 60.0,      # straight hex-key access out of every screw head
    "ball_link_deg": 25.0,      # ball-link swivel limit (goBILDA 2913: not published, inferred)
    "servo_travel_deg": 135.0,  # +/- from centre (goBILDA 2000 standard mode: 300 deg)
    "leverage_min": 0.25,       # servo deg per joint deg, the weakest rod
    "wall_min_mm": 0.8,         # 2 perimeters at 0.4 mm
    "bed_mm": (325.0, 320.0, 325.0),  # Bambu H2D, both nozzles (until Brandon confirms)
    "grid_deg": 5.0,
}


@dataclass
class Body:
    id: str
    link: str
    mesh: trimesh.Trimesh  # assembly frame, zero pose
    kind: str  # part class or "fastener"
    linkage: str | None = None
    role: str | None = None


def T(id_, status, title, summary, **k):
    return Check(id_, "test", status, title, summary, **k)


class Suite:
    def __init__(self, asm: Assembly, tol: dict | None = None, explained: dict | None = None, full: bool = False):
        self.asm = asm
        self.full = full  # --full: 1 deg sweeps, 5 deg grids; default: 5 deg sweeps, 10 deg grids
        self.tol = {**TOL, **(asm.tolerances or {}), **(tol or {})}
        self.explained = list(explained or [])
        self.bodies: dict[str, Body] = {}
        for p in asm.parts:
            self.bodies[p.id] = Body(p.id, p.link, _check_mesh(p.mesh), p.cls, p.linkage, p.role)
        for f in asm.fasteners:
            if f.matrix is None:
                continue
            from .geom import fastener_mesh

            m = (f.mesh if f.mesh is not None else fastener_mesh(f.spec)).copy()
            m.apply_transform(f.matrix)
            self.bodies[f.id] = Body(f.id, f.link, m, "fastener", f.linkage, f.role)
        from .collide import DiskCache, Scene

        self.scene = Scene({k: b.mesh for k, b in self.bodies.items()})
        self.cache = DiskCache("suite")
        self.timing: dict[str, float] = {}
        self.features = {p.id: p.features for p in asm.parts}
        self.features.update({f.id: f.features for f in asm.fasteners})
        self.mated = {frozenset((m.a[0], m.b[0])) for m in asm.mates}
        self._pts: dict[str, np.ndarray] = {}
        self._trees: dict[str, cKDTree] = {}
        self.results: list[Check] = []

    # ------------------------------------------------------------------ helpers
    def pts(self, bid, spacing=1.2):
        if bid not in self._pts:
            self._pts[bid] = sample(self.bodies[bid].mesh, spacing, cap=15000)
        return self._pts[bid]

    def tree(self, bid):
        if bid not in self._trees:
            self._trees[bid] = cKDTree(self.pts(bid))
        return self._trees[bid]

    def body_matrix(self, b: Body, ms, sol):
        """Where a body is at a pose: its link, or its linkage (horn turns, rod follows)."""
        if not b.linkage:
            return ms[b.link]
        lk = next(l for l in self.asm.linkages if l.id == b.linkage)
        s = sol.get(lk.id)
        if s is None:
            return ms[b.link]
        ang, a, bb = s
        if b.role == "horn":
            n, _, _ = horn_basis(lk)
            c = np.asarray(lk.centre)
            from .kinematics import rot, trans

            return ms[lk.horn_link] @ trans(c) @ rot(n, ang) @ trans(-c)
        if not hasattr(self, "_zero_sol"):
            self._zero_sol = solve_linkages(self.asm, {})
        z = self._zero_sol[lk.id]
        a0, b0 = z[1], z[2]
        return _rod_matrix(a0, b0, a, bb)

    def add(self, id_, title, status, summary, parts=(), pose=None, joint=None, value=None, assumptions=()):
        self.results.append(T(f"test_{id_}", status, title, summary, joint=joint, value=value,
                              pose=pose or {}, parts=list(parts), assumptions=list(assumptions)))

    def split(self, test_id, items):
        """Failures an assembly module has explained (EXPLAINED: test, part patterns, cause, fix)
        are separated from the rest. items: [(parts, text)]. -> (open items, explanations used)."""
        from fnmatch import fnmatch

        open_, used = [], []
        for parts, text in items:
            hit = next((e for e in self.explained if e["test"] in (test_id, "*") and
                        all(any(fnmatch(p, pat) for pat in e["parts"]) for p in parts)), None)
            if hit is None:
                open_.append((parts, text))
            elif hit not in used:
                used.append(hit)
        return open_, used

    def verdict(self, id_, title, items, ok_text, pose=None, assumptions=(), limit=8):
        open_, used = self.split(id_, items)
        status = "fail" if open_ else "explained" if used else "pass"
        text = "; ".join(t for _, t in open_[:limit]) + (f" (+{len(open_) - limit} more)" if len(open_) > limit else "")
        if used:
            text += (" | " if text else "") + "explained: " + "; ".join(
                f"{e['cause']} -> fix: {e['fix']}" for e in used)
        parts = sorted({p for ps, _ in (open_ or items)[:limit] for p in ps})
        self.add(id_, title, status, text or ok_text, parts=parts, pose=pose, assumptions=assumptions)

    # ------------------------------------------------------------------ 1 connected
    def connected(self):
        g = defaultdict(set)
        for m in self.asm.mates:
            g[m.a[0]].add(m.b[0])
            g[m.b[0]].add(m.a[0])
        ids = [p.id for p in self.asm.parts] + [f.id for f in self.asm.fasteners if f.matrix is not None]
        root = next(p.id for p in self.asm.parts if not next(l for l in self.asm.links if l.id == p.link).joint)
        seen, stack = {root}, [root]
        while stack:
            for n in g[stack.pop()]:
                if n not in seen:
                    seen.add(n)
                    stack.append(n)
        floating = [i for i in ids if i not in seen]
        self.add("connected", "Every part reaches the root through mates", "fail" if floating else "pass",
                 f"{len(ids) - len(floating)} of {len(ids)} connected to {root}" +
                 (f"; floating: {', '.join(floating[:12])}{' …' if len(floating) > 12 else ''}" if floating else ""),
                 parts=floating[:20], assumptions=["joints connect links only through the bearing/pivot mates"])
        # every placed fastener in a mate, BOM counts = placed + unplaced
        unmated = [f.id for f in self.asm.fasteners if f.matrix is not None and f.id not in g]
        bom = {b.key: b.qty for b in self.asm.bom if b.category == "fastener"}
        have = defaultdict(float)
        for f in self.asm.fasteners:
            have[f.key] += 1
        for s in self.asm.steps:
            for u in s.unplaced:
                have[u["key"]] += u.get("count", 0)
        bad = [f"{k}: BOM {bom.get(k, 0):g}, model {have.get(k, 0):g}" for k in sorted(set(bom) | set(have))
               if abs(bom.get(k, 0) - have.get(k, 0)) > 1e-6]
        self.add("fasteners_used", "Every BOM fastener is used where the BOM says", "fail" if unmated or bad else "pass",
                 ("; ".join(bad) + ("; " if bad and unmated else "") +
                  (f"placed with no mate: {', '.join(unmated)}" if unmated else "")) or f"{len(bom)} fastener lines match",
                 parts=unmated)

    # ------------------------------------------------------------------ 2 mates hold
    def mates_hold(self):
        bad = []
        for m in self.asm.mates:
            fa = self.features[m.a[0]][m.a[1]]
            fb = self.features[m.b[0]][m.b[1]]
            r = residual(m, fa, fb)
            why = []
            if r.get("angle_deg", 0) > self.tol["axis_angle_deg"]:
                why.append(f"{r['angle_deg']:.2f} deg")
            if m.type in ("concentric", "threaded", "spline") and r.get("offset_mm", 0) > self.tol["axis_offset_mm"]:
                why.append(f"axes {r['offset_mm']:.2f} mm apart")
            if m.type in ("seated", "coplanar") and abs(r.get("gap_mm", 0)) > self.tol["seat_gap_mm"]:
                why.append(f"gap {r['gap_mm']:+.2f} mm")
            if m.type in ("glue", "press") and m.params.get("gap_mm", 0) > self.tol["contact_gap_mm"]:
                why.append(f"not touching: {m.params['gap_mm']:.1f} mm apart")
            if m.type == "ball_link" and r.get("offset_mm", 0) > self.tol["ball_offset_mm"]:
                why.append(f"ball off by {r['offset_mm']:.2f} mm")
            if m.type == "spline" and "teeth" in r and r["teeth"][0] != r["teeth"][1]:
                why.append(f"teeth {r['teeth'][0]} vs {r['teeth'][1]}")
            if why:
                bad.append((m, why))
        self.verdict("mates_hold", "Mates hold (axes, seats, splines, balls)",
                     [((m.a[0], m.b[0]), f"{m.id} ({m.a[0]}.{m.a[1]} / {m.b[0]}.{m.b[1]}): {', '.join(w)}") for m, w in bad],
                     f"{len(self.asm.mates)} mates within tolerance",
                     assumptions=[f"concentric <= {self.tol['axis_offset_mm']} mm / {self.tol['axis_angle_deg']} deg; "
                              f"seated gap <= {self.tol['seat_gap_mm']} mm; ball <= {self.tol['ball_offset_mm']} mm"])

    # ------------------------------------------------------------------ 3 fasteners are real
    def fasteners_real(self):
        bad, tool = [], []
        steps = [s.id for s in self.asm.steps]
        first = {}
        for i, s in enumerate(self.asm.steps):
            for p in s.parts:
                first.setdefault(p, i)
        for m in self.asm.mates:
            if m.type != "threaded":
                continue
            f = next((x for x in self.asm.fasteners if x.id == m.a[0]), None)
            if f is None:
                continue
            d = float(f.spec["thread"].lstrip("M#").split("-")[0]) if f.spec["thread"].startswith("M") else 3.5
            into = m.params.get("into", "plastic")
            need = self.tol[f"engage_{into}_d"] * d
            got = m.params.get("engage_mm", 0.0)
            if got + 0.05 < need:
                bad.append(((f.id, m.b[0]), f"{f.id} into {m.b[0]}: {got:.1f} mm engaged, needs {need:.1f} ({into})"))
            depth = m.params.get("hole_depth_mm")
            if depth is not None and got > depth + 0.25:
                bad.append(((f.id, m.b[0]), f"{f.id}: tip bottoms out ({got:.1f} mm into a {depth:.1f} mm hole)"))
        for m in self.asm.mates:
            if m.type != "concentric":
                continue
            f = next((x for x in self.asm.fasteners if x.id == m.a[0]), None)
            if f is None or f.spec.get("type") not in ("shcs", "bhcs", "fhcs", "shcs_low", "pan", "truss"):
                continue
            hole = self.features[m.b[0]][m.b[1]]
            d = _nominal(f.spec["thread"])
            if hole.get("r", 99) * 2 + 1e-6 < d:
                bad.append(((f.id, m.b[0]), f"{f.id}: {d:.1f} mm shank in a {hole['r'] * 2:.1f} mm hole of {m.b[0]}"))
        # tool access: from the head straight out, against the parts present at that step
        ms = link_matrices(self.asm, {})
        for f in self.asm.fasteners:
            if f.matrix is None or f.spec.get("type") not in ("shcs", "bhcs", "fhcs", "shcs_low", "pan"):
                continue
            k = steps.index(f.step) if f.step in steps else len(steps)
            present = [b for b in self.bodies.values() if b.kind != "fastener" and first.get(b.id, 0) <= k
                       and b.id not in f.joins]
            p0 = f.matrix[:3, 3]
            out = -f.matrix[:3, 2]
            head_top = p0 + out * (_nominal(f.spec["thread"]) * 1.1)
            seg_lo = np.minimum(head_top, head_top + out * self.tol["tool_reach_mm"]) - 0.5
            seg_hi = np.maximum(head_top, head_top + out * self.tol["tool_reach_mm"]) + 0.5
            for b in present:
                lo, hi = b.mesh.bounds
                if np.any(seg_lo > hi) or np.any(seg_hi < lo):  # the segment's box misses the part's
                    continue
                k = ("ray", self.scene.hash[b.id]) + tuple(np.round(np.concatenate([head_top, out]), 3))
                hits = self.cache.get(k)
                if hits is None:
                    hits = b.mesh.ray.intersects_location([head_top], [out])[0]
                    self.cache.put(k, hits)
                hits = [h for h in hits if 0.3 < (h - head_top) @ out < self.tol["tool_reach_mm"]]
                if hits:
                    tool.append(((f.id, b.id), f"{f.id} blocked by {b.id} at {min((h - head_top) @ out for h in hits):.0f} mm"))
                    break
        self.verdict("fasteners_real", "Screws pass their clearance holes and engage their threads", bad,
                     "every threaded mate engages enough",
                     assumptions=[f"engagement >= {self.tol['engage_insert_d']}d into inserts, "
                              f"{self.tol['engage_plastic_d']}d into plastic, {self.tol['engage_metal_d']}d into metal"])
        self.verdict("tool_access", "A hex key reaches every screw head (at its step)", tool,
                     "every head has straight access",
                     assumptions=[f"a straight line out of the head, {self.tol['tool_reach_mm']:.0f} mm, against the parts "
                              "fitted up to that step (not the key's width)"])

    # ------------------------------------------------------------------ 4 no overlaps at rest
    def inserts(self):
        """The fastening rule (fastening.py): screws into printed parts go into heat-set inserts;
        each insert's hole, depth, boss wall and engagement against its spec; nut joints say why."""
        from . import fastening as FR

        parts = {p.id: p for p in self.asm.parts}
        fast = {f.id: f for f in self.asm.fasteners}
        raw, hole_bad, wall_bad, eng_bad, nut_bad, unknown = [], [], [], [], [], []
        for m in self.asm.mates:
            if m.type != "threaded":
                continue
            host = parts.get(m.b[0])
            if host is not None and host.printed and m.params.get("into") == "plastic":
                raw.append(((m.a[0], host.id), f"{m.a[0]} threads into printed {host.id}.{m.b[1]} (no insert)"))
        for f in self.asm.fasteners:
            if f.spec.get("type") in ("nut", "lock_nut") and not (
                    f.spec.get("reason") in FR.NUT_OK or any(w in (f.inferred_note or "").lower() for w in FR.NUT_OK)):
                nut_bad.append(((f.id,), f"{f.id}: a nut joint with no reason (clamp / pivot / captive)"))
            if f.spec.get("type") != "insert":
                continue
            press = next((m for m in self.asm.mates if m.type == "press" and m.a[0] == f.id), None)
            if press is None or press.b[0] not in parts:
                continue
            host = parts[press.b[0]]
            if not host.printed:
                continue
            spec = FR.spec_of(f.spec) or {}
            hole = host.features.get(press.b[1], {})
            where = f"{host.id}.{press.b[1]}"
            r = float(hole.get("r", 0))
            L = float(spec.get("length_mm") or f.spec.get("length_mm") or 0)
            od = spec.get("od_mm") or f.spec.get("od_mm")
            if spec.get("hole_d") is None:
                unknown.append(((f.id, host.id), f"{where}: {spec.get('key', f.key)} has no datasheet hole size on file"))
            elif abs(2 * r - spec["hole_d"]) > FR.HOLE_TOL_MM:
                hole_bad.append(((f.id, host.id), f"{where}: hole {2 * r:.2f} mm, the insert wants {spec['hole_d']:.2f}"))
            depth, through = self._hole_depth(host, hole, r)
            if through:
                if depth + 0.05 < L:
                    hole_bad.append(((f.id, host.id), f"{where}: through a {depth:.1f} mm wall, the insert is {L:g} long"))
            elif depth + 0.05 < L + FR.DEPTH_EXTRA_MM:
                hole_bad.append(((f.id, host.id), f"{where}: {depth:.1f} mm deep, needs {L + FR.DEPTH_EXTRA_MM:g} "
                                                  f"(insert {L:g} + {FR.DEPTH_EXTRA_MM:g})"))
            w = self._boss_wall(host, hole, r, min(L, depth))
            need = FR.wall_min(od)
            if w is not None and w + 0.05 < need:
                wall_bad.append(((f.id, host.id), f"{where}: {w:.1f} mm of wall round the insert (needs {need:.1f})"))
            for m in self.asm.mates:
                if m.type == "threaded" and m.b[0] == f.id:
                    d = _nominal(fast[m.a[0]].spec["thread"]) if m.a[0] in fast else 4.0
                    need_e = min(L, 1.5 * d)
                    got = float(m.params.get("engage_mm", 0))
                    if got + 0.05 < need_e:
                        eng_bad.append(((m.a[0], f.id, host.id), f"{m.a[0]} in {where}: {got:.1f} mm engaged (needs {need_e:.1f})"))
        items = raw + hole_bad + wall_bad + eng_bad + nut_bad + unknown
        n_ins = sum(1 for f in self.asm.fasteners if f.spec.get("type") == "insert")
        self.verdict("inserts", "Screws into prints go into heat-set inserts that fit", items,
                     f"{n_ins} inserts: holes, depth, walls and engagement to spec; no screw in raw plastic",
                     assumptions=["fastening.py: hole +/-0.15 mm, blind depth >= insert + 1 mm (through: wall >= insert), "
                                  "wall >= max(1.5 mm, 0.5 x OD) to the part's nearest other surface, engagement >= "
                                  "min(insert length, 1.5 d); nut joints carry a reason (clamp / pivot / captive)"])

    def _hole_depth(self, host, hole, r):
        """(depth, through) of a hole feature: the floor along its axis, or the wall it passes through."""
        p, d = np.asarray(hole["p"], float), unit(hole["d"])
        mesh = host.mesh
        hits = mesh.ray.intersects_location([p + d * 0.3], [d])[0]
        ts = sorted(float((h - p) @ d) for h in hits if (h - p) @ d > 0.35)
        if ts and ts[0] < 80:
            return ts[0], False
        side = np.cross(d, [1, 0, 0] if abs(d[0]) < 0.9 else [0, 0, 1])
        side = unit(side) * (r + 0.6)
        q = p + side - d * 1.0
        hits = mesh.ray.intersects_location([q], [d])[0]
        ts = sorted(float((h - q) @ d) for h in hits)
        return (ts[1] - ts[0], True) if len(ts) >= 2 else (float("inf"), True)

    def _boss_wall(self, host, hole, r, depth):
        """The thinnest material round a hole over the insert's depth: the nearest surface of the
        part that is not the hole's own wall, entry face or floor (an edge, another hole)."""
        p, d = np.asarray(hole["p"], float), unit(hole["d"])
        m = host.mesh
        c = m.triangles_center
        rel = c - p
        t = rel @ d
        rho = np.linalg.norm(rel - np.outer(t, d), axis=1)
        near = (t > -1) & (t < depth + 1) & (rho < r + 12)
        if not near.any():
            return None
        sub = m.submesh([np.nonzero(near)[0]], append=True)
        pts, _ = trimesh.sample.sample_surface(sub, max(2000, int(sub.area / 0.04)), seed=3)
        rel = pts - p
        t = rel @ d
        rho = np.linalg.norm(rel - np.outer(t, d), axis=1)
        keep = (t > 0.5) & (t < depth - 0.5) & (rho > r + 0.3)
        if not keep.any():
            return None
        return float(rho[keep].min() - r)

    def overlaps(self):
        """At rest: FCL finds the intersecting pairs (AABB broad phase first); only those get the
        exact penetration depth, cached by the two meshes' hashes."""
        bad = []
        ids = list(self.bodies)
        idx = {k: i for i, k in enumerate(self.scene.ids)}
        eye = np.eye(4)
        boxes = self.scene.aabbs({k: eye for k in self.scene.ids})
        pairs = list(itertools.combinations(ids, 2))
        ia = np.array([idx[a] for a, _ in pairs])
        ib = np.array([idx[b] for _, b in pairs])
        near = self.scene.gaps(boxes, ia, ib) <= 0.0
        for (a, b), n in zip(pairs, near):
            if not n:
                continue
            k = ("pen", self.scene.hash[a], self.scene.hash[b])
            hit = self.cache.get(k)
            if hit is None:
                hit = (None, None)
                if self.scene.collide(a, eye, b, eye):
                    hit = self.penetration(a, b)
                self.cache.put(k, hit)
            depth, where = hit
            if depth is None:
                continue
            limit = self.tol["press_mm"] if frozenset((a, b)) in self.mated else self.tol["overlap_mm"]
            if depth > limit:
                bad.append((depth, a, b, where))
        bad.sort(key=lambda t: -t[0])
        self.verdict("no_overlap", "No overlaps at rest (mated parts only touch at their mate)",
                     [((a, b), f"{a} x {b}: {d:.2f} mm deep at {np.round(w, 1).tolist()}") for d, a, b, w in bad],
                     "no penetration beyond tolerance",
                     assumptions=[f"FCL finds intersecting pairs; depth = deepest point of one closed mesh inside the "
                                  f"other (exact); mated pairs may overlap {self.tol['press_mm']} mm, others "
                                  f"{self.tol['overlap_mm']} mm", "results cached by geometry hash"])

    def penetration(self, a, b):
        """Deepest point of one body inside the other (mm), or None when they do not touch.
        Closed pair: the intersection solid (manifold3d), its vertices' exact distance to the
        surfaces. Otherwise: surface samples of one inside the other."""
        A, B = self.bodies[a].mesh, self.bodies[b].mesh
        if A.is_watertight and B.is_watertight:
            try:
                import manifold3d as mf

                def man(m):
                    return mf.Manifold(mf.Mesh(vert_properties=np.asarray(m.vertices, np.float32),
                                               tri_verts=np.asarray(m.faces, np.uint32)))
                I = man(A) ^ man(B)
                if I.is_empty():
                    return None, None
                mm = I.to_mesh()
                V = np.asarray(mm.vert_properties)[:, :3]
                if len(V) > 600:
                    V = V[np.random.default_rng(0).choice(len(V), 600, replace=False)]
                _, da, _ = trimesh.proximity.closest_point(A, V)
                _, db, _ = trimesh.proximity.closest_point(B, V)
                d = np.maximum(da, db)
                k = int(np.argmax(d))
                return float(d[k]), V[k]
            except Exception:
                pass
        best = None
        for X, Y, yid in ((A, B, b), (B, A, a)):
            if not Y.is_watertight:
                continue
            xid = a if yid == b else b
            P = self.pts(xid)
            lo, hi = Y.bounds
            sel = P[np.all((P >= lo - 0.01) & (P <= hi + 0.01), axis=1)]
            if not len(sel):
                continue
            # only where the other part is near (KD-tree on its samples), at most 1500 points
            dn, _ = self.tree(yid).query(sel, distance_upper_bound=3.0)
            sel = sel[np.isfinite(dn)]
            if len(sel) > 1500:
                sel = sel[np.random.default_rng(1).choice(len(sel), 1500, replace=False)]
            if not len(sel):
                continue
            inside = sel[Y.contains(sel)]
            if not len(inside):
                continue
            if len(inside) > 400:
                inside = inside[np.random.default_rng(0).choice(len(inside), 400, replace=False)]
            _, d, _ = trimesh.proximity.closest_point(Y, inside)  # exact distance to the surface
            k = int(np.argmax(d))
            if best is None or d[k] > best[0]:
                best = (float(d[k]), inside[k])
        return best if best else (None, None)

    # ------------------------------------------------------------------ 5 motion
    def poses(self):
        joints = {j.id: j for j in self.asm.joints}
        g = self.tol["grid_deg"] if self.full else 2 * self.tol["grid_deg"]
        step = 1.0 if self.full else 5.0
        out = []
        for j in joints.values():
            vals = sorted(set(np.round(np.append(np.arange(j.limits[0], j.limits[1] + 1e-9, step), [j.limits[1]]), 3)))
            for v in vals:
                out.append(("sweep", {j.id: float(v)}))
        pair = [j for j in ("head_tilt", "head_roll") if j in joints] or [j.id for j in list(joints.values())[:2]]
        if len(pair) == 2:
            a, b = (joints[x] for x in pair)
            for va in np.arange(a.limits[0], a.limits[1] + 1e-9, g):
                for vb in np.arange(b.limits[0], b.limits[1] + 1e-9, g):
                    out.append(("grid", {a.id: float(va), b.id: float(vb)}))
            for va in (a.limits[0], a.limits[1]):  # the corners always
                for vb in (b.limits[0], b.limits[1]):
                    out.append(("grid", {a.id: float(va), b.id: float(vb)}))
        for cid, pose in _clip_poses({j.profile_joint: j.id for j in joints.values() if j.profile_joint}):
            out.append((f"clip {cid}", pose))
        return out

    def motion(self):
        poses = self.poses()
        reach, travel, swivel, lev = [], [], [], []
        L0 = {lk.id: lk.rod_length for lk in self.asm.linkages}
        drive = {j.id: j.drive.get("linkages", []) for j in self.asm.joints}
        for src, pose in poses:
            sol = solve_linkages(self.asm, pose)
            for lid, s in sol.items():
                if s is None:
                    reach.append((src, pose, lid))
                    continue
                ang, a, b = s
                if abs(np.linalg.norm(a - b) - L0[lid]) > 0.01:
                    reach.append((src, pose, lid))
                if abs(ang) > self.tol["servo_travel_deg"]:
                    travel.append((abs(ang), src, pose, lid))
                sw = self.swivel(lid, pose, a, b)
                if sw > self.tol["ball_link_deg"]:
                    swivel.append((sw, src, pose, lid))
            jids = [j for j in pose if drive.get(j)]
            if jids:
                jac = servo_jacobian(self.asm, pose, jids)
                if jac is not None:
                    gmin = float(np.min(np.max(np.abs(jac), axis=0)))
                    if gmin < self.tol["leverage_min"]:
                        lev.append((gmin, src, pose))
        fmt = lambda p: ", ".join(f"{k} {v:+g}" for k, v in p.items())
        self.add("linkage_closure", "Push rods close at every pose (constant length)", "fail" if reach else "pass",
                 f"{len(reach)} unreachable poses, first: {reach[0][2]} at {fmt(reach[0][1])} ({reach[0][0]})" if reach
                 else f"{len(poses)} poses (sweeps, tilt x roll grid, show clips)", pose=reach[0][1] if reach else None)
        worst = max(travel) if travel else None
        self.add("servo_travel", "Servos stay inside their travel", "fail" if travel else "pass",
                 f"{worst[3]} needs {worst[0]:.0f} deg at {fmt(worst[2])}" if worst else f"within +/-{self.tol['servo_travel_deg']:g} deg",
                 pose=worst[2] if worst else None)
        worst = max(swivel) if swivel else None
        self.add("ball_link_angle", "Rod ends stay inside their swivel", "fail" if swivel else "pass",
                 f"{worst[3]} at {worst[0]:.1f} deg (limit {self.tol['ball_link_deg']:g}) at {fmt(worst[2])} ({worst[1]})"
                 if worst else f"every rod end within {self.tol['ball_link_deg']:g} deg", pose=worst[2] if worst else None,
                 assumptions=["swivel = angle between the rod and the plane square to the ball stud"])
        worst = min(lev) if lev else None
        self.add("leverage", "Every joint the rods drive keeps leverage", "fail" if lev else "pass",
                 f"weakest {worst[0]:.2f} servo deg per joint deg at {fmt(worst[2])} ({worst[1]})" if worst
                 else f">= {self.tol['leverage_min']} servo deg per joint deg on every sweep and clip",
                 pose=worst[2] if worst else None)
        self.clearance(poses)

    def swivel(self, lid, pose, a, b):
        lk = next(l for l in self.asm.linkages if l.id == lid)
        ms = link_matrices(self.asm, pose)
        n = ms[lk.horn_link][:3, :3] @ unit(lk.axis)  # horn stud axis
        ng = ms[lk.ground_link][:3, :3] @ unit(lk.ground_axis)
        r = unit(b - a)
        return max(math.degrees(math.asin(min(1.0, abs(float(r @ n))))),
                   math.degrees(math.asin(min(1.0, abs(float(r @ ng))))))

    def moving_joints(self, bid):
        """Joints that move a body: its link chain, plus the joints its linkage follows."""
        b = self.bodies[bid]
        js = set(self.asm.link_chain(b.link))
        if b.linkage:
            js |= {j.id for j in self.asm.joints if b.linkage in (j.drive or {}).get("linkages", [])}
        return js

    def clearance(self, poses):
        """Moving pairs (their links move relative to each other, not mated): the smallest gap
        across the motion must stay >= the print tolerance. Pairs are grouped by the joints that
        move them; each group is swept on its own grid (a clearance map), AABBs prune far pairs
        per pose, FCL measures the rest; show-clip poses are checked only for pairs the map
        finds near. Results are cached by geometry hash."""
        tol = self.tol["clearance_mm"]
        margin = 3.0
        ids = list(self.bodies)
        mv = {k: self.moving_joints(k) for k in ids}
        groups: dict[tuple, list] = defaultdict(list)
        for a, b in itertools.combinations(ids, 2):
            if frozenset((a, b)) in self.mated:
                continue
            A, B = self.bodies[a], self.bodies[b]
            if A.link == B.link and not (A.linkage or B.linkage):
                continue
            if A.linkage and A.linkage == B.linkage and A.role == B.role:
                continue
            js = tuple(sorted(mv[a] ^ mv[b] if not (A.linkage or B.linkage) else mv[a] | mv[b]))
            if js:
                groups[js].append((a, b))
        joints = {j.id: j for j in self.asm.joints}
        step1 = 1.0 if self.full else 5.0
        stepn = self.tol["grid_deg"] if self.full else 2 * self.tol["grid_deg"]
        results = {}  # pair -> (min d, pose, d at rest)
        idx = {k: i for i, k in enumerate(self.scene.ids)}
        mat_cache: dict = {}

        def mats_at(pose):
            key = tuple(sorted(pose.items()))
            if key not in mat_cache:
                ms = link_matrices(self.asm, pose)
                sol = solve_linkages(self.asm, pose)
                mat_cache[key] = {k: self.body_matrix(self.bodies[k], ms, sol) for k in ids}
            return mat_cache[key]

        n_fcl = 0
        for js, pairs in groups.items():
            axes = []
            for j in js:
                lo, hi = joints[j].limits
                st = step1 if len(js) == 1 else stepn
                axes.append(sorted(set(np.round(np.append(np.arange(lo, hi + 1e-9, st), [lo, hi, 0.0]), 3))))
            cap = self.tol.get("max_grid_poses")
            n = int(np.prod([len(ax) for ax in axes]))
            if cap and n > cap:  # many joints (the whole droid): each axis at its ends and zero, then a sample
                axes = [sorted({ax[0], ax[-1], min(max(0.0, ax[0]), ax[-1])}) for ax in axes]
                combos = list(itertools.product(*axes))
                if len(combos) > cap:
                    rng = np.random.default_rng(0)
                    keep = rng.choice(len(combos), size=cap - 1, replace=False)
                    zero = tuple(min(max(0.0, ax[0]), ax[-1]) for ax in axes)
                    combos = [zero] + [combos[i] for i in sorted(keep)]
                self.capped_groups = getattr(self, "capped_groups", 0) + 1
                grid = [dict(zip(js, map(float, c))) for c in combos]
            else:
                grid = [dict(zip(js, map(float, c))) for c in itertools.product(*axes)]
            spec = (js, tuple(len(ax) for ax in axes),
                    tuple((j, joints[j].limits, tuple(joints[j].pivot), tuple(joints[j].axis)) for j in js),
                    tuple((lk.id, lk.rod_length, lk.radius, tuple(lk.zero_dir), tuple(lk.ground_point))
                          for lk in self.asm.linkages))
            keys = [("clr", self.scene.hash[a], self.scene.hash[b], spec) for a, b in pairs]
            D = np.full((len(pairs), len(grid)), np.inf)
            todo = []
            for i, k in enumerate(keys):
                row = self.cache.get(k)
                if row is None:
                    todo.append(i)
                else:
                    D[i] = row
            if todo:  # only the pairs whose geometry (or motion) changed since the last run
                ia = np.array([idx[pairs[i][0]] for i in todo])
                ib = np.array([idx[pairs[i][1]] for i in todo])
                for k, pose in enumerate(grid):
                    mats = mats_at(pose)
                    gaps = self.scene.gaps(self.scene.aabbs(mats), ia, ib)
                    for t, i in enumerate(todo):
                        if gaps[t] >= tol + margin:
                            D[i, k] = gaps[t]
                        else:
                            a, b = pairs[i]
                            D[i, k] = self.scene.distance(a, mats[a], b, mats[b])
                            n_fcl += 1
                for i in todo:
                    self.cache.put(keys[i], D[i].copy())
            rest = next((k for k, p in enumerate(grid) if all(abs(v) < 1e-9 for v in p.values())), 0)
            for i, (a, b) in enumerate(pairs):
                k = int(np.argmin(D[i]))
                results[(a, b)] = (float(D[i, k]), grid[k], float(D[i, rest]))
        # show clips: exact checks only for pairs the map found near
        near = [pr for pr, (d, _, _) in results.items() if d < tol + margin]
        clips = [p for src, p in poses if src.startswith("clip")]
        lk_key = tuple((lk.id, lk.rod_length, lk.radius, tuple(lk.zero_dir), tuple(lk.ground_point)) for lk in self.asm.linkages)
        for pose in clips:
            ptuple = tuple(sorted((k, round(v, 3)) for k, v in pose.items()))
            mats = None
            for a, b in near:
                ck = ("clip", self.scene.hash[a], self.scene.hash[b], ptuple, lk_key)
                d = self.cache.get(ck)
                if d is None:
                    mats = mats or mats_at(pose)
                    d = self.scene.distance(a, mats[a], b, mats[b])
                    self.cache.put(ck, d)
                    n_fcl += 1
                if d < results[(a, b)][0]:
                    results[(a, b)] = (d, pose, results[(a, b)][2])
        low = sorted((d, a, b, p) for (a, b), (d, p, d0) in results.items() if d < tol and d < d0 - 0.05)
        fmt = lambda p: ", ".join(f"{k} {v:+g}" for k, v in p.items()) or "rest"
        self.n_fcl = n_fcl
        self.verdict("clearance", "Moving gaps stay at least the print tolerance",
                     [((a, b), f"{a} to {b}: {d:.2f} mm at {fmt(p)}") for d, a, b, p in low],
                     f">= {tol} mm across {sum(len(p) for p in groups.values())} moving pairs", pose=low[0][3] if low else None,
                     assumptions=[f"{len(groups)} joint groups, each on its own grid ({'1/5' if self.full else '5/10'} deg "
                                  "for 1/n joints) + every show-clip keyframe for the pairs the grid finds within 3 mm"
                                  + (f"; {self.capped_groups} groups over {self.tol['max_grid_poses']} poses reduced to "
                                     "each joint's ends and zero (sampled past that)" if getattr(self, "capped_groups", 0) else ""),
                                  "AABB broad phase, FCL distance on the parts' BVHs; cached by geometry hash",
                                  "pairs that touch through a mate are not gaps; a gap the motion does not change "
                                  "(parts turning about a shared axis) is left to the overlap test"])

    def _dist(self, a, ma, b, mb, far=25.0):
        if len(self.pts(a)) > len(self.pts(b)):
            a, ma, b, mb = b, mb, a, ma
        rel = np.linalg.inv(mb) @ ma
        P = self.pts(a) @ rel[:3, :3].T + rel[:3, 3]
        lo, hi = self.bodies[b].mesh.bounds
        if np.any(P.min(0) > hi + far) or np.any(P.max(0) < lo - far):
            return far
        d, _ = self.tree(b).query(P, k=1, distance_upper_bound=far, workers=-1)
        return float(min(d.min(), far))

    # ------------------------------------------------------------------ 6 printable
    def printable(self):
        bed = sorted(self.tol["bed_mm"])
        too_big, thin = [], []
        for p in self.asm.parts:
            if not p.printed:
                continue
            ext = sorted(p.mesh.extents)
            if any(e > b for e, b in zip(ext, bed)):
                too_big.append(f"{p.id} {np.round(p.mesh.extents).astype(int).tolist()} mm")
            k = ("wall", self.scene.hash.get(p.id))
            w = self.cache.get(k)
            if w is None:
                w = _thin_wall(p.mesh)
                self.cache.put(k, w)
            if w is not None and w < self.tol["wall_min_mm"]:
                thin.append(f"{p.id} {w:.2f} mm")
        self.add("printable_bed", "Printed parts fit the bed", "fail" if too_big else "pass",
                 "; ".join(too_big) or f"all fit {'x'.join(str(int(b)) for b in self.tol['bed_mm'])} mm (any orientation)",
                 parts=[t.split(" ")[0] for t in too_big], assumptions=["Bambu H2D envelope until Brandon confirms"])
        self.add("printable_wall", "Printed parts keep the minimum wall", "warn" if thin else "pass",
                 "; ".join(thin[:8]) or f">= {self.tol['wall_min_mm']} mm", parts=[t.split(" ")[0] for t in thin],
                 assumptions=["2nd-percentile ray thickness from 3000 surface samples (closed meshes only)"])

    def run(self) -> list[Check]:
        import time

        for name, fn in (("connected", self.connected), ("mates_hold", self.mates_hold),
                         ("fasteners_real", self.fasteners_real), ("inserts", self.inserts),
                         ("no_overlap", self.overlaps),
                         ("motion", self.motion), ("printable", self.printable)):
            t0 = time.perf_counter()
            n0 = len(self.results)
            fn()
            dt = time.perf_counter() - t0
            for c in self.results[n0:]:
                c.seconds = round(dt / max(1, len(self.results) - n0), 3)
        self.cache.save()
        return self.results


def _check_mesh(m: trimesh.Trimesh, faces: int = 20000) -> trimesh.Trimesh:
    """A lighter copy for the checks (inside tests and exact distances scale with faces): decimated
    only while it stays closed, so penetration depths stay exact to ~0.05 mm. Cached by hash."""
    if len(m.faces) <= faces:
        return m
    from .collide import mesh_hash
    from .geom import cached

    def make():
        try:
            d = m.simplify_quadric_decimation(face_count=faces)
            if d.is_watertight or not m.is_watertight:
                return np.asarray(d.vertices), np.asarray(d.faces)
        except Exception:
            pass
        return np.asarray(m.vertices), np.asarray(m.faces)

    v, f = cached(f"checkmesh-{mesh_hash(m)}-{faces}", make)
    return trimesh.Trimesh(v, f, process=False)


def _nominal(thread: str) -> float:
    if thread.upper().startswith("M"):
        return float(thread[1:].split("-")[0])
    return {"#4": 2.845, "#6": 3.505, "#8": 4.166, "#10": 4.826}.get(thread.split("-")[0], 3.5)


def _thin_wall(m: trimesh.Trimesh):
    if not m.is_watertight or len(m.faces) < 20:
        return None
    pts, fi = trimesh.sample.sample_surface(m, 3000, seed=2)
    n = m.face_normals[fi]
    origins = pts - n * 0.01
    locs, idx, _ = m.ray.intersects_location(origins, -n, multiple_hits=False)
    if not len(idx):
        return None
    d = np.linalg.norm(locs - origins[idx], axis=1)
    d = d[d > 0.05]
    return float(np.percentile(d, 2)) if len(d) else None


def _rod_matrix(a0, b0, a, b):
    u0, u = unit(b0 - a0), unit(b - a)
    v = np.cross(u0, u)
    c = float(u0 @ u)
    if np.linalg.norm(v) < 1e-12:
        R = np.eye(3) if c > 0 else -np.eye(3)
    else:
        vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
        R = np.eye(3) + vx + vx @ vx * (1 / (1 + c))
    m = np.eye(4)
    m[:3, :3] = R
    m[:3, 3] = a - R @ a0
    return m


def _clip_poses(profile_to_joint: dict):
    """(clip id, pose) at every keyframe of every show clip that moves these joints (intensity 1)."""
    d = REPO / "show" / "clips"
    if not d.exists():
        return
    for f in sorted(d.glob("*.json")):
        try:
            c = json.loads(f.read_text())
        except Exception:
            continue
        tracks = {k: v for k, v in (c.get("tracks") or {}).items() if k in profile_to_joint}
        if not tracks:
            continue
        times = sorted({k[0] for t in tracks.values() for k in t.get("keys", [])})
        for t in times:
            pose = {}
            for name, tr in tracks.items():
                keys = tr.get("keys", [])
                pose[profile_to_joint[name]] = float(np.interp(t, [k[0] for k in keys], [k[1] for k in keys]))
            yield c.get("id", f.stem), pose


def run(asm: Assembly, explained: dict | None = None, full: bool = False) -> list[Check]:
    return Suite(asm, explained=explained, full=full).run()
