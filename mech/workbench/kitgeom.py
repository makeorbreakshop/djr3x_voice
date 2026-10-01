"""What a mesh-only assembly (the kit's STLs) says about how it goes together, read from its geometry at
manifest build (build.py `assembly_json`; the suite's own Assembly is not changed):

- `place_hardware`: the hardware an authored step lists (the guide transcription's McMaster lines, with a
  count and the parts it goes into) placed in the step's parts' round holes - an insert or a magnet in a
  pocket of the size it needs, a screw down a line of coaxial holes through the parts it joins, head on
  the open end - when exactly as many holes of that size are there as the step names; otherwise the line
  stays `unplaced`. Placed hardware is `inferred` (the guide names how many and what, the holes where).
- `insert_axes`: for a part nothing else gives a way in (no mate, no fastener to what is already built),
  the axis of its contact with what it goes onto - the normal of a flat seat, the axis of a bore or a pin
  (the faces it touches, by their normals' principal directions) - as a derived, inferred mate with the
  part it touches most, which the viewer moves it in along.

Holes are read from the meshes' planar facets: each closed boundary loop of a flat face that is a circle
is a hole (or a pocket) opening on that face, its axis the face's normal.
"""

from __future__ import annotations

import math
import re

import numpy as np
import trimesh

from . import geom
from .collide import mesh_hash
from .mates import axis, frame_on_axis, plane
from .model import Fastener, Step

INCH = 25.4
#: Nominal major diameter (mm) of the threads the kit's hardware uses.
THREAD_D = {"2-56": 2.18, "4-40": 2.84, "6-32": 3.51, "8-32": 4.17, "10-32": 4.83, "1/4-20": 6.35,
            "M2": 2.0, "M2.5": 2.5, "M3": 3.0, "M4": 4.0, "M5": 5.0, "M6": 6.0}


def _inch(s: str) -> float:
    """"1/2" -> 12.7, "1" -> 25.4, "5/16" -> 7.94."""
    s = s.strip()
    if " " in s:
        a, b = s.split(" ", 1)
        return _inch(a) + _inch(b)
    if "/" in s:
        n, d = s.split("/")
        return float(n) / float(d) * INCH
    return float(s) * INCH


def parse(desc: str) -> dict | None:
    """A guide hardware line as a placeable spec: {type, thread, d_mm, length_mm} (or None: not placeable -
    standoffs, tubes, rod ends, washers... stay listed)."""
    t = desc.lower()
    if any(w in t for w in ("standoff", "spacer", "tube", "rod end", "linkage", "bearing", "washer", "shim", "nut")):
        return None
    m = re.search(r"\b(m\d(?:\.\d)?|\d+-\d+|1/4-20)\b", t)
    if "magnet" in t:
        od = re.search(r"([\d/ .]+)in od", t)
        h = re.search(r"x\s*([\d/ .]+)in", t)
        if not od:
            return None
        D = _inch(od.group(1))
        return {"type": "magnet", "d_mm": round(D, 2), "length_mm": round(_inch(h.group(1)) if h else D / 2, 2)}
    if not m:
        return None
    thread = m.group(1).upper() if m.group(1).startswith("m") else m.group(1)
    d = THREAD_D.get(thread)
    if d is None:
        return None
    if "insert" in t:
        ln = re.search(r",\s*([\d.]+)(mm|in)", t)
        L = (float(ln.group(1)) * (INCH if ln.group(2) == "in" else 1.0)) if ln else d * 1.4
        return {"type": "insert", "thread": thread, "d_mm": d, "length_mm": round(L, 2)}
    if "screw" not in t:
        return None
    lm = re.search(r"x\s*([\d.]+)\s*mm", t) or re.search(r"x\s*([\d/ .]+)\s*in", t)
    if not lm:
        return None
    L = float(lm.group(1)) if "mm" in lm.group(0) else _inch(lm.group(1))
    kind = "fhcs" if "flat head" in t else "bhcs" if ("button" in t or "pan head" in t or "truss" in t) else "shcs"
    return {"type": kind, "thread": thread, "d_mm": d, "length_mm": round(L, 2)}


