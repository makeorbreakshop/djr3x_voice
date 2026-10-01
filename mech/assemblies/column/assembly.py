"""The droid's central column internals (ours): the default `internals` variant of the droid.

    cd mech && .venv/bin/python -m workbench test column      # the column alone (fast)
    cd mech && .venv/bin/python -m workbench test kit         # the whole droid (column = default)

Design (parts/column/_layout.py has every number and its evidence):

* **Base.** The column's 1/4 in aluminium foot plate bolts to the Gil plate (Ferreira's Gil drive
  plate, 169.7 x 169.7 x 8, measured from Morton's file) with four M5 + lock nuts inside the plate's
  own eight holes. The electronics and the battery sit round it on Morton's skid plate (its 171.7 mm
  window takes the Gil plate) and in the base up to the shell ring.
* **Column.** Four 2020 posts on a 100 mm square, fixed (not rotating), from the foot plate to an
  aluminium top plate under the top cap. 100 mm is the largest square the kit's pedestal allows (its
  back grille pocket at r 59.7; >= 9.7 mm clear). Stiff and simple: four extrusions tied at both
  ends and by every bracket; T-nuts anywhere along the slots.
* **Ring drives.** Printed L-brackets on the back posts at the lower- and top-ring heights; each
  hangs a goBILDA 2000 spline-down, a 1906 hub and a pinion with Anderson's teeth exactly where
  Anderson's pinion was, so his sectors on the rings are unchanged. Shell support: a Morton/Hunter
  ring round the pedestal's foot, bolted to the posts, under the kit's base top.
* **Sled** (Jason Charlton's build, his replies 2026-10-01): an inner box round the neck - goBILDA-pattern
  grid plates front, back and sides on a 6061 bottom plate, a 6061 top plate carrying the neck's
  bearings - that glides up and down the four posts (V-slot 2020) on eight V-wheels in the posts'
  inner X slots, eccentric spacers for the preload. Both servos ride it, so a retractile service cable
  from the foot plate to its bottom plate carries both (and the clock spring's ribbon out).
* **Lift.** The sled climbs a fixed rack: a goBILDA 2000 behind a hanger on the sled's right side, its
  brass Mod 0.8 48T servo gear on a Mod 0.8 rack on the back-right post's inner face; 0.335 mm/deg,
  -37..+45 mm (the servo's +-135 deg is +-45.2 mm; down is limited by the head's mouth on the top cap,
  as today). Stiffer than a belt; no tension, no idler.
* **Pan.** Direct (Charlton: the rotation servo at the base of the inner assembly, a round coupler up
  into the neck): a goBILDA 2000 hung spline-up under the sled's bottom plate on the axis, a 1906 hub and
  a turned coupler clamping the neck tube's bottom. The neck runs up through two 6806-2RS in the top
  plate's housing (a bearing hub cross-bolted to the tube, its flange on the upper inner race, a collar
  under the lower one). 1:1, so the servo's +-150 is the mechanism's end; +-135 is the design range (the
  suite's servo margin); the clock spring's ribbon allows +-200. Past ~+120 relative to the top ring, with
  the head low and the hero arm raised, the head's right ear meets the arm: a coupled limit to add later.
  The short neck (26 x 1.5 aluminium) rises into Hunter's coupler, its top on the coupler's bore stop:
  the head sits where it does today.
* **Connected motion** (manifest `gears`): the lift pinion turns as the sled climbs (and the lift servo
  maps to head_lift), the ring pinions and their hubs turn with the kit's rings; the pan's hub and
  coupler ride the neck.

Fastening (workbench/fastening.py): screws into metal (post ends, tapped plates, hubs, MGN blocks,
standoffs, T-nuts) or through-bolts with lock nuts (servo flanges: the case is 4 mm from each hole,
too close for an insert's wall; the Gil plate; the neck's cross bolt). Prototype: the sled's plate joints and
the hanger's arms are listed as unplaced hardware (their part numbers are still to choose).
"""

from __future__ import annotations

import copy
import math
from pathlib import Path

import numpy as np
import trimesh

from parts.column import _layout as L
from parts.library import part as lib_part, spec_part
from workbench.geom import parametric_mesh
from workbench.mates import Mate, axis, plane, spline, unit
from workbench.model import Assembly, BomLine, Fastener, Joint, Link, Part, Step

ID = "column_internals"
MECH = Path(__file__).resolve().parents[2]
GOBILDA = "https://www.gobilda.com/"
UP = np.array([0.0, 1.0, 0.0])

# The suite's failures traced to a cause (TESTS.md), each with its fix; anything unmatched fails.
EXPLAINED = [
    {"test": "clearance", "parts": ["col_coil_cable", "col_*"],
     "cause": "the retractile service cable stretches with the lift (manifest `stretch`); the suite moves the "
              "sled into it as a rigid coil",
     "fix": "none: on the droid the coil compresses as the sled comes down"},
    {"test": "clearance", "parts": ["col_lift_pinion", "col_lift_rack"],
     "cause": "the lift's pinion and its rack: the manifest turns the pinion with head_lift (gear g_lift), the suite's "
              "sweep does not read `gears` yet, so their teeth pass through each other there; at rest they are phased "
              "to mesh",
     "fix": "none on the parts (the mesh is checked at rest and through the travel by the gear check)"},
    {"test": "clearance", "parts": ["col_wheel_*", "col_post_*"],
     "cause": "the V-wheels bear on the V-slot's flanks (line contact, drawn to touch)",
     "fix": "none: rolling contact"},
    {"test": "tool_access", "parts": ["col_scr_lpin_*", "col_scr_tpin_*", "p_m_*", "col_lower_*"],
     "cause": "the ring pinions' four M4 go in from below (the servo hangs spline-down above the pinion); in the "
              "droid the pedestal top (P_M_3) is 30 mm under the lower one and the lower drive under the top one",
     "fix": "build each ring drive (bracket, servo, hub, pinion) on the bench and bolt it to the column as a unit "
            "(its four M4 into the back posts' T-nuts have straight access)"},
]
TOLERANCES: dict = {}


# ------------------------------------------------------------------ helpers

def frame(xw, yw, o):
    """4x4: canonical X -> xw, Y -> yw, Z -> xw x yw, origin -> o."""
    xw, yw = unit(xw), unit(yw)
    m = np.eye(4)
    m[:3, 0], m[:3, 1], m[:3, 2], m[:3, 3] = xw, yw, np.cross(xw, yw), o
    return m


def moved(f, m):
    from workbench.mates import moved as mv

    return mv(f, m)


def placed(mesh, m):
    out = mesh.copy()
    out.apply_transform(m)
    return out


class Hw:
    """Fasteners, mates and features as they are solved (the pattern of hunter_head/hardware.py,
    any metric thread)."""

    def __init__(self, parts: dict):
        self.p = parts
        self.fast: list[Fastener] = []
        self.mates: list[Mate] = []
        self.n = 0

    def feat(self, pid):
        if pid in self.p:
            return self.p[pid].features
        return next(f for f in self.fast if f.id == pid).features

    def mate(self, type_, a, b, solved=True, note="", **params):
        self.n += 1
        self.mates.append(Mate(f"c{self.n:03d}_{type_}", type_, a, b, params, solved, note))

    def screw(self, fid, head_at, d, clamps, into, step, link, kind="shcs", thread="M4", length=None, note="",
              inferred=False):
        """A screw whose head bears at `head_at`, along `d` through `clamps` [(part, hole name)] into
        `into` = (part or fastener id, feature, 'metal'|'insert'|'nut', thread available). Length: the
        smallest standard one that engages the rule's minimum (a nut: all of its thread)."""
        from assemblies.hunter_head.hardware import STD_LEN

        d = unit(d)
        head_at = np.asarray(head_at, float)
        tpid, tfeat, kind_into, avail = into
        tf = self.feat(tpid)[tfeat]
        grip = float((np.asarray(tf["p"]) - head_at) @ d)
        dn = float(thread[1:])
        need = {"insert": 1.5, "metal": 1.0, "nut": 1.05}[kind_into] * dn
        if kind_into == "nut":
            need = min(need, avail)
        if length is None:
            length = next((x for x in STD_LEN if x >= grip + need - 0.05 and (kind_into == "nut" or x <= grip + avail + 0.05)),
                          None)
            if length is None:
                raise ValueError(f"{fid}: no standard length for grip {grip:.2f} + {need:.2f} (<= {avail})")
        spec = {"type": kind, "thread": thread, "length_mm": length}
        r = spec_part("screw", spec)
        feats = {"shank": axis(head_at, d, dn / 2), "head": plane(head_at, d)}
        f = Fastener(fid, spec, f"{kind}-{thread}x{length:g}", [c[0] for c in clamps] + [tpid], link, step,
                     _on_axis(head_at, d), inferred, note, mesh=r.mesh, cad=r.status, features=feats)
        self.fast.append(f)
        for pid, hn in clamps:
            self.mate("concentric", (fid, "shank"), (pid, f"hole_{hn}"))
        if clamps:
            self.mate("seated", (fid, "head"), (clamps[0][0], f"face_{clamps[0][1]}"))
        engage = min(length - grip, avail)
        self.mate("threaded", (fid, "shank"), (tpid, tfeat), engage_mm=round(engage, 2), into=kind_into,
                  hole_depth_mm=avail)
        return f

    def nut(self, fid, at, d, step, link, thread="M4", lock=True, reason="clamp", joins=()):
        """A nut whose bearing face is at `at`, its thread along d (the screw's direction)."""
        dn = float(thread[1:])
        spec = {"type": "lock_nut" if lock else "nut", "thread": thread, "reason": reason,
                "thickness_mm": round((1.25 if lock else 0.8) * dn, 2),
                "standard": "ISO 10511" if lock else "ISO 4032"}
        r = spec_part("nut", {"type": spec["type"], "thread": thread})
        feats = {"thread": axis(at, d, dn / 2), "face": plane(at, -unit(d))}
        self.fast.append(Fastener(fid, spec, f"{spec['type']}-{thread}", list(joins), link, step, _on_axis(at, d),
                                  mesh=r.mesh, cad=r.status, features=feats))
        return fid, spec["thickness_mm"]

    def tnut(self, fid, m, thread, step, post):
        """A drop-in T-nut, placed by `m` (its slot frame -> body), seated under the post's lips."""
        mesh, fe, _ = parametric_mesh("parts.column.purchased", {"kind": "tnut", "thread": thread})
        feats = {k: moved(v, m) for k, v in fe.items()}
        spec = {"type": "t_nut", "thread": thread, "thickness_mm": 5.0, "reason": "captive",
                "standard": "drop-in T-nut, 2020 (5 series)"}
        self.fast.append(Fastener(fid, spec, f"t_nut-{thread}", [post], "column", step, m, mesh=placed(mesh, np.eye(4)),
                                  cad="parametric", features=feats, catalog=None))
        lip = plane(m[:3, 3] + m[:3, 2] * 8.2, -m[:3, 2])
        self.p[post].features[f"lip_{fid}"] = lip
        self.mate("seated", (fid, "seat"), (post, f"lip_{fid}"), note="T-nut drawn up under the slot's lips")
        return fid

    def insert(self, fid, pid, hole, step, link, spec):
        from parts.library import spec_part as sp

        h = self.p[pid].features[f"hole_{hole}"]
        dd = unit(h["d"])
        r = sp("insert", spec)
        depth = h.get("depth")
        feats = {"outer": axis(h["p"], dd, spec["od_mm"] / 2), "top": plane(h["p"], -dd),
                 "thread": dict(axis(h["p"], dd, float(spec["thread"][1:]) / 2), depth=depth)}
        self.fast.append(Fastener(fid, dict(spec), f"insert-{spec['thread']}x{spec['length_mm']:g}", [pid], link, step,
                                  _on_axis(h["p"], dd), mesh=r.mesh, cad=r.status, features=feats))
        self.mate("press", (fid, "outer"), (pid, f"hole_{hole}"), note="heat-set into the printed hole", gap_mm=0.0)
        self.mate("coplanar", (fid, "top"), (pid, f"face_{hole}"))
        return fid


