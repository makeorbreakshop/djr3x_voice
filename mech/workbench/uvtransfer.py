"""Texture transfer: the Original rig's baked paint (sim/web/public/model/r3x.glb, built by sim/model/build_r3x.py
from the Oga's Cantina references) onto our display meshes, so Build's Exterior look and the assembly video show
the real weathered finish on parts the Original does not have as such (Hunter's head) or has merged.

For every painted part seen from outside, each face of its display GLBs (overview and full detail) takes UVs from
the Original's surface after the part's group is registered onto the Original (a trimmed ICP from where the
manifest places it). Groups: the head's parts onto the head atlas, the body's onto the body atlas, and each arm
link (ARMS) on its own onto the Original's nodes of the same arm - the Original poses its arms its own way (the
poker and throttle arms sit at another turn of their rings), so they take a coarse search over the ring's turn
and the link's joint first. A body part still below MIN_COVER (an arm's hub or shaft on a ring) tries the arms'
registrations and keeps the best. Per face corner, not per vertex: the face's centre picks the nearest Original
triangle - in the same atlas, with the normals agreeing - and so one UV island; each corner takes the UV of its
own nearest point when that lies on the same island, else the centre triangle's UV mapping carried out to the
corner. Per-vertex UVs would let a face whose corners land on different islands smear the whole atlas between
them.

Nearest points are per region: each part searches only the Original's triangles inside its own registered box
(grown by MARGIN_MM), split into pieces no larger than SPLIT_MM, through k-d trees over the pieces' centres
(Region). trimesh's ProximityQuery over the whole Original took ~2 ms a point and GBs of memory, and a droid run
needs a few million points: the registration alone (4000 points x 30 iterations x 2 atlases) never finished.

Written next to each GLB as `<mesh>.uv.bin` (float32 u, v per face corner, in the GLB's index order) and
summarised in `uvtransfer.json` in each mesh folder (the folder above `parts/`, which is where the viewer looks:
part id -> atlas, coverage = share of faces that found the Original); the viewer samples the Original's own
textures with them. Parts below MIN_COVER are listed as `textured: false` (they keep the weathering shader).
A part is redone only when its GLBs, the Original or its registered placement changed
(`<asm>/.uvtransfer_state.json`); `--only <assembly or part id>` limits a run, `--force` redoes it.

The UVs derive from kit meshes: they live in mech/out (gitignored), like the display meshes.

    node sim/web/scripts/export_original_surfaces.mjs     # the Original's surfaces -> out/.cache/
    .venv/bin/python -m workbench.uvtransfer hunter_head r3x_droid [--only lower_ring] [--force]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import resource
import struct
import sys
import time
from pathlib import Path

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree

MECH = Path(__file__).resolve().parents[1]
OUT = MECH / "out"
SURF = OUT / ".cache" / "original_surfaces.json"
MAX_MM = 12.0  # farther than this from the Original: not its surface
NORMAL_DOT = 0.3  # the normals must agree at least this much
MIN_COVER = 0.6  # a part takes the texture when this share of its faces found the Original
MARGIN_MM = 30.0  # a part's crop of the Original: its registered box grown by this
SEARCH_MM = MAX_MM  # the large triangles are searched out to this (no face farther away takes the texture)
REG_SAMPLES = 4000
REG_MARGIN_MM = 60.0
REG_INLIER_MM = 25.0  # registration pairs farther than this are outliers (an arm posed differently, an extra)
REG_ITERS = 30
REG_SPLIT_MM = 8.0  # the registration's pieces: coarser (the whole body at once), a pair per point is enough
REG_FIT_MM = 3.0  # a registered point this close to the Original counts as on it
# The Original rig poses its arms its own way (another turn of the rings, other joint angles): the parts of an arm
# link are registered on their own, onto the Original's nodes of the same arm (sim/model/build_r3x.py names
# them after the joint that moves them), and keep that when it fits better than the body's registration.
ARMS = ("poker", "throttle", "hero")
HEAD_ASSEMBLIES = ("head_r3x", "hunter_head")  # parts under these take the head atlas
HEAD_ATLAS, BODY_ATLAS = "r3x_head_basecolor", "r3x_body_basecolor"
ALGO = "uvtransfer-7"  # bump when the projection changes: every part is redone
SPLIT_MM = 3.0  # the Original's triangles are searched in pieces of at most this radius (mm)
_BANDS = (0.5, 1.0, 2.0, 4.0)  # piece radius bands (mm), up to the split
KNN_K = 8  # nearest centres tried per band
_CHUNK = 4096  # query points per batch: bounds the candidate pairs held at once
_WIDE_ROWS = 256  # rows per ball query in the wide search


def rss_mb() -> float:
    """Peak resident set of this process (macOS reports bytes, Linux KiB)."""
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return r / 1e6 if sys.platform == "darwin" else r / 1e3


# ---- our display GLBs ------------------------------------------------------------------------------------------

_COMP = {5120: (np.int8, 127.0), 5121: (np.uint8, 255.0), 5122: (np.int16, 32767.0), 5123: (np.uint16, 65535.0),
         5125: (np.uint32, None), 5126: (np.float32, None)}
_NCOMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}


def _node_matrix(n: dict) -> np.ndarray:
    if "matrix" in n:
        return np.asarray(n["matrix"], float).reshape(4, 4).T
    m = np.eye(4)
    m[:3, :3] = _quat(n.get("rotation", [0, 0, 0, 1])) @ np.diag(n.get("scale", [1, 1, 1]))
    m[:3, 3] = n.get("translation", [0, 0, 0])
    return m


def read_glb(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """A display GLB's triangles (V, F) in the file's frame and the file's index order (which the .uv.bin
    follows): positions dequantized (normalized int16 under KHR_mesh_quantization, as rigsync reads them since
    ab00a45), every node's transform applied, primitives concatenated in document order. Never welded."""
    b = Path(path).read_bytes()
    if b[:4] != b"glTF":
        raise ValueError(f"{path}: not a GLB")
    jl = struct.unpack_from("<I", b, 12)[0]
    gl = json.loads(b[20:20 + jl])
    off = 20 + jl
    binc = b[off + 8: off + 8 + struct.unpack_from("<I", b, off)[0]] if len(b) > off + 8 else b""
    acc, bvs = gl.get("accessors", []), gl.get("bufferViews", [])

    def read(i: int) -> np.ndarray:
        a = acc[i]
        dt, norm = _COMP[a["componentType"]]
        bv = bvs[a["bufferView"]]
        k, item = _NCOMP[a["type"]], np.dtype(dt).itemsize
        stride = bv.get("byteStride") or k * item
        start = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
        rows = np.frombuffer(binc, np.uint8, count=stride * (a["count"] - 1) + k * item, offset=start)
        rows = np.lib.stride_tricks.as_strided(rows, (a["count"], k * item), (stride, 1))
        out = np.ascontiguousarray(rows).view(dt).reshape(a["count"], k)
        if norm and a.get("normalized"):
            return np.maximum(out.astype(np.float64) / norm, -1.0)
        return out

    Vs, Fs, n0 = [], [], 0

    def walk(ni: int, parent: np.ndarray):
        nonlocal n0
        n = gl["nodes"][ni]
        m = parent @ _node_matrix(n)
        if "mesh" in n:
            for pr in gl["meshes"][n["mesh"]]["primitives"]:
                if pr.get("mode", 4) != 4:
                    continue
                P = read(pr["attributes"]["POSITION"]).astype(np.float64)
                idx = read(pr["indices"]).astype(np.int64).ravel() if "indices" in pr else np.arange(len(P))
                Vs.append(P @ m[:3, :3].T + m[:3, 3])
                Fs.append(idx.reshape(-1, 3) + n0)
                n0 += len(P)
        for c in n.get("children", []):
            walk(c, m)

    for ni in gl["scenes"][gl.get("scene", 0)]["nodes"]:
        walk(ni, np.eye(4))
    if not Vs:
        return np.zeros((0, 3)), np.zeros((0, 3), np.int64)
    return np.vstack(Vs), np.vstack(Fs)


