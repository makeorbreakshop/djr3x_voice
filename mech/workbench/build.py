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
from . import kitgeom, paths
from .steps import plan as plan_steps
from .model import SCHEMA, VERSION, Assembly, Transform, rollup, vec

MECH = Path(__file__).resolve().parents[1]
OUT = MECH / "out"
GENERATOR = "mech/workbench 0.1"
# Display meshes (the GLBs Build and the Mechanical view load): a triangle budget per class, scaled by
# the part's size (k x bbox diagonal in mm, between a floor and a cap). Full detail stays in the
# STL/3MF exports and in the suite's collision meshes; only the display GLB is decimated.
# These are the overview level (the whole droid at once); a reduced part also gets a full-detail GLB
# (`mesh_full`) the viewer swaps in when it is in focus or close (geom.display_lods).
DISPLAY_BUDGET = {  # class: (triangles per mm of diagonal, floor, cap)
    "shell": (40.0, 800, 30000),
    "mech": (30.0, 600, 16000),
    "servo": (40.0, 1500, 8000),
    "bearing": (40.0, 600, 4000),
    "hardware": (40.0, 600, 6000),
    "fastener": (16.0, 300, 1500),
}
LOD_VERSION = 2  # part of every display GLB's signature: bump when the display levels change


def display_budget(mesh: trimesh.Trimesh, cls: str, override: int | None = None) -> int:
    if override:
        return int(override)
    k, lo, hi = DISPLAY_BUDGET.get(cls, DISPLAY_BUDGET["hardware"])
    diag = float(np.linalg.norm(mesh.extents)) if len(mesh.vertices) else 0.0
    return int(min(hi, max(lo, k * diag)))


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
    """A display GLB: positions quantized to normalized int16 (KHR_mesh_quantization; the node's
    translation + scale dequantize them), indices uint16 where they fit, no normals (the readers
    crease their own). ~8 bytes a vertex + 6-12 a triangle."""
    import json as _json

    path.parent.mkdir(parents=True, exist_ok=True)
    mesh = mesh.copy()
    mesh.remove_unreferenced_vertices()
    mesh.update_faces(mesh.nondegenerate_faces())
    V = np.asarray(mesh.vertices, np.float64)
    F = np.asarray(mesh.faces, np.int64)
    if not len(V) or not len(F):
        V, F = np.zeros((3, 3)), np.array([[0, 1, 2]])
    lo, hi = V.min(0), V.max(0)
    c = (lo + hi) / 2
    h = np.maximum((hi - lo) / 2, 1e-6)
    q = np.round((V - c) / h * 32767).clip(-32767, 32767).astype(np.int16)
    pos = np.zeros((len(q), 4), np.int16)  # 8-byte stride (vertex attributes align to 4)
    pos[:, :3] = q
    idx = F.astype(np.uint16 if len(V) < 65536 else np.uint32).ravel()
    pb, ib = pos.tobytes(), idx.tobytes()
    ib += b"\0" * (-len(ib) % 4)
    gl = {
        "asset": {"version": "2.0", "generator": GENERATOR},
        "extensionsUsed": ["KHR_mesh_quantization"], "extensionsRequired": ["KHR_mesh_quantization"],
        "scene": 0, "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "translation": [float(x) for x in c], "scale": [float(x) for x in h]}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1, "mode": 4}]}],
        "buffers": [{"byteLength": len(pb) + len(ib)}],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(pb), "byteStride": 8, "target": 34962},
                        {"buffer": 0, "byteOffset": len(pb), "byteLength": len(idx) * idx.itemsize, "target": 34963}],
        "accessors": [{"bufferView": 0, "componentType": 5122, "normalized": True, "count": len(q), "type": "VEC3",
                       "min": [int(x) for x in q.min(0)], "max": [int(x) for x in q.max(0)]},
                      {"bufferView": 1, "componentType": 5123 if idx.dtype == np.uint16 else 5125, "count": len(idx),
                       "type": "SCALAR"}],
    }
    js = _json.dumps(gl, separators=(",", ":")).encode()
    js += b" " * (-len(js) % 4)
    binc = pb + ib
    out = (b"glTF" + (2).to_bytes(4, "little") + (12 + 8 + len(js) + 8 + len(binc)).to_bytes(4, "little")
           + len(js).to_bytes(4, "little") + b"JSON" + js + len(binc).to_bytes(4, "little") + b"BIN\0" + binc)
    path.write_bytes(out)


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


