"""Run an assembly module and write mech/out/<assembly>/: per-part GLBs (decimated for
display), full-detail STL/3MF exports, shared fastener GLBs and manifest.json (SCHEMA.md)."""

from __future__ import annotations

import datetime as dt
import importlib
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import trimesh

from . import geom
from .model import SCHEMA, VERSION, Assembly, Transform, rollup, vec

MECH = Path(__file__).resolve().parents[1]
OUT = MECH / "out"
GENERATOR = "mech/workbench 0.1"
# Display meshes: only the big cosmetic shells are decimated (with a floor); mechanical parts keep
# their full tessellation so holes and bosses stay round. Normals are creased at load (30 deg).
DISPLAY_FACES = {"shell": 80000}


def log(msg):
    print(f"[workbench] {msg}", flush=True)


def load_module(name: str):
    """`hunter_head`, `assemblies.hunter_head.assembly` or a path to an assembly.py."""
    if str(MECH) not in sys.path:
        sys.path.insert(0, str(MECH))
    p = Path(name)
    if p.suffix == ".py" and p.exists():
        spec = importlib.util.spec_from_file_location(f"asm_{p.parent.name}", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    if "." not in name:
        name = f"assemblies.{name}.assembly"
    return importlib.import_module(name)


def _glb(mesh: trimesh.Trimesh, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    mesh = mesh.copy()
    mesh.remove_unreferenced_vertices()
    mesh.update_faces(mesh.nondegenerate_faces())
    mesh.visual = trimesh.visual.ColorVisuals(mesh)
    path.write_bytes(trimesh.exchange.gltf.export_glb(trimesh.Scene(mesh), include_normals=True))


def _atomic_json(obj, path: Path):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=1))
    os.replace(tmp, path)


def _empty(v) -> bool:
    if isinstance(v, np.ndarray):
        return v.size == 0
    return v is None or (isinstance(v, (str, list, dict, tuple)) and len(v) == 0)


def _jsonable(v):
    if isinstance(v, np.ndarray):
        return v.tolist()
    if isinstance(v, (np.floating, np.integer)):
        return v.item()
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    return v


def _clean(d: dict) -> dict:
    return {k: _jsonable(v) for k, v in d.items() if not _empty(v) or k in ("link",)}


