"""Tiny dependency-free orthographic renderer (matplotlib painter's algorithm).

Good enough for proof renders and debugging placements: flat shading by face normal,
per-part colours, optional axis arrows. Output goes to mech/out/ (gitignored).
"""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.collections import PolyCollection  # noqa: E402

# Canonical frame: +X droid's left, +Y up, +Z front. View = (right, up, toward-viewer).
VIEWS = {
    "front": (np.array([-1, 0, 0]), np.array([0, 1, 0]), np.array([0, 0, 1])),
    "back": (np.array([1, 0, 0]), np.array([0, 1, 0]), np.array([0, 0, -1])),
    "left": (np.array([0, 0, -1]), np.array([0, 1, 0]), np.array([1, 0, 0])),   # droid's left side
    "right": (np.array([0, 0, 1]), np.array([0, 1, 0]), np.array([-1, 0, 0])),
    "top": (np.array([-1, 0, 0]), np.array([0, 0, -1]), np.array([0, 1, 0])),
    "iso": None,
}


def _iso():
    v = np.array([-0.55, 0.45, 0.7]); v /= np.linalg.norm(v)
    up = np.array([0, 1.0, 0]); r = np.cross(up, v); r /= np.linalg.norm(r)
    u = np.cross(v, r)
    return r, u, v


PALETTE = ["#d9822b", "#4c78a8", "#54a24b", "#e45756", "#b279a2", "#72b7b2", "#eeca3b",
           "#9d755d", "#ff9da6", "#bab0ac", "#f58518", "#439894"]


def render(items, path, view="front", title=None, arrows=(), markers=(), size=9, max_faces=60000,
           bounds=None, dpi=110):
    """items: list of (trimesh.Trimesh, colour, alpha). arrows: (origin, dir, length, colour, label)."""
    r, u, v = _iso() if view == "iso" else VIEWS[view]
    light = np.array([0.3, 0.5, 0.8]) @ np.array([r, u, v]); light /= np.linalg.norm(light)
    polys, cols, depth = [], [], []
    total = sum(len(m.faces) for m, *_ in items) or 1
    for m, colour, alpha in items:
        tri = m.triangles
        keep = max(1, int(len(tri) * min(1.0, max_faces / total)))
        if keep < len(tri):
            idx = np.random.default_rng(0).choice(len(tri), keep, replace=False)
            tri = tri[idx]; nrm = m.face_normals[idx]
        else:
            nrm = m.face_normals
        P = np.stack([tri @ r, tri @ u], axis=-1)
        d = (tri @ v).mean(1)
        facing = nrm @ v
        shade = 0.35 + 0.65 * np.clip(np.abs(nrm @ light), 0, 1)
        base = np.array(matplotlib.colors.to_rgb(colour))
        c = np.clip(base[None] * shade[:, None], 0, 1)
        c = np.c_[c, np.full(len(c), alpha)]
        # cheap backface cull for opaque parts
        if alpha >= 0.99:
            sel = facing > -0.05
            P, c, d = P[sel], c[sel], d[sel]
        polys.append(P); cols.append(c); depth.append(d)
    P = np.concatenate(polys); C = np.concatenate(cols); D = np.concatenate(depth)
    o = np.argsort(D)
    fig, ax = plt.subplots(figsize=(size, size))
    ax.add_collection(PolyCollection(P[o], facecolors=C[o], edgecolors="none", antialiased=False))
    for org, d, L, colour, label in arrows:
        org = np.asarray(org, float); d = np.asarray(d, float); d = d / np.linalg.norm(d)
        a = org - d * L / 2; b = org + d * L / 2
        ax.annotate("", xy=(b @ r, b @ u), xytext=(a @ r, a @ u),
                    arrowprops=dict(arrowstyle="-|>", color=colour, lw=2.2), zorder=10)
        ax.plot([org @ r], [org @ u], "o", color=colour, ms=5, zorder=11)
        if label:
            ax.text(b @ r, b @ u, " " + label, color=colour, fontsize=9, zorder=12, weight="bold")
    for p, colour, label in markers:
        p = np.asarray(p, float)
        ax.plot([p @ r], [p @ u], "x", color=colour, ms=12, mew=3, zorder=12)
        if label:
            ax.text(p @ r, p @ u, " " + label, color=colour, fontsize=9, zorder=12, weight="bold")
    if bounds is None:
        ax.autoscale_view()
    else:
        ax.set_xlim(bounds[0]); ax.set_ylim(bounds[1])
    ax.set_aspect("equal"); ax.grid(True, lw=0.3, alpha=0.5)
    ax.set_title(title or view)
    fig.tight_layout(); fig.savefig(path, dpi=dpi); plt.close(fig)
    return path