def _on_axis(p, d):
    from workbench.mates import frame_on_axis

    return frame_on_axis(np.asarray(p, float), np.asarray(d, float))


def _near(feats: dict, prefix: str, p, tol=0.6):
    """The name (without `hole_`) of the hole feature of `feats` nearest p (in the plane square to its axis)."""
    best = None
    for k, f in feats.items():
        if not k.startswith(f"hole_{prefix}"):
            continue
        q, d = np.asarray(f["p"]), unit(f["d"])
        w = np.asarray(p, float) - q
        off = float(np.linalg.norm(w - d * (w @ d)))
        if best is None or off < best[0]:
            best = (off, k[5:])
    if best is None or best[0] > tol:
        raise ValueError(f"no hole {prefix}* within {tol} mm of {np.round(p, 2).tolist()} (nearest {best})")
    return best[1]


def phase(mod, params, other_mesh, teeth, key):
    """The tooth phase (deg, one pitch in 0.5 deg steps) at which a gear shares the least volume with
    its mate at rest (the placements fix the centres, not the tooth phase): the gear's mesh at turn 0
    turned about its `axis` feature, so only the final gear is built. Cached on the two meshes."""
    import manifold3d as mf

    from workbench.collide import mesh_hash
    from workbench.geom import _cache_key, cached

    def man(m):
        return mf.Manifold(mf.Mesh(vert_properties=np.asarray(m.vertices, np.float32), tri_verts=np.asarray(m.faces, np.uint32)))

    m0, feats, _ = parametric_mesh(mod, {**params, "turn": 0.0})
    ax = feats["axis"]

    def run():
        o = man(other_mesh)
        best = None
        for t in np.arange(0.0, 360.0 / teeth, 0.5):
            m = m0.copy()
            m.apply_transform(trimesh.transformations.rotation_matrix(np.radians(t), ax["d"], ax["p"]))
            v = (man(m) ^ o).volume()
            if best is None or v < best[1]:
                best = (float(t), float(v))
        return best

    return cached(_cache_key("col-phase", key, mesh_hash(m0), mesh_hash(other_mesh), teeth, 3), run)


# ------------------------------------------------------------------ build

