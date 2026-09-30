"""RX Neck Joint Member V1 (Hunter). Reference: `RX Neck Joint Member V1.stl`.

The same solid as the Custom Joint Piece (joint_ring.py; volume and bounds agree to 0.5 mm3):
Hunter ships it twice. Its place on the neck is not in his files (the workbench flags it as
inferred); the mates name its holes by where they face in its own frame:
`pin` = the side hole on +X (the one the coupler's side pin reaches), `pin_b` opposite,
`end_a` / `end_b` = the end-wall holes (+Z / -Z).
"""

from __future__ import annotations

from ._common import Print, finish, hole_features, plane
from .joint_ring import build, resolve

REFERENCE = "RX Neck Joint Member V1.stl"
LABEL = "RX Neck Joint Member V1 (parametric)"


def make(params: dict | None = None, **kw):
    P = resolve(params, kw)
    part, holes, d = build(P)
    feats: dict = {}
    for k, name in {"xp": "pin", "xn": "pin_b", "zp": "end_a", "zn": "end_b"}.items():
        p, n = holes[k]
        hole_features(feats, name, p, n, d / 2, depth=P["wall"], bolt=P["bolt"], kind=P["hole"])
    feats["face_top"] = plane((0, P["thick"] / 2, 0), (0, 1, 0))
    feats["face_bottom"] = plane((0, -P["thick"] / 2, 0), (0, -1, 0))
    return finish(part, label=LABEL, params=P, features=feats, reference=REFERENCE,
                  printability=Print("flat (an 8 mm face on the bed)", "face_bottom", False,
                                     "identical to the Custom Joint Piece; no supports"))