EXPORT_VERSION = 4  # bump when the export format changes: every file is rewritten (3: LOD + quantized GLBs;
# 4: rewrite every display mesh decimated before the simplifier lock, geom.decimate)


class _Sigs:
    """Incremental export: each written file's signature (mesh hash + settings) in
    out/<id>/.export_sigs.json; a file whose signature matches and exists is not rewritten."""

    def __init__(self, out: Path):
        self.f = out / ".export_sigs.json"
        try:
            self.d = json.loads(self.f.read_text())
        except Exception:
            self.d = {}
        self.written = 0
        self.skipped = 0

    def fresh(self, rel: str, sig: str, out: Path) -> bool:
        if self.d.get(rel) == sig and (out / rel).exists():
            self.skipped += 1
            return True
        return False

    def mark(self, rel: str, sig: str):
        self.d[rel] = sig
        self.written += 1

    def save(self):
        _atomic_json(self.d, self.f)


def _part_files(p, prefix: str, out: Path, export: bool, sigs: "_Sigs"):
    """(centre, display faces, source faces, exports) for a part; writes only what changed."""
    from .collide import mesh_hash

    m = p.mesh
    centre = (m.bounds[0] + m.bounds[1]) / 2
    target = display_budget(m, p.cls, p.decimate_to)
    h = mesh_hash(m)
    sig = f"{h}:{target}:{EXPORT_VERSION}:L{LOD_VERSION}"
    mesh_rel = f"{prefix}parts/{p.id}.glb"
    full_rel = f"{prefix}parts/full/{p.id}.glb"
    exports = {}
    todo = []
    if not sigs.fresh(mesh_rel, sig, out) or (sigs.d.get(full_rel + "#faces") and not sigs.fresh(full_rel, sig, out)):
        todo.append("glb")
    if export:
        exports["stl"] = f"{prefix}export/{p.id}.stl"
        if not sigs.fresh(exports["stl"], sig, out):
            todo.append("stl")
        if p.printed:
            exports["3mf"] = f"{prefix}export/{p.id}.3mf"
            if not sigs.fresh(exports["3mf"], sig, out):
                todo.append("3mf")
    n_disp = sigs.d.get(mesh_rel + "#faces")
    if todo or n_disp is None:
        local = m.copy()
        local.apply_translation(-centre)
        disp, full = geom.display_lods(local, target, p.cad == "parametric") if target else (local, None)
        n_disp = int(len(disp.faces))
        if "glb" in todo:
            _glb(disp, out / mesh_rel)
            sigs.mark(mesh_rel, sig)
            if full is not None:
                _glb(full, out / full_rel)
                sigs.mark(full_rel, sig)
            sigs.d[full_rel + "#faces"] = int(len(full.faces)) if full is not None else 0
        if "stl" in todo:
            (out / exports["stl"]).parent.mkdir(parents=True, exist_ok=True)
            local.export(out / exports["stl"])
            sigs.mark(exports["stl"], sig)
        if "3mf" in todo:
            try:
                local.export(out / exports["3mf"])
                sigs.mark(exports["3mf"], sig)
            except Exception as e:  # 3MF needs lxml/networkx; STL always works
                log(f"3mf export skipped for {p.id}: {e}")
                exports.pop("3mf", None)
        sigs.d[mesh_rel + "#faces"] = n_disp
    import hashlib

    n_full = int(sigs.d.get(full_rel + "#faces") or 0)
    full_out = (full_rel, n_full) if n_full and (out / full_rel).exists() else None
    return centre, n_disp, mesh_rel, exports, hashlib.sha1(sig.encode()).hexdigest()[:12], full_out


