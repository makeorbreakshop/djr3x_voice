"""Build steps (workbench/steps.py): every part and fastener in exactly one step, grouped like a builder.

Run: cd mech && .venv/bin/python -m pytest workbench/tests/test_steps.py -q
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pytest
import trimesh

MECH = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(MECH))

from workbench.geom import align  # noqa: E402
from workbench.model import Assembly, Fastener, Gear, Joint, Link, Part, Step  # noqa: E402
from workbench.steps import plan, stem  # noqa: E402


def box(ext, at):
    m = trimesh.creation.box(ext)
    m.apply_translation(at)
    return m


def part(pid, name, link, ext, at, cls="mech"):
    return Part(pid, name, cls, link, box(ext, at), {"kind": "generated"})


def tiny() -> Assembly:
    """A frame plate, two posts (a pair), a servo in its bracket with its pinion, a slide with a cap riding it."""
    a = Assembly("tiny", "Tiny")
    a.links = [Link("frame", "Frame"), Link("slide", "Slide", "lift")]
    a.joints = [Joint("lift", "Lift", "prismatic", "frame", "slide", (0, 0, 0), (0, 1, 0), (0, 50), "mm")]
    a.parts = [
        part("plate", "Base plate", "frame", [200, 10, 200], [0, -5, 0]),
        part("post_l", "Post (left)", "frame", [10, 150, 10], [-60, 75, 0]),
        part("post_r", "Post (right)", "frame", [10, 150, 10], [60, 75, 0]),
        part("bracket", "Servo bracket", "frame", [40, 4, 30], [0, 2, 60]),
        part("servo", "Servo", "frame", [20, 30, 20], [0, 19, 60], cls="servo"),
        part("pinion", "Pinion", "frame", [12, 4, 12], [0, 36, 60]),
        part("slide", "Slide plate", "slide", [130, 8, 60], [0, 120, 0]),
        part("slide_cap", "Slide cap", "slide", [20, 4, 20], [0, 126, 0]),
        part("old", "Replaced", "frame", [5, 5, 5], [90, 2, 90]),
    ]
    a.parts[-1].replaced_by = "x/y"
    a.gears = [Gear("g", "rack_pinion", "lift", "frame", (0, 36, 60), (0, 1, 0), 1.0, parts=["pinion"], servo="servo")]
    a.fasteners = [Fastener(f"f{i}", {"type": "shcs", "thread": "M3", "length_mm": 8}, "shcs-M3x8", ["bracket", "plate"], "frame",
                            "", align([0, -1, 0], [x, 4, 60])) for i, x in enumerate((-15, 15))]
    a.fasteners.append(Fastener("fs", {"type": "shcs", "thread": "M3", "length_mm": 6}, "shcs-M3x6", ["slide_cap", "slide"], "slide",
                                "", align([0, -1, 0], [0, 128, 0])))
    # an authored step that names the plate, and one that names it again (works on it): the plate is placed once
    a.steps = [Step("s1", "Base plate", ["plate"]), Step("s2", "Step 2", ["plate"], unplaced=[{"key": "insert", "count": 4}])]
    return a


def test_every_part_and_fastener_once():
    a = tiny()
    steps = plan(a)
    placed = Counter(p for s in steps for p in s.parts)
    fast = Counter(f for s in steps for f in s.fasteners)
    live = {p.id for p in a.parts if not p.replaced_by}
    assert set(placed) == live and all(v == 1 for v in placed.values())
    assert set(fast) == {f.id for f in a.fasteners} and all(v == 1 for v in fast.values())
    # the authored step that only works on the plate keeps it as context, titled by what it adds
    s2 = next(s for s in steps if s.id == "s2")
    assert s2.parts == [] and "plate" in s2.context and s2.title == "Base plate + 4 inserts"
    assert [s.id for s in steps if not s.derived] == ["s1", "s2"] and steps[0].id == "s1"
    # derived groups go in after the step that placed what they touch: the posts stand on the plate
    assert [s.id for s in steps].index("s2") > 1


def test_groups_like_a_builder():
    steps = plan(tiny())
    by = {p: s for s in steps for p in s.parts}
    assert by["post_l"] is by["post_r"]  # a pair together
    assert by["servo"] is by["pinion"] is by["bracket"]  # a servo with its gear and its mount
    assert by["slide_cap"] is by["slide"]  # a small part rides what it sits on
    order = [s for s in steps]
    assert order.index(by["slide"]) > order.index(by["servo"])  # the moving link after the frame
    assert by["bracket"].fasteners == ["f0", "f1"]  # fasteners with the part they hold
    assert "fs" in by["slide"].fasteners
    assert stem("morton_post_low_3") == "morton_post_low" and stem("visor_bearing_l") == "visor_bearing"


def _manifests():
    out = MECH / "out"
    for name in ("r3x_droid", "column_internals", "hunter_head"):
        f = out / name / "manifest.json"
        if f.exists():
            yield f


def _walk(a: dict, base: Path):
    if "ref" in a:
        f = (base / a["ref"]).resolve()
        a = json.loads(f.read_text())["root"]
        base = f.parent
    yield a
    for c in a.get("children", []):
        yield from _walk(c, base)


@pytest.mark.parametrize("manifest", list(_manifests()) or [pytest.param(None, marks=pytest.mark.skip("no mech/out manifests"))])
def test_built_manifests_cover_everything_once(manifest):
    root = json.loads(manifest.read_text())["root"]
    for a in _walk(root, manifest.parent):
        if not a.get("parts") and not a.get("fasteners"):
            continue
        placed = Counter(p for s in a.get("steps", []) for p in s.get("parts", []))
        fast = Counter(f for s in a.get("steps", []) for f in s.get("fasteners", []))
        live = {p["id"] for p in a["parts"] if not p.get("replaced_by")}
        assert live <= set(placed), (a["id"], sorted(live - set(placed)))
        assert all(v == 1 for v in placed.values()), (a["id"], [k for k, v in placed.items() if v > 1])
        assert set(fast) == {f["id"] for f in a.get("fasteners", [])}, a["id"]
        assert all(v == 1 for v in fast.values()), a["id"]


def test_a_big_group_splits_into_kinds_and_rows():
    from workbench.steps import MAX_PARTS
    a = Assembly("panels", "Panels")
    a.links = [Link("ring", "Ring")]
    a.parts = [part("ring", "Ring", "ring", [300, 40, 300], [0, 0, 0])]
    for row, x in (("l", -60), ("m", 0), ("r", 60)):
        a.parts += [part(f"lpi_{row}_{k}", f"LPI_{row.upper()}_{k}", "ring", [8, 8, 2], [x, k * 10 - 10, 151]) for k in (1, 2, 3)]
    a.parts += [part("led", "LED_B", "ring", [30, 6, 2], [0, 15, 151])]
    steps = plan(a)
    assert all(len(s.parts) <= MAX_PARTS for s in steps)
    rows = [set(s.parts) for s in steps if any(p.startswith("lpi_") for p in s.parts)]
    assert {"lpi_l_1", "lpi_l_2", "lpi_l_3"} in rows and {"lpi_r_1", "lpi_r_2", "lpi_r_3"} in rows


def test_kit_hardware_goes_in_the_holes_the_guide_counts():
    import manifold3d as mf
    from workbench.kitgeom import holes, parse, place_hardware

    assert parse("10-32 x 1/2in 316 SS Phillips flat head screw") == {"type": "fhcs", "thread": "10-32", "d_mm": 4.83, "length_mm": 12.7}
    assert parse("M4 tapered heat-set insert, 4.7mm")["type"] == "insert"
    assert parse("neodymium magnet 1/4in OD x 1/8in")["d_mm"] == 6.35
    assert parse("aluminium round standoff 1/4in OD x 7/16in, 6-32 female") is None
    plate = mf.Manifold.cube((80, 6, 80), True)
    for x, z in ((-30, -30), (30, -30), (-30, 30), (30, 30)):
        plate = plate - mf.Manifold.cylinder(10, 2.6, 2.6, 48).rotate((90, 0, 0)).translate((x, 5, z))
    m = plate.to_mesh()
    mesh = trimesh.Trimesh(np.asarray(m.vert_properties)[:, :3], np.asarray(m.tri_verts))
    found = holes(mesh)
    assert len(found) == 8 and all(abs(r - 2.6) < 0.1 for _, _, r in found)  # each through hole opens on both faces
    a = Assembly("kitlike", "Kit-like")
    a.links = [Link("l", "L")]
    a.parts = [Part("plate", "TR_RR_Full", "shell", "l", mesh, {"kind": "stl"})]
    st = Step("s1", "Step 1", ["plate"], unplaced=[{"key": "mcmaster-93365A240", "count": 4, "note": "into TR_RR_Full",
                                                   "spec": {"mcmaster": "93365A240", "desc": "10-32 tapered heat-set insert, 0.15in"}}])
    fs = place_hardware(a, [st])
    assert len(fs) == 4 and st.unplaced == [] and st.fasteners == [f.id for f in fs]
    for f in fs:  # each on its hole's axis, going into the plate
        z = f.matrix[:3, 2]
        assert abs(abs(z[1]) - 1) < 1e-6 and f.inferred


def test_paths_go_round_what_is_in_the_way():
    """A peg into a socket under a bridge: straight down its axis is blocked, so it comes in level and then down."""
    from workbench.mates import Mate, axis
    from workbench.paths import plan as plan_paths

    a = Assembly("bridge", "Bridge")
    a.links = [Link("l", "L")]
    base = part("base", "Base", "l", [120, 10, 60], [0, -5, 0])
    bridge = part("bridge", "Bridge", "l", [40, 6, 60], [0, 40, 0])
    peg = part("peg", "Peg", "l", [8, 20, 8], [0, 10, 0])
    peg.features = {"axis": axis([0, 0, 0], [0, 1, 0], 4)}
    base.features = {"socket": axis([0, 0, 0], [0, 1, 0], 4)}
    a.parts = [base, bridge, peg]
    a.mates = [Mate("m1", "concentric", ("peg", "axis"), ("base", "socket"))]
    steps = [Step("s1", "Base and bridge", ["base", "bridge"]), Step("s2", "Peg", ["peg"])]
    assert plan_paths(a, steps, []) == []
    path = steps[1].sequence[0]["paths"]["peg"]
    # not straight down from above through the bridge, nor up through the base
    straight_up = len(path) == 1 and abs(path[0][0]) < 1e-6 and abs(path[0][2]) < 1e-6
    assert not straight_up and steps[1].sequence[0]["clean"]


# Items of Hunter's head no approach path clears, and why (the build's order, not a path, is the issue).
HUNTER_KNOWN = {
    "nut_tilt_l": "the left tilt nut's line runs through the cross and the other bearing; it goes on before the cross",
    "h_v_3": "the kit visor's right arm is glued into the brow, which overlaps it in the print",
    "pin_visor_tab": "the tab pin goes through before the axle slides into its bracket; the step places the axle first",
    "side_left": "the sides go on over the visor axle ends glued on in the step before (they flex round them)",
    "side_right": "as side_left",
    "head_top": "the top goes on with the visor flipped up; the path is planned at visor 0",
    "h_fp": "the face plate goes in with the visor flipped up; the path is planned at visor 0",
}


def test_hunter_head_paths_cross_nothing():
    f = MECH / "out" / "hunter_head" / "manifest.json"
    if not f.exists():
        pytest.skip("no mech/out/hunter_head manifest")
    root = json.loads(f.read_text())["root"]
    bad = set()
    for s in root["steps"]:
        seq = s.get("sequence") or []
        named = {i for it in seq for i in it["ids"]}
        assert set(s.get("parts", [])) <= named and set(s.get("fasteners", [])) <= named, s["id"]
        for it in seq:
            assert set(it["ids"]) <= set(it["paths"]), (s["id"], it["ids"])
            if not it.get("clean", True):
                bad |= set(it["ids"])
    assert bad <= set(HUNTER_KNOWN), sorted(bad - set(HUNTER_KNOWN))
