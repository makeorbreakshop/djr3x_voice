"""Build the DJ R3X simulation model from the printable STL kit.

Run headless through Blender (see build.sh):

    blender -b -P sim/model/build_r3x.py -- --kit <zip-or-dir> --out sim/web/public/model

The kit (Patrick Gray / David Ferreira, "DJ R3X - v2", CC BY-NC, group-only) is NOT in
this repo and the GLB this script produces must not be committed either - the repo is
public. The output directory is gitignored.

Every STL in the kit's "Large Cut" set is exported in its assembled position (Y-up,
millimetres), so no part is hand-placed here. What this script adds:

* x4 parts (base panels, speaker pods, lower vents) are exported once by the kit and
  replicated here at 90 degree steps about the droid's vertical axis.
* The droid is rotated so the torso's RX-24 plate faces +Z (glTF/three.js "front"), and
  the head is turned an extra ~19 degrees so pan=0 means "looking where the body faces".
* A joint hierarchy of empties (``j_<name>``) at pivots measured from the geometry:
  the hinge discs, bearing hubs and shaft axes of each part - see JOINTS below.
* Anchor empties (``a_<name>``) where the sim mounts the WS2812 eye rings, the mouth V
  and the hero-arm piston rod, oriented so local +Z is outward.
* Material *classes* (paint_orange, metal_grey, ...); the web sim owns actual colours.
* Collapse decimation to a triangle budget, then Draco-compressed GLB.
* Visual only: a shared UV atlas and two baked weathering masks (occlusion, exposed
  edges) for the web sim's paint shader - see bake_weathering(). --no-bake skips it.

It also writes rig.json - the joint list (pivot, axis, limits) in final metres - which the
web sim loads so the joint table has one source of truth.
"""

import atexit
import json
import math
import os
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix, Vector

# --------------------------------------------------------------------------------------
# Arguments
# --------------------------------------------------------------------------------------

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def _arg(name, default=None):
    if name in argv:
        return argv[argv.index(name) + 1]
    return default


KIT = Path(os.path.expanduser(_arg("--kit", "~/Desktop/DJ-R3X/3D Models/DJ R3X - v2.zip")))
OUT = Path(_arg("--out", "sim/web/public/model")).resolve()
TRI_BUDGET = int(_arg("--budget", "600000"))
# The holed mouth grille + light pipe + back mount ("Mic-Mouth-Split", Brandon's Drive).
# Replaces the kit's solid H_M_1 when present.
MOUTH_DIR = Path(os.path.expanduser(_arg(
    "--mouth",
    "~/Library/CloudStorage/GoogleDrive-brandon@makeorbreakshop.com/My Drive/MOB/Projects/DJ-R3X/Mic-Mouth-Split")))
MOUTH_PARTS = {"Grill": ("H_MOUTH_GRILL", "metal_dark"), "LightPipe": ("H_MOUTH_PIPE", "light_pipe"),
               "BackMount": ("H_MOUTH_MOUNT", "metal_dark")}
# Mic-Mouth-Split files are stored upside down and back to front relative to the kit head
# (front at -Z, narrow end up). Rotate 180 deg about X and slide so the grille's extent
# matches H_M_1 exactly (both 83.5 mm tall): y' = MOUTH_FLIP_Y - y, z' = -z.
MOUTH_FLIP_Y = 1471.3
PREVIEW = "--preview" in argv
# Weathering bake (UVs + occlusion/edge masks for the web sim's paint shader). --no-bake
# skips it; the sim then falls back to procedural-only wear.
BAKE = "--no-bake" not in argv

# --------------------------------------------------------------------------------------
# Frames. Source = kit coordinates (mm, Y-up). Final = metres, Y-up, front = +Z.
# --------------------------------------------------------------------------------------

# Body front: direction of the RX-24 plate's centre, atan2(x, z) = -33.0 deg.
BODY_YAW = math.radians(33.0)
# Head front: perpendicular to the ear axis (H_LE_1 -> H_RE_1 centres), -52.2 deg.
# The kit poses the head turned 19.2 deg off the body; we square it up.
HEAD_YAW = math.radians(52.2)


def roty(a):
    """three.js-convention rotation about +Y (right-handed, Y-up)."""
    c, s = math.cos(a), math.sin(a)
    return Matrix(((c, 0, s, 0), (0, 1, 0, 0), (-s, 0, c, 0), (0, 0, 0, 1)))


# Y-up (x, y, z) -> Blender Z-up (x, -z, y). The glTF exporter applies the inverse, so the
# GLB ends up in exactly our "final" Y-up coordinates.
YUP_TO_BL = Matrix(((1, 0, 0, 0), (0, 0, -1, 0), (0, 1, 0, 0), (0, 0, 0, 1)))
MM = Matrix.Scale(0.001, 4)


def frame_matrix(frame):
    return YUP_TO_BL @ MM @ roty(HEAD_YAW if frame == "head" else BODY_YAW)


def final_point(p_mm, frame):
    """Source mm point -> final metres (Y-up), as a plain list for rig.json."""
    v = MM @ roty(HEAD_YAW if frame == "head" else BODY_YAW) @ Vector(p_mm)
    return [round(v.x, 5), round(v.y, 5), round(v.z, 5)]


def final_dir(d, frame):
    r = roty(HEAD_YAW if frame == "head" else BODY_YAW).to_3x3()
    v = (r @ Vector(d)).normalized()
    return [round(v.x, 5), round(v.y, 5), round(v.z, 5)]


def to_bl(p_final):
    return YUP_TO_BL.to_3x3() @ Vector(p_final)


# --------------------------------------------------------------------------------------
# Part selection
# --------------------------------------------------------------------------------------

SKIP = [
    r"\(Old\)",                  # superseded LS_M_Full
    r"/LED Board Mounts/",       # interior LED board brackets
    r"/MS_LPI_",                 # same brackets, duplicated in the parent folder
    r"/MS_DB",                   # interior light diffusers behind the logic panels
    r"/MS_L - x4",
    r"/LED_B_1x9",
    # Duplicates of Logic Panels/MS_P_*_Full, stored one folder up. Loading both put two
    # identical panels in the same place - z-fighting ("flicker") on the chest.
    r"Midsection - Middle/STLs/MS_P_[12]\.stl$",
]