def radius_window(spec: dict) -> tuple[float, float]:
    """The hole radii (mm) the hardware fits: a pocket for an insert or a magnet, a clearance or a tap hole for a screw."""
    d = spec["d_mm"]
    if spec["type"] == "magnet":
        return 0.46 * d, 0.62 * d
    if spec["type"] == "insert":
        return 0.5 * d, 0.85 * d
    return 0.36 * d, 0.62 * d


def hardware_mesh(spec: dict) -> trimesh.Trimesh:
    """A plain display mesh, head (or top face) at the origin, +Z the insertion direction."""
    d = spec["d_mm"]
    L = spec["length_mm"]
    if spec["type"] in ("insert", "magnet"):
        if spec["type"] == "magnet":
            m = trimesh.creation.cylinder(radius=d / 2, height=L, sections=24)
        else:
            m = trimesh.creation.annulus(r_min=d / 2 * 0.95, r_max=d * 0.62, height=L, sections=20)
        m.apply_translation([0, 0, L / 2])
        return m
    shank = trimesh.creation.cylinder(radius=d / 2, height=L, sections=16)
    shank.apply_translation([0, 0, L / 2])
    if spec["type"] == "fhcs":
        head = trimesh.creation.cone(radius=d, height=d * 0.6, sections=20)
        head.apply_transform(trimesh.transformations.rotation_matrix(math.pi, [1, 0, 0]))
        head.apply_translation([0, 0, d * 0.6])
    else:
        hh = d * (0.55 if spec["type"] == "bhcs" else 1.0)
        head = trimesh.creation.cylinder(radius=d * (0.95 if spec["type"] == "bhcs" else 0.8), height=hh, sections=20)
        head.apply_translation([0, 0, -hh / 2])
    return trimesh.util.concatenate([head, shank])


# ------------------------------------------------------------------ holes

def holes(mesh: trimesh.Trimesh) -> list[tuple[np.ndarray, np.ndarray, float]]:
    """Round openings on the mesh's flat faces: (centre, outward normal of the face, radius), mm."""
    key = geom._cache_key("kit-holes-v1", mesh_hash(mesh))

    def run():
        out = []
        m = mesh
        if not len(m.facets):
            return out
        for fi, edges in enumerate(m.facets_boundary):
            if len(edges) < 8:
                continue
            n = m.facets_normal[fi]
            g = trimesh.graph.nx.Graph()
            g.add_edges_from(map(tuple, edges))
            for comp in trimesh.graph.nx.connected_components(g):
                if len(comp) < 8 or len(comp) > 400:
                    continue
                sub = g.subgraph(comp)
                if any(dg != 2 for _, dg in sub.degree()):
                    continue
                v = m.vertices[list(comp)]
                c = v.mean(axis=0)
                rel = v - c
                rel -= np.outer(rel @ n, n)
                rr = np.linalg.norm(rel, axis=1)
                r = float(rr.mean())
                if r < 0.6 or r > 12 or float(rr.std()) > 0.06 * r:
                    continue
                out.append((c.tolist(), n.tolist(), r))
        return out

    return [(np.asarray(c), np.asarray(n), r) for c, n, r in geom.cached(key, run)]


def _sites(asm, pids: list[str]) -> list[list[dict]]:
    """The step's holes grouped into lines (coaxial across parts): each a list of {part, c, n, r}."""
    parts = {p.id: p for p in asm.parts}
    hs = []
    for pid in pids:
        p = parts.get(pid)
        if p is None or p.mesh is None or p.replaced_by:
            continue
        for c, n, r in holes(p.mesh):
            hs.append({"part": pid, "c": c, "n": n, "r": r})
    sites: list[list[dict]] = []
    for h in hs:
        for s in sites:
            a = s[0]
            if abs(float(a["n"] @ h["n"])) < 0.995:
                continue
            off = h["c"] - a["c"]
            if np.linalg.norm(off - a["n"] * (off @ a["n"])) < 0.6:
                s.append(h)
                break
        else:
            sites.append([h])
    return sites