def build_column(mount_link: str = "base", variant: dict | None = None, in_droid: bool = False) -> Assembly:
    asm = Assembly(
        id=ID, name="Central column internals (default)",
        description="Fixed V-slot 2020 column on the Gil plate; a sled on eight V-wheels in the posts' inner slots "
                    "(Charlton's) climbs a fixed rack with its own servo; the pan servo hangs under the sled and turns "
                    "the neck directly through a coupler (1:1, +-135 deg design, +-150 the servo's end); the neck in two "
                    "6806 in the sled's top plate; ring drives on brackets on the column; shell support ring round "
                    "the pedestal's foot.",
        frame_note="Body frame (show/SPEC.md): mm, +Y up, +Z front, +X droid's left; rest pose. Every part is "
                   "modelled in it (parts/column/_layout.py).",
        mount={"parent_link": mount_link, "transform": {"t": [0, 0, 0]}, "inferred": False,
               "rests_on": ["b_b_1", "b_b_2"],
               "rests_on_note": "the kit's skirt (B_B) and the Gil plate both stand on Morton's skid plate / the Gil "
                                "drive (not modelled)",
               **({"variant": variant} if variant else {})},
    )
    asm.links = [Link("column", "Column (fixed: posts, plates, ring drives, the lift's rack)", None),
                 Link("sled", "Lift sled (V-wheels on the posts; lift and pan servos, neck bearings)", "head_lift"),
                 Link("neck", "Neck: coupler, bearing hub, tube (the head rides it)", "head_pan")]
    P: dict[str, Part] = {}

    def add(pid, name, cls, link, mesh, src, material, printed, feats=None, mass=None, mass_note="", cad="parametric",
            catalog=None, inferred_note="", note="", explode=(0, 1, 0), stretch=None):
        p = Part(pid, name, cls, link, mesh, src, material, printed, tuple(explode), 40.0, mass, mass_note,
                 inferred=bool(inferred_note), inferred_note=inferred_note, note=note, features=dict(feats or {}),
                 cad=cad, catalog=catalog, stretch=stretch)
        P[pid] = p
        return p

    def ours(pid, name, cls, link, module, params=None, material="PETG (printed)", printed=True, **kw):
        mesh, feats, prm = parametric_mesh(module, params or {})
        src = {"kind": "parametric", "model": module, "params": {k: v for k, v in prm.items() if not isinstance(v, dict)},
               "placement": "body frame (parts/column/_layout.py)"}
        return add(pid, name, cls, link, mesh, src, material, printed, copy.deepcopy(feats), **kw)

    ALU = "aluminium (6061-T6)"
    # ---------------------------------------------------------- base and column
    ours("col_gil_plate", "Gil base plate (Ferreira's, measured)", "hardware", "column", "parts.column.gil_plate",
         material=ALU, printed=False, explode=(0, -1, 0),
         inferred_note="Outline, thickness and holes measured from Morton's Gil STL; its height is inferred: top at "
                       "y -48, in the 171.7 mm window of his skid plate (the kit frame), where his 2020 posts stand",
         note="The file's electronics-stack uprights (on the plate's edges) give way to the column; the electronics "
              "and battery go on Morton's skid plate round it (r ~86-165, y -48) and in the base up to the shell ring.")
    ours("col_foot_plate", "Column foot plate, 1/4 in 6061", "hardware", "column", "parts.column.foot_plate",
         material=ALU, printed=False, explode=(0, -1, 0))
    posts = []
    for i, (sx, sz) in enumerate([(-1, -1), (1, -1), (-1, 1), (1, 1)]):
        x, z = sx * L.POST_C, sz * L.POST_C
        r = lib_part("openbuilds", "V-Slot-2020", {"length_mm": L.POST_LEN, "profile": "vslot2020"}, kind="extrusion")
        m = placed(r.mesh, frame((1, 0, 0), (0, 1, 0), (x, L.Y_FOOT_TOP, z)))
        f = {"bore_bot": dict(axis((x, L.Y_FOOT_TOP, z), UP, 2.1), depth=L.POST_THREAD),
             "bore_top": dict(axis((x, L.Y_POST_TOP, z), -UP, 2.1), depth=L.POST_THREAD),
             "bottom": plane((x, L.Y_FOOT_TOP, z), -UP), "top": plane((x, L.Y_POST_TOP, z), UP),
             "face_front": plane((x, 300.0, z + 10 * sz), (0, 0, sz)),
             "face_in_z": plane((x, 300.0, z - 10 * sz), (0, 0, -sz))}
        pid = f"col_post_{'bf'[sz > 0]}{'rl'[sx > 0]}"
        posts.append(pid)
        add(pid, f"V-slot 2020 extrusion {L.POST_LEN:.1f} mm (column post, {'front' if sz > 0 else 'back'} "
                 f"{'left' if sx > 0 else 'right'})", "hardware", "column", m,
            {"kind": "parametric", "model": "parts/models.py:extrusion(vslot2020)", "placement": "body frame"},
            "aluminium (6063)", False, f, mass=0.5 * L.POST_LEN, mass_note="0.50 g/mm",
            note="OpenBuilds V-slot 2020: the sled's V-wheels run in its inner (axis-facing X) slot. Both ends tapped "
                 "M5 in the 4.2 mm centre bore.",
            inferred_note="V-slot mouth drawn to the OpenBuilds profile's proportions (not from its drawing)")
    ours("col_top_plate", "Column top plate, 6 mm 6061", "hardware", "column", "parts.column.top_plate", material=ALU,
         printed=False)
    ours("col_base_ring", "Shell support ring (pedestal foot)", "mech", "column", "parts.column.base_ring")
    # ---------------------------------------------------------- the sled: four plates, eight V-wheels, top and bottom
    ALU_GRID = "aluminium (goBILDA-pattern grid plate)"
    grid_note = ("goBILDA-pattern grid plate (4 mm holes on an 8 mm grid) cut to size; thickness (3 mm) and the part "
                 "number (or a cut-down goBILDA plate) to choose")
    for k in ("front", "back", "side_l", "side_r"):
        ours(f"col_sled_{k}", f"Sled {k.replace('_', ' ')} plate", "hardware", "sled", "parts.column.sled", {"kind": k},
             material=ALU_GRID, printed=False, inferred_note=grid_note)
    ours("col_sled_bottom", "Sled bottom plate (the pan servo hangs under it)", "hardware", "sled", "parts.column.sled",
         {"kind": "bottom"}, material=ALU, printed=False, explode=(0, -1, 0))
    ours("col_sled_top", "Sled top plate + bearing housing (two 6806-2RS)", "hardware", "sled", "parts.column.sled",
         {"kind": "top"}, material=ALU, printed=False)
    ours("col_lift_hanger", "Lift servo hanger (behind the servo's flange)", "mech", "sled", "parts.column.sled",
         {"kind": "hanger"}, explode=(-1, 0, 0))
    wmesh, wfe, _ = parametric_mesh("parts.column.purchased", {"kind": "vwheel"})
    emesh, efe, _ = parametric_mesh("parts.column.purchased", {"kind": "ecc"})
    wheels = []
    for sz, fb in ((1, "f"), (-1, "b")):
        for j, yw in enumerate(L.WHEEL_Y):
            for sx, lr in ((1, "l"), (-1, "r")):
                x = sx * L.WHEEL_X
                mw = frame((1, 0, 0), (0, 1, 0), (x, yw, sz * L.POST_C))
                wid = f"col_wheel_{fb}{lr}{'lu'[j]}"
                add(wid, f"V-wheel (OpenBuilds solid), {'front' if sz > 0 else 'back'} {'left' if sx > 0 else 'right'} "
                         f"{'lower' if j == 0 else 'upper'}", "bearing", "sled", placed(wmesh, mw),
                    {"kind": "parametric", "model": "parts/column/purchased.py:vwheel", "placement": "in the post's inner slot"},
                    "polycarbonate + 625-2RS", False, {k_: moved(v, mw) for k_, v in wfe.items()}, mass=8.0,
                    mass_note="catalogue (typical)", explode=(sx, 0, 0),
                    inferred_note="the V's depth in the slot (1.5 mm) and the V-slot's mouth are drawn to fit; the "
                                  "OpenBuilds wheel's 24.39 x 10.23 is its datasheet's")
                me = frame((1, 0, 0), (0, sz, 0), (x, yw, sz * L.SLED["z_out"]))
                add(f"col_ecc_{fb}{lr}{'lu'[j]}", "Eccentric spacer 6 mm (V-wheel preload)", "hardware", "sled",
                    placed(emesh, me), {"kind": "parametric", "model": "parts/column/purchased.py:ecc", "placement": "mates"},
                    "aluminium", False, {k_: moved(v, me) for k_, v in efe.items()}, mass=1.0, mass_note="catalogue")
                wheels.append((wid, fb, lr, j, x, yw, sz))
    # ---------------------------------------------------------- the lift's rack (on the back-right post)
    ours("col_lift_rack", f"Lift rack, Mod 0.8, {L.RACK['y1'] - L.RACK['y0']:.0f} mm (aluminium, to spec)", "hardware", "column",
         "parts.column.lift_rack", material="aluminium (6061)", printed=False,
         inferred_note="goBILDA-style Mod 0.8 aluminium gear rack, modelled to spec; part number to confirm")
    servo = lib_part("gobilda", "2000-0025-0002")
    hub = lib_part("gobilda", "1906-0025-0032")

    def servo_at(pid, label, link, xw, yw, o, back_entry, with_hub=True, hub_link=None):
        m = frame(xw, yw, o)
        xw_, yw_, zw_ = m[:3, 0], m[:3, 1], m[:3, 2]
        f = {"spline": spline(o, yw_, 25), "boss": plane(np.asarray(o) - yw_ * L.SPLINE_ABOVE_BOSS, yw_)}
        for i, (cl, cw) in enumerate(L.FLANGE_HOLES):
            ps = np.asarray(o) + xw_ * cl + zw_ * cw + yw_ * L.FLANGE_Y[1]
            pb = np.asarray(o) + xw_ * cl + zw_ * cw + yw_ * L.FLANGE_Y[0]
            if back_entry:
                f[f"hole_fl{i + 1}"], f[f"face_fl{i + 1}"] = axis(pb, yw_, 2.2), plane(pb, -yw_)
            else:
                f[f"hole_fl{i + 1}"], f[f"face_fl{i + 1}"] = axis(ps, -yw_, 2.2), plane(ps, yw_)
            f[f"nutface_fl{i + 1}"] = ps if back_entry else pb
        f["flange_back"] = plane(np.asarray(o) + yw_ * L.FLANGE_Y[0], -yw_)
        f["flange_front"] = plane(np.asarray(o) + yw_ * L.FLANGE_Y[1], yw_)
        nf = {k: v for k, v in f.items() if not k.startswith("nutface")}
        p = add(pid, f"Servo goBILDA 2000-0025-0002 ({label})", "servo", link, placed(servo.mesh, m),
                {"kind": "step", "file": "vendor/parts_cad/gobilda/2000-0025-0002.step", "placement": "mates"}, "servo",
                False, nf, mass=70.0, mass_note="catalogue ~70 g", cad=servo.status, catalog="gobilda:2000-0025-0002",
                note="25.2 kg-cm stall at 6 V, 300 deg (standard mode); 25T spline.")
        p._nutfaces = {k: v for k, v in f.items() if k.startswith("nutface")}
        p.features["out_thr"] = dict(axis(o, -yw_, 1.25), depth=5.0)   # the output's M3 centre-screw thread (inferred depth)
        if not with_hub:
            return p, None
        # its 1906 hub: servo face on the boss, the same clocking as the servo (it turns with the output)
        boss = np.asarray(o) - yw_ * L.SPLINE_ABOVE_BOSS
        mh = frame(xw_, yw_, boss)
        hf = {"spline": spline(boss, yw_, 25), "servo_face": plane(boss, -yw_), "top": plane(boss + yw_ * L.HUB_H, yw_),
              "axis": axis(boss, yw_, 3.0)}
        for i, (dx, dz) in enumerate([(L.HUB_TAP_R, 0), (0, L.HUB_TAP_R), (-L.HUB_TAP_R, 0), (0, -L.HUB_TAP_R)]):
            q = boss + yw_ * L.HUB_H + xw_ * dx + zw_ * dz
            hf[f"tap{i + 1}"] = dict(axis(q, -yw_, 1.78), depth=L.HUB_THREAD)
        hp = add(pid.replace("servo", "hub"), f"Servo hub goBILDA 1906, 25T ({label})", "hardware", hub_link or link,
                 placed(hub.mesh, mh), {"kind": "step", "file": "vendor/parts_cad/gobilda/1906-0025-0032.step",
                                        "placement": "mates"}, "aluminium", False, hf, mass=9.0, mass_note="catalogue",
                 cad=hub.status, catalog="gobilda:1906-0025-0032")
        return p, hp

    # ---------------------------------------------------------- ring drives
    ring = {}
    for key in ("lower", "top"):
        R = L.RING[key]
        ours(f"col_{key}_bracket", f"Ring drive bracket ({key})", "mech", "column", "parts.column.drive_bracket",
             {"ring": key})
        cx, cz = L.RING_PINION_C
        sv, hb = servo_at(f"col_{key}_servo", f"{key} ring", "column", (1, 0, 0), (0, -1, 0), (cx, R["spline"], cz),
                          back_entry=True)
        ring[key] = (sv, hb)
    # ---------------------------------------------------------- pan: direct drive under the sled, the neck in its bearings
    # the pan servo hangs under the bottom plate, spline up on the axis, long side +X; its hub turns with the neck
    pan_servo, pan_hub = servo_at("col_pan_servo", "pan", "sled", (1, 0, 0), (0, 1, 0), L.PAN_SERVO_SPLINE,
                                  back_entry=True, hub_link="neck")
    ours("col_pan_coupler", "Pan coupler: 1906 hub to the neck tube (turned 6061)", "hardware", "neck",
         "parts.column.pan_coupler", material=ALU, printed=False,
         note="Charlton's round aluminium coupler: the servo turns the neck directly, 1:1.")
    # the lift servo rides the sled behind its hanger (Charlton's): long side down, spline +X into its pinion
    lift_servo, _ = servo_at("col_lift_servo", "lift", "sled", (0, -1, 0), (1, 0, 0), L.LIFT_SPLINE,
                             back_entry=False, with_hub=False)
    rk, _, _ = parametric_mesh("parts.column.lift_rack", {})
    t_lift, _ = phase("parts.column.lift_pinion", {}, rk, L.LIFT_PINION["teeth"], "lift")
    ours("col_lift_pinion", f"Lift pinion: brass servo gear, Mod 0.8, {L.LIFT_PINION['teeth']}T, 25T spline", "hardware",
         "sled", "parts.column.lift_pinion", {"turn": t_lift}, material="brass", printed=False,
         inferred_note="goBILDA brass servo gear (Mod 0.8, 25T spline) modelled to spec; the 48T size and the part "
                       "number to confirm against goBILDA's range",
         note=f"Phased {t_lift:g} deg to mesh the rack at rest; {L.LIFT_MM_PER_DEG:.4f} mm of lift per servo degree.")
    brg = spec_part("bearing", {"type": "deep_groove", "id_mm": L.BRG["id"], "od_mm": L.BRG["od"], "width_mm": L.BRG["w"]})
    for s, (y0, y1) in (("lo", L.Y_BRG_LO), ("up", L.Y_BRG_UP)):
        m = frame((1, 0, 0), (0, 0, -1), (0, (y0 + y1) / 2, 0))   # canonical Z (axis) -> body +Y
        add(f"col_brg_{s}", f"Ball bearing 6806-2RS 30 x 42 x 7 ({'lower' if s == 'lo' else 'upper'})", "bearing",
            "sled", placed(brg.mesh, m), {"kind": "parametric", "model": "parts/models.py:bearing", "placement": "mates"},
            "steel", False, {"outer": axis((0, y0, 0), UP, L.BRG["od"] / 2), "bore": axis((0, y0, 0), UP, L.BRG["id"] / 2),
                             "bottom": plane((0, y0, 0), -UP), "top": plane((0, y1, 0), UP)},
            mass=24.0, mass_note="catalogue (typical 6806-2RS)")
    ours("col_neck_hub", "Neck bearing hub (turned 6061)", "hardware", "neck", "parts.column.neck_hub", material=ALU,
         printed=False)
    cmesh, cfe, _ = parametric_mesh("parts.column.purchased", {"kind": "collar"})
    mc = frame((1, 0, 0), (0, 1, 0), (0, L.COLLAR["y0"], 0))
    add("col_collar", "Clamp collar, 30 mm bore, 45 x 13 (one-piece, its own M4 clamp screw)", "hardware", "neck",
        placed(cmesh, mc), {"kind": "parametric", "model": "parts/column/purchased.py:collar", "placement": "mates"},
        "aluminium", False, {k: moved(v, mc) for k, v in cfe.items()}, mass=31.0, mass_note="aluminium, by volume")
    for key in ("lower", "top"):
        sec = _anderson_sector(key)
        t, _ = phase("parts.column.ring_pinion", {"ring": key}, sec, L.RING[key]["teeth"], f"ring-{key}")
        ours(f"col_{key}_pinion", f"Ring pinion ({key}, {L.RING[key]['teeth']}T, Anderson's teeth)", "mech", "column",
             "parts.column.ring_pinion", {"ring": key, "turn": t},
             note=f"Phased {t:g} deg to mesh Anderson's {key} sector at rest.")
    ours("col_neck_tube", f"Neck tube 26 x 1.5 6061, {L.TUBE['top'] - L.TUBE['y0']:.1f} mm", "hardware", "neck",
         "parts.column.neck_tube", material=ALU, printed=False, mass=round((L.TUBE["top"] - L.TUBE["y0"]) * 0.312, 1),
         mass_note="0.312 g/mm")
    so = lib_part("mcmaster", "92871A317")
    for i, a in enumerate(L.STANDOFF["angles"]):
        from parts.column._cad import at_angle

        x, z = at_angle(L.STANDOFF["r"], a)
        y0 = L.HOUSING["y1"]
        m = frame((1, 0, 0), (0, 0, -1), (x, y0, z))   # canonical +Z (its length) -> body +Y
        add(f"col_standoff_{i + 1}", "Standoff, round 6 mm, M4 female, 24 mm (McMaster 92871A317)", "hardware",
            "sled", placed(so.mesh, m), {"kind": "parametric", "model": "parts/models.py:standoff", "placement": "mates"},
            "aluminium", False,
            {"thr_bot": dict(axis((x, y0, z), UP, 1.6), depth=L.STANDOFF_THREAD),
             "thr_top": dict(axis((x, y0 + L.STANDOFF["length"], z), -UP, 1.6), depth=L.STANDOFF_THREAD),
             "bottom": plane((x, y0, z), -UP), "top": plane((x, y0 + L.STANDOFF["length"], z), UP)},
            mass=1.5, mass_note="catalogue", cad=so.status, catalog="mcmaster:92871A317",
            inferred_note="female thread depth taken as 8 mm")
    from parts.column.clockspring_case import ribbon_turns

    ours("col_clockspring", "Clock-spring cassette (head cables across the pan)", "mech", "sled",
         "parts.column.clockspring_case",
         note=f"Ribbon 250 mm between r 15 and r 26: {ribbon_turns(250.0):.2f} turns of travel "
              f"(+-{ribbon_turns(250.0) * 180:.0f} deg), more than the servo's +-{L.PAN_MECH:g}.")
    # the service cable and the cosmetic neck spring: they stretch with the lift (manifest `stretch`)
    h = L.BOTTOM["y0"] - L.Y_FOOT_TOP - 2 * L.COIL["wire_r"]
    from assemblies.r3x_animation.assembly import coil_generator

    coil = coil_generator(r=L.COIL["r"], wire_r=L.COIL["wire_r"], h=h, turns=h / 9.0, n=int(h / 9.0 * 16), sides=8)()
    y_c0 = L.Y_FOOT_TOP + L.COIL["wire_r"]
    coil.apply_translation([L.COIL["x"], y_c0, L.COIL["z"]])
    add("col_coil_cable", f"Retractile service cable, 12-core, {h:.0f} mm coil at rest", "hardware", "column", coil,
        {"kind": "generated", "placement": "from the foot plate's cable hole to the sled's bottom-plate tab"},
        "rubber (PU jacket)", False,
        {"bottom": plane((L.COIL["x"], L.Y_FOOT_TOP, L.COIL["z"]), -UP),
         "top": plane((L.COIL["x"], L.BOTTOM["y0"], L.COIL["z"]), UP)},
        mass=100.0, mass_note="inferred (12-core coiled cord)",
        stretch={"joint": "head_lift", "axis": [0, 1, 0], "anchor": [L.COIL["x"], y_c0, L.COIL["z"]], "rest_mm": h},
        inferred_note="Cable route inferred: the pan and lift servos' and the head's leads (via the clock spring) up "
                      "the column's front opening to the sled, coiled so it follows the lift's 82 mm",
        note="It stretches with head_lift (manifest stretch); zip-tied at both ends.")
    from assemblies.r3x_animation.assembly import SPRING_H, SPRING_Y

    sp = coil_generator(h=SPRING_H)()
    sp.apply_translation([0, SPRING_Y, 0])
    add("neck_spring", "Neck coil spring (cosmetic, the sim's)", "shell", "column", sp,
        {"kind": "generated", "placement": "fitted",
         "evidence": "sim/web/src/rig.ts buildNeckSpring: r 21, wire 3.2, 5.5 turns, 55 mm on the top cap at y 608.5"},
        "steel (painted, cosmetic)", False, {"seat": plane((0, SPRING_Y, 0), -UP)}, mass=40.0, mass_note="as the sim's",
        stretch={"joint": "head_lift", "axis": [0, 1, 0], "anchor": [0, SPRING_Y, 0], "rest_mm": SPRING_H},
        note="On the top cap round the neck; stretches with head_lift.")

    asm.parts = list(P.values())
    hw = Hw(P)
    _hardware(hw, P, posts, lift_servo, pan_servo, pan_hub, ring)
    asm.fasteners, asm.mates = hw.fast, hw.mates
    asm.joints = _joints()
    _gears(asm)
    _steps(asm)
    _bom(asm)
    if in_droid:  # the head's set screw is placed by the droid's interface (workbench/droid.py)
        asm.bom.append(BomLine("set_screw-M4x6", "M4 x 6 cup-point set screw (Hunter's coupler onto the neck tube)", 1,
                               "fastener", {"type": "set_screw", "thread": "M4", "length_mm": 6}))
    asm.notes = _notes()
    asm.children.append(_head_ref())
    return asm