# Exported once by the kit, printed four times, spaced 90 deg about the vertical axis.
REPLICATE_X4 = ["B_M_D - x4", "B_SM - x4", "B_S_C - x4", "B_S_O - x4", "B_S_PO - x4", "LS_V_1 - x4"]

# Material classes, first match wins. The web sim maps class -> colour.
MATERIAL_RULES = [
    # Paint as on the Oga's Cantina animatronic, taken from neutral-light references (the
    # Hasbro Black Series figure, painted to match) - park photos are too tinted by the
    # booth's amber stage lights to judge colour. Sources: ~/Desktop/DJ-R3X/Reference Photos.
    # Weathered orange top/bottom rings and base; charcoal middle ring, top cap, pedestal
    # and head shell; orange visor with cream chevrons; blue headphone cups; blue RX-24
    # letters on a dark plate; light-grey arms with orange wrist cuffs.
    (r"^H_[LR]Eye_4$", "eye_lens"),
    (r"^H_[LR]Eye_", "metal_dark"),
    (r"^H_[LR]E_1$", "accent_blue"),          # headphone cups
    (r"^H_[LR]E_2$", "metal_dark"),
    (r"^H_HP_", "metal_dark"),                # headband
    (r"^H_M_1$", "metal_dark"),               # mouthpiece grille
    (r"^H_V_1$", "visor_stripes"),            # the brow / visor
    (r"^H_", "paint_charcoal"),               # head shell
    (r"^RX-24$", "metal_dark"),               # plate; its raised letters -> accent_blue (split_raised_letters)
    (r"_DNP$", "rubber"),                     # ribbed gasket rings (floor-mat material)
    (r"^TR_RR_Full$", "rubber"),
    (r"^(MS_P_[12]_Full)$", "metal_dark"),    # logic panels
    (r"^(LS_M_Full|TR_NR_Full)", "paint_orange"),
    (r"^(MS_Main_Full|TR_N_[123])", "paint_charcoal"),
    (r"^(B_B_|B_S_[12]$|B_T_|B_M_D)", "paint_orange"),
    (r"^(B_MT_|B_SM|B_S_[COP])", "metal_dark"),
    (r"^P_", "metal_dark"),
    (r"^(TR[-_]|LS_|MS_)", "metal_grey"),
    # Arms: light grey paint, orange cuff at each wrist, bare metal on rods and pistons.
    (r"^(HA_W_[12]|TA_W_[12]|PA_W_[12])$", "paint_orange"),
    (r"^(HA_PS_|HA_P_1$|TA_B_|PA_W_3$)", "metal_grey"),
    (r"^(HA_|TA_|PA_)", "paint_lightgrey"),
]

# Parts kept as their own glTF node (the sim reads their positions).
KEEP_SEPARATE = {"H_LEye_4", "H_REye_4", "H_M_1", "H_MOUTH_GRILL", "H_MOUTH_PIPE"}


def material_class(name):
    for pat, cls in MATERIAL_RULES:
        if re.search(pat, name):
            return cls
    return "metal_grey"


# --------------------------------------------------------------------------------------
# Joints. Pivots and axes are in SOURCE coordinates (mm), measured from the geometry:
# bounding-box centres of hinge discs / hubs and the PCA thin-axis of disc parts.
# `parts` are regexes on the part name; when several joints match, the one listed last
# (children follow their parents) wins - see assign_joint(). Unmatched parts are static.
# --------------------------------------------------------------------------------------

EAR_L = (86.4, 770.6, 111.3)       # H_LE_1 centre
EAR_R = (-86.4, 770.6, -111.3)     # H_RE_1 centre
EAR_AXIS = (0.613, 0.0, 0.790)

