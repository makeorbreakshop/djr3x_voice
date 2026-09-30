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

from parts.anderson import PARTS, REF_DIR  # noqa: E402
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
