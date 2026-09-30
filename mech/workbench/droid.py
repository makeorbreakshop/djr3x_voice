"""The whole droid as one assembly, for the suite: every sub-assembly of the built tree (ChildRefs
resolved by building their modules), mounted where the tree puts it, in one frame.

    cd mech && .venv/bin/python -m workbench test kit

The tree keeps each sub-assembly's own frame; the suite works on one set of links and joints, so
`flatten` carries everything into the root frame:

* parts, fasteners, features, linkages and joint pivots/axes by each mount's transform;
* links named `<assembly>:<link>`, except a sub-assembly's jointless links, which ride its mount's
  parent link (they are rigid to it);
* a part or fastener id that repeats across sub-assemblies gets its assembly as a prefix;
* a child that is an unselected variant option (`mount.variant.default` false) is left out, as the
  panel leaves it out;
* a ChildRef's `interface` (mates, fasteners) joins the parent's parts to the child's: ids there
  are `<child id>/<part id>` for the child's side, plain for the parent's.

Sub-assemblies that model no mates (the kit's placement-only models) are reported per assembly
by the connected test's explanation, not hidden: `explained_placement`.
"""

from __future__ import annotations

import copy
import importlib
import math
import time

import numpy as np
import trimesh

from .mates import Mate, moved
from .model import Assembly, Fastener, Joint, Link, Linkage, Part

REFS = {"hunter_head": "assemblies.hunter_head.assembly"}


def _quat_matrix(q):
    x, y, z, w = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def mount_matrix(mount: dict | None) -> np.ndarray:
    m = np.eye(4)
    tr = (mount or {}).get("transform") or {}
    if tr.get("q"):
        m[:3, :3] = _quat_matrix(tr["q"])
    m[:3, 3] = tr.get("t", [0, 0, 0])
    return m


def resolve(child) -> tuple[Assembly | None, dict]:
    """(Assembly, ChildRef dict or {}) for a child entry: a ChildRef is built from its module."""
    if isinstance(child, Assembly):
        return child, {}
    mod = REFS.get(child.get("id"))
    if mod is None:
        return None, child
    a = importlib.import_module(mod).build()
    a.mount = child.get("mount")
    return a, child