def _anderson_sector(key):
    """Anderson's ring sector as the kit droid places it (assemblies/r3x_animation: top_drive / lower_drive)."""
    from r3xmech import frames
    from r3xmech.frames import ZUP, trans
    from workbench.geom import parametric_mesh as pm

    if key == "lower":
        m, _, _ = pm("parts.anderson.ring_gear", {})
        T = trans(0, 342.0 + 3, 0) @ ZUP @ frames.rot((0, 0, 1), 6.7)
    else:
        from parts.anderson import ring_gear

        m, _, _ = pm("parts.anderson.ring_gear", dict(ring_gear.TOP))
        T = trans(0, 462.0, 0) @ ZUP @ frames.rot((0, 0, 1), 51.7)
    return placed(m, T)


def _joints():
    lift_k = 1.0 / L.LIFT_MM_PER_DEG * LIFT_SIGN
    return [
        Joint("head_lift", "Head lift (the sled climbs a fixed rack with its own servo)", "prismatic", "column",
              "sled", (0.0, 0.0, 0.0), (0.0, 1.0, 0.0), L.LIFT, "mm", "head_lift", None,
              {"kind": "gear", "servos": ["col_lift_servo"], "mm_per_servo_deg": round(L.LIFT_MM_PER_DEG, 4),
               "servo_deg_per_unit": round(lift_k, 4), "gears": [f"{ID}/g_lift"],
               "pinion_pitch_radius_mm": round(L.R_LIFT, 3),
               "note": f"goBILDA 2000 behind the sled's hanger, brass Mod 0.8 {L.LIFT_PINION['teeth']}T servo gear (pitch r "
                       f"{L.R_LIFT:.1f}) climbing a Mod 0.8 rack on the back-right post (Charlton's lift); the sled rides "
                       f"eight V-wheels in the posts' inner slots. {L.LIFT_MM_PER_DEG:.4f} mm per servo deg: +-135 deg = "
                       f"+-{135 * L.LIFT_MM_PER_DEG:.1f} mm."},
              {"how": "servo at its centre pulse (1500 us) with the head at today's height (tube top on the coupler's "
                      "bore stop at y 696.3); fit the brass gear on the rack there", "step": "s13"}),
        Joint("head_pan", "Head pan (direct drive: the pan servo under the sled turns the neck through a coupler)",
              "revolute", "sled", "neck", (0.0, 0.0, 0.0), (0.0, 1.0, 0.0), L.PAN, "deg", "head_pan", None,
              {"kind": "direct", "servos": ["col_pan_servo"], "gear_ratio": 1.0, "servo_deg_per_joint_deg": 1.0,
               "servo_deg_per_unit": 1.0,
               "mechanical_range_deg": [-L.PAN_MECH, L.PAN_MECH],
               "note": f"goBILDA 2000 hung spline-up under the sled's bottom plate on the axis; its 1906 hub and a turned "
                       f"coupler clamp the neck tube's bottom (Charlton's). 1:1: the servo's own travel is the pan's, "
                       f"+-{L.PAN_MECH:g} (standard mode, 300 deg); the design range +-{L.PAN[1]:g} keeps the suite's "
                       f"servo margin. The head's cables cross in the clock-spring cassette (+-200 deg of ribbon). Past "
                       f"~+120 relative to the top ring the low head's right ear meets the raised hero arm: a coupled "
                       f"limit to add (not a clamp)."},
              {"how": "servo at its centre pulse with the head facing the front; fit the coupler on the hub there",
               "step": "s13"}),
    ]


