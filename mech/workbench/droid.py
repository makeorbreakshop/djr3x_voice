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
from pathlib import Path

import numpy as np
import trimesh

from .mates import Mate, moved
from .model import Assembly, BomLine, Fastener, Joint, Link, Linkage, Part, Step

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
    info = {"assemblies": [], "no_mates": {}, "left_out": [], "interfaces": [], "superseded": [],
            "bom_by": {}, "have_by": {}, "bom_item": {}}
    ground = [None]
    seen_ids: set[str] = set()
    seen_links = {"ground"}
    interfaces = []

    def uniq(aid, pid):
        return pid if pid not in seen_ids else f"{aid}/{pid}"

    def walk(a: Assembly, M: np.ndarray, parent_link: str, path: str, parent_anchor: str | None = None):
        R = M[:3, :3]
        # a mechanism's own coupled limits (profile joint names, so they hold in the droid as they are)
        out.child_couplings = list(getattr(out, "child_couplings", [])) + list(getattr(a, "couplings", None) or [])
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
        dropped = [p.id for p in a.parts if p.replaced_by or "superseded" in (p.note or "")]
        info["superseded"] += [f"{a.id}/{x}" for x in dropped]
        for p in a.parts:
            if p.id in dropped:
                continue
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
        # steps and BOM lines, per sub-assembly (ids prefixed where they would clash)
        for st in a.steps:
            out.steps.append(Step(f"{a.id}:{st.id}", st.title, [pmap.get(x, x) for x in st.parts],
                                  [pmap.get(x, x) for x in st.fasteners], st.unplaced, st.tools, st.notes,
                                  [pmap.get(x, x) for x in st.context], f"{a.id}:{st.joint}" if st.joint else None,
                                  {f"{a.id}:{k}": v for k, v in (st.pose or {}).items()}, st.guide_page,
                                  text=st.text))
        for f in out.fasteners[len(out.fasteners) - len(a.fasteners):]:
            f.step = f"{a.id}:{f.step}" if f.step else f.step
        for bl in a.bom:
            if bl.category == "fastener":
                info["bom_by"].setdefault(bl.key, {})[a.id] = info["bom_by"].get(bl.key, {}).get(a.id, 0) + bl.qty
                info["bom_item"][bl.key] = bl.item
        for f in a.fasteners:
            info["have_by"].setdefault(f.key, {})[a.id] = info["have_by"].get(f.key, {}).get(a.id, 0) + 1
        for st in a.steps:
            for u in st.unplaced:
                if u.get("count"):
                    info["have_by"].setdefault(u["key"], {})[a.id] = info["have_by"].get(u["key"], {}).get(a.id, 0) + u["count"]
        for bl in a.bom:  # one line per key across the tree (quantities add)
            ex = next((x for x in out.bom if x.key == bl.key), None)
            if ex is None:
                out.bom.append(copy.copy(bl))
            else:
                ex.qty += bl.qty
        kept = [pmap[p.id] for p in a.parts if p.id in pmap]
        info["assemblies"].append({"id": a.id, "path": path, "parts": len(kept), "fasteners": len(a.fasteners),
                                   "mates": len(a.mates), "joints": len(a.joints)})
        parent_anchor = parent_anchor or ground[0]
        anchor = kept[0] if kept else parent_anchor
        if anchor and ground[0] is None:
            ground[0] = anchor  # the first part on the ground: what parentless sub-assemblies join
        if kept and (not a.mates or (a.mount or {}).get("placement_model")):
            # a placement model: its parts joined to its first part, that to the parent's (connected test only);
            # `mount.placement_model`: the kit's mesh assemblies keep that join under the mates modelled in them
            # (assemblies/kit/fastening.py: the races and their screws)
            info["no_mates"][a.id] = kept
            for q in kept[1:] + ([parent_anchor] if parent_anchor else []):
                out.mates.append(Mate(f"placed:{a.id}:{q}", "placed", (kept[0], ""), (q, ""), {}, False,
                                      "placement only (no mate modelled)"))
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
            walk(ca, cm, cpl, f"{path}/{ca.id}", None if ref.get("interface") else anchor)
            # a mated sub-assembly that sits on parts of its parent (the column's Gil plate under the kit's
            # skirt): `mount.rests_on` names them; a placement join for the connected test
            # (an entry {"part", "on", "note"} names the child's part that carries it: a ring plate under a race)
            for rid in ((ca.mount or {}).get("rests_on") or []):
                if isinstance(rid, dict):
                    out.mates.append(Mate(f"placed:{ca.id}:{rid['part']}:{rid['on']}", "placed", (rid["part"], ""),
                                          (rid["on"], ""), {}, False, rid.get("note", "carried by the child's part")))
                elif len(out.parts) > n0:
                    out.mates.append(Mate(f"placed:{ca.id}:{rid}", "placed", (out.parts[n0].id, ""), (rid, ""), {},
                                          False, (ca.mount or {}).get("rests_on_note", "rests on its parent's part")))
            if ref.get("interface"):
                interfaces.append((ca.id, pmap, ref["interface"], cm))

    t0 = time.perf_counter()
    ground = [None]
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