def flatten(root: Assembly) -> tuple[Assembly, dict]:
    """(one Assembly in the root frame, info) - see the module doc."""
    out = Assembly(id=root.id, name=root.name, description="whole droid (flattened for the suite)")
    out.links.append(Link("ground", "Ground", None))
    info = {"assemblies": [], "no_mates": {}, "left_out": [], "interfaces": []}
    seen_ids: set[str] = set()
    seen_links = {"ground"}
    interfaces = []

    def uniq(aid, pid):
        return pid if pid not in seen_ids else f"{aid}/{pid}"

    def walk(a: Assembly, M: np.ndarray, parent_link: str, path: str):
        R = M[:3, :3]
        lmap = {}
        for l in a.links:
            if l.joint is None:
                lmap[l.id] = parent_link
            else:
                name = f"{a.id}:{l.id}"
                lmap[l.id] = name
                if name not in seen_links:
                    out.links.append(Link(name, f"{a.name} - {l.name}", f"{a.id}:{l.joint}"))
                    seen_links.add(name)
        # a jointless link that is a joint's parent still maps to the parent link (handled above)
        pmap = {}
        for p in a.parts:
            nid = uniq(a.id, p.id)
            seen_ids.add(nid)
            pmap[p.id] = nid
            q = copy.copy(p)
            q.id = nid
            q.link = lmap.get(p.link, parent_link)
            q.mesh = p.mesh.copy()
            q.mesh.apply_transform(M)
            q.features = {k: moved(v, M) for k, v in (p.features or {}).items()}
            q.explode = tuple(R @ np.asarray(p.explode, float))
            q.linkage = f"{a.id}:{p.linkage}" if p.linkage else None
            out.parts.append(q)
        for f in a.fasteners:
            nid = uniq(a.id, f.id)
            seen_ids.add(nid)
            pmap[f.id] = nid
        for f in a.fasteners:
            g = copy.copy(f)
            g.id = pmap[f.id]
            g.link = lmap.get(f.link, parent_link)
            g.matrix = None if f.matrix is None else M @ f.matrix
            g.features = {k: moved(v, M) for k, v in (f.features or {}).items()}
            g.joins = [pmap.get(j, j) for j in f.joins]
            g.linkage = f"{a.id}:{f.linkage}" if f.linkage else None
            out.fasteners.append(g)
        for j in a.joints:
            out.joints.append(Joint(f"{a.id}:{j.id}", j.name, j.type, lmap.get(j.parent_link, parent_link),
                                    lmap[j.child_link], tuple(M[:3, :3] @ np.asarray(j.pivot, float) + M[:3, 3]),
                                    tuple(R @ np.asarray(j.axis, float)), tuple(j.limits), j.unit, j.profile_joint,
                                    j.profile_limits, dict(j.drive, linkages=[f"{a.id}:{x}" for x in j.drive.get("linkages", [])]),
                                    j.zero, j.inferred, j.inferred_note))
        for lk in a.linkages:
            out.linkages.append(Linkage(
                f"{a.id}:{lk.id}", pmap.get(lk.servo, lk.servo), lmap.get(lk.horn_link, parent_link),
                tuple(R @ np.asarray(lk.centre, float) + M[:3, 3]), tuple(R @ np.asarray(lk.axis, float)), lk.radius,
                tuple(R @ np.asarray(lk.zero_dir, float)), lk.ball_offset, lmap.get(lk.ground_link, parent_link),
                tuple(R @ np.asarray(lk.ground_point, float) + M[:3, 3]), lk.rod_length, lk.servo_range,
                tuple(R @ np.asarray(lk.ground_axis, float)), [pmap.get(x, x) for x in lk.parts], lk.inferred,
                lk.inferred_note))
        for m in a.mates:
            out.mates.append(Mate(f"{a.id}:{m.id}", m.type, (pmap.get(m.a[0], m.a[0]), m.a[1]),
                                  (pmap.get(m.b[0], m.b[0]), m.b[1]), dict(m.params), m.solved, m.note))
        info["assemblies"].append({"id": a.id, "path": path, "parts": len(a.parts), "fasteners": len(a.fasteners),
                                   "mates": len(a.mates), "joints": len(a.joints)})
        if a.parts and not a.mates:
            info["no_mates"][a.id] = [pmap[p.id] for p in a.parts]
        for c in a.children:
            v = (c.mount if isinstance(c, Assembly) else c.get("mount") or {}) or {}
            var = v.get("variant")
            cid = c.id if isinstance(c, Assembly) else c.get("id")
            if var and not var.get("default", False):
                info["left_out"].append(cid)
                continue
            ca, ref = resolve(c)
            if ca is None:
                info["left_out"].append(f"{cid} (not buildable here)")
                continue
            cm = M @ mount_matrix(ca.mount)
            cpl = lmap.get((ca.mount or {}).get("parent_link", ""), parent_link)
            n0 = len(out.parts)
            walk(ca, cm, cpl, f"{path}/{ca.id}")
            if ref.get("interface"):
                interfaces.append((ca.id, pmap, ref["interface"], cm))

    t0 = time.perf_counter()
    walk(root, np.eye(4), "ground", root.id)
    info["flatten_s"] = round(time.perf_counter() - t0, 2)
    # interfaces: '<child>/<id>' names the child's side; plain ids the parent's
    ids = {p.id for p in out.parts} | {f.id for f in out.fasteners}

    def name(child, ref):
        if "/" in ref:
            c, pid = ref.split("/", 1)
            return pid if pid in ids else f"{c}/{pid}"
        return ref

    feats = {p.id: p.features for p in out.parts}
    feats.update({f.id: f.features for f in out.fasteners})
    for cid, _, itf, cm in interfaces:
        for k, m in enumerate(itf.get("mates", [])):
            a, b = (name(cid, m["a"][0]), m["a"][1]), (name(cid, m["b"][0]), m["b"][1])
            out.mates.append(Mate(f"iface:{cid}:{k + 1}_{m['type']}", m["type"], a, b, {}, True, m.get("note", "")))
            info["interfaces"].append(f"{m['type']} {a[0]}.{a[1]} / {b[0]}.{b[1]}")
        for fs in itf.get("fasteners", []):
            host, hf = name(cid, fs["in"][0]), fs["in"][1]
            hfeat = feats[host][hf]
            onto = name(cid, fs["onto"])
            ax = feats[onto].get("axis")
            p0 = np.asarray(hfeat["p"], float)
            d = np.asarray(hfeat["d"], float)
            L = float(fs["spec"]["length_mm"])
            tip = p0 + d * 60.0
            if ax is not None:  # the tip on the tube's surface: along d until radius r from the axis
                c0, u, r = np.asarray(ax["p"]), np.asarray(ax["d"]), ax["r"]
                for s in np.linspace(0, 60, 1201):
                    q = p0 + d * s
                    w = q - c0 - u * ((q - c0) @ u)
                    if np.linalg.norm(w) <= r + 1e-6:
                        tip = q
                        break
            head = tip - d * L
            m = np.eye(4)
            z = d / np.linalg.norm(d)
            xax = np.cross(z, [1, 0, 0] if abs(z[0]) < 0.9 else [0, 1, 0])
            xax /= np.linalg.norm(xax)
            m[:3, 0], m[:3, 1], m[:3, 2], m[:3, 3] = xax, np.cross(z, xax), z, head
            mesh = trimesh.creation.cylinder(radius=float(fs["spec"]["thread"][1:]) / 2 - 0.05, height=L, sections=20)
            mesh.apply_translation([0, 0, L / 2])
            host_link = next((f.link for f in out.fasteners if f.id == host), None) or fs.get("link", "ground")
            out.fasteners.append(Fastener(fs["id"], dict(fs["spec"]), f"{fs['spec']['type']}-{fs['spec']['thread']}x{L:g}",
                                          [host, onto], host_link, "", m, False, fs.get("note", ""), mesh=mesh,
                                          cad="parametric", features={"shank": {"type": "axis", "p": list(map(float, head)),
                                                                             "d": list(map(float, z)), "r": 2.0},
                                                                   "tip": {"type": "plane", "p": list(map(float, tip)),
                                                                           "n": list(map(float, z))}}))
            engage = float(np.linalg.norm(tip - p0))
            out.mates.append(Mate(f"iface:{cid}:{fs['id']}_threaded", "threaded", (fs["id"], "shank"), (host, hf),
                                  {"engage_mm": round(min(engage, L), 2), "into": "metal"}, True,
                                  "set screw in the brass insert (metal thread; its load is the clamp, not pull-out)"))
            info["interfaces"].append(f"{fs['id']}: {fs['spec']['thread']} x {L:g} set screw in {host}, tip on {onto}")
    return out, info


def explained_placement(info) -> list[dict]:
    """The connected test's explanation for sub-assemblies that model no mates: their parts are
    placed (kit export, fitted STL) rather than mated - named per assembly, so any other floating
    part still fails."""
    out = []
    for aid, pids in info["no_mates"].items():
        out.append({"test": "connected", "parts": list(pids),
                    "cause": f"{aid}: a placement model (the kit / R-3X Animation export, fitted STLs): its parts are "
                             "placed, not mated, so the mate graph cannot reach them",
                    "fix": f"model {aid}'s joins as mates (screws, glue seams) as hunter_head does"})
    return out
