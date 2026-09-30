"""Each parametric head part against the Hunter file its defaults reproduce.

    cd mech && .venv/bin/python -m pytest parts/head/tests -q            # add -s for the numbers

Per part: a valid closed solid; volume within 2 %; bounding box within 0.3 mm; a symmetric surface
deviation (mean <= 0.3 mm for mechanical parts; shells report theirs against a looser bound);
every hole feature matched to a round hole found *on the reference* (closed circular loops in
sections along X/Y/Z) within 0.2 mm, and no hole on the reference left without one. Parts from a
STEP are also checked against the B-rep's own cylinders. The references live in mech/vendor
(gitignored): without them those tests skip; the parameter/feature tests always run.

Deviation heatmaps: set HEAD_PARTS_HEATMAPS=<dir> (a scratch directory - never commit renders of
vendored parts).
"""

from __future__ import annotations

import importlib
import json
import math
import os
import sys
from pathlib import Path

import numpy as np
import pytest

MECH = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(MECH))

from parts.head import PARTS  # noqa: E402
from parts.head.tests import regress as R  # noqa: E402

MODULES = sorted(set(PARTS.values()))
# per part: (mean deviation bound mm, volume tolerance)
BOUNDS = {m: (0.3, 0.02) for m in MODULES}
RESULTS: dict = {}


def _mod(name):
    return importlib.import_module(name)


def _ref_path(mod):
    return R.REF_DIR / mod.REFERENCE


@pytest.fixture(scope="module")
def built():
    return {name: _mod(name).make() for name in MODULES}


@pytest.mark.parametrize("name", MODULES)
def test_contract(name, built):
    """What the workbench's swap hook reads: a valid solid, features, params, reference, printability."""
    p = built[name]
    mod = _mod(name)
    assert p.is_valid and p.volume > 0
    assert p.reference == mod.REFERENCE
    assert set(p.printability) == {"orientation", "base", "supports", "note"}
    assert p.params and p.features
    for k, f in p.features.items():
        assert f["type"] in ("axis", "plane"), k
        v = f["d"] if f["type"] == "axis" else f["n"]
        assert math.isclose(np.linalg.norm(v), 1.0, abs_tol=1e-9), k
        if k.startswith("hole_"):
            assert f"face_{k[5:]}" in p.features, f"{k} has no entry face"
    # the hook calls make(params) with a dict; defaults reproduce make()
    assert math.isclose(mod.make({}).volume, p.volume, rel_tol=1e-9)
    with pytest.raises(TypeError):
        mod.make({"no_such_parameter": 1})


@pytest.mark.parametrize("name", MODULES)
def test_matches_reference(name, built):
    mod = _mod(name)
    if not _ref_path(mod).exists():
        pytest.skip("reference not on this machine (mech/vendor is gitignored)")
    p = built[name]
    ref = R.load_ref(mod.REFERENCE)
    c = R.compare(p, ref)
    h = R.match_holes(p.features, ref, frame=getattr(mod, "EXPORT_FRAME", None))
    RESULTS[name] = dict(c, holes=len(h["matched"]), hole_err_mm=h["max_err_mm"])
    print(name, json.dumps({k: round(v, 4) if isinstance(v, float) else v for k, v in RESULTS[name].items()}))
    out = os.environ.get("HEAD_PARTS_HEATMAPS")
    if out:
        R.heatmap(p, ref, str(Path(out) / f"{name.split('.')[-1]}_deviation.png"), vmax=0.5, title=name)
    mean_bound, vol_tol = BOUNDS[name]
    assert abs(c["volume_ratio"] - 1) <= vol_tol, c
    assert c["bbox_mm"] <= 0.3, c
    assert c["mean_mm"] <= mean_bound, c
    assert not h["missing_in_ref"], f"model holes not on the reference: {h['missing_in_ref']}"
    assert not h["missing_in_model"], f"reference holes the model lacks: {h['missing_in_model']}"
    assert h["max_err_mm"] <= 0.2


@pytest.mark.parametrize("name", [m for m in MODULES if hasattr(_mod(m), "REFERENCE_CAD")])
def test_holes_against_brep(name, built):
    """Parts Hunter shipped as a STEP: every hole feature on one of the B-rep's own cylinders."""
    mod = _mod(name)
    path = R.REF_DIR / mod.REFERENCE_CAD
    if not path.exists():
        pytest.skip("reference not on this machine")
    from build123d import GeomType, import_step

    cyl = []
    for f in import_step(str(path)).faces():
        if f.geom_type == GeomType.CYLINDER:
            a = f.axis_of_rotation
            cyl.append((np.array(tuple(a.position)), np.array(tuple(a.direction)), f.radius))
    for k, f in built[name].features.items():
        if not k.startswith("hole_"):
            continue
        p, d = np.asarray(f["p"]), np.asarray(f["d"])
        best = min((np.linalg.norm((p - c) - a * ((p - c) @ a)) for c, a, r in cyl
                    if abs(abs(a @ d) - 1) < 1e-6 and abs(r - f["r"]) < 0.01), default=None)
        assert best is not None and best <= 0.01, f"{k}: nearest B-rep cylinder {best}"


def test_coupler_variants():
    from parts.head import neck_coupler

    a, b = neck_coupler.make(), neck_coupler.make(**neck_coupler.VARIANTS["r3x_26"])
    assert b.volume > a.volume                     # a thicker wall round the smaller tube
    assert b.features["bore"]["r"] == 13.0 and a.features["bore"]["r"] == 16.0
    assert b.features["hole_side_f"]["depth"] == 7.0
    assert neck_coupler.make({"bore_d": 26.0}).features["bore"]["r"] == 13.0   # the workbench's name for it
    with pytest.raises(ValueError):
        neck_coupler.make(tube_od=39.0)


def test_fit_opens_holes_and_pockets():
    from parts.head import base_plate

    a, b = base_plate.make(), base_plate.make(fit=0.2)
    assert math.isclose(b.features["hole_fl1"]["r"] - a.features["hole_fl1"]["r"], 0.1)
    assert math.isclose(b.features["pocket_l"]["size"][0] - a.features["pocket_l"]["size"][0], 0.4)
    assert b.volume < a.volume


def test_servo_pocket_follows_the_catalogue():
    from parts.head import base_plate
    from parts.head.servos import SERVOS

    p = base_plate.make(servo="mg996r", servo_pattern_w=None)
    s = SERVOS["mg996r"]
    assert p.features["pocket_l"]["size"] == [s["body_l"], s["body_w"]]
    xs = sorted({round(f["p"][0], 3) for k, f in p.features.items() if k.startswith("hole_sins")})
    assert math.isclose(xs[-1] - xs[-2], s["pattern_l"], abs_tol=1e-6)
    zs = sorted({round(f["p"][2], 3) for k, f in p.features.items() if k.startswith("hole_sins")})
    assert math.isclose(zs[-1] - zs[0], s["pattern_w"], abs_tol=1e-6)
    assert p.is_valid


def test_bolt_size_drives_holes():
    from parts.head import custom_joint_piece

    p = custom_joint_piece.make(bolt="M3", hole="heatset")
    assert p.features["hole_tilt_l"]["r"] == 2.0 and p.features["hole_tilt_l"]["kind"] == "heatset"
