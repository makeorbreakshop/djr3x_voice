"""Left Side w alignment holes (Hunter) - see side.py."""

from __future__ import annotations

from . import side as _side
from ._common import finish

REFERENCE = "Left Side w alignment holes.stl"
LABEL = "Left side (parametric)"
EXPORT_FRAME = _side.SHELL_FROM_HEAD @ _side.rot_x(_side.TILT_DEG)
DEFAULTS = _side.DEFAULTS
UNMODELLED = _side.UNMODELLED


def make(params: dict | None = None, **kw):
    body, feats, P, frame = _side.make("left", params, **kw)
    return finish(body, label=LABEL, params=P, features=feats, reference=REFERENCE, frame=frame,
                  printability=_side.printability())
