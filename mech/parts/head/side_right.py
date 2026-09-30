"""Right Side w alignment holes (Hunter) - the left piece mirrored about the seam; see side.py."""

from __future__ import annotations

from . import side as _side
from ._common import finish

REFERENCE = "Right Side w alignment holes.stl"
LABEL = "Right side (parametric)"
EXPORT_FRAME = _side.SHELL_FROM_HEAD @ _side.rot_x(_side.TILT_DEG)
DEFAULTS = _side.DEFAULTS
UNMODELLED = [dict(u, box=[[-u["box"][1][0], u["box"][0][1], u["box"][0][2]],
                           [-u["box"][0][0], u["box"][1][1], u["box"][1][2]]]) for u in _side.UNMODELLED]


def make(params: dict | None = None, **kw):
    body, feats, P, frame = _side.make("right", params, **kw)
    return finish(body, label=LABEL, params=P, features=feats, reference=REFERENCE, frame=frame,
                  printability=_side.printability())
