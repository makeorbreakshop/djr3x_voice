"""Part finishes (SCHEMA.md "Finish"): the check, the kit's paint table, and the Original rig agreeing
with the manifest (sim/model/build_r3x.py bakes the same paint).

Run: cd mech && .venv/bin/python -m pytest workbench/tests/test_finish.py -q
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

MECH = Path(__file__).resolve().parents[2]
REPO = MECH.parent
sys.path.insert(0, str(MECH))

from workbench.finish import BARE, PETG_GREY, check, finish, palette_classes, problems  # noqa: E402

BUILD_R3X = REPO / "sim/model/build_r3x.py"
KIT_FINISH = MECH / "assemblies/kit/finish.json"


def part(pid, cls="mech", printed=False, exposed=None, fin=None, replaced_by=None):
    return SimpleNamespace(id=pid, cls=cls, printed=printed, exposed=exposed, finish=fin, replaced_by=replaced_by)


def test_check_flags_a_seen_part_without_paint_and_a_print_without_colour():
    ok = [part("shell", "shell", True, fin=finish(paint="paint_orange", print=PETG_GREY)),
          part("inside", "shell", True, exposed=False, fin=finish(print=PETG_GREY)),      # not seen: no paint needed
          part("tube", "hardware", exposed=True),                                          # bare metal may show
          part("bare", printed=True, exposed=True, fin=finish(paint=BARE, print=PETG_GREY)),
          part("old", "shell", True, fin=finish(print=PETG_GREY), replaced_by="top/new")]  # not in the build
    assert check(ok).status == "pass"
    bad = [part("cap", "shell", True, fin=finish(print=PETG_GREY)),
           part("arm", printed=True, exposed=True, fin=finish(paint="paint_pink", print=PETG_GREY)),
           part("bracket", printed=True)]
    c = check(bad)
    assert c.status == "fail" and c.parts == ["arm", "bracket", "cap"]
    assert problems(bad[0]) == ["seen from outside but has no paint"]
    assert "not a palette class" in problems(bad[1])[0]
    assert problems(bad[2]) == ["printed but has no print filament / colour"]


def test_kit_table_uses_palette_classes():
    d = json.loads(KIT_FINISH.read_text())
    classes = palette_classes()
    assert classes, "sim/web/src/palette.json not found"
    assert {v for v in d["paint"].values() if v} <= classes
    for code in d["not_printed"]:
        assert code in d["paint"]
    for pr in [d["print"], *d["print_overrides"].values()]:
        assert pr["filament"] and re.fullmatch(r"#[0-9a-f]{6}", pr["color"]) and pr["color_name"]


def test_every_kit_file_has_a_paint_entry():
    from assemblies.kit.assembly import KIT, kit_files, pid

    if not KIT.exists():
        pytest.skip("kit not vendored on this machine")
    keys = {pid(k) for k in json.loads(KIT_FINISH.read_text())["paint"]}
    clean = lambda s: s.replace(" (New)", "").replace(" (DNP)", "_DNP").replace(" - x4", "")  # noqa: E731
    missing = sorted(clean(f.stem) for f in kit_files() if pid(clean(f.stem)) not in keys)
    assert not missing, f"kit files with no entry in finish.json: {missing}"


def _build_r3x():
    return ast.parse(BUILD_R3X.read_text())


def _literal(tree, name):
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise KeyError(name)


@pytest.mark.skipif(not BUILD_R3X.exists(), reason="no sim/ checkout")
def test_original_rig_reads_the_kit_table():
    src = BUILD_R3X.read_text()
    assert "mech/assemblies/kit/finish.json" in src and "MATERIAL_RULES" not in src
    assert re.search(r"def material_class\(name\):\s+cls = KIT_PAINT\.get", src)


@pytest.mark.skipif(not BUILD_R3X.exists(), reason="no sim/ checkout")
def test_original_rig_paints_the_mouth_and_neck_as_the_manifest_does():
    from assemblies.community.assembly import MOUTH_FINISH
    from assemblies.r3x_animation.assembly import apply_finishes

    mouth = _literal(_build_r3x(), "MOUTH_PARTS")  # {"Grill": ("H_MOUTH_GRILL", "metal_dark"), ...}
    ids = {"Grill": "mouth_grill", "LightPipe": "mouth_light_pipe", "BackMount": "mouth_back_mount"}
    for f, (_, paint) in mouth.items():
        assert MOUTH_FINISH[ids[f]].get("paint") == paint, f
    # the neck rod: build_r3x's NECK cylinder and the neck tubes (Anderson's and the column's)
    src = BUILD_R3X.read_text()
    neck = re.search(r'neck\.name = "NECK".*?get_material\("(\w+)"\)', src, re.S).group(1)
    tube = part("neck_tube", "hardware", exposed=True)
    tube.origin = "ours"
    apply_finishes([tube])
    assert tube.finish["paint"] == neck
    col = (MECH / "assemblies/column/assembly.py").read_text()
    assert re.search(r'"col_neck_tube": finish\(paint="(\w+)"', col).group(1) == neck
