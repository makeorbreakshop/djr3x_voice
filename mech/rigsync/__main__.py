"""`mech/.venv/bin/python -m rigsync [--manifest M] [--apply]`

Generates profiles/r3x/robot.generated.json and robot.generated.diff.md from the built droid
manifest. `--apply` then copies the generated joints and actuators into robot.json (after
writing robot.json.bak) - the only way this tool changes the live profile. The runtime can
also run the generated profile without applying it: `R3X_PROFILE=profiles/r3x/robot.generated.json`;
the sim: `?profile=generated`.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

MECH = Path(__file__).resolve().parents[1]
REPO = MECH.parent
sys.path.insert(0, str(MECH))

from rigsync.generate import clip_hits, generate  # noqa: E402
from rigsync.report import write_report  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="rigsync", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", type=Path, default=MECH / "out/r3x_droid/manifest.json")
    ap.add_argument("--profile", type=Path, default=REPO / "profiles/r3x/robot.json")
    ap.add_argument("--out", type=Path, default=REPO / "profiles/r3x/robot.generated.json")
    ap.add_argument("--apply", action="store_true", help="copy the generated joints/actuators into robot.json (backs it up)")
    a = ap.parse_args(argv)
    if not a.manifest.exists():
        print(f"{a.manifest} missing: build it first (mech/.venv/bin/python -m workbench build r3x_droid)", file=sys.stderr)
        return 2
    res = generate(a.manifest, a.profile, MECH / "out/checks.json")
    gen = res["profile"]
    a.out.write_text(json.dumps(gen, indent=2) + "\n")
    old = json.loads(a.profile.read_text())
    hits = clip_hits(REPO / "show", gen, old)
    report = a.out.with_name(a.out.stem + ".diff.md")
    write_report(report, gen, old, res["changes"], hits)
    n_beh = sum(1 for c in res["changes"] if c.get("behaviour"))
    print(f"wrote {a.out.relative_to(REPO)} and {report.relative_to(REPO)}: {len(res['changes'])} changes, "
          f"{n_beh} alter behaviour, {len(hits)} clip tracks leave the new animation range")
    if a.apply:
        bak = a.profile.with_suffix(".json.bak")
        shutil.copy2(a.profile, bak)
        live = json.loads(a.profile.read_text())
        live["joints"] = gen["joints"]
        live["actuators"] = gen["actuators"]
        a.profile.write_text(json.dumps(live, indent=2) + "\n")
        print(f"applied to {a.profile.relative_to(REPO)} (previous: {bak.name})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