def assembly_json(asm: Assembly, out: Path, prefix: str = "", export: bool = True, loaded_children=None,
                  sigs: "_Sigs | None" = None) -> dict:
    """Write the meshes of `asm` under out/prefix and return its manifest node. Only files whose
    mesh changed are rewritten (_Sigs); the writes run in parallel."""
    from concurrent.futures import ThreadPoolExecutor

    sigs = sigs or _Sigs(out)
    # the build steps (every part and fastener once), the hardware a mesh-only assembly's steps list placed
    # in its holes, and a way in for each part nothing else gives one (workbench/steps.py, kitgeom.py)
    steps = plan_steps(asm)
    all_fast = [*asm.fasteners, *kitgeom.place_hardware(asm, steps)]
    d_mates, d_feats = kitgeom.insert_axes(asm, steps, all_fast)
    # approach paths that pass through nothing already there (workbench/paths.py): for assemblies with their
    # own modelled hardware (the meshes' contacts are meaningful there)
    if asm.fasteners:
        t0 = time.time()
        unclean = paths.plan(asm, steps, all_fast)
        log(f"paths {time.time() - t0:.1f} s: {sum(len(s.sequence) for s in steps)} items, {len(unclean)} not clean"
            + "".join(f"\n  {u['step']} {'+'.join(u['ids'])} hits {', '.join(u['hits'])}" for u in unclean))
    with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 4)) as ex:
        files = list(ex.map(lambda p: _part_files(p, prefix, out, export, sigs), asm.parts))
    parts = []
    for p, (centre, n_disp, mesh_rel, exports, msig, full) in zip(asm.parts, files):
        m = p.mesh
        parts.append(_clean({
            "id": p.id, "name": p.name, "class": p.cls, "link": p.link,
            "transform": Transform(tuple(centre)).json(),
            "mesh": mesh_rel, "mesh_sig": msig, "mesh_full": full[0] if full else None, "export": exports, "source": p.source,
            "material": p.material, "printed": p.printed,
            "explode": vec(np.asarray(p.explode, float) / (np.linalg.norm(p.explode) or 1)),
            "explode_mm": p.explode_mm,
            "mass_g": round(p.mass_g, 1) if p.mass_g else None, "mass_note": p.mass_note,
            "linkage": p.linkage, "role": p.role,
            "bbox": [vec(m.bounds[0]), vec(m.bounds[1])],
            "triangles": _clean({"display": n_disp, "full": full[1] if full else None, "source": int(len(m.faces))}),
            "inferred": p.inferred, "inferred_note": p.inferred_note, "note": p.note,
            "cad": p.cad, "catalog": p.catalog, "features": {**(p.features or {}), **d_feats.get(p.id, {})}, "stretch": p.stretch, "exposed": p.exposed,
            "replaced_by": p.replaced_by, "finish": p.finish,
        }))
    fast = []
    written = set()
    for f in all_fast:
        node = _clean({"id": f.id, "spec": f.spec, "key": f.key, "joins": f.joins, "link": f.link,
                       "placed": f.matrix is not None, "step": f.step, "cad": f.cad, "catalog": f.catalog,
                       "linkage": f.linkage, "role": f.role,
                       "features": f.features, "inferred": f.inferred, "inferred_note": f.inferred_note})
        if f.matrix is not None:
            node["transform"] = Transform.from_matrix(f.matrix).json()
            name = (f.catalog or f.key).replace(":", "_").replace("/", "_")
            rel = f"{prefix}fasteners/{name}.glb"
            if rel not in written:
                fm = f.mesh if f.mesh is not None else geom.fastener_mesh(f.spec)
                from .collide import mesh_hash

                fsig = f"{mesh_hash(fm)}:{EXPORT_VERSION}:L{LOD_VERSION}"
                if not sigs.fresh(rel, fsig, out):
                    _glb(geom.reduce_mesh(fm, display_budget(fm, "fastener")), out / rel)
                    sigs.mark(rel, fsig)
                written.add(rel)
            node["mesh"] = rel
        node["placed"] = f.matrix is not None
        fast.append(node)
    children = []
    for c in asm.children:
        if isinstance(c, Assembly):
            children.append(assembly_json(c, out, f"{prefix}{c.id}/", export, loaded_children, sigs))
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
        "gears": [_clean({
            "id": g.id, "kind": g.kind, "joint": g.joint, "joint_assembly": g.joint_assembly, "link": g.link,
            "pivot": vec(g.pivot), "axis": vec(g.axis), "deg_per_unit": round(float(g.deg_per_unit), 6),
            "servo": g.servo,
            "servo_deg_per_unit": round(float(g.servo_deg_per_unit), 6) if g.servo_deg_per_unit is not None else None,
            "parts": g.parts, "fasteners": g.fasteners, "mesh_with": g.mesh_with, "note": g.note,
            "inferred": g.inferred, "inferred_note": g.inferred_note,
        }) for g in asm.gears],
        "fasteners": fast,
        "steps": [_clean({**s.__dict__, "n": i + 1}) for i, s in enumerate(steps)],
        "bom": [_clean(b.__dict__) for b in asm.bom],
        "bom_rollup": [_clean(b.__dict__) for b in rollup(asm, loaded_children)],
        "checks": [_clean(c.__dict__) for c in asm.checks] + [_clean(c.__dict__) for c in asm.tests],
        "mates": [_clean({"id": m.id, "type": m.type, "a": {"part": m.a[0], "feature": m.a[1]},
                          "b": {"part": m.b[0], "feature": m.b[1]}, "params": m.params,
                          "solved": m.solved, "note": m.note}) | {"solved": m.solved} for m in asm.mates] + d_mates,
        "children": children,
        "designs": asm.designs,
        "ground": asm.ground,
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
        # cached by everything the checks read: each part's geometry and link, the joints and linkages,
        # the module's explanations and the checks' code
        from .collide import mesh_hash

        ck = geom._cache_key("checks", sorted((p.id, p.link, p.cls, mesh_hash(p.mesh)) for p in asm.parts),
                             [(j.id, j.type, j.parent_link, j.child_link, tuple(j.pivot), tuple(j.axis), tuple(j.limits),
                               json.dumps(j.drive, sort_keys=True, default=str)) for j in asm.joints],
                             [(lk.id, tuple(lk.centre), lk.radius, tuple(lk.zero_dir), tuple(lk.ground_point), lk.rod_length)
                              for lk in asm.linkages],
                             sorted((f.id, f.key, f.link, None if f.matrix is None else np.round(f.matrix, 3).tobytes().hex())
                                    for f in asm.fasteners),
                             json.dumps(getattr(mod, "EXPLAINED", None), default=str),
                             geom.code_sig("workbench.checks"), geom._file_sig(Path(mod.__file__)))
        if hasattr(mod, "checks"):
            asm.checks = geom.cached(ck, lambda: mod.checks(asm))
        else:
            from .checks import run_all

            asm.checks = geom.cached(ck, lambda: run_all(asm))
        for c in asm.checks:
            log(f"  [{c.status:4}] {c.title}: {c.summary}")
        log(f"checks {time.time() - t1:.1f} s")
    # finishes (paint, print colour) on every inline part: cheap, so on every build, --sketch included
    from .finish import check as finish_check

    fc = finish_check(list(_all_parts(asm)))
    asm.checks = [c for c in asm.checks if c.id != "finish"] + [fc]
    log(f"  [{fc.status:4}] {fc.title}: {fc.summary}")
    if run_checks:
        t2 = time.time()
        asm.tests = suite_for(mod, asm, out=out_root / asm.id)
        log(f"suite {time.time() - t2:.1f} s: " + ", ".join(f"{c.id[5:]} {c.status}" for c in asm.tests))
    out = out_root / asm.id
    out.mkdir(parents=True, exist_ok=True)
    t3 = time.time()
    sigs = _Sigs(out)
    node = assembly_json(asm, out, "", export, sigs=sigs)
    try:  # coupled joint limits the whole-droid suite derived (python -m workbench test <root>)
        node["couplings"] = json.loads((out / "couplings.json").read_text())["couplings"]
    except Exception:
        pass
    removed = prune(out, node, export, sigs)
    sigs.save()
    log(f"files {time.time() - t3:.1f} s: {sigs.written} written, {sigs.skipped} unchanged, {removed} stale removed")
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