# ---- geometry --------------------------------------------------------------------------------------------------

def _dot(a, b):
    return np.einsum("ij,ij->i", a, b)


def closest_on_triangles(p: np.ndarray, a: np.ndarray, b: np.ndarray, c: np.ndarray) -> np.ndarray:
    """The nearest point of each triangle (a, b, c)[i] to p[i] (Ericson, Real-Time Collision Detection 5.1.5),
    vectorized: the regions are applied last-to-first so the first that holds wins."""
    ab, ac, ap = b - a, c - a, p - a
    d1, d2 = _dot(ab, ap), _dot(ac, ap)
    bp = p - b
    d3, d4 = _dot(ab, bp), _dot(ac, bp)
    cp = p - c
    d5, d6 = _dot(ab, cp), _dot(ac, cp)
    va, vb, vc = d3 * d6 - d5 * d4, d5 * d2 - d1 * d6, d1 * d4 - d3 * d2
    with np.errstate(divide="ignore", invalid="ignore"):
        den = va + vb + vc
        v, w = np.where(den != 0, vb / den, 1 / 3), np.where(den != 0, vc / den, 1 / 3)
        out = a + ab * v[:, None] + ac * w[:, None]  # inside
        m = (va <= 0) & (d4 - d3 >= 0) & (d5 - d6 >= 0)  # edge bc
        t = (d4 - d3) / ((d4 - d3) + (d5 - d6))
        out[m] = (b + (c - b) * t[:, None])[m]
        m = (vb <= 0) & (d2 >= 0) & (d6 <= 0)  # edge ac
        t = d2 / (d2 - d6)
        out[m] = (a + ac * t[:, None])[m]
        m = (d6 >= 0) & (d5 <= d6)  # vertex c
        out[m] = c[m]
        m = (vc <= 0) & (d1 >= 0) & (d3 <= 0)  # edge ab
        t = d1 / (d1 - d3)
        out[m] = (a + ab * t[:, None])[m]
        m = (d3 >= 0) & (d4 <= d3)  # vertex b
        out[m] = b[m]
        m = (d1 <= 0) & (d2 <= 0)  # vertex a
        out[m] = a[m]
    return np.nan_to_num(out, nan=np.inf)


