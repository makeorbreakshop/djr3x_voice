"""Manifest/export tests on a tiny synthetic assembly (no vendored files).

Run: cd mech && .venv/bin/python -m pytest workbench/tests -q
"""

from __future__ import annotations

import json
import math
import sys
import textwrap
from pathlib import Path

import numpy as np
import pytest
import trimesh

MECH = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(MECH))

from workbench.build import build  # noqa: E402
from workbench.kinematics import link_matrices, solve_linkages  # noqa: E402

TINY = textwrap.dedent('''
    import numpy as np, trimesh
    from workbench.geom import align
    from workbench.model import *

    def box(ext, at):
        m = trimesh.creation.box(ext)
        m.apply_translation(at)
        return m

    def build():
        a = Assembly("tiny", "Tiny arm", frame_note="mm, +Y up")
        a.links = [Link("base", "Base"), Link("arm", "Arm", "hinge")]
        a.parts = [
            Part("base_block", "Base block", "mech", "base", box([40, 10, 40], [0, -5, 0]),
                 {"kind": "generated"}, "PLA", True, (0, -1, 0), 20, 10.0),
            # a bar hinged at the origin about +X, lying along +Z just above the block
            Part("arm_bar", "Arm bar", "mech", "arm", box([10, 4, 60], [0, 4, 32]),
                 {"kind": "generated"}, "PLA", True, (0, 1, 0), 20, 5.0),
            Part("servo", "Servo", "servo", "base", box([10, 10, 10], [0, 5, -30]),
                 {"kind": "generated"}, "servo", False, (0, 1, 0), 20, 40.0),
        ]
        a.joints = [Joint("hinge", "Hinge", "revolute", "base", "arm", (0, 0, 0), (1, 0, 0), (-30, 30),
                          "deg", "head_tilt", (-20, 25),
                          {"kind": "push_rod", "servos": ["servo"], "linkages": ["rod"]})]
        a.linkages = [Linkage("rod", "servo", "base", (0, 10, -30), (0, 1, 0), 10, (1, 0, 0), 2,
                              "arm", (0, 12, 20), 2600 ** 0.5)]
        a.fasteners = [Fastener("f1", {"type": "shcs", "thread": "M4", "length_mm": 10}, "shcs-M4x10",
                                ["base_block", "servo"], "base", "s1", align([0, -1, 0], [5, 0, 5])),
                       Fastener("f2", {"type": "shcs", "thread": "M4", "length_mm": 10}, "shcs-M4x10",
                                ["base_block"], "base", "s1", None)]
        a.steps = [Step("s1", "Bolt it", ["base_block", "servo"], ["f1", "f2"],
                        unplaced=[{"key": "nut-M4", "spec": {"type": "nut", "thread": "M4"}, "count": 2, "note": "n"}],
                        guide_page=3)]
        a.bom = [BomLine("shcs-M4x10", "M4 x 10 SHCS", 2, "fastener")]
        child = Assembly("sub", "Sub", links=[Link("s", "S")],
                         parts=[Part("sub_box", "Sub box", "hardware", "s", box([5, 5, 5], [0, 0, 0]),
                                     {"kind": "generated"})],
                         bom=[BomLine("shcs-M4x10", "M4 x 10 SHCS", 3, "fastener")])
        a.children = [child, {"ref": "../other/manifest.json", "id": "other", "name": "Other",
                              "mount": {"parent_link": "arm", "transform": {"t": [0, 0, 0]}}}]
        return a
''')


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    root = tmp_path_factory.mktemp("mech")
    mod_dir = root / "tiny"
    mod_dir.mkdir()
    (mod_dir / "assembly.py").write_text(TINY)
    out = root / "out"
    path = build(str(mod_dir / "assembly.py"), out)
    return path, json.loads(path.read_text())


def test_schema_header(built):
    _, m = built
    assert m["schema"] == "r3x.mech.manifest" and m["version"] == 1
    r = m["root"]
    for k in ("id", "name", "links", "parts", "joints", "linkages", "fasteners", "steps", "bom", "bom_rollup",
              "checks", "children"):
        assert k in r, k


def test_files_exist_and_load(built):
    path, m = built
    base = path.parent
    for p in m["root"]["parts"]:
        mesh = trimesh.load(base / p["mesh"], force="mesh")
        assert len(mesh.faces) > 0
        assert (base / p["export"]["stl"]).exists()
        if p.get("printed"):
            assert (base / p["export"]["3mf"]).exists()
        # display mesh is in the part frame: centred, transform restores it
        lo, hi = np.array(p["bbox"][0]), np.array(p["bbox"][1])
        assert np.allclose(p["transform"]["t"], (lo + hi) / 2, atol=1e-3)
    placed = [f for f in m["root"]["fasteners"] if f["placed"]]
    assert placed and all((base / f["mesh"]).exists() for f in placed)
    unplaced = [f for f in m["root"]["fasteners"] if not f["placed"]]
    assert unplaced and "transform" not in unplaced[0]


def test_joint_and_profile_mapping(built):
    _, m = built
    j = m["root"]["joints"][0]
    assert j["profile_joint"] == "head_tilt"
    assert j["limits"] == {"min": -30, "max": 30} and j["profile_limits"] == {"min": -20, "max": 25}
    assert m["root"]["links"][1]["joint"] == "hinge"


def test_steps_and_rollup(built):
    _, m = built
    s = m["root"]["steps"][0]
    assert s["n"] == 1 and s["guide_page"] == 3 and s["unplaced"][0]["count"] == 2
    roll = {b["key"]: b["qty"] for b in m["root"]["bom_rollup"]}
    assert roll["shcs-M4x10"] == 5  # own 2 + child 3
    kids = m["root"]["children"]
    assert kids[0]["id"] == "sub" and kids[0]["parts"][0]["mesh"].startswith("sub/parts/")
    assert kids[1]["ref"] == "../other/manifest.json"


def test_checks_present(built):
    _, m = built
    kinds = {c["kind"] for c in m["root"]["checks"]}
    assert {"reach", "interference"} <= kinds
    interf = next(c for c in m["root"]["checks"] if c["id"] == "interference_hinge")
    # the bar lies 2 mm over the block: tipping it down (-X rotation = +Z end down is +deg about +X)
    # must hit the block well inside 30 deg
    assert interf["status"] == "fail" and interf["pose"]["hinge"] > 0


def test_closed_form_rod_keeps_length(tmp_path):
    sys.path.insert(0, str(tmp_path))
    (tmp_path / "tiny_mod.py").write_text(TINY)
    import importlib

    mod = importlib.import_module("tiny_mod")
    asm = mod.build()
    lk = asm.linkages[0]
    assert solve_linkages(asm, {"hinge": -90})["rod"] is None  # out of reach: reported, never clamped
    for v in (-20, -5, 0, 5, 15):
        r = solve_linkages(asm, {"hinge": v})["rod"]
        assert r is not None
        ang, a, b = r
        assert math.isclose(np.linalg.norm(a - b), lk.rod_length, abs_tol=1e-6)
    assert solve_linkages(asm, {"hinge": 0})["rod"][0] == pytest.approx(0.0, abs=1e-9)  # zero pose = centre pulse
    ms = link_matrices(asm, {"hinge": 90})
    assert np.allclose(ms["arm"][:3, :3] @ [0, 0, 1], [0, -1, 0], atol=1e-9)  # +deg about +X tips +Z down
