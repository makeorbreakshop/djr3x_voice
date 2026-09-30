"""Regression of a parametric part against the reference file its defaults reproduce.

    compare(part, ref_mesh)      bbox, volume ratio, symmetric surface deviation (Hausdorff, p95, mean)
    ref_holes(mesh)              every round hole in a mesh, found by feature: closed circular
                                 loops (interior rings) in sections along X, Y and Z
    match_holes(part, ref)       each of the part's hole features against the reference's holes,
                                 and each reference hole against the part's (none may be missing)
    heatmap(part, ref, out)      deviation renders (scratchpad only: never commit renders of
                                 vendored parts)

Nothing here needs the vendored files except the callers; mech/vendor is gitignored, so the
tests skip when a reference is not on the machine.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import trimesh

MECH = Path(__file__).resolve().parents[3]
REF_DIR = MECH / "vendor" / "hunter_head"


def load_ref(name: str, thickness: float = 3.0) -> trimesh.Trimesh:
    p = REF_DIR / name
    if p.suffix.lower() == ".dxf":        # a cut profile: extruded as the workbench does (centred on z = 0)
        from workbench.geom import dxf_profile, extrude

        poly, _ = dxf_profile(p, 0.02)
        return extrude(poly, thickness)
    if p.suffix.lower() in (".step", ".stp"):
        from build123d import import_step

        from parts.head._common import mesh

        return mesh(import_step(str(p)), 0.005, 0.05)
    return trimesh.load(str(p), force="mesh")


def ref_volume(ref: trimesh.Trimesh) -> float:
    """The reference's volume; an open export (Hunter's side shells) is closed on a copy first."""
    if ref.is_watertight:
        return float(ref.volume)
    r = ref.copy()
    r.merge_vertices(digits_vertex=3)
    trimesh.repair.fill_holes(r)
    trimesh.repair.fix_normals(r)
    return float(abs(r.volume))


def compare(part, ref: trimesh.Trimesh, n: int = 40000, model_mesh=None) -> dict:
    from parts.head._common import mesh

    m = model_mesh if model_mesh is not None else mesh(part)
    pa, _ = trimesh.sample.sample_surface(m, n, seed=1)
    pb, _ = trimesh.sample.sample_surface(ref, n, seed=2)
    _, da, _ = trimesh.proximity.closest_point(ref, pa)
    _, db, _ = trimesh.proximity.closest_point(m, pb)
    d = np.concatenate([da, db])
    rv = ref_volume(ref)
    return {
        "bbox_mm": float(np.abs(m.bounds - ref.bounds).max()),
        "volume_ratio": float(part.volume / rv),
        "hausdorff_mm": float(d.max()),
        "p95_mm": float(np.percentile(d, 95)),
        "mean_mm": float(d.mean()),
        "model_to_ref_mean_mm": float(da.mean()),
        "ref_to_model_mean_mm": float(db.mean()),
        "ref_watertight": bool(ref.is_watertight),
    }


# ------------------------------------------------------------------ holes by feature

def _circle(pts: np.ndarray):
    """Least-squares circle through 2D points: (centre, radius, roundness = rms(r - R) / R)."""
    A = np.c_[2 * pts, np.ones(len(pts))]
    b = (pts ** 2).sum(1)
    sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    c = sol[:2]
    R = float(np.sqrt(sol[2] + c @ c))
    rr = np.linalg.norm(pts - c, axis=1)
    return c, R, float(np.sqrt(np.mean((rr - R) ** 2)) / R)


def _stadium(pts: np.ndarray):
    """A slot outline: (centre, end radius, straight length) if the ring is a stadium, else None."""
    c = pts.mean(0)
    u, sv, vt = np.linalg.svd(pts - c, full_matrices=False)
    a = vt[0]
    s = (pts - c) @ a
    t = (pts - c) @ vt[1]
    half_w = (t.max() - t.min()) / 2
    L = (s.max() - s.min()) - 2 * half_w
    if L <= 0.2 or half_w <= 0:
        return None
    cc = c + a * (s.max() + s.min()) / 2 + vt[1] * (t.max() + t.min()) / 2
    # every point within the stadium's boundary band
    q = pts - cc
    sa = np.clip(q @ a, -L / 2, L / 2)
    dist = np.linalg.norm(q - np.outer(sa, a), axis=1)
    if np.abs(dist - half_w).max() > 0.08 * half_w:
        return None
    return cc, float(half_w), float(L)


def ref_holes(mesh: trimesh.Trimesh, step: float = 0.5, rmin: float = 0.8, rmax: float = 8.0,
              axes=(0, 1, 2), roundness: float = 0.03) -> list[dict]:
    """Round holes (and slots) in a mesh, found by feature: every closed interior ring in sections
    taken every `step` mm along each principal axis that is circular (a hole) or a stadium (a
    slot: two semicircles of equal radius joined by straights). Returns [{axis, c (3D centre),
    r, span (min, max along the axis), slot_len (0 for a hole)}], one per distinct feature."""
    out: list[dict] = []
    lo, hi = mesh.bounds
    for ax in axes:
        n = np.zeros(3)
        n[ax] = 1.0
        found: list[dict] = []
        for v in np.arange(lo[ax] + step / 2, hi[ax], step):
            o = np.zeros(3)
            o[ax] = v
            sec = mesh.section(plane_origin=o, plane_normal=n)
            if sec is None:
                continue
            try:
                p2, T = sec.to_2D(to_2D=trimesh.geometry.plane_transform(origin=o, normal=n))
            except Exception:
                continue
            for poly in p2.polygons_full:
                for ring in poly.interiors:
                    pts = np.asarray(ring.coords)[:-1]
                    if len(pts) < 6:
                        continue
                    c, R, rd = _circle(pts)
                    slot_len = 0.0
                    if not (rmin <= R <= rmax) or rd > roundness:
                        st = _stadium(pts)
                        if st is None or not (rmin <= st[1] <= rmax):
                            continue
                        c, R, slot_len = st
                    c3 = trimesh.transform_points(np.array([[c[0], c[1], 0.0]]), T)[0]
                    hit = next((h for h in found if abs(h["r"] - R) < 0.3 and
                                np.linalg.norm(np.delete(h["c"] - c3, ax)) < 0.3), None)
                    if hit:
                        hit["span"][1] = float(v)
                        hit["n"] += 1
                    else:
                        found.append({"axis": ax, "c": c3, "r": R, "span": [float(v), float(v)], "n": 1,
                                      "slot_len": slot_len})
        out += [h for h in found if h["n"] >= 2 or step >= 1.0]
    return out


def match_holes(features: dict, ref_mesh: trimesh.Trimesh, tol: float = 0.2, holes=None, frame=None, **kw) -> dict:
    """Every `hole_*` axis feature of the part against the holes (and slots) found on the
    reference, and back. A match: same principal axis, centre (perpendicular to the axis) within
    `tol`, radius within 0.3 mm, same kind (hole or slot). `frame` (4x4, design -> reference) is
    for parts exported askew (the visor mount): both sides are compared in the design frame.
    Returns {matched: [(name, err_mm)], missing_in_ref, missing_in_model, max_err_mm}."""
    if frame is not None:
        from parts.head._common import moved

        inv = np.linalg.inv(frame)
        ref_mesh = ref_mesh.copy()
        ref_mesh.apply_transform(inv)
        features = {k: moved(f, inv) for k, f in features.items()}
        holes = None
    holes = holes if holes is not None else ref_holes(ref_mesh, **kw)
    mh = {k: f for k, f in features.items() if k.startswith("hole_") and f["type"] == "axis"}
    matched, miss_ref, used = [], [], set()
    for name, f in sorted(mh.items()):
        d = np.abs(np.asarray(f["d"]))
        ax = int(np.argmax(d))
        if d[ax] < 0.999:
            miss_ref.append((name, "not along a principal axis"))
            continue
        best = None
        for i, h in enumerate(holes):
            if h["axis"] != ax or abs(h["r"] - f["r"]) > 0.3:
                continue
            if (h.get("slot_len", 0.0) > 0) != (f.get("slot_len", 0.0) > 0):
                continue
            e = float(np.linalg.norm(np.delete(np.asarray(f["p"]) - h["c"], ax)))
            if best is None or e < best[0]:
                best = (e, i)
        if best is None or best[0] > tol:
            miss_ref.append((name, None if best is None else round(best[0], 3)))
        else:
            matched.append((name, round(best[0], 4)))
            used.add(best[1])
    miss_model = [(round(h["r"] * 2, 2), "xyz"[h["axis"]], np.round(h["c"], 2).tolist())
                  for i, h in enumerate(holes) if i not in used]
    return {"matched": matched, "missing_in_ref": miss_ref, "missing_in_model": miss_model,
            "max_err_mm": max([e for _, e in matched], default=0.0)}


# ------------------------------------------------------------------ heatmaps (scratchpad only)

def heatmap(part, ref: trimesh.Trimesh, out: str, vmax: float = 0.5, title: str = ""):
    """Model faces coloured by distance to the reference (and the reference by distance to the
    model), six views each. Write to a scratch directory, never into the repo."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    from parts.head._common import mesh

    m = mesh(part, 0.05, 0.2)
    _, dm, _ = trimesh.proximity.closest_point(ref, m.triangles_center)
    _, dr, _ = trimesh.proximity.closest_point(m, ref.triangles_center)
    views = [(25, -60), (25, 120), (89.9, -90), (-89.9, -90)]
    fig = plt.figure(figsize=(20, 10))
    lo = np.minimum(m.bounds[0], ref.bounds[0])
    hi = np.maximum(m.bounds[1], ref.bounds[1])
    c, r = (lo + hi) / 2, (hi - lo).max() / 2
    cmap = plt.get_cmap("turbo")
    for row, (mm, dd, lbl) in enumerate(((m, dm, "model -> ref"), (ref, dr, "ref -> model"))):
        tri = mm.vertices[mm.faces][:, :, [0, 2, 1]]
        n = mm.face_normals[:, [0, 2, 1]]
        light = np.clip(0.45 + 0.55 * np.abs(n @ np.array([0.3, -0.5, 0.8]) / 1.0), 0, 1)
        fc = cmap(np.clip(dd / vmax, 0, 1))[:, :3] * light[:, None]
        for k, (el, az) in enumerate(views):
            ax = fig.add_subplot(2, 4, row * 4 + k + 1, projection="3d")
            ax.add_collection3d(Poly3DCollection(tri, facecolors=np.clip(fc, 0, 1), edgecolor="none"))
            ax.set_xlim(c[0] - r, c[0] + r)
            ax.set_ylim(c[2] - r, c[2] + r)
            ax.set_zlim(c[1] - r, c[1] + r)
            ax.view_init(el, az)
            ax.set_box_aspect((1, 1, 1))
            ax.set_axis_off()
            ax.set_title(f"{lbl} (max {dd.max():.2f} mm)", fontsize=9)
    fig.suptitle(f"{title}  deviation, 0 (blue) .. {vmax} mm (red)")
    plt.tight_layout()
    fig.savefig(out, dpi=60)
    plt.close(fig)
