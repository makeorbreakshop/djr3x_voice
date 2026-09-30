"""The parts library and our parametric remodels.

    cd mech && .venv/bin/python -m pytest parts/tests -q

Parametric models and the catalog need nothing vendored. The remodel regressions compare
against Hunter's reference STLs and the vendor-frame checks against cached vendor STEPs; both
skip when those files are not on this machine (they are never in git).
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest

MECH = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(MECH))

from parts import models  # noqa: E402
from parts.fetch import cached_file  # noqa: E402
from parts.hunter import REMODELS, compare, neck_coupler  # noqa: E402
from parts.library import resolve, spec_part  # noqa: E402

REF = MECH / "vendor" / "hunter_head"

# Remodel tolerances (mm). The coupler and member are exact rebuilds; the plate and the visor
# mount are rebuilt from their interfaces with straight walls where the reference leans or is
# gusseted (their numbers are reported in the manifest and TESTS.md).
TOL = {
    "neck_coupler": {"p95_mm": 0.05, "mean_mm": 0.02, "volume": 0.01},
    "neck_joint_member": {"p95_mm": 0.05, "mean_mm": 0.02, "volume": 0.01},
    "base_plate": {"p95_mm": 2.5, "mean_mm": 0.5, "volume": 0.02},
    "visor_servo_mount": {"p95_mm": 2.0, "mean_mm": 0.5, "volume": 0.15},
}
REF_FILE = {"neck_coupler": "RXNeckCouplerV1.stl", "neck_joint_member": "RX Neck Joint Member V1.stl",
            "base_plate": "RX Head Mech Base Plate V4.stl", "visor_servo_mount": "Visor Servo Mount.stl"}


@pytest.mark.parametrize("name", sorted(REMODELS))
def test_remodel_matches_reference(name):
    ref = REF / REF_FILE[name]
    if not ref.exists():
        pytest.skip("reference STL not on this machine (mech/vendor is gitignored)")
    import trimesh

    r = REMODELS[name]()
    m = r.mesh()
    R = trimesh.load(str(ref), force="mesh")
    if name == "visor_servo_mount":
        from assemblies.hunter_head.assembly import square_mount

        R.apply_transform(square_mount(R))
    s = compare(m, R, 8000)
    t = TOL[name]
    print(name, json.dumps({k: round(v, 3) if isinstance(v, float) else v for k, v in s.items()}))
    assert m.is_watertight
    assert s["p95_mm"] <= t["p95_mm"] and s["mean_mm"] <= t["mean_mm"]
    assert abs(s["volume_ratio"] - 1) <= t["volume"]


@pytest.mark.parametrize("name", sorted(REMODELS))
def test_remodel_features_and_params(name):
    r = REMODELS[name]()
    assert r.params and r.features
    for f in r.features.values():
        assert f["type"] in ("axis", "plane")
        if f["type"] == "axis":
            assert math.isclose(np.linalg.norm(f["d"]), 1.0, abs_tol=1e-9)


def test_coupler_variant_for_anderson_tube():
    a, b = neck_coupler().mesh(), neck_coupler(bore_d=26.0).mesh()
    assert b.volume > a.volume  # a thicker wall around the smaller tube
    assert neck_coupler(bore_d=26.0).features["bore"]["r"] == 13.0


@pytest.mark.parametrize("spec,extent", [
    ({"type": "shcs", "thread": "M4", "length_mm": 12}, (7.0, 16.0)),      # ISO 4762: head 7 x 4
    ({"type": "shcs", "thread": "#6-32", "length_mm": 6.35}, (5.5, 9.9)),  # ASME B18.3
    ({"type": "bhcs", "thread": "M3", "length_mm": 8}, (5.7, 9.65)),
])
def test_parametric_screws_to_spec(spec, extent):
    m = spec_part("screw", spec).mesh
    ext = m.extents
    assert abs(max(ext[0], ext[1]) - extent[0]) < 0.4
    assert abs(ext[2] - extent[1]) < 0.6
    assert m.bounds[1][2] == pytest.approx(spec["length_mm"], abs=1e-6)  # shank along +Z from the head


def test_nut_has_its_bore_and_servo_case():
    nut = models.nut({"type": "lock_nut", "thread": "M4"})
    assert not nut.contains([[0, 0, 1.0]])[0]  # the bore is open
    sv = models.servo({"case": "standard"})
    assert sv.bounds[1][1] == pytest.approx(0.0, abs=1e-6)  # spline top at 0


def test_catalog_statuses():
    cat = json.loads((MECH / "parts" / "catalog.json").read_text())
    assert cat["schema"] == "r3x.mech.parts"
    for p in cat["parts"]:
        assert p["cad"]["status"] in ("vendor", "parametric", "placeholder")
        assert p["qty"] == sum(p["used_by"].values())


@pytest.mark.parametrize("pn,expect", [
    ("2913-0004-0241", {"ball_at_origin": True}),
    ("2000-0025-0002", {"spline_top_y": 0.0}),
    ("1916-0014-0048", {"face_y": 0.0}),
])
def test_vendor_frames(pn, expect):
    if cached_file("gobilda", pn) is None:
        pytest.skip("vendor CAD not in the cache on this machine")
    r = resolve({"vendor": "gobilda", "pn": pn, "kind": "hardware", "spec": {}}, want_mesh=True)
    assert r.status == "vendor"
    b = r.mesh.bounds
    if "spline_top_y" in expect:
        assert b[1][1] == pytest.approx(0.0, abs=0.05)
    if "face_y" in expect:
        assert b[0][1] == pytest.approx(0.0, abs=0.05)
    if "ball_at_origin" in expect:
        assert abs(b[0][0] + b[1][0]) < 0.1 and b[0][2] < -6 and b[1][2] > 24
