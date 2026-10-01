"""mech/.venv/bin/python -m workbench build <assembly> [--watch] [--sketch] [--no-checks] [--no-export]
mech/.venv/bin/python -m workbench test <assembly>     (the suite, TESTS.md; exit 1 on a failure)

Run from mech/ (or set PYTHONPATH=mech). <assembly> is a folder name under mech/assemblies/
(`hunter_head`), a dotted module, or a path to an assembly.py. Output: mech/out/<id>/.
--watch rebuilds whenever the assembly folder or the workbench changes (each build in a
fresh process, so edited modules always reload).
--sketch (prototyping): --no-checks --no-export; only the files whose mesh changed are rewritten (the
build's signature cache), and with --watch it also watches mech/parts and every assembly, so saving any
part rebuilds and Build (the sim's dev server) reloads the open assembly in place.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

# bytecode caches on, even under PYTHONDONTWRITEBYTECODE (an agent shell sets it): recompiling
# build123d/trimesh/sympy costs ~3 s on every build
sys.dont_write_bytecode = False

from .build import MECH, OUT, build, log


def main(argv=None):
    ap = argparse.ArgumentParser(prog="workbench")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="build an assembly into mech/out/<id>/")
    b.add_argument("assembly")
    b.add_argument("--watch", action="store_true")
    b.add_argument("--no-checks", action="store_true")
    b.add_argument("--no-export", action="store_true")
    b.add_argument("--sketch", action="store_true", help="prototype: --no-checks --no-export, watch all parts")
    b.add_argument("--out", type=Path, default=OUT)
    t = sub.add_parser("test", help="run the assembly test suite (TESTS.md)")
    t.add_argument("assembly")
    t.add_argument("--out", type=Path, default=OUT)
    t.add_argument("--full", action="store_true", help="1 deg sweeps and 5 deg grids (default: 5 / 10 deg)")
    a = ap.parse_args(argv)

    if a.cmd == "test":
        from .build import run_suite

        return 1 if run_suite(a.assembly, a.out, a.full)["failed"] else 0

    if a.sketch:
        a.no_checks = a.no_export = True
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
    if a.sketch:
        cmd.append("--sketch")

    def once():
        import time

        t0 = time.time()
        r = subprocess.run(cmd, cwd=MECH)
        log(f"build ok in {time.time() - t0:.1f} s - watching" if r.returncode == 0 else f"build failed ({r.returncode}) - watching")

    once()
    paths = [p for p in (folder, MECH / "workbench") if p.exists()]
    if a.sketch:  # every assembly and part module (the droid pulls in the column, the kit, Hunter's head...)
        paths = [p for p in (MECH / "assemblies", MECH / "parts", MECH / "workbench", MECH / "r3xmech") if p.exists()]
    log("watching " + ", ".join(str(p) for p in paths))
    for changes in watch(*paths, watch_filter=lambda _c, p: p.endswith(".py")):
        log(f"changed: {', '.join(sorted({Path(p).name for _, p in changes}))}")
        once()
    return 0


if __name__ == "__main__":
    sys.exit(main())
