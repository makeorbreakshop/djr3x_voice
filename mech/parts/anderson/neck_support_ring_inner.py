"""Neck support ring, inner (Anderson) - the disc that carries the neck support's upright.

A 6 mm disc round the neck (bore), eight M5 clearance holes on a circle, and an upright tab at the
edge: 4 mm thick with 5 mm edges, two M3 holes through it, an r6 fillet at its outer foot (relieved
for the M5 head below it), r7.5
fillet-gussets inside its edges, and a window through the disc under its inner face.

Frame = the STEP's: axis +Z at the origin, underside z = 0, the tab toward -Y.
"""

from __future__ import annotations

import math

from parts.head._common import Print, finish, hole_d, hole_features, plane

from . import HOLE_SIZES

REFERENCE = "r3x-internal - neck-support-ring-inner.step"
LABEL = "Neck support ring, inner (parametric)"

DEFAULTS = dict(
    fit=0.0,
    outer_r=57.0, bore_r=17.5, thickness=6.0,
    ring_bolt="M5", ring_holes=(51.0, 8, 0.0),     # radius, count, first angle (deg)
    tab=(-46.25, 4.0, 1.0, 20.0, 12.5, 46.0),     # outer face y, thickness, edge extra, width, web width, top z
    tab_fillet=6.0,          # outer foot
    head_relief=9.0,         # the M5 head under the tab's foot: a 9 mm bore through the fillet
    gusset=7.5,              # inside the edges
    window=(12.5, -42.25, -33.75),   # through the disc under the tab: width, y0, y1
    tab_bolt="M3", tab_holes=(16.5, 41.5),         # z of the two holes (along Y, x = 0)
    hole_sizes=HOLE_SIZES,
)


def make(params: dict | None = None, **kw):
    from build123d import Box, Cylinder, Pos, Rot

    P = dict(DEFAULTS)
    P.update(params or {})
    P.update(kw)
    unknown = set(P) - set(DEFAULTS)
    if unknown:
        raise TypeError(f"unknown neck_support_ring_inner parameters: {sorted(unknown)}")
    fit = P["fit"]
    R, rb, t = P["outer_r"], P["bore_r"] + fit / 2, P["thickness"]
    body = Pos(0, 0, t / 2) * (Cylinder(R, t) - Cylinder(rb, t + 1))
    ty, tt, te, tw, tweb, tz = P["tab"]
    yi = ty + tt                                   # tab inner face (web)
    # the tab: full width to the web's face, the edges one more mm
    body += Pos(0, ty + tt / 2, (t + tz) / 2) * Box(tw, tt, tz - t)
    for s in (-1, 1):
        body += Pos(s * (tweb / 2 + (tw - tweb) / 4), yi + te / 2, (t + tz) / 2) * Box((tw - tweb) / 2, te, tz - t)
    # outer foot fillet (full width) and the edge gussets, each a square less a quarter circle
    rf = P["tab_fillet"]
    foot = Pos(0, ty - rf / 2, t + rf / 2) * Box(tw, rf, rf)
    foot -= Pos(0, ty - rf, t + rf) * Rot(0, 90, 0) * Cylinder(rf, tw + 1)
    body += foot
    g = P["gusset"]
    for s in (-1, 1):
        xc = s * (tweb / 2 + (tw - tweb) / 4)
        gus = Pos(xc, yi + te + g / 2, t + g / 2) * Box((tw - tweb) / 2, g, g)
        gus -= Pos(xc, yi + te + g, t + g) * Rot(0, 90, 0) * Cylinder(g, tw)
        body += gus
    ww, wy0, wy1 = P["window"]
    body -= Pos(0, (wy0 + wy1) / 2, t / 2) * Box(ww, wy1 - wy0, t + 1)

    feats: dict = {"bottom": plane((0, 0, 0), (0, 0, -1)), "top": plane((0, 0, t), (0, 0, 1)),
                   "tab_face": plane((0, ty, (t + tz) / 2), (0, -1, 0))}
    hole_features(feats, "bore", (0, 0, 0), (0, 0, 1), rb, depth=t)
    d5 = hole_d(P["ring_bolt"], "clearance", fit, P["hole_sizes"])
    r, cnt, a0 = P["ring_holes"]
    for k in range(cnt):
        a = math.radians(a0 + 360.0 * k / cnt)
        x, y = r * math.cos(a), r * math.sin(a)
        body -= Pos(x, y, t / 2) * Cylinder(d5 / 2, t + 0.02)
        hole_features(feats, f"ring{k + 1}", (x, y, 0), (0, 0, 1), d5 / 2, depth=t, bolt=P["ring_bolt"], kind="clearance")
        if abs(x) < 1e-6 and y < 0 and y - d5 / 2 < ty:                # the one under the tab's foot fillet
            hr = (P["head_relief"] + fit) / 2
            body -= Pos(x, y, t + rf / 2 + 0.5) * Cylinder(hr, rf + 1)
            hole_features(feats, "head_relief", (x, y, t + rf), (0, 0, -1), hr, depth=rf)
    d3 = hole_d(P["tab_bolt"], "clearance", fit, P["hole_sizes"])
    for k, z in enumerate(P["tab_holes"]):
        body -= Pos(0, ty + tt / 2, z) * Rot(90, 0, 0) * Cylinder(d3 / 2, tt + 0.02)
        hole_features(feats, f"tab{k + 1}", (0, ty, z), (0, 1, 0), d3 / 2, depth=tt, bolt=P["tab_bolt"], kind="clearance")
    return finish(body, label=LABEL, params=P, features=feats, reference=REFERENCE,
                  printability=Print("disc down (z = 0 on the bed)", "bottom", False,
                                     "the tab stands vertical; the fillets and gussets need no supports"))
