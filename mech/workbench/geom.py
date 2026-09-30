"""Geometry helpers for assembly modules: STEP/STL/DXF loading (cached), parametric
fasteners, simple goBILDA-style hardware, decimation and fitting."""

from __future__ import annotations

import hashlib
import math
import pickle
from pathlib import Path

import logging

import numpy as np
import trimesh

logging.getLogger("trimesh").setLevel(logging.ERROR)

MECH = Path(__file__).resolve().parents[1]
CACHE = MECH / "out" / ".cache"


def _cache_key(*parts) -> str:
    return hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:16]


def _file_sig(path: Path) -> str:
    st = path.stat()
    return f"{path}:{st.st_size}:{st.st_mtime_ns}"


def code_sig(module_name: str) -> str:
    """A hash of a parametric model's code: its module file, its package's other modules and
    mech/parts' top-level modules (what it can import from mech/parts). Mesh caches key on it,
    so an edited model rebuilds."""
    import importlib

    mod = importlib.import_module(module_name)
    here = Path(mod.__file__).resolve()
    root = Path(__file__).resolve().parents[1] / "parts"
    files = {here} | set(here.parent.glob("*.py")) | set(root.glob("*.py"))
    h = hashlib.sha1()
    for f in sorted(files):
        h.update(f.name.encode())
        h.update(f.read_bytes())
    return h.hexdigest()[:16]


def parametric_mesh(module_name: str, params: dict | None = None):
    """(trimesh, features, params) of a parametric model (make(params) -> build123d Part with
    .features/.params), cached on its code and parameters."""
    import importlib

    import trimesh

    def run():
        from parts.head._common import mesh as b_mesh

        part = importlib.import_module(module_name).make(dict(params or {}))
        m = b_mesh(part)
        return (np.asarray(m.vertices), np.asarray(m.faces), dict(getattr(part, "features", {}) or {}),
                dict(getattr(part, "params", {}) or {}))

    v, f, feats, prm = cached(_cache_key("pmesh", module_name, code_sig(module_name),
                                         sorted((k, str(x)) for k, x in (params or {}).items())), run)
    return trimesh.Trimesh(v, f, process=False), feats, prm


def cached(key: str, fn):
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"{key}.pkl"
    if f.exists():
        try:
            return pickle.loads(f.read_bytes())
        except Exception:
            pass
    v = fn()
    f.write_bytes(pickle.dumps(v))
    return v


# ------------------------------------------------------------------ loaders

def step_leaves(path: Path, tol: float = 0.02, ang: float = 0.12) -> list[tuple[str, str, np.ndarray, np.ndarray]]:
    """Every leaf solid of a STEP assembly, placed by the file's own locations:
    [(path_label, label, vertices, faces)] in the STEP's frame (mm)."""

    def load():
        from build123d import Solid, import_step

        root = import_step(str(path))
        out = []

        def walk(n, trail):
            kids = list(n.children)
            if not kids or isinstance(n, Solid):
                s = n.located(n.global_location)
                v, t = _tessellate(s, tol, ang)
                out.append((f"{trail}/{n.label}", n.label, v, t))
                return
            for c in kids:
                walk(c, f"{trail}/{n.label}")

        walk(root, "")
        return out

    return cached(_cache_key("step", _file_sig(path), tol, ang), load)


def _tessellate(shape, tol, ang):
    """Vertices/faces of a shape; a face that will not mesh is skipped rather than failing the part
    (some vendor STEPs carry one degenerate face)."""
    try:
        v, t = shape.tessellate(tol, ang)
        return np.array([(p.X, p.Y, p.Z) for p in v]).reshape(-1, 3), np.array(t, dtype=np.int64).reshape(-1, 3)
    except Exception:
        vs, ts, n = [], [], 0
        for f in shape.faces():
            try:
                v, t = f.tessellate(tol, ang)
            except Exception:
                continue
            if not len(t):
                continue
            vs.append(np.array([(p.X, p.Y, p.Z) for p in v]))
            ts.append(np.array(t, dtype=np.int64) + n)
            n += len(v)
        return (np.vstack(vs) if vs else np.zeros((0, 3))), (np.vstack(ts) if ts else np.zeros((0, 3), dtype=np.int64))


def step_mesh(path: Path, tol: float = 0.02) -> trimesh.Trimesh:
    """A single-part STEP as one mesh."""
    leaves = step_leaves(path, tol)
    return trimesh.util.concatenate([trimesh.Trimesh(v, f, process=True) for _, _, v, f in leaves])


def stl(path: Path) -> trimesh.Trimesh:
    return trimesh.load(str(path), force="mesh")


