"""The assembly suite in pytest/CI (TESTS.md). Slow (minutes) and needs the vendored sources:
runs only with R3X_MECH_SUITE=1, e.g.

    cd mech && R3X_MECH_SUITE=1 .venv/bin/python -m pytest workbench/tests/test_assemblies.py -q
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

MECH = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(MECH))

ASSEMBLIES = ["hunter_head"]


@pytest.mark.skipif(os.environ.get("R3X_MECH_SUITE") != "1", reason="slow: set R3X_MECH_SUITE=1")
@pytest.mark.parametrize("name", ASSEMBLIES)
def test_suite_green_or_explained(name):
    if not (MECH / "vendor" / name).exists():
        pytest.skip("vendored sources not on this machine")
    from workbench.build import run_suite

    report = run_suite(name)
    failed = [t for t in report["tests"] if t["status"] == "fail"]
    assert not failed, "\n".join(f"{t['title']}: {t['summary']}" for t in failed)