# The lift pinion's turn per mm of lift: the rack is fixed, so the pinion rolls up it; the sign is the
# geometry's (the pinion's teeth stay in the rack's through the travel: checked by the overlap sweep,
# parts/column/tests/test_column_gears.py), with the pinion's axis +X.
LIFT_SIGN = -1.0
# The ring pinions turn with their internal sectors in the same sense (an internal gear), Z_sector / Z_pinion.
RING_RATIO = {"lower": 95.0 / 25.0, "top": 85.0 / 20.0}


def _gears(asm: Assembly):
    """Every part a joint turns through a ratio (SCHEMA.md "Gear"): the lift's pinion as the sled climbs,
    the ring pinions (and their servos' hubs) as the kit's rings turn. The pan is direct (its hub and
    coupler ride the neck link)."""
    from workbench.model import Gear

    fast = {f.id for f in asm.fasteners}
    k = LIFT_SIGN / L.LIFT_MM_PER_DEG
    out = [Gear("g_lift", "rack_pinion", "head_lift", "sled", tuple(L.LIFT_PINION_C), (1.0, 0.0, 0.0), k,
                parts=["col_lift_pinion"], fasteners=[f for f in ("col_scr_liftgear",) if f in fast],
                servo="col_lift_servo", servo_deg_per_unit=k, mesh_with="col_lift_rack",
                note=f"brass 48T on the fixed rack: {abs(k):.3f} deg of pinion (and servo) per mm of lift")]
    cx, cz = L.RING_PINION_C
    for key, jid, asm_id in (("lower", "torso_lower", "lower_ring"), ("top", "torso_top", "top_ring")):
        r = RING_RATIO[key]
        out.append(Gear(f"g_{key}_ring", "internal", jid, "column", (cx, L.RING[key]["y0"], cz), (0.0, 1.0, 0.0), r,
                        parts=[f"col_{key}_pinion", f"col_{key}_hub"],
                        fasteners=sorted(f for f in fast if f.startswith(f"col_scr_{'lpin' if key == 'lower' else 'tpin'}_")),
                        joint_assembly=asm_id, servo=f"col_{key}_servo", servo_deg_per_unit=r,
                        mesh_with=f"{key}_sector",
                        note=f"the {key} ring's internal sector turns its pinion the same way, {r:.2f}:1"))
    asm.gears = out


# ------------------------------------------------------------------ the hardware, from the parts' features