JOINTS = [
    # name, parent, frame, pivot(mm), axis, (min, max) deg, parts
    dict(name="torso_lower", parent=None, frame="body", pivot=(0, 0, 0), axis=(0, 1, 0),
         limits=(-35, 35), parts=[r"^LS_", r"^PA_M_"],
         note="Lower ring on its 10in lazy susan; travel set by the ~98 deg internal gear sector."),
    dict(name="poker_shoulder", parent="torso_lower", frame="body", pivot=(-4.2, 372.0, 200.6),
         axis=(1, 0, 0), limits=(-45, 35), parts=[r"^PA_B_", r"^PA_W_", r"^PA_F_"],
         note="Hub PA_M_1/PA_M_3; the twin PA_B plates swing about X."),
    dict(name="poker_wrist", parent="poker_shoulder", frame="body", pivot=(-8.0, 465.7, 328.0),
         axis=(1, 0, 0), limits=(-40, 40), parts=[r"^PA_W_", r"^PA_F_"]),
    dict(name="poker_claw_upper", parent="poker_wrist", frame="body", pivot="auto",
         axis="auto", limits=(-5, 30), parts=[r"^PA_F_1$"], hand=("PA_W_1", ["PA_F_1", "PA_F_2"], "PA_F_1")),
    dict(name="poker_claw_lower", parent="poker_wrist", frame="body", pivot="auto",
         axis="auto", limits=(-5, 30), parts=[r"^PA_F_2$"], hand=("PA_W_1", ["PA_F_1", "PA_F_2"], "PA_F_2")),

    dict(name="torso_middle", parent="torso_lower", frame="body", pivot=(0, 0, 0), axis=(0, 1, 0),
         limits=(-60, 60), parts=[r"^MS_", r"^TA_S_1$"],
         note="Middle ring (logic panels) on the second lazy susan."),
    dict(name="throttle_shoulder", parent="torso_middle", frame="body", pivot=(-6.3, 448.6, -211.2),
         axis=(0, 0, 1), limits=(-50, 50), parts=[r"^TA_S_[2-5]$", r"^TA_B_", r"^TA_FA", r"^TA_W"],
         note="Shaft TA_S_1 runs along Z into the ring; the shoulder block turns on it."),
    dict(name="throttle_elbow", parent="throttle_shoulder", frame="body", pivot=(-8.05, 242.1, -211.2),
         axis=(0, 0, 1), limits=(-60, 45), parts=[r"^TA_FA", r"^TA_W"],
         note="Hubs TA_FA_I_3/I_4."),
    dict(name="throttle_wrist", parent="throttle_elbow", frame="body", pivot=(-187.3, 321.6, -205.8),
         axis=(0, 0, 1), limits=(-60, 60), parts=[r"^TA_FA_1$", r"^TA_W"],
         note="Hubs TA_FA_I_1/I_2."),
    dict(name="throttle_claw_a", parent="throttle_wrist", frame="body", pivot="auto", axis="auto",
         limits=(-5, 30), parts=[r"^TA_W_F[13]$"], hand=("TA_W_1", ["TA_W_F1", "TA_W_F2"], "TA_W_F1")),
    dict(name="throttle_claw_b", parent="throttle_wrist", frame="body", pivot="auto", axis="auto",
         limits=(-5, 30), parts=[r"^TA_W_F[24]$"], hand=("TA_W_1", ["TA_W_F1", "TA_W_F2"], "TA_W_F2")),

    dict(name="torso_top", parent="torso_middle", frame="body", pivot=(0, 0, 0), axis=(0, 1, 0),
         limits=(-30, 30), parts=[r"^TR[-_]", r"^RR_", r"^RX-24$", r"^HA_EB_", r"^HA_[LR]E_1$", r"^HA_PJ_"]),
    dict(name="hero_shoulder", parent="torso_top", frame="body", pivot=(40.8, 528.4, 227.5),
         axis=(0.988, 0.0, -0.152), limits=(-35, 45),
         parts=[r"^HA_SB_", r"^HA_SP_", r"^HA_PL_", r"^HA_P_1$", r"^HA_PS_", r"^HA_W_", r"^HA_[LRT]F_"],
         note="Axis through the HA_LE_1 / HA_RE_1 hinge discs."),
    dict(name="hero_wrist", parent="hero_shoulder", frame="body", pivot=(71.5, 693.1, 397.1),
         axis=(0.114, 0.674, 0.727), limits=(-90, 90), parts=[r"^HA_W_1$", r"^HA_[LRT]F_"],
         note="Roll about the forearm (HA_SP_2) axis."),
    dict(name="hero_claw_l", parent="hero_wrist", frame="body", pivot="auto", axis="auto",
         limits=(-5, 30), parts=[r"^HA_LF_"], hand=("HA_W_1", ["HA_LF_2", "HA_RF_2", "HA_TF_2"], "HA_LF_2")),
    dict(name="hero_claw_r", parent="hero_wrist", frame="body", pivot="auto", axis="auto",
         limits=(-5, 30), parts=[r"^HA_RF_"], hand=("HA_W_1", ["HA_LF_2", "HA_RF_2", "HA_TF_2"], "HA_RF_2")),
    dict(name="hero_claw_t", parent="hero_wrist", frame="body", pivot="auto", axis="auto",
         limits=(-5, 30), parts=[r"^HA_TF_"], hand=("HA_W_1", ["HA_LF_2", "HA_RF_2", "HA_TF_2"], "HA_TF_2")),

    dict(name="head_lift", parent="torso_top", frame="head", type="prismatic", pivot=(0, 0, 0),
         axis=(0, 1, 0), limits=(-20, 20), parts=[],
         note="mm. Neck tube slides on MGN12 rails, rack + pinion from a 60 kg servo in the base "
              "(R-3X Animation design). Travel is an estimate until the lift gear is measured."),
    dict(name="head_pan", parent="head_lift", frame="head", pivot=(0, 0, 0), axis=(0, 1, 0),
         limits=(-70, 70), parts=[r"^NECK$"], note="The neck tube itself rotates (neck-center-gear)."),
    dict(name="head_tilt", parent="head_pan", frame="head", pivot=(0, 740.0, 0), axis=EAR_AXIS,
         limits=(-20, 25), parts=[r"^H_"]),
    dict(name="visor", parent="head_tilt", frame="head",
         pivot=tuple((a + b) / 2 for a, b in zip(EAR_L, EAR_R)), axis=EAR_AXIS,
         limits=(-15, 30), parts=[r"^H_V_"],
         note="Brow (H_V_1) and its side arms pivot on the ear axles; a push-rod servo in the "
              "head flips it (R-3X Animation visor design). The headband is glued, static."),
]

JOINT_BY_NAME = {j["name"]: j for j in JOINTS}


def assign_joint(name):
    """Deepest joint whose patterns match. JOINTS is ordered parent-before-child."""
    hit = None
    for j in JOINTS:
        if any(re.search(p, name) for p in j["parts"]):
            hit = j["name"]
    return hit  # None -> static base


def frame_of(name):
    return "head" if name.startswith("H_") or name == "NECK" else "body"


# --------------------------------------------------------------------------------------
# Import
# --------------------------------------------------------------------------------------

def kit_root():
    if KIT.is_dir():
        root = KIT
    else:
        tmp = Path(tempfile.mkdtemp(prefix="r3x_kit_"))
        atexit.register(shutil.rmtree, tmp, True)
        with zipfile.ZipFile(KIT) as z:
            z.extractall(tmp, [m for m in z.namelist() if "/STLs/Large Cut/" in m])
        root = tmp
    hits = list(root.rglob("Large Cut"))
    if not hits:
        sys.exit(f"No 'Large Cut' folder under {KIT}")
    return hits[0]


def list_parts(large_cut):
    out = []
    for p in sorted(large_cut.rglob("*.stl")):
        s = p.as_posix()
        if any(re.search(pat, s) for pat in SKIP):
            continue
        out.append(p)
    return out


def clean_name(stem):
    return stem.replace(" (New)", "").replace(" (DNP)", "_DNP").replace(" - x4", "")


def import_stl(path):
    bpy.ops.wm.stl_import(filepath=str(path))
    obj = bpy.context.selected_objects[0]
    return obj


def weld_and_smooth(obj, angle_deg=30.0):
    me = obj.data
    bm = bmesh.new()
    bm.from_mesh(me)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.001)  # mm
    lim = math.radians(angle_deg)
    for f in bm.faces:
        f.smooth = True
    for e in bm.edges:
        if len(e.link_faces) != 2:
            e.smooth = False
        else:
            e.smooth = e.calc_face_angle(0.0) < lim
    bm.to_mesh(me)
    bm.free()


def decimate(obj, ratio):
    if ratio >= 0.999:
        return
    mod = obj.modifiers.new("dec", "DECIMATE")
    mod.decimate_type = "COLLAPSE"
    mod.ratio = ratio
    mod.use_collapse_triangulate = True
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.modifier_apply(modifier=mod.name)


