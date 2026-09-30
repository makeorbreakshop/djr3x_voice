"""mech/.venv/bin/python -m workbench build <assembly> [--watch] [--no-checks] [--no-export]
mech/.venv/bin/python -m workbench test <assembly>     (the suite, TESTS.md; exit 1 on a failure)

Run from mech/ (or set PYTHONPATH=mech). <assembly> is a folder name under mech/assemblies/
(`hunter_head`), a dotted module, or a path to an assembly.py. Output: mech/out/<id>/.
--watch rebuilds whenever the assembly folder or the workbench changes (each build in a
fresh process, so edited modules always reload).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from .build import MECH, OUT, build, log


def main(argv=None):
    ap = argparse.ArgumentParser(prog="workbench")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="build an assembly into mech/out/<id>/")
    b.add_argument("assembly")
    b.add_argument("--watch", action="store_true")
    b.add_argument("--no-checks", action="store_true")
    b.add_argument("--no-export", action="store_true")
    b.add_argument("--out", type=Path, default=OUT)
    t = sub.add_parser("test", help="run the assembly test suite (TESTS.md)")
    t.add_argument("assembly")
    t.add_argument("--out", type=Path, default=OUT)
    t.add_argument("--full", action="store_true", help="1 deg sweeps and 5 deg grids (default: 5 / 10 deg)")
    a = ap.parse_args(argv)

    if a.cmd == "test":
        from .build import run_suite

        return 1 if run_suite(a.assembly, a.out, a.full)["failed"] else 0

    if not a.watch:
        build(a.assembly, a.out, not a.no_checks, not a.no_export)
        return 0

    from watchfiles import watch

    name = a.assembly
    folder = Path(name).parent if name.endswith(".py") else MECH / "assemblies" / name.split(".")[0]
    cmd = [sys.executable, "-m", "workbench", "build", name, "--out", str(a.out)]
    if a.no_checks:
        cmd.append("--no-checks")
    if a.no_export:
        cmd.append("--no-export")

    def once():
        r = subprocess.run(cmd, cwd=MECH)
        log("build ok - watching" if r.returncode == 0 else f"build failed ({r.returncode}) - watching")

    once()
    paths = [p for p in (folder, MECH / "workbench") if p.exists()]
    log("watching " + ", ".join(str(p) for p in paths))
    for changes in watch(*paths, watch_filter=lambda _c, p: p.endswith(".py")):
        log(f"changed: {', '.join(sorted({Path(p).name for _, p in changes}))}")
        once()
    return 0


if __name__ == "__main__":
    sys.exit(main())
