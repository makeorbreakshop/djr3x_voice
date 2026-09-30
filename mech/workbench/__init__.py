"""The mech workbench: runs an assembly module (mech/assemblies/<name>/assembly.py) and writes
mech/out/<name>/ - per-part GLBs, STL/3MF exports, manifest.json (SCHEMA.md) with checks.

    cd mech && .venv/bin/python -m workbench build hunter_head [--watch]
    cd mech && .venv/bin/python -m pytest workbench/tests -q

The panel's Build mode (sim/web/src/workbench/) reads the output through the dev server.
"""
