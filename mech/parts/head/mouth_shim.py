"""Mouth shim (ours, new): the kit's mouth (H_M_1) was drawn to sit on the kit's own bottom shell;
on Hunter's head bottom its nearest face stands ~0.6 mm off. A thin printed (or cut) pad fills
that gap where the two come closest, so the mouth can be glued on a real contact.

Design frame: the pad centred on the origin, its thickness along +Y (the bottom face at y = 0
on the head bottom, the top face at y = t under the mouth). The assembly places it at the
measured closest point, +Y along the gap.
"""

from __future__ import annotations

from ._common import Print, finish, plane

REFERENCE = None
LABEL = "Mouth shim (parametric)"

DEFAULTS = dict(t=0.6, w=8.0, l=8.0, corner=1.5)


def make(params: dict | None = None, **kw):
    from build123d import Box, Pos, fillet, Axis

    P = dict(DEFAULTS)
    P.update(params or {})
    P.update(kw)
    t, w, l = P["t"], P["w"], P["l"]
    part = Pos(0, t / 2, 0) * Box(w, t, l)
    if P["corner"] > 0:
        part = fillet(part.edges().filter_by(Axis.Y), P["corner"])
    feats = {"face_top": plane((0, t, 0), (0, 1, 0)), "face_bottom": plane((0, 0, 0), (0, -1, 0))}
    return finish(part, label=LABEL, params=P, features=feats, reference=REFERENCE,
                  printability=Print("flat", "face_bottom", False, f"{t:g} mm: two layers at 0.3, or cut from sheet"))
