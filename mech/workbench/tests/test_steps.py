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
