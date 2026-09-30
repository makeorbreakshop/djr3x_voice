"""Proof renders (local only: out/renders/ is gitignored because kit geometry is licensed)."""
from __future__ import annotations

import functools
from pathlib import Path

import numpy as np
import trimesh

from .meshes import part_mesh_local
from .render import PALETTE, render

LINK_COLOURS = {}


def colour(link):
    if link not in LINK_COLOURS:
        LINK_COLOURS[link] = PALETTE[len(LINK_COLOURS) % len(PALETTE)]
    return LINK_COLOURS[link]


@functools.lru_cache(maxsize=None)
def _dec(key, faces):
    import fast_simplification  # noqa: F401  (trimesh backend)
    part = _REG[key]
    m = part_mesh_local(part)
    if len(m.faces) > faces:
        try:
            m = m.simplify_quadric_decimation(face_count=faces)
        except Exception:
            pass
    return m


_REG = {}


def posed(part, T_link=None, faces=6000):
    _REG[part.id] = part
    m = _dec(part.id, faces).copy()
    m.apply_transform((T_link if T_link is not None else np.eye(4)) @ part.T)
    return m


def items_for(root, parts, pose=None, shell_alpha=0.18, only=None, faces_shell=4000, faces_mech=3000):
    pose = pose or {}
    Tl = {}
    out = []
    for p in parts:
        if only is not None and not only(p):
            continue
        if p.link not in Tl:
            Tl[p.link] = root.link_T(p.link, pose)
        shell = p.origin == "kit"
        m = posed(p, Tl[p.link], faces_shell if shell else faces_mech)
        out.append((m, "#9a9a9a" if shell else colour(p.link), shell_alpha if shell else 1.0))
    return out


def joint_arrows(root, pose=None, which=None, length=160):
    pose = pose or {}
    arrows = []
    for j in root.all_joints().values():
        if which and j.id not in which:
            continue
        T = root.link_T(j.parent_link, pose)
        piv = T[:3, :3] @ np.asarray(j.pivot, float) + T[:3, 3]
        ax = T[:3, :3] @ np.asarray(j.axis, float)
        c = {"high": "#d62728", "medium": "#ff7f0e", "low": "#9467bd"}.get(j.confidence, "#d62728")
        arrows.append((piv, ax, length if j.type != "prismatic" else 90, c if j.type != "prismatic" else "#1f77b4",
                       j.id))
    return arrows


def render_all(root, parts, out: Path):
    out.mkdir(parents=True, exist_ok=True)
    DRIVEN = ["head_pan", "head_lift", "head_tilt", "visor", "torso_lower", "torso_top", "hero_shoulder", "hero_wrist"]
    full = items_for(root, parts)
    arrows = joint_arrows(root, which=DRIVEN)
    for view in ("front", "left", "top", "iso"):
        render(full, out / f"droid_{view}.png", view=view, title=f"R3X rest pose, {view} (grey = kit shell)",
               arrows=arrows, max_faces=400000, size=10)
        print("render", view)
    mech = items_for(root, parts, only=lambda p: p.origin != "kit")
    for view in ("front", "left"):
        render(mech, out / f"mech_only_{view}.png", view=view, title=f"mechanisms only, {view}", arrows=arrows,
               max_faces=400000, size=10)
    # close-ups
    head = items_for(root, parts, only=lambda p: p.link in ("head", "visor", "head_mount") or
                     p.id.startswith(("neck_clamp_upper", "neck_guide")), shell_alpha=0.12)
    for view in ("front", "left"):
        render(head, out / f"head_{view}.png", view=view, title=f"head + neck top, {view}",
               arrows=joint_arrows(root, which=["head_tilt", "visor"], length=120), max_faces=300000, size=9)
    base = items_for(root, parts, only=lambda p: p.link in ("neck_stage", "turntable", "slide", "frame") and
                     p.id != "neck_tube" or p.id in ("neck_tube",), shell_alpha=0.1)
    for view in ("iso", "left", "top"):
        render(base, out / f"base_stage_{view}.png", view=view, title=f"pan/lift stage + Morton frame, {view}",
               arrows=joint_arrows(root, which=["head_pan", "head_lift"], length=200), max_faces=300000, size=9,
               bounds=None)
    print("renders ->", out)
