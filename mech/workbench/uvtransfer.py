"""Texture transfer: the Original rig's baked paint (sim/web/public/model/r3x.glb, built by sim/model/build_r3x.py
from the Oga's Cantina references) onto our display meshes, so Build's Exterior look and the assembly video show
the real weathered finish on parts the Original does not have as such (Hunter's head) or has merged.

For every painted part seen from outside, each face of its display GLBs (overview and full detail) takes UVs from
the Original's surface after the part's assembly is registered onto the Original (an ICP from where the manifest
places it). Per face corner, not per vertex: the face's centre picks the nearest Original triangle - in the same
atlas, with the normals agreeing - and so one UV island; each corner takes the UV of its own nearest point when
that lies on the same island, else the centre triangle's UV mapping carried out to the corner. Per-vertex UVs
would let a face whose corners land on different islands smear the whole atlas between them. Written next to each
GLB as `<mesh>.uv.bin` (float32 u, v per face corner, in the GLB's index order) and summarised in
`<out>/<asm>/uvtransfer.json` (part -> atlas, coverage = share of faces that found the Original); the viewer
samples the Original's own textures with them. Parts below MIN_COVER are listed as `procedural` (they keep the
weathering shader).

The UVs derive from kit meshes: they live in mech/out (gitignored), like the display meshes.

    node sim/web/scripts/export_original_surfaces.mjs     # the Original's surfaces -> out/.cache/
    .venv/bin/python -m workbench.uvtransfer hunter_head r3x_droid
"""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

import numpy as np
import trimesh

MECH = Path(__file__).resolve().parents[1]
OUT = MECH / "out"
SURF = OUT / ".cache" / "original_surfaces.json"
MAX_MM = 12.0  # farther than this from the Original: not its surface
NORMAL_DOT = 0.3  # the normals must agree at least this much
MIN_COVER = 0.6  # a part takes the texture when this share of its faces found the Original


