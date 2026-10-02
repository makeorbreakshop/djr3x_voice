"""Hunter's head mech: a two-servo push-rod gimbal (tilt + roll) with the R3X head shell on
top, and a direct-drive visor arm.

Sources (mech/vendor/hunter_head/, licensed, never committed): `Head Joint Asm.step` (the
mechanism with its own placements), the printed parts as STL, `Visor Arm DXF.dxf`,
`RX Head Mech BOM.pdf`. Run with `mech/.venv/bin/python -m workbench build hunter_head`.

FRAME (mm): the head frame. Origin = the gimbal centre = the head_tilt pivot; +Y up, +Z
forward (the face), +X the droid's left (show/SPEC.md "Frame and zeros"). In the droid body
frame this origin sits at y = 738.3 mm (the kit's shell coordinates; rig.json says 740).

How the parts were placed, and how sure that is:
- STEP parts: the STEP's own placements, rotated into the head frame (STEP +X = forward,
  STEP +Z = the droid's right, gimbal centre at STEP (0, 67, 0)). Exact.
- Mount plate (STL V4): its six M4 clearance holes (120 x 28.7 grid) onto the six 6 mm
  heat-set insert holes in the head bottom; the STL's servo insert holes land on the STEP
  servos' flanges. Fitted, residual in the manifest notes.
- Shell (top, bottom, sides): the same fit - the head bottom's insert holes define the head
  frame in the shell's own coordinates (yaw 51.8 deg in the kit export). Fitted.
- Purchased hardware (hardware.py): goBILDA vendor STEPs and ISO parametric screws from the
  parts library (mech/parts), solved from mates on measured features; the push-rod geometry
  the STEP leaves open is chosen by testing every real-part combination.
- Neck joint member, visor servo + mount, visor arms: not placed by any source file. Inferred,
  and flagged on every part.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

import numpy as np
import trimesh

from workbench import geom
from workbench.collide import mesh_hash
from workbench.geom import _cache_key, align, cached, fastener_mesh
from workbench.kinematics import apply, horn_basis, link_matrices, solve_rod
from workbench.model import (Assembly, BomLine, Fastener, Joint, Link, Linkage, Part, Step,
                             Transform)
from parts.library import part as lib_part
from assemblies.hunter_head.hardware import add_hardware, clamp_screws, contact_mates, neck_member_pin

ID = "hunter_head"
HEAD_ORIGIN_Y = 738.3  # the head frame's origin (gimbal centre) in the body frame
MECH = Path(__file__).resolve().parents[2]
VENDOR = MECH / "vendor" / "hunter_head"
PROFILE = MECH.parent / "profiles" / "r3x" / "robot.json"

# ------------------------------------------------------------------ parameters
PARAMS = {
    # the visor's drive: "hunter_direct" (Hunter's, 2026-10-02: a standard servo on each side on the visor
    # axis, his Visor Servo Mount and DXF arm, 1:1) or "anderson_linkage" (Anderson's horn, push rod and tab
    # from one servo in a cradle above the plate, the visor on F6001ZZ bearings). HUNTER_VISOR_DRIVE overrides.
    "visor_drive": os.environ.get("HUNTER_VISOR_DRIVE", "hunter_direct"),
    "visor_arm_thickness": 3.0,   # mm; the DXF implies a cut plate (inferred: 3 mm aluminium)
    # the gimbal horns: goBILDA 1916 arms cut down (BOM, "these will need to be cut down"; Hunter 2026-10-02:
    # "I cut down the stock plastic gobilda servo horns"). His top-down photo puts both ball studs 30.5 / 31.5 mm
    # from the hub (scaled by the arm's own 8-hole ring, 11.31 mm): the 32 mm hole (inferred from the photo)
    "horn_hole_mm": 32.0,
    "fit_gimbal_limits": True,    # when his horn cannot reach tilt/roll below, the joints take the range it does
    "horn_hub_height": 8.0,       # goBILDA 1906 hub
    "horn_arm_thickness": 3.0,
    "ball_above_arm": 3.5,        # ball-link ball centre above the arm face
    "hub_ball_height": 3.5,       # ball centre above the top sonic hub's face
    "hub_pattern": 8.0,           # sonic hub 16 mm pattern: holes at (+/-8, +/-8)
    "tilt_limits": (-20.0, 25.0),
    "roll_limits": (-12.0, 12.0),
    "visor_limits": (-15.0, 30.0),
    "servo_centre_us": 1500,
    "printed_wall_mm": 0.8,       # mass model: 2 x 0.4 mm perimeters on every face
    "neck_tube_bore": 26.0,       # the coupler's bore: 26 on Anderson's neck tube (the droid's default); Hunter's own neck is 32
    "plate_insert_depth": 6.0,    # servo insert holes as Hunter drew them (stock, Brandon 2026-09-30); the plate's
                                  # M3-insert change (parametric agent) brings the walls and the tunnel clearance
    "coupler_top_t": 10.0,        # the coupler's hub plate: 10 (was 5): room for the hub boss relief over the nut pockets
    "coupler_hub_hole": "nut_trap",  # decided: through-bolts with nuts; the coupler takes hex pockets under its plate
                                     # (the relief only as deep as the hub's boss, a 12 mm hole on: 1.8 mm walls)
    "visor_channel_mm": 1.5,      # clearance channel in the shells along the kit visor's sweep (0 = none)
    "printed_infill": 0.15,
}
STEP_G = np.array([0.0, 67.0, 0.0])  # gimbal centre in the STEP's frame
# STEP -> head: head = (-z, y - 67, x)
M_STEP = np.array([[0, 0, -1, 0], [0, 1, 0, -STEP_G[1]], [1, 0, 0, 0], [0, 0, 0, 1]], float)
PLATE_STL_TO_STEP = (0.24, 38.89)  # the STEP places the plate at (0.24, 38.89, 0), rot Y 90
# plate STL -> head: (x, y - 28.11, z + 0.24)
M_PLATE = np.eye(4)
M_PLATE[:3, 3] = [0, PLATE_STL_TO_STEP[1] - STEP_G[1], PLATE_STL_TO_STEP[0]]

GOBILDA = "https://www.gobilda.com/"

# The test suite's failures this module has traced to a cause, each with the fix proposed to
# Brandon (TESTS.md). Anything not matched here fails the suite.
EXPLAINED = [
    {"test": "no_overlap", "parts": ["side_left", "side_right", "head_top", "head_bottom"],
     "cause": "Hunter's shell STLs interpenetrate ~3 mm where the top's lip meets the sides (as exported)",
     "fix": "check the printed parts; if they bind, offset the top's lip 0.3 mm in CAD or sand it"},
    {"test": "no_overlap", "parts": ["mount_plate", "side_left", "side_right"],
     "cause": "the V4 plate's back corners run 2.4-2.7 mm into the side shells in the source files",
     "fix": "trim 3 mm off the plate's back corners (or the sides' inner rib) before printing"},
    {"test": "no_overlap", "parts": ["mount_plate", "servo_l", "servo_r"],
     "cause": "the V4 plate's servo pockets are 1-2 mm tighter than goBILDA's 2000-series case (the STEP's "
              "older plate fits it)",
     "fix": "open each servo pocket 1.5 mm in the plate"},
    {"test": "no_overlap", "parts": ["hub_top", "hub_bottom", "clamp_hub_*"],
     "cause": "goBILDA's sonic-hub models leave the clamp screw's tapped hole out on one side: the vendor clamp "
              "screws (from Hunter's STEP) pass through modelled material",
     "fix": "none needed on the parts (a vendor-model simplification); check against goBILDA's current STEP"},
    {"test": "no_overlap", "parts": ["h_le_*", "h_re_*", "h_fp", "h_leye_*", "h_reye_*", "h_m_*", "h_mb", "h_hp_*",
                                     "side_left", "side_right", "head_top", "head_bottom"],
     "cause": "the kit's cosmetic head parts (ears, face plate, eyes, mouth, headband) sit where the kit head puts "
              "them, and Hunter's shells are not the kit's: where they meet, they overlap (Brandon: the kit part's "
              "look wins)",
     "fix": "trim Hunter's shell where each kit part lands (the overlap report gives the depth and place), or "
            "glue the kit part on its surface; nothing moves"},
    {"test": "inserts", "parts": ["neck_coupler", "ins_coupler_side"],
     "cause": "the coupler's side insert (the tube's set screw) has 1.5 mm of plastic round it: the parametric "
              "coupler grows an outside boss to the 1.5 mm wall rule, the suite's 0.5 x OD (6 mm) wants 3",
     "fix": "accepted at the rule's 1.5 mm (a set screw, no load along it); the hub screws are through-bolts "
            "into nut traps now"},
    {"test": "inserts", "parts": ["mount_plate", "ins_servo_1", "ins_servo_2", "ins_servo_7", "ins_servo_8"],
     "cause": "the four outer-post servo inserts (Ruthex M3 x 5.7, OD 4.6) have 2.0 mm of plastic to the servo pocket: "
              "above the 1.5 mm floor, short of the rule's 0.5 x OD = 2.3 mm, and no M3 insert can reach 2.3 because the "
              "servo case is 4.0 mm from each hole centre (the servo's flange hole spacing fixes it)",
     "fix": "none: geometry-limited by the servo flange spacing (2.0 >= the 1.5 mm floor). The 1.30 mm of plastic "
            "over the cable tunnel under the outer bosses is accepted too"},
    {"test": "inserts", "parts": ["visor_mount_l", "visor_mount_r", "ins_vmount_*"],
     "cause": "Hunter's Visor Servo Mount holds the servo by its flange: the 48 mm hole pattern on a 40 mm case leaves "
              "4 mm from each insert's centre to the case end, so the bars carry 0.8 mm of plastic between a 6 mm insert "
              "and the servo pocket (his own print: 1.2 mm, with the case 0.2 mm into the bar); the rule wants 0.5 x OD",
     "fix": "none: geometry-limited by the servo's flange spacing, as on the plate's servo bosses; the load is the "
            "visor's, small. M3 inserts (Ruthex 5.7, OD 4.6) would give 1.5 mm if wanted"},
    {"test": "clearance", "parts": ["head_top", "horn_arm_*", "link_a_*", "stud_a_*", "nut_ball_a_*", "wsh_ball_a_*"],
     "cause": "a show clip asks head_tilt +16..+18 (with the visor up), past the range Hunter's cut-down horns reach "
              "(32 mm hole: the coupled limit, tilt max +13 at roll 0 falling to +9 at |roll| 12): there the horn arm and "
              "its ball link swing into the head top. The clip, not the head, is out of range",
     "fix": "the performer clamps to the coupled limit on the Physical rig (the clip lint warns); or the 40 mm hole "
            "(the uncut arm) if more nod is wanted than Hunter's head gives"},
    {"test": "mates_hold", "parts": ["h_hp_1", "side_left", "side_right"],
     "cause": "the kit headband (H_HP_1) stands 0.5 mm off Hunter's side piece where it lands (the kit drew it on the "
              "kit's own head); the glue rule wants <= 0.5",
     "fix": "thickened epoxy (or a 0.5 mm pad) where the headband meets the side"},
    {"test": "clearance", "parts": ["side_left", "side_right", "head_top", "h_v_*"],
     "cause": "the kit visor's side arms run 0.90-0.96 mm from Hunter's top and side shells over the whole visor "
              "range (FCL, 3 deg steps; they never touch): the kit visor was drawn round the kit's own shell",
     "fix": "sand 0.5 mm off the shells' inner faces along the arms' path (or accept it: a 0.1 mm shortfall on a "
            "1 mm print-tolerance rule)"},
    {"test": "clearance", "parts": ["h_v_4", "h_v_5", "h_le_*", "h_re_*"],
     "cause": "the kit visor's axle ends (H_V_4/5) turn inside the kit's ear cups (H_LE/H_RE) with 0-0.1 mm, as the "
              "kit draws them: on the kit they are the visor's bearing; here the stub axles carry the visor, so the "
              "cups only rub",
     "fix": "open each ear cup's bore 0.5 mm (sand or reprint scaled 102 % in X/Z)"},
    {"test": "clearance", "parts": ["custom_joint_piece", "pillow_f", "pillow_b", "pillow_bearing_*"],
     "cause": "in the STEP the cross sits 0.1-0.5 mm off the pillow blocks as it turns",
     "fix": "the BOM's M4 washers as spacers, one each side of the cross (0.8 mm)"},
]

SERVO = {"model": "goBILDA 2000-0025-0002 (Dual Mode, 25-2 Torque)", "stall_kgcm": 25.2,
         "volts": 6.0, "mass_g": 70.0, "range_deg": 300, "centre_us": 1500,
         "note": "catalogue: 25.2 kg-cm stall at 6 V, 500-2500 us, ~300 deg (inferred from the product page named in the BOM)"}


def vendor(name: str) -> Path:
    p = VENDOR / name
    if not p.exists():
        raise FileNotFoundError(f"{p} missing - the hunter_head sources live in mech/vendor/ (gitignored)")
    return p


def xf(mesh: trimesh.Trimesh, m: np.ndarray) -> trimesh.Trimesh:
    out = mesh.copy()
    out.apply_transform(m)
    return out


# ------------------------------------------------------------------ shell fit

def fit_shell():
    """Head frame in the shell STLs' own coordinates, from the head bottom's insert holes and
    the plate's clearance holes. Returns (4x4 shell->head, report)."""
    hb = geom.stl(vendor("RX Head Bottom with mount holes.stl"))
    holes = np.array([c for c, r in geom.section_holes(hb, 1, 733.0, 2.5, 3.5)])
    assert len(holes) == 6, f"expected 6 insert holes in the head bottom, found {len(holes)}"
    c = holes.mean(0)
    _, _, vt = np.linalg.svd(holes[:, [0, 2]] - c[[0, 2]])
    x = np.array([vt[0, 0], 0, vt[0, 1]])
    left = geom.stl(vendor("Left Side w alignment holes.stl"))
    if (left.centroid - c) @ x < 0:
        x = -x
    # the six boss tops are not level in the STLs (the shells sit ~1 deg pitched): the plate seats
    # on the plane through them, so that plane's normal is the head's up
    tops = []
    for h in holes:
        near = hb.vertices[np.linalg.norm(hb.vertices[:, [0, 2]] - h[[0, 2]], axis=1) < 4.5]
        tops.append([h[0], float(near[:, 1].max()), h[2]])
    tops = np.array(tops)
    A_ = np.c_[tops[:, 0], tops[:, 2], np.ones(len(tops))]
    ka, kb, kc = np.linalg.lstsq(A_, tops[:, 1], rcond=None)[0]
    y = np.array([-ka, 1.0, -kb])
    y /= np.linalg.norm(y)
    x = x - y * (x @ y)
    x /= np.linalg.norm(x)
    z = np.cross(x, y)
    r = np.stack([x, y, z])
    boss_top = float(ka * c[0] + kb * c[2] + kc)
    boss_rms = float(np.sqrt(np.mean(np.square(A_ @ [ka, kb, kc] - tops[:, 1]))))
    # the plate's hole grid centre in the head frame (plate STL (0, 25, 0.03) -> head)
    plate_c = np.array([0.0, 25.0 - 28.11, 0.03 + 0.24])
    o = np.array([c[0], boss_top, c[2]]) - r.T @ plate_c
    m = np.eye(4)
    m[:3, :3] = r
    m[:3, 3] = -r @ o
    # residual: every head-bottom hole against the nearest plate hole
    plate_holes = np.array([[sx * 60.0, 0, z0 + 0.24] for sx in (-1, 1) for z0 in (-28.71, 0.03, 28.77)])
    got = (np.c_[holes[:, 0], tops[:, 1], holes[:, 2]] - o) @ r.T
    res = [float(np.min(np.linalg.norm(plate_holes[:, [0, 2]] - g[[0, 2]], axis=1))) for g in got]
    return m, {
        "yaw_deg": round(math.degrees(math.atan2(x[2], x[0])), 2),
        "pitch_deg": round(math.degrees(math.atan2(math.hypot(ka, kb), 1.0)), 2),
        "boss_plane_rms_mm": round(boss_rms, 3),
        "origin_in_shell": [round(float(v), 2) for v in o],
        "hole_rms_mm": round(float(np.sqrt(np.mean(np.square(res)))), 3),
        "holes_head": got,
        "boss_top_head": float((r @ (np.array([c[0], boss_top, c[2]]) - o))[1]),
    }


