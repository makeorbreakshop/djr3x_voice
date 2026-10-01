"""The column's connected motion (manifest `gears`): each pinion turned by its gear entry stays in mesh
with its rack or sector through the joint's travel, and the opposite sign does not - so the sign is
the geometry's, not a guess.

    cd mech && .venv/bin/python -m pytest parts/column/tests/test_column_gears.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

MECH = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(MECH))


def _man(m):
    import manifold3d as mf

    return mf.Manifold(mf.Mesh(vert_properties=np.asarray(m.vertices, np.float32), tri_verts=np.asarray(m.faces, np.uint32)))


def _T(m, x):
    return m.transform(np.asarray(x)[:3, :].astype(np.float32))


@pytest.fixture(scope="module")
def column():
    try:
        from assemblies.column.assembly import build
        return build()
    except FileNotFoundError as e:  # vendor CAD (gitignored) not on this machine
        pytest.skip(f"vendor files missing: {e}")


def _sweep(asm, g, mate_mesh, mate_matrix, values, sign=1.0):
    from workbench.kinematics import gear_matrix, link_matrices

    pin = _man(asm.part(g.parts[0]).mesh)
    mate = _man(mate_mesh)
    out = []
    for v in values:
        lm = link_matrices(asm, {g.joint: v} if not g.joint_assembly else {})[g.link]
        k0 = g.deg_per_unit
        g.deg_per_unit = sign * k0
        try:
            out.append((_T(pin, gear_matrix(g, lm, v)) ^ _T(mate, mate_matrix(v))).volume())
        finally:
            g.deg_per_unit = k0
    return out


def test_lift_pinion_rolls_up_the_rack(column):
    g = next(x for x in column.gears if x.id == "g_lift")
    rack = column.part("col_lift_rack").mesh
    vals = (0.4, 0.63, 1.25, 7.3, -11.1, -37.0, 45.0)
    good = _sweep(column, g, rack, lambda v: np.eye(4), vals)
    bad = _sweep(column, g, rack, lambda v: np.eye(4), vals, sign=-1.0)
    assert max(good) < 2.0, good                     # the rest phase's residue only
    assert max(bad) > 10.0, bad


def test_ring_pinions_turn_with_their_sectors(column):
    from assemblies.column.assembly import _anderson_sector
    from workbench.kinematics import rot

    for key in ("lower", "top"):
        g = next(x for x in column.gears if x.id == f"g_{key}_ring")
        sec = _anderson_sector(key)
        vals = (1.0, 5.3, -7.7, 12.0)
        good = _sweep(column, g, sec, lambda v: rot((0, 1, 0), v), vals)
        bad = _sweep(column, g, sec, lambda v: rot((0, 1, 0), v), vals, sign=-1.0)
        assert max(good) < 1.0, (key, good)
        assert max(bad) > 50.0, (key, bad)


def test_every_servo_drives_something(column):
    """Each column servo is named by a joint's drive or a gear (none is left unconnected)."""
    named = {s for j in column.joints for s in j.drive.get("servos", [])} | {g.servo for g in column.gears}
    servos = {p.id for p in column.parts if p.cls == "servo"}
    assert servos <= named, servos - named
