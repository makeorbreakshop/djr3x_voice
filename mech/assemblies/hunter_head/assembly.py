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
- Neck joint member, visor servo + mount, visor arms, horns and rods: not placed by any source
  file. Inferred, and flagged on every part.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import trimesh

from workbench import geom
from workbench.geom import align, fastener_mesh
from workbench.kinematics import apply, horn_basis, link_matrices, solve_rod
from workbench.model import (Assembly, BomLine, Fastener, Joint, Link, Linkage, Part, Step,
                             Transform)

ID = "hunter_head"
MECH = Path(__file__).resolve().parents[2]
VENDOR = MECH / "vendor" / "hunter_head"
PROFILE = MECH.parent / "profiles" / "r3x" / "robot.json"

# ------------------------------------------------------------------ parameters
PARAMS = {
    "visor_arm_thickness": 3.0,   # mm; the DXF implies a cut plate (inferred: 3 mm aluminium)
    "horn_radius": 40.0,          # mm, horn centre to ball on the 48 mm arm "cut down" (BOM); the
                                  # smallest radius (4 mm steps) whose rods reach tilt -20..25 x roll +/-12
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
    y = np.array([0.0, 1, 0])
    z = np.cross(x, y)
    r = np.stack([x, y, z])
    near = hb.vertices[np.min(np.linalg.norm(hb.vertices[:, None, [0, 2]] - holes[None, :, [0, 2]], axis=2), axis=1) < 4.5]
    boss_top = float(near[:, 1].max())
    # the plate's hole grid centre in the head frame (plate STL (0, 25, 0.03) -> head)
    plate_c = np.array([0.0, 25.0 - 28.11, 0.03 + 0.24])
    o = np.array([c[0], boss_top, c[2]]) - r.T @ plate_c
    m = np.eye(4)
    m[:3, :3] = r
    m[:3, 3] = -r @ o
    # residual: every head-bottom hole against the nearest plate hole
    plate_holes = np.array([[sx * 60.0, 0, z0 + 0.24] for sx in (-1, 1) for z0 in (-28.71, 0.03, 28.77)])
    got = (holes - o) @ r.T
    res = [float(np.min(np.linalg.norm(plate_holes[:, [0, 2]] - g[[0, 2]], axis=1))) for g in got]
    return m, {
        "yaw_deg": round(math.degrees(math.atan2(x[2], x[0])), 2),
        "origin_in_shell": [round(float(v), 2) for v in o],
        "hole_rms_mm": round(float(np.sqrt(np.mean(np.square(res)))), 3),
        "holes_head": got,
        "boss_top_head": boss_top - o[1],
    }


# ------------------------------------------------------------------ helpers

def leaf_axis(v: np.ndarray):
    """Principal axis, centre and length of a slender leaf (screws, shafts)."""
    c = v.mean(0)
    _, _, vt = np.linalg.svd(v - c)
    a = vt[0]
    s = (v - c) @ a
    return c, a, float(s.max() - s.min()), s


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

    coupler = step_part("RX_Neck_Coupler_V1")
    m, n = printed_mass(coupler)
    add(Part("neck_coupler", "RX Neck Coupler V1", "mech", "neck", coupler,
             src_step("RX_Neck_Coupler_V1", also=["RX Neck Coupler V1 v1.step", "RXNeckCouplerV1.stl"]),
             "PLA (printed)", True, (0, -1, 0), 60, m, n,
             note="Slides over the neck tube; the 16 mm pattern on top takes the bottom sonic hub."))

    # neck joint member: in the coupler, its centre hole on the coupler's side holes (STEP y 15)
    njm = geom.stl(vendor("RX Neck Joint Member V1.stl"))
    njm = xf(njm, M_STEP @ _t([0, 15, 0]))
    m, n = printed_mass(njm)
    add(Part("neck_joint_member", "RX Neck Joint Member V1", "mech", "neck", njm,
             {"file": "RX Neck Joint Member V1.stl", "kind": "stl", "placement": "inferred"},
             "PLA (printed)", True, (0, -1, 0), 90, m, n, inferred=True,
             inferred_note="Not in the STEP. Placed across the coupler bore with its centre hole on the coupler's "
                           "side holes (STEP y 15); its end holes then face the neck tube wall. Confirm."))

    hub_names = sorted(range(len(by_label["1309-0016-4008"])),
                       key=lambda k: by_label["1309-0016-4008"][k][2][:, 1].mean())
    for k, (pid, name, note) in zip(hub_names, [
        ("hub_bottom", "Sonic hub, thru-hole (goBILDA 1311)", "Bolts down onto the coupler; clamps the hex shaft."),
        ("hub_top", "Sonic hub, threaded (goBILDA 1309)", "Top of the fixed post; the two push-rod balls screw into its face."),
    ]):
        mesh = step_part("1309-0016-4008", k)
        add(Part(pid, name, "hardware", "neck", mesh,
                 src_step("1309-0016-4008", bom="1311" if pid == "hub_bottom" else "1309"),
                 "aluminium", False, (0, 1 if pid == "hub_top" else -1, 0), 50,
                 metal_mass(mesh, 2.7), "aluminium 2.7 g/cc", inferred=pid == "hub_bottom",
                 inferred_note="The STEP models both hubs as 1309; the BOM lists one 1311 thru-hole + one 1309 "
                               "threaded. The coupler's 3.3 mm (M4 tap) holes need screws from above, so the "
                               "thru-hole one goes at the bottom." if pid == "hub_bottom" else "",
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
    m, n = printed_mass(cjp)
    add(Part("custom_joint_piece", "Custom Joint Piece", "mech", "cross", cjp,
             src_step("Custom_Joint_Piece", also=["Custom Joint Piece.step"]), "PLA (printed)", True,
             (0, 0, 0), 0, m, n, note="The gimbal cross: tilt axis (X) in the U-joint bearings, "
                                      "roll axis (Z) in the pillow-block bearings."))

    plate_stl = geom.stl(vendor("RX Head Mech Base Plate V4.stl"))
    plate = xf(plate_stl, M_PLATE)
    step_plate = step_part("Head_Mounting_Plate")
    dev = float(np.abs(plate.bounds - step_plate.bounds).max())
    m, n = printed_mass(plate)
    add(Part("mount_plate", "Head mount plate (RX Head Mech Base Plate V4)", "mech", "head", plate,
             {"file": "RX Head Mech Base Plate V4.stl", "kind": "stl", "placement": "fitted",
              "fit": f"STL V4 vs the STEP's Head_Mounting_Plate: bounds agree within {dev:.2f} mm"},
             "PLA (printed)", True, (0, 1, 0), 50, m, n,
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
        mesh = xf(geom.stl(vendor(f)), m_shell)
        m, n = printed_mass(mesh)
        add(Part(pid, name, "shell", "head", mesh, shell_src(f), "PLA (printed)", True, ex, 150, m, n))

    # ---------------------------------------------------------- visor
    poly, circles = geom.dxf_profile(vendor("Visor Arm DXF.dxf"))
    pivot_c = min(circles, key=lambda c: abs(c[2] - 4.75))  # the 9.5 mm hub hole
    tip_c = max(circles, key=lambda c: math.hypot(c[0] - pivot_c[0], c[1] - pivot_c[1]))
    shell_to_head = lambda p: (m_shell @ np.append(p, 1))[:3]
    vp = shell_to_head([0.0, pivot_c[1], 0.0])  # the DXF's x = 0 is the kit's centre plane
    visor_pivot = np.array([0.0, vp[1], vp[2]])
    t = PARAMS["visor_arm_thickness"]
    arm2d = geom.extrude(poly, t)
    side_x = max(p.mesh.bounds[1][0] for p in parts if p.id == "side_left")
    arm_x = side_x + 2 + t / 2
    # DXF (x, y) -> head: x_dxf -> -Z, y_dxf -> +Y, extrusion -> X
    def arm_matrix(sx):
        mm = np.eye(4)
        mm[:3, :3] = np.array([[0, 0, sx], [0, 1, 0], [-1, 0, 0]], float)
        mm[:3, 3] = [sx * arm_x, visor_pivot[1] - pivot_c[1], visor_pivot[2] + pivot_c[0]]
        return mm
    for sx, side in ((1, "l"), (-1, "r")):
        arm = xf(arm2d, arm_matrix(sx))
        add(Part(f"visor_arm_{side}", f"Visor arm ({'left' if side == 'l' else 'right'})", "mech", "visor", arm,
                 {"file": "Visor Arm DXF.dxf", "kind": "dxf", "placement": "inferred",
                  "extrude_mm": t, "mirrored": side == "l"},
                 "aluminium 3 mm (inferred)", False, (sx, 0, 0), 40, metal_mass(arm, 2.7),
                 "aluminium 2.7 g/cc at the chosen thickness", inferred=True,
                 inferred_note=f"Thickness {t} mm is a parameter (DXF only). Pivot = the DXF's 9.5 mm hole on the "
                               "ear axis (DXF y 770.59 = the kit's visor pivot height); arms just outside the ear "
                               "blocks; the DXF's -x = forward; the drawn pose = visor 0. The brow itself is not "
                               "in Hunter's files."))
    tip_head = apply(arm_matrix(-1), [tip_c[0], tip_c[1], 0])

    # visor servo: output on the visor axis inside the right ear, pointing out (-X)
    vs_spline = np.array([-(side_x - 30), visor_pivot[1], visor_pivot[2]])
    # servo_box: spline +Y at the origin, long axis +X -> output out (-X), long axis up (+Y)
    m_vs = np.eye(4)
    m_vs[:3, :3] = np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1]], float)
    m_vs[:3, 3] = vs_spline
    vservo = xf(geom.servo_box(), m_vs)
    add(Part("visor_servo", "Visor servo (not in the BOM)", "servo", "head", vservo,
             {"kind": "generated", "placement": "inferred"}, "servo", False, (0, 0, -1), 40, SERVO["mass_g"],
             "catalogue ~70 g (inferred)", inferred=True,
             inferred_note="The BOM names two servos (tilt/roll). A third drives the visor: model and position "
                           "are not in the sources; drawn as a standard servo with its output on the ear axis."))
    vmount = geom.stl(vendor("Visor Servo Mount.stl"))
    # the mount's servo pocket: flange holes at x 52.1/62.5, y 1126.9/1174.9, face z ~ 92
    # de-yaw the bracket (long axis -> x), then its pocket normal (z) -> -X, up stays up
    pocket = np.array([57.3, 1150.9 + 10.0, 92.2])  # spline ~10 mm above the flange-hole centre
    _, _, vt = np.linalg.svd(vmount.vertices[:, [0, 2]] - vmount.vertices[:, [0, 2]].mean(0))
    yaw = math.atan2(vt[0, 1], vt[0, 0])
    m_yaw = trimesh.transformations.rotation_matrix(yaw, [0, 1, 0], pocket)
    rot = np.eye(4)
    rot[:3, :3] = np.array([[0, 0, -1], [0, 1, 0], [1, 0, 0]], float)
    vm = xf(vmount, _t(vs_spline + [4.0, 0, 0]) @ rot @ _t(-pocket) @ m_yaw)
    m, n = printed_mass(vm)
    add(Part("visor_servo_mount", "Visor servo mount", "mech", "head", vm,
             {"file": "Visor Servo Mount.stl", "kind": "stl", "placement": "inferred"}, "PLA (printed)", True,
             (0, 0, -1), 40, m, n, inferred=True,
             inferred_note="The STL is exported in another model's coordinates (y 1109-1179). Placed so the servo "
                           "in its pocket has its output on the visor axis. Confirm where its slotted feet screw."))

    # ---------------------------------------------------------- push rods (closed form)
    hp = PARAMS["hub_pattern"]
    hub_top_face = max(p.mesh.bounds[1][1] for p in parts if p.id == "hub_top")
    ball_y = hub_top_face + PARAMS["hub_ball_height"]
    linkages = []
    for side, sx in (("l", 1), ("r", -1)):
        c = servos[side]
        off = PARAMS["horn_hub_height"] + PARAMS["horn_arm_thickness"] + PARAMS["ball_above_arm"]
        b = np.array([sx * hp, ball_y, -hp])
        r = PARAMS["horn_radius"]
        # horn perpendicular to the rod at zero: (C - B).u = -r in the horn plane, forward-most root
        d = (c - b)[[0, 2]]
        base = math.atan2(d[1], d[0])
        phi = math.acos(-r / np.linalg.norm(d))
        us = [np.array([math.cos(base + s * phi), 0, math.sin(base + s * phi)]) for s in (1, -1)]
        u = max(us, key=lambda v: v[2])
        a = c + np.array([0, off, 0]) + r * u
        length = float(np.linalg.norm(a - b))
        lk = Linkage(f"rod_{side}", f"servo_{side}", "head", tuple(c), (0, 1, 0), r, tuple(u), off, "neck",
                     tuple(b), length, (-150, 150), inferred=True,
                     inferred_note="Horn radius (cut-down arm), hub hole choice and ball heights are not in the "
                                   "STEP; the horn is set square to the rod at zero for the most even reach.")
        linkages.append(lk)
        # horn mesh: horn frame +Y axis, +X arm -> head (u, +Y, u x Y)
        hm = np.eye(4)
        w = np.cross(u, [0, 1, 0])
        hm[:3, 0], hm[:3, 1], hm[:3, 2], hm[:3, 3] = u, [0, 1, 0], -w, c
        horn = xf(geom.servo_hub_and_arm(r, PARAMS["horn_hub_height"], PARAMS["horn_arm_thickness"]), hm)
        add(Part(f"horn_{side}", f"Servo hub + cut-down arm ({side.upper()})", "hardware", "head", horn,
                 {"kind": "generated", "placement": "inferred", "bom": "1906 hub + 48 mm hub-mount arm"},
                 "aluminium hub, nylon arm", False, (0, 1, 0), 30, 12.0, "estimate", linkage=lk.id, role="horn",
                 inferred=True, inferred_note=lk.inferred_note))
        rod_axis = (b - a) / length
        rod_m = align(rod_axis, a)
        end_len = 24.1
        rod_len = 50.0
        rod = xf(fastener_mesh({"type": "threaded_rod", "thread": "M4", "length_mm": rod_len}),
                 rod_m @ geom_trans([0, 0, (length - rod_len) / 2]))
        e_a = xf(geom.ball_link(end_len), rod_m)
        e_b = xf(geom.ball_link(end_len), align(-rod_axis, b))
        for pid, mesh, role, nm in ((f"rod_{side}", rod, "rod", "M4 threaded rod, 50 mm (goBILDA 2808)"),
                                    (f"rod_{side}_end_a", e_a, "rod_end_a", "Ball linkage, horn end (goBILDA 2913)"),
                                    (f"rod_{side}_end_b", e_b, "rod_end_b", "Ball linkage, post end (goBILDA 2913)")):
            add(Part(pid, f"{nm} ({side.upper()})", "hardware", "head", mesh,
                     {"kind": "generated", "placement": "inferred"}, "steel", False,
                     (0, 1, 0), 30, 6.0 if role == "rod" else 8.0, "estimate", linkage=lk.id, role=role,
                     inferred=True, inferred_note=f"Ball centres {length:.1f} mm apart at zero: set by the "
                                                  "thread engagement of the two ball ends."))
        lk.parts = [f"horn_{side}", f"rod_{side}", f"rod_{side}_end_a", f"rod_{side}_end_b"]

    asm.parts = parts
    asm.linkages = linkages

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
        Joint("visor", "Visor", "revolute", "head", "visor", tuple(visor_pivot), (1, 0, 0),
              PARAMS["visor_limits"], "deg", "visor", plims.get("visor"),
              {"kind": "direct", "servos": ["visor_servo"], "gear_ratio": 1.0,
               "note": "The arm's 9.5 mm hole + 4 x M3 (10 mm square) bolt it to the servo horn: 1:1, no push-rod."},
              {"how": "Visor at the bottom of its flap with the servo at its centre pulse.", "step": "s11"},
              inferred=True, inferred_note="Direct drive read from the arm's hole pattern; the servo is not in the sources."),
    ]
    asm.notes = [
        "Provides head_tilt, head_roll and visor from the profile.",
        "head_pan is not in this mechanism: the neck coupler rides the neck tube, which turns (R-3X neck gear).",
        "head_lift is not in this mechanism: the coupler is fixed on the neck tube. With a lift, the whole "
        "head moves on the tube; nothing here changes.",
        f"Shell fit: 6 insert holes, rms {fit['hole_rms_mm']} mm; the shell is yawed {fit['yaw_deg']} deg in its "
        f"export. Head frame origin in shell coordinates: {fit['origin_in_shell']}.",
        f"Mount plate STL V4 vs the STEP's plate: bounds within {dev:.2f} mm.",
        "Profile actuators drive head_tilt and head_roll on separate channels; this mech mixes both on its "
        "two servos (tilt = opposite, roll = same direction), so the actuator map needs a mixer.",
    ]

    asm.visor_tip = tip_head  # for the torque check's assumed brow load
    add_fasteners(asm, fit, servos, linkages, visor_pivot, arm_x, tip_head)
    add_steps(asm)
    add_bom(asm)
    return asm


def checks(asm: Assembly):
    """The workbench calls this after build(): the checks with this mech's assumptions."""
    from workbench.checks import run_all

    prof = json.loads(PROFILE.read_text())
    amax = {j["name"]: j.get("a_max", 0) for j in prof["joints"]}
    amax["head_roll"] = amax.get("head_tilt", 0)  # not in the profile: same as tilt
    brow_g = 60.0
    return run_all(asm, SERVO, amax, {"visor brow (assumed, at the arm tips)": ("visor", asm.visor_tip, brow_g)})


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


def add_fasteners(asm: Assembly, fit, servos, linkages, visor_pivot, arm_x, tip_head):
    fs: list[Fastener] = []
    down = np.array([0, -1.0, 0])

    def f(fid, spec, joins, link, step, pos=None, direction=None, inferred=False, note=""):
        m = align(direction, pos) if pos is not None else None
        fs.append(Fastener(fid, spec, key_of(spec), joins, link, step, m, inferred, note))

    # inserts: head bottom (6), plate servo bosses (8)
    for i, h in enumerate(sorted(fit["holes_head"].tolist(), key=lambda p: (p[0], p[2]))):
        top = [h[0], fit["boss_top_head"], h[2]]
        f(f"ins_bottom_{i + 1}", INSERT, ["head_bottom"], "head", "s01", top, down)
        # plate -> bottom: 4 mm flange (ray-measured on the STL) + 6 mm insert => M4x10
        f(f"scr_plate_bottom_{i + 1}", SHCS(screw_for(4.0, 5.5)), ["mount_plate", "head_bottom"], "head", "s10",
          [h[0], fit["boss_top_head"] + 4.0, h[2]], down)
    plate_top = 64.26 - 28.11
    for side, sx in (("l", 1), ("r", -1)):
        for i, (px, pz) in enumerate([(16, -60.11), (16, -69.89), (64, -60.11), (64, -69.89)]):
            pos = [sx * px, plate_top, pz + 0.24]
            f(f"ins_servo_{side}{i + 1}", INSERT, ["mount_plate"], "head", "s01", pos, down)
            f(f"scr_servo_{side}{i + 1}", SHCS(8), [f"servo_{side}", "mount_plate"], "head", "s07",
              [pos[0], pos[1] + 2.5, pos[2]], down, True, "Flange ~2.5 mm + 6 mm insert: M4x8.")
    # pillow blocks: 4 mm bridge (ray-measured), tapped goBILDA posts
    for i, (px, pz) in enumerate([(16, 28.79), (-16, 28.79), (16, -28.69), (-16, -28.69)]):
        side = "f" if pz > 0 else "b"
        f(f"scr_pillow_{i + 1}", SHCS(screw_for(4.0, 6.0)), ["mount_plate", f"pillow_{side}"], "head", "s05",
          [px, 56.11 - 28.11, pz + 0.24], down, True, "goBILDA pillow blocks are tapped M4: 4 mm bridge + 6 mm thread.")
    # gimbal pivots: tilt (X) through the U-joint bearings, roll (Z) through the pillow bearings
    for sx, side in ((1, "l"), (-1, "r")):
        f(f"pin_tilt_{side}", SHCS(16), ["ujoint", "custom_joint_piece"], "neck", "s04",
          [sx * 19.0, 0, 0], [-sx, 0, 0], True, "Pivot screw through the flanged bearing into the cross; "
                                                 "a washer as spacer (BOM). Length inferred.")
        f(f"wash_tilt_{side}", WASHER, ["ujoint", "custom_joint_piece"], "neck", "s04",
          [sx * 11.5, 0, 0], [-sx, 0, 0], True)
    for sz, side in ((1, "f"), (-1, "b")):
        f(f"pin_roll_{side}", SHCS(20), [f"pillow_{side}", "custom_joint_piece"], "head", "s05",
          [0, 0, sz * 36.0], [0, 0, -sz], True, "Pivot screw through the pillow-block bearing into the cross end, "
                                                 "washer spacer between (BOM). Length inferred.")
        f(f"wash_roll_{side}", WASHER, [f"pillow_{side}", "custom_joint_piece"], "head", "s05",
          [0, 0, sz * 25.5], [0, 0, -sz], True)
    # hubs: bottom hub onto the coupler (4, into the 3.3 mm holes), clamp screws from the STEP
    coupler_top = 35 - 67
    for i, (px, pz) in enumerate([(8, 8), (8, -8), (-8, 8), (-8, -8)]):
        f(f"scr_hub_coupler_{i + 1}", SHCS(16), ["hub_bottom", "neck_coupler"], "neck", "s03",
          [px, coupler_top + 10.5, pz], down, True, "Thru-hole hub (10 mm) + ~6 mm into the printed M4 tap holes.")
    leaves = geom.step_leaves(vendor("Head Joint Asm.step"))
    clamps = [xf(trimesh.Trimesh(v, fc), M_STEP) for _, lbl, v, fc in leaves if lbl.startswith("2800-0004-0014")]
    for i, cm in enumerate(sorted(clamps, key=lambda m: m.centroid[1])):
        c, a, length, s = leaf_axis(cm.vertices)
        # the head is the fatter end
        v = cm.vertices - c
        hi = v[s > s.max() - 3]
        lo = v[s < s.min() + 3]
        rad = lambda p: np.linalg.norm(p - np.outer(p @ a, a), axis=1).max()
        if rad(hi) > rad(lo):
            a = -a
        head = c - a * (length / 2)
        which = "hub_bottom" if cm.centroid[1] < 0 else "hub_top"
        f(f"clamp_{which}_{i % 2 + 1}", {**SHCS(14), "mcmaster": "90128A207"}, [which, "hex_shaft"], "neck",
          "s03" if which == "hub_bottom" else "s06", head, a, False,
          "Sonic hub clamp screw (placed from the STEP).")
    # ball studs: post end into the threaded hub face; horn end through the arm with a lock nut
    for lk in linkages:
        side = lk.id[-1]
        b = np.array(lk.ground_point)
        f(f"stud_post_{side}", SHCS(16), [f"rod_{side}_end_b", "hub_top"], "neck", "s09",
          b + [0, 6, 0], down, True, "Through the ball, into the threaded sonic hub. Loctite.")
        n, u, w = horn_basis(lk)
        a = np.array(lk.centre) + lk.ball_offset * n + lk.radius * u
        f(f"stud_horn_{side}", SHCS(16), [f"rod_{side}_end_a", f"horn_{side}"], "head", "s09",
          a + [0, 6, 0], down, True, "Through the ball and the arm's end hole.")
        f(f"nut_horn_{side}", LOCKNUT, [f"horn_{side}"], "head", "s09",
          a - [0, lk.ball_offset - PARAMS["horn_hub_height"] + 0.5, 0], down, True)
    # neck joint member pin (coupler side holes, STEP x = +/-19.5 -> head z)
    f("pin_neck_member", SHCS(45), ["neck_coupler", "neck_joint_member"], "neck", "s02",
      [0, 15 - 67, 22.5], [0, 0, -1], True, "Through the coupler's side holes and the member; lock nut inside.")
    f("nut_neck_member", LOCKNUT, ["neck_coupler"], "neck", "s02", [0, 15 - 67, -21.0], [0, 0, -1], True)
    # visor: arm to the servo horn (4 x M3 on 10 mm), left arm on a pivot screw
    for i, (dx, dy) in enumerate([(-5, 5), (5, 5), (5, -5), (-5, -5)]):
        f(f"scr_visor_horn_{i + 1}", SHCS(8, "M3"), ["visor_arm_r"], "visor", "s11",
          [-arm_x - 2, visor_pivot[1] + dy, visor_pivot[2] - dx], [1, 0, 0], True,
          "M3 clearance holes in the DXF (3.4 mm); the horn is not in the sources.")
    f("pin_visor_l", SHCS(16), ["visor_arm_l", "side_left"], "visor", "s11",
      [arm_x + 2, visor_pivot[1], visor_pivot[2]], [-1, 0, 0], True,
      "The left arm has no servo: a pivot screw into the left ear (inferred).")
    asm.fasteners = fs


# ------------------------------------------------------------------ steps

def add_steps(asm: Assembly):
    F = lambda prefix: [f.id for f in asm.fasteners if f.id.startswith(prefix)]
    asm.steps = [
        Step("s01", "Heat-set the inserts", ["head_bottom", "mount_plate"], F("ins_"),
             tools=["soldering iron with an M4 insert tip (~220 C for PLA)", "flat plate to seat them level"],
             notes=["Do all 14 before anything else: 6 in the head bottom's bosses, 8 in the plate's servo bosses.",
                    "Press straight and stop flush; a proud insert tilts the plate or the servo."]),
        Step("s02", "Neck coupler and joint member", ["neck_coupler", "neck_joint_member"], F("pin_neck") + F("nut_neck"),
             tools=["3 mm hex key", "7 mm spanner"],
             notes=["The member crosses the coupler bore; its end holes take the screws through the neck tube wall "
                    "when the head goes on the droid."],
             inferred=True, inferred_note="The member is not in the STEP; its place and the M4x45 pin are read from the holes."),
        Step("s03", "Bottom sonic hub and the hex post", ["hub_bottom", "hex_shaft"],
             F("scr_hub_coupler") + F("clamp_hub_bottom"), context=["neck_coupler"],
             tools=["3 mm hex key", "Loctite 243"],
             notes=["Hub onto the coupler's 16 mm pattern (4 x M4 into the printed holes: snug, not tight).",
                    "Push the hex shaft fully into the hub and clamp both screws, Loctite on the clamps."],
             inferred=True, inferred_note="Thru-hole vs threaded hub assignment and screw length inferred."),
        Step("s04", "U-joint and the cross (tilt axis)", ["ujoint", "uj_bearing_l", "uj_bearing_r", "custom_joint_piece"],
             F("pin_tilt") + F("wash_tilt"), context=["hex_shaft", "hub_bottom", "neck_coupler"],
             tools=["3 mm hex key", "Loctite 243"],
             notes=["Slide the U-joint body down the hex post onto the hub.",
                    "Pivot the cross in the U-joint's bearings with a washer each side as a spacer: it must swing "
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
             tools=["3 mm hex key"],
             notes=["Output splines up, toward the back corners.",
                    "Cable routing: run both servo leads down the back of the plate, clear of the roll arc, and "
                    "out through the neck with a service loop - the head tilts and rolls around the post."],
             inferred=True, inferred_note="Cable route inferred; the sources do not show it."),
        Step("s08", "Centre both servos, then fit the horns", ["horn_l", "horn_r"], [],
             unplaced=[{"key": "shcs-M4x8", "spec": SHCS(8), "count": 8,
                        "note": "hub-mount arm to servo hub (goBILDA pattern), 4 per horn - supplied with the parts"}],
             tools=["servo tester or the r3x bench (1500 us)", "2.5 mm hex key"],
             notes=["Power each servo and send its centre pulse (1500 us) BEFORE fitting the horn. This is what "
                    "defines head_tilt = 0 and head_roll = 0 in real life.",
                    "With the plate held level, fit the hub on the spline and the arm so it sits square to where "
                    "its rod will run (the step shows the angle). Clamp the hub.",
                    "Record the centre pulse in the robot profile (actuator center_us) as the zero."],
             joint="head_tilt", pose={"head_tilt": 0, "head_roll": 0},
             inferred=True, inferred_note="1500 us is goBILDA's centre; the horn angle comes from the rod geometry."),
        Step("s09", "Push rods", ["rod_l", "rod_r", "rod_l_end_a", "rod_l_end_b", "rod_r_end_a", "rod_r_end_b"],
             F("stud_") + F("nut_horn"), context=["horn_l", "horn_r", "hub_top"],
             tools=["3 mm hex key", "7 mm spanner", "calipers", "Loctite 243"],
             notes=[f"Thread a ball end onto each end of the 50 mm rod until the ball centres are "
                    f"{asm.linkages[0].rod_length:.1f} mm apart (both rods). Loctite the threads.",
                    "Horn ends: screw through the ball and the arm's end hole, lock nut under the arm.",
                    "Post ends: screw through the ball into the threaded top hub.",
                    "With the servos still at centre the plate must sit level: adjust a rod half a turn if not."],
             pose={"head_tilt": 0, "head_roll": 0},
             inferred=True, inferred_note="Rod length is computed from the inferred horn radius and hub holes."),
        Step("s10", "Head bottom onto the plate", ["head_bottom"], F("scr_plate_bottom"), context=["mount_plate"],
             tools=["3 mm hex key"],
             notes=["6 x M4x10 down through the plate's flange into the head bottom's inserts.",
                    "Feed the servo leads through the neck opening first."]),
        Step("s11", "Visor servo, centre it, then the visor arms",
             ["visor_servo_mount", "visor_servo", "visor_arm_r", "visor_arm_l"],
             F("scr_visor") + F("pin_visor"),
             unplaced=[{"key": "shcs-M4x8", "spec": SHCS(8), "count": 4, "note": "visor servo into the mount"},
                       {"key": "insert-M4x6", "spec": INSERT, "count": 4, "note": "the mount's four 6 mm bosses (beyond the BOM's 14)"},
                       {"key": "shcs-M4x10", "spec": SHCS(10), "count": 2, "note": "mount feet (slots) to the shell"}],
             tools=["servo tester or the r3x bench", "2.5 mm hex key"],
             notes=["Centre the visor servo (1500 us) before bolting the arm to its horn: that is visor = 0, the "
                    "visor at the bottom of its flap.",
                    "The left arm pivots on a screw in the left ear, no servo."],
             joint="visor", pose={"visor": 0},
             inferred=True, inferred_note="The visor servo, its horn and the mount's position are not in the sources."),
        Step("s12", "Sides and top", ["side_left", "side_right", "head_top"], [],
             unplaced=[{"key": "pin-align", "spec": {"type": "pin", "note": "alignment pins or filament"},
                        "count": 0, "note": "alignment holes on the side pieces: pins + CA glue (count on the parts)"}],
             tools=["CA glue or 2-part epoxy"],
             notes=["Dry-fit first: sweep the head through tilt and roll by hand and look for rubbing (Checks tab).",
                    "The top lifts off for service; glue only the sides to the bottom if you glue at all."],
             inferred=True, inferred_note="The sources give alignment holes but no fastener for the shell."),
    ]


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
        BomLine("gobilda-arm-48", "Plastic hub-mount control arm, 48 mm (cut down)", 2, "hardware",
                source=GOBILDA + "plastic-hub-mount-control-arm-48mm-length/", parts=["horn_l", "horn_r"]),
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
        BomLine("servo-visor", "Visor servo (standard size)", 1, "servo", parts=["visor_servo"], inferred=True,
                inferred_note="Not in the BOM; the visor needs one."),
    ]
    for p in asm.parts:
        if p.printed:
            lines.append(BomLine(f"print-{p.id}", f"Print: {p.name}", 1, "printed", parts=[p.id],
                                 spec={"file": p.source.get("file")}, inferred=p.inferred, inferred_note=p.inferred_note))
    names = {"shcs": "socket head cap screw", "insert": "heat-set insert (6 x 6 mm)", "lock_nut": "lock nut",
             "washer": "washer", "nut": "nut"}
    for key, fl in count.items():
        s = fl[0].spec
        size = f"{s['thread']} x {s['length_mm']:g}" if "length_mm" in s and s["type"] != "insert" else s["thread"]
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
    ins.inferred_note = (f"BOM: 14 inserts; placed here: {len(count.get('insert-M4x6', []))} "
                         "(6 head bottom + 8 servo bosses) - matches. +4 for the visor mount (not in the BOM).")
    asm.bom = lines
