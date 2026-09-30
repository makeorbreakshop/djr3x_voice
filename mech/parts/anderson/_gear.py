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


def rounded_teeth(teeth: int, tip: tuple, fillet: tuple, internal: bool = False):
    """One rounded tooth of Anderson's ring drives, in the tooth frame (axis +X): (polygon, tip
    centre, fillet centres). A round tip (radius tip[0] centred on radius tip[1]) joined by the cross
    tangents to round root fillets (radius fillet[0] centred on radius fillet[1]) mid-gap. Internal
    teeth point inward (their fillets sit outside the tips)."""
    rt, Rt = tip
    rf, Rf = fillet
    phi = 180.0 / teeth
    T = (Rt, 0.0)

    def flank(sgn):
        F = (Rf * math.cos(math.radians(sgn * phi)), Rf * math.sin(math.radians(sgn * phi)))
        D = (F[0] - T[0], F[1] - T[1])
        L = math.hypot(*D)
        base = math.atan2(D[1], D[0])
        a = math.acos(min(1.0, (rf + rt) / L))
        cands = [(math.cos(base + s * a), math.sin(base + s * a)) for s in (1, -1)]
        n = max(cands, key=lambda v: sgn * v[1])            # the normal pointing away from the tooth's axis
        return (T[0] + rt * n[0], T[1] + rt * n[1]), (F[0] - rf * n[0], F[1] - rf * n[1]), F

    (tu, fu, Fu), (tl, fl, Fl) = flank(1), flank(-1)
    far = Rf + (2 * rf + 1.0 if internal else -(2 * rf + 1.0))
    return [tu, fu, (far, fu[1]), (far, fl[1]), fl, tl], T, (Fu, Fl)


def rounded_spur(teeth: int, tip: tuple, fillet: tuple, phase_deg: float = 0.0):
    """An external gear of rounded teeth (see rounded_teeth): the 2D face (local XY), a tooth on
    `phase_deg`."""
    from build123d import BuildSketch, Circle, Locations, Mode, Polygon

    poly, T, fcs = rounded_teeth(teeth, tip, fillet)
    with BuildSketch() as sk:
        Circle(fillet[1])
        for k in range(teeth):
            ang = phase_deg + 360.0 * k / teeth
            Polygon(*[_rot(p, ang) for p in poly], align=None)
            with Locations(_rot(T, ang)):
                Circle(tip[0])
        for k in range(teeth):
            ang = phase_deg + 360.0 * k / teeth
            with Locations(*[_rot(F, ang) for F in fcs]):
                Circle(fillet[0], mode=Mode.SUBTRACT)
    return sk.sketch_local


def rounded_internal_band(teeth: int, tip: tuple, fillet: tuple, phase_deg: float, a0: float, a1: float,
                          outer_r: float):
    """The toothed band of an internal gear sector: from the teeth out to `outer_r`, between angles
    a0 and a1 (deg; teeth whose centre lies inside the range). A 2D face (local XY)."""
    from build123d import BuildSketch, Circle, Locations, Mode, Polygon

    poly, T, fcs = rounded_teeth(teeth, tip, fillet, internal=True)
    pitch = 360.0 / teeth
    k0 = math.ceil((a0 - phase_deg) / pitch) - 1
    k1 = math.floor((a1 - phase_deg) / pitch) + 1
    with BuildSketch() as sk:
        Circle(outer_r)
        Circle(fillet[1], mode=Mode.SUBTRACT)
        for k in range(k0, k1 + 1):
            ang = phase_deg + pitch * k
            Polygon(*[_rot(p, ang) for p in poly], align=None)
            with Locations(_rot(T, ang)):
                Circle(tip[0])
        for k in range(k0, k1 + 1):
            ang = phase_deg + pitch * k
            with Locations(*[_rot(F, ang) for F in fcs]):
                Circle(fillet[0], mode=Mode.SUBTRACT)
        n = 48
        big = 2 * outer_r
        wedge = [(0.0, 0.0)] + [(big * math.cos(math.radians(a0 + (a1 - a0) * i / n)),
                                 big * math.sin(math.radians(a0 + (a1 - a0) * i / n))) for i in range(n + 1)]
        Polygon(*wedge, align=None, mode=Mode.INTERSECT)
    return sk.sketch_local


def _rot(p, ang_deg):
    a = math.radians(ang_deg)
    return (p[0] * math.cos(a) - p[1] * math.sin(a), p[0] * math.sin(a) + p[1] * math.cos(a))