def dxf_profile(path: Path, flatten: float = 0.1):
    """Closed outlines and circles of a DXF as a shapely polygon with holes (units: drawing mm)."""
    import ezdxf
    from ezdxf import path as dpath
    from shapely.geometry import Point, Polygon

    doc = ezdxf.readfile(str(path))
    msp = doc.modelspace()
    loops = []
    for e in msp.query("LWPOLYLINE POLYLINE SPLINE"):
        pts = [(p.x, p.y) for p in dpath.make_path(e).flattening(flatten)]
        if len(pts) > 2:
            loops.append(Polygon(pts).buffer(0))
    circles = [(e.dxf.center.x, e.dxf.center.y, e.dxf.radius) for e in msp.query("CIRCLE")]
    outer = max(loops, key=lambda p: p.area)
    poly = outer
    for x, y, r in circles:
        poly = poly.difference(Point(x, y).buffer(r, resolution=32))
    return poly, circles


def extrude(poly, thickness: float) -> trimesh.Trimesh:
    """Extrude a shapely polygon (drawing XY) along +Z by `thickness`, centred on z=0."""
    m = trimesh.creation.extrude_polygon(poly, thickness)
    m.apply_translation([0, 0, -thickness / 2])
    return m


# ------------------------------------------------------------------ fitting

def fit_rigid_2d(src: np.ndarray, dst: np.ndarray):
    """Least-squares rotation + translation mapping 2D points src -> dst (paired).
    Returns (angle rad, t, rms)."""
    cs, cd = src.mean(0), dst.mean(0)
    h = (src - cs).T @ (dst - cd)
    ang = math.atan2(h[0, 1] - h[1, 0], h[0, 0] + h[1, 1])
    r = np.array([[math.cos(ang), -math.sin(ang)], [math.sin(ang), math.cos(ang)]])
    t = cd - r @ cs
    rms = float(np.sqrt(((src @ r.T + t - dst) ** 2).sum(1).mean()))
    return ang, t, rms


def section_holes(mesh: trimesh.Trimesh, axis: int, at: float, rmin=0.8, rmax=6.0, roundness=0.35):
    """Circular loops (centre, radius) in the section of `mesh` normal to `axis` at `at`."""
    n = np.zeros(3)
    n[axis] = 1
    o = np.zeros(3)
    o[axis] = at
    sec = mesh.section(plane_origin=o, plane_normal=n)
    out = []
    if sec is None:
        return out
    for e in sec.discrete:
        c = e.mean(0)
        r = np.linalg.norm(e - c, axis=1)
        if rmin < r.mean() < rmax and r.std() < roundness:
            out.append((c, float(r.mean())))
    return out


def ray_depth(mesh: trimesh.Trimesh, origin, direction) -> list[float]:
    """Distances along a ray at which it crosses the mesh surface (sorted)."""
    locs, _, _ = mesh.ray.intersects_location([origin], [direction])
    if not len(locs):
        return []
    return sorted(float(np.dot(p - origin, direction)) for p in locs)


# ------------------------------------------------------------------ display

def decimate(mesh: trimesh.Trimesh, target: int) -> trimesh.Trimesh:
    if len(mesh.faces) <= target:
        return mesh
    try:
        out = mesh.simplify_quadric_decimation(face_count=target)
        if len(out.faces) > 0:
            return out
    except Exception:
        pass
    return mesh


def sample(mesh: trimesh.Trimesh, spacing: float = 1.5, cap: int = 25000) -> np.ndarray:
    """Surface points at roughly `spacing` mm (for distance checks)."""
    n = int(min(cap, max(300, mesh.area / (spacing * spacing))))
    pts, _ = trimesh.sample.sample_surface_even(mesh, n, seed=1)
    return pts


# ------------------------------------------------------------------ fasteners & hardware

def _to_mesh(shape, tol=0.05) -> trimesh.Trimesh:
    v, t = shape.tessellate(tol, 0.3)
    return trimesh.Trimesh(np.array([(p.X, p.Y, p.Z) for p in v]), np.array(t))