def explained_placement(info, flat) -> list[dict]:
    """Droid-level explanations that follow from what the kit is: its printed parts come as the
    kit cuts them (sized for a larger bed than the H2D's)."""
    kit = [p.id for p in flat.parts if (p.source or {}).get("origin") == "kit" and p.printed]
    kit_all = [p.id for p in flat.parts if (p.source or {}).get("origin") == "kit"]
    morton = [p.id for p in flat.parts if p.id.startswith("morton_")]
    out = []
    if kit:
        out.append({"test": "printable_bed", "parts": kit,
                    "cause": "the kit's printed parts as the kit cuts them (its 'Large Cut' STLs, for a larger bed)",
                    "fix": "print on a larger bed, or use the kit's 'Small Cut' set for these pieces"})
    if kit_all:
        out.append({"test": "no_overlap", "parts": kit_all, "max_mm": 0.4,
                    "cause": "kit part against kit part, <= 0.4 mm: the kit export's own fit (its pieces are drawn to "
                             "touch; the STL tessellation and the kit's snug joints overlap a few tenths)",
                    "fix": "none in the model; on the print, the usual clean-up of a tight kit joint"})
    ids = {p.id for p in flat.parts}
    gears = [x for x in ("pan_pinion", "pan_sector", "lift_pinion", "lift_rack", "lower_pinion", "lower_sector",
                         "top_pinion", "top_sector", "col_lower_pinion", "col_top_pinion", "col_lift_pinion",
                         "col_lift_rack", "tilt_pinion", "tilt_center_gear") if x in ids]
    if gears:
        out.append({"test": "clearance", "parts": gears,
                    "cause": "a pinion and its sector/rack: the manifest turns the pinion with its joint (`gears`), but "
                             "the suite's sweep does not read gears yet, so their teeth pass through each other in the "
                             "sweep; at rest they are phased to mesh",
                    "fix": "none on the parts (the mesh is checked at rest); gear-driven poses in the sweep are a "
                           "model limit"})
    if "neck_spring" in ids:
        out.append({"test": "clearance", "parts": ["neck_spring", "neck_coupler", "ins_coupler_side", "set_coupler_tube"],
                    "cause": "the cosmetic neck spring is rigid in the suite; on the droid it compresses as the head "
                             "comes down (the manifest's stretch)",
                    "fix": "none"})
    if any(x.startswith("col_") for x in ids):  # the central column (assemblies/column): its own explanations
        from assemblies.column.assembly import EXPLAINED as COLUMN_EXPLAINED

        out += list(COLUMN_EXPLAINED)
    if morton:
        out.append({"test": "no_overlap", "parts": morton, "max_mm": 1.1,
                    "cause": "Sam Morton's 2020 posts run up to 1.1 mm into his own frame rings (his export: the post "
                             "ends sit in the rings' sockets)",
                    "fix": "none if the sockets take them on the print; else trim the posts 1 mm"})
    return out


