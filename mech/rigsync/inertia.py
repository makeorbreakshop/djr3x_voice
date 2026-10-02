"""Per-link mass, centre of mass and inertia from the manifest's parts.

Each part's shape - volume, centroid and the inertia tensor of a uniform solid - comes from the
manifest's `mass_props`, which the build computes from the part's source mesh before any display
LOD or quantization (workbench/build.py `_mass_props`): how a part is drawn can never change its
mass. A manifest built before that field existed falls back to the part's GLB (`mesh_full`, else
`mesh`), read with its glTF dequantization applied (normalized int16 positions times the node's
scale plus its translation: KHR_mesh_quantization, which trimesh reads as raw integers). The mass is the catalogue mass
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


_NORM = {5120: (np.int8, 127.0), 5121: (np.uint8, 255.0), 5122: (np.int16, 32767.0), 5123: (np.uint16, 65535.0),
         5125: (np.uint32, None), 5126: (np.float32, None)}


def _node_matrix(n: dict) -> np.ndarray:
    if "matrix" in n:
        return np.asarray(n["matrix"], float).reshape(4, 4).T
    m = np.eye(4)
    x, y, z, w = n.get("rotation", [0, 0, 0, 1])
    R = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                  [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                  [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
    m[:3, :3] = R @ np.diag(n.get("scale", [1, 1, 1]))
    m[:3, 3] = n.get("translation", [0, 0, 0])
    return m


def read_glb(path: str):
    """A GLB's triangles as a trimesh, in the file's frame: every node's meshes through its (and its
    parents') transform, positions dequantized when the accessor is normalized (glTF 2.0 3.6.1 and
    KHR_mesh_quantization). Triangle lists only."""
    import json
    import struct

    import trimesh

    b = Path(path).read_bytes()
    if b[:4] != b"glTF":
        raise ValueError("not a GLB")
    jl = struct.unpack_from("<I", b, 12)[0]
    gl = json.loads(b[20:20 + jl])
    off = 20 + jl
    binc = b[off + 8: off + 8 + struct.unpack_from("<I", b, off)[0]] if len(b) > off + 8 else b""
    acc, bvs = gl.get("accessors", []), gl.get("bufferViews", [])
    ncomp = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}

    def read(i):
        a = acc[i]
        dt, norm = _NORM[a["componentType"]]
        bv = bvs[a["bufferView"]]
        k = ncomp[a["type"]]
        item = np.dtype(dt).itemsize
        stride = bv.get("byteStride") or k * item
        start = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
        rows = np.frombuffer(binc, np.uint8, count=stride * (a["count"] - 1) + k * item, offset=start)
        rows = np.lib.stride_tricks.as_strided(rows, (a["count"], k * item), (stride, 1))
        out = np.ascontiguousarray(rows).view(dt).reshape(a["count"], k).astype(np.float64)
        if a.get("normalized") and norm:
            out = np.maximum(out / norm, -1.0)
        return out

    V, F, n0 = [], [], 0

    def walk(ni, parent):
        n = gl["nodes"][ni]
        m = parent @ _node_matrix(n)
        if "mesh" in n:
            for pr in gl["meshes"][n["mesh"]]["primitives"]:
                if pr.get("mode", 4) != 4:
                    continue
                P = read(pr["attributes"]["POSITION"])
                P = P @ m[:3, :3].T + m[:3, 3]
                idx = read(pr["indices"]).astype(np.int64).ravel() if "indices" in pr else np.arange(len(P))
                nonlocal n0
                V.append(P)
                F.append(idx.reshape(-1, 3) + n0)
                n0 += len(P)
        for c in n.get("children", []):
            walk(c, m)

    for ni in gl["scenes"][gl.get("scene", 0)]["nodes"]:
        walk(ni, np.eye(4))
    if not V:
        return None
    return trimesh.Trimesh(np.vstack(V), np.vstack(F), process=True)


def _props_shape(mp: dict):
    """(volume, centroid, inertia per unit density, how) from a manifest part's `mass_props`."""
    return (float(mp["volume_mm3"]), np.asarray(mp["centroid"], float),
            np.asarray(mp["inertia_unit"], float).reshape(3, 3),
            "source mesh" if mp.get("closed", True) else "source mesh (open)")


@lru_cache(maxsize=None)
def _shape(path: str, mtime: float):
    """(volume mm^3, centroid, inertia tensor per unit density) of a mesh file, or None."""
    import trimesh

    try:
        m = read_glb(path) if path.endswith(".glb") else trimesh.load(path, force="mesh", process=True)
    except Exception:
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
    shape = _props_shape(raw["mass_props"]) if raw.get("mass_props") else None
    for key in ("mesh_full", "mesh"):
        if shape is not None:
            break
        mesh = (p.base / raw[key]) if raw.get(key) else None
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