# Printed PLA is not solid: ~3 perimeters + 15-20% infill weighs roughly 45% of a solid
# part. Estimate only - weigh a printed part and correct PLA_EFFECTIVE_DENSITY.
PLA_DENSITY = 1240.0  # kg/m^3, solid
PLA_EFFECTIVE_DENSITY = PLA_DENSITY * 0.45
NECK_DENSITY = 2700.0 * 0.26  # 22 mm aluminium tube, 1.5 mm wall, as an equivalent solid
G = 9.80665


def mass_properties(obj, density):
    """Mass (kg) and centre of mass (Blender coords, m) from a closed triangle mesh.

    Signed tetrahedra against the origin; robust to the small holes STL exports often have
    as long as the shell is mostly closed. Uses abs(volume) so winding does not matter.
    """
    import numpy as np
    me = obj.data
    me.calc_loop_triangles()
    n = len(me.loop_triangles)
    if n == 0:
        return 0.0, Vector((0, 0, 0))
    co = np.empty(len(me.vertices) * 3)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    tri = np.empty(n * 3, dtype=np.int64)
    me.loop_triangles.foreach_get("vertices", tri)
    v = co[tri.reshape(-1, 3)]
    vol = np.einsum("ij,ij->i", v[:, 0], np.cross(v[:, 1], v[:, 2])) / 6.0
    total = vol.sum()
    if abs(total) < 1e-12:
        return 0.0, Vector(v.reshape(-1, 3).mean(0))
    com = (vol[:, None] * (v.sum(1) / 4.0)).sum(0) / total
    return abs(total) * density, Vector(com)


def joint_dynamics(parts_info, rig):
    """Per joint: outboard mass, COM, worst-case gravity torque and inertia about the axis.

    parts_info: [(joint_name_or_None, mass, com_final)]. Inertia treats each part as a
    point mass at its COM (parallel-axis term only) - fine for sizing, low for bulky parts.
    """
    children = {}
    for j in rig:
        children.setdefault(j["parent"], []).append(j["name"])

    def subtree(name):
        out = {name}
        for c in children.get(name, []):
            out |= subtree(c)
        return out

    g_dir = Vector((0, -1, 0))  # final space is Y-up
    out = {}
    for j in rig:
        names = subtree(j["name"])
        pivot = Vector(j["pivot"])
        axis = Vector(j["axis"]).normalized()
        m_tot, moment, inertia = 0.0, Vector((0, 0, 0)), 0.0
        for jn, m, c in parts_info:
            if jn in names:
                m_tot += m
                moment += c * m
                r = c - pivot
                r_perp = r - axis * r.dot(axis)
                inertia += m * r_perp.length_squared
        if m_tot <= 0:
            continue
        com = moment / m_tot
        r = com - pivot
        r_perp = r - axis * r.dot(axis)
        # Max over rotation of axis . (r x F): |F perpendicular to axis| * |r_perp|.
        f_perp = m_tot * G * math.sqrt(max(0.0, 1 - axis.dot(g_dir) ** 2))
        worst = f_perp * r_perp.length
        rest = axis.dot(r.cross(g_dir * (m_tot * G)))
        out[j["name"]] = {
            "mass_kg": round(m_tot, 4),
            "com": [round(x, 5) for x in com],
            "lever_m": round(r_perp.length, 4),
            "gravity_torque_worst_kgcm": round(worst / G * 100, 3),
            "gravity_torque_rest_kgcm": round(rest / G * 100, 3),
            "inertia_kgm2": round(inertia, 6),
        }
    return out


def tri_count(obj):
    return sum(len(p.vertices) - 2 for p in obj.data.polygons)


def get_material(cls):
    m = bpy.data.materials.get(cls)
    if m is None:
        m = bpy.data.materials.new(cls)
        m.diffuse_color = {
            "paint_orange": (0.78, 0.36, 0.1, 1), "metal_grey": (0.45, 0.47, 0.5, 1),
            "metal_dark": (0.18, 0.19, 0.21, 1), "rubber": (0.05, 0.05, 0.05, 1),
            "accent_blue": (0.15, 0.35, 0.7, 1), "eye_lens": (0.3, 0.6, 1.0, 1),
        }.get(cls, (0.5, 0.5, 0.5, 1))
    return m


# --------------------------------------------------------------------------------------
# Auto pivots for claw fingers
# --------------------------------------------------------------------------------------

