"""Mass properties: never from how a part is drawn.

The display GLBs are decimated and quantized (KHR_mesh_quantization: normalized int16 positions, the
node's scale + translation dequantize them). Read as raw integers they made every link weigh ~1e12 kg
(2026-10-02). These pin: the build's `mass_props` (source mesh), the dequantizing reader, and sane links
on the built droid.

Run: cd mech && .venv/bin/python -m pytest rigsync/tests -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest
import trimesh

MECH = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(MECH))

from rigsync.inertia import link_inertials, part_inertial, read_glb  # noqa: E402
from workbench.build import _glb, _mass_props  # noqa: E402

DROID = MECH / "out" / "r3x_droid" / "manifest.json"


def _box():
    m = trimesh.creation.box([40.0, 20.0, 37.2])  # a standard servo's case
    m.apply_translation([10.0, -22.9, 3.0])
    return m


def test_quantized_glb_reads_back_to_scale(tmp_path):
    m = _box()
    f = tmp_path / "box.glb"
    _glb(m, f)
    r = read_glb(str(f))
    assert np.allclose(r.bounds, m.bounds, atol=0.01)
    assert r.volume == pytest.approx(40 * 20 * 37.2, rel=1e-3)


def test_mass_props_from_the_source_mesh():
    m = _box()
    mp = _mass_props(m, centre=[10.0, -22.9, 3.0])
    assert mp["volume_mm3"] == pytest.approx(40 * 20 * 37.2, rel=1e-6)
    assert np.allclose(mp["centroid"], 0, atol=1e-6)
    v = 40 * 20 * 37.2
    Ixx = v / 12 * (20 ** 2 + 37.2 ** 2)  # per unit density, about the centroid
    assert np.reshape(mp["inertia_unit"], (3, 3))[0, 0] == pytest.approx(Ixx, rel=1e-6)


def test_part_inertial_catalogue_mass_and_box_inertia():
    """A 70 g servo case: 70 g, and the uniform box's inertia scaled to it."""
    m = _box()

    class P:
        raw = {"id": "s", "mass_g": 70.0, "material": "servo", "mass_props": _mass_props(m, [10.0, -22.9, 3.0])}
        T = np.eye(4)
        base = Path(".")

    r = part_inertial(P)
    assert r["kg"] == pytest.approx(0.070)
    assert r["I"][0, 0] == pytest.approx(0.070 / 12 * (20 ** 2 + 37.2 ** 2) * 1e-6, rel=1e-6)
    assert np.allclose(r["com"], 0, atol=1e-6)


@pytest.mark.skipif(not DROID.exists(), reason="no built droid (mech/out is local)")
def test_built_droid_links_are_sane():
    from rigsync.tree import load

    tree = load(DROID)
    links = link_inertials(tree)
    assert links
    for gid, li in links.items():
        assert 0 < li["kg"] < 30, (gid, li["kg"])
        assert np.all(np.abs(li["com"]) < 2500), (gid, li["com"])
        assert np.max(np.abs(li["I"])) < 5.0, (gid, li["I"])
    head = links.get("hunter_head/head")
    if head is not None:  # Hunter's head: plate, servos, shells and the kit face: ~1-2 kg
        assert 0.8 < head["kg"] < 3.0
    # a known part: the goBILDA 2000 gimbal servo, 70 g catalogue, its centre inside its own box
    found = False
    for p in tree.parts:
        if p.raw["id"] == "servo_l" and p.asm == "hunter_head":
            r = part_inertial(p)
            assert r["kg"] == pytest.approx(0.070)
            lo, hi = (np.asarray(x, float) for x in p.raw["bbox"])
            t = np.asarray((p.raw.get("transform") or {}).get("t", [0, 0, 0]), float)
            box_c = p.T[:3, :3] @ ((lo + hi) / 2 - t) + p.T[:3, 3]
            assert np.linalg.norm(r["com"] - box_c) < 0.5 * np.linalg.norm(hi - lo)
            assert r["how"].startswith("catalogue; shape source mesh")
            found = True
    assert found
