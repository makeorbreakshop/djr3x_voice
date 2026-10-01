"""The column's parts: each builds closed, carries its mate features, and keeps the layout's
clearances to the kit (numbers measured from the kit, parts/column/_layout.py).

    cd mech && .venv/bin/python -m pytest parts/column/tests -q
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

MECH = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(MECH))

from parts.column import PARTS, _layout as L  # noqa: E402

CASES = [(m, {}) for m in sorted(PARTS)] + [("parts.column.ring_pinion", {"ring": "top"}),
                                             ("parts.column.drive_bracket", {"ring": "top"})]
CASES += [("parts.column.sled", {"kind": k}) for k in ("back", "side_l", "side_r", "bottom", "top", "hanger")]
CASES += [("parts.column.purchased", {"kind": k}) for k in ("vwheel", "ecc", "tnut", "collar")]


@pytest.mark.parametrize("mod,params", CASES)
def test_builds_closed_with_features(mod, params):
    from workbench.geom import parametric_mesh

    m, feats, _ = parametric_mesh(mod, params)
    assert m.is_watertight, f"{mod} {params}: not closed"
    assert m.volume > 0
    assert feats, f"{mod}: no mate features"
    for k, f in feats.items():
        assert f["type"] in ("axis", "plane", "spline"), k
        if f["type"] == "axis":
            assert abs(np.linalg.norm(f["d"]) - 1) < 1e-6


def test_column_clears_the_pedestal():
    """The pedestal's back grille pocket at r 59.7 (y 165-265) and its wall at r 86-88."""
    half = L.HALF
    assert half < 59.7 - 5.0                         # back face to the pocket
    assert math.hypot(half, half) < 86.0 - 10.0      # corners to the wall
    assert half / math.cos(math.radians(25)) < 66.0  # the pocket's sides (r 66 at 160 deg)


def test_lift_travel_and_ratio():
    assert L.PAN == (-135.0, 135.0)
    assert L.LIFT[0] <= -37.0 and L.LIFT[1] >= 37.0
    servo_deg = max(abs(v) for v in L.LIFT) / L.LIFT_MM_PER_DEG
    assert servo_deg <= 135.0 + 1e-6
    # the sled's cassette stays under the top plate at full lift
    top = L.CASE["y0"] + L.CASE["h"] + L.LIFT[1]
    assert top < L.Y_POST_TOP - 2.0
    # the lift pinion's pitch point stays on the rack's teeth over the travel
    assert L.RACK["teeth_y"][0] <= L.LIFT_PINION_C[1] + L.LIFT[0] and L.LIFT_PINION_C[1] + L.LIFT[1] <= L.RACK["teeth_y"][1]


def test_head_height_unchanged():
    # Hunter's coupler: bore stop 42 mm under the gimbal centre (body y 738.3)
    assert abs(L.TUBE["top"] - (738.3 - 42.0)) < 1e-6


def test_clock_spring_allows_the_pan():
    """Direct drive: the servo's own end (+-150) is the pan's; the ribbon must allow at least that."""
    from parts.column.clockspring_case import ribbon_turns

    assert ribbon_turns(250.0) * 360 >= 2 * L.PAN_MECH
    assert L.PAN[1] <= L.PAN_MECH


def test_sled_between_the_posts():
    """The sled's plates clear the posts (inner faces at +-30 where |the other| >= 30) and the rack; the
    V-wheels' tips reach the slot."""
    S = L.SLED
    assert S["z_out"] < L.POST_C - 10.0                 # front/back plates inside the posts' inner Z faces
    assert -S["half_x"] > L.RACK["x"] + L.RACK["face"] / 2   # the back plate's right edge clears the rack (x -35)
    assert abs(L.WHEEL_X + L.VWHEEL["od"] / 2 - (L.POST_C - 10.0 + L.VWHEEL["tip_in"])) < 1e-9
    assert S["z_out"] + L.ECC["l"] + L.VWHEEL["w"] / 2 - L.POST_C < 1e-6        # the wheel centred on the slot
    assert L.WHEEL_Y[0] - L.VWHEEL["od"] / 2 >= L.BOTTOM["y1"] and L.WHEEL_Y[1] + L.VWHEEL["od"] / 2 <= L.HOUSING["y1"]


def test_ring_pinions_where_anderson_put_them():
    """Same centre and height as Anderson's pinions (assemblies/r3x_animation): his sectors mesh."""
    assert L.RING_PINION_C == (-9.8, -83.0)
    assert abs(L.RING["lower"]["y0"] - (342.0 + 3 + 4 + 16.8 + 7.5)) < 1e-6
    assert abs(L.RING["top"]["y0"] - 462.0) < 1e-6