def find_panel_lights(obj, step_mm=0.8):
    """Openings in a logic panel that light can shine through: windows and LED dots.

    Casts radial rays (the panels sit on the middle ring, whose axis is +Y) on a fine grid
    over the panel's footprint. Cells whose ray misses the panel are see-through; connected
    see-through regions that are enclosed by panel on all sides are openings. Returns
    [{kind, pos, normal, w, h}] in final coordinates (metres); dots are the small round
    holes of the 8-LED rows, windows the square cut-outs over the diffuser blocks.
    """
    from mathutils.bvhtree import BVHTree
    import numpy as np
    bvh = BVHTree.FromObject(obj, bpy.context.evaluated_depsgraph_get())
    bl2y = YUP_TO_BL.to_3x3().inverted()
    pts = np.array([list(bl2y @ p) for p in world_verts(obj)]) * 1000.0  # mm, final
    theta = np.arctan2(pts[:, 0], pts[:, 2])
    rad = np.hypot(pts[:, 0], pts[:, 2])
    t0, t1 = theta.min(), theta.max()
    y0, y1 = pts[:, 1].min(), pts[:, 1].max()
    r_out, r_in = rad.max() + 20.0, rad.min() - 1.0
    r_mean = rad.mean()
    nt = int((t1 - t0) * r_mean / step_mm) + 1
    ny = int((y1 - y0) / step_mm) + 1
    hit_r = np.full((nt, ny), np.nan)
    for i in range(nt):
        th = t0 + (t1 - t0) * i / max(1, nt - 1)
        d = Vector((-math.sin(th), 0.0, -math.cos(th)))
        for j in range(ny):
            y = y0 + (y1 - y0) * j / max(1, ny - 1)
            o = Vector((math.sin(th) * r_out, y, math.cos(th) * r_out)) / 1000.0
            loc, _n, _i, dist = bvh.ray_cast(YUP_TO_BL.to_3x3() @ o, YUP_TO_BL.to_3x3() @ d,
                                             (r_out - r_in) / 1000.0)
            if loc is not None:
                hit_r[i, j] = r_out - dist * 1000.0
    miss = np.isnan(hit_r)
    # Flood-fill see-through components; drop any touching the footprint border.
    seen = np.zeros_like(miss)
    lights = []
    for i in range(nt):
        for j in range(ny):
            if not miss[i, j] or seen[i, j]:
                continue
            stack, cells, border = [(i, j)], [], False
            seen[i, j] = True
            while stack:
                a, b = stack.pop()
                cells.append((a, b))
                if a in (0, nt - 1) or b in (0, ny - 1):
                    border = True
                for da, db in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    u, v = a + da, b + db
                    if 0 <= u < nt and 0 <= v < ny and miss[u, v] and not seen[u, v]:
                        seen[u, v] = True
                        stack.append((u, v))
            if border:
                continue
            ci = np.array(cells)
            w = (ci[:, 0].max() - ci[:, 0].min() + 1) * step_mm
            h = (ci[:, 1].max() - ci[:, 1].min() + 1) * step_mm
            # LED holes are ~2.5 mm, windows 12-15 mm; 6-8 mm openings are fastener holes.
            if max(w, h) < 1.5 or max(w, h) > 45 or (3.5 < max(w, h) and min(w, h) < 10):
                continue
            # Front face radius from the panel around the opening.
            ring = []
            for a, b in cells:
                for da, db in ((2, 0), (-2, 0), (0, 2), (0, -2)):
                    u, v = a + da, b + db
                    if 0 <= u < nt and 0 <= v < ny and not miss[u, v]:
                        ring.append(hit_r[u, v])
            r_front = float(np.median(ring)) if ring else r_mean
            th = t0 + (t1 - t0) * ci[:, 0].mean() / max(1, nt - 1)
            y = y0 + (y1 - y0) * ci[:, 1].mean() / max(1, ny - 1)
            kind = "dot" if max(w, h) <= 3.5 else "window"
            depth = 2.0 if kind == "dot" else 4.0  # LED / diffuser just behind the face
            r = r_front - depth
            lights.append({
                "kind": kind,
                "pos": [round(math.sin(th) * r / 1000, 5), round(y / 1000, 5), round(math.cos(th) * r / 1000, 5)],
                "normal": [round(math.sin(th), 4), 0.0, round(math.cos(th), 4)],
                "w": round(w / 1000, 4), "h": round(h / 1000, 4),
            })
    return lights


def split_raised_letters(obj, letter_material, min_relief_mm=0.3):
    """Give the raised letters of a plate (RX-24) their own material.

    Fits the plate's plane (least-variance axis), finds the height of the plate face and of
    the letter tops among faces parallel to it, and assigns every face above the midpoint
    to `letter_material`. Works on the transformed mesh (Blender units = metres).
    """
    import numpy as np
    me = obj.data
    co = np.array([v.co[:] for v in me.vertices])
    c = co.mean(0)
    _w, vec = np.linalg.eigh(np.cov((co - c).T))
    n = vec[:, 0]
    # Outward = away from the droid's vertical axis (Blender Z).
    radial = np.array([c[0], c[1], 0.0])
    if np.dot(n, radial) < 0:
        n = -n
    heights, areas, idx = [], [], []
    for f in me.polygons:
        if abs(np.dot(np.array(f.normal[:]), n)) > 0.95:
            heights.append(np.dot(np.array(f.center[:]) - c, n))
            areas.append(f.area)
            idx.append(f.index)
    if not heights:
        return 0
    heights = np.array(heights)
    top = heights.max()
    below = heights < top - min_relief_mm / 1000.0
    if not below.any():
        return 0
    plate = heights[below][np.argmax(np.array(areas)[below])]
    cut = (top + plate) / 2.0
    obj.data.materials.append(get_material(letter_material))
    slot = len(obj.data.materials) - 1
    moved = 0
    for f in me.polygons:
        if np.dot(np.array(f.center[:]) - c, n) > cut:
            f.material_index = slot
            moved += 1
    return moved


def world_verts(obj):
    return [obj.matrix_world @ v.co for v in obj.data.vertices]


def centroid(pts):
    s = Vector((0, 0, 0))
    for p in pts:
        s += p
    return s / max(1, len(pts))


def finger_pivot(finger_obj, hand_obj, finger_objs):
    """Hinge at the finger's base; axis opens the finger radially away from the hand axis.

    Worked in Blender space on already-transformed meshes. Positive rotation about the
    returned axis moves the tip outward (open): for tip direction h and radial r,
    d/dtheta = (h x r) x h = r.
    """
    hand_c = centroid(world_verts(hand_obj))
    tips_c = centroid([centroid(world_verts(o)) for o in finger_objs])
    h = (tips_c - hand_c).normalized()
    fv = world_verts(finger_obj)
    proj = [(p - hand_c).dot(h) for p in fv]
    lo, hi = min(proj), max(proj)
    base = [p for p, t in zip(fv, proj) if t < lo + 0.12 * (hi - lo)]
    pivot = centroid(base)
    r = centroid(fv) - hand_c
    r = r - h * r.dot(h)
    if r.length < 1e-6:
        r = Vector((1, 0, 0))
    axis = h.cross(r.normalized()).normalized()
    return pivot, axis


# --------------------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------------------

