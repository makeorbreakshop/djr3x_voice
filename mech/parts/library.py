"""The real-parts library: a purchased part's mesh in its kind's canonical frame, from the
vendor's CAD when it is in the cache (mech/vendor/parts_cad/, gitignored), else the
parametric model to spec (models.py), else a sized placeholder. Every result says which:
`cad` = vendor | parametric | placeholder, so Build shows which parts are exact.

    from parts.library import part
    p = part("gobilda", "2913-0004-0241")      # -> Resolved(mesh, status, file, note)

Canonical frames (what every consumer places):
    screw/pin        head (or top face) at 0, shank along +Z
    insert           top face at 0, body along +Z
    nut, washer      bearing face at 0, along +Z
    magnet, bearing  centred, axis +Z
    standoff, rod    from 0 along +Z
    ball_link        ball centre at 0, body along +Z
    rod_end          ball centre at 0, shank along +Z
    servo            output spline top at 0, axis +Y, long side +X (spline end at -X),
                     body below
    servo_hub        spline axis +Y, the face that sits on the servo at y = 0
    control_arm      hub axis +Y, arm along +X, the face on the hub at y = 0
    extrusion        along +Y from 0;  lazy_susan: axis +Y, base at 0;  linear_rail: along +Z
Vendor STEPs come in the vendor's own frame; VENDOR_FRAMES maps each one onto its canonical
frame, from the geometry (axes, faces), with the numbers checked in tests.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
import trimesh

from . import models
from .fetch import cached_file

HERE = Path(__file__).resolve().parent
CATALOG = HERE / "catalog.json"


@dataclass
class Resolved:
    status: str  # vendor | parametric | placeholder
    file: str = ""
    note: str = ""
    mesh: trimesh.Trimesh | None = None


def _rot(axis, deg):
    return trimesh.transformations.rotation_matrix(np.radians(deg), axis)


def _step_mesh(path: Path) -> trimesh.Trimesh:
    import sys

    sys.path.insert(0, str(HERE.parent))
    from workbench.geom import step_mesh

    return step_mesh(path, tol=0.03)


def _T(t=(0, 0, 0), R=None):
    m = np.eye(4)
    if R is not None:
        m[:3, :3] = R
    m[:3, 3] = t
    return m


Z_TO_Y = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], float)   # (x, y, z) -> (x, z, -y)
Y_TO_Z = np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]], float)   # (x, y, z) -> (x, -z, y)


def _servo_2000(m):
    """goBILDA 2000 series: spline axis +Z at x = -10, top at z = 16.9 (case boss top 12.8)."""
    V = m.vertices
    top = V[V[:, 2] > V[:, 2].max() - 1.0]
    c = np.array([top[:, 0].mean(), top[:, 1].mean(), V[:, 2].max()])
    return _T(R=Z_TO_Y) @ _T(-c)


def _hub_1906(m):
    """Servo face (the spline side, the small boss) at y = -0.9 -> y = 0."""
    return _T((0, -m.bounds[0][1], 0))


def _arm_1916(m):
    """Already canonical: hub axis +Y, face on the hub at y = 0, arm along +X (holes at 24/32/40/48)."""
    return np.eye(4)


def _ball_link_2913(m):
    """The ball (bore along Z) is the smallest near-sphere; the housing runs along +Y from it."""
    V = m.vertices
    # ball centre: centre of the vertices within the ball's 4.75 mm of the densest ring; the
    # housing is ~32 mm long in Y, the ball sits at its -Y end, 9.8 mm in from it
    y0 = V[:, 1].min()
    c = np.array([(V[:, 0].min() + V[:, 0].max()) / 2, y0 + 6.88, (V[:, 2].min() + V[:, 2].max()) / 2])
    return _T(R=Y_TO_Z) @ _T(-c)


def _rod_2808(m):
    return _T((0, 0, -m.bounds[0][2]))


def _screw_2800(m):
    """Head at the -Z end: its bearing face (z = -14 for the 14 mm screw) -> 0, shank +Z."""
    return _T((0, 0, -(m.bounds[0][2] + 4.0)))


def _standoff_1501(m):
    lo, hi = m.bounds
    ext = hi - lo
    ax = int(np.argmax(ext))
    R = np.eye(3) if ax == 2 else (Y_TO_Z if ax == 1 else np.array([[0, 0, -1], [0, 1, 0], [1, 0, 0]], float))
    mm = _T(R=R)
    c = (lo + hi) / 2
    b = (R @ (lo - c)), (R @ (hi - c))
    return _T((0, 0, -min(b[0][2], b[1][2]))) @ mm @ _T(-c)


# Vendor frame -> canonical frame, per part number (each checked against the vendor geometry:
# see workbench/tests and the notes above).
VENDOR_FRAMES: dict[str, callable] = {
    "2000-0025-0002": _servo_2000,
    "1906-0025-0032": _hub_1906,
    "1916-0014-0048": _arm_1916,
    "2913-0004-0241": _ball_link_2913,
    "2808-0004-0050": _rod_2808,
    "2800-0004-0014": _screw_2800,
    "1501-0006-0430": _standoff_1501,
    "1501-0006-0460": _standoff_1501,
    "1501-0006-0600": _standoff_1501,
}


@lru_cache(maxsize=None)
def _catalog() -> dict:
    if not CATALOG.exists():
        return {}
    return {p["id"]: p for p in json.loads(CATALOG.read_text())["parts"]}


def resolve(entry: dict, want_mesh: bool = False) -> Resolved:
    v, pn, kind, spec = entry["vendor"], entry["pn"], entry.get("kind", ""), entry.get("spec", {})
    f = cached_file(v, pn)
    if f is not None:
        if pn in VENDOR_FRAMES or not want_mesh:
            r = Resolved("vendor", f"vendor/parts_cad/{v}/{f.name}",
                         "" if pn in VENDOR_FRAMES else "frame not yet mapped: placed parts use the parametric model")
            if want_mesh:
                m = _step_mesh(f)
                m.apply_transform(VENDOR_FRAMES[pn](m))
                r.mesh = m
            if pn in VENDOR_FRAMES or not want_mesh:
                return r
    gen = models.GENERATORS.get(kind)
    if gen and _complete(kind, spec):
        return Resolved("parametric", note=f"{kind} to spec", mesh=gen(spec) if want_mesh else None)
    ph = models.PLACEHOLDERS.get(kind)
    return Resolved("placeholder", note="sized box" if ph else "no model yet",
                    mesh=(ph(spec) if ph else None) if want_mesh else None)


def _complete(kind: str, spec: dict) -> bool:
    need = {"screw": ("thread", "length_mm"), "insert": ("thread",), "servo": ("case",), "magnet": ("od_mm",),
            "standoff": ("length_mm",), "threaded_rod": ("thread", "length_mm"), "bearing": ("id_mm",),
            "lazy_susan": ("size_in",), "washer": (), "nut": ("thread",), "tube": ("od_mm",), "pin": ("d_mm",),
            "rod_end": (), "ball_link": (), "linear_rail": (), "extrusion": (), "t_nut": (), "disc_horn": (), "carriage": ()}.get(kind)
    return need is not None and all(k in spec for k in need)


def part(vendor: str, pn: str, spec: dict | None = None, kind: str | None = None) -> Resolved:
    """The mesh for a catalog part (or an ad-hoc spec), canonical frame, with its cad status."""
    e = _catalog().get(f"{vendor}:{pn}", {"vendor": vendor, "pn": pn})
    e = {**e, "spec": {**e.get("spec", {}), **(spec or {})}, "kind": kind or e.get("kind", "")}
    return _cached_part(json.dumps(e, sort_keys=True))


@lru_cache(maxsize=None)
def _cached_part(key: str) -> Resolved:
    return resolve(json.loads(key), want_mesh=True)


def spec_part(kind: str, spec: dict) -> Resolved:
    """A generic part by spec only (an ISO screw of a given length, a washer)."""
    return part("iso", f"{kind}:{json.dumps(spec, sort_keys=True)}", spec, kind)


def fit_to_standin(standin: trimesh.Trimesh, model: trimesh.Trimesh, samples: int = 3000):
    """Put a real/parametric part where a builder's stand-in ("-dnp" placeholder) sits: match the
    principal frames (all 24 proper axis maps) and keep the one closest to the stand-in's
    surface. Returns (model in the stand-in's frame, mean surface distance mm)."""
    from itertools import permutations, product

    from scipy.spatial import cKDTree

    def frame(m):
        c = m.bounds.mean(0)
        _, _, vt = np.linalg.svd(m.vertices - m.vertices.mean(0), full_matrices=False)
        return c, vt  # rows = principal axes

    cs, As = frame(standin)
    cm, Am = frame(model)
    ps, _ = trimesh.sample.sample_surface(standin, samples, seed=3)
    tree = cKDTree(ps)
    pm, _ = trimesh.sample.sample_surface(model, samples, seed=4)
    best = None
    for perm in permutations(range(3)):
        for signs in product((1, -1), repeat=3):
            B = np.array([signs[i] * Am[perm[i]] for i in range(3)])
            R = As.T @ B  # model axes -> stand-in axes
            if np.linalg.det(R) < 0:
                continue
            q = (pm - cm) @ R.T + cs
            d, _ = tree.query(q)
            score = float(d.mean())
            if best is None or score < best[0]:
                M = np.eye(4)
                M[:3, :3] = R
                M[:3, 3] = cs - R @ cm
                best = (score, M)
    out = model.copy()
    out.apply_transform(best[1])
    return out, best[0]
