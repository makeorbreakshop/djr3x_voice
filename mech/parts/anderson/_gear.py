"""Anderson's gear teeth: not involutes - printed square-rooted trapezoids (the lift gear and rack)
and tapered teeth with a round tip (the pan gear). One tooth is described in its own frame (tooth
axis +Y, x across) and patterned round a root circle or along a rack.

    trapezoid(root_w, tip_w, wall_to, tip_at)   straight walls root_w wide up to `wall_to`, then
                                                flanks to a tip land tip_w wide at `tip_at`
    round_tip(root_w_at, root_r, tip_r, tip_at) straight flanks from root_w_at (width at the root
                                                circle) to a semicircular tip of radius tip_r
                                                centred at `tip_at`
"""

from __future__ import annotations

import math


def trapezoid(root_w: float, tip_w: float, wall_to: float, tip_at: float, base: float):
    """Tooth outline points (x, y), tooth axis +Y, from y = base (inside the root) to the tip."""
    a, b = root_w / 2, tip_w / 2
    return [(a, base), (a, wall_to), (b, tip_at), (-b, tip_at), (-a, wall_to), (-a, base)]


def spur(teeth: int, root_r: float, tooth, phase_deg: float = 90.0):
    """A spur gear's 2D face (local XY): the root disc plus `teeth` copies of `tooth(angle)` (adds
    one tooth, drawn along +Y, turned by `angle` deg, inside a BuildSketch), the first on `phase_deg`."""
    from build123d import BuildSketch, Circle

    with BuildSketch() as sk:
        Circle(root_r)
        for k in range(teeth):
            tooth(phase_deg - 90.0 + 360.0 * k / teeth)
    return sk.sketch_local


def polygon_tooth(points):
    """A tooth callable for `spur` from an outline in the tooth frame."""
    from build123d import Polygon

    def add(ang):
        Polygon(*[_rot(p, ang) for p in points], align=None)

    return add


def round_tip_tooth(root_half: float, root_r: float, tip_r: float, tip_at: float, base: float):
    """Straight flanks from (+/-root_half on the root circle) to (+/-tip_r, tip_at), closed over the
    top by a semicircle of radius tip_r centred at (0, tip_at)."""
    from build123d import Edge, Face, Wire, add as b3d_add

    y0 = math.sqrt(root_r ** 2 - root_half ** 2)
    # extend each flank inward to y = base along its own line
    sx = (tip_r - root_half) / (tip_at - y0)
    xb = root_half + sx * (base - y0)
    pts = [(xb, base), (tip_r, tip_at), (0.0, tip_at + tip_r), (-tip_r, tip_at), (-xb, base)]

    def add(ang):
        P = [(*_rot(p, ang), 0.0) for p in pts]
        wire = Wire([Edge.make_line(P[4], P[0]), Edge.make_line(P[0], P[1]),
                     Edge.make_three_point_arc(P[1], P[2], P[3]), Edge.make_line(P[3], P[4])])
        b3d_add(Face(wire))

    return add


def _rot(p, ang_deg):
    a = math.radians(ang_deg)
    return (p[0] * math.cos(a) - p[1] * math.sin(a), p[0] * math.sin(a) + p[1] * math.cos(a))