def overlap_requests(out_dir: Path | None = None) -> Path:
    """The unexplained overlaps of the whole droid as change requests, with geometry:

        cd mech && .venv/bin/python -c "from workbench.droid import overlap_requests; overlap_requests()"

    For a pair where one side is a kit / community part (a vendored STL we do not own), the request
    is a clearance cut on that part: the other part grown by 0.5 mm, as a cutter STL in the kit
    part's own STL frame (so a parametric remodel, or a mesh edit, subtracts it directly). Pairs
    inside one designer's own assembly are listed as placement conflicts to review. Reads
    out/r3x_droid/interference.json (run `python -m workbench test kit` first)."""
    import json as _json

    from assemblies.kit.assembly import build_model
    from r3xmech.meshes import part_mesh

    MECHD = Path(__file__).resolve().parents[1]
    out_dir = out_dir or MECHD / "out" / "r3x_droid" / "requests"
    out_dir.mkdir(parents=True, exist_ok=True)
    pairs = [p for p in _json.loads((MECHD / "out" / "r3x_droid" / "interference.json").read_text())["pairs"]
             if not p["explained"]]
    root = build_model()
    by = {p.id: p for p in root.all_parts()}
    hunter = importlib.import_module(REFS["hunter_head"]).build()
    M_h = mount_matrix({"transform": {"t": [0, 738.3, 0]}})
    for p in hunter.parts:
        m = p.mesh.copy()
        m.apply_transform(M_h)
        by.setdefault(p.id, type("P", (), {"id": p.id, "origin": "ours (hunter_head)", "T": M_h, "mesh": m,
                                            "file": None})())

    def owner(p):
        o = getattr(p, "origin", "") or ""
        return "kit" if o == "kit" else "community" if o == "community" else o or "?"

    def world(p):
        return p.mesh if hasattr(p, "mesh") and not hasattr(p, "cls") else part_mesh(p)

    rows = []
    for k, it in enumerate(pairs, 1):
        a, b = by.get(it["a"].split("/")[-1]), by.get(it["b"].split("/")[-1])
        if a is None or b is None:
            rows.append((it, "?", "not found in the model", ""))
            continue
        oa, ob = owner(a), owner(b)
        vendor = [x for x in (a, b) if owner(x) in ("kit", "community")]
        if len(vendor) == 1:
            v = vendor[0]
            o = b if v is a else a
            cut = world(o).copy()
            # grow 0.5 mm: offset along vertex normals (cutters are for clearance, not for fit)
            cut.vertices = cut.vertices + cut.vertex_normals * 0.5
            cut.apply_transform(np.linalg.inv(v.T))  # into the vendored part's STL frame
            fn = f"cut_{v.id}__by__{o.id}.stl"
            cut.export(out_dir / fn)
            rows.append((it, f"{owner(v)} part", f"clearance cut on {v.id} ({Path(str(v.file)).name if v.file else ''}): "
                                                  f"subtract {o.id} + 0.5 mm", fn))
        elif not vendor:
            rows.append((it, f"{oa} / {ob}", "placement / pocket conflict inside our models: review the placement "
                                             "(inferred) or the servo case vs its pocket", ""))
        else:
            rows.append((it, "kit / kit", "two vendored parts: a variant or placement question", ""))
    L = ["# Overlap requests: the droid's unexplained overlaps at rest", "",
         f"{len(pairs)} pairs from out/r3x_droid/interference.json. Cutters are STLs in the vendored part's own "
         "STL frame (the part's mesh grown 0.5 mm): subtract them from that part.", "",
         "| # | pair | depth mm | shared mm3 | whose | request | cutter |", "|---|---|---|---|---|---|---|"]
    for k, (it, who, req, fn) in enumerate(rows, 1):
        L.append(f"| {k} | {it['a']} x {it['b']} | {it['depth_mm']} | {it.get('volume_mm3')} | {who} | {req} | {fn} |")
    f = out_dir / "requests.md"
    f.write_text("\n".join(L) + "\n")
    return f


# joint couplings the whole-droid sweep derives (the dependent joint's limits as a function of the
# driving one, with the others in the chain swept): profile joint names
COUPLED = [("throttle_shoulder", "throttle_elbow", ["throttle_wrist"]),
           # the head low on its lift, nodding forward: the kit mouth reaches the top ring's collars
           # (TR_N, rings round the neck: pan and the ring's own turn do not change the gap)
           ("head_lift", "head_tilt", ["head_roll"])]


