"""Small build123d helpers for the column parts (all modelled in the body frame, _layout.py)."""

from __future__ import annotations

import math

import numpy as np

from parts.head._common import hole_features, plane  # noqa: F401  (re-exported for the part modules)


def box(x0, x1, y0, y1, z0, z1):
    from build123d import Box, Pos

    return Pos((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2) * Box(x1 - x0, y1 - y0, z1 - z0)


def cyl(p0, d, r, length):
    """A cylinder of radius r from p0 along d (unit-ish) for `length`."""
    from build123d import Align, Cylinder, Plane

    d = np.asarray(d, float) / np.linalg.norm(d)
    return (Plane(origin=tuple(float(v) for v in p0), z_dir=tuple(float(v) for v in d)).location
            * Cylinder(r, length, align=(Align.CENTER, Align.CENTER, Align.MIN)))


def cyl_y(x, z, r, y0, y1):
    return cyl((x, y0, z), (0, 1, 0), r, y1 - y0)


def ring_y(r_in, r_out, y0, y1, x=0.0, z=0.0):
    return cyl_y(x, z, r_out, y0, y1) - cyl_y(x, z, r_in, y0 - 0.01, y1 + 0.01)


def hexagon(p0, d, af, length, clock_deg=0.0):
    """A hex prism (across flats `af`) from p0 along d, a flat square to `clock_deg` about d."""
    from build123d import BuildSketch, Plane, RegularPolygon, extrude

    d = np.asarray(d, float) / np.linalg.norm(d)
    x = np.cross(d, [1, 0, 0] if abs(d[0]) < 0.9 else [0, 0, 1])
    x /= np.linalg.norm(x)
    with BuildSketch(Plane(origin=tuple(float(v) for v in p0), x_dir=tuple(x), z_dir=tuple(d))) as s:
        RegularPolygon(af / math.sqrt(3), 6, rotation=clock_deg)
    return extrude(s.sketch, amount=length)


def polygon_y(pts_xz, y0, y1):
    """A prism of the XZ polygon `pts_xz` from y0 to y1."""
    from build123d import BuildSketch, Plane, Polygon, extrude

    # Plane.XZ: local (u, v) = (x, z), normal -Y; build on a plane whose normal is +Y
    pl = Plane(origin=(0, y0, 0), x_dir=(1, 0, 0), z_dir=(0, 1, 0))   # local v = -z
    with BuildSketch(pl) as s:
        Polygon(*[(float(x), float(-z)) for x, z in pts_xz], align=None)
    return extrude(s.sketch, amount=y1 - y0)


def polygon_z(pts_xy, z0, z1):
    from build123d import BuildSketch, Plane, Polygon, extrude

    with BuildSketch(Plane.XY.offset(z0)) as s:
        Polygon(*[(float(x), float(y)) for x, y in pts_xy], align=None)
    return extrude(s.sketch, amount=z1 - z0)


def clearance_hole(body, feats, name, entry, d, bolt, depth, r=None, kind="clearance"):
    """A through/clearance hole (ISO 273 medium, parts/head HOLES) cut and recorded as hole_/face_."""
    from parts.head._common import hole_d

    rr = r if r is not None else hole_d(bolt, "clearance") / 2
    body = body - cyl(np.asarray(entry, float) - np.asarray(d, float) * 0.01, d, rr, depth + 0.02)
    hole_features(feats, name, entry, d, rr, depth=depth, bolt=bolt, kind=kind)
    return body


def tapped_hole(body, feats, name, entry, d, bolt, depth):
    """A tapped hole in metal (modelled at the tap drill: the thread's minor diameter)."""
    from parts.head._common import hole_d

    rr = hole_d(bolt, "tap") / 2
    body = body - cyl(np.asarray(entry, float) - np.asarray(d, float) * 0.01, d, rr, depth + 0.01)
    hole_features(feats, name, entry, d, rr, depth=depth, bolt=bolt, kind="tapped")
    return body


def rot_about_y(deg):
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    m = np.eye(4)
    m[0, 0], m[0, 2], m[2, 0], m[2, 2] = c, s, -s, c
    return m


def at_angle(r, deg):
    """(x, z) on a circle about the body axis: deg from +Z (front) toward +X (the droid's left)."""
    a = math.radians(deg)
    return r * math.sin(a), r * math.cos(a)


def involute_outline(teeth: int, module: float, pa_deg: float = 20.0, n_flank: int = 8):
    """A standard external involute spur gear's outline (addendum m, dedendum 1.25 m, no profile
    shift; the root drawn as an arc): [(x, y)] counter-clockwise, a tooth centred on +X."""
    rp = module * teeth / 2
    a = math.radians(pa_deg)
    rb = rp * math.cos(a)
    ra, rf = rp + module, rp - 1.25 * module
    inv = lambda x: math.tan(x) - x  # noqa: E731
    psi_p = math.pi / (2 * teeth) + inv(a)            # half tooth angle at the base-involute reference

    def half(r):
        r = max(r, rb)
        return psi_p - inv(math.acos(min(1.0, rb / r)))

    pts = []
    rs = np.linspace(max(rf, rb), ra, n_flank)
    for k in range(teeth):
        c = 2 * math.pi * k / teeth
        flank_l = [(r, c - half(r)) for r in rs]
        flank_r = [(r, c + half(r)) for r in rs[::-1]]
        if rf < rb:
            flank_l = [(rf, c - half(rb))] + flank_l
            flank_r = flank_r + [(rf, c + half(rb))]
        tip = [(ra, c - half(ra) + t * 2 * half(ra)) for t in np.linspace(0.2, 0.8, 3)]
        nxt = 2 * math.pi * (k + 1) / teeth
        root = [(rf, c + half(max(rf, rb)) + t * (nxt - c - 2 * half(max(rf, rb)))) for t in np.linspace(0.25, 0.75, 3)]
        pts += flank_l + tip + flank_r + root
    return [(r * math.cos(t), r * math.sin(t)) for r, t in pts]