def barycentric(p: np.ndarray, a: np.ndarray, b: np.ndarray, c: np.ndarray) -> np.ndarray:
    """Barycentric coordinates of p[i] in its triangle's plane - also outside the triangle (a mapping carried
    past its edges). A degenerate triangle gives its centre."""
    v0, v1, v2 = b - a, c - a, p - a
    d00, d01, d11, d20, d21 = _dot(v0, v0), _dot(v0, v1), _dot(v1, v1), _dot(v2, v0), _dot(v2, v1)
    den = d00 * d11 - d01 * d01
    ok = np.abs(den) > 1e-18
    sden = np.where(ok, den, 1.0)
    v = np.where(ok, (d11 * d20 - d01 * d21) / sden, 1 / 3)
    w = np.where(ok, (d00 * d21 - d01 * d20) / sden, 1 / 3)
    return np.stack([1 - v - w, v, w], axis=1)


def face_normals(V: np.ndarray, F: np.ndarray) -> np.ndarray:
    n = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]])
    ln = np.linalg.norm(n, axis=1)
    return n / np.where(ln > 0, ln, 1.0)[:, None]


def _scatter_min(n: int, qi: np.ndarray, key: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per query 0..n-1: the smallest key among its pairs and that pair's index (-1 where it has none). The
    pairs come grouped by query (qi non-decreasing), as the searches below emit them."""
    best = np.full(n, np.inf)
    arg = np.full(n, -1, np.int64)
    if len(qi):
        starts = np.flatnonzero(np.r_[True, qi[1:] != qi[:-1]])
        mins = np.minimum.reduceat(key, starts)
        hit = np.flatnonzero(key == np.repeat(mins, np.diff(np.r_[starts, len(qi)])))
        first = hit[np.r_[True, qi[hit][1:] != qi[hit][:-1]]]
        best[qi[starts]] = mins
        arg[qi[first]] = first
    return best, arg


class Atlas:
    """One texture atlas of the Original: its triangles (mm, droid frame), UVs, UV islands, normals and a
    bounding sphere per triangle (centre, radius) for the region search."""

    def __init__(self, name: str, P: np.ndarray, U: np.ndarray, F: np.ndarray, node: np.ndarray | None = None,
                 node_names: list[str] | None = None):
        self.name, self.P, self.U, self.F = name, P, U, F
        self.node = np.zeros(len(F), np.int32) if node is None else node  # per triangle: the rig node it is on
        self.node_names = node_names or [""]
        T = P[F]
        self.N = face_normals(P, F)
        self.lo, self.hi = T.min(axis=1), T.max(axis=1)
        self.cen = T.mean(axis=1)
        self.rad = np.linalg.norm(T - self.cen[:, None], axis=2).max(axis=1)
        # UV islands: glTF splits vertices at seams, so faces sharing a vertex share an island
        nf = len(F)
        g = coo_matrix((np.ones(3 * nf, np.int8), (np.repeat(np.arange(nf), 3), nf + F.ravel())),
                       shape=(nf + len(P), nf + len(P)))
        self.island = connected_components(g, directed=False)[1][:nf]

    def region(self, lo, hi, nodes: str | None = None, split: float = SPLIT_MM) -> "Region":
        """The triangles whose boxes meet [lo, hi] (and, with `nodes`, on the rig nodes named with that prefix)."""
        sel = np.all(self.hi >= np.asarray(lo), axis=1) & np.all(self.lo <= np.asarray(hi), axis=1)
        if nodes is not None:
            sel &= np.isin(self.node, [i for i, n in enumerate(self.node_names) if n.startswith(nodes)])
        return Region(self, np.nonzero(sel)[0], split)

    def has_nodes(self, prefix: str) -> bool:
        return any(n.startswith(prefix) for n in self.node_names)

    def uv_at(self, tri: np.ndarray, pts: np.ndarray) -> np.ndarray:
        f = self.F[tri]
        bary = barycentric(pts, self.P[f[:, 0]], self.P[f[:, 1]], self.P[f[:, 2]])
        return np.einsum("ij,ijk->ik", bary, self.U[f])


def _subdivision(n: int) -> np.ndarray:
    """(n*n, 3, 3) barycentric weights of the n*n congruent sub-triangles of a triangle split n ways per edge."""
    tris = []
    for i in range(n):
        for j in range(n - i):
            tris.append([(i, j), (i + 1, j), (i, j + 1)])
            if i + j <= n - 2:
                tris.append([(i + 1, j), (i + 1, j + 1), (i, j + 1)])
    ij = np.asarray(tris, float) / n  # (n*n, 3 corners, (i, j)) -> weights of b and c
    return np.stack([1 - ij[..., 0] - ij[..., 1], ij[..., 0], ij[..., 1]], axis=-1)


class Region:
    """Nearest points on a crop of an atlas. A triangle's nearest point is found through its centre, which is
    only reliable while triangles are small: the Original's large ones (flat panels up to ~200 mm) are split into
    congruent pieces of at most SPLIT_MM radius, which keep their parent's identity (UVs, normal, island). The
    pieces are banded by size, and each band's KNN_K nearest centres are tried - a triangle not among them is
    at least the band's k-th centre distance less its radius away, so a band that cannot beat the best so far
    costs nothing past its k-d tree query."""

    def __init__(self, atlas: Atlas, tris: np.ndarray, split: float = SPLIT_MM):
        self.a = atlas
        self.n = len(tris)
        if not self.n:
            return
        f = atlas.F[tris]
        A, B, C = atlas.P[f[:, 0]], atlas.P[f[:, 1]], atlas.P[f[:, 2]]
        n = np.maximum(1, np.ceil(atlas.rad[tris] / split)).astype(int)
        As, Bs, Cs, par = [A[n == 1]], [B[n == 1]], [C[n == 1]], [tris[n == 1]]
        for k in np.unique(n[n > 1]):
            sel = n == k
            w = _subdivision(int(k))  # (k*k, 3, 3)
            abc = np.stack([A[sel], B[sel], C[sel]], axis=1)  # (m, 3, 3)
            sub = np.einsum("scv,mvx->mscx", w, abc).reshape(-1, 3, 3)
            As.append(sub[:, 0]), Bs.append(sub[:, 1]), Cs.append(sub[:, 2])
            par.append(np.repeat(tris[sel], len(w)))
        self.A, self.B, self.C = np.concatenate(As), np.concatenate(Bs), np.concatenate(Cs)
        self.tris = np.concatenate(par)  # piece -> the atlas triangle it is part of
        self.N = atlas.N[self.tris]
        cen = (self.A + self.B + self.C) / 3
        rad = np.sqrt(np.max([((self.A - cen) ** 2).sum(1), ((self.B - cen) ** 2).sum(1), ((self.C - cen) ** 2).sum(1)], axis=0))
        self.bands = []  # (largest radius, piece indices, k-d tree of their centres)
        lo = -1.0
        for hi in [b for b in _BANDS if b < split] + [split + 1e-6]:
            idx = np.nonzero((rad > lo) & (rad <= hi))[0]
            lo = hi
            if len(idx):
                self.bands.append((float(rad[idx].max()), idx, cKDTree(cen[idx])))

    def closest(self, Q: np.ndarray, nrm: np.ndarray | None = None, limit: float = SEARCH_MM):
        """(point, distance, atlas triangle) nearest each query. With `nrm`, among the triangles whose normals
        agree (either side), falling back to the nearest of any when none within `limit` does. Empty region:
        distance inf, triangle -1."""
        nq = len(Q)
        pt, dist, tri = np.zeros((nq, 3)), np.full(nq, np.inf), np.full(nq, -1, np.int64)
        if not self.n or not nq:
            return pt, dist, tri
        for s in range(0, nq, _CHUNK):
            q = Q[s:s + _CHUNK]
            m = len(q)
            nn = None if nrm is None else nrm[s:s + _CHUNK]
            best = {"any": [np.full(m, np.inf), np.zeros((m, 3)), np.full(m, -1, np.int64)]}
            if nn is not None:
                best["ok"] = [np.full(m, np.inf), np.zeros((m, 3)), np.full(m, -1, np.int64)]

            def take(qa, ta):
                p = closest_on_triangles(q[qa], self.A[ta], self.B[ta], self.C[ta])
                d = np.linalg.norm(p - q[qa], axis=1)
                keys = [("any", d)]
                if nn is not None:
                    keys.append(("ok", np.where(np.abs(_dot(self.N[ta], nn[qa])) >= NORMAL_DOT, d, np.inf)))
                for name, dd in keys:
                    bd, bp, bt = best[name]
                    md, marg = _scatter_min(m, qa, dd)
                    better = md < bd
                    bd[better], bp[better], bt[better] = md[better], p[marg[better]], ta[marg[better]]

            def search(rows, k_want):
                for rmax, idx, tree in self.bands:
                    k = min(k_want, len(idx))
                    dk, ii = tree.query(q[rows], k=k, workers=-1)
                    ii, dk = np.asarray(ii).reshape(len(rows), -1), np.asarray(dk).reshape(len(rows), -1)
                    # only the candidates that can still beat the best so far (a centre less its radius bounds a piece)
                    cut = best["any"][0][rows]
                    if nn is not None:
                        cut = np.maximum(cut, np.minimum(best["ok"][0][rows], limit))
                    r, c = np.nonzero(dk - rmax < cut[:, None])
                    if len(r):
                        take(rows[r], idx[ii[r, c]])

            search(np.arange(m), KNN_K)
            if nn is not None:
                # near the Original but none of the nearest pieces agree (beside a wall of small triangles, the
                # surface it shares a normal with a few mm further): every agreeing piece within the limit
                wide = np.flatnonzero((best["ok"][0] > limit) & (best["any"][0] <= limit))
                for rmax, idx, tree in self.bands if len(wide) else ():
                    for w0 in range(0, len(wide), _WIDE_ROWS):  # a few rows at a time: a ball can hold thousands
                        rows = wide[w0:w0 + _WIDE_ROWS]
                        lists = tree.query_ball_point(q[rows], limit + rmax, workers=-1, return_sorted=False)
                        lens = np.fromiter((len(x) for x in lists), np.int64, count=len(rows))
                        if not lens.sum():
                            continue
                        qa = np.repeat(rows, lens)
                        ta = idx[np.concatenate([np.asarray(x, np.int64) for x in lists if len(x)])]
                        del lists
                        keep = np.abs(_dot(self.N[ta], nn[qa])) >= NORMAL_DOT
                        qa, ta = qa[keep], ta[keep]
                        for c0 in range(0, len(qa), 16 * _CHUNK):  # bounded pair batches
                            take(qa[c0:c0 + 16 * _CHUNK], ta[c0:c0 + 16 * _CHUNK])
            bd, bp, bt = best["any"]
            if nn is not None:  # the nearest agreeing one within the limit, else the nearest of any
                od, op, ot = best["ok"]
                use = od <= limit
                bd, bp, bt = np.where(use, od, bd), np.where(use[:, None], op, bp), np.where(use, ot, bt)
            pt[s:s + m], dist[s:s + m] = bp, bd
            tri[s:s + m] = np.where(bt >= 0, self.tris[np.maximum(bt, 0)], -1)
        return pt, dist, tri


class Original:
    def __init__(self, path: Path = SURF):
        raw = Path(path).read_bytes()
        self.sig = hashlib.sha1(raw).hexdigest()[:16]
        d = json.loads(raw)
        del raw
        acc: dict[str, dict] = {}
        for p in d["prims"]:
            a = acc.setdefault(p["atlas"], {"P": [], "U": [], "F": [], "node": [], "names": [], "n": 0})
            P = np.asarray(p["P"], float).reshape(-1, 3)
            F = np.asarray(p["F"], np.int64).reshape(-1, 3)
            if p.get("node", "") not in a["names"]:
                a["names"].append(p.get("node", ""))
            a["P"].append(P)
            a["U"].append(np.asarray(p["U"], float).reshape(-1, 2))
            a["F"].append(F + a["n"])
            a["node"].append(np.full(len(F), a["names"].index(p.get("node", "")), np.int32))
            a["n"] += len(P)
        del d
        self.atlas = {k: Atlas(k, np.concatenate(a["P"]), np.concatenate(a["U"]), np.concatenate(a["F"]),
                               np.concatenate(a["node"]), a["names"]) for k, a in acc.items()}


def project(atlas: Atlas, V: np.ndarray, F: np.ndarray, region: Region | None = None):
    """Per face corner: uv (len(F)*3, 2) float32; per face: ok (found the Original, normals agreeing)."""
    if region is None:
        region = atlas.region(V.min(axis=0) - MARGIN_MM, V.max(axis=0) + MARGIN_MM)
    if not region.n or not len(F):
        return np.zeros((3 * len(F), 2), np.float32), np.zeros(len(F), bool)
    C = V[F].mean(axis=1)
    n = face_normals(V, F)
    _cpt, cdist, ctri = region.closest(C, n, limit=MAX_MM)
    agree = np.abs(_dot(atlas.N[ctri], n))
    ok = (cdist <= MAX_MM) & (agree >= NORMAL_DOT)
    vpt, _vd, vtri = region.closest(V, None, limit=SEARCH_MM)
    vuv = atlas.uv_at(vtri, vpt)
    corner_v = F.reshape(-1)
    corner_t = np.repeat(ctri, 3)
    same = atlas.island[vtri[corner_v]] == atlas.island[corner_t]
    uv = vuv[corner_v].copy()
    out = ~same
    if out.any():  # the centre triangle's mapping, carried to the corner (barycentrics past the triangle)
        uv[out] = atlas.uv_at(corner_t[out], V[corner_v[out]])
    return uv.astype(np.float32), ok


def kabsch(X: np.ndarray, Y: np.ndarray) -> np.ndarray:
    """The rigid 4x4 taking X onto Y (least squares, no reflection)."""
    mx, my = X.mean(axis=0), Y.mean(axis=0)
    H = (X - mx).T @ (Y - my)
    U, _S, Vt = np.linalg.svd(H)
    D = np.diag([1, 1, np.sign(np.linalg.det(Vt.T @ U.T))])
    R = Vt.T @ D @ U.T
    T = np.eye(4)
    T[:3, :3], T[:3, 3] = R, my - R @ mx
    return T


def register(atlas: Atlas, W: np.ndarray, iters: int = REG_ITERS, init: np.ndarray | None = None,
             region: Region | None = None, shift_first: int = 5) -> tuple[np.ndarray, float, float]:
    """(T, rms of the inliers mm, inlier share): a trimmed ICP of the points W onto the atlas cropped to their
    box (or onto `region`), from `init` - translation only for the first `shift_first` iterations, then rigid."""
    if region is None:
        region = atlas.region(W.min(axis=0) - REG_MARGIN_MM, W.max(axis=0) + REG_MARGIN_MM, split=REG_SPLIT_MM)
    T = np.eye(4) if init is None else init.copy()
    rms, share = math.inf, 0.0
    for it in range(iters):
        X = W @ T[:3, :3].T + T[:3, 3]
        pt, d, _ = region.closest(X, None, limit=REG_INLIER_MM)
        keep = d < min(REG_INLIER_MM, max(3 * float(np.median(d)), 2.0))
        if keep.sum() < 10:
            break
        rms, share = float(np.sqrt(np.mean(d[keep] ** 2))), float(keep.mean())
        if it < shift_first:
            step = np.eye(4)
            step[:3, 3] = (pt[keep] - X[keep]).mean(axis=0)
        else:
            step = kabsch(X[keep], pt[keep])
        T = step @ T
        if np.linalg.norm(step[:3, 3]) < 1e-3 and np.abs(step[:3, :3] - np.eye(3)).max() < 1e-6 and it >= shift_first:
            break
    return T, rms, share


def _rot(axis, deg: float, pivot=(0.0, 0.0, 0.0)) -> np.ndarray:
    """The 4x4 rotation by `deg` about `axis` through `pivot`."""
    k = np.asarray(axis, float) / np.linalg.norm(axis)
    t = math.radians(deg)
    K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    R = np.eye(3) + math.sin(t) * K + (1 - math.cos(t)) * K @ K
    T = np.eye(4)
    T[:3, :3], T[:3, 3] = R, np.asarray(pivot) - R @ np.asarray(pivot)
    return T


def register_arm(atlas: Atlas, W: np.ndarray, nodes: str, joint: dict | None) -> tuple[np.ndarray, float, float]:
    """An arm link onto the Original's own arm (its rig nodes `nodes*`), which the Original poses its own way: a
    coarse search over the rings' turn (about +Y through the droid's axis) and the link's joint, scored by the
    share of points that land on the arm, then the best few refined by the trimmed ICP."""
    region = atlas.region(W.min(axis=0) - 1e4, W.max(axis=0) + 1e4, nodes=nodes, split=REG_SPLIT_MM)
    if not region.n:
        return np.eye(4), math.inf, 0.0
    probe = W[np.random.default_rng(2).choice(len(W), min(400, len(W)), replace=False)]
    swings = [0.0] if joint is None else list(np.arange(-60.0, 61.0, 15.0))
    Ts = [_rot([0, 1, 0], theta) @ (np.eye(4) if joint is None else _rot(joint["axis"], phi, joint["pivot"]))
          for phi in swings for theta in np.arange(0.0, 360.0, 10.0)]
    X = np.concatenate([probe @ T[:3, :3].T + T[:3, 3] for T in Ts])  # every candidate in one search
    _, d0, _ = region.closest(X, None, limit=REG_INLIER_MM)
    cands = [(float(s), T) for s, T in zip(np.mean(d0.reshape(len(Ts), -1) < 6.0, axis=1), Ts)]
    cands.sort(key=lambda c: -c[0])
    best = (np.eye(4), math.inf, 0.0)
    for _score, T0 in cands[:6]:
        T, rms, _share = register(atlas, probe, iters=REG_ITERS, init=T0, region=region, shift_first=3)
        _, d, _ = region.closest(probe @ T[:3, :3].T + T[:3, 3], None, limit=REG_INLIER_MM)
        fit = float(np.mean(d < REG_FIT_MM))
        if fit > best[2]:
            best = (T, rms, fit)
    return best


def fit_share(atlas: Atlas, W: np.ndarray, T: np.ndarray) -> float:
    """The share of the points W within REG_FIT_MM of the atlas once moved by T."""
    X = W @ T[:3, :3].T + T[:3, 3]
    region = atlas.region(X.min(axis=0) - REG_MARGIN_MM, X.max(axis=0) + REG_MARGIN_MM, split=REG_SPLIT_MM)
    _, d, _ = region.closest(X, None, limit=REG_INLIER_MM)
    return float(np.mean(d < REG_FIT_MM))


# ---- the manifest ----------------------------------------------------------------------------------------------

def _tree(asm_dir: Path) -> dict:
    return json.loads((asm_dir / "manifest.json").read_text())["root"]


def _quat(q):
    x, y, z, w = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def _parts(root: dict, M=np.eye(4), path: tuple = ()):
    """(part, the part's frame -> droid frame matrix, the ids of the assemblies above it) for every part in the
    tree (rest pose). A referenced assembly (Hunter's head) is transferred on its own."""
    for p in root.get("parts", []):
        T = np.eye(4)
        tr = p.get("transform") or {}
        if tr.get("q"):
            T[:3, :3] = _quat(tr["q"])
        T[:3, 3] = tr.get("t", [0, 0, 0])
        yield p, M @ T, path
    for c in root.get("children", []):
        if "ref" in c:
            continue
        mt = (c.get("mount") or {}).get("transform") or {}
        T = np.eye(4)
        if mt.get("q"):
            T[:3, :3] = _quat(mt["q"])
        T[:3, 3] = mt.get("t", [0, 0, 0])
        yield from _parts(c, M @ T, path + (c.get("id"),))


def _joints(root: dict):
    """Every joint in the tree (pivots and axes are in the droid frame at the zero pose, SCHEMA.md)."""
    yield from root.get("joints") or []
    for c in root.get("children", []):
        if "ref" not in c:
            yield from _joints(c)


def _exterior(p: dict) -> bool:
    f = p.get("finish") or {}
    return bool(f.get("paint")) and f["paint"] not in ("none",) and (p.get("exposed") or p.get("class") == "shell" or f.get("kit"))


def _folder(mesh: str) -> str:
    """The mesh's folder as the viewer reads it: everything before `parts/` (workbench.ts loadUvSummary)."""
    i = mesh.find("parts/")
    return mesh[:i] if i == 0 or (i > 0 and mesh[i - 1] == "/") else str(Path(mesh).parent) + "/"


def _xf(M: np.ndarray, V: np.ndarray) -> np.ndarray:
    return V @ M[:3, :3].T + M[:3, 3]


def _sha(*parts) -> str:
    h = hashlib.sha1()
    for p in parts:
        h.update(p if isinstance(p, bytes) else str(p).encode())
        h.update(b"\0")
    return h.hexdigest()[:16]


def _project_files(a: Atlas, meshes: list, Mw: np.ndarray) -> tuple[list, float, int]:
    """(uv per mesh, coverage = the worst mesh's share of faces on the Original, region triangles): a part's
    meshes (overview, full) placed by Mw, against one crop of the atlas around the first."""
    uvs, cover, region = [], [], None
    for V, F in meshes:
        W = _xf(Mw, V)
        if region is None:  # the crop of the first (overview) mesh's box serves the full one too
            region = a.region(W.min(axis=0) - MARGIN_MM - 5, W.max(axis=0) + MARGIN_MM + 5)
        uv, ok = project(a, W, F, region)
        uvs.append(uv)
        cover.append(float(ok.mean()) if len(ok) else 0.0)
    return uvs, min(cover), region.n


def transfer(name: str, orig: Original, mount: np.ndarray | None = None, only: set[str] | None = None,
             force: bool = False, log=print) -> dict:
    """One assembly's exterior parts: register each atlas group on the Original, project the parts that changed,
    write the sidecars and each mesh folder's summary. Returns {"groups", "parts": {key: entry}}."""
    d = OUT / name
    root = _tree(d)
    mount = np.eye(4) if mount is None else mount
    state_path = d / ".uvtransfer_state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    if state.get("algo") != ALGO or state.get("original") != orig.sig:
        state = {"algo": ALGO, "original": orig.sig, "groups": {}, "parts": {}}
    if force:  # redo the registration and the selected parts (the others' entries stay)
        state["groups"] = {}

    joints = {j["child_link"]: j for j in _joints(root)}
    items = []  # (part, files, atlas, matrix, ancestors, registration group)
    for p, M, path in _parts(root):
        if not _exterior(p) or p.get("replaced_by"):
            continue
        files = [p["mesh"]] + ([p["mesh_full"]] if p.get("mesh_full") else [])
        head = name in HEAD_ASSEMBLIES or any(a in HEAD_ASSEMBLIES for a in path)
        atl = HEAD_ATLAS if head else BODY_ATLAS
        j = joints.get(p.get("link"))
        arm = j["id"].split("_")[0] if j else None
        grp = f"arm:{p['link']}" if arm in ARMS and atl == BODY_ATLAS and orig.atlas[atl].has_nodes(arm + "_") else atl
        items.append((p, files, atl, mount @ M, path, grp))
    if not items:
        return {}

    # registration per group, on the overview meshes (enough points, a fraction of the reading): each atlas's
    # parts together, then each arm link on its own (falling back to its atlas's when that fits better)
    groups: dict[str, np.ndarray] = {}
    for grp in sorted({it[5] for it in items}, key=lambda g: (g.startswith("arm:"), g)):
        mine = [it for it in items if it[5] == grp]
        atl = mine[0][2]
        sig = _sha(*[(d / it[1][0]).read_bytes() for it in mine], *[(np.round(it[3], 3) + 0.0).tobytes() for it in mine],
                   state["groups"].get(atl, {}).get("key", "") if grp != atl else "")
        g = state["groups"].get(grp)
        if g and g.get("key") == sig:
            groups[grp] = np.asarray(g["T"])
            log(f"{name}: {len(mine)} parts on {grp}: registration unchanged (rms {g['rms_mm']} mm)")
            continue
        t0 = time.time()
        W = np.concatenate([_xf(it[3], read_glb(d / it[1][0])[0]) for it in mine])
        sample = W[np.random.default_rng(1).choice(len(W), min(REG_SAMPLES, len(W)), replace=False)]
        how = ""
        if grp == atl:
            T, rms, share = register(orig.atlas[atl], sample)
            share = fit_share(orig.atlas[atl], sample, T)
        else:
            link = grp[4:]
            j = joints[link]
            T, rms, share = register_arm(orig.atlas[atl], sample, j["id"].split("_")[0] + "_", j)
            body = fit_share(orig.atlas[atl], sample, groups[atl])
            if share <= body + 0.05:  # the body's registration fits as well: keep it
                T, share, how = groups[atl], body, f" - kept the body's ({body:.0%} vs {share:.0%} on its own)"
            else:
                how = f" - on the Original's {j['id'].split('_')[0]} arm ({body:.0%} with the body's)"
        T = groups[grp] = np.asarray(T.round(6).tolist())  # as the state keeps it, so a rerun keys the same
        state["groups"][grp] = {"key": sig, "T": T.tolist(), "rms_mm": round(rms, 2) if math.isfinite(rms) else None,
                                "fit": round(share, 3), "shift_mm": round(float(np.linalg.norm(T[:3, 3])), 2)}
        log(f"{name}: {len(mine)} parts on {grp}: registered (rms {rms:.2f} mm, {share:.0%} of the points within "
            f"{REG_FIT_MM:.0f} mm, moved {np.linalg.norm(T[:3, 3]):.1f} mm{how}) in {time.time() - t0:.1f} s", flush=True)

    sel = [it for it in items if not only or it[0]["id"] in only or any(a in only for a in it[4])]
    arm_ts = {g: T for g, T in groups.items() if g.startswith("arm:")}  # (all on the body atlas)
    arms_sig = _sha(*[(np.round(T, 3) + 0.0).tobytes() for _g, T in sorted(arm_ts.items())])
    done = skipped = 0
    t0 = time.time()
    for i, (p, files, atl, M, path, grp) in enumerate(sel):
        a = orig.atlas[atl]
        old = state["parts"].get(p["mesh"])
        key = _sha(ALGO, orig.sig, atl, (np.round(groups[grp] @ M, 3) + 0.0).tobytes(), *[(d / f).read_bytes() for f in files])
        expect = _sha(key, arms_sig) if old and old.get("tried_arms") else key  # then it also hangs on the arms
        if not force and old and old.get("key") == expect and all((d / f).with_suffix(".uv.bin").exists() for f in files):
            skipped += 1
            continue
        tp = time.time()
        meshes = [read_glb(d / fn) for fn in files]
        used, out, why = grp, _project_files(a, meshes, groups[grp] @ M), ""
        tried = False
        if out[1] < MIN_COVER and grp == BODY_ATLAS and arm_ts:
            # a part on a ring that the Original moved with an arm (its shoulder hub, its shaft): try each arm's
            # registration on the overview mesh, and keep the best when it carries the part past MIN_COVER
            tried = True
            trial = {g: _project_files(a, meshes[:1], T @ M)[1] for g, T in arm_ts.items()}
            centre = _xf(M, meshes[0][0]).mean(axis=0)
            # the best, and of equals the arm whose joint is nearest (a ring panel fits under any turn of the ring)
            g = max(trial, key=lambda g: (round(trial[g], 2), -np.linalg.norm(np.asarray(joints[g[4:]]["pivot"]) - centre)))
            if trial[g] >= MIN_COVER and trial[g] > out[1] + 0.1:
                used, out, why = g, _project_files(a, meshes, arm_ts[g] @ M), f", with {g}'s registration"
        uvs, c, rn = out
        for fn, uv in zip(files, uvs):
            (d / fn).with_suffix(".uv.bin").write_bytes(uv.tobytes())
        state["parts"][p["mesh"]] = {"id": p["id"], "folder": _folder(p["mesh"]), "atlas": atl, "group": used,
                                     "key": _sha(key, arms_sig) if tried else key, "tried_arms": tried,
                                     "coverage": round(c, 3), "files": files, "faces": int(len(meshes[-1][1])),
                                     "region_tris": int(rn)}
        done += 1
        log(f"  [{i + 1}/{len(sel)}] {name}/{p['id']}: {atl} {c:.0%}{why} ({len(meshes[-1][1])} faces, {rn} Original "
            f"tris, {time.time() - tp:.2f} s, peak {rss_mb():.0f} MB)", flush=True)
    # forget parts no longer in the manifest
    live = {it[0]["mesh"] for it in items}
    state["parts"] = {k: v for k, v in state["parts"].items() if k in live}
    state_path.write_text(json.dumps(state, indent=1))
    log(f"{name}: {done} transferred, {skipped} unchanged, in {time.time() - t0:.1f} s")

    # each mesh folder's summary (the viewer reads <folder>/uvtransfer.json for the parts under it)
    reg = {k: {kk: g[kk] for kk in ("rms_mm", "fit", "shift_mm")} for k, g in state["groups"].items()}
    folders: dict[str, dict] = {}
    for v in state["parts"].values():
        f = folders.setdefault(v["folder"], {"source": "sim/web/public/model/r3x.glb", "original": orig.sig,
                                             "registration": reg, "min_cover": MIN_COVER, "parts": {}})
        f["parts"][v["id"]] = {"atlas": v["atlas"], "coverage": v["coverage"], "textured": v["coverage"] >= MIN_COVER,
                               "files": [x.replace(".glb", ".uv.bin") for x in v["files"]]}
    for folder, s in folders.items():
        (d / folder / "uvtransfer.json").write_text(json.dumps(s, indent=1))
    if "" not in folders:  # the assembly's own summary: where the folders are
        (d / "uvtransfer.json").write_text(json.dumps({"source": "sim/web/public/model/r3x.glb", "original": orig.sig,
                                                       "registration": reg, "folders": sorted(folders), "parts": {}},
                                                      indent=1))
    return {"groups": state["groups"], "parts": state["parts"]}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m workbench.uvtransfer", description=__doc__.split("\n\n")[0])
    ap.add_argument("names", nargs="*", default=["hunter_head", "r3x_droid"], help="assemblies under mech/out")
    ap.add_argument("--only", action="append", default=[], help="a sub-assembly or part id (repeatable)")
    ap.add_argument("--force", action="store_true", help="redo every part and the registration")
    a = ap.parse_args(argv)
    t0 = time.time()
    orig = Original()
    print(f"Original {orig.sig}: " + ", ".join(f"{k} {len(v.F)} tris" for k, v in orig.atlas.items())
          + f" ({time.time() - t0:.1f} s, peak {rss_mb():.0f} MB)", flush=True)
    for name in a.names or ["hunter_head", "r3x_droid"]:
        mount = np.eye(4)
        if name == "hunter_head":  # the head stands on the neck: start it at the Original head's height
            mount[1, 3] = 738.3
        s = transfer(name, orig, mount, set(a.only) or None, a.force)
        parts = s.get("parts", {})
        tex = [v for v in parts.values() if v["coverage"] >= MIN_COVER]
        proc = [f"{v['folder']}{v['id']} ({v['coverage']:.0%})" for v in parts.values() if v["coverage"] < MIN_COVER]
        print(f"{name}: {len(tex)} of {len(parts)} textured; procedural: {', '.join(proc) or 'none'}")
    print(f"done in {time.time() - t0:.1f} s, peak {rss_mb():.0f} MB")


if __name__ == "__main__":
    main()
