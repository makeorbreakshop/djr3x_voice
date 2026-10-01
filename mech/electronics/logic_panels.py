"""Measure the middle ring's three logic panels from the kit STLs -> logic_panels.json.

    mech/.venv/bin/python mech/electronics/logic_panels.py   (needs mech/vendor linked)

For each panel (MS_P_1_Full holds all three; the LED Board Mounts MS_LPI_{L,M,R}_* say which
is which) this finds, from the mesh itself:

- the front face plane (area-weighted normal of the outward faces),
- every opening, by projecting the panel's triangles onto that plane and flood-filling the
  uncovered cells: the 8 round LED holes (~2.5 mm) and the 3 square windows (~13 mm),
- the 2 x 3 grid of window slots behind the panel (all three panels share it: the union of
  their 9 windows fills 6 slots); the 3 slots a panel does not open are covered by its
  MS_LPI mount.

Output is in the model's kit frame (the GLB's own frame: kit mm -> m, turned by the model
build's BODY_YAW, sim/model/build_r3x.py), which is the frame of rig.json and of the
electronics packages' layouts; the sim parents each point to its link (Rig.kitToLocal).
Only derived coordinates are committed, never the meshes.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import trimesh
from scipy import ndimage

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
KIT = REPO / "mech/vendor/kit/unz/DJ R3X - v2/STLs/Large Cut/Midsection/Middle/STLs/Midsection - Middle/STLs/Logic Panels"
BODY_YAW = math.radians(33.0)  # sim/model/build_r3x.py
CELL = 0.2  # mm raster


def to_model(p_mm):
    """Kit mm -> model kit frame, metres (three.js rotation about +Y, like build_r3x.roty)."""
    x, y, z = p_mm
    c, s = math.cos(BODY_YAW), math.sin(BODY_YAW)
    return [round((c * x + s * z) / 1000, 5), round(y / 1000, 5), round((-s * x + c * z) / 1000, 5)]


def dir_to_model(d):
    x, y, z = d
    c, s = math.cos(BODY_YAW), math.sin(BODY_YAW)
    v = np.array([c * x + s * z, y, -s * x + c * z])
    return [round(float(t), 4) for t in v / np.linalg.norm(v)]


def az(p):
    return math.degrees(math.atan2(p[0], p[2]))


def panel_openings(mesh, centre_az, half_width=16.0):
    tri = mesh.triangles
    cen = tri.mean(1)
    a = np.degrees(np.arctan2(cen[:, 0], cen[:, 2]))
    sel = np.abs(a - centre_az) < half_width
    tri, cen, fn, area = tri[sel], cen[sel], mesh.face_normals[sel], mesh.area_faces[sel]
    radial = np.c_[np.sin(np.radians(a[sel])), np.zeros(sel.sum()), np.cos(np.radians(a[sel]))]
    front = (fn * radial).sum(1) > 0.9
    n = (fn[front] * area[front, None]).sum(0)
    n[1] = 0.0
    n /= np.linalg.norm(n)
    u = np.array([n[2], 0.0, -n[0]])  # horizontal tangent
    v = np.array([0.0, 1.0, 0.0])
    P = np.stack([tri @ u, tri @ v], -1)  # (T, 3, 2)
    lo, hi = P.reshape(-1, 2).min(0), P.reshape(-1, 2).max(0)
    shape = np.ceil((hi - lo) / CELL).astype(int) + 1
    cov = np.zeros(shape, bool)
    # rasterise every triangle (barycentric test over its bbox)
    for t in P:
        b0 = np.floor((t.min(0) - lo) / CELL).astype(int)
        b1 = np.ceil((t.max(0) - lo) / CELL).astype(int)
        gx, gy = np.mgrid[b0[0]:b1[0] + 1, b0[1]:b1[1] + 1]
        q = np.stack([gx, gy], -1) * CELL + lo
        v0, v1 = t[1] - t[0], t[2] - t[0]
        den = v0[0] * v1[1] - v0[1] * v1[0]
        if abs(den) < 1e-9:
            continue
        w = q - t[0]
        l1 = (w[..., 0] * v1[1] - w[..., 1] * v1[0]) / den
        l2 = (v0[0] * w[..., 1] - v0[1] * w[..., 0]) / den
        inside = (l1 >= -0.02) & (l2 >= -0.02) & (l1 + l2 <= 1.02)
        cov[gx[inside], gy[inside]] = True
    lab, k = ndimage.label(~cov)
    border = set(np.unique(np.r_[lab[0], lab[-1], lab[:, 0], lab[:, -1]]))
    depth_all = cen @ n
    uv_c = np.stack([cen @ u, cen @ v], -1)
    out = []
    for i in range(1, k + 1):
        if i in border:
            continue
        ij = np.argwhere(lab == i)
        wmm = (np.ptp(ij[:, 0]) + 1) * CELL
        hmm = (np.ptp(ij[:, 1]) + 1) * CELL
        if max(wmm, hmm) < 1.5 or max(wmm, hmm) > 20:
            continue
        kind = "hole" if max(wmm, hmm) <= 3.5 else ("window" if min(wmm, hmm) >= 9 else None)
        if kind is None:
            continue
        cu, cv = ij.mean(0) * CELL + lo
        # the front face right around the opening
        ring = (np.abs(uv_c[:, 0] - cu) < wmm / 2 + 2.5) & (np.abs(uv_c[:, 1] - cv) < hmm / 2 + 2.5) & front
        d = float(np.median(depth_all[ring])) if ring.any() else float(depth_all[front].max())
        out.append({"kind": kind, "u": cu, "v": cv, "d": d, "w": wmm, "h": hmm})
    return n, u, out


def main():
    mesh = trimesh.load(KIT / "MS_P_1_Full.stl")
    mounts = {}
    for side in "LMR":
        pts = np.vstack([trimesh.load(p).vertices for p in sorted((KIT / "LED Board Mounts").glob(f"MS_LPI_{side}_*.stl"))])
        mounts[side] = az(pts.mean(0))
    # Panel 1..3 = the droid's right side, rear to front (the packages' panel / board order).
    order = sorted(mounts, key=lambda s: mounts[s])
    panels = []
    for side in order:
        n, u, ops = panel_openings(mesh, mounts[side])
        holes = sorted([o for o in ops if o["kind"] == "hole"], key=lambda o: o["v"])
        wins = [o for o in ops if o["kind"] == "window"]
        assert len(holes) == 8 and len(wins) == 3, (side, len(holes), len(wins))
        hu = float(np.mean([h["u"] for h in holes]))
        panels.append({"side": side, "n": n, "u": u, "holes": holes, "windows": wins, "hole_u": hu})
    # Shared slot grid, in each panel's coordinates relative to its LED-hole column.
    rel = [(w["u"] - p["hole_u"], w["v"]) for p in panels for w in p["windows"]]
    du = np.array([r[0] for r in rel])
    dv = np.array([r[1] for r in rel])
    cols = sorted({round(x) for x in du})
    col_c = [float(du[np.abs(du - c) < 4].mean()) for c in sorted(set(np.round(du / 5) * 5))]
    row_c = sorted(float(dv[np.abs(dv - c) < 4].mean()) for c in sorted(set(np.round(dv / 5) * 5)))
    # merge near-duplicates
    def merge(xs):
        out = []
        for x in sorted(xs):
            if out and abs(x - out[-1]) < 4:
                out[-1] = (out[-1] + x) / 2
            else:
                out.append(x)
        return out
    col_c, row_c = merge(col_c), merge(row_c)
    assert len(col_c) == 2 and len(row_c) == 3, (col_c, row_c)
    # col 0 = the column farther from the LED holes; row 0 = bottom.
    col_c = sorted(col_c, key=lambda c: -abs(c))
    result = {"_doc": __doc__.strip().splitlines()[0], "frame": "model kit frame (GLB), metres",
              "slot_grid": "group g of a board -> row g // 2 (0 = bottom), col g % 2 (0 = away from the LED holes)",
              "panels": []}
    for i, p in enumerate(panels):
        n, u = p["n"], p["u"]
        v = np.array([0.0, 1.0, 0.0])

        def pt(pu, pv, d):
            return to_model(pu * u + pv * v + d * n)

        wd = float(np.median([w["d"] for w in p["windows"]]))
        slots = []
        for r_i, rv in enumerate(row_c):
            for c_i, cu in enumerate(col_c):
                su = p["hole_u"] + cu
                win = next((w for w in p["windows"] if abs(w["u"] - su) < 4 and abs(w["v"] - rv) < 4), None)
                slots.append({"group": r_i * 2 + c_i, "open": win is not None,
                              "centre": pt(win["u"] if win else su, win["v"] if win else rv, win["d"] if win else wd),
                              "size_m": [round((win or {"w": 12})["w"] / 1000, 4), round((win or {"h": 12})["h"] / 1000, 4)],
                              "u": dir_to_model(u)})
        result["panels"].append({
            "panel": i + 1, "mount": f"MS_LPI_{p['side']}_1..3", "azimuth_model_deg": round(mounts[p["side"]] + 33.0, 1),
            "normal": dir_to_model(n), "across": dir_to_model(u),
            "holes": [{"centre": pt(h["u"], h["v"], h["d"]), "diam_m": round(max(h["w"], h["h"]) / 1000, 4)} for h in p["holes"]],
            "slots": slots,
        })
    (HERE / "logic_panels.json").write_text(json.dumps(result, indent=1) + "\n")
    for P in result["panels"]:
        print(P["panel"], P["mount"], P["azimuth_model_deg"], "open groups:", [s["group"] for s in P["slots"] if s["open"]])


if __name__ == "__main__":
    main()