def derive_couplings(suite, flat, clear: float = 1.0, step: float = 5.0) -> list[dict]:
    """For each COUPLED (a, b, others): over a grid of a x b (others at their ends and zero), the
    smallest gap between everything those joints move and everything they do not; for each value
    of a, the contiguous range of b round its rest that keeps >= `clear`. A coupled limit surface
    as a table [[a, b_min, b_max], ...], like Bret Benz's djr3x-v2 does in software. Cached by the
    geometry and the joints."""
    from .geom import _cache_key, cached
    from .kinematics import link_matrices, solve_linkages

    byp = {j.profile_joint: j for j in flat.joints if j.profile_joint}
    out = []
    for a_name, b_name, others in COUPLED:
        if a_name not in byp or b_name not in byp:
            continue
        ja, jb = byp[a_name], byp[b_name]
        jo = [byp[o] for o in others if o in byp]
        chain = {ja.id, jb.id} | {j.id for j in jo}
        # what the dependent joint moves, against what none of the chain moves (a limit of the driving
        # joint alone is its own range, not a coupling); parts that stretch are not rigid here
        stretch = {p.id for p in flat.parts if getattr(p, "stretch", None)}
        moving = [k for k in suite.bodies if jb.id in suite.moving_joints(k) and k not in stretch]
        still = [k for k in suite.bodies if not (suite.moving_joints(k) & chain) and k not in stretch]
        va = sorted(set(np.round(np.append(np.arange(ja.limits[0], ja.limits[1] + 1e-9, step), [0.0, ja.limits[1]]), 3)))
        vb = sorted(set(np.round(np.append(np.arange(jb.limits[0], jb.limits[1] + 1e-9, step), [0.0, jb.limits[1]]), 3)))
        vo = [sorted({j.limits[0], 0.0, j.limits[1]}) for j in jo]
        key = _cache_key("coupling-v3", a_name, b_name, [(j.id, tuple(j.limits), tuple(j.pivot), tuple(j.axis)) for j in [ja, jb, *jo]],
                         sorted(suite.scene.hash[k] for k in moving), sorted(suite.scene.hash[k] for k in still), clear, step)
        idx = {k: i for i, k in enumerate(suite.scene.ids)}

        def grid():
            import itertools

            pairs = [(m, s) for m in moving for s in still]
            # pairs already that close at rest (a joint's own bearing faces, the kit's snug pieces,
            # a gear mesh) are not what the motion does: leave them to the overlap test
            ms0 = link_matrices(flat, {})
            sol0 = solve_linkages(flat, {})
            mats0 = {x: suite.body_matrix(suite.bodies[x], ms0, sol0) for x in suite.bodies}
            g0 = suite.scene.gaps(suite.scene.aabbs(mats0), np.array([idx[m] for m, _ in pairs]),
                                  np.array([idx[s] for _, s in pairs]))
            near0 = {pairs[t] for t in np.nonzero(g0 < clear + 0.5)[0]
                     if suite.scene.distance(pairs[t][0], mats0[pairs[t][0]], pairs[t][1], mats0[pairs[t][1]]) < clear}
            pairs = [p for p in pairs if p not in near0 and frozenset(p) not in suite.mated]
            ia = np.array([idx[m] for m, _ in pairs])
            ib = np.array([idx[s] for _, s in pairs])
            D = np.zeros((len(va), len(vb)))
            worst = {}
            for i, a in enumerate(va):
                for k, b in enumerate(vb):
                    dmin, who = np.inf, None
                    for oc in itertools.product(*vo) if vo else [()]:
                        pose = {ja.id: float(a), jb.id: float(b), **{j.id: float(v) for j, v in zip(jo, oc)}}
                        ms = link_matrices(flat, pose)
                        sol = solve_linkages(flat, pose)
                        mats = {x: suite.body_matrix(suite.bodies[x], ms, sol) for x in suite.bodies}
                        gaps = suite.scene.gaps(suite.scene.aabbs(mats), ia, ib)
                        for t in np.nonzero(gaps < clear + 3.0)[0]:
                            m, s_ = pairs[t]
                            d = suite.scene.distance(m, mats[m], s_, mats[s_])
                            if d < dmin:
                                dmin, who = d, (m, s_)
                    D[i, k] = dmin
                    worst[(i, k)] = who
            return D, worst

        D, worst = cached(key, grid)
        rows = []
        k0 = int(np.argmin(np.abs(np.asarray(vb))))
        for i, a in enumerate(va):
            ok = D[i] >= clear
            if not ok.any():
                rows.append([float(a), None, None])
                continue
            k = k0 if ok[k0] else int(np.argmin([abs(vb[x]) if ok[x] else 1e9 for x in range(len(vb))]))
            lo = hi = k
            while lo - 1 >= 0 and ok[lo - 1]:
                lo -= 1
            while hi + 1 < len(vb) and ok[hi + 1]:
                hi += 1
            rows.append([float(a), float(vb[lo]), float(vb[hi])])
        hits = sorted({w for (i, k), w in worst.items() if w and D[i, k] < clear})
        out.append({"joint": b_name, "depends_on": a_name, "swept": others, "clearance_mm": clear, "step_deg": step,
                    "table": rows, "limits": [jb.limits[0], jb.limits[1]],
                    "because": sorted({f"{m} / {s}" for m, s in hits})[:12],
                    "note": f"{b_name}'s range as a function of {a_name} ({', '.join(others)} swept to its ends): outside "
                            f"it the arm comes within {clear} mm of the body"})
    return out


