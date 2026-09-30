"""Features and mates: how every part connects to its neighbour.

A *feature* is a named piece of real geometry on a part, measured from its mesh or taken from
the vendor model, stored in the assembly frame at the zero pose:

    axis   {p, d, r}         a hole, bore, shaft or screw shank (r = radius; d points into the part)
    plane  {p, n}            a face (n points out of the material)
    spline {p, d, teeth}     a servo output or a hub's spline bore
    ball   {c, r}            a ball stud / ball-link ball

A *mate* joins a feature on one part to a feature on another:

    concentric   axis <-> axis           (bolt in hole, shaft in bore)
    seated       plane <-> plane         (a head on its counterbore floor, a face on a face: normals opposed)
    coplanar     plane <-> plane         (flush faces, e.g. an insert's top with its boss: normals equal)
    spline       spline <-> spline       (servo output <-> hub; tooth counts must match)
    ball_link    ball <-> ball           (rod-end socket on its ball)
    threaded     axis <-> axis           (screw into an insert / tapped hole; `engage_mm` required)
    press, glue  any                     (where the source says so)

Parts that are `solved` get their placement from their mates (`place_*` below); parts placed by
a vendor assembly (Hunter's STEP) keep that placement and their mates are *verified* by the
test suite. Either way the suite checks every mate's residual.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

MATE_TYPES = ("concentric", "seated", "coplanar", "spline", "ball_link", "threaded", "press", "glue", "placed")
# "placed": no geometric mate - a part set where a placement model (the kit export, a fitted STL)
# puts it, joined to its assembly for the connected test only (the whole-droid suite names them)


def unit(v):
    v = np.asarray(v, float)
    return v / np.linalg.norm(v)


def axis(p, d, r=0.0):
    return {"type": "axis", "p": [float(x) for x in p], "d": [float(x) for x in unit(d)], "r": float(r)}


def plane(p, n):
    return {"type": "plane", "p": [float(x) for x in p], "n": [float(x) for x in unit(n)]}


def spline(p, d, teeth):
    return {"type": "spline", "p": [float(x) for x in p], "d": [float(x) for x in unit(d)], "teeth": int(teeth)}


def ball(c, r):
    return {"type": "ball", "c": [float(x) for x in c], "r": float(r)}


def moved(f: dict, m: np.ndarray) -> dict:
    """A feature carried by a 4x4 matrix."""
    R, t = m[:3, :3], m[:3, 3]
    g = dict(f)
    for k in ("p", "c"):
        if k in f:
            g[k] = [float(x) for x in R @ np.asarray(f[k]) + t]
    for k in ("d", "n"):
        if k in f:
            g[k] = [float(x) for x in R @ np.asarray(f[k])]
    return g


@dataclass
class Mate:
    id: str
    type: str
    a: tuple  # (part id, feature name)
    b: tuple
    params: dict = field(default_factory=dict)
    solved: bool = True  # False: the placement came from a vendor assembly; the mate is verified
    note: str = ""

    def __post_init__(self):
        assert self.type in MATE_TYPES, self.type


# ------------------------------------------------------------------ placement from a mate

def frame_on_axis(p, d, clock_ref=None) -> np.ndarray:
    """A matrix whose +Z runs along `d` from `p`; +X toward `clock_ref` (projected) when given."""
    z = unit(d)
    ref = np.asarray(clock_ref, float) if clock_ref is not None else (np.array([0, 1, 0.0]) if abs(z[1]) < 0.9 else np.array([1, 0, 0.0]))
    x = ref - z * (ref @ z)
    if np.linalg.norm(x) < 1e-9:
        x = np.cross(z, [0, 0, 1.0]) if abs(z[2]) < 0.9 else np.cross(z, [1.0, 0, 0])
    x = unit(x)
    y = np.cross(z, x)
    m = np.eye(4)
    m[:3, 0], m[:3, 1], m[:3, 2], m[:3, 3] = x, y, z, p
    return m


def place_axis(local_p, local_d, target: dict, offset: float = 0.0, clock_ref=None, local_x=None) -> np.ndarray:
    """Matrix taking a part's local axis (point, direction) onto a target axis feature, the local
    point `offset` along the target direction, clocked so local +x (or `local_x`) points toward
    `clock_ref`."""
    tgt = frame_on_axis(np.asarray(target["p"]) + unit(target["d"]) * offset, target["d"], clock_ref)
    lx = local_x if local_x is not None else None
    src = frame_on_axis(local_p, local_d, lx)
    return tgt @ np.linalg.inv(src)


# ------------------------------------------------------------------ residuals (for the suite)

def residual(m: Mate, fa: dict, fb: dict) -> dict:
    """How far a mate is from holding: axis angle (deg), axis offset (mm), plane gap (mm), ball
    centre distance (mm), tooth mismatch."""
    out: dict = {}
    if fa["type"] in ("axis", "spline") and fb["type"] in ("axis", "spline"):
        da, db = unit(fa["d"]), unit(fb["d"])
        out["angle_deg"] = math.degrees(math.acos(min(1.0, abs(float(da @ db)))))
        w = np.asarray(fb["p"]) - np.asarray(fa["p"])
        out["offset_mm"] = float(np.linalg.norm(w - da * (w @ da)))
        if fa["type"] == "spline" and fb["type"] == "spline":
            out["teeth"] = (fa["teeth"], fb["teeth"])
    elif fa["type"] == "plane" and fb["type"] == "plane":
        na, nb = unit(fa["n"]), unit(fb["n"])
        sign = 1.0 if m.type == "coplanar" else -1.0
        out["angle_deg"] = math.degrees(math.acos(min(1.0, max(-1.0, float(sign * na @ nb)))))
        out["gap_mm"] = float((np.asarray(fb["p"]) - np.asarray(fa["p"])) @ na)
    elif fa["type"] == "ball" and fb["type"] == "ball":
        out["offset_mm"] = float(np.linalg.norm(np.asarray(fa["c"]) - np.asarray(fb["c"])))
        out["radius_diff_mm"] = abs(fa["r"] - fb["r"])
    elif "axis" in (fa["type"], fb["type"]) and "plane" in (fa["type"], fb["type"]):
        ax, pl = (fa, fb) if fa["type"] == "axis" else (fb, fa)
        out["angle_deg"] = math.degrees(math.acos(min(1.0, abs(float(unit(ax["d"]) @ unit(pl["n"]))))))
        out["gap_mm"] = float((np.asarray(ax["p"]) - np.asarray(pl["p"])) @ unit(pl["n"]))
    return out
