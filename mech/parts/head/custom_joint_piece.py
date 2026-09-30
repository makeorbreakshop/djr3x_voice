"""Custom Joint Piece - the gimbal cross (Hunter). Reference: `Custom Joint Piece.step`.

A joint ring (joint_ring.py): the tilt pivots (head X) screw through its side walls from the
U-joint's bearings, the roll pivots (head Z) through its end walls from the pillow-block
bearings. In `Head Joint Asm.step` it sits at (0, 67, 0) turned -90 deg about Y, so its local
-X face is the droid's left (+X) and its local -Z end is the front: the mate names follow that.
"""

from __future__ import annotations

from ._common import Print, finish, hole_features, plane
from .joint_ring import DEFAULTS, build, resolve

REFERENCE = "RX Neck Joint Member V1.stl"   # the STEP's mesh twin (identical solid, same frame): the
                                              # workbench's regression loads meshes only
REFERENCE_CAD = "Custom Joint Piece.step"     # Hunter's B-rep: what the defaults are read from
LABEL = "Custom Joint Piece (parametric)"


def make(params: dict | None = None, **kw):
    P = resolve(params, kw)
    part, holes, d = build(P)
    feats: dict = {}
    names = {"xn": "tilt_l", "xp": "tilt_r", "zn": "roll_f", "zp": "roll_b"}
    for k, name in names.items():
        p, n = holes[k]
        hole_features(feats, name, p, n, d / 2, depth=P["wall"], bolt=P["bolt"], kind=P["hole"])
    feats["face_top"] = plane((0, P["thick"] / 2, 0), (0, 1, 0))
    feats["face_bottom"] = plane((0, -P["thick"] / 2, 0), (0, -1, 0))
    return finish(part, label=LABEL, params=P, features=feats, reference=REFERENCE,
                  printability=Print("flat (an 8 mm face on the bed)", "face_bottom", False,
                                     "the four 3.3 mm holes print horizontally; no supports"))