def _hardware(hw: Hw, P, posts, lift_servo, pan_servo, pan_hub, ring):
    F = lambda pid: P[pid].features  # noqa: E731
    # -- posts on the foot plate (M5 flat heads from below into the posts' tapped ends) and the top plate
    for i, pid in enumerate(posts):
        b = F(pid)["bore_bot"]
        hn = _near(F("col_foot_plate"), "post", b["p"])
        head = F("col_foot_plate")[f"hole_{hn}"]["p"]
        hw.screw(f"col_scr_post_bot_{i + 1}", head, UP, [("col_foot_plate", hn)], (pid, "bore_bot", "metal", L.POST_THREAD),
                 "s01", "column", kind="fhcs", thread="M5")
        hw.mate("seated", (pid, "bottom"), ("col_foot_plate", "top"))
        t = F(pid)["bore_top"]
        hn = _near(F("col_top_plate"), "post", t["p"])
        hw.screw(f"col_scr_post_top_{i + 1}", F("col_top_plate")[f"hole_{hn}"]["p"], -UP, [("col_top_plate", hn)],
                 (pid, "bore_top", "metal", L.POST_THREAD), "s18", "column", thread="M5")
        hw.mate("seated", ("col_top_plate", "bottom"), (pid, "top"))
    # -- foot plate on the Gil plate: M5 through both, lock nuts under
    hw.mate("seated", ("col_foot_plate", "bottom"), ("col_gil_plate", "top"))
    for i in range(4):
        h = F("col_foot_plate")[f"hole_gil{i + 1}"]
        gh = _near(F("col_gil_plate"), "foot", h["p"])
        far = np.asarray(h["p"]) - UP * (L.FOOT["t"] + L.GIL["t"])
        nid, nt = hw.nut(f"col_nut_gil_{i + 1}", far, -UP, "s03", "column", thread="M5", reason="clamp",
                         joins=["col_gil_plate"])
        hw.screw(f"col_scr_gil_{i + 1}", h["p"], -UP, [("col_foot_plate", f"gil{i + 1}"), ("col_gil_plate", gh)],
                 (nid, "thread", "nut", nt), "s03", "column", thread="M5")
    # -- T-nuts and their screws: the shell ring's tabs, the drive brackets, the rack
    def tnut_on(post, face_n, y, thread, step, fid):
        c = np.array([float(np.sign(P[post].features["bottom"]["p"][0])) * L.POST_C, y,
                      float(np.sign(P[post].features["bottom"]["p"][2])) * L.POST_C])
        n = unit(face_n)
        along = UP
        t = np.cross(along, n)
        m = np.eye(4)
        m[:3, 0], m[:3, 1], m[:3, 2], m[:3, 3] = t, along, n, c
        return hw.tnut(fid, m, thread, step, post)

    for i in range(4):
        h = F("col_base_ring")[f"hole_tab{i + 1}"]
        p = np.asarray(h["p"])
        post = f"col_post_{'bf'[bool(p[2] > 0)]}{'rl'[bool(p[0] > 0)]}"
        tid = tnut_on(post, (0, 0, np.sign(p[2])), p[1], "M4", "s04", f"col_tnut_ring_{i + 1}")
        hw.screw(f"col_scr_ring_{i + 1}", p, h["d"], [("col_base_ring", f"tab{i + 1}")], (tid, "thread", "metal", L.TNUT_THREAD),
                 "s04", "column", thread="M4")
        P[post].features[f"face_ring{i + 1}"] = plane((p[0], p[1], np.sign(p[2]) * L.HALF), (0, 0, np.sign(p[2])))
        hw.mate("seated", ("col_base_ring", f"seat{i + 1}"), (post, f"face_ring{i + 1}"))
    # -- the lift: the rack on the back-right post (M3 into T-nuts at its ends), the servo on the sled's
    #    hanger, its brass pinion on the spline with the servo's centre screw
    hw.mate("seated", ("col_lift_rack", "base"), ("col_post_br", "face_in_z"))
    for i in range(len(L.RACK["screws_y"])):
        h = F("col_lift_rack")[f"hole_s{i + 1}"]
        tid = tnut_on("col_post_br", (0, 0, 1), h["p"][1], "M3", "s00", f"col_tnut_rack_{i + 1}")
        hw.screw(f"col_scr_rack_{i + 1}", h["p"], h["d"], [("col_lift_rack", f"s{i + 1}")], (tid, "thread", "metal", L.TNUT_THREAD),
                 "s00", "column", thread="M3")
    _servo_bolts(hw, P, lift_servo, "col_lift_hanger", "lh", "s10", "sled", "lift")
    hw.mate("seated", (lift_servo.id, "flange_back"), ("col_lift_hanger", "hanger_face"))
    hw.mate("spline", ("col_lift_pinion", "spline"), (lift_servo.id, "spline"), teeth=25,
            note="fitted at the servo's centre pulse with the head at today's height")
    hw.mate("seated", ("col_lift_pinion", "seat"), (lift_servo.id, "boss"))
    hc = F("col_lift_pinion")["hole_c"]
    hw.screw("col_scr_liftgear", hc["p"], hc["d"], [("col_lift_pinion", "c")], (lift_servo.id, "out_thr", "metal", 5.0),
             "s13", "sled", thread="M3", note="The servo's own centre screw through the brass gear.")
    # -- ring drives: brackets on the back posts, servos hung spline-down, hubs, pinions
    for key, (sv, hb) in ring.items():
        bid = f"col_{key}_bracket"
        for k in range(4):
            h = F(bid)[f"hole_post{k + 1}"]
            p = np.asarray(h["p"])
            post = f"col_post_b{'rl'[bool(p[0] > 0)]}"
            tid = tnut_on(post, (0, 0, -1), p[1], "M4", "s07" if key == "lower" else "s09", f"col_tnut_{key}_{k + 1}")
            hw.screw(f"col_scr_{key}brk_{k + 1}", p, h["d"], [(bid, f"post{k + 1}")], (tid, "thread", "metal", L.TNUT_THREAD),
                     "s07" if key == "lower" else "s09", "column", thread="M4")
            P[post].features[f"face_{key}{k + 1}"] = plane((p[0], p[1], -L.HALF), (0, 0, -1))
            hw.mate("seated", (bid, f"seat{k + 1}"), (post, f"face_{key}{k + 1}"))
        _servo_bolts(hw, P, sv, bid, "fl", "s07" if key == "lower" else "s09", "column", key, from_bracket=True)
        hw.mate("seated", (sv.id, "flange_back"), (bid, "shelf_under"))
        st = "s08" if key == "lower" else "s10"
        _hub_on(hw, sv, hb, st)
        pid = f"col_{key}_pinion"
        _on_hub(hw, P, pid, hb, "hub", st, "column", "lpin" if key == "lower" else "tpin")
        hw.mate("seated", (pid, "top"), (hb.id, "top"))
    # -- the sled: eight V-wheels on M5 axles through the front and back plates, eccentric spacers outside
    for fb, sz in (("f", 1), ("b", -1)):
        plate = f"col_sled_{'front' if sz > 0 else 'back'}"
        for k in range(4):
            h = F(plate)[f"hole_axle{k + 1}"]
            p = np.asarray(h["p"])
            tag = f"{fb}{'lr'[bool(p[0] < 0)]}{'lu'[int(abs(p[1] - L.WHEEL_Y[1]) < 1)]}"
            wid, eid = f"col_wheel_{tag}", f"col_ecc_{tag}"
            hw.mate("seated", (eid, "bottom"), (plate, "outer"), note="the eccentric spacer on the plate (turn it to preload)")
            hw.mate("seated", (wid, "face_a" if sz > 0 else "face_b"), (eid, "top"))
            far = np.array([p[0], p[1], sz * (L.POST_C + L.VWHEEL["w"] / 2)])
            nid, nt = hw.nut(f"col_nut_axle_{tag}", far, (0, 0, sz), "s14", "sled", thread="M5", reason="pivot",
                             joins=[wid])
            hw.screw(f"col_scr_axle_{tag}", np.array([p[0], p[1], sz * L.SLED["z_in"]]), (0, 0, sz),
                     [(plate, f"axle{k + 1}")], (nid, "thread", "nut", nt), "s14", "sled", thread="M5",
                     note="The wheel's axle: through the plate, the eccentric spacer and the wheel's bearings.")
    # -- bearings, bearing hub, collar, tube; the coupler on the pan servo's hub
    for s_ in ("lo", "up"):
        hw.mate("press", (f"col_brg_{s_}", "outer"), ("col_sled_top", f"seat_{s_}"), gap_mm=0.0, note="pressed into the housing")
        hw.mate("concentric", ("col_neck_hub", "journal"), (f"col_brg_{s_}", "bore"), note="the hub's journal in the inner race")
    hw.mate("seated", ("col_brg_lo", "top"), ("col_sled_top", "shoulder_lo"))
    hw.mate("seated", ("col_brg_up", "bottom"), ("col_sled_top", "shoulder_up"))
    hw.mate("seated", ("col_neck_hub", "flange_under"), ("col_brg_up", "top"), note="the head's weight on the upper inner race")
    hw.mate("concentric", ("col_collar", "bore"), ("col_neck_hub", "journal"), note="clamped on the journal")
    hw.mate("seated", ("col_collar", "top"), ("col_brg_lo", "bottom"), note="the collar takes the lower inner race (no end play)")
    hw.mate("concentric", ("col_neck_tube", "axis"), ("col_neck_hub", "bore"))
    cr = F("col_neck_hub")["hole_cross"]
    far = np.asarray(F("col_neck_hub")["cross_far"]["p"])
    nid, nt = hw.nut("col_nut_cross", far, cr["d"], "s16", "neck", thread="M4", reason="pivot", joins=["col_neck_hub"])
    hw.screw("col_bolt_cross", cr["p"], cr["d"], [("col_neck_hub", "cross"), ("col_neck_tube", "cross")],
             (nid, "thread", "nut", nt), "s16", "neck", thread="M4",
             note="Locates the tube in the bearing hub (the head's weight onto the upper inner race).")
    # -- pan servo under the bottom plate (four M3 up through the flange and the plate into lock nuts), its hub, the coupler
    _servo_bolts(hw, P, pan_servo, "col_sled_bottom", "pf", "s12", "sled", "pan")
    hw.mate("seated", (pan_servo.id, "flange_front"), ("col_sled_bottom", "under"))
    _hub_on(hw, pan_servo, pan_hub, "s13")
    _on_hub(hw, P, "col_pan_coupler", pan_hub, "hub", "s13", "neck", "pcpl")
    hw.mate("seated", ("col_pan_coupler", "seat"), (pan_hub.id, "top"))
    hw.mate("concentric", ("col_neck_tube", "axis"), ("col_pan_coupler", "bore"), note="the tube's bottom in the socket")
    hw.mate("seated", ("col_neck_tube", "bottom"), ("col_pan_coupler", "stop"))
    # -- the lift servo's hanger on the sled (its arms to the right side plate and under the bottom plate)
    hw.mate("seated", ("col_lift_hanger", "arm_face"), ("col_sled_side_r", "outer"))
    hw.mate("seated", ("col_lift_hanger", "arm_top_face"), ("col_sled_bottom", "under"))
    # -- the cassette on its standoffs
    for i in range(len(L.STANDOFF["angles"])):
        sid = f"col_standoff_{i + 1}"
        hw.mate("seated", (sid, "bottom"), ("col_sled_top", f"so_top{i + 1}"))
        hw.mate("seated", (sid, "top"), ("col_clockspring", f"so_under{i + 1}"))
        h = F("col_sled_top")[f"hole_so{i + 1}"]
        hw.screw(f"col_scr_so_bot_{i + 1}", h["p"], h["d"], [("col_sled_top", f"so{i + 1}")],
                 (sid, "thr_bot", "metal", L.STANDOFF_THREAD), "s17", "sled", thread="M4")
        h = F("col_clockspring")[f"hole_so{i + 1}"]
        hw.screw(f"col_scr_so_top_{i + 1}", h["p"], h["d"], [("col_clockspring", f"so{i + 1}")],
                 (sid, "thr_top", "metal", L.STANDOFF_THREAD), "s17", "sled", thread="M4")
    # -- the service cable (zip-tied at both ends) and the cosmetic spring (placement only)
    hw.mate("glue", ("col_coil_cable", "bottom"), ("col_foot_plate", "top"), gap_mm=0.0, note="zip-tied over the cable hole")
    hw.mate("glue", ("col_coil_cable", "top"), ("col_sled_bottom", "cable_under"), gap_mm=0.0,
            note="zip-tied under the bottom plate's tab")
    hw.mate("placed", ("neck_spring", "seat"), ("col_top_plate", "top"), solved=False,
            note="cosmetic: it stands on the kit's top cap (placement only)")


