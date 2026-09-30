"""Each of Anderson's parts against the STEP its defaults reproduce.

    cd mech && .venv/bin/python -m pytest parts/anderson/tests -q            # -s for the numbers

His STEPs are exact B-reps, so the bars are tight: volume within 0.5 %, bbox within 0.05 mm, mean
surface deviation within 0.02 mm, Hausdorff within 0.1 mm, and every hole matched both ways against
the B-rep's own closed cylinders within 0.02 mm (a part may declare the ones it knowingly leaves
out: UNMODELLED_HOLES). The STEPs live in mech/vendor (gitignored): without them those tests skip.
"""

from __future__ import annotations

import importlib
import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest

MECH = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(MECH))

from parts.anderson import PARTS, REF_DIR, STL_DIR, STL_PARTS  # noqa: E402
from parts.head.tests import regress as R  # noqa: E402

MODULES = sorted(PARTS)


def _mod(name):
    return importlib.import_module(name)


@pytest.fixture(scope="module")
def built():
    return {name: _mod(name).make() for name in MODULES}


@pytest.mark.parametrize("name", MODULES)
def test_contract(name, built):
    p = built[name]
    mod = _mod(name)
    assert p.is_valid and p.volume > 0 and len(p.solids()) == 1
    assert p.reference == mod.REFERENCE
    assert set(p.printability) == {"orientation", "base", "supports", "note"}
    for k, f in p.features.items():
        v = f["d"] if f["type"] == "axis" else f["n"]
        assert math.isclose(np.linalg.norm(v), 1.0, abs_tol=1e-9), k
        if k.startswith("hole_"):
            assert f"face_{k[5:]}" in p.features
    assert math.isclose(mod.make({}).volume, p.volume, rel_tol=1e-9)
    with pytest.raises(TypeError):
        mod.make({"no_such_parameter": 1})


@pytest.mark.parametrize("name", MODULES)
def test_matches_step(name, built):
    mod = _mod(name)
    path = REF_DIR / mod.REFERENCE
    if not path.exists():
        pytest.skip("reference STEP not on this machine (mech/vendor is gitignored)")
    p = built[name]
    c = R.compare(p, R.load_ref(mod.REFERENCE, ref_dir=REF_DIR))
    h = R.match_brep_holes(p.features, R.brep_holes(path), tol=0.02)
    print(name, json.dumps({k: round(v, 4) if isinstance(v, float) else v for k, v in c.items()}),
          f"holes {len(h['matched'])}")
    assert abs(c["volume_ratio"] - 1) <= 0.005, c
    assert c["bbox_mm"] <= 0.05 and c["mean_mm"] <= 0.02 and c["hausdorff_mm"] <= 0.1, c
    assert not h["missing_in_ref"], h["missing_in_ref"]
    left = [m for m in h["missing_in_model"] if m not in getattr(mod, "UNMODELLED_HOLES", [])]
    assert not left, f"STEP holes the model lacks: {left}"


@pytest.mark.parametrize("name", [m for m in MODULES if hasattr(_mod(m), "ALSO")])
def test_same_solid_twice(name, built):
    """Where Anderson ships one part under two names, both files are this model."""
    from build123d import import_step

    for also in _mod(name).ALSO:
        path = REF_DIR / also
        if not path.exists():
            pytest.skip("reference STEP not on this machine")
        other = import_step(str(path))
        assert math.isclose(other.volume, built[name].volume, rel_tol=1e-4)


# ------------------------------------------------------------------ parts sourced from STLs
STL_CASES = [(m, v) for m, vs in sorted(STL_PARTS.items()) for v in vs]


def _stl_case(module, variant):
    mod = _mod(module)
    params = getattr(mod, variant) if variant else {}
    sub, ref = getattr(mod, variant + "_REFERENCE") if variant else (mod.REF_SUBDIR, mod.REFERENCE)
    return mod, params, STL_DIR / sub, ref


@pytest.mark.parametrize("module,variant", STL_CASES)
def test_stl_part_matches(module, variant):
    """No B-rep for these: held to the STL (its faceting ~0.01 mm): mean deviation <= 0.05 mm, volume
    within 1 %, bbox within 2 mm (the ring sectors' simplified end blends), holes found on the mesh
    matched both ways within 0.2 mm."""
    mod, params, ref_dir, ref = _stl_case(module, variant)
    p = mod.make(params)
    assert p.is_valid and len(p.solids()) == 1
    if not (ref_dir / ref).exists():
        pytest.skip("reference STL not on this machine")
    refm = R.load_ref(ref, ref_dir=ref_dir)
    c = R.compare(p, refm)
    h = R.match_holes(p.features, refm, rmin=0.5)
    print(module, variant or "default", json.dumps({k: round(v, 4) if isinstance(v, float) else v for k, v in c.items()}),
          f"holes {len(h['matched'])}")
    assert abs(c["volume_ratio"] - 1) <= 0.01 and c["mean_mm"] <= 0.05 and c["bbox_mm"] <= 2.0, c
    assert not h["missing_in_ref"] and not h["missing_in_model"], h