def _all_parts(asm: Assembly):
    yield from asm.parts
    for c in asm.children:
        if isinstance(c, Assembly):
            yield from _all_parts(c)


def prune(out: Path, node: dict, export: bool, sigs: "_Sigs") -> int:
    """Delete the meshes and exports under out/ that this manifest no longer references (parts moved
    to another sub-assembly, a renamed or removed part, a variant restructured), and empty folders.
    Interference, requests and clearance outputs are left alone; without --export so are exports."""
    keep: set[str] = set()

    def walk(n):
        for p in n.get("parts", []):
            keep.add(p.get("mesh", ""))
            if p.get("mesh_full"):
                keep.add(p["mesh_full"])
            keep.update((p.get("export") or {}).values())
        for f in n.get("fasteners", []):
            if f.get("mesh"):
                keep.add(f["mesh"])
        for c in n.get("children", []):
            if "parts" in c or "children" in c:
                walk(c)

    walk(node)
    exts = {".glb", ".stl", ".3mf"} if export else {".glb"}
    n = 0
    for f in sorted(out.rglob("*")):
        rel = f.relative_to(out).as_posix()
        if not f.is_file() or f.suffix not in exts or rel.split("/")[0] in ("interference", "requests", "clearance"):
            continue
        if rel not in keep:
            f.unlink()
            sigs.d.pop(rel, None)
            sigs.d.pop(rel + "#faces", None)
            n += 1
    for d in sorted((x for x in out.rglob("*") if x.is_dir()), key=lambda x: -len(x.parts)):
        if not any(d.iterdir()):
            d.rmdir()
    return n


