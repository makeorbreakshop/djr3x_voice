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
