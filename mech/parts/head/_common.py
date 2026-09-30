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


# ------------------------------------------------------------------ hole types, inserts and nuts
# Every screw hole group in a parametric part has a `hole_type`:
#   clearance  the screw passes (the designer's clearance diameter)
#   heat_set   a heat-set insert: its datasheet hole diameter, depth >= insert length + 1 mm
#   tapped     the screw self-threads into the plastic (tap-drill diameter)
#   nut_trap   clearance, with a hex pocket for the nut at the far end
# Parts reproduce their source "as designed" by default. `inserts=True` (a named preset) turns the
# groups a part lists as screw-into-plastic into heat_set; a group's own `<group>_hole` parameter
# overrides both. Nut traps and through-bolts stay where they clamp or carry pivot shear.

HOLE_TYPES = ("clearance", "heat_set", "tapped", "nut_trap")

# Heat-set inserts (datasheet hole diameter, insert length). Standard metric brass inserts (Ruthex /
# CNC Kitchen sizes) unless a kit names its own; hole depth = length + 1 mm.
INSERTS = {
    "M2": {"d": 3.2, "length": 4.0, "source": "M2 x 4 heat-set (Ruthex RX-M2x4: hole 3.2)"},
    "M2.5": {"d": 3.6, "length": 5.7, "source": "M2.5 x 5.7 heat-set (Ruthex RX-M2.5x5.7: hole 3.6)"},
    "M3": {"d": 4.0, "length": 5.7, "source": "M3 x 5.7 heat-set (Ruthex RX-M3x5.7: hole 4.0)"},
    "M4": {"d": 5.6, "length": 8.1, "source": "M4 x 8.1 heat-set (Ruthex RX-M4x8.1: hole 5.6)"},
    "M5": {"d": 6.4, "length": 9.5, "source": "M5 x 9.5 heat-set (Ruthex RX-M5x9.5: hole 6.4)"},
    # the kit's own: Hunter's head BOM "M4 heat set inserts - 6mm by 6mm", drawn at 6.0
    "M4-kit": {"d": 6.0, "length": 6.0, "source": "R-3X head BOM: M4 heat-set, 6 x 6 mm (Hunter's hole 6.0)"},
    # Anderson's lift rack, as drawn: a 5.5 x 4.5 mm pocket (a short M4 insert)
    "M4-anderson": {"d": 5.5, "length": 3.5, "source": "Anderson's lift rack as drawn: 5.5 x 4.5 mm pocket (short M4)"},
    # the kit's McMaster inserts: no datasheet on this machine - standard values, flagged
    "94180A331": {"d": 4.0, "length": 5.7, "verified": False,
                  "source": "McMaster 94180A331 (M3 heat-set): hole unverified, standard M3 values used"},
    "94180A351": {"d": 5.6, "length": 8.1, "verified": False,
                  "source": "McMaster 94180A351 (M4 heat-set): hole unverified, standard M4 values used"},
    "93365A": {"d": 4.0, "length": 5.7, "verified": False,
               "source": "McMaster 93365A* (tapered heat-set, M3 assumed): hole unverified, standard M3 values used"},
}
NUTS = {  # ISO 4032 hex nuts: across flats, thickness
    "M2": (4.0, 1.6), "M2.5": (5.0, 2.0), "M3": (5.5, 2.4), "M4": (7.0, 3.2), "M5": (8.0, 4.7),
}
WALL_MIN = 1.5            # plastic round an insert


def insert_for(bolt: str, insert: str | None = None) -> dict:
    return INSERTS[insert or bolt]


ALIASES = {"tap": "tapped", "heatset": "heat_set", "heat-set": "heat_set", "nut": "nut_trap"}


def resolve_hole_types(P: dict, designed: dict, candidates: tuple = ()) -> dict:
    """{group: hole_type}: the part's `<group>_hole` parameter if set, else heat_set when the
    `inserts` preset is on and the group is a screw-into-plastic candidate, else as designed."""
    out = {}
    for g, t in designed.items():
        v = P.get(f"{g}_hole")
        if v is None:
            v = "heat_set" if P.get("inserts") and g in candidates else t
        v = ALIASES.get(v, v)
        if v not in HOLE_TYPES:
            raise ValueError(f"{g}_hole = {v!r}: one of {HOLE_TYPES}")
        out[g] = v
    return out