def _servo_bolts(hw, P, sv, host, prefix, step, link, tag, from_bracket=False):
    """Four M3 through a servo's flange and its host, lock nuts on the far side. From the servo's side
    (lift, pan) or from the host's (the ring drives, whose flange is under the shelf)."""
    F = sv.features
    hf = P[host].features
    for i in range(4):
        h = F[f"hole_fl{i + 1}"]
        hn = _near(hf, prefix, h["p"])
        nf = np.asarray(sv._nutfaces[f"nutface_fl{i + 1}"])
        if from_bracket:
            hh = hf[f"hole_{hn}"]
            nid, nt = hw.nut(f"col_nut_{tag}fl_{i + 1}", nf, hh["d"], step, link, thread="M3", reason="clamp", joins=[sv.id])
            hw.screw(f"col_scr_{tag}fl_{i + 1}", hh["p"], hh["d"], [(host, hn), (sv.id, f"fl{i + 1}")],
                     (nid, "thread", "nut", nt), step, link, thread="M3",
                     note="Through the shelf and the servo's flange into a lock nut (the case is 4 mm from each hole: "
                          "no room for an insert's wall).")
        else:
            far = np.asarray(hf[f"hole_{hn}"]["p"]) + unit(h["d"]) * hf[f"hole_{hn}"]["depth"]
            nid, nt = hw.nut(f"col_nut_{tag}fl_{i + 1}", far, h["d"], step, link, thread="M3", reason="clamp", joins=[host])
            hw.screw(f"col_scr_{tag}fl_{i + 1}", h["p"], h["d"], [(sv.id, f"fl{i + 1}"), (host, hn)],
                     (nid, "thread", "nut", nt), step, link, thread="M3",
                     note="Through the servo's flange and its seat into a lock nut.")


def _hub_on(hw, sv, hb, step):
    hw.mate("spline", (hb.id, "spline"), (sv.id, "spline"), teeth=25,
            note="fitted at the servo's centre pulse (1500 us)")
    hw.mate("seated", (hb.id, "servo_face"), (sv.id, "boss"))


def _on_hub(hw, P, pid, hb, prefix, step, link, tag):
    """Four M4 through a part's clearance holes into a 1906 hub's tapped holes."""
    for i in range(4):
        h = P[pid].features[f"hole_{prefix}{i + 1}"]
        tap = min((k for k in hb.features if k.startswith("tap")),
                  key=lambda k: np.linalg.norm(np.cross(np.asarray(hb.features[k]["p"]) - np.asarray(h["p"]), unit(h["d"]))))
        hw.screw(f"col_scr_{tag}_{i + 1}", h["p"], h["d"], [(pid, f"{prefix}{i + 1}")], (hb.id, tap, "metal", L.HUB_THREAD),
                 step, link, thread="M4")


# ------------------------------------------------------------------ steps, BOM, notes

def _steps(asm: Assembly):
    def F(*prefixes):
        return [f.id for f in asm.fasteners if f.id.startswith(prefixes)]

    plates = [f"col_sled_{k}" for k in ("front", "back", "side_l", "side_r", "bottom")]
    wheels = sorted(p.id for p in asm.parts if p.id.startswith(("col_wheel_", "col_ecc_")))
    asm.steps = [
        Step("s00", "The lift rack on the back-right post (on the bench)", ["col_post_br", "col_lift_rack"],
             F("col_tnut_rack", "col_scr_rack"), tools=["2.5 mm hex key"],
             notes=["Two M3 into drop-in T-nuts in the post's slot facing the axis (+Z), teeth toward the axis, "
                    "before the column goes together: the screws are driven from the column's inside."]),
        Step("s01", "Column: four V-slot posts on the foot plate", ["col_foot_plate", "col_post_bl", "col_post_fl",
             "col_post_fr"], F("col_scr_post_bot"), tools=["3 mm hex key (M5 flat heads)", "M5 tap for the post ends"],
             notes=["Tap both ends of each post M5 (the 4.2 mm centre bore is the tap drill).",
                    "Turn each post so a V slot faces the axis across X (+-X): the sled's wheels run in those four."]),
        Step("s03", "Foot plate on the Gil plate", ["col_gil_plate"], F("col_scr_gil", "col_nut_gil"),
             tools=["4 mm hex key", "8 mm spanner"], notes=["Four M5 through both plates, lock nuts underneath."]),
        Step("s04", "Shell support ring round the pedestal's foot", ["col_base_ring"], F("col_tnut_ring", "col_scr_ring"),
             tools=["3 mm hex key"], notes=["Its top carries the kit's base top (B_T); set it flush with the skirt's top edge."]),
        Step("s07", "Lower ring drive: bracket and servo", ["col_lower_bracket", "col_lower_servo"],
             F("col_tnut_lower", "col_scr_lowerbrk", "col_scr_lowerfl", "col_nut_lowerfl"), tools=["3 mm hex key", "2.5 mm hex key"],
             notes=["Build this drive on the bench (bracket, servo, hub, pinion: s07-s08) and bolt it to the back posts "
                    "as a unit: the pinion's screws go in from below."]),
        Step("s08", "Lower ring pinion (centred servo)", ["col_lower_hub", "col_lower_pinion"], F("col_scr_lpin"),
             tools=["servo tester (1500 us)", "3 mm hex key"],
             notes=["Centre the servo, then mesh the pinion with Anderson's sector with the lower ring at its zero."]),
        Step("s09", "Top ring drive: bracket and servo", ["col_top_bracket", "col_top_servo"],
             F("col_tnut_top", "col_scr_topbrk", "col_scr_topfl", "col_nut_topfl"), tools=["3 mm hex key", "2.5 mm hex key"]),
        Step("s10", "Top ring pinion (centred servo)", ["col_top_hub", "col_top_pinion"], F("col_scr_tpin"),
             tools=["servo tester (1500 us)", "3 mm hex key"]),
        Step("s11", "Sled box: four grid plates on the bottom plate, the bearing housing in the top",
             plates + ["col_sled_top", "col_brg_lo", "col_brg_up", "col_neck_hub", "col_collar"], [],
             unplaced=[{"key": "bhcs-M4x8", "spec": {"type": "bhcs", "thread": "M4", "length_mm": 8}, "count": 24,
                        "note": "plate to plate (goBILDA pattern: M4 through the grid into nuts or the plates' "
                                "tapped corners): to place when the plates' part numbers are chosen"}],
             tools=["2.5 mm hex key", "arbor press or a bench vice"],
             notes=["Press both 6806 into the top plate's housing against their shoulders; the bearing hub in from "
                    "the top; the collar tight under the lower inner race with no end play."],
             inferred=True, inferred_note="the plates' joints are not modelled yet (prototype)"),
        Step("s12", "Pan servo under the bottom plate", ["col_pan_servo"], F("col_scr_panfl", "col_nut_panfl"),
             tools=["2.5 mm hex key", "5.5 mm spanner"],
             notes=["Spline up on the axis, its case's top through the plate's cut-out; four M3 up through the "
                    "flange and the plate into lock nuts on top."]),
        Step("s13", "Lift servo on its hanger; centre the pan and lift servos, then the coupler and the brass gear",
             ["col_lift_hanger", "col_lift_servo", "col_pan_hub", "col_pan_coupler", "col_lift_pinion"],
             F("col_scr_liftfl", "col_nut_liftfl", "col_scr_pcpl", "col_scr_liftgear"),
             unplaced=[{"key": "shcs-M4x12", "spec": {"type": "shcs", "thread": "M4", "length_mm": 12}, "count": 4,
                        "note": "the hanger's arms to the right side plate and the bottom plate (into heat-set inserts "
                                "in the printed hanger)"}],
             tools=["servo tester (1500 us)", "3 mm hex key", "2.5 mm hex key"], joint="head_pan",
             notes=["Centre the pan servo; its 1906 hub, then the coupler (four M4 down into the hub) with the head "
                    "facing the front: head_pan = 0.",
                    "The brass lift gear goes on its spline once the sled is in the column (s14), the lift servo at its "
                    "centre pulse with the head at today's height: head_lift = 0."]),
        Step("s14", "Wheels on the sled, the sled into the column", wheels, F("col_scr_axle", "col_nut_axle"),
             tools=["3 mm hex key", "8 mm spanner", "eccentric spacer wrench"],
             notes=["Each wheel on an M5 through its plate and an eccentric spacer; slide the sled down between the "
                    "posts from the top (before the top plate), the wheels in the posts' inner X slots.",
                    "Turn the eccentric spacers until every wheel turns with the sled and none slips: no play across "
                    "the slots, no binding over the travel."]),
        Step("s16", "Neck tube: into the coupler and the bearing hub, the cross bolt", ["col_neck_tube"],
             F("col_bolt_cross", "col_nut_cross"),
             tools=["3 mm hex key", "7 mm spanner"],
             notes=["Tube down through the bearing hub to the coupler's stop; pinch the coupler's slit (its M4); "
                    "cross-drill through the hub's hole (4.5) if not pre-drilled."]),
        Step("s17", "Clock-spring cassette on its standoffs", [f"col_standoff_{i + 1}" for i in range(len(L.STANDOFF["angles"]))]
             + ["col_clockspring"], F("col_scr_so"), tools=["3 mm hex key"],
             notes=["Wind the ribbon (250 mm) loosely on the tube with the head at pan 0, its outer end through the "
                    "case's slot to the service cable."]),
        Step("s18", "Top plate", ["col_top_plate"], F("col_scr_post_top"), tools=["4 mm hex key"]),
        Step("s20", "Service cable", ["col_coil_cable"], [], tools=["zip ties"],
             notes=["From the foot plate's cable hole up the column's front opening to the sled's bottom-plate tab: the "
                    "pan and lift servos' leads and the clock spring's ribbon (both servos ride the sled)."]),
        Step("s21", "Neck spring (cosmetic)", ["neck_spring"], []),
    ]