def write_interference(suite, out: Path):
    """out/interference.json (+ interference/<n>.glb, the shared solid of each pair) for Build's
    Interference overlay: every overlapping pair at rest, explained or not."""
    rows = []
    (out / "interference").mkdir(parents=True, exist_ok=True)
    for k, it in enumerate(getattr(suite, "interference", []) or []):
        row = {x: it[x] for x in ("a", "b", "depth_mm", "at", "explained", "volume_mm3")}
        if it.get("mesh") is not None:
            v, f, _ = it["mesh"]
            rel = f"interference/{k + 1}.glb"
            _glb(trimesh.Trimesh(v, f, process=False), out / rel)
            row["mesh"] = rel
        rows.append(row)
    _atomic_json({"pairs": rows, "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")},
                 out / "interference.json")


def suite_for(mod, asm, full: bool = False, whole: bool = False, out: Path | None = None):
    """The suite on `asm`. A root that only holds sub-assemblies (the droid) is tested whole when
    `whole` (the `test` command: every sub-assembly flattened into one, workbench/droid.py);
    a build skips it (the flatten builds each ChildRef's module)."""
    from .suite import Suite

    if not asm.parts:
        if not whole or not asm.children:
            return []
        from .droid import REFS, explained_placement, flatten

        t0 = time.time()
        flat, info = flatten(asm)
        explained = list(getattr(mod, "EXPLAINED", None) or [])
        for cid, modname in REFS.items():  # a child module's own explanations hold inside the droid too
            explained += list(getattr(importlib.import_module(modname), "EXPLAINED", None) or [])
        explained += explained_placement(info, flat)
        tol = {**(getattr(mod, "TOLERANCES", None) or {}), "max_grid_poses": 3000}
        s = Suite(flat, tol=tol, explained=explained, full=full)
        from .droid import derive_couplings

        t1 = time.time()
        flat.couplings = s.couplings = derive_couplings(s, flat)
        info["couplings_s"] = round(time.time() - t1, 1)
        if out is not None:
            out.mkdir(parents=True, exist_ok=True)
            _atomic_json({"couplings": flat.couplings}, out / "couplings.json")
            from .droid import bom_discrepancies

            bom_discrepancies(info, out)
        res = s.run()
        if out is not None:
            write_interference(s, out)
        from .model import Check

        res.insert(0, Check("test_droid_tree", "test", "pass", "Whole droid: the tree, flattened",
                            f"{len(info['assemblies'])} assemblies, {len(flat.parts)} parts, {len(flat.fasteners)} fasteners, "
                            f"{len(flat.joints)} joints, {len(flat.mates)} mates ({sum(m.type == 'placed' for m in flat.mates)} placement-only); "
                            f"superseded kit parts left out: {len(info['superseded'])}; left out (unselected variants): "
                            f"{', '.join(info['left_out']) or 'none'}; interface: {'; '.join(info['interfaces']) or 'none'}; "
                            f"flatten {time.time() - t0:.1f} s (child builds included); coupled limits: "
                            + ("; ".join(f"{c['joint']}({c['depends_on']})" for c in flat.couplings) or "none")
                            + f" in {info.get('couplings_s', 0)} s",
                            parts=[], assumptions=[f"{a['path']}: {a['parts']} parts, {a['mates']} mates"
                                                   for a in info["assemblies"]]))
        return res
    s = Suite(asm, tol=getattr(mod, "TOLERANCES", None), explained=getattr(mod, "EXPLAINED", None), full=full)
    res = s.run()
    if out is not None:
        write_interference(s, out)
    return res


def run_suite(name: str, out_root: Path = OUT, full: bool = False) -> dict:
    """`python -m workbench test <assembly>`: build (no export), run the suite, write
    out/<id>/test_report.{json,md}, print the table."""
    mod = load_module(name)
    asm: Assembly = mod.build()
    asm.validate()
    t0 = time.time()
    tests = suite_for(mod, asm, full, whole=True, out=out_root / asm.id)
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