def place_hardware(asm, steps: list[Step]) -> list[Fastener]:
    """Place the hardware the authored steps list where the holes say (see the module docstring). The steps'
    `fasteners` gain the new ids and their `unplaced` counts go down by what was placed."""
    out: list[Fastener] = []
    taken: set[int] = set()
    placed_ids = set()
    for st in steps:
        if not st.unplaced:
            continue
        pids = list(dict.fromkeys([*st.parts, *st.context]))
        sites = _sites(asm, pids)
        keep = []
        for u in st.unplaced:
            spec = (u.get("spec") or {})
            sp = parse(str(spec.get("desc", ""))) if spec.get("mcmaster") else None
            n = u.get("count")
            if not sp or not n:
                keep.append(u)
                continue
            lo, hi = radius_window(sp)
            cand = [k for k, s in enumerate(sites) if id(s) not in taken and any(lo <= h["r"] <= hi for h in s)]
            if sp["type"] in ("insert", "magnet"):
                # a pocket in the part the guide names (its note says which), else in any one part
                named = [k for k in cand if any(h["part"].lower() in str(u.get("note", "")).lower().replace("_", "_")
                                                 or _code(asm, h["part"]).lower() in str(u.get("note", "")).lower() for h in sites[k])]
                if len(named) >= n:
                    cand = named
            else:
                through = [k for k in cand if len({h["part"] for h in sites[k]}) > 1]
                if len(through) >= n:
                    cand = through
            if len(cand) != n:
                keep.append(u)
                continue
            new_parts = set(st.parts)
            for k in cand:
                s = sites[k]
                taken.add(id(s))
                fid = f"kit_{st.id}_{spec['mcmaster']}_{len(placed_ids) + 1}"
                placed_ids.add(fid)
                f = _fastener(asm, fid, sp, spec, u, s, new_parts, st.id)
                out.append(f)
                st.fasteners.append(fid)
        st.unplaced = keep
    return out


def _code(asm, pid: str) -> str:
    p = next((x for x in asm.parts if x.id == pid), None)
    return p.name if p is not None else pid


def _fastener(asm, fid, sp, spec, u, site, new_parts, step_id) -> Fastener:
    """One piece of hardware on a line of holes: an insert or a magnet into its pocket from the open face,
    a screw from the outermost hole of the line (a new part's, when it has one) through the rest."""
    a = site[0]["n"]
    proj = sorted(site, key=lambda h: float(h["c"] @ a))
    if sp["type"] in ("insert", "magnet"):
        h = proj[-1] if float(proj[-1]["n"] @ a) > 0 else proj[0]
        p, d = h["c"], -h["n"]
        joins = [h["part"]]
    else:
        ends = [proj[0], proj[-1]]
        # the head sits on an end whose face looks out of the stack, on a part this step brings when it can
        out_ends = [h for h in ends if (h is proj[-1] and float(h["n"] @ a) > 0) or (h is proj[0] and float(h["n"] @ a) < 0)] or ends
        head = next((h for h in out_ends if h["part"] in new_parts), out_ends[0])
        p, d = head["c"], -head["n"]
        joins = list(dict.fromkeys(h["part"] for h in site))
    link = next(x.link for x in asm.parts if x.id == joins[0])
    full = {**spec, "type": sp["type"], "thread": sp.get("thread"), "length_mm": sp["length_mm"], "d_mm": sp["d_mm"]}
    feats = {"shank" if sp["type"] not in ("insert", "magnet") else "bore": axis(p, d, sp["d_mm"] / 2), "head": plane(p, -d)}
    f = Fastener(fid, {k: v for k, v in full.items() if v is not None}, f"mcmaster-{spec['mcmaster']}", joins, link, step_id,
                 frame_on_axis(p, d), inferred=True,
                 inferred_note=f"the guide lists it for this step ({u.get('note', '') or 'no note'}); placed in the matching "
                               f"round hole of the print, its position read from the mesh", mesh=hardware_mesh(sp),
                 cad="placeholder", features=feats)
    return f


# ------------------------------------------------------------------ insertion axes