def read_glb(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Our display GLB (workbench/build.py _glb): quantized positions, the node's translation + scale."""
    b = path.read_bytes()
    jl = struct.unpack_from("<I", b, 12)[0]
    gl = json.loads(b[20:20 + jl])
    bin_ = b[20 + jl + 8:]
    acc, bv = gl["accessors"], gl["bufferViews"]
    node = gl["nodes"][0]
    pa, ia = acc[0], acc[1]
    pv, iv = bv[pa["bufferView"]], bv[ia["bufferView"]]
    q = np.frombuffer(bin_, np.int16, count=pa["count"] * 4, offset=pv.get("byteOffset", 0)).reshape(-1, 4)[:, :3]
    V = q.astype(np.float64) / 32767 * np.asarray(node["scale"]) + np.asarray(node["translation"])
    dt = np.uint16 if ia["componentType"] == 5123 else np.uint32
    F = np.frombuffer(bin_, dt, count=ia["count"], offset=iv.get("byteOffset", 0)).reshape(-1, 3).astype(np.int64)
    return V, F


class Original:
    def __init__(self):
        d = json.loads(SURF.read_text())
        self.atlas: dict[str, dict] = {}
        for p in d["prims"]:
            P = np.asarray(p["P"], float).reshape(-1, 3)
            U = np.asarray(p["U"], float).reshape(-1, 2)
            F = np.asarray(p["F"], np.int64).reshape(-1, 3)
            a = self.atlas.setdefault(p["atlas"], {"P": [], "U": [], "F": [], "mat": [], "n": 0})
            a["P"].append(P)
            a["U"].append(U)
            a["F"].append(F + a["n"])
            a["mat"] += [p["material"]] * len(F)
            a["n"] += len(P)
        for k, a in self.atlas.items():
            a["P"], a["U"], a["F"] = np.concatenate(a["P"]), np.concatenate(a["U"]), np.concatenate(a["F"])
            a["mesh"] = trimesh.Trimesh(a["P"], a["F"], process=False)
            a["prox"] = trimesh.proximity.ProximityQuery(a["mesh"])
            # UV islands: glTF splits vertices at seams, so faces sharing a vertex share an island
            a["island"] = trimesh.graph.connected_component_labels(a["mesh"].face_adjacency, node_count=len(a["F"]))

    def _uv_at(self, a: dict, tri: np.ndarray, pts: np.ndarray) -> np.ndarray:
        bary = trimesh.triangles.points_to_barycentric(a["P"][a["F"][tri]], pts)
        return np.einsum("ij,ijk->ik", bary, a["U"][a["F"][tri]])

    def project(self, atlas: str, V: np.ndarray, F: np.ndarray):
        """Per face corner: uv (len(F)*3, 2); per face: ok."""
        a = self.atlas[atlas]
        mesh = trimesh.Trimesh(V, F, process=False)
        C = V[F].mean(axis=1)
        cpt, cdist, ctri = a["prox"].on_surface(C)
        agree = np.abs(np.einsum("ij,ij->i", a["mesh"].face_normals[ctri], mesh.face_normals))  # either side
        ok = (cdist <= MAX_MM) & (agree >= NORMAL_DOT)
        vpt, _vd, vtri = a["prox"].on_surface(V)
        vuv = self._uv_at(a, vtri, vpt)
        corner_v = F.reshape(-1)
        corner_t = np.repeat(ctri, 3)
        same = a["island"][vtri[corner_v]] == a["island"][corner_t]
        uv = vuv[corner_v].copy()
        out = ~same
        if out.any():  # the centre triangle's mapping, carried to the corner (barycentrics past the triangle)
            uv[out] = self._uv_at(a, corner_t[out], V[corner_v[out]])
        return uv.astype(np.float32), ok


def _tree(asm_dir: Path) -> dict:
    return json.loads((asm_dir / "manifest.json").read_text())["root"]


def _quat(q):
    x, y, z, w = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def _parts(root: dict, base: Path, M=np.eye(4)):
    """(part, mesh files, the part's frame -> droid frame matrix) for every part in the tree (rest pose)."""
    for p in root.get("parts", []):
        yield p, M
    for c in root.get("children", []):
        if "ref" in c:
            continue  # a referenced assembly (Hunter's head): transferred on its own
        mt = (c.get("mount") or {}).get("transform") or {}
        T = np.eye(4)
        if mt.get("q"):
            T[:3, :3] = _quat(mt["q"])
        T[:3, 3] = mt.get("t", [0, 0, 0])
        yield from _parts(c, base, M @ T)


def _exterior(p: dict) -> bool:
    f = p.get("finish") or {}
    return bool(f.get("paint")) and f["paint"] not in ("none",) and (p.get("exposed") or p.get("class") == "shell" or f.get("kit"))


def transfer(name: str, orig: Original, mount: np.ndarray, atlas_of) -> dict:
    """One assembly's exterior parts: register on the Original, project, write the sidecars and the summary."""
    d = OUT / name
    root = _tree(d)
    items = []
    for p, M in _parts(root, d):
        if not _exterior(p) or p.get("replaced_by"):
            continue
        files = [p["mesh"]] + ([p["mesh_full"]] if p.get("mesh_full") else [])
        V, F = read_glb(d / files[-1])  # the fullest for the registration
        W = (mount @ M @ np.vstack([(V + p["transform"]["t"]).T, np.ones(len(V))]))[:3].T
        items.append((p, files, M, W, F))
    if not items:
        return {}
    # registration: everything at once onto the Original (translation-first ICP from the manifest's placement)
    allW = np.concatenate([w for *_, w, _ in items])
    sample = allW[np.random.default_rng(1).choice(len(allW), min(4000, len(allW)), replace=False)]
    best = None
    for atlas in orig.atlas:
        mesh = orig.atlas[atlas]["mesh"]
        T, _, cost = trimesh.registration.icp(sample, mesh, initial=np.eye(4), max_iterations=30, reflection=False, scale=False)
        if best is None or cost < best[2]:
            best = (atlas, T, cost)
    T = best[1]
    print(f"{name}: {len(items)} exterior parts, registered on {best[0]} (rms {np.sqrt(best[2]):.2f} mm)", flush=True)
    summary = {"source": "sim/web/public/model/r3x.glb", "registration_rms_mm": round(float(np.sqrt(best[2])), 2), "parts": {}}
    for p, files, M, _W, _F in items:
        atl = atlas_of(p)
        cover = []
        for fn in files:
            V, F = read_glb(d / fn)
            W = (T @ mount @ M @ np.vstack([(V + p["transform"]["t"]).T, np.ones(len(V))]))[:3].T
            uv, ok = orig.project(atl, W, F)
            (d / fn).with_suffix(".uv.bin").write_bytes(uv.tobytes())
            cover.append(float(ok.mean()))
        c = min(cover)
        print(f"  {name}/{p['id']}: {atl} {c:.0%}", flush=True)
        summary["parts"][p["id"]] = {"atlas": atl, "coverage": round(c, 3), "textured": c >= MIN_COVER,
                                     "files": [f.replace(".glb", ".uv.bin") for f in files]}
    (d / "uvtransfer.json").write_text(json.dumps(summary, indent=1))
    return summary


def main(argv=None):
    names = (argv if argv is not None else sys.argv[1:]) or ["hunter_head", "r3x_droid"]
    orig = Original()
    head = "r3x_head_basecolor"
    body = "r3x_body_basecolor"
    for name in names:
        if name == "hunter_head":
            # the head stands on the neck: start it at the Original head's height
            mount = np.eye(4)
            mount[1, 3] = 738.3
            s = transfer(name, orig, mount, lambda p: head)
        else:
            s = transfer(name, orig, np.eye(4), lambda p: head if p["id"].startswith("h_") else body)
        parts = s.get("parts", {})
        tex = [k for k, v in parts.items() if v["textured"]]
        proc = [f"{k} ({v['coverage']:.0%})" for k, v in parts.items() if not v["textured"]]
        print(f"{name}: registration rms {s.get('registration_rms_mm')} mm; {len(tex)} textured; procedural: {', '.join(proc) or 'none'}")


if __name__ == "__main__":
    main()