def hole_group_params(designed: dict) -> dict:
    """The DEFAULTS entries for a part's hole groups: `inserts` (the preset) and `<group>_hole`
    (None = as designed / preset)."""
    return {"inserts": False, **{f"{g}_hole": None for g in designed}}


# ISO 10511 prevailing-torque (nylon insert) lock nuts: AF, height. The pivot rule: a hole that
# carries a pivot's shear takes a through-bolt with one of these, never a screw tapped into plastic.
LOCK_NUTS = {"M3": (5.5, 4.0), "M4": (7.0, 5.0), "M5": (8.0, 5.0), "M6": (10.0, 6.0)}


def lock_nut(feats: dict, name: str, p, n, bolt: str):
    """Record a lock nut on hole `name`: `nut_<name>` (the face it bears on, `n` out of the part) and
    the nut on `hole_<name>`, for the workbench's hardware placement."""
    af, h = LOCK_NUTS[bolt]
    feats[f"nut_{name}"] = plane(p, n)
    feats[f"hole_{name}"]["lock_nut"] = f"{bolt} ISO 10511 nylock, AF {af}, h {h}"


def cut_hole(body, feats: dict, name: str, entry, d_into, bolt: str, hole_type: str, depth: float,
             fit: float = 0.0, sizes: dict | None = None, insert: str | None = None, grow_boss: bool = False,
             through: float | None = None):
    """Cut one screw hole of `hole_type` into `body`, entering at `entry` along `d_into`, and record
    it as `hole_<name>` / `face_<name>`. `depth` is the hole's length as designed (the material it
    runs through); `through` (if longer) keeps a clearance hole on past an insert for the screw tip.
    Returns the new body."""
    import numpy as np
    from build123d import Align, BuildSketch, Cylinder, Plane, RegularPolygon, extrude

    d_into = np.asarray(d_into, float) / np.linalg.norm(d_into)
    entry = np.asarray(entry, float)
    pl = Plane(origin=tuple(entry), z_dir=tuple(d_into))

    def cyl(r, length, start=0.0):
        return (Plane(origin=tuple(entry + d_into * start), z_dir=tuple(d_into)).location
                * Cylinder(r, length, align=(Align.CENTER, Align.CENTER, Align.MIN)))

    extra = {"bolt": bolt, "kind": hole_type}
    if hole_type == "heat_set":
        ins = insert_for(bolt, insert)
        dh = ins["d"] + fit
        L = ins["length"] + 1.0
        if grow_boss:
            body = body + cyl(dh / 2 + WALL_MIN, L)
        body = body - cyl(dh / 2, L + 0.01, -0.01)
        rest = max(depth, through or 0.0) - L
        if rest > 0:
            body = body - cyl(hole_d(bolt, "clearance", fit, sizes) / 2, rest + 0.02, L - 0.01)
        extra.update(insert=ins["source"], insert_length=ins["length"])
        hole_features(feats, name, entry, d_into, dh / 2, depth=L)
    else:
        kind = "tap" if hole_type == "tapped" else "clearance"
        dh = hole_d(bolt, kind, fit, sizes)
        body = body - cyl(dh / 2, depth + 0.02, -0.01)
        if hole_type == "nut_trap":
            af, nt = NUTS[bolt]
            with BuildSketch(Plane(origin=tuple(entry + d_into * (depth - nt - 0.2)), z_dir=tuple(d_into))) as hx:
                RegularPolygon((af + fit) / math.sqrt(3), 6)
            body = body - extrude(hx.sketch, amount=nt + 0.21)
            extra.update(nut=f"{bolt} ISO 4032, AF {af}")
        hole_features(feats, name, entry, d_into, dh / 2, depth=depth)
    feats[f"hole_{name}"].update(extra)
    return body