def main():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    large_cut = kit_root()
    paths = list_parts(large_cut)
    print(f"[r3x] {len(paths)} STL parts from {large_cut}")

    parts = []  # (name, obj)
    for p in paths:
        stem = p.stem
        obj = import_stl(p)
        name = clean_name(stem)
        obj.name = name
        weld_and_smooth(obj)
        obj.data.transform(frame_matrix(frame_of(name)))
        obj.data.materials.append(get_material(material_class(name)))
        parts.append((name, obj))
        if stem in REPLICATE_X4:
            for k in (1, 2, 3):
                dup = obj.copy()
                dup.data = obj.data.copy()
                dup.name = f"{name}_r{k}"
                # rotate about the (final) vertical axis = Blender Z
                dup.data.transform(Matrix.Rotation(math.radians(90 * k), 4, "Z"))
                scene.collection.objects.link(dup)
                parts.append((dup.name, dup))

    # The holed mouth: grille, light pipe (LED diffuser bars behind the slots), back mount.
    if all((MOUTH_DIR / f"{f}.stl").is_file() for f in MOUTH_PARTS):
        for n, o in [p for p in parts if p[0] == "H_M_1"]:
            bpy.data.objects.remove(o)
        parts = [p for p in parts if p[0] != "H_M_1"]
        flip = Matrix(((1, 0, 0, 0), (0, -1, 0, MOUTH_FLIP_Y), (0, 0, -1, 0), (0, 0, 0, 1)))
        for fname, (name, mat) in MOUTH_PARTS.items():
            obj = import_stl(MOUTH_DIR / f"{fname}.stl")
            obj.name = name
            weld_and_smooth(obj)
            obj.data.transform(YUP_TO_BL @ MM @ flip)
            obj.data.materials.append(get_material(mat))
            parts.append((name, obj))
        print(f"[r3x] mouth: holed grille + light pipe from {MOUTH_DIR}")
    else:
        print(f"[r3x] mouth: kit H_M_1 (no Mic-Mouth-Split at {MOUTH_DIR})")

    # Neck rod: a metal rod the kit doesn't model. r=11mm, from the top ring into the head.
    bpy.ops.mesh.primitive_cylinder_add(vertices=24, radius=0.011, depth=0.235,
                                        location=to_bl(final_point((0, 690.0, 0), "head")))
    neck = bpy.context.active_object
    neck.name = "NECK"
    bpy.ops.object.shade_smooth()
    neck.data.materials.append(get_material("metal_dark"))
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    parts.append(("NECK", neck))

    by_name = {n: o for n, o in parts}
    if "RX-24" in by_name:
        n_letters = split_raised_letters(by_name["RX-24"], "accent_blue")
        print(f"[r3x] RX-24: {n_letters} raised letter faces -> accent_blue")

    # Mass properties on the full-resolution meshes, before decimation.
    bl2y = YUP_TO_BL.to_3x3().inverted()
    parts_info = []
    for n, o in parts:
        density = NECK_DENSITY if n == "NECK" else PLA_EFFECTIVE_DENSITY
        m, com_bl = mass_properties(o, density)
        parts_info.append((assign_joint(n), m, bl2y @ com_bl))
    print(f"[r3x] estimated printed mass {sum(m for _, m, _ in parts_info):.2f} kg")

    # Chest logic-panel openings (before decimation, which would blur the small holes).
    chest_lights = []
    for n in ("MS_P_1_Full", "MS_P_2_Full"):
        if n in by_name:
            found = find_panel_lights(by_name[n])
            for L in found:
                L["panel"] = n
            chest_lights += found
    print(f"[r3x] chest panels: {sum(L['kind'] == 'window' for L in chest_lights)} windows, "
          f"{sum(L['kind'] == 'dot' for L in chest_lights)} LED dots")

    # UVs on the clean full-resolution meshes: decimation keeps seams and interpolates
    # UVs, whereas unwrapping the decimated meshes shatters them into ~100k islands.
    if BAKE:
        unwrap([o for _, o in parts])

    # Decimate to budget (small parts untouched).
    total = sum(tri_count(o) for _, o in parts)
    big = [(n, o) for n, o in parts if tri_count(o) > 2500]
    small_total = total - sum(tri_count(o) for _, o in big)
    ratio = max(0.12, min(1.0, (TRI_BUDGET - small_total) / max(1, total - small_total)))
    print(f"[r3x] {total} tris before decimation; ratio {ratio:.3f} on {len(big)} large parts")
    for n, o in big:
        # The mouth grille's slots and light-pipe bars are thin; decimate them gently.
        decimate(o, max(0.5, ratio) if n.startswith("H_MOUTH_") else ratio)
    print(f"[r3x] {sum(tri_count(o) for _, o in parts)} tris after decimation")

    # Joint empties.
    empties = {}
    rig = []
    for j in JOINTS:
        if j["pivot"] == "auto":
            hand_name, finger_names, finger_name = j["hand"]
            piv_bl, ax_bl = finger_pivot(by_name[finger_name], by_name[hand_name],
                                         [by_name[n] for n in finger_names])
            bl2y = YUP_TO_BL.to_3x3().inverted()
            piv = list(bl2y @ piv_bl)
            ax = list((bl2y @ ax_bl).normalized())
        else:
            piv = final_point(j["pivot"], j["frame"])
            ax = final_dir(j["axis"], j["frame"])
        e = bpy.data.objects.new(f"j_{j['name']}", None)
        e.empty_display_size = 0.02
        scene.collection.objects.link(e)
        e.location = to_bl(piv)
        empties[j["name"]] = e
        rig.append(dict(name=j["name"], parent=j["parent"], pivot=[round(v, 5) for v in piv],
                        axis=[round(v, 5) for v in ax], min=j["limits"][0], max=j["limits"][1],
                        type=j.get("type", "revolute"), note=j.get("note", "")))

    root = bpy.data.objects.new("r3x_root", None)
    scene.collection.objects.link(root)
    bpy.context.view_layer.update()
    for j in JOINTS:
        e = empties[j["name"]]
        parent = empties[j["parent"]] if j["parent"] else root
        mw = e.matrix_world.copy()
        e.parent = parent
        e.matrix_world = mw
    bpy.context.view_layer.update()

    # Anchors (local +Z out, +Y up in final space). Blender: final +Z -> -Y, final +Y -> +Z.
    def add_anchor(name, parent_joint, p_final):
        a = bpy.data.objects.new(f"a_{name}", None)
        a.empty_display_size = 0.01
        scene.collection.objects.link(a)
        a.location = to_bl(p_final)
        bpy.context.view_layer.update()
        mw = a.matrix_world.copy()
        a.parent = empties[parent_joint]
        a.matrix_world = mw

    def bbox_final(obj):
        bl2y = YUP_TO_BL.to_3x3().inverted()
        pts = [bl2y @ p for p in world_verts(obj)]
        lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
        hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
        return lo, hi

    def r5(v):
        return [round(x, 5) for x in v]

    # Eye anchors sit on the axis of the louvred housing (H_*Eye_1), at its front face.
    # H_*Eye_4 is the diffusion bulb centred behind the louvres (guide p.67/69); the sim
    # puts the WS2812 7-LED jewel against the back of it.
    anchors = {}
    for side, housing in (("L", "H_LEye_1"), ("R", "H_REye_1")):
        lo, hi = bbox_final(by_name[housing])
        c = Vector(((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, hi.z))
        anchors[f"eye_{side}"] = r5(c)
        anchors[f"eye_{side}_size"] = r5(hi - lo)
        add_anchor(f"eye_{side}", "head_tilt", list(c))
    # Mouth anchor: centre of the light pipe's front face (the 8-LED V sits behind the pipe,
    # in the back mount); falls back to the kit's solid grille.
    mouth_src = by_name.get("H_MOUTH_PIPE") or by_name["H_M_1"]
    lo, hi = bbox_final(mouth_src)
    c = Vector(((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, hi.z))
    anchors["mouth"] = r5(c)
    anchors["mouth_size"] = r5(hi - lo)
    anchors["mouth_kind"] = "light_pipe" if "H_MOUTH_PIPE" in by_name else "solid_grille"
    add_anchor("mouth", "head_tilt", list(c))
    # Piston rod: from the HA_PJ_1 clevis (body) to the lower end of the HA_P_1 cylinder.
    pj = final_point((30.4, 551.9, 166.8), "body")
    pe = final_point((40.1, 600.0, 220.2), "body")
    add_anchor("piston_base", "torso_top", pj)
    add_anchor("piston_end", "hero_shoulder", pe)
    anchors["piston_base"], anchors["piston_end"] = pj, pe

    # Group parts: join per (joint, material) unless kept separate; parent to joint.
    groups = {}
    for n, o in parts:
        jn = assign_joint(n)
        key = (jn, o.data.materials[0].name) if n not in KEEP_SEPARATE else (jn, n)
        groups.setdefault(key, []).append(o)
    for (jn, tag), objs in groups.items():
        bpy.ops.object.select_all(action="DESELECT")
        for o in objs:
            o.select_set(True)
        bpy.context.view_layer.objects.active = objs[0]
        if len(objs) > 1:
            bpy.ops.object.join()
        obj = bpy.context.view_layer.objects.active
        obj.name = f"{jn or 'base'}__{tag}"
        obj.parent = empties[jn] if jn else root
        obj.matrix_parent_inverse = obj.parent.matrix_world.inverted()

    OUT.mkdir(parents=True, exist_ok=True)
    if BAKE:
        bake_weathering([o for o in bpy.data.objects if o.type == "MESH"], OUT)
    glb = OUT / "r3x.glb"
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.export_scene.gltf(
        filepath=str(glb), export_format="GLB", use_selection=False, export_apply=True,
        export_yup=True, export_normals=True, export_materials="EXPORT",
        export_draco_mesh_compression_enable=True, export_draco_mesh_compression_level=6,
        export_draco_position_quantization=14, export_draco_normal_quantization=10,
        export_draco_texcoord_quantization=13,
        export_extras=False,
    )
    dynamics = joint_dynamics(
        [(jn, m, Vector(c)) for jn, m, c in parts_info], rig)
    rig_doc = {
        "mass_model": {
            "pla_effective_density_kg_m3": PLA_EFFECTIVE_DENSITY,
            "total_printed_mass_kg": round(sum(m for _, m, _ in parts_info), 3),
            "note": "Estimate from STL volume at 45% of solid PLA; servos/hardware not included.",
        },
        "dynamics": dynamics,
        "chest_lights": chest_lights,
        "units": "metres, Y-up, droid front = +Z",
        "source": "DJ R3X - v2 printable kit (Patrick Gray & David Ferreira), Large Cut STLs",
        "joints": rig,
        "anchors": anchors,
        "triangles": sum(len(p.vertices) - 2 for o in bpy.data.objects if o.type == "MESH"
                         for p in o.data.polygons),
    }
    (OUT / "rig.json").write_text(json.dumps(rig_doc, indent=2))
    print(f"[r3x] wrote {glb} ({glb.stat().st_size / 1e6:.1f} MB) and rig.json")

    if PREVIEW:
        render_previews()


# --------------------------------------------------------------------------------------
# Weathering masks (visual only: nothing here changes joints, anchors or rig.json)
# --------------------------------------------------------------------------------------

BAKE_OCCLUSION_RES = 2048
BAKE_EDGE_RES = 4096


def edit_all(objs):
    """Enter multi-object edit mode on `objs` with everything selected."""
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")


def unwrap(objs):
    """One Smart-UV unwrap over every part (islands scaled by true 3D area)."""
    import time
    t = time.time()
    for o in objs:
        while o.data.uv_layers:
            o.data.uv_layers.remove(o.data.uv_layers[0])
        o.data.uv_layers.new(name="UVMap")
    edit_all(objs)
    bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=0.0,
                             area_weight=0.0, correct_aspect=True, scale_to_bounds=False)
    bpy.ops.object.mode_set(mode="OBJECT")
    print(f"[r3x] bake: smart UV over {len(objs)} parts in {time.time() - t:.1f}s")


def bake_weathering(objs, out_dir):
    """Bake the masks the web sim's paint shader weathers with.

    STL meshes have no UVs and big flat faces are a few long triangles, so per-vertex data
    can't hold an edge mask. Instead: one shared Smart-UV atlas over every part, then two
    Cycles emission bakes of shader-computed masks, stored as greyscale JPEGs:

    * r3x_occlusion.jpg - ambient occlusion: broad (12 cm, other parts count) x crevice
      (1.5 cm). Drives grime and ambient darkening.
    * r3x_edges.jpg - exposed edges: 1 - N_bevel . N from Cycles' Bevel node (4 mm),
      i.e. how much a small round-over would bend the normal. Convex and concave edges
      both light up; the sim gates wear by occlusion so only exposed edges chip.
    """
    import time
    t = time.time()
    scene = bpy.context.scene
    edit_all(objs)
    # Re-pack the whole droid into one atlas now the parts are decimated and joined.
    bpy.ops.uv.pack_islands(udim_source="CLOSEST_UDIM", rotate=True, scale=True,
                            margin_method="FRACTION", margin=0.0005, shape_method="CONCAVE")
    bpy.ops.object.mode_set(mode="OBJECT")
    print(f"[r3x] bake: packed UV atlas over {len(objs)} meshes in {time.time() - t:.1f}s")

    scene.render.engine = "CYCLES"
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
        prefs.compute_device_type = "METAL"
        prefs.get_devices()
        for d in prefs.devices:
            d.use = d.type == "METAL"
        scene.cycles.device = "GPU"
        print(f"[r3x] bake: devices {[(d.name, d.type) for d in prefs.devices]}")
    except Exception as e:  # no Metal: CPU works, just slower
        print(f"[r3x] bake: GPU unavailable ({e}); using CPU")
        scene.cycles.device = "CPU"
    scene.cycles.samples = 16
    scene.cycles.use_denoising = False
    scene.render.bake.margin_type = "EXTEND"

    mat = bpy.data.materials.new("r3x_bake")
    if mat.node_tree is None:  # Blender < 5 creates materials without a node tree
        mat.use_nodes = True
    nt = mat.node_tree
    N, L = nt.nodes, nt.links
    N.clear()
    out = N.new("ShaderNodeOutputMaterial")
    emit = N.new("ShaderNodeEmission")
    L.new(emit.outputs["Emission"], out.inputs["Surface"])

    def math_node(op, a, b=None, clamp=False):
        m = N.new("ShaderNodeMath")
        m.operation = op
        m.use_clamp = clamp
        for i, v in enumerate((a, b)):
            if v is None:
                continue
            if isinstance(v, (int, float)):
                m.inputs[i].default_value = v
            else:
                L.new(v, m.inputs[i])
        return m.outputs[0]

    def ao(distance, samples):
        n = N.new("ShaderNodeAmbientOcclusion")
        n.samples = samples
        n.only_local = False
        n.inputs["Distance"].default_value = distance
        return n.outputs["AO"]

    occlusion = math_node("MULTIPLY", math_node("POWER", ao(0.12, 16), 0.8), ao(0.015, 16))
    bevel = N.new("ShaderNodeBevel")
    bevel.samples = 8
    bevel.inputs["Radius"].default_value = 0.004
    geo = N.new("ShaderNodeNewGeometry")
    dot = N.new("ShaderNodeVectorMath")
    dot.operation = "DOT_PRODUCT"
    L.new(bevel.outputs["Normal"], dot.inputs[0])
    L.new(geo.outputs["Normal"], dot.inputs[1])
    edge = math_node("MULTIPLY", math_node("SUBTRACT", 1.0, dot.outputs["Value"]), 5.0, clamp=True)
    tex = N.new("ShaderNodeTexImage")
    N.active = tex

    # Bake one joined copy: Cycles re-syncs the whole scene per baked object, so baking
    # 35 objects separately costs ~35x the scene setup. The UVs are one shared atlas, so
    # the result is identical. Originals are hidden from rays meanwhile.
    bpy.ops.object.select_all(action="DESELECT")
    copies = []
    for o in objs:
        c = o.copy()
        c.data = o.data.copy()
        c.parent = None
        c.matrix_world = o.matrix_world.copy()
        scene.collection.objects.link(c)
        c.data.materials.clear()
        c.data.materials.append(mat)
        copies.append(c)
        o.hide_render = True
    for c in copies:
        c.select_set(True)
    bpy.context.view_layer.objects.active = copies[0]
    bpy.ops.object.join()
    baker = bpy.context.view_layer.objects.active
    try:
        for name, socket, res, margin in (("occlusion", occlusion, BAKE_OCCLUSION_RES, 4),
                                          ("edges", edge, BAKE_EDGE_RES, 6)):
            t = time.time()
            img = bpy.data.images.new(f"r3x_{name}", res, res, alpha=False)
            img.colorspace_settings.name = "Non-Color"
            tex.image = img
            L.new(socket, emit.inputs["Color"])
            bpy.ops.object.bake(type="EMIT", margin=margin, use_clear=True,
                                target="IMAGE_TEXTURES")
            path = out_dir / f"r3x_{name}.jpg"
            img.filepath_raw = str(path)
            img.file_format = "JPEG"
            img.save(quality=85)
            print(f"[r3x] bake: {path.name} {res}px in {time.time() - t:.1f}s "
                  f"({path.stat().st_size / 1e6:.1f} MB)")
    finally:
        bpy.data.objects.remove(baker)
        for o in objs:
            o.hide_render = False
        bpy.data.materials.remove(mat)


def render_previews():
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.display.shading.light = "STUDIO"
    scene.display.shading.color_type = "MATERIAL"
    scene.render.resolution_x, scene.render.resolution_y = 800, 1000
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    scene.collection.objects.link(cam)
    scene.camera = cam
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = 1.15
    for label, yaw in (("front", 0), ("left", 90), ("back", 180), ("three_quarter", -35)):
        a = math.radians(yaw)
        d = Vector((math.sin(a), -math.cos(a), 0.18)).normalized()  # Blender: front is -Y
        cam.location = Vector((0, 0, 0.48)) + d * 3
        cam.rotation_euler = (-d).to_track_quat("-Z", "Y").to_euler()
        scene.render.filepath = str(OUT / f"preview_{label}.png")
        bpy.ops.render.render(write_still=True)


def copy_show_scripts():
    """Maestro show scripts from the R-3X Animation folder (Drive) -> public/shows (gitignored)."""
    src = MOUTH_DIR.parent / "R-3X Animation"
    shows = OUT.parent / "shows"
    found = sorted(src.glob("*maestro*script*.txt")) if src.is_dir() else []
    if not found:
        return
    shows.mkdir(parents=True, exist_ok=True)
    names = []
    for f in found:
        (shows / f.name).write_bytes(f.read_bytes())
        names.append(f.name)
    (shows / "index.json").write_text(json.dumps(names, indent=2))
    print(f"[r3x] copied {len(names)} Maestro show script(s) to {shows}")


def extract_sfx():
    """Copy the kit's R3X voice clips next to the model (gitignored) for the sim's demo."""
    if not KIT.is_file():
        return
    sfx = OUT.parent / "sfx"
    sfx.mkdir(parents=True, exist_ok=True)
    names = []
    with zipfile.ZipFile(KIT) as z:
        for m in z.namelist():
            if "/SFX/" in m and m.lower().endswith(".mp3"):
                name = Path(m).name
                (sfx / name).write_bytes(z.read(m))
                names.append(name)
    (sfx / "index.json").write_text(json.dumps(sorted(names), indent=2))
    print(f"[r3x] extracted {len(names)} SFX clips to {sfx}")


main()
extract_sfx()
copy_show_scripts()
