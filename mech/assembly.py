"""DJ R3X engineering model - Phase A entry point.

    .venv/bin/python assembly.py                 # summary: parts, links, joints (+ evidence)
    .venv/bin/python assembly.py --render        # proof renders -> out/renders/ (gitignored)
    .venv/bin/python assembly.py --checks        # mass, torque, interference sweep -> out/checks.json
    .venv/bin/python -m workbench build kit      # the workbench manifest (mech/workbench, SCHEMA.md)

The model is `assemblies/kit/assembly.py:build_model()` - the printable kit as the backbone
with the R-3X Animation mechanisms (assemblies/r3x_animation) and the community frame/variants
(assemblies/community) attached. Everything is in the canonical body frame (show/SPEC.md), mm,
at the rest pose; `Asm.link_T(link, pose)` gives forward kinematics.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

MECH = Path(__file__).resolve().parent
sys.path.insert(0, str(MECH))

from assemblies.kit.assembly import build_model  # noqa: E402
from r3xmech.model import Asm  # noqa: E402

OUT = MECH / "out"
# Variant selection for checks/renders: the inline model carries the R-3X head (Hunter's is a
# separate module), the Morton frame, and not the alternates below.
EXCLUDE_ASMS = {"randall_frame", "mouth_split"}


def active_parts(root: Asm, include_superseded=False):
    sup = {r for p in root.all_parts() for r in p.replaces}
    skip = {c.id for a in root.walk() if a.id in EXCLUDE_ASMS for c in a.walk()}
    out = []
    for a in root.walk():
        if a.id in skip:
            continue
        for p in a.parts:
            if not include_superseded and p.id in sup:
                continue
            out.append(p)
    return out


def summary(root: Asm):
    parts = active_parts(root)
    print(f"{len(root.all_parts())} parts in the tree, {len(parts)} active; {len(root.all_joints())} joints")
    for j in root.all_joints().values():
        print(f"  {j.id:18s} {j.type:9s} {j.parent_link:>18s} -> {j.child_link:14s} limits {j.limits} "
              f"pivot {np.round(j.pivot, 1)} axis {np.round(j.axis, 3)} [{j.confidence}]")
        for e in j.evidence:
            print(f"      - {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--render", action="store_true")
    ap.add_argument("--checks", action="store_true")
    ap.add_argument("--quick", action="store_true", help="coarser sweeps")
    args = ap.parse_args()
    root = build_model()
    summary(root)
    if args.render:
        from r3xmech.proof import render_all
        render_all(root, active_parts(root), OUT / "renders")
    if args.checks:
        from r3xmech.checks import run_checks
        res = run_checks(root, active_parts(root), quick=args.quick)
        OUT.mkdir(exist_ok=True)
        (OUT / "checks.json").write_text(json.dumps(res, indent=1, default=float))
        print(f"wrote {OUT / 'checks.json'}")


if __name__ == "__main__":
    main()