def assembly_json(asm: Assembly, out: Path, prefix: str = "", export: bool = True, loaded_children=None) -> dict:
    """Write the meshes of `asm` under out/prefix and return its manifest node."""
    parts = []
    for p in asm.parts:
        m = p.mesh
        centre = (m.bounds[0] + m.bounds[1]) / 2
        local = m.copy()
        local.apply_translation(-centre)
        target = p.decimate_to or DISPLAY_FACES.get(p.cls)
        disp = geom.decimate(local, target) if target else local
        mesh_rel = f"{prefix}parts/{p.id}.glb"
        _glb(disp, out / mesh_rel)
        exports = {}
        if export:
            stl_rel = f"{prefix}export/{p.id}.stl"
            (out / stl_rel).parent.mkdir(parents=True, exist_ok=True)
            local.export(out / stl_rel)
            exports["stl"] = stl_rel
            if p.printed:
                try:
                    tmf_rel = f"{prefix}export/{p.id}.3mf"
                    local.export(out / tmf_rel)
                    exports["3mf"] = tmf_rel
                except Exception as e:  # 3MF needs lxml/networkx; STL always works
                    log(f"3mf export skipped for {p.id}: {e}")
        parts.append(_clean({
            "id": p.id, "name": p.name, "class": p.cls, "link": p.link,
            "transform": Transform(tuple(centre)).json(),
            "mesh": mesh_rel, "export": exports, "source": p.source,
            "material": p.material, "printed": p.printed,
            "explode": vec(np.asarray(p.explode, float) / (np.linalg.norm(p.explode) or 1)),
            "explode_mm": p.explode_mm,
            "mass_g": round(p.mass_g, 1) if p.mass_g else None, "mass_note": p.mass_note,
            "linkage": p.linkage, "role": p.role,
            "bbox": [vec(m.bounds[0]), vec(m.bounds[1])],
            "triangles": {"display": int(len(disp.faces)), "source": int(len(m.faces))},
            "inferred": p.inferred, "inferred_note": p.inferred_note, "note": p.note,
            "cad": p.cad, "catalog": p.catalog, "features": p.features,
        }))
    fast = []
    written = set()
    for f in asm.fasteners:
        node = _clean({"id": f.id, "spec": f.spec, "key": f.key, "joins": f.joins, "link": f.link,
                       "placed": f.matrix is not None, "step": f.step, "cad": f.cad, "catalog": f.catalog,
                       "linkage": f.linkage, "role": f.role,
                       "features": f.features, "inferred": f.inferred, "inferred_note": f.inferred_note})
        if f.matrix is not None:
            node["transform"] = Transform.from_matrix(f.matrix).json()
            name = (f.catalog or f.key).replace(":", "_").replace("/", "_")
            rel = f"{prefix}fasteners/{name}.glb"
            if rel not in written:
                _glb(f.mesh if f.mesh is not None else geom.fastener_mesh(f.spec), out / rel)
                written.add(rel)
            node["mesh"] = rel
        node["placed"] = f.matrix is not None
        fast.append(node)
    children = []
    for c in asm.children:
        if isinstance(c, Assembly):
            children.append(assembly_json(c, out, f"{prefix}{c.id}/", export, loaded_children))
        else:
            children.append(c)
    return _clean({
        "id": asm.id, "name": asm.name, "description": asm.description, "frame_note": asm.frame_note,
        "mount": asm.mount, "guide": asm.guide,
        "links": [_clean(l.__dict__) | {"joint": l.joint} for l in asm.links],
        "parts": parts,
        "joints": [_clean({
            "id": j.id, "name": j.name, "type": j.type, "parent_link": j.parent_link, "child_link": j.child_link,
            "pivot": vec(j.pivot), "axis": vec(j.axis), "unit": j.unit,
            "limits": {"min": j.limits[0], "max": j.limits[1]},
            "profile_joint": j.profile_joint,
            "profile_limits": {"min": j.profile_limits[0], "max": j.profile_limits[1]} if j.profile_limits else None,
            "drive": j.drive, "zero": j.zero, "inferred": j.inferred, "inferred_note": j.inferred_note,
        }) | {"profile_joint": j.profile_joint} for j in asm.joints],
        "linkages": [_clean({
            "id": lk.id, "kind": "push_rod", "servo": lk.servo,
            "horn": {"link": lk.horn_link, "centre": vec(lk.centre), "axis": vec(lk.axis), "radius": lk.radius,
                     "zero_dir": vec(lk.zero_dir), "ball_offset": lk.ball_offset},
            "ground": {"link": lk.ground_link, "point": vec(lk.ground_point)},
            "rod_length": round(lk.rod_length, 3), "servo_range_deg": list(lk.servo_range),
            "ground_axis": vec(lk.ground_axis),
            "parts": lk.parts, "inferred": lk.inferred, "inferred_note": lk.inferred_note,
        }) for lk in asm.linkages],
        "fasteners": fast,
        "steps": [_clean({**s.__dict__, "n": i + 1}) for i, s in enumerate(asm.steps)],
        "bom": [_clean(b.__dict__) for b in asm.bom],
        "bom_rollup": [_clean(b.__dict__) for b in rollup(asm, loaded_children)],
        "checks": [_clean(c.__dict__) for c in asm.checks] + [_clean(c.__dict__) for c in asm.tests],
        "mates": [_clean({"id": m.id, "type": m.type, "a": {"part": m.a[0], "feature": m.a[1]},
                          "b": {"part": m.b[0], "feature": m.b[1]}, "params": m.params,
                          "solved": m.solved, "note": m.note}) | {"solved": m.solved} for m in asm.mates],
        "children": children,
        "notes": asm.notes,
    })