def contact_axis(mesh: trimesh.Trimesh, others: list[trimesh.Trimesh], tol: float = 1.0):
    """The axis a part goes on along, from its faces that touch `others`: (point, direction, kind), kind
    'plane' (a flat seat: along its normal) or 'axis' (a bore, a pin, a corner: along the line the contact
    normals turn about); None when it touches nothing."""
    if not others:
        return None
    key = geom._cache_key("kit-contact-v1", tol, mesh_hash(mesh), sorted(mesh_hash(o) for o in others))

    def run():
        pts, fi = trimesh.sample.sample_surface_even(mesh, 2500, seed=7) if len(mesh.faces) else (np.zeros((0, 3)), [])
        if not len(pts):
            return None
        near = np.zeros(len(pts), bool)
        for o in others:
            lo, hi = o.bounds
            inbox = np.all((pts >= lo - tol) & (pts <= hi + tol), axis=1)
            if not inbox.any():
                continue
            _, dist, _ = o.nearest.on_surface(pts[inbox])
            idx = np.where(inbox)[0]
            near[idx[dist <= tol]] = True
        if near.sum() < 12:
            return None
        nrm = mesh.face_normals[np.asarray(fi)[near]]
        cov = nrm.T @ nrm / len(nrm)
        w, v = np.linalg.eigh(cov)  # ascending
        c = pts[near].mean(axis=0)
        if w[2] > 0.75:
            return c.tolist(), v[:, 2].tolist(), "plane"
        return c.tolist(), v[:, 0].tolist(), "axis"

    r = geom.cached(key, run)
    return None if r is None else (np.asarray(r[0]), np.asarray(r[1]), r[2])


def insert_axes(asm, steps: list[Step], fasteners: list[Fastener]) -> tuple[list[dict], dict[str, dict]]:
    """Derived mates (JSON) for the parts nothing else gives a way in, and the features they name."""
    parts = {p.id: p for p in asm.parts}
    mated = {m.a[0] for m in asm.mates} | {m.b[0] for m in asm.mates}
    order = {pid: k for k, s in enumerate(steps) for pid in s.parts}
    mates: list[dict] = []
    feats: dict[str, dict] = {}
    for k, st in enumerate(steps):
        for pid in st.parts:
            p = parts.get(pid)
            if p is None or p.mesh is None or pid in mated:
                continue
            built = [q for q, kk in order.items() if kk < k or (kk == k and st.parts.index(q) < st.parts.index(pid))]
            if any(pid in f.joins and any(j in built for j in f.joins if j != pid) for f in fasteners):
                continue  # its screws say how it goes on
            # the built part it touches most is its seat (each tested alone: the first with any contact, biggest first)
            near = [parts[q] for q in built if parts[q].mesh is not None and _gap(p.mesh, parts[q].mesh) < 1.5]
            if not near:
                continue
            got = contact_axis(p.mesh, [q.mesh for q in near])
            if not got:
                continue
            c, d, kind = got
            name = "contact_seat" if kind == "plane" else "contact_axis"
            feats.setdefault(pid, {})[name] = plane(c, d) if kind == "plane" else axis(c, d)
            host = max(near, key=lambda q: _overlap(p.mesh, q.mesh))
            mates.append({"id": f"derived_{pid}", "type": "coincident" if kind == "plane" else "concentric",
                          "a": {"part": pid, "feature": name}, "b": {"part": host.id, "feature": name},
                          "solved": True, "derived": True, "inferred": True,
                          "note": "read from the faces it touches (their normals' principal direction), not modelled"})
    return mates, feats


def _gap(a: trimesh.Trimesh, b: trimesh.Trimesh) -> float:
    lo = np.maximum(a.bounds[0], b.bounds[0])
    hi = np.minimum(a.bounds[1], b.bounds[1])
    return float(np.linalg.norm(np.maximum(lo - hi, 0)))


def _overlap(a: trimesh.Trimesh, b: trimesh.Trimesh) -> float:
    lo = np.maximum(a.bounds[0], b.bounds[0])
    hi = np.minimum(a.bounds[1], b.bounds[1])
    return float(np.prod(np.maximum(hi - lo + 1.0, 0)))
