"""rigsync on a tiny synthetic droid (no meshes, no vendored files).

Run: cd mech && .venv/bin/python -m pytest workbench/tests rigsync/tests -q
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

from rigsync.generate import clip_hits, generate, solve_rod  # noqa: E402
from rigsync.tree import load  # noqa: E402


def _profile():
    def j(name, parent, kind="revolute", unit="deg", lim=40):
        return {"name": name, "kind": kind, "unit": unit, "parent": parent, "hard": {"min": -lim, "max": lim},
                "soft": {"min": -lim + 4, "max": lim - 4}, "animation": {"min": -lim + 4, "max": lim - 4},
                "v_max": 100, "a_max": 400, "j_max": 3000}

    cal = {"center_us": 1500, "gear": 1.0, "pulse_min_us": 500, "pulse_max_us": 2500, "range_deg": 270}
    return {"name": "t", "servo_controller": {"channels": 8, "frame_hz": 50, "resolution_us": 1, "supply_volts": 6.0},
            "joints": [j("torso_top", None), j("head_lift", "torso_top", "prismatic", "mm", 20), j("head_pan", "head_lift", lim=70),
                       j("visor", "head_pan", lim=30)],
            "actuators": [{"name": "neck", "joints": {"head_pan": 1}, "driver": "r3x_servo", "channel": 0, "servo": "SERVO_35KG_270",
                           "calibration": dict(cal, gear=1.5)},
                          {"name": "lift", "joints": {"head_lift": 1}, "driver": "r3x_servo", "channel": 1, "servo": "SERVO_60KG_270",
                           "calibration": dict(cal, mm_per_deg=0.21)}]}


def _write(tmp: Path):
    head = {"schema": "r3x.mech.manifest", "version": 1, "root": {
        "id": "head", "links": [{"id": "neck", "joint": None}, {"id": "shell", "joint": None}, {"id": "visor", "joint": "visor"}],
        "joints": [{"id": "visor", "type": "revolute", "parent_link": "shell", "child_link": "visor", "pivot": [0, 30, 0],
                    "axis": [1, 0, 0], "limits": {"min": -15, "max": 30}, "profile_joint": "visor",
                    "drive": {"kind": "push_rod", "servos": ["vs"], "linkages": ["rod"]}}],
        "linkages": [{"id": "rod", "kind": "push_rod", "servo": "vs",
                      "horn": {"link": "shell", "centre": [0, 0, 20], "axis": [1, 0, 0], "radius": 20, "zero_dir": [0, 1, 0], "ball_offset": 0},
                      "ground": {"link": "visor", "point": [0, 50, 0]}, "rod_length": 36.05551275, "parts": []}],
        "parts": [{"id": "shell_p", "link": "shell", "mesh": "missing.glb", "material": "PLA", "bbox": [[-50, 0, -50], [50, 60, 50]]},
                  {"id": "vs", "name": "Visor servo 35 kg", "class": "servo", "link": "shell", "mesh": "missing.glb", "material": "servo",
                   "mass_g": 60, "bbox": [[-10, 0, 0], [10, 20, 40]]}],
        "checks": [{"id": "interference_visor", "kind": "interference", "joint": "visor", "status": "pass",
                    "summary": "range -15..+30 deg; first contact +26 a x b (-4 past); -40 c x d"}]}}
    # the shell rides the neck: mount the shell link on the neck (same frame)
    head["root"]["links"][1] = {"id": "shell", "joint": None}
    droid = {"schema": "r3x.mech.manifest", "version": 1, "root": {
        "id": "droid", "links": [{"id": "ground", "joint": None}],
        "children": [
            {"id": "neck_drive", "mount": {"parent_link": "ground"},
             "links": [{"id": "stage", "joint": None}, {"id": "turn", "joint": "head_pan"}, {"id": "slide", "joint": "head_lift"}],
             "joints": [{"id": "head_pan", "type": "revolute", "parent_link": "stage", "child_link": "turn", "pivot": [0, 0, 0],
                         "axis": [0, 1, 0], "limits": {"min": -33.8, "max": 33.8}, "profile_joint": "head_pan",
                         "drive": {"kind": "gear", "servos": ["pan_s"], "servo_deg_per_joint_deg": 4.0}},
                        {"id": "head_lift", "type": "prismatic", "parent_link": "turn", "child_link": "slide", "pivot": [0, 0, 0],
                         "axis": [0, 1, 0], "unit": "mm", "limits": {"min": -37, "max": 37}, "profile_joint": "head_lift",
                         "drive": {"kind": "gear", "servos": ["lift_s"], "mm_per_servo_deg": 0.475}}],
             "parts": [{"id": "pan_s", "link": "turn", "material": "servo:SERVO_35KG_270", "mass_g": 60, "mesh": "x.glb", "bbox": [[0, 0, 0], [1, 1, 1]]}],
             "children": [{"ref": "../head/manifest.json", "id": "head", "mount": {"parent_link": "slide", "transform": {"t": [0, 700, 0]}}},
                          {"id": "alt_head", "mount": {"parent_link": "slide", "variant": {"group": "h", "id": "alt", "default": False}},
                           "links": [], "parts": []}]},
            {"id": "ring", "mount": {"parent_link": "ground"}, "links": [{"id": "ring_mount", "joint": None}, {"id": "ring", "joint": "torso_top"}],
             "joints": [{"id": "torso_top", "type": "revolute", "parent_link": "ring_mount", "child_link": "ring", "pivot": [0, 0, 0],
                         "axis": [0, 1, 0], "limits": {"min": -25.5, "max": 25.5}, "profile_joint": "torso_top",
                         "drive": {"kind": "gear", "servos": ["top_s"], "gear_ratio": 1 / 4.25}}]}]}}
    (tmp / "droid").mkdir()
    (tmp / "head").mkdir()
    (tmp / "droid/manifest.json").write_text(json.dumps(droid))
    (tmp / "head/manifest.json").write_text(json.dumps(head))
    prof = tmp / "robot.json"
    prof.write_text(json.dumps(_profile()))
    return tmp / "droid/manifest.json", prof


@pytest.fixture()
def built(tmp_path):
    # generate() reports paths relative to manifest.parents[2]
    root = tmp_path / "repo" / "mech" / "out"
    root.mkdir(parents=True)
    return _write(root)


def test_tree_mounts_childref_and_drops_variants(built):
    t = load(built[0])
    assert [a.id for a in t.asms] == ["droid", "neck_drive", "head", "ring"]
    assert t.skipped == ["alt_head"]
    v = t.joints["head/visor"]
    assert np.allclose(v.pivot, [0, 730, 0])          # head frame at y 700 on the slide
    assert t.moving_ancestor(v.parent_link).profile_joint == "head_lift"
    assert t.moving_ancestor(t.joints["neck_drive/head_pan"].parent_link) is None


def test_link_matrices_follow_the_chain(built):
    t = load(built[0])
    ms = t.link_matrices({"head_pan": 90, "head_lift": 10})
    p = ms["head/visor"][:3, :3] @ np.array([0, 0, 1.0])
    assert np.allclose(p, [1, 0, 0], atol=1e-9)        # +90 about +Y turns +Z to +X
    assert math.isclose(ms["head/visor"][1, 3], 10)     # lifted 10 mm


def test_rod_solve_and_ratio(built):
    t = load(built[0])
    lk = t.linkages["head/rod"].body()
    ms = t.link_matrices({})
    assert abs(solve_rod(lk, ms[lk["horn"]["link"]], ms[lk["ground"]["link"]])) < 1e-6  # built at rest
    res = generate(built[0], built[1])
    d = res["profile"]["mech"]["joints"]["visor"]["drive"]
    # horn radius 20 = lever radius (ball 20 mm from the visor pivot): a parallelogram, 1:1
    assert d["servo_deg_per_unit"] == pytest.approx(1.0, abs=0.02)
    assert d["servo_model"] == "SERVO_35KG_270"          # from the part's name


def test_generated_profile(built, tmp_path):
    res = generate(built[0], built[1])
    g = {j["name"]: j for j in res["profile"]["joints"]}
    assert g["head_pan"]["parent"] is None and g["head_lift"]["parent"] == "head_pan"
    assert g["head_pan"]["hard"] == {"min": -33.8, "max": 33.8}
    m = max(1.5, 0.04 * 67.6)
    assert g["head_pan"]["soft"]["max"] == pytest.approx(33.8 - m, abs=0.01)
    # 70 % of the 35 kg servo's no-load speed at 6 V, through 4:1
    assert g["head_pan"]["v_max"] == pytest.approx(0.7 * 60 / (0.16 + (0.11 - 0.16) * (1 / 2.4)) / 4, abs=0.1)
    # first contact +26 pulls the visor's animation max to 26 - margin
    assert g["visor"]["animation"]["max"] == pytest.approx(26 - max(1.5, 0.04 * 45), abs=0.01)
    a = {x["name"]: x for x in res["profile"]["actuators"]}
    assert a["neck"]["calibration"]["gear"] == 4.0
    assert a["lift"]["calibration"]["mm_per_deg"] == 0.475
    beh = {(c["joint"], c["field"]) for c in res["changes"] if c["behaviour"]}
    assert ("head_pan", "animation") in beh and ("head_pan", "parent") in beh
    # clip hits: a pan key at 40 sits inside the old +-66 and outside the new range
    show = tmp_path / "show"
    (show / "clips").mkdir(parents=True)
    (show / "clips/look.json").write_text(json.dumps({"id": "look", "tracks": {"head_pan": {"mode": "absolute", "keys": [[0, 0], [1, 34]]}}}))
    old = json.loads(built[1].read_text())
    hits = clip_hits(show, res["profile"], old)
    assert [(h["clip"], h["joint"]) for h in hits] == [("look", "head_pan")]
    # links and drives are present, bbox fallbacks give finite masses
    mech = res["profile"]["mech"]
    assert {d["servo"] for d in mech["drives"]} >= {"pan_s", "vs"}
    assert all(math.isfinite(l["kg"]) for l in mech["links"])
