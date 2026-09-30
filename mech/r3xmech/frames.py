"""Frames and rigid transforms.

Canonical body frame (show/SPEC.md "Frame and zeros"): mm, +Y up, +Z forward (the base's
front pod), +X the droid's left; every joint at 0 = the park droid's rest pose.

Two source frames feed it:

* **The printable kit** ("DJ R3X - v2", Large Cut STLs) is exported assembled, Y-up, mm, in
  a display pose. `sim/model/build_r3x.py` measured the rotations that bring each rigid
  subtree to the canonical rest; they are reused here unchanged (see KIT_YAW_DEG).
* **The R-3X Animation mechanism** (Onshape part studios) is Z-up and every studio has its
  own origin, usually on the axis the part turns about. Parts are placed by mates written
  out in `assemblies/r3x_animation/placements.py`, each with its evidence.
"""
from __future__ import annotations

import math

import numpy as np

# --- kit display pose -> canonical rest -------------------------------------------------
# build_r3x.py: BODY_YAW 33.0 (RX-24 plate to the front), HEAD_YAW 52.2 (head squared to
# the body), then rig_limits.json: body_yaw 12.0 and per-ring zero_offset (cumulative down
# the ring stack): torso_lower 46.1, torso_middle -2.4, torso_top -55.7.
_BODY, _SIMYAW = 33.0, 12.0
KIT_YAW_DEG = {
    "static": _BODY + _SIMYAW,                         # base, pedestal: 45.0
    "torso_lower": _BODY + _SIMYAW + 46.1,             # lower ring + poker arm: 91.1
    "torso_middle": _BODY + _SIMYAW + 46.1 - 2.4,      # middle ring + throttle arm: 88.7
    "torso_top": _BODY + _SIMYAW + 46.1 - 2.4 - 55.7,  # top ring + hero arm: 33.0
    "head": 52.2 + _SIMYAW + (46.1 - 2.4 - 55.7),      # head: 52.2 (net of the ring stack)
}


def roty(deg: float) -> np.ndarray:
    """4x4 rotation about +Y, three.js convention (x' = c x + s z, z' = -s x + c z)."""
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return np.array([[c, 0, s, 0], [0, 1, 0, 0], [-s, 0, c, 0], [0, 0, 0, 1]], float)


def rot(axis, deg: float) -> np.ndarray:
    """4x4 right-handed rotation about a unit axis through the origin."""
    a = np.asarray(axis, float)
    a = a / np.linalg.norm(a)
    x, y, z = a
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    C = 1 - c
    R = np.array([[c + x * x * C, x * y * C - z * s, x * z * C + y * s],
                  [y * x * C + z * s, c + y * y * C, y * z * C - x * s],
                  [z * x * C - y * s, z * y * C + x * s, c + z * z * C]])
    T = np.eye(4)
    T[:3, :3] = R
    return T


def trans(x, y=None, z=None) -> np.ndarray:
    if y is None:
        x, y, z = x
    T = np.eye(4)
    T[:3, 3] = (x, y, z)
    return T


def about(pivot, axis, value, kind="revolute") -> np.ndarray:
    """Joint motion in the canonical rest frame: rotation about (pivot, axis) or slide."""
    if kind == "prismatic":
        return trans(np.asarray(axis, float) / np.linalg.norm(axis) * value)
    if kind == "fixed":
        return np.eye(4)
    p = np.asarray(pivot, float)
    return trans(p) @ rot(axis, value) @ trans(-p)


# Onshape part-studio frame (Z up) -> canonical (Y up): (x, y, z) -> (x, z, -y).
ZUP = np.array([[1, 0, 0, 0], [0, 0, 1, 0], [0, -1, 0, 0], [0, 0, 0, 1]], float)


def basis(x_to, y_to, z_to, origin_to=(0, 0, 0), origin_from=(0, 0, 0)) -> np.ndarray:
    """Transform taking source axes (x, y, z) to the given canonical directions, and the
    source point `origin_from` to canonical `origin_to`. Directions must be orthonormal and
    right-handed (checked)."""
    R = np.column_stack([np.asarray(v, float) / np.linalg.norm(v) for v in (x_to, y_to, z_to)])
    if not np.allclose(R.T @ R, np.eye(3), atol=1e-6) or np.linalg.det(R) < 0:
        raise ValueError("basis(): axes must be orthonormal and right-handed")
    T = np.eye(4)
    T[:3, :3] = R
    T[:3, 3] = np.asarray(origin_to, float) - R @ np.asarray(origin_from, float)
    return T


def apply(T, pts) -> np.ndarray:
    pts = np.asarray(pts, float)
    return pts @ T[:3, :3].T + T[:3, 3]


def apply_dir(T, d) -> np.ndarray:
    v = T[:3, :3] @ np.asarray(d, float)
    return v / np.linalg.norm(v)


def to_tq(T) -> dict:
    """4x4 -> schema Transform {t, q (x, y, z, w)}."""
    R = T[:3, :3]
    w = math.sqrt(max(0.0, 1 + R[0, 0] + R[1, 1] + R[2, 2])) / 2
    if w > 1e-6:
        x = (R[2, 1] - R[1, 2]) / (4 * w)
        y = (R[0, 2] - R[2, 0]) / (4 * w)
        z = (R[1, 0] - R[0, 1]) / (4 * w)
    else:  # 180 deg: pick the largest diagonal
        i = int(np.argmax(np.diag(R)))
        q = np.zeros(3)
        q[i] = math.sqrt(max(0.0, 1 + 2 * R[i, i] - np.trace(R))) / 2
        j, k = (i + 1) % 3, (i + 2) % 3
        q[j] = (R[j, i] + R[i, j]) / (4 * q[i])
        q[k] = (R[k, i] + R[i, k]) / (4 * q[i])
        x, y, z = q
        w = (R[k, j] - R[j, k]) / (4 * q[i])
    n = math.sqrt(x * x + y * y + z * z + w * w)
    r = lambda v: round(float(v), 6)  # noqa: E731
    return {"t": [round(float(v), 4) for v in T[:3, 3]], "q": [r(x / n), r(y / n), r(z / n), r(w / n)]}
