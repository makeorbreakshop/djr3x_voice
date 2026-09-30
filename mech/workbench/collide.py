"""Geometry engine for the test suite: FCL BVHs built once per part, per-pose transforms only,
a vectorised AABB broad phase, and on-disk caches keyed by geometry hash, so a warm re-run of
an unchanged assembly does no mesh work at all.

    scene = Scene(bodies)            # {id: (mesh in the assembly frame at zero pose)}
    scene.distance(a, Ma, b, Mb)     # minimum distance (0 when touching or overlapping)
    scene.colliding(pairs, mats)     # pairs whose meshes intersect
"""

from __future__ import annotations

import hashlib
import pickle
from pathlib import Path

import numpy as np
import trimesh

CACHE = Path(__file__).resolve().parents[1] / "out" / ".cache" / "suite"


def mesh_hash(m: trimesh.Trimesh) -> str:
    h = hashlib.sha1()
    h.update(np.round(m.vertices, 4).astype(np.float32).tobytes())
    h.update(np.asarray(m.faces, np.int32).tobytes())
    return h.hexdigest()[:16]


def key_of(*parts) -> str:
    h = hashlib.sha1()
    for p in parts:
        if isinstance(p, np.ndarray):
            h.update(np.round(p, 5).astype(np.float64).tobytes())
        else:
            h.update(repr(p).encode())
    return h.hexdigest()[:20]


class DiskCache:
    def __init__(self, name: str):
        CACHE.mkdir(parents=True, exist_ok=True)
        self.path = CACHE / f"{name}.pkl"
        try:
            self.data = pickle.loads(self.path.read_bytes())
        except Exception:
            self.data = {}
        self.dirty = False

    def get(self, k):
        return self.data.get(k)

    def put(self, k, v):
        self.data[k] = v
        self.dirty = True

    def save(self):
        if self.dirty:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_bytes(pickle.dumps(self.data))
            tmp.replace(self.path)
            self.dirty = False


class Scene:
    def __init__(self, meshes: dict[str, trimesh.Trimesh]):
        self.meshes = meshes
        self.hash = {k: mesh_hash(m) for k, m in meshes.items()}
        self.ids = list(meshes)
        self.index = {k: i for i, k in enumerate(self.ids)}
        corners = []
        for k in self.ids:
            lo, hi = meshes[k].bounds
            corners.append(np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]))
        self.corners = np.stack(corners)  # (N, 8, 3)
        self._fcl = {}

    def obj(self, k):
        import fcl

        if k not in self._fcl:
            m = self.meshes[k]
            g = fcl.BVHModel()
            g.beginModel(len(m.vertices), len(m.faces))
            g.addSubModel(np.asarray(m.vertices, float), np.asarray(m.faces, np.int32))
            g.endModel()
            self._fcl[k] = fcl.CollisionObject(g, fcl.Transform())
        return self._fcl[k]

    @staticmethod
    def _set(o, M):
        import fcl

        o.setTransform(fcl.Transform(np.asarray(M[:3, :3], float), np.asarray(M[:3, 3], float)))

    def distance(self, a, Ma, b, Mb) -> float:
        import fcl

        oa, ob = self.obj(a), self.obj(b)
        self._set(oa, Ma)
        self._set(ob, Mb)
        res = fcl.DistanceResult()
        return max(0.0, float(fcl.distance(oa, ob, fcl.DistanceRequest(), res)))

    def collide(self, a, Ma, b, Mb) -> bool:
        import fcl

        oa, ob = self.obj(a), self.obj(b)
        self._set(oa, Ma)
        self._set(ob, Mb)
        return fcl.collide(oa, ob, fcl.CollisionRequest(), fcl.CollisionResult()) > 0

    def aabbs(self, mats: dict[str, np.ndarray]):
        """World AABBs (N, 2, 3) of every body for one pose."""
        M = np.stack([mats[k] for k in self.ids])  # (N, 4, 4)
        w = np.einsum("nij,nkj->nki", M[:, :3, :3], self.corners) + M[:, None, :3, 3]
        return np.stack([w.min(1), w.max(1)], axis=1)

    def gaps(self, boxes, ia, ib):
        """AABB separation (lower bound on the distance) for index pairs, vectorised."""
        lo_a, hi_a = boxes[ia, 0], boxes[ia, 1]
        lo_b, hi_b = boxes[ib, 0], boxes[ib, 1]
        g = np.maximum(lo_a - hi_b, lo_b - hi_a)
        return np.linalg.norm(np.maximum(g, 0.0), axis=1)