def build(name: str, out_root: Path = OUT, run_checks: bool = True, export: bool = True) -> Path:
    t0 = time.time()
    mod = load_module(name)
    asm: Assembly = mod.build()
    asm.validate()
    log(f"{asm.id}: {len(asm.parts)} parts, {len(asm.joints)} joints, {len(asm.fasteners)} fasteners, "
        f"{len(asm.steps)} steps ({time.time() - t0:.1f} s)")
    if run_checks:
        t1 = time.time()
        if hasattr(mod, "checks"):
            asm.checks = mod.checks(asm)
        else:
            from .checks import run_all

            asm.checks = run_all(asm)
        for c in asm.checks:
            log(f"  [{c.status:4}] {c.title}: {c.summary}")
        log(f"checks {time.time() - t1:.1f} s")
    if run_checks:
        t2 = time.time()
        asm.tests = suite_for(mod, asm)
        log(f"suite {time.time() - t2:.1f} s: " + ", ".join(f"{c.id[5:]} {c.status}" for c in asm.tests))
    out = out_root / asm.id
    out.mkdir(parents=True, exist_ok=True)
    node = assembly_json(asm, out, "", export)
    manifest = {"schema": SCHEMA, "version": VERSION,
                "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                "generator": GENERATOR, "root": node}
    _atomic_json(manifest, out / "manifest.json")
    index_path = out_root / "index.json"
    try:
        index = json.loads(index_path.read_text())
    except Exception:
        index = {"assemblies": []}
    index["assemblies"] = [a for a in index["assemblies"] if a["id"] != asm.id] + [
        {"id": asm.id, "name": asm.name, "manifest": f"{asm.id}/manifest.json", "built": manifest["generated_at"]}]
    _atomic_json(index, index_path)
    log(f"wrote {out / 'manifest.json'} ({time.time() - t0:.1f} s)")
    return out / "manifest.json"


def suite_for(mod, asm, full: bool = False):
    from .suite import Suite

    return Suite(asm, tol=getattr(mod, "TOLERANCES", None), explained=getattr(mod, "EXPLAINED", None), full=full).run()


def run_suite(name: str, out_root: Path = OUT, full: bool = False) -> dict:
    """`python -m workbench test <assembly>`: build (no export), run the suite, write
    out/<id>/test_report.{json,md}, print the table."""
    mod = load_module(name)
    asm: Assembly = mod.build()
    asm.validate()
    t0 = time.time()
    tests = suite_for(mod, asm, full)
    took = time.time() - t0
    out = out_root / asm.id
    out.mkdir(parents=True, exist_ok=True)
    rows = [_clean(c.__dict__) for c in tests]
    failed = [c for c in tests if c.status == "fail"]
    report = {"assembly": asm.id, "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
              "failed": len(failed), "explained": sum(c.status == "explained" for c in tests),
              "mode": "full" if full else "quick", "seconds": round(took, 1), "tests": rows}
    _atomic_json(report, out / "test_report.json")
    md = [f"# {asm.name}: test report", "", f"{report['generated_at']}", "", "| test | status | detail |", "|---|---|---|"]
    for c in tests:
        md.append(f"| {c.title} | **{c.status}** | {c.summary.replace('|', '/')} |")
    (out / "test_report.md").write_text("\n".join(md) + "\n")
    for c in tests:
        print(f"{c.status:9} {c.seconds if c.seconds is not None else 0:6.2f}s  {c.title}: {c.summary[:300]}")
    print(f"{len(tests)} tests, {len(failed)} failed, {report['explained']} explained, {report['mode']} run "
          f"{took:.0f} s -> {out / 'test_report.md'}")
    report["failed"] = len(failed)
    return report