# ------------------------------------------------------------------ hole types and the inserts preset
ALL_MODULES = sorted(set(MODULES) | set(STL_PARTS))


@pytest.mark.parametrize("name", ALL_MODULES)
def test_inserts_preset(name):
    """`inserts=True` turns the part's screw-into-plastic groups into heat-set holes (datasheet
    diameter, depth >= insert length + 1) and leaves the rest as designed."""
    from parts.head._common import HOLE_TYPES, INSERTS

    mod = _mod(name)
    designed = getattr(mod, "DESIGNED", None)
    assert designed is not None, "every part exposes its hole groups (DESIGNED)"
    assert all(t in HOLE_TYPES for t in designed.values())
    p = mod.make({"inserts": True})
    assert p.is_valid and len(p.solids()) == 1
    for g in getattr(mod, "INSERT_CANDIDATES", ()):
        hs = [f for k, f in p.features.items() if k.startswith("hole_") and f.get("kind") == "heat_set"
              and f.get("insert")]
        assert hs, f"{g}: no heat-set holes after the preset"
        for f in hs:
            ins = next((v for v in INSERTS.values() if v["source"] == f["insert"]), None)
            if ins:
                assert math.isclose(2 * f["r"], ins["d"], abs_tol=1e-6) and f["depth"] >= ins["length"] + 1 - 1e-6
    with pytest.raises(ValueError):
        mod.make({f"{next(iter(designed))}_hole": "rivet"})


def test_visor_drive_interface():
    """The feature names and spacings the workbench's visor drive mates by (its interface)."""
    horn = _mod("parts.anderson.visor_horn").make()
    rod = _mod("parts.anderson.visor_push_rod").make()
    tab = _mod("parts.anderson.visor_rod_tab").make()
    f = horn.features
    assert f["spline"]["teeth"] == 25 and f["spline"]["d"] == [0.0, 0.0, 1.0]
    assert f["servo_face"]["p"][2] == 0.0 and f["servo_face"]["n"] == [0.0, 0.0, -1.0]
    assert math.isclose(math.dist(f["spline"]["p"], f["tip"]["p"]), 25.0, abs_tol=1e-9)
    assert math.isclose(math.dist(rod.features["pin_a"]["p"], rod.features["pin_b"]["p"]), 58.0, abs_tol=1e-9)
    assert math.isclose(2 * rod.features["pin_a"]["r"], 3.5) and rod.features["pin_a"]["depth"] == 5.0
    t = tab.features
    assert t["socket"]["p"] == [0.0, 0.0, -1.5] and math.isclose(2 * t["socket"]["r"], 8.5)
    assert math.isclose(math.dist(t["socket"]["p"], t["tip"]["p"]), 19.5, abs_tol=1e-9)
    assert math.isclose(t["tip"]["p"][2], 18.0) and math.isclose(2 * t["tip"]["r"], 3.5)
    assert t["cross"]["d"] == [1.0, 0.0, 0.0]
    # nut traps / inserts are real alternatives for every group
    for mod, g in (("visor_push_rod", "pin"), ("visor_rod_tab", "cross"), ("visor_rod_tab", "tip")):
        p = _mod(f"parts.anderson.{mod}").make({f"{g}_hole": "nut_trap"})
        assert p.is_valid and len(p.solids()) == 1


def test_visor_pivots_take_a_lock_nut():
    """The pivot rule: the horn tip is 3.0 tapped as drawn and a 3.4 bolt + nylock under the preset."""
    horn = _mod("parts.anderson.visor_horn")
    f = horn.make().features["hole_tip"]
    assert f["kind"] == "tapped" and math.isclose(2 * f["r"], 3.0) and "lock_nut" not in f
    p = horn.make({"inserts": True})
    f = p.features["hole_tip"]
    assert f["kind"] == "clearance" and math.isclose(2 * f["r"], 3.4) and "nylock" in f["lock_nut"]
    assert p.features["nut_tip"]["n"] == [0.0, 0.0, -1.0]
    assert "nylock" in _mod("parts.anderson.visor_rod_tab").make().features["hole_tip"]["lock_nut"]
