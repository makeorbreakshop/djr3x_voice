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
    if part.real is not None and (part.file is not None or part.generator is not None):
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
        fitted, err = fit_to_standin(standin, model)
        return fitted.vertices, fitted.faces, err

    v, f, err = cached(key, make)
    part.real["fit_mm"] = round(float(err), 2)
    return trimesh.Trimesh(v, f, process=False)