# ------------------------------------------------------------------ helpers

def leaf_axis(v: np.ndarray):
    """Principal axis, centre and length of a slender leaf (screws, shafts)."""
    c = v.mean(0)
    sub = v[:: max(1, len(v) // 4000)]
    _, _, vt = np.linalg.svd(sub - c, full_matrices=False)
    a = vt[0]
    s = (v - c) @ a
    return c, a, float(s.max() - s.min()), s


# Swap hook: part id -> a parametric module that replaces the part's geometry. The module exposes
# make(params: dict) -> build123d Part in the reference STL's frame, with a `features` attribute
# ({name: axis/plane dict}, SCHEMA.md) and optionally REFERENCE (the STL's file name) for the
# regression. Without an entry, parts use parts/hunter.py (or their source mesh).
PARAMETRIC: dict[str, str] = {
    "mount_plate": "parts.head.base_plate",
    "neck_coupler": "parts.head.neck_coupler",
    "custom_joint_piece": "parts.head.custom_joint_piece",
    "head_bottom": "parts.head.head_bottom",
    "side_left": "parts.head.side_left",
    "side_right": "parts.head.side_right",
    "head_top": "parts.head.head_top",
}
# design defaults passed to the parametric shells (reversible): the fastening rule's hole depths
SHELL_PARAMS = {"head_bottom": {"insert_depth": 7.0}}  # 6 x 6 mm insert + 1 mm
# the STEP's own placement of the cross (Custom_Joint_Piece at (0, 67, 0), -90 deg about Y)
M_CJP = np.array([[0, 0, -1, 0], [0, 1, 0, 67.0], [1, 0, 0, 0], [0, 0, 0, 1.0]])


from workbench.geom import code_sig  # noqa: E402  (kept importable from here: visor.py uses it)


def remodel(name: str, M: np.ndarray, part_id: str | None = None, **params):
    """One of our parametric remodels (mech/parts/hunter.py, or a PARAMETRIC override) placed by
    M (its reference STL's frame -> head), with its parameters and its regression against the
    reference."""
    import importlib

    from parts import hunter
    from workbench.geom import cached, _cache_key, _file_sig

    if part_id in PARAMETRIC:
        mod = importlib.import_module(PARAMETRIC[part_id])
        shape = mod.make(params)
        r = hunter.Remodel(getattr(mod, "NAME", part_id), params, shape, dict(getattr(shape, "features", {}) or {}),
                           getattr(mod, "REFERENCE", hunter.REMODELS[name]().reference if name in hunter.REMODELS else ""))
        name = f"{PARAMETRIC[part_id]}"
    else:
        r = hunter.REMODELS[name](**params)
    ref = vendor(r.reference)
    sig = code_sig(PARAMETRIC[part_id] if part_id in PARAMETRIC else "parts.hunter")
    vf = cached(_cache_key("remodel-mesh", name, sorted((k, str(v)) for k, v in r.params.items()), sig, 1),
                lambda: (lambda m: (np.asarray(m.vertices), np.asarray(m.faces)))(r.mesh()))
    mesh = trimesh.Trimesh(*vf, process=False)

    def reg():
        R = geom.stl(ref)
        if name.endswith("visor_servo_mount"):  # compared in its squared frame
            R.apply_transform(square_mount(R))
        return {k: (round(v, 3) if isinstance(v, float) else v) for k, v in hunter.compare(mesh, R, 8000).items()}

    stats = cached(_cache_key("remodel-reg", name, sorted(r.params.items()), _file_sig(ref), sig, 2), reg)
    placed = mesh.copy()
    placed.apply_transform(M)
    feats = {k: moved_feature(v, M) for k, v in r.features.items()}
    src = {"kind": "parametric", "model": name if part_id in PARAMETRIC else f"parts/hunter.py:{name}",
           "params": r.params, "reference": r.reference,
           "regression": stats}
    return placed, src, feats


def moved_feature(f, M):
    from workbench.mates import moved

    return moved(f, M)


def square_mount(m: trimesh.Trimesh) -> np.ndarray:
    """The visor mount STL's frame -> its squared frame (feet along X, up +Y, pocket along Z)."""
    xz = m.vertices[:, [0, 2]] - m.vertices[:, [0, 2]].mean(0)
    _, _, vt = np.linalg.svd(xz, full_matrices=False)
    yaw = math.atan2(vt[0, 1], vt[0, 0])
    r = trimesh.transformations.rotation_matrix(yaw, [0, 1, 0], m.centroid)
    q = m.copy()
    q.apply_transform(r)
    t = np.eye(4)
    t[:3, 3] = -np.array([(q.bounds[0][0] + q.bounds[1][0]) / 2, q.bounds[0][1], q.bounds[0][2]])
    return t @ r


def printed_mass(mesh: trimesh.Trimesh, density=1.24) -> tuple[float, str]:
    wall = PARAMS["printed_wall_mm"]
    shell_v = mesh.area * wall
    if mesh.is_watertight and mesh.volume > shell_v:
        v = shell_v + PARAMS["printed_infill"] * (mesh.volume - shell_v)
        return v * density / 1000, f"PLA {density} g/cc: area x {wall} mm walls + {PARAMS['printed_infill']:.0%} infill"
    return shell_v * density / 1000, f"PLA {density} g/cc: area x {wall} mm walls (mesh not closed, no infill counted)"


def metal_mass(mesh, density):
    v = abs(mesh.volume) if mesh.is_watertight else mesh.convex_hull.volume * 0.5
    return v * density / 1000


# ------------------------------------------------------------------ build

def build() -> Assembly:
    prof = json.loads(PROFILE.read_text())
    plims = {j["name"]: (j["hard"]["min"], j["hard"]["max"]) for j in prof["joints"]}
    asm = Assembly(
        id=ID,
        name="Hunter head mech",
        description="Two-servo push-rod gimbal (head tilt + head roll) on a fixed hex post, "
                    "with the R3X head shell bolted to the mount plate and a servo-driven visor arm.",
        frame_note="mm. Origin = gimbal centre = head_tilt pivot. +Y up, +Z forward (face), +X droid left. "
                   "Sits at body y = 738.3 mm on the neck (head_pan) - see mount.",
        mount={"parent_link": "neck", "transform": {"t": [0, 738.3, 0]}, "mount_node": "hunter_head_mount",
               "inferred": False,
               "note": "Body frame: the head rides the neck tube (head_pan, and head_lift if fitted)."},
        guide=None,
    )
    asm.links = [
        Link("neck", "Neck post (coupler, hubs, hex shaft, U-joint body)", None),
        Link("cross", "Cross (custom joint piece)", "head_tilt"),
        Link("head", "Head (mount plate, servos, shell)", "head_roll"),
        Link("visor", "Visor arms", "visor"),
    ]

    # ---------------------------------------------------------- STEP parts
    leaves = geom.step_leaves(vendor("Head Joint Asm.step"))
    by_label: dict[str, list] = {}
    for trail, label, v, f in leaves:
        by_label.setdefault(label, []).append((trail, trail.split("/")[-2] if trail.count("/") > 1 else "", v, f))

    def step_part(label, k=0) -> trimesh.Trimesh:
        _, _, v, f = by_label[label][k]
        return xf(trimesh.Trimesh(v, f, process=True), M_STEP)

    src_step = lambda label, **kw: {"file": "Head Joint Asm.step", "kind": "step", "entity": label, "placement": "step", **kw}
    parts: list[Part] = []

    def add(p: Part):
        parts.append(p)
        return p

    step_coupler = step_part("RX_Neck_Coupler_V1")
    coupler, csrc, cfeat = remodel("neck_coupler", M_STEP, "neck_coupler", bore_d=PARAMS["neck_tube_bore"],
                                    pin_hole="heat_set", hub_hole=PARAMS["coupler_hub_hole"], top_t=PARAMS["coupler_top_t"])
    csrc["placement"] = "step (the STEP's own coupler instance)"
    csrc["vs_step_bounds_mm"] = round(float(np.abs(coupler.bounds - step_coupler.bounds).max()), 3)
    m, n = printed_mass(coupler)
    add(Part("neck_coupler", "RX Neck Coupler V1 (parametric)", "mech", "neck", coupler, csrc,
             "PLA (printed)", True, (0, -1, 0), 60, m, n, features=cfeat, cad="parametric",
             note="Slides over the neck tube; the 16 mm pattern on top takes the bottom sonic hub. "
                  "bore_d=26 makes the variant for Anderson's 26 mm neck tube."))

    # Hunter's "neck joint member" is the same solid as the cross (custom_joint_piece: the
    # parametric remodel of both is one model), so it is not a second part here.

    hub_names = sorted(range(len(by_label["1309-0016-4008"])),
                       key=lambda k: by_label["1309-0016-4008"][k][2][:, 1].mean())
    for k, (pid, name, note) in zip(hub_names, [
        ("hub_bottom", "Sonic hub, thru-hole (goBILDA 1311)", "Bolts down onto the coupler; clamps the hex shaft."),
        ("hub_top", "Sonic hub, threaded (goBILDA 1309)", "Top of the fixed post; the two push-rod balls screw into its face."),
    ]):
        mesh = step_part("1309-0016-4008", k)
        if pid == "hub_bottom":
            mesh = fit_1311(mesh, coupler)
        add(Part(pid, name, "hardware", "neck", mesh,
                 src_step("1309-0016-4008", bom="1311" if pid == "hub_bottom" else "1309"),
                 "aluminium", False, (0, 1 if pid == "hub_top" else -1, 0), 50,
                 metal_mass(mesh, 2.7), "aluminium 2.7 g/cc", inferred=pid == "hub_bottom",
                 inferred_note="The STEP models both hubs as 1309; the BOM lists one 1311 thru-hole + one 1309 "
                               "threaded. The coupler's 3.3 mm (M4 tap) holes need screws from above, so the "
                               "thru-hole one goes at the bottom: goBILDA's 1311 STEP, fitted onto the STEP's "
                               "1309 (axis, face, clamp clocking)." if pid == "hub_bottom" else "",
                 note=note))

    shaft = step_part("1516-4008-0960")
    add(Part("hex_shaft", "8 mm REX hex shaft, 96 mm (goBILDA 1516)", "hardware", "neck", shaft,
             src_step("1516-4008-0960"), "aluminium", False, (0, 1, 0), 110, metal_mass(shaft, 2.7),
             "aluminium 2.7 g/cc", note="Fixed post: the head tilts and rolls around it."))
    uj = step_part("4005-0016-0001_Outer_Shell")
    add(Part("ujoint", "Universal joint (goBILDA 4005, 16-1)", "hardware", "neck", uj,
             src_step("4005-0016-0001_Outer_Shell"), "aluminium", False, (0, -1, 0), 30, metal_mass(uj, 2.7),
             "aluminium 2.7 g/cc", note="Its bearings carry the cross on the tilt axis."))
    for k, side in enumerate(("l", "r")):
        b = step_part("1600-0410-0004", k)
        side = "l" if b.centroid[0] > 0 else "r"
        add(Part(f"uj_bearing_{side}", f"Flanged bearing, tilt axis ({'left' if side == 'l' else 'right'})",
                 "bearing", "neck", b, src_step("1600-0410-0004"), "steel", False,
                 (1 if side == "l" else -1, 0, 0), 25, metal_mass(b, 7.85), "steel 7.85 g/cc"))

    cjp = step_part("Custom_Joint_Piece")
    cjsrc, cjkw = src_step("Custom_Joint_Piece", also=["Custom Joint Piece.step"]), {}
    if "custom_joint_piece" in PARAMETRIC:  # ours, on the STEP's own placement of the cross
        cjp, cjsrc, cjfeat = remodel("custom_joint_piece", M_STEP @ M_CJP, "custom_joint_piece",
                                     pivot_hole="clearance")  # through-bolts + lock nuts (fastening rule: pivots)
        cjsrc["placement"] = "step (the STEP's own cross instance)"
        cjkw = {"features": cjfeat, "cad": "parametric"}
    m, n = printed_mass(cjp)
    add(Part("custom_joint_piece", "Custom Joint Piece", "mech", "cross", cjp,
             cjsrc, "PLA (printed)", True,
             (0, 0, 0), 0, m, n, note="The gimbal cross: tilt axis (X) in the U-joint bearings, "
                                      "roll axis (Z) in the pillow-block bearings.", **cjkw))

    # the servo bosses: the inserts preset's M3 (option a, 2026-09-30: the kit's 6 mm M4 leaves 1.0 mm of
    # wall to the servo case); PARAMS["plate_inserts"] = False keeps Hunter's stock M4 holes
    pkw = {"inserts": True} if PARAMS.get("plate_inserts", True) else {"insert_depth": PARAMS["plate_insert_depth"]}
    plate, psrc, pfeat = remodel("base_plate", M_PLATE, "mount_plate", **pkw)
    step_plate = step_part("Head_Mounting_Plate")
    dev = float(np.abs(plate.bounds - step_plate.bounds).max())
    psrc["placement"] = "fitted (the reference V4 STL's frame, on the head bottom's inserts)"
    psrc["fit"] = f"vs the STEP's Head_Mounting_Plate: bounds agree within {dev:.2f} mm"
    m, n = printed_mass(plate)
    add(Part("mount_plate", "Head mount plate (RX Head Mech Base Plate V4, parametric)", "mech", "head", plate, psrc,
             "PLA (printed)", True, (0, 1, 0), 50, m, n, features=pfeat, cad="parametric",
             note="Carries both servos, the pillow blocks and the head shell (6 x M4 into the head bottom)."))

    for k in range(2):
        pb = step_part("1602-0032-0006", k)
        side = "f" if pb.centroid[2] > 0 else "b"
        brg = step_part("1600-0515-0006", k)
        add(Part(f"pillow_{side}", f"Pillow block ({'front' if side == 'f' else 'back'}), goBILDA 1602",
                 "hardware", "head", pb, src_step("1602-0032-0006"), "aluminium", False,
                 (0, 0, 1 if side == "f" else -1), 35, metal_mass(pb, 2.7), "aluminium 2.7 g/cc"))
        add(Part(f"pillow_bearing_{side}", f"Bearing, roll axis ({'front' if side == 'f' else 'back'})",
                 "bearing", "head", brg, src_step("1600-0515-0006"), "steel", False,
                 (0, 0, 1 if side == "f" else -1), 45, metal_mass(brg, 7.85), "steel 7.85 g/cc"))

    servos = {}
    for k in range(2):
        sv = step_part("Servo_-_2000-0025-0002", k)
        side = "l" if sv.centroid[0] > 0 else "r"
        top = sv.vertices[sv.vertices[:, 1] > sv.vertices[:, 1].max() - 2.5]
        spline = np.array([top[:, 0].mean(), sv.vertices[:, 1].max(), top[:, 2].mean()])
        servos[side] = spline
        add(Part(f"servo_{side}", f"Servo {'left' if side == 'l' else 'right'} (goBILDA 2000-0025-0002)",
                 "servo", "head", sv, src_step("Servo_-_2000-0025-0002"), "servo", False, (0, 1, 0), 70,
                 SERVO["mass_g"], "catalogue ~70 g (inferred)",
                 note=f"Output spline at {np.round(spline, 2).tolist()}, axis +Y (vertical)."))

    # ---------------------------------------------------------- shell
    m_shell, fit = fit_shell()
    shell_src = lambda f: {"file": f, "kind": "stl", "placement": "fitted",
                           "fit": f"insert-hole fit, rms {fit['hole_rms_mm']} mm, yaw {fit['yaw_deg']} deg"}
    for pid, name, f, ex in [
        ("head_bottom", "Head bottom (with mount holes)", "RX Head Bottom with mount holes.stl", (0, -1, 0)),
        ("side_left", "Left side (alignment holes)", "Left Side w alignment holes.stl", (1, 0, 0)),
        ("side_right", "Right side (alignment holes)", "Right Side w alignment holes.stl", (-1, 0, 0)),
        ("head_top", "Head top", "RX Head Top.stl", (0, 1, 0)),
    ]:
        src, kw = shell_src(f), {}
        if pid in PARAMETRIC:  # ours, in the STL's frame: the same fit places it
            mesh, psrc, feats = remodel(pid, m_shell, pid, **SHELL_PARAMS.get(pid, {}))
            src = dict(psrc, placement=src["placement"], fit=src["fit"])
            kw = {"features": feats, "cad": "parametric"}
        else:
            mesh = xf(geom.stl(vendor(f)), m_shell)
        m, n = printed_mass(mesh)
        add(Part(pid, name, "shell", "head", mesh, src, "PLA (printed)", True, ex, 150, m, n, **kw))

    # ---------------------------------------------------------- visor: the kit's, on Hunter's or Anderson's drive
    from assemblies.hunter_head import visor as V_
    from assemblies.kit.assembly import kit_head_parts
    from r3xmech.meshes import load_file

    kit = []
    for kid, kfile, kT, klink, _ in kit_head_parts(HEAD_ORIGIN_Y):
        if kid in V_.KIT_VISOR + V_.KIT_EARS + V_.KIT_FACE:
            km = load_file(str(kfile)).copy()
            km.apply_transform(kT)
            kit.append((kid, km, "visor" if kid in V_.KIT_VISOR else "head", Path(kfile).name))
    visor_pivot = V_.pivot_of(next(m for k, m, _, _ in kit if k == "h_v_4"))
    direct = PARAMS["visor_drive"] == "hunter_direct"
    if PARAMS["visor_drive"] not in ("hunter_direct", "anderson_linkage"):
        raise ValueError(f"visor_drive {PARAMS['visor_drive']!r}: hunter_direct | anderson_linkage")
    if direct:
        vparts, vlay = V_.direct_parts(kit, visor_pivot, next(p for p in parts if p.id == "mount_plate").mesh,
                                       PARAMS["visor_arm_thickness"])
        bad = [s for s, L in vlay.items() if not L["ok"]]
        if bad:
            raise RuntimeError(f"Hunter's visor mount cannot reach the plate holes on side(s) {bad}: {vlay}")
    else:
        vparts = V_.fixed_parts(kit, visor_pivot)
    for p in vparts:
        add(p)
    tip_head = next(p for p in parts if p.id == "h_v_1").mesh.centroid
    shim = V_.mouth_shim_part({p.id: p for p in parts})
    if shim is not None:
        add(shim)

    asm.parts = parts

    # ---------------------------------------------------------- joints
    asm.joints = [
        Joint("head_tilt", "Head tilt (nod)", "revolute", "neck", "cross", (0, 0, 0), (1, 0, 0),
              PARAMS["tilt_limits"], "deg", "head_tilt", plims.get("head_tilt"),
              {"kind": "push_rod_pair", "servos": ["servo_l", "servo_r"], "linkages": ["rod_l", "rod_r"],
               "gear_ratio": None,
               "note": "The horns turn opposite ways (mirror images). Tilt axis = the U-joint bearings."},
              {"how": "Plate level, both servos at their centre pulse with the horns square to the rods.",
               "step": "s08"}),
        Joint("head_roll", "Head roll", "revolute", "cross", "head", (0, 0, 0), (0, 0, 1),
              PARAMS["roll_limits"], "deg", "head_roll", plims.get("head_roll"),
              {"kind": "push_rod_pair", "servos": ["servo_l", "servo_r"], "linkages": ["rod_l", "rod_r"],
               "gear_ratio": None,
               "note": "Both horns turn the same way. Roll axis = the pillow-block bearings."},
              {"how": "Same as head_tilt: one servo-centring sets both.", "step": "s08"},
              inferred=True, inferred_note="Roll range is not stated by the sources; +/-12 deg (the profile's hard range) "
                                           "sits inside the first contact at +/-17."),
        Joint("visor", "Visor (the kit's, direct on Hunter's two servos)", "revolute", "head", "visor",
              tuple(visor_pivot), (1, 0, 0), PARAMS["visor_limits"], "deg", "visor", plims.get("visor"),
              {"kind": "direct", "servos": ["visor_servo_l", "visor_servo_r"], "linkages": [], "gear_ratio": 1.0,
               "servo_deg_per_unit": 1.0,
               "servo_deg_per_unit_by_servo": {"visor_servo_l": 1.0, "visor_servo_r": -1.0},
               "note": "Hunter: one standard servo on each side on the visor axis, its horn bolted to his arm: 1:1. "
                       "The two face opposite ways, so for one visor move they turn opposite ways: visor_servo_l +1, "
                       "visor_servo_r -1 servo deg per visor deg (+ = right-hand about the servo's own output, which "
                       "points outboard; `gears` carries each). servo_deg_per_unit is the left's."},
              {"how": "Visor at the bottom of its flap with both servos at their centre pulse.", "step": "s12"},
              inferred=True, inferred_note="Hunter's servos and mount from his files and reply; the horn and the arm's "
                                           "thickness are inferred (see the parts).")
        if direct else
        Joint("visor", "Visor (the kit's, on Anderson's push rod)", "revolute", "head", "visor", tuple(visor_pivot),
              (1, 0, 0), PARAMS["visor_limits"], "deg", "visor", plims.get("visor"),
              {"kind": "push_rod", "servos": ["visor_servo"], "linkages": ["rod_visor"], "gear_ratio": None,
               "note": "Anderson: 25 mm horn, 58 mm rod, 18 mm tab on a split axle (stub axles in our brackets)."},
              {"how": "Visor at the bottom of its flap with the servo at its centre pulse.", "step": "s12"},
              inferred=True, inferred_note="The drive's place on Hunter's head is chosen by design_drive (visor.py)."),
    ]
    asm.notes = [
        "Provides head_tilt, head_roll and visor from the profile.",
        "head_pan is not in this mechanism: the neck coupler rides the neck tube, which turns (R-3X neck gear).",
        "head_lift is not in this mechanism: the coupler is fixed on the neck tube. With a lift, the whole "
        "head moves on the tube; nothing here changes.",
        f"Shell fit: 6 insert holes, rms {fit['hole_rms_mm']} mm; boss tops on a plane tilted {fit['pitch_deg']} deg "
        f"(rms {fit['boss_plane_rms_mm']} mm), which the head's up follows; the shell is yawed {fit['yaw_deg']} deg in its "
        f"export. Head frame origin in shell coordinates: {fit['origin_in_shell']}.",
        f"Mount plate STL V4 vs the STEP's plate: bounds within {dev:.2f} mm.",
        "Profile actuators drive head_tilt and head_roll on separate channels; this mech mixes both on its "
        "two servos (tilt = opposite, roll = same direction), so the actuator map needs a mixer.",
    ]

    asm.visor_tip = tip_head  # for the torque check's assumed brow load
    asm.clamp_leaves = clamp_leaves()
    asm.horn_holes = (PARAMS["horn_hole_mm"],)
    asm.fit_gimbal_limits = PARAMS["fit_gimbal_limits"]
    asm.notes += add_hardware(asm, fit, {"pivot": visor_pivot})
    if not direct:
        # the visor drive: Anderson's parts, placed where every test passes
        pick, table = V_.design_drive(asm, visor_pivot, PARAMS["visor_limits"])
        if pick is None:
            raise RuntimeError("no visor drive placement passes: " + str(table[:5]))
        vparts, vlk, vgeo = V_.drive_parts(asm, visor_pivot, pick)
        # the axles and hubs again, the D flat now along the chosen lever
        redo = {p.id: p for p in V_.axle_and_hub_parts(visor_pivot, vgeo["lev"], kit)}
        asm.parts = [redo.get(p.id, p) for p in asm.parts]
        asm.parts += vparts
        asm.parts.append(V_.cradle_part(asm, vgeo))
    # the design default: a clearance channel in the shells along the kit visor's sweep (the
    # parametric shells can take the same envelopes as `clearance_cuts`; exported for them)
    if PARAMS["visor_channel_mm"]:
        byid = {p.id: p for p in asm.parts}
        key = _cache_key("visor-channel", PARAMS["visor_channel_mm"], PARAMS["visor_limits"], visor_pivot.round(3).tolist(),
                         sorted(mesh_hash(byid[k].mesh) for k in V_.KIT_VISOR + V_.CHANNEL_SHELLS + ("visor_arm_l", "visor_arm_r")
                                if k in byid))
        envs = [trimesh.Trimesh(v, f, process=False) for v, f in cached(key, lambda: [
            (np.asarray(e.vertices), np.asarray(e.faces))
            for e in V_.visor_channel(byid, visor_pivot, PARAMS["visor_limits"], PARAMS["visor_channel_mm"])])]
        out_dir = MECH / "out" / "hunter_head" / "clearance"
        out_dir.mkdir(parents=True, exist_ok=True)
        shell_files = []
        for k, e in enumerate(envs):
            e.export(out_dir / f"visor_channel_{k + 1}.stl")  # head frame
            es = e.copy()
            es.apply_transform(np.linalg.inv(m_shell))
            # the shells' frame, the content hash in the name (the parametric shells cache their cut on it)
            fp = out_dir / f"visor_channel_{k + 1}_{mesh_hash(e)[:8]}_shell_frame.stl"
            if not fp.exists():
                es.export(fp)
            shell_files.append(fp)
        cut_note = []
        for pid in V_.CHANNEL_SHELLS:
            p = byid.get(pid)
            if p is None or not envs:
                continue
            hit = [k for k, e in enumerate(envs)
                   if np.all(e.bounds[0] <= p.mesh.bounds[1]) and np.all(e.bounds[1] >= p.mesh.bounds[0])]
            if not hit:
                continue
            before = float(p.mesh.volume)
            how = "parametric clearance_cuts"
            try:  # the shell's own CAD cut (parts/head: clearance_cuts, cached there as BREP)
                cut, _, _ = remodel(pid, m_shell, pid, **SHELL_PARAMS.get(pid, {}),
                                    clearance_cuts=tuple(str(shell_files[k]) for k in hit))
                ok = True
            except Exception as ex:  # a mesh boolean if the model cannot take the cut
                how = f"mesh boolean ({type(ex).__name__})"
                vf = cached(_cache_key("channel-cut", mesh_hash(p.mesh), sorted(mesh_hash(envs[k]) for k in hit)),
                            lambda: (lambda r: (np.asarray(r[0].vertices), np.asarray(r[0].faces), r[1]))(
                                V_.cut_meshes(p.mesh, [envs[k] for k in hit])))
                cut, ok = trimesh.Trimesh(vf[0], vf[1], process=False), vf[2]
            if ok:
                p.mesh = cut
                p.source["clearance_cuts"] = {"visor_channel_mm": PARAMS["visor_channel_mm"], "how": how,
                                              "removed_mm3": round(before - float(cut.volume), 1),
                                              "envelopes": [str(shell_files[k].relative_to(MECH)) for k in hit]}
                cut_note.append(f"{pid} -{before - float(cut.volume):.0f} mm3 ({how})")
        asm.notes.append(f"Visor channel ({PARAMS['visor_channel_mm']} mm round the kit visor's sweep, "
                         f"{PARAMS['visor_limits'][0]:g}..{PARAMS['visor_limits'][1]:g} deg): {len(envs)} envelopes; "
                         + (", ".join(cut_note) or "no shell cut"))
    hw = asm._hw
    hw.p.update({p.id: p for p in asm.parts})
    if direct:
        V_.direct_mates(hw, hw.p, visor_pivot, vlay)
        asm.fasteners, asm.mates = hw.fast, hw.mates
        del asm._hw
        asm.gears = V_.direct_gears(visor_pivot)
        asm.visor_layout = vlay
        L = vlay[1]
        asm.notes.append(
            "Visor: Hunter's direct drive (his reply, 2026-10-02: \"one standard size servo on either side, basically "
            "where your bearings are\"), carrying the kit's brow and axle ends on his DXF arms (the kit side arms' "
            f"outline). Each side, outboard in: axle end at |x| {L['x_ao']:.2f}, arm {L['x_ai']:.2f}..{L['x_ao']:.2f}, "
            f"horn, spline top {L['tip']:.2f}, servo flange on the mount face at {L['x_face']:.2f}; the mount's feet on "
            f"the plate flange (y {L['feet_y']:.2f}) and its slots on the plate's M4 holes (x {L['hole_x']:.2f}, z "
            f"{L['hole_z'][0]:.2f} / {L['hole_z'][1]:.2f}), the hole "
            + ", ".join(f"{vlay[s]['slot_used']:+.2f} mm ({'L' if s > 0 else 'R'})" for s in (1, -1))
            + f" from the slot centre (travel +/-{L['slot_travel']:.2f}). 1:1: visor_servo_l +1, visor_servo_r -1 servo "
              "deg per visor deg.")
    if not direct:
        asm.visor_link = vlk
        asm.visor_geo = vgeo
        asm.joints[-1].drive["gear_ratio"] = pick.get("ratio")
        asm.visor_design = {"pick": pick, "candidates": len(table), "passing": sum(1 for r in table if r.get("ok"))}
        V_.visor_mates(hw, hw.p, {"geo": vgeo, "V": visor_pivot})
        asm.fasteners, asm.mates = hw.fast, hw.mates
        del asm._hw
        asm.linkages.append(vlk)
        asm.notes.append(f"Visor: the kit's (H_V_1..5) and ears on Hunter's head, Anderson's drive: "
                     + (f"re-checked the {asm.visor_design['candidates']} placements that passed before this geometry "
                        f"change, {asm.visor_design['passing']} still pass (WB_RESEARCH=1 for the full search); "
                        if any(r.get("revalidated") for r in table) else
                        f"{asm.visor_design['passing']} of {asm.visor_design['candidates']} placements pass; ")
                     + f"picked lever "
                     f"rest {pick['alpha']} deg, horn {pick.get('phi', 0)} deg off it, rod {pick['theta']} deg, servo orientation {pick['orient']}: "
                     f"{pick['ratio']} visor deg per servo deg mid-range (it varies over the range: a 4-bar), servo travel {pick['travel']} deg, "
                     f"clearance {pick['clear']} mm.")
    add_steps(asm)
    add_bom(asm)
    # seen from outside though not shells (manifest `exposed`): the neck post between the body and the head's
    # shell, and the kit's visor brow, arms and axle ends
    for p in asm.parts:
        if p.id in EXPOSED:
            p.exposed = True
    finishes(asm.parts)
    return asm


def finishes(parts):
    """Paint and print (manifest `finish`, workbench/finish.py). Hunter's photos show his head mech printed
    in black (PLA and PETG, as his files' materials say); his head shells wear the kit head's charcoal, the
    kit's ears, eyes, face and visor their kit paint (assemblies/kit/finish.json), printed as he prints
    (black). The coupler, seen between the neck and the head, takes the neck's dark paint. goBILDA servos
    and hubs in their own colours; the visor servos black (Hunter's photo; Anderson's too), Hunter's visor arms
    the kit side arm's paint, their disc horns bare aluminium."""
    from assemblies.kit.assembly import kit_finish
    from workbench.finish import (BLACK_ANODISED, GOBILDA_SERVO, PETG_BLACK, PLA_BLACK, SERVO_BLACK, apply,
                                  finish)

    table = {"neck_coupler": finish(paint="metal_dark", print=PLA_BLACK)}
    for pid in ("head_bottom", "side_left", "side_right", "head_top"):
        table[pid] = finish(paint="paint_charcoal", print=PLA_BLACK)
    for p in parts:
        kf, _ = kit_finish(p.id) if p.source.get("file", "").startswith("vendor/kit/") else (None, True)
        if kf:  # the kit's paint; printed black, except the eyes' translucent diffusion bulbs
            translucent = kf["print"]["color_name"].startswith("Natural")
            table[p.id] = dict(kf, print=dict(kf["print"] if translucent else PLA_BLACK))
        elif p.cls == "servo":
            table[p.id] = finish(color=GOBILDA_SERVO if "goBILDA" in p.name else SERVO_BLACK)
        elif p.id in ("hub_bottom", "hub_top") or p.id.startswith("horn_hub_"):
            table[p.id] = finish(color=BLACK_ANODISED)
        elif p.id.startswith("visor_arm_"):  # Hunter's arm in the kit side arm's place: the kit arm's paint
            kf, _ = kit_finish("h_v_2")
            table[p.id] = finish(paint=kf["paint"] if kf else "paint_charcoal",
                                 note="Hunter's cut arm, painted as the kit side arm it stands in for")
        elif p.id.startswith("visor_horn_"):
            table[p.id] = finish(color={"color": "#b9bdc3", "color_name": "Bare aluminium"})
        elif p.printed and p.id not in table:
            table[p.id] = finish(print=PETG_BLACK if "PETG" in p.material else PLA_BLACK)
    apply(parts, table)


EXPOSED = {"neck_coupler", "hub_bottom", "hex_shaft", "ujoint", "h_v_1", "h_v_2", "h_v_3", "h_v_4", "h_v_5",
           "visor_arm_l", "visor_arm_r"}


def checks(asm: Assembly):
    """The workbench calls this after build(): the checks with this mech's assumptions."""
    from workbench.checks import run_all

    prof = json.loads(PROFILE.read_text())
    amax = {j["name"]: j.get("a_max", 0) for j in prof["joints"]}
    amax["head_roll"] = amax.get("head_tilt", 0)  # not in the profile: same as tilt
    return run_all(asm, SERVO, amax, {}, EXPLAINED)


def _t(v):
    m = np.eye(4)
    m[:3, 3] = v
    return m


geom_trans = _t


# ------------------------------------------------------------------ fasteners

SHCS = lambda l, thread="M4": {"type": "shcs", "thread": thread, "length_mm": l, "standard": "ISO 4762"}
INSERT = {"type": "insert", "thread": "M4", "length_mm": 6, "od_mm": 6, "note": "6 x 6 mm (BOM)"}
LOCKNUT = {"type": "lock_nut", "thread": "M4", "standard": "ISO 10511"}
WASHER = {"type": "washer", "thread": "M4", "standard": "ISO 7089"}
STD_LEN = [6, 8, 10, 12, 14, 16, 20, 25, 30, 35, 40, 45, 50]


def screw_for(grip: float, engage: float) -> int:
    need = grip + engage
    return next(l for l in STD_LEN if l >= need - 0.5)


def key_of(spec):
    t = spec["type"]
    if t in ("shcs", "bhcs"):
        return f"{t}-{spec['thread']}x{spec['length_mm']:g}"
    if t == "insert":
        return f"insert-{spec['thread']}x{spec['length_mm']:g}"
    return f"{t}-{spec['thread']}"


def fit_1311(inst_1309: trimesh.Trimesh, coupler=None) -> trimesh.Trimesh:
    """Cached by the two meshes' hashes (see _fit_1311)."""
    from workbench.collide import mesh_hash
    from workbench.geom import cached

    global HUB_1311_M
    M, verts, faces = cached(f"fit1311-{mesh_hash(inst_1309)}-{mesh_hash(coupler) if coupler is not None else 0}-2",
                             lambda: (lambda o: (HUB_1311_M, np.asarray(o.vertices), np.asarray(o.faces)))(_fit_1311(inst_1309, coupler)))
    HUB_1311_M = M
    return trimesh.Trimesh(verts, faces, process=False)


def _fit_1311(inst_1309: trimesh.Trimesh, coupler=None) -> trimesh.Trimesh:
    """goBILDA's 1311 (thru-hole) hub, fitted onto the STEP's 1309 instance: the same outer
    cylinder (axis, centre) and clamp clocking. Both are 32 x 10 mm sonic hubs."""
    from workbench.geom import step_leaves

    leaves = step_leaves(MECH / "vendor" / "parts_cad" / "gobilda" / "1311-0016-4008.step", 0.03)
    hub = next(trimesh.Trimesh(v, f) for _, lbl, v, f in leaves if lbl.startswith("1311"))

    def frame(m):
        c = (m.bounds[0] + m.bounds[1]) / 2
        ext = m.bounds[1] - m.bounds[0]
        ax = np.zeros(3)
        ax[int(np.argmin(ext))] = 1.0
        # centre on the 8 mm REX bore (the clamp tab makes the bounding box off-centre)
        k = int(np.argmin(ext))
        sec = m.section(plane_origin=c, plane_normal=ax)
        if sec is not None:
            loops = [e for e in sec.discrete if 3.5 < np.linalg.norm(e - e.mean(0), axis=1).max() < 5.5]
            if loops:
                bc = min(loops, key=lambda e: np.linalg.norm(e.mean(0) - c)).mean(0)
                c = np.array([bc[i] if i != k else c[i] for i in range(3)])
        # clocking: toward the clamp slot, the side where the hub's material is off-centre
        off = m.centroid - c
        off -= ax * (off @ ax)
        x = off / (np.linalg.norm(off) or 1)
        f = np.eye(4)
        f[:3, 0], f[:3, 1], f[:3, 2], f[:3, 3] = x, np.cross(ax, x), ax, c
        return f

    from workbench.geom import sample

    base = frame(inst_1309) @ np.linalg.inv(frame(hub))
    best = None
    for flip in (False, True):
        for k in range(8):  # clocking about the hub axis, and which face is down
            R = trimesh.transformations.rotation_matrix(k * math.pi / 4, [0, 0, 1])
            if flip:
                R = R @ trimesh.transformations.rotation_matrix(math.pi, [1, 0, 0])
            m = frame(inst_1309) @ R @ np.linalg.inv(frame(hub))
            out = hub.copy()
            out.apply_transform(m)
            out.apply_translation([0, inst_1309.bounds[0][1] - out.bounds[0][1], 0])
            P = sample(out, 1.5, 6000)
            pen = int(coupler.contains(P).sum()) if coupler is not None else 0
            # the coupler's 16 mm pattern must show through the hub's thru-holes
            for hx, hz in ((8, 8), (8, -8), (-8, 8), (-8, -8)):
                if geom.ray_depth(out, [hx, out.bounds[0][1] - 5, hz], [0, 1, 0]):
                    pen += 500
            if best is None or pen < best[0]:
                full = trimesh.transformations.translation_matrix([0, inst_1309.bounds[0][1] - (hub.copy().apply_transform(m)).bounds[0][1], 0]) @ m
                best = (pen, out, full)
    global HUB_1311_M
    HUB_1311_M = best[2]
    return best[1]


HUB_1311_M = None  # the fitted 1311's matrix (its own clamp screws follow it)


SERVO_FLANGE = {"holes": [(10 + sx * 24.0, sz * 4.89) for sx in (-1, 1) for sz in (-1, 1)], "bottom_y": -16.9}


def place_visor_mount(mount, m_servo, others, squared=False):
    """Cached by the inputs' hashes (see _place_visor_mount)."""
    from workbench.collide import key_of, mesh_hash
    from workbench.geom import cached

    k = key_of("vmount", mesh_hash(mount), np.asarray(m_servo), [mesh_hash(o.mesh) for o in others], squared, 2)
    v, f, how = cached(k, lambda: (lambda r: (np.asarray(r[0].vertices), np.asarray(r[0].faces), r[1]))(
        _place_visor_mount(mount, m_servo, others, squared)))
    return trimesh.Trimesh(v, f, process=False), how


def _place_visor_mount(mount, m_servo, others, squared=False):
    """The mount's four insert bosses (measured) onto the servo's flange holes (goBILDA 2000:
    48 x 9.8 mm, the same pattern Hunter's plate uses), its face on the flange's underside.
    Every face/flip is tried; the one whose mount overlaps nothing is kept."""
    from workbench.geom import sample

    # square the bracket first: its long side (feet to feet) along X, the slab face normal to Z
    m_sq = np.eye(4) if squared else square_mount(mount)
    orig = mount
    mount = mount.copy()
    mount.apply_transform(m_sq)
    lo, hi = mount.bounds
    zmid = (lo[2] + hi[2]) / 2
    holes = []
    for z in np.linspace(lo[2] + 2, hi[2] - 2, 15):
        found = [c for c, r in geom.section_holes(mount, 2, z, 2.9, 3.2) if c[1] > lo[1] + 10]
        if len(found) == 4 and abs(np.ptp([c[1] for c in found]) - 48) < 1.5:
            holes = found
            zmid = z
            break
    if len(holes) != 4:
        return orig.copy(), "no insert bosses found: left in the file's frame"
    H = np.array(holes)
    faces = []
    for c in H:
        hits = geom.ray_depth(mount, [c[0] + 3.6, c[1], lo[2] - 5], [0, 0, 1])
        faces.append((lo[2] - 5 + hits[0], lo[2] - 5 + hits[-1]) if hits else (lo[2], hi[2]))
    tgt = np.array([(m_servo @ np.array([x, SERVO_FLANGE["bottom_y"], z, 1.0]))[:3] for x, z in SERVO_FLANGE["holes"]])
    nb_pts = [sample(o.mesh, 1.5, 12000) for o in others]
    # order both sets along their long axis, then across
    def order(P, ax_long):
        c = P.mean(0)
        a = (P - c) @ ax_long
        return P[np.lexsort((P @ np.cross(ax_long, np.cross(ax_long, [0, 0, 1.0]) + 1e-9), a))]
    best = None
    for fi in (0, 1):
        z = np.mean([f[fi] for f in faces])
        src = H.copy()
        src[:, 2] = z
        for perm in ([0, 1, 2, 3], [1, 0, 3, 2], [2, 3, 0, 1], [3, 2, 1, 0], [0, 2, 1, 3], [2, 0, 3, 1], [1, 3, 0, 2], [3, 1, 2, 0]):
            S = src[np.argsort(src[:, 1] * 100 + src[:, 0])][perm]
            T = tgt[np.argsort([np.round(t @ [0, 1, 0], 0) * 100 + t @ [0, 0, 1] for t in tgt])]
            cs, ct = S.mean(0), T.mean(0)
            U, _, Vt = np.linalg.svd((S - cs).T @ (T - ct))
            D = np.diag([1, 1, np.sign(np.linalg.det(Vt.T @ U.T))])
            R = Vt.T @ D @ U.T
            M = np.eye(4)
            M[:3, :3], M[:3, 3] = R, ct - R @ cs
            rms = float(np.sqrt((((S @ R.T + M[:3, 3]) - T) ** 2).sum(1).mean()))
            if rms > 1.5:
                continue
            M = M @ m_sq
            placed = orig.copy()
            placed.apply_transform(M)
            # the mount is closed: count the neighbours' surface points inside it
            inside = sum(int(placed.contains(Q).sum()) for Q in nb_pts)
            score = (inside, rms)
            if best is None or score < best[0]:
                best = (score, placed, f"insert bosses on the flange holes, rms {rms:.2f} mm, face {fi}, "
                                       f"{inside} sample points inside neighbours")
    if best is None:
        return orig.copy(), "no face/flip matched the flange holes"
    return best[1], best[2]


def clamp_leaves():
    """The STEP's sonic-hub clamp screws: (hub, head point, direction) measured from the leaves."""
    leaves = geom.step_leaves(vendor("Head Joint Asm.step"))
    clamps = [xf(trimesh.Trimesh(v, fc), M_STEP) for _, lbl, v, fc in leaves if lbl.startswith("2800-0004-0014")]
    clamps = [c for c in clamps if c.centroid[1] > 0]  # the top hub's, from the STEP
    if HUB_1311_M is not None:  # the bottom (1311) hub's own clamp screws, from goBILDA's file
        lv = geom.step_leaves(MECH / "vendor" / "parts_cad" / "gobilda" / "1311-0016-4008.step", 0.03)
        clamps += [xf(trimesh.Trimesh(v, fc), HUB_1311_M) for _, lbl, v, fc in lv if lbl.startswith("2800")]
    out = []
    for cm in sorted(clamps, key=lambda m: (m.centroid[1], m.centroid[0])):
        c, a, length, s = leaf_axis(cm.vertices)
        v = cm.vertices - c
        rad = lambda p: np.linalg.norm(p - np.outer(p @ a, a), axis=1).max()
        if rad(v[s > s.max() - 3]) > rad(v[s < s.min() + 3]):
            a = -a
        # the head's bearing face: 4 mm (head height) in from the head end
        out.append(("hub_bottom" if cm.centroid[1] < 0 else "hub_top", c - a * (length / 2) + a * 4.0, a))
    return out


# ------------------------------------------------------------------ steps

def add_steps(asm: Assembly):
    from assemblies.hunter_head import visor as V_

    F = lambda prefix: [f.id for f in asm.fasteners if f.id.startswith(prefix)]
    direct = any(p.id == "visor_mount_l" for p in asm.parts)
    n_vm = len(F("ins_vmount"))
    mount_screws = [f.id for f in asm.fasteners
                    if f.id.startswith("scr_plate_bottom") and any(j.startswith("visor_mount") for j in f.joins)]
    asm.steps = [
        Step("s01", "Heat-set the inserts", [], F("ins_"), context=["head_bottom", "mount_plate"],
             tools=["soldering iron with an M4 insert tip (~220 C for PLA)", "flat plate to seat them level"],
             notes=["Do them all before anything else: 6 in the head bottom's bosses, 8 in the plate's servo bosses"
                    + (f", {n_vm} in the two visor servo mounts' bosses (4 each)." if n_vm else "."),
                    "Press straight and stop flush; a proud insert tilts the plate or the servo."]),
        Step("s02", "Neck coupler", ["neck_coupler"], F("ins_coupler") + F("nut_coupler"),
             tools=["3 mm hex key"],
             notes=["Print the 26 mm-bore coupler for Anderson's neck tube (Hunter's own neck takes 32).",
                    "Heat-set an M4 insert in its side hole: the set screw that clamps the tube goes in there when "
                    "the head goes on the droid.",
                    "Press an M4 nut into each of the four hex pockets under the hub plate (from the bore side), "
                    "flats toward the centre; the hub screws in s04 thread into them."]),
        Step("s03", "Bottom sonic hub and the hex post", ["hub_bottom", "hex_shaft"],
             F("clamp_hub_bottom"), context=["neck_coupler"],
             tools=["3 mm hex key", "Loctite 243"],
             notes=["Hub onto the coupler's 16 mm pattern (4 x M4 into the printed holes: snug, not tight).",
                    "Push the hex shaft fully into the hub and clamp both screws, Loctite on the clamps."],
             inferred=True, inferred_note="Thru-hole vs threaded hub assignment and screw length inferred."),
        Step("s04", "U-joint onto the hub", ["ujoint", "uj_bearing_l", "uj_bearing_r"],
             F("scr_hub_coupler"),
             context=["hex_shaft", "hub_bottom", "neck_coupler"], tools=["3 mm hex key"],
             notes=["Slide the U-joint body down the hex post onto the hub; 4 x M4 down through its pattern "
                    "mount and the thru-hole hub, through the coupler's plate into its trapped nuts."]),
        Step("s04b", "The cross into the U-joint (tilt axis)", ["custom_joint_piece"],
             F("pin_tilt") + F("wash_tilt"), context=["ujoint", "hex_shaft"],
             tools=["3 mm hex key", "Loctite 243"],
             notes=["Pivot the cross in the U-joint's bearings with a washer each side as a spacer: it must swing "
                    "freely with no end play."],
             inferred=True, inferred_note="Pivot screw length and washer count are inferred from the BOM note."),
        Step("s05", "Pillow blocks onto the cross (roll axis), then onto the plate",
             ["pillow_f", "pillow_b", "pillow_bearing_f", "pillow_bearing_b", "mount_plate"],
             F("pin_roll") + F("wash_roll") + F("scr_pillow"), context=["custom_joint_piece"],
             tools=["3 mm hex key", "Loctite 243"],
             notes=["Washers between each pillow block and the cross (BOM: 'washers for spacers').",
                    "Bolt the plate down onto both pillow blocks from above; check the plate rolls freely."],
             inferred=True, inferred_note="Order (blocks to cross before the plate) inferred from access."),
        Step("s06", "Top sonic hub", ["hub_top"], F("clamp_hub_top"), context=["hex_shaft", "mount_plate"],
             tools=["3 mm hex key", "Loctite 243"],
             notes=["Threaded hub on the top of the post, face up, one pattern hole toward each servo.",
                    "Set its height so the balls sit level with the horn balls (both ~67 mm above the gimbal centre)."],
             inferred=True, inferred_note="Hub clocking and height are set by the rod geometry, not stated."),
        Step("s07", "Servos into the plate", ["servo_l", "servo_r"], F("scr_servo"), context=["mount_plate"],
             tools=["2.5 mm hex key (M4 low heads)"],
             notes=["Output splines up, toward the back corners.",
                    "Cable routing: run both servo leads down the back of the plate, clear of the roll arc, and "
                    "out through the neck with a service loop - the head tilts and rolls around the post."],
             inferred=True, inferred_note="Cable route inferred; the sources do not show it."),
        Step("s08", "Centre both servos, then fit the horns",
             ["horn_hub_l", "horn_hub_r", "horn_arm_l", "horn_arm_r"], F("scr_arm_"),
             tools=["servo tester or the r3x bench (1500 us)", "2.5 mm hex key"],
             notes=["Power each servo and send its centre pulse (1500 us) BEFORE fitting the horn. This is what "
                    "defines head_tilt = 0 and head_roll = 0 in real life.",
                    f"With the plate held level, fit the 1906 hub on the spline and the arm at the angle the step "
                    f"shows ({asm.linkage_design['pick']['phi']:g} deg from the servo's long side; the 25T spline "
                    f"clocks in 14.4 deg steps). 4 x M4 through the arm into the hub.",
                    f"Cut the arm just past the {asm.linkages[0].radius:g} mm hole (it clears the shell).",
                    "Record the centre pulse in the robot profile (actuator center_us) as the zero."],
             joint="head_tilt", pose={"head_tilt": 0, "head_roll": 0},
             inferred=True, inferred_note="1500 us is goBILDA's centre; the horn angle comes from the rod geometry."),
        *([Step("s09", "Head bottom onto the plate", ["head_bottom"],
                [f for f in F("scr_plate_bottom") if f not in mount_screws], context=["mount_plate"],
                tools=["3 mm hex key"],
                notes=["Feed the servo leads through the neck opening first.",
                       "The two middle M4 down through the plate's flange into the head bottom's inserts now: the "
                       "visor servo mounts stand over them next."]),
           Step("s09b", "Hunter's visor servo mounts on the plate flange", ["visor_mount_l", "visor_mount_r"],
                mount_screws, context=["mount_plate", "head_bottom"],
                tools=["3 mm hex key"],
                notes=["Each mount's slotted feet over the plate's front and back flange holes, flange face "
                       "outboard: 2 x M4 per side down through the foot and the plate into the head bottom's "
                       "inserts. Finger-tight: the slots set the mount's place along the axis in s12b."])]
          if direct else
          [Step("s09", "Head bottom onto the plate (the visor brackets under the side screws)",
             ["head_bottom", "visor_bracket_l", "visor_bracket_r", "visor_bearing_l", "visor_bearing_r"],
             F("scr_plate_bottom"), context=["mount_plate"],
             tools=["3 mm hex key"],
             notes=["6 x M4 down through the plate's flange into the head bottom's inserts; on each side the three "
                    "screws also clamp a visor bracket's foot (press its F6001ZZ bearing in first).",
                    "Feed the servo leads through the neck opening first."])]),
        Step("s10", "Push rods", ["rod_l", "rod_r", "link_a_l", "link_b_l", "link_a_r", "link_b_r"],
             F("stud_") + F("nut_ball") + F("wsh_ball"), context=["horn_arm_l", "horn_arm_r", "hub_top"],
             tools=["3 mm hex key", "7 mm spanner", "calipers", "Loctite 243"],
             notes=[f"Thread a ball end onto each end of the 50 mm rod until the ball centres are "
                    f"{asm.linkages[0].rod_length:.1f} mm apart ({asm.linkage_design['pick']['engage']:g} mm of "
                    f"thread in each end, both rods). Loctite the threads.",
                    f"Horn ends: screw through the ball, a washer and the arm's {asm.linkages[0].radius:g} mm hole, "
                    f"ball {asm.linkage_design['pick']['ball']} the arm, lock nut on the other side.",
                    "Post ends: screw through the ball into the threaded top hub.",
                    "With the servos still at centre the plate must sit level: adjust a rod half a turn if not."],
             pose={"head_tilt": 0, "head_roll": 0},
             inferred=True, inferred_note="Rod length is computed from the inferred horn radius and hub holes."),
        *(_direct_visor_steps(asm, F) if direct else _anderson_visor_steps(asm, F)),
        Step("s13", "Sides and top", ["side_left", "side_right", "head_top"], [],
             unplaced=[{"key": "pin-align", "spec": {"type": "pin", "note": "alignment pins or filament"},
                        "count": 0, "note": "alignment holes on the side pieces: pins + CA glue (count on the parts)"}],
             tools=["CA glue or 2-part epoxy"],
             notes=["Dry-fit first: sweep the head through tilt and roll by hand and look for rubbing (Checks tab).",
                    "The top lifts off for service; glue only the sides to the bottom if you glue at all."],
             inferred=True, inferred_note="The sources give alignment holes but no fastener for the shell."),
        Step("s13b", "Kit ears", ["h_le_1", "h_le_2", "h_re_1", "h_re_2"], [], context=["side_left", "side_right"],
             tools=["CA glue"], notes=["The kit's ear cups over the visor axle ends, on Hunter's side pieces."]),
        Step("s13c", "Kit face: plate, eyes, mouth, headband",
             [*(["mouth_shim"] if any(p.id == "mouth_shim" for p in asm.parts) else []), *V_.KIT_FACE],
             [], context=["head_bottom", "side_left", "side_right", "head_top"],
             tools=["CA glue"],
             notes=["Glue the eyes and the mic into the face plate as the kit does, then the plate into the head's front.",
                    "The mouth glues to Hunter's head bottom (our shim goes under it only if the gap is over 0.5 mm).",
                    "Headband over the top, as the kit's goes on."]),
    ]
    for s in asm.steps:
        s.text = (direct and _STEP_TEXT.get(f"{s.id}_direct")) or _STEP_TEXT.get(s.id, "")
        if s.id == "s10":
            s.text = (f"Set both push rods to {asm.linkages[0].rod_length:.1f} mm between ball centres and fit them from "
                      "each horn to the top hub. The plate must sit level with both servos centred.")


def _anderson_visor_steps(asm, F):
    """Anderson's visor drive (visor_drive = "anderson_linkage")."""
    return [
        Step("s11", "Visor servo in its cradle, centred, horn on", ["visor_servo_cradle", "visor_servo", "visor_horn"], [],
             unplaced=[{"key": "shcs-M3x8", "spec": SHCS(8, "M3"), "count": 4, "note": "servo flange into the cradle's tap holes"},
                       {"key": "shcs-M4x10", "spec": SHCS(10), "count": 2, "note": "cradle leg into the plate (holes to add to the plate)"}],
             tools=["servo tester or the r3x bench", "2.5 mm hex key"],
             notes=["Centre the visor servo (1500 us) before fitting Anderson's 25 mm horn: that is visor = 0, the "
                    "visor at the bottom of its flap.",
                    f"Horn angle at centre: along the lever ({asm.visor_design['pick']['alpha']} deg in the side view)."],
             joint="visor", pose={"visor": 0}, context=["mount_plate"],
             inferred=True, inferred_note="Where the visor servo sits on Hunter's head is chosen by the drive search."),
        Step("s12a", "Kit visor: glue it up, our hubs onto its arms", ["h_v_1", "h_v_2", "h_v_3", "visor_hub_l",
             "visor_hub_r"], F("ins_vhub") + F("scr_vhub"), context=["visor_bracket_l", "visor_bracket_r"],
             tools=["soldering iron (M3 inserts)", "2.5 mm hex key", "CA glue (the kit visor's joints)"],
             notes=["Glue the brow (H_V_1) to the side arms (H_V_2/3), as the kit's visor goes together.",
                    "Heat-set four M3 inserts in each hub's outboard face.",
                    "Hub against each arm's inboard face, four M3 screws from the arm's outboard face into the "
                    "inserts (the axle ends H_V_4/5 go on over the heads later)."],
             inferred=True, inferred_note="The kit's own hardware for these holes is not in the kit files; the "
                                          "hub takes them as M3 clearance holes."),
        Step("s12", "Visor axles, Anderson's linkage, the axle ends", ["visor_axle_l", "visor_axle_r", "visor_tab",
             "visor_push_rod", "h_v_4", "h_v_5"],
             F("pin_visor"), context=["visor_bracket_l", "visor_bracket_r", "visor_servo", "h_v_1"],
             unplaced=[{"key": "grub-M3x4", "spec": {"type": "set_screw", "thread": "M3", "length_mm": 4}, "count": 2,
                        "note": "hub set screws onto each axle's D flat"}],
             tools=["1.5 mm hex key", "CA glue"],
             notes=["Lower the visor between the brackets, hubs outboard. Slide each stub axle in from inside the "
                    "head, through its bearing, into its hub (D flat to the hub's flat); tighten the set screws.",
                    "The left axle takes the rod tab on its 8 mm section first: tab over the flat, the 3 x 16 mm "
                    "cross pin through the tab's jaws and the axle.",
                    "Pin the 58 mm push rod to the tab and to the horn (3 mm pins).",
                    "Glue the axle ends H_V_4/5 over the arms (over the hub screws' heads)."],
             joint="visor", pose={"visor": 0}),
    ]


def _direct_visor_steps(asm, F):
    """Hunter's visor: the servos into their mounts, centred, horns on, his arms with the kit brow onto the
    horns, the kit axle ends over the screws."""
    L = asm.visor_layout[1]
    return [
        Step("s11", "Visor servos into Hunter's mounts", ["visor_servo_l", "visor_servo_r"], F("scr_vservo"),
             context=["visor_mount_l", "visor_mount_r", "mount_plate"],
             tools=["3 mm hex key (M4 low heads)"],
             notes=["Each servo goes in from outboard, spline down and pointing out of the head, its flange on the "
                    "mount's four bosses: 4 x M4 low heads into the mount's inserts.",
                    "Run both leads down to the plate and out through the neck with the gimbal servos' leads."]),
        Step("s12", "Centre both visor servos, horns on", ["visor_horn_l", "visor_horn_r"], F("scr_vhorn"),
             context=["visor_servo_l", "visor_servo_r"],
             tools=["servo tester or the r3x bench (1500 us)", "2 mm hex key"],
             notes=["Send both visor servos their centre pulse (1500 us) BEFORE the horns go on: that is visor = 0, "
                    "the visor at the bottom of its flap, on both sides at once.",
                    "Disc horn on each spline with one of its tapped holes square to the plate (the arm's four holes "
                    "line up with it at visor 0), then the servo's own M3 centre screw.",
                    "The two servos face opposite ways: for one visor move, the left turns + and the right - "
                    "(each about its own output). Mirror the right channel in the controller."],
             joint="visor", pose={"visor": 0},
             inferred=True, inferred_note="The horn is inferred from the arm's hole pattern."),
        Step("s12b", "Hunter's arms onto the horns, with the kit brow", ["h_v_1", "visor_arm_l", "visor_arm_r"],
             F("scr_varm"), context=["visor_horn_l", "visor_horn_r", "visor_mount_l", "visor_mount_r"],
             tools=["2 mm hex key", "CA glue or epoxy (the brow)"],
             notes=["Glue the kit brow (H_V_1) to both arms where the kit's own side arms (H_V_2/3, not used here) "
                    "would meet it.",
                    "Lower the visor over the head, each arm's centre hole over its horn: 4 x M3 flat heads per side "
                    "through the arm's countersinks into the horn.",
                    f"Slide each mount along its slots until the arm's outer face is at {L['x_ao']:.1f} mm from the "
                    "centre plane (the kit axle end's face; both sides equal), then tighten the plate screws.",
                    "Hunter's arm tip hole (for his own brow) is not used with the kit brow."],
             joint="visor", pose={"visor": 0}),
        Step("s12c", "Kit axle ends over the arms", ["h_v_4", "h_v_5"], [],
             context=["visor_arm_l", "visor_arm_r", "h_v_1"], tools=["CA glue"],
             notes=["Glue each axle end flat on its arm's outer face, over the screw heads (flush in their "
                    "countersinks)."]),
    ]


# The Instructions' sentence per step (SCHEMA.md "Step" `text`): imperative, one or two sentences.
_STEP_TEXT = {
    "s01": "Heat-set every insert before anything else: six M4 in the head bottom's bosses, eight M3 in the plate's "
           "servo bosses. Press them straight and stop flush.",
    "s02": "Heat-set an M4 insert in the neck coupler's side hole and press an M4 nut into each hex pocket under "
           "its hub plate.",
    "s03": "Screw the thru-hole sonic hub to the coupler, push the hex shaft fully in and clamp it with two M4 screws.",
    "s04": "Slide the U-joint down the hex shaft onto the hub and bolt it through to the coupler's trapped nuts with "
           "four M4.",
    "s04b": "Pivot the cross in the U-joint's bearings, a washer each side. It must swing freely with no end play.",
    "s05": "Bolt the two pillow blocks to the cross, then the mount plate down onto them. Check the plate rolls "
           "freely.",
    "s06": "Clamp the threaded sonic hub on top of the post, one pattern hole toward each servo.",
    "s07": "Screw both servos into the plate, splines up toward the back corners, with eight M3.",
    "s08": "Centre both servos at 1500 µs, then fit each hub and control arm with the plate held level. This sets "
           "tilt and roll zero.",
    "s09": "Feed the servo leads through the neck, then screw the head bottom to the plate with six M4. The side "
           "screws also clamp the visor brackets.",
    "s10": "Set both push rods to 74.3 mm between ball centres and fit them from each horn to the top hub. The "
           "plate must sit level with both servos centred.",
    "s11": "Centre the visor servo, fit its horn along the lever and mount the servo in its cradle.",
    "s11_direct": "Fit each visor servo into Hunter's mount from outboard, spline down and out, with four M4 low heads.",
    "s12_direct": "Centre both visor servos at 1500 µs, then fit a disc horn on each spline with the servo's centre "
                  "screw. This sets visor zero.",
    "s12b": "Glue the kit brow to Hunter's two arms, bolt each arm to its horn with four M3 flat heads, then slide the "
            "mounts along their slots until both arms sit at the axle ends' faces and tighten the plate screws.",
    "s12c": "Glue the kit's axle ends flat on the arms' outer faces.",
    "s09_direct": "Feed the servo leads through the neck, then screw the head bottom to the plate with the two middle "
                  "M4.",
    "s09b": "Stand Hunter's two visor servo mounts on the plate flange, flange faces outboard, and screw each down "
            "through its slotted feet with two M4, finger-tight.",
    "s12a": "Glue the visor brow to its side arms, then screw a hub to each arm's inboard face.",
    "s12": "Slide the stub axles through the bearings into the visor hubs and pin the push rod between the tab and "
           "the horn. Glue the axle ends over the arms.",
    "s13": "Dry-fit both sides and the top, then sweep tilt and roll by hand to check for rubbing.",
    "s13b": "Glue the kit's ears over the visor axle ends.",
    "s13c": "Glue the eyes and mic into the face plate and the plate into the head. Fit the mouth and the headband.",
}


# ------------------------------------------------------------------ BOM (the PDF, plus what the geometry adds)

def add_bom(asm: Assembly):
    count: dict[str, list] = {}
    for f in asm.fasteners:
        count.setdefault(f.key, []).append(f)
    lines = [
        BomLine("gobilda-4005-uj", "Universal joint, pattern mount (4005-0016-0001)", 1, "hardware",
                source=GOBILDA + "4005-series-pattern-mount-universal-joint-16-1/", parts=["ujoint"]),
        BomLine("gobilda-1311-hub", "Sonic hub, thru-hole, 8 mm REX (1311)", 1, "hardware",
                source=GOBILDA + "1311-series-thru-hole-sonic-hub-8mm-rex-bore/", parts=["hub_bottom"]),
        BomLine("gobilda-1309-hub", "Sonic hub, threaded, 8 mm REX (1309)", 1, "hardware",
                source=GOBILDA + "1309-series-sonic-hub-8mm-rex-bore/", parts=["hub_top"]),
        BomLine("gobilda-2000-servo", SERVO["model"], 2, "servo",
                source=GOBILDA + "2000-series-dual-mode-servo-25-2-torque/", parts=["servo_l", "servo_r"]),
        BomLine("gobilda-1602-pillow", "Pillow block, 6 mm bore, 1-side 2-post, 24 mm (1602)", 2, "hardware",
                source=GOBILDA + "6mm-bore-1-side-2-post-pillow-block-24mm-height/", parts=["pillow_f", "pillow_b"]),
        BomLine("gobilda-1516-shaft", "8 mm REX hex standoff/shaft, M4, 96 mm (1516), 4-pack", 1, "hardware",
                source=GOBILDA + "1516-series-8mm-rex-standoff-m4-x-0-7mm-threads-96mm-length-4-pack/", parts=["hex_shaft"]),
        BomLine("gobilda-1906-hub", "Lightweight servo hub, 25T, 32 mm (1906)", 2, "hardware",
                source=GOBILDA + "1906-series-lightweight-servo-hub-25-tooth-spline-32mm-diameter/", parts=["horn_l", "horn_r"]),
        BomLine("gobilda-arm-48", f"Plastic hub-mount control arm, 48 mm (cut down past the {asm.linkages[0].radius:g} mm "
                "hole)", 2, "hardware",
                source=GOBILDA + "plastic-hub-mount-control-arm-48mm-length/", parts=["horn_arm_l", "horn_arm_r"],
                inferred=True, inferred_note="The hole (and so the cut) from Hunter's top-down photo: both ball studs "
                                             "30.5-31.5 mm from the hub, scaled by the arm's 11.31 mm hole ring."),
        BomLine("gobilda-2913-ball", "Ball linkage, female M4, 24.1 mm (2913), 2-pack", 2, "hardware",
                source=GOBILDA + "2913-series-steel-ball-linkage-female-m4-x-0-7mm-24-1mm-length-2-pack/",
                parts=[f"rod_{s}_end_{e}" for s in "lr" for e in "ab"]),
        BomLine("gobilda-2808-rod", "Threaded rod, M4, 50 mm (2808), 2-pack", 1, "hardware",
                source=GOBILDA + "2808-series-stainless-steel-threaded-rod-m4-x-0-7mm-50mm-length-2-pack/",
                parts=["rod_l", "rod_r"]),
        BomLine("bearing-flanged-4mm", "Flanged bearing (in the U-joint, 1600-0410-0004)", 2, "bearing",
                parts=["uj_bearing_l", "uj_bearing_r"], inferred=True, inferred_note="Comes with the U-joint (STEP), not a BOM line."),
        BomLine("bearing-6mm", "Flanged bearing, 6 mm bore (in the pillow blocks, 1600-0515-0006)", 2, "bearing",
                parts=["pillow_bearing_f", "pillow_bearing_b"], inferred=True, inferred_note="Comes with the pillow blocks (STEP)."),
        BomLine("loctite-243", "Loctite (threadlocker, medium)", 1, "hardware"),
    ]
    if any(p.id == "visor_servo_l" for p in asm.parts):
        lines += [
            BomLine("servo-visor", "Visor servo, standard size (one each side)", 2, "servo",
                    parts=["visor_servo_l", "visor_servo_r"], inferred=True,
                    inferred_note="Not in Hunter's BOM; his reply (2026-10-02): one standard size servo on either side. "
                                  "Make not stated (black cases in his photo)."),
            BomLine("horn-disc-25t", "25T aluminium disc servo horn, 4 x M3 on a 10 mm square", 2, "hardware",
                    parts=["visor_horn_l", "visor_horn_r"], inferred=True,
                    inferred_note="Inferred from his visor arm's hole pattern (DXF) and the round horn in his photo."),
            BomLine("visor-arm-dxf", f"Visor arm, cut from {PARAMS['visor_arm_thickness']:g} mm aluminium (Visor Arm DXF)",
                    2, "hardware", parts=["visor_arm_l", "visor_arm_r"], spec={"file": "Visor Arm DXF.dxf"},
                    inferred=True, inferred_note="Material and thickness not in the DXF."),
        ]
    else:
        lines.append(BomLine("servo-visor", "Visor servo (standard size)", 1, "servo", parts=["visor_servo"],
                             inferred=True, inferred_note="Not in the BOM; the visor needs one."))
    for p in asm.parts:
        if p.printed:
            lines.append(BomLine(f"print-{p.id}", f"Print: {p.name}", 1, "printed", parts=[p.id],
                                 spec={"file": p.source.get("file")}, inferred=p.inferred, inferred_note=p.inferred_note))
    names = {"shcs": "socket head cap screw", "shcs_low": "low-head cap screw (DIN 7984)", "bhcs": "button head screw",
             "fhcs": "flat head screw (ISO 10642)",
             "insert": "heat-set insert", "lock_nut": "lock nut", "pin": "steel pin", "washer": "washer", "nut": "nut"}
    for key, fl in count.items():
        s = fl[0].spec
        if s["type"] == "pin":
            size = f"{s['d_mm']:g} x {s['length_mm']:g} mm"
        else:
            size = f"{s['thread']} x {s['length_mm']:g}" if "length_mm" in s else s["thread"]
            if s["type"] == "insert" and s.get("od_mm"):
                size += f" (OD {s['od_mm']:g})"
        lines.append(BomLine(key, f"{size} {names.get(s['type'], s['type'])}", len(fl), "fastener", s,
                             fasteners=[f.id for f in fl], inferred=any(f.inferred for f in fl),
                             inferred_note="Length or placement read from the geometry, not the BOM."
                             if any(f.inferred for f in fl) else ""))
    for st in asm.steps:
        for u in st.unplaced:
            if not u["count"]:
                continue
            ex = next((b for b in lines if b.key == u["key"]), None)
            if ex:
                ex.qty += u["count"]
            else:
                lines.append(BomLine(u["key"], u["note"], u["count"], "fastener", u["spec"], inferred=True))
    ins = next(b for b in lines if b.key == "insert-M4x6")
    where = {}
    for f in count.get("insert-M4x6", []):
        where[f.joins[0]] = where.get(f.joins[0], 0) + 1
    ins.inferred_note = ("Hunter's BOM: 14 (6 head bottom + 8 servo bosses); placed here: "
                         + ", ".join(f"{n} in {k}" for k, n in sorted(where.items()))
                         + ". The extra ones follow the fastening rule (no screws into raw prints).")
    asm.bom = lines
