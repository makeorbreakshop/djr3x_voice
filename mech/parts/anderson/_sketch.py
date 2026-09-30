"""A closed 2D profile from lines and centred arcs, as a build123d Face on the XY plane.

    profile([(x0, y0), (x1, y1), ("arc", (cx, cy)), (x2, y2), ...])

Points are joined by lines; ("arc", centre) makes the next segment an arc about `centre` from the
previous point to the next one (the short way round).
"""

from __future__ import annotations

import math


def profile(items):
    from build123d import Edge, Face, Wire

    pts, edges = [], []
    i = 0
    seq = list(items)
    cur = seq[0]
    first = cur
    i = 1
    while i <= len(seq):
        nxt = seq[i] if i < len(seq) else first
        if isinstance(nxt, tuple) and nxt and nxt[0] == "arc":
            c = nxt[1]
            end = seq[i + 1] if i + 1 < len(seq) else first
            a0 = math.atan2(cur[1] - c[1], cur[0] - c[0])
            a1 = math.atan2(end[1] - c[1], end[0] - c[0])
            da = (a1 - a0 + math.pi) % (2 * math.pi) - math.pi
            r = math.hypot(cur[0] - c[0], cur[1] - c[1])
            mid = (c[0] + r * math.cos(a0 + da / 2), c[1] + r * math.sin(a0 + da / 2), 0.0)
            edges.append(Edge.make_three_point_arc((*cur, 0.0), mid, (*end, 0.0)))
            cur = end
            i += 2
        else:
            if math.dist(cur, nxt) > 1e-9:
                edges.append(Edge.make_line((*cur, 0.0), (*nxt, 0.0)))
            cur = nxt
            i += 1
    return Face(Wire(edges))
