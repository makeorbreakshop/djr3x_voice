"""Mesh loading with a small on-disk cache (mech/out/cache, gitignored)."""
from __future__ import annotations

import functools
import hashlib
from pathlib import Path

import numpy as np
import trimesh

from .model import MECH, Part

CACHE = MECH / "out" / "cache"


@functools.lru_cache(maxsize=None)
def load_file(path: str) -> trimesh.Trimesh:
    p = Path(path)
    key = hashlib.sha1(f"{p}:{p.stat().st_mtime_ns}".encode()).hexdigest()[:16]
    c = CACHE / f"{p.stem[:40]}-{key}.npz"
    if c.exists():
        d = np.load(c)
        return trimesh.Trimesh(d["v"], d["f"], process=False)
    if p.suffix.lower() in (".step", ".stp"):
        from build123d import import_step
        s = import_step(str(p))
        v, f = s.tessellate(0.05, 0.3)
        m = trimesh.Trimesh(np.array([[q.X, q.Y, q.Z] for q in v]), np.array(f))
    else:
        m = trimesh.load(p, force="mesh")
    m.merge_vertices()
    CACHE.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(c, v=m.vertices, f=m.faces)
    return m


def part_mesh_local(part: Part) -> trimesh.Trimesh:
    if part.real is not None and "kind" in part.real and (part.file is not None or part.generator is not None):
        return real_part(part)
    if part.file is not None:
        return load_file(str(part.file))
    if part.generator is not None:
        return part.generator()
    raise ValueError(f"{part.id}: no mesh source")


def part_mesh(part: Part, T_link: np.ndarray | None = None) -> trimesh.Trimesh:
    """Mesh in the body frame: link motion (pose) x part rest placement."""
    m = part_mesh_local(part).copy()
    T = part.T if T_link is None else T_link @ part.T
    m.apply_transform(T)
    return m


def servo_seat_T(spec: dict, seat: dict) -> np.ndarray:
    """The parametric servo (mech/parts/models.servo: spline top at its origin, shaft +Y, body
    centre toward +X) in a stand-in's frame, mated by features: `seat["at"]` is where the centre of
    the flange's underside goes ("ref": "flange", a servo screwed down on its tabs) or the middle of
    the body on the shaft line ("ref": "body", the dual-shaft servo held by its body), `axis` the
    output shaft's direction and `body` the direction from the shaft to the body's centre."""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from parts.models import CASES

    c = CASES[spec.get("case", "standard")]
    y_ref = (-c["sh"] - c["H"] + c["fh"] - c["ft"] / 2) if seat.get("ref", "flange") == "flange" else (-c["sh"] - c["H"] / 2)
    a = np.asarray(seat["axis"], float)
    b = np.asarray(seat["body"], float)
    T = np.eye(4)
    T[:3, :3] = np.column_stack([b, a, np.cross(b, a)])
    T[:3, 3] = np.asarray(seat["at"], float) - T[:3, :3] @ np.array([c["off"], y_ref, 0.0])
    return T


def real_part(part: Part) -> trimesh.Trimesh:
    """The real (vendor or parametric, mech/parts) part in place of a builder's stand-in: fitted
    onto the stand-in's geometry (cached)."""
    import json
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from parts.library import fit_to_standin, spec_part
    from workbench.geom import cached, _cache_key, _file_sig

    src = _file_sig(Path(part.file)) if part.file is not None else f"gen:{part.id}"
    key = _cache_key("real", src, json.dumps({k: v for k, v in part.real.items() if k != "fit_mm"}, sort_keys=True), 1)

    def make():
        model = spec_part(part.real["kind"], part.real["spec"]).mesh
        standin = load_file(str(part.file)) if part.file is not None else part.generator()
        if part.real.get("seat"):
            # mated by features, not by shape: the principal-axis fit tilts a servo ~18 deg in its
            # stand-in (the spline and flange skew the vertex cloud) and slides it ~6 mm off its seat
            fitted = model.copy()
            fitted.apply_transform(servo_seat_T(part.real["spec"], part.real["seat"]))
            from scipy.spatial import cKDTree
            ps, _ = trimesh.sample.sample_surface(fitted, 3000, seed=4)
            err = float(cKDTree(trimesh.sample.sample_surface(standin, 3000, seed=3)[0]).query(ps)[0].mean())
            return fitted.vertices, fitted.faces, err
        fitted, err = fit_to_standin(standin, model)
        return fitted.vertices, fitted.faces, err

    v, f, err = cached(key, make)
    part.real["fit_mm"] = round(float(err), 2)
    return trimesh.Trimesh(v, f, process=False)
