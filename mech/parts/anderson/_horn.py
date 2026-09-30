"""The servo horn pocket in Anderson's STEPs (the hero arm's hand): a six-arm star whose arms taper
from `hub_half` either side of the axis at the centre to a semicircular tip (radius r at radius R).
The flanks run straight from the hub to the ends of each tip's semicircle (not tangent to it: the
STEP's tips sweep exactly 180 deg), and adjacent flanks meet between the arms."""

from __future__ import annotations

import math


def horn_star_tapered(arms: int, R: float, r: float, hub_half: float):
    from build123d import Edge, Face, Wire

    def rot(p, a):
        c, s = math.cos(a), math.sin(a)
        return (p[0] * c - p[1] * s, p[0] * s + p[1] * c)

    # the corner between arm 0 (along +x) and arm 1: arm 0's upper flank meets the bisector
    step = 2 * math.pi / arms
    slope = (hub_half - r) / R                           # the flank: y = hub_half - slope * x
    b = step / 2
    t = hub_half / (math.sin(b) + slope * math.cos(b))
    corner = (t * math.cos(b), t * math.sin(b))
    edges = []
    for k in range(arms):
        a = k * step
        lo = rot((R, -r), a)
        tip = rot((R + r, 0.0), a)
        hi = rot((R, r), a)
        prev = rot(corner, a - step)
        nxt = rot(corner, a)
        edges += [Edge.make_line((*prev, 0), (*lo, 0)), Edge.make_three_point_arc((*lo, 0), (*tip, 0), (*hi, 0)),
                  Edge.make_line((*hi, 0), (*nxt, 0))]
    return Face(Wire(edges))