def _bom(asm: Assembly):
    count: dict[str, list] = {}
    for f in asm.fasteners:
        count.setdefault(f.key, []).append(f)
    lines = [
        BomLine("gobilda-2000-servo", "Servo goBILDA 2000-0025-0002 (Dual Mode, 25-2 Torque)", 4, "servo",
                source=GOBILDA + "2000-series-dual-mode-servo-25-2-torque/",
                parts=["col_lift_servo", "col_pan_servo", "col_lower_servo", "col_top_servo"]),
        BomLine("gobilda-1906-hub", "Servo hub goBILDA 1906, 25T, 32 mm", 3, "hardware",
                source=GOBILDA + "1906-series-lightweight-servo-hub-25-tooth-spline-32mm-diameter/",
                parts=["col_pan_hub", "col_lower_hub", "col_top_hub"]),
        BomLine("gear-brass-m08-48", "Brass servo gear, Mod 0.8, 48T, 25T spline (goBILDA brass servo gear range)", 1,
                "hardware", parts=["col_lift_pinion"], inferred=True, inferred_note="tooth count / part number to confirm"),
        BomLine("rack-m08", f"Gear rack, Mod 0.8, {L.RACK['y1'] - L.RACK['y0']:.0f} mm (cut)", 1, "hardware",
                parts=["col_lift_rack"], inferred=True, inferred_note="goBILDA-style rack, part number to confirm"),
        BomLine("vslot-2020", f"V-slot 2020 extrusion (OpenBuilds), 4 x {L.POST_LEN:.1f} mm, ends tapped M5", 4, "hardware",
                parts=[f"col_post_{x}" for x in ("bl", "br", "fl", "fr")]),
        BomLine("vwheel-solid", "V-wheel, solid (OpenBuilds), with 625-2RS bearings", 8, "bearing",
                parts=sorted(p.id for p in asm.parts if p.id.startswith("col_wheel_"))),
        BomLine("ecc-spacer-6", "Eccentric spacer, 6 mm (OpenBuilds)", 8, "hardware",
                parts=sorted(p.id for p in asm.parts if p.id.startswith("col_ecc_"))),
        BomLine("grid-plate", "goBILDA-pattern grid plate, cut to the sled (front, back, two sides)", 4, "hardware",
                parts=[f"col_sled_{k}" for k in ("front", "back", "side_l", "side_r")], inferred=True,
                inferred_note="part numbers / thickness to choose (Charlton's build uses goBILDA grid plates)"),
        BomLine("plate-sled", "Sled bottom plate (6061, 4 mm, waterjet) and top plate with the bearing housing "
                "(6061, machined)", 2, "hardware", parts=["col_sled_bottom", "col_sled_top"]),
        BomLine("coupler-pan", "Pan coupler, turned 6061 (1906 hub to the 26 mm tube)", 1, "hardware",
                parts=["col_pan_coupler"]),
        BomLine("bearing-6806-2rs", "Ball bearing 6806-2RS, 30 x 42 x 7", 2, "bearing", parts=["col_brg_lo", "col_brg_up"]),
        BomLine("collar-30", "Clamp collar, one-piece, 30 mm bore, 45 x 13 (e.g. Ruland MSP-30-F)", 1, "hardware",
                parts=["col_collar"]),
        BomLine("standoff-92871A317", "Round standoff, 6 mm, M4 female, 24 mm (McMaster 92871A317)",
                len(L.STANDOFF["angles"]), "hardware",
                parts=[f"col_standoff_{i + 1}" for i in range(len(L.STANDOFF["angles"]))]),
        BomLine("tube-26x1.5", f"6061 tube 26 x 1.5, {L.TUBE['top'] - L.TUBE['y0']:.0f} mm (the neck)", 1, "hardware",
                parts=["col_neck_tube"]),
        BomLine("plate-gil", "Gil base plate (Ferreira's Gil drive; 169.7 x 169.7 x 8; four 5.5 mm holes added "
                "for the column's foot)", 1, "hardware", parts=["col_gil_plate"]),
        BomLine("plate-foot", "Column foot plate, 1/4 in 6061, 150 x 150 (waterjet, tapped)", 1, "hardware",
                parts=["col_foot_plate"]),
        BomLine("plate-top", "Column top plate, 6 mm 6061, 100 x 100 (waterjet)", 1, "hardware", parts=["col_top_plate"]),
        BomLine("hub-pan", "Neck bearing hub, turned 6061 (journal 30, flange 43, bore 26.1)", 1, "hardware",
                parts=["col_neck_hub"]),
        BomLine("cable-coil-12", "Retractile cable, 12-core, ~480 mm coil", 1, "electronics", parts=["col_coil_cable"],
                inferred=True, inferred_note="core count: pan + lift servos (6) + the clock spring's ribbon out (head "
                                             "servos, eyes)"),
        BomLine("ribbon-ffc", "Flat ribbon / FFC for the clock spring, 250 mm", 1, "electronics", inferred=True,
                inferred_note="conductor count follows the head's wiring"),
        BomLine("neck-spring", "Neck coil spring (cosmetic)", 1, "hardware", parts=["neck_spring"]),
    ]
    for p in asm.parts:
        if p.printed:
            lines.append(BomLine(f"print-{p.id}", f"Print: {p.name}", 1, "printed", parts=[p.id],
                                 spec={"model": p.source.get("model")}))
    names = {"shcs": "socket head cap screw (ISO 4762)", "fhcs": "flat head screw (ISO 10642)", "lock_nut": "lock nut (ISO 10511)",
             "insert": "heat-set insert", "t_nut": "drop-in T-nut, 2020"}
    for key, fl in sorted(count.items()):
        s = fl[0].spec
        size = f"{s['thread']} x {s['length_mm']:g}" if "length_mm" in s else s["thread"]
        lines.append(BomLine(key, f"{size} {names.get(s['type'], s['type'])}", len(fl), "fastener", s,
                             fasteners=[f.id for f in fl]))
    asm.bom = lines


def _notes():
    from parts.column.clockspring_case import ribbon_turns

    return [
        "Variant `internals: column` (default) of the droid; `anderson_morton` keeps Anderson's base turntable, rack "
        "lift and 744 mm neck on Morton's cage.",
        f"Lift: {L.LIFT[0]:g}..+{L.LIFT[1]:g} mm, {L.LIFT_MM_PER_DEG:.4f} mm per servo deg; pan direct 1:1, design "
        f"+-{L.PAN[1]:g} deg, mechanical +-{L.PAN_MECH:g} (the servo's end); the clock spring allows "
        f"+-{ribbon_turns(250.0) * 180:.0f}.",
        "After Jason Charlton's build (his replies 2026-10-01, mech/vendor/refs/column_internals): the sled on V-wheels, "
        "the lift servo behind a plate on it on a rack at the rear, the pan servo at its base turning the neck through "
        "a coupler.",
        "The arms keep riding their rings (a shoulder must turn with its ring); what moved onto the column is the rings' "
        "drives (static brackets on the back posts) and the shells' support (the ring round the pedestal's foot).",
        "Hunter's head is unchanged: its 26 mm coupler takes the short neck's top at its bore stop (y 696.3).",
        "Kit change: LS_IC_1 (the static core) takes a relief for the lower pinion's swept disc (Anderson's pinion sits "
        "in the same place; the cut applies to both internals).",
    ]


def _head_ref():
    return {
        "ref": "../hunter_head/manifest.json", "id": "hunter_head", "name": "Head: Hunter's gimbal mech",
        "mount": {"parent_link": "neck", "transform": {"t": [0, 738.3, 0], "q": [0, 0, 0, 1]}, "inferred": False,
                  "note": "the coupler clamps the short neck's top; the neck turns (head_pan) and rides the sled (head_lift)"},
        "interface": {
            "mates": [
                {"type": "concentric", "a": ["hunter_head/neck_coupler", "bore"], "b": ["col_neck_tube", "axis"],
                 "note": "coupler bore (26 mm) on the short neck"},
                {"type": "seated", "a": ["hunter_head/neck_coupler", "bore_stop"], "b": ["col_neck_tube", "top"],
                 "note": "height stop: the tube's top against the coupler's hub plate"},
            ],
            "fasteners": [
                {"id": "set_coupler_tube", "spec": {"type": "set_screw", "thread": "M4", "length_mm": 6},
                 "in": ["hunter_head/ins_coupler_side", "thread"], "onto": "col_neck_tube", "link": "hunter_head:neck",
                 "note": "cup-point set screw through the coupler's side insert onto the tube"},
            ],
        },
    }


def ring_drive(joint: str, drive: dict) -> dict:
    """The kit ring joint's drive when the column is the droid's internals: the same pinion and sector
    (same ratio), the column's servo (goBILDA 2000, hung spline-down from a bracket on the back posts)."""
    key = {"torso_lower": "lower", "torso_top": "top"}[joint]
    d = dict(drive)
    d["servos"] = [f"col_{key}_servo"]
    d["servo_deg_per_unit"] = round(RING_RATIO[key], 4)
    d["gears"] = [f"{ID}/g_{key}_ring"]
    d["note"] = (f"goBILDA 2000 hung from the column's {key} drive bracket (back posts), a 1906 hub and our "
                 f"{L.RING[key]['teeth']}T pinion with Anderson's teeth into his sector on the ring "
                 f"({drive.get('note', '')})")
    return d


def build() -> Assembly:
    """`python -m workbench build column`: the column alone (with Hunter's head by reference)."""
    return build_column()


def checks(asm: Assembly):
    return []
