"""Shared design vocabulary for the head's parametric parts: fastener hole sizes, the print fit,
named mate features, printability metadata, and the part wrapper the workbench's swap hook reads.

Every `make()` in this package returns a build123d `Part` in its *reference file's own frame*
(so a swap changes no placement), carrying:

    part.features     {id: axis|plane dict}   mate features (mech/workbench/mates.py conventions:
                                               axis d points into the material, plane n out of it)
    part.params       the resolved parameters (units in the names/docstrings: mm, deg)
    part.reference    the vendored file the defaults reproduce (mech/vendor/hunter_head/...)
    part.printability {"orientation", "base", "supports", "note"}
    part.label        a human name
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

# ------------------------------------------------------------------ fastener holes (mm)
# Nominal printed-hole diameters before the print fit is added. Hunter's parts use:
# M4 clearance 4.5 (plate flanges, pillow bridge), M4 tap 3.33 (coupler, cross: self-threaded),
# M4 heat-set 6.0 (the BOM's 6 x 6 mm inserts). Clearance = ISO 273 "medium".
HOLES = {
    "M2": {"clearance": 2.4, "tap": 1.6, "heatset": 3.2, "heatset_depth": 4.0},
    "M2.5": {"clearance": 2.9, "tap": 2.05, "heatset": 3.6, "heatset_depth": 4.0},
    "M3": {"clearance": 3.4, "tap": 2.5, "heatset": 4.0, "heatset_depth": 5.7},
    "M4": {"clearance": 4.5, "tap": 3.333, "heatset": 6.0, "heatset_depth": 6.0},
    "M5": {"clearance": 5.5, "tap": 4.2, "heatset": 6.4, "heatset_depth": 7.0},
}
HOLE_KINDS = ("clearance", "tap", "heatset")


def hole_d(bolt: str, kind: str, fit: float = 0.0, sizes: dict | None = None) -> float:
    """Printed hole diameter (mm) for `bolt` ('M4') as `kind` ('clearance' | 'tap' | 'heatset'),
    opened by the print fit `fit` (mm on the diameter). `sizes` overrides the table per designer
    ({"M3": {"clearance": 3.5}}: Anderson draws M3 clearance at 3.5)."""
    if kind not in HOLE_KINDS:
        raise ValueError(f"hole kind {kind!r}: one of {HOLE_KINDS}")
    return (sizes or {}).get(bolt, {}).get(kind, HOLES[bolt][kind]) + fit


# ------------------------------------------------------------------ features

def _unit(v):
    v = np.asarray(v, float)
    return v / np.linalg.norm(v)


def axis(p, d, r, **extra) -> dict:
    """A hole/bore/shaft: `p` the entry point on the surface, `d` into the material, `r` radius."""
    f = {"type": "axis", "p": [float(x) for x in p], "d": [float(x) for x in _unit(d)], "r": float(r)}
    f.update(extra)
    return f


def plane(p, n) -> dict:
    """A face: `p` on it, `n` out of the material."""
    return {"type": "plane", "p": [float(x) for x in p], "n": [float(x) for x in _unit(n)]}


def hole_features(feats: dict, name: str, entry, d, r, depth=None, bolt=None, kind=None):
    """`hole_<name>` (axis) + `face_<name>` (its entry face): the pair the workbench's hardware
    solver names its measured holes with, so its mates attach here unchanged."""
    extra = {}
    if depth is not None:
        extra["depth"] = float(depth)
    if bolt:
        extra["bolt"] = bolt
    if kind:
        extra["kind"] = kind
    feats[f"hole_{name}"] = axis(entry, d, r, **extra)
    feats[f"face_{name}"] = plane(entry, -_unit(d))


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


# ------------------------------------------------------------------ printability

@dataclass
class Print:
    orientation: str
    base: str
    supports: bool
    note: str = ""

    def as_dict(self):
        return {"orientation": self.orientation, "base": self.base, "supports": self.supports, "note": self.note}


# ------------------------------------------------------------------ wrapping

def finish(part, *, label: str, params: dict, features: dict, reference: str, printability: Print,
           frame: np.ndarray | None = None):
    """Attach the metadata the swap hook reads. `frame` (4x4), when given, carries the part and
    its features from the design frame into the reference file's frame."""
    if frame is not None:
        part = transform(part, frame)
        features = {k: moved(v, frame) for k, v in features.items()}
    part.label = label
    part.features = features
    part.params = {k: (list(v) if isinstance(v, tuple) else v) for k, v in params.items()}
    part.reference = reference
    part.printability = printability.as_dict()
    return part


def transform(shape, m: np.ndarray):
    """A rigid 4x4 applied to a build123d shape (rotation must be orthonormal)."""
    from build123d import Location

    from OCP.gp import gp_Trsf

    t = gp_Trsf()
    R = np.asarray(m, float)
    t.SetValues(*[float(R[i, j]) for i in range(3) for j in range(4)])
    return shape.moved(Location(t))


def mesh(part, tol: float = 0.01, ang: float = 0.1):
    """A trimesh of a part (for comparison, display and the workbench)."""
    import trimesh

    v, t = part.tessellate(tol, ang)
    m = trimesh.Trimesh(np.array([(p.X, p.Y, p.Z) for p in v]), np.array(t), process=True)
    # OCC tessellates cone apexes (drill points) with near-coincident duplicates: weld them
    m.merge_vertices(digits_vertex=4)
    m.update_faces(m.nondegenerate_faces())
    m.remove_unreferenced_vertices()
    return m


def rot_x(deg):
    a = math.radians(deg)
    m = np.eye(4)
    m[1:3, 1:3] = [[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]]
    return m


def rot_y(deg):
    a = math.radians(deg)
    m = np.eye(4)
    m[0, 0], m[0, 2], m[2, 0], m[2, 2] = math.cos(a), math.sin(a), -math.sin(a), math.cos(a)
    return m


def rot_z(deg):
    a = math.radians(deg)
    m = np.eye(4)
    m[:2, :2] = [[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]]
    return m


def trans(v):
    m = np.eye(4)
    m[:3, 3] = v
    return m
