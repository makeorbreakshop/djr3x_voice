"""Per-link mass, centre of mass and inertia from the manifest's parts.

Each part: its display mesh (the manifest's decimated GLB, in the part frame) gives the shape
- volume, centroid and the inertia tensor of a uniform solid. The mass is the catalogue mass
(`mass_g`) where the manifest has one, else volume x density x effective fill (the same
material table as r3xmech/checks.py). The shape's inertia is scaled to that mass, i.e. every
part is treated as uniformly dense (a printed part's walls are denser than its infill, so its
inertia is a slight under-estimate). A mesh that is not a closed solid falls back to its
bounding box. Everything is summed per link about the link's COM (parallel axis).
"""

from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

import numpy as np

from .tree import Tree, apply

# g/cc and effective fill; r3xmech/catalog.py MATERIALS plus the metals the manifests name.
MATERIALS = {
    "PLA": (1.24, 0.45),
    "PETG": (1.27, 0.65),
    "rubber": (1.20, 1.0),
    "aluminium": (2.70, 1.0),
    "steel": (7.85, 1.0),
    "stainless": (7.90, 1.0),
    "nylon": (1.14, 1.0),
    "brass": (8.50, 1.0),
}


def material(name: str | None) -> tuple[float, float, str]:
    n = (name or "").lower()
    for k, (rho, fill) in MATERIALS.items():
        if n.startswith(k.lower()):
            return rho, fill, k
    return MATERIALS["PLA"][0], MATERIALS["PLA"][1], "PLA (assumed)"


@lru_cache(maxsize=None)
def _shape(path: str, mtime: float):
    """(volume mm^3, centroid, inertia tensor per unit density) of a mesh file, or None."""
    import trimesh

    try:
        m = trimesh.load(path, force="mesh", process=True)
    except Exception:
        return None
    if m is None or len(getattr(m, "faces", [])) == 0:
        return None
    lo, hi = m.bounds
    box = _box(lo, hi)
    try:
        # The divergence-theorem integrals are fine for a nearly closed mesh (r3xmech does the
        # same); an open or inverted one shows up as a volume <= 0 or above its bounding box.
        closed = bool(m.is_volume)
        v = float(m.volume)
        if v < 0:
            m.invert()
            v = -v
        c = np.asarray(m.center_mass, float)
        I = np.asarray(m.moment_inertia, float)  # about the COM, density 1
        if not (0 < v <= box[0] * 1.01 and np.all(np.isfinite(c)) and np.all(np.isfinite(I))):
            raise ValueError("degenerate")
        return v, c, I, "mesh" if closed else "mesh (open)"
    except Exception:
        return box + ("bbox",)


def _box(lo, hi):
    e = np.asarray(hi, float) - np.asarray(lo, float)
    v = float(np.prod(np.maximum(e, 1e-6)))
    a, b, c = e
    I = v / 12.0 * np.diag([b * b + c * c, a * a + c * c, a * a + b * b])
    return v, (np.asarray(lo, float) + np.asarray(hi, float)) / 2, I


def part_inertial(p) -> dict:
    """{kg, com (body mm), I (kg m^2 about com, body axes), how}."""
    raw = p.raw
    mesh = (p.base / raw["mesh"]) if raw.get("mesh") else None
    shape = None
    if mesh is not None and mesh.exists():
        shape = _shape(str(mesh), mesh.stat().st_mtime)
    T = p.T
    if shape is None:
        bb = raw.get("bbox")
        if not bb:
            return {"kg": 0.0, "com": apply(p.T, [0, 0, 0]), "I": np.zeros((3, 3)), "how": "none"}
        # bbox is in the assembly frame: use the assembly's placement instead of the part's
        T = p.T @ np.linalg.inv(_xform_of(raw))
        shape = _box(bb[0], bb[1]) + ("bbox (no mesh)",)
    vol, c_local, I_unit, how = shape
    rho, fill, mat = material(raw.get("material"))
    if raw.get("mass_g") is not None:
        kg = float(raw["mass_g"]) / 1000.0
        src = "catalogue"
    else:
        kg = vol / 1000.0 * rho * fill / 1000.0
        src = f"{mat} {rho} g/cc x {fill}"
    R = T[:3, :3]
    scale = kg / vol if vol > 0 else 0.0  # kg per mm^3 of shape
    I = R @ (I_unit * scale) @ R.T * 1e-6  # kg mm^2 -> kg m^2
    return {"kg": kg, "com": apply(T, c_local), "I": I, "how": f"{src}; shape {how}"}


def _xform_of(raw):
    from .tree import xform

    return xform(raw.get("transform"))


def link_inertials(tree: Tree, hidden: set[str] | None = None) -> dict[str, dict]:
    """gid -> {kg, com (mm), I (kg m^2 about com), parts, estimated_parts}."""
    acc: dict[str, list] = {}
    for p in tree.parts:
        if hidden and p.raw["id"] in hidden:
            continue
        acc.setdefault(p.link, []).append(part_inertial(p))
    out = {}
    for gid, items in acc.items():
        kg = sum(i["kg"] for i in items)
        if kg <= 0:
            continue
        com = sum(i["kg"] * i["com"] for i in items) / kg
        I = np.zeros((3, 3))
        for i in items:
            d = (i["com"] - com) / 1000.0
            I += i["I"] + i["kg"] * ((d @ d) * np.eye(3) - np.outer(d, d))
        out[gid] = {
            "kg": kg, "com": com, "I": I, "parts": len(items),
            "catalogue_parts": sum(1 for i in items if i["how"].startswith("catalogue")),
            "bbox_parts": sum(1 for i in items if "bbox" in i["how"]),
        }
    return out


def fmt_inertia(I: np.ndarray) -> list[float]:
    """[Ixx, Iyy, Izz, Ixy, Ixz, Iyz] kg m^2."""
    return [float(f"{x:.4g}") + 0.0 for x in (I[0, 0], I[1, 1], I[2, 2], I[0, 1], I[0, 2], I[1, 2])]


def is_finite(x) -> bool:
    return all(math.isfinite(float(v)) for v in np.ravel(x))