def coupling_allows(couplings: list[dict], pose_by_profile: dict) -> tuple[bool, str]:
    """(inside every coupling, why not) for a pose keyed by profile joint names."""
    for c in couplings:
        a, b = pose_by_profile.get(c["depends_on"]), pose_by_profile.get(c["joint"])
        if a is None or b is None:
            continue
        rows = c["table"]
        xs = [r[0] for r in rows]
        k = int(np.clip(np.searchsorted(xs, a), 1, len(xs) - 1))
        r0, r1 = rows[k - 1], rows[k]
        if r0[1] is None or r1[1] is None:  # inside a span where no value of the joint clears
            return False, f"{c['joint']}: no allowed range at {c['depends_on']} {a:+.1f}"
        w = 0.0 if xs[k] == xs[k - 1] else min(1.0, max(0.0, (a - xs[k - 1]) / (xs[k] - xs[k - 1])))
        lo = r0[1] + (r1[1] - r0[1]) * w
        hi = r0[2] + (r1[2] - r0[2]) * w
        if not (lo - 1e-6 <= b <= hi + 1e-6):
            return False, f"{c['joint']} {b:+.1f} outside {lo:+.1f}..{hi:+.1f} at {c['depends_on']} {a:+.1f}"
    return True, ""


def bom_discrepancies(info, out: Path) -> Path:
    """Every fastener key whose BOM quantity (the guide's lists, per sub-assembly) differs from what
    the model places or lists as unplaced, per sub-assembly: out/bom_check.md."""
    keys = sorted(set(info["bom_by"]) | set(info["have_by"]))
    L = ["# BOM vs model, per part number", "",
         "BOM = the kit guide's parts lists (and hunter_head's BOM), per sub-assembly; model = fasteners placed + "
         "listed as unplaced in the steps, per sub-assembly. Only the keys that differ.", "",
         "| key | item | BOM total | model total | BOM by sub-assembly | model by sub-assembly |", "|---|---|---|---|---|---|"]
    n = 0
    for k in keys:
        b, h = info["bom_by"].get(k, {}), info["have_by"].get(k, {})
        if abs(sum(b.values()) - sum(h.values())) < 1e-6:
            continue
        n += 1
        fmt = lambda d: ", ".join(f"{a} {q:g}" for a, q in sorted(d.items())) or "-"
        L.append(f"| {k} | {info['bom_item'].get(k, '')} | {sum(b.values()):g} | {sum(h.values()):g} | {fmt(b)} | {fmt(h)} |")
    L.insert(3, f"{n} keys differ.")
    f = out / "bom_check.md"
    f.write_text("\n".join(L) + "\n")
    return f