def fastener_mesh(spec: dict) -> trimesh.Trimesh:
    """Display mesh for a fastener spec, head at the origin, pointing +Z (insertion)."""

    def make():
        kind = spec["type"]
        thread = spec.get("thread", "M4")
        d = float(thread[1:])
        length = float(spec.get("length_mm", 10))
        if kind in ("shcs", "bhcs"):
            from bd_warehouse.fastener import ButtonHeadScrew, SocketHeadCapScrew

            cls = SocketHeadCapScrew if kind == "shcs" else ButtonHeadScrew
            std = "iso4762" if kind == "shcs" else "iso7380_1"
            m = _to_mesh(cls(f"{thread}-{ {3: '0.5', 4: '0.7', 5: '0.8'}[int(d)] }", length, std, simple=True))
            m.apply_transform(trimesh.transformations.rotation_matrix(math.pi, [1, 0, 0]))
            return m
        if kind in ("nut", "lock_nut"):
            from bd_warehouse.fastener import HexNut

            m = _to_mesh(HexNut(f"{thread}-0.7", "iso4032", simple=True))
            if kind == "lock_nut":  # nylon ring: a short cylinder on top
                ring = trimesh.creation.cylinder(radius=d * 0.85, height=d * 0.45, sections=24)
                ring.apply_translation([0, 0, m.bounds[1][2] + d * 0.22])
                m = trimesh.util.concatenate([m, ring])
            return m
        if kind == "washer":
            outer = trimesh.creation.annulus(r_min=d / 2 + 0.2, r_max=d * 1.125, height=0.8, sections=32)
            outer.apply_translation([0, 0, 0.4])
            return outer
        if kind == "insert":  # heat-set insert, spec 6 x 6 for M4 (BOM)
            od = float(spec.get("od_mm", 6))
            m = trimesh.creation.annulus(r_min=d / 2, r_max=od / 2, height=length, sections=24)
            m.apply_translation([0, 0, length / 2])
            return m
        if kind == "threaded_rod":
            m = trimesh.creation.cylinder(radius=d / 2, height=length, sections=16)
            m.apply_translation([0, 0, length / 2])
            return m
        raise ValueError(kind)

    return cached(_cache_key("fastener", sorted(spec.items()), 3), make)


def servo_hub_and_arm(radius: float, hub_h=8.0, arm_t=3.0) -> trimesh.Trimesh:
    """goBILDA 1906 lightweight servo hub (32 mm) + a cut-down hub-mount control arm, horn
    frame: axis +Y, arm toward +X, the hub's underside at y=0."""
    hub = trimesh.creation.cylinder(radius=16, height=hub_h, sections=48)
    hub.apply_transform(trimesh.transformations.rotation_matrix(-math.pi / 2, [1, 0, 0]))
    hub.apply_translation([0, hub_h / 2, 0])
    arm = trimesh.creation.box([radius + 6, arm_t, 10])
    arm.apply_translation([(radius + 6) / 2 - 3, hub_h + arm_t / 2, 0])
    tip = trimesh.creation.cylinder(radius=5, height=arm_t, sections=24)
    tip.apply_transform(trimesh.transformations.rotation_matrix(-math.pi / 2, [1, 0, 0]))
    tip.apply_translation([radius, hub_h + arm_t / 2, 0])
    return trimesh.util.concatenate([hub, arm, tip])


def ball_link(length=24.1) -> trimesh.Trimesh:
    """goBILDA 2913 steel ball linkage (female M4, 24.1 mm): ball at the origin, body along +Z."""
    ball = trimesh.creation.icosphere(subdivisions=2, radius=3.2)
    ring = trimesh.creation.cylinder(radius=4.6, height=6.0, sections=24)
    ring.apply_transform(trimesh.transformations.rotation_matrix(math.pi / 2, [0, 1, 0]))
    body = trimesh.creation.cylinder(radius=3.4, height=length - 5, sections=16)
    body.apply_translation([0, 0, 5 + (length - 5) / 2 - 2])
    return trimesh.util.concatenate([ball, ring, body])


def servo_box() -> trimesh.Trimesh:
    """A generic standard-size servo (40 x 20 x 37 mm, 54.8 mm across the flange), output
    spline at the origin pointing +Y, body below, long axis along X, spline 10 mm off centre."""
    body = trimesh.creation.box([40, 36.5, 20])
    body.apply_translation([10, -36.5 / 2 - 1.5, 0])
    flange = trimesh.creation.box([54.8, 2.5, 20])
    flange.apply_translation([10, -9.5, 0])
    spline = trimesh.creation.cylinder(radius=3, height=4, sections=16)
    spline.apply_transform(trimesh.transformations.rotation_matrix(-math.pi / 2, [1, 0, 0]))
    spline.apply_translation([0, 0.5, 0])
    return trimesh.util.concatenate([body, flange, spline])


def align(z_to, origin) -> np.ndarray:
    """A matrix whose +Z points along `z_to`, positioned at `origin`."""
    z = np.asarray(z_to, float)
    z /= np.linalg.norm(z)
    ref = np.array([0, 1, 0]) if abs(z[1]) < 0.9 else np.array([1, 0, 0])
    x = np.cross(ref, z)
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    m = np.eye(4)
    m[:3, 0], m[:3, 1], m[:3, 2], m[:3, 3] = x, y, z, origin
    return m
