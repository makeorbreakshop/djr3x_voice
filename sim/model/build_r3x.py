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
* Material *classes* (paint_orange, metal_grey, ...). Colours and weathering per class
  live in sim/web/src/palette.json (the one place to edit them).
* Collapse decimation to a triangle budget, weighted by how visible each part is: hero
  parts (head, visor, cups, face, mouth, RX-24, arms) get ~2.5x the density, parts hidden
  inside the droid less. Then Smooth-by-Angle + Weighted Normal (face area, keep sharp),
  which fixes the blotchy shading of long CAD triangles on big flat/curved panels.
* Visual only (--no-bake skips it): baked PBR texture sets - see bake_textures(). Two UV
  atlases (head, body), each with baseColor (paint + weathering), ORM (occlusion,
  roughness, metalness) and a tangent-space normal map baked from the undecimated STLs.
  The GLB carries them in the standard glTF PBR slots, so any glTF viewer (and a path
  tracer) sees the finished paint with no custom shader.

The Blender side writes an uncompressed GLB + PNG textures into sim/model/.work/;
sim/web/scripts/pack-model.mjs (run by build.sh) assigns the textures, compresses them to
KTX2 (basisu: ETC1S colour, UASTC normal maps; ORM see pack-model.mjs) and the meshes with Draco.

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
TRI_BUDGET = int(_arg("--budget", "700000"))
# rig.json's claw pivots and eye/mouth anchors are measured on part meshes decimated as the
# original 600k build did (vertex centroids depend on decimation). Measuring on copies
# decimated that way keeps rig.json stable whatever the visual triangle budget is.
RIG_MEASURE_BUDGET = 600000
# The holed mouth grille + light pipe + back mount ("Mic-Mouth-Split", Brandon's Drive).
# Replaces the kit's solid H_M_1 when present.
MOUTH_DIR = Path(os.path.expanduser(_arg(
    "--mouth",
    "~/Library/CloudStorage/GoogleDrive-brandon@makeorbreakshop.com/My Drive/MOB/Projects/DJ-R3X/Mic-Mouth-Split")))
# Their paint is mech/assemblies/community/assembly.py MOUTH_FINISH's (mech/workbench/tests/test_finish.py
# checks the two agree).
MOUTH_PARTS = {"Grill": ("H_MOUTH_GRILL", "metal_dark"), "LightPipe": ("H_MOUTH_PIPE", "light_pipe"),
               "BackMount": ("H_MOUTH_MOUNT", "metal_dark")}
# Mic-Mouth-Split files are stored upside down and back to front relative to the kit head
# (front at -Z, narrow end up). Rotate 180 deg about X and slide so the grille's extent
# matches H_M_1 exactly (both 83.5 mm tall): y' = MOUTH_FLIP_Y - y, z' = -z.
MOUTH_FLIP_Y = 1471.3
PREVIEW = "--preview" in argv
# Texture bake (UVs + PBR texture sets). --no-bake skips it; the GLB then carries flat
# per-class colours from the palette.
BAKE = "--no-bake" not in argv
# Intermediate outputs (raw GLB, PNG textures) for pack-model.mjs.
WORK = Path(_arg("--work", str(Path(__file__).resolve().parent / ".work"))).resolve()
PALETTE = Path(_arg("--palette", str(Path(__file__).resolve().parent.parent / "web/src/palette.json")))
# Texture atlas sizes per set (px). Head gets its own atlas: it is what close-ups look at.
BAKE_RES = {"head": int(_arg("--head-res", "4096")), "body": int(_arg("--body-res", "4096"))}
# Debug: bake only these sets (comma list), e.g. --sets head.
BAKE_SETS = _arg("--sets", "head,body").split(",")
# Shipped texture sizes (px), downsampled from the 4K bake (so smaller maps are
# supersampled). Colour carries the visible detail (chips, bars, grime) at 4K; normal and
# ORM are smaller to keep the whole GLB under ~15 MB (a 4K UASTC normal map alone is
# ~4.5 MB). The head's ORM is 1K but UASTC (pack-model.mjs): at close-up range ETC1S
# blocks show in the specular, a slightly softer UASTC map does not.
TEX_RES = {("head", "basecolor"): 4096, ("head", "normal"): 2048, ("head", "orm"): 1024,
           ("body", "basecolor"): 4096, ("body", "normal"): 2048, ("body", "orm"): 2048}
# Keep the raw bake passes (.npy, ~1 GB) in .work/ for tuning the paint without a re-bake.
KEEP_PASSES = "--keep-passes" in argv
# Debug: per-pair normal-bake coverage stats and raw dumps (slow).
DIAG = "--diag" in argv

# --------------------------------------------------------------------------------------
# Frames. Source = kit coordinates (mm, Y-up). Final = metres, Y-up, front = +Z.
# --------------------------------------------------------------------------------------

# Body front: direction of the RX-24 plate's centre, atan2(x, z) = -33.0 deg.
# This is the kit's display pose, not the canonical rest (show/SPEC.md "Frame and zeros":
# +Z = the base's front pod, which is kit -45.0 deg; the rings turned to the park droid's
# rest). sim/web/src/show/rig_limits.json carries that correction (body_yaw, zero_offset)
# and the sim's Rig applies it. If this build bakes the rest in, zero those entries in the
# same change, or the sim applies it twice.
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

# Material (paint) classes per kit part: mech/assemblies/kit/finish.json, the table the mech workbench
# also paints Build's Exterior from, so the Original rig and Build always agree. Colours per class:
# sim/web/src/palette.json. Keys are kit codes, matched case- and punctuation-insensitively (as the
# workbench's part ids are). Unpainted parts (null: inside the droid) and codes missing from the table
# fall back to metal_grey with a warning.
KIT_FINISH = Path(_arg("--finish", str(Path(__file__).resolve().parents[2] / "mech/assemblies/kit/finish.json")))


def _code(name):
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


KIT_PAINT = {_code(k): v for k, v in json.loads(KIT_FINISH.read_text())["paint"].items()}


# Parts whose decimated geometry rig.json is measured on (see RIG_MEASURE_BUDGET).
MEASURED_PARTS = ["HA_W_1", "HA_LF_2", "HA_RF_2", "HA_TF_2", "TA_W_1", "TA_W_F1", "TA_W_F2",
                  "PA_W_1", "PA_F_1", "PA_F_2", "H_LEye_1", "H_REye_1", "H_MOUTH_PIPE", "H_M_1"]

# Parts kept as their own glTF node (the sim reads their positions).
KEEP_SEPARATE = {"H_LEye_4", "H_REye_4", "H_M_1", "H_MOUTH_GRILL", "H_MOUTH_PIPE"}


def material_class(name):
    cls = KIT_PAINT.get(_code(name))
    if cls is None:
        print(f"[r3x] {name}: no paint in {KIT_FINISH.name}; metal_grey")
        return "metal_grey"
    return cls


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
        c = load_palette()["classes"].get(cls.split("__")[0])  # previews only; pack-model sets the rest
        m.diffuse_color = (*hex_linear(c["color"]), 1) if c else (0.5, 0.5, 0.5, 1)
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
# Triangle density: hero parts denser, hidden parts sparser
# --------------------------------------------------------------------------------------

# What the eye goes to: head shell, visor, headphone cups, face plate, eyes, mouth
# grille/light pipe, the RX-24 plate and the arms. Hidden ones are still thinned by
# visibility (below).
HERO = [r"^H_", r"^RX-24$", r"^(HA_|TA_|PA_)"]
HERO_DENSITY = 2.5
MIN_RATIO = 0.06


def smoothstep(e0, e1, x):
    t = min(1.0, max(0.0, (x - e0) / (e1 - e0)))
    return t * t * (3 - 2 * t)


def part_visibility(parts, n_samples=64, n_dirs=40):
    """Fraction of each part's surface a viewer can see from somewhere, 0..1.

    Area-weighted sample points on the part, rays toward a Fibonacci sphere of directions
    (minus those from below the floor); a point counts as seen when at least two rays
    escape the droid. (Counting the share of escaping rays instead would rate a recessed
    but plainly visible face - the face plate under the visor - as hidden.) Parts buried
    inside the rings score ~0, closed outer shells ~0.5 (their inside is hidden).
    Full-resolution meshes; ~5 s.
    """
    import time
    import numpy as np
    t = time.time()
    dg = bpy.context.evaluated_depsgraph_get()
    scene = bpy.context.scene
    k = np.arange(n_dirs) + 0.5
    phi = np.arccos(1 - 2 * k / n_dirs)
    th = math.pi * (1 + 5 ** 0.5) * k
    dirs = np.stack([np.cos(th) * np.sin(phi), np.sin(th) * np.sin(phi), np.cos(phi)], 1)
    dirs = dirs[dirs[:, 2] > -0.35]  # Blender Z is up; nobody looks from under the floor
    out = {}
    for i, (name, obj) in enumerate(parts):
        me = obj.data
        n = len(me.polygons)
        area = np.empty(n); me.polygons.foreach_get("area", area)
        cen = np.empty(n * 3); me.polygons.foreach_get("center", cen)
        nrm = np.empty(n * 3); me.polygons.foreach_get("normal", nrm)
        cen, nrm = cen.reshape(-1, 3), nrm.reshape(-1, 3)
        if area.sum() <= 0:
            out[name] = 0.0
            continue
        rng = np.random.default_rng(1000 + i)
        pick = rng.choice(n, size=min(n_samples, n), p=area / area.sum())
        seen = 0
        for f in pick:
            o = Vector(cen[f] + nrm[f] * 0.0003)
            esc = 0
            for d in dirs:
                if d @ nrm[f] <= 0.05:
                    continue
                if not scene.ray_cast(dg, o, Vector(d), distance=4.0)[0]:
                    esc += 1
                    if esc >= 2:
                        seen += 1
                        break
        out[name] = seen / max(1, len(pick))
    print(f"[r3x] visibility of {len(parts)} parts in {time.time() - t:.1f}s")
    return out


def density_weight(name, vis):
    hero = HERO_DENSITY if any(re.search(p, name) for p in HERO) else 1.0
    return hero * (0.4 + 0.6 * smoothstep(0.05, 0.4, vis))


def allocate_ratios(parts, vis, budget):
    """Per-part decimation ratio: ratio_i = clamp(w_i * r) with r solved for the budget."""
    tris = {n: tri_count(o) for n, o in parts}
    fixed = sum(t for t in tris.values() if t <= 2500)

    def ratio(n, r):
        lo = 0.5 if n in ("H_MOUTH_GRILL", "H_MOUTH_PIPE") else MIN_RATIO  # thin slots / light-pipe bars
        return max(lo, min(1.0, density_weight(n, vis.get(n, 0.3)) * r))

    def total(r):
        return fixed + sum(ratio(n, r) * t for n, t in tris.items() if t > 2500)

    lo, hi = 0.0, 1.0
    for _ in range(40):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if total(mid) < budget else (lo, mid)
    return {n: (ratio(n, lo) if t > 2500 else 1.0) for n, t in tris.items()}


def legacy_ratio(parts, budget):
    """The single decimation ratio the original build used (rig measurements only)."""
    total = sum(tri_count(o) for _, o in parts)
    small_total = sum(tri_count(o) for _, o in parts if tri_count(o) <= 2500)
    return max(0.12, min(1.0, (budget - small_total) / max(1, total - small_total)))


def shade_weighted(obj, angle_deg=30.0):
    """Smooth by angle, then Weighted Normal (face area, keep sharp).

    CAD STLs tessellate big flat and gently curved panels into long thin triangles;
    plain vertex-normal averaging lets the thin ones tilt the normals of the big ones,
    which shows as blotches across the panel. Face-area weighting keeps big faces flat.
    """
    me = obj.data
    bm = bmesh.new()
    bm.from_mesh(me)
    lim = math.radians(angle_deg)
    for f in bm.faces:
        f.smooth = True
    for e in bm.edges:
        e.smooth = len(e.link_faces) == 2 and e.calc_face_angle(0.0) < lim
    bm.to_mesh(me)
    bm.free()
    mod = obj.modifiers.new("wn", "WEIGHTED_NORMAL")
    mod.mode = "FACE_AREA"
    mod.weight = 50
    mod.keep_sharp = True
    mod.thresh = 0.01
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.modifier_apply(modifier=mod.name)


def scale_uvs(obj, s):
    """Scale a part's UV islands (texel density) before the atlas is packed."""
    if abs(s - 1.0) < 1e-3 or not obj.data.uv_layers:
        return
    import numpy as np
    uv = obj.data.uv_layers.active.data
    a = np.empty(len(uv) * 2, dtype=np.float32)
    uv.foreach_get("uv", a)
    uv.foreach_set("uv", a * s)


def texel_weight(name, vis):
    """Relative texel density: hidden parts get little of the atlas, hero parts more."""
    hero = 1.6 if any(re.search(p, name) for p in HERO) else 1.0
    return hero * (0.15 + 0.85 * smoothstep(0.05, 0.4, vis))


def join(objs, name):
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]
    if len(objs) > 1:
        bpy.ops.object.join()
    obj = bpy.context.view_layer.objects.active
    obj.name = name
    return obj


def set_of(joint_name):
    """Texture set (UV atlas) a joint's meshes live in."""
    return "head" if joint_name in ("head_pan", "head_tilt", "visor") else "body"


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

    # How much of each part can be seen from outside (drives triangle and texel density).
    vis = part_visibility(parts)

    # UVs on the clean full-resolution meshes: decimation keeps seams and interpolates
    # UVs, whereas unwrapping the decimated meshes shatters them into ~100k islands.
    if BAKE:
        unwrap([o for _, o in parts])

    # rig.json measurements (claw pivots, eye/mouth anchors) use copies decimated exactly as
    # the original single-ratio build did, so the rig does not move with the visual budget.
    r_legacy = legacy_ratio(parts, RIG_MEASURE_BUDGET)
    measure = {}
    for n in MEASURED_PARTS:
        o = by_name.get(n)
        if o is None:
            continue
        c = o.copy()
        c.data = o.data.copy()
        c.name = f"{n}__measure"
        scene.collection.objects.link(c)
        if tri_count(c) > 2500:
            decimate(c, max(0.5, r_legacy) if n.startswith("H_MOUTH_") else r_legacy)
        c.hide_render = True
        measure[n] = c

    # Full-resolution copies: the normal-map bake's high-poly source.
    highs = {}
    if BAKE:
        for n, o in parts:
            c = o.copy()
            c.data = o.data.copy()
            c.name = f"{n}__hi"
            scene.collection.objects.link(c)
            while c.data.uv_layers:
                c.data.uv_layers.remove(c.data.uv_layers[0])
            shade_weighted(c)
            c.hide_render = True
            highs[n] = c
        for n, o in parts:
            scale_uvs(o, math.sqrt(texel_weight(n, vis.get(n, 0.3))))

    # Decimate to budget, denser where it is seen (small parts untouched).
    total = sum(tri_count(o) for _, o in parts)
    ratios = allocate_ratios(parts, vis, TRI_BUDGET)
    print(f"[r3x] {total} tris before decimation")
    for n, o in parts:
        decimate(o, ratios[n])
        shade_weighted(o)
    hero = sum(tri_count(o) for n, o in parts if any(re.search(p, n) for p in HERO))
    print(f"[r3x] {sum(tri_count(o) for _, o in parts)} tris after decimation ({hero} on hero parts)")
    for n, o in sorted(parts, key=lambda p: -tri_count(p[1]))[:12]:
        print(f"[r3x]   {n:22s} vis {vis.get(n, 0):.2f} ratio {ratios[n]:.3f} -> {tri_count(o)} tris")

    def meas(n):
        return measure.get(n) or by_name[n]

    # Joint empties.
    empties = {}
    rig = []
    for j in JOINTS:
        if j["pivot"] == "auto":
            hand_name, finger_names, finger_name = j["hand"]
            piv_bl, ax_bl = finger_pivot(meas(finger_name), meas(hand_name),
                                         [meas(n) for n in finger_names])
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
        lo, hi = bbox_final(meas(housing))
        c = Vector(((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, hi.z))
        anchors[f"eye_{side}"] = r5(c)
        anchors[f"eye_{side}_size"] = r5(hi - lo)
        add_anchor(f"eye_{side}", "head_tilt", list(c))
    # Mouth anchor: centre of the light pipe's front face (the 8-LED V sits behind the pipe,
    # in the back mount); falls back to the kit's solid grille.
    mouth_src = meas("H_MOUTH_PIPE") if "H_MOUTH_PIPE" in by_name else meas("H_M_1")
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

    for c in measure.values():
        bpy.data.objects.remove(c)

    # Group parts: join per (joint, material) unless kept separate; parent to joint.
    groups = {}
    for n, o in parts:
        jn = assign_joint(n)
        key = (jn, o.data.materials[0].name) if n not in KEEP_SEPARATE else (jn, n)
        groups.setdefault(key, []).append(n)
    low_sets = {}
    high_of = {}  # low mesh -> its joined full-resolution twin (normal-map source)
    for (jn, tag), names in groups.items():
        obj = join([by_name[n] for n in names], f"{jn or 'base'}__{tag}")
        obj.parent = empties[jn] if jn else root
        obj.matrix_parent_inverse = obj.parent.matrix_world.inverted()
        low_sets.setdefault(set_of(jn), []).append(obj)
        if highs:
            high_of[obj] = join([highs[n] for n in names], f"high__{obj.name}")

    OUT.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)
    for f in WORK.glob("r3x_*"):
        f.unlink()
    if BAKE:
        bake_textures(low_sets, high_of, WORK)
        for o in high_of.values():
            bpy.data.objects.remove(o)
    # One glTF material per (class, texture set): "<class>" (body atlas), "<class>__head".
    for o in low_sets.get("head", []):
        for i, m in enumerate(o.data.materials):
            o.data.materials[i] = get_material(f"{m.name}__head")
    raw = WORK / "r3x_raw.glb"
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.export_scene.gltf(
        filepath=str(raw), export_format="GLB", use_selection=False, export_apply=True,
        export_yup=True, export_normals=True, export_tangents=BAKE, export_materials="EXPORT",
        export_draco_mesh_compression_enable=False, export_extras=False,
    )
    print(f"[r3x] wrote {raw} ({raw.stat().st_size / 1e6:.1f} MB); pack-model.mjs makes r3x.glb")
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
    print(f"[r3x] wrote {OUT / 'rig.json'}")

    if PREVIEW:
        render_previews()


# --------------------------------------------------------------------------------------
# Texture bake (visual only: nothing here changes joints, anchors or rig.json)
# --------------------------------------------------------------------------------------


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


def load_palette():
    return json.loads(PALETTE.read_text())


def srgb_to_linear(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def hex_linear(h):
    """'#rrggbb' (sRGB, as three.js Color) -> linear RGB tuple."""
    h = h.lstrip("#")
    return tuple(srgb_to_linear(int(h[i:i + 2], 16) / 255) for i in (0, 2, 4))


def setup_cycles():
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
        prefs.compute_device_type = "METAL"
        prefs.get_devices()
        for d in prefs.devices:
            d.use = d.type == "METAL"
        scene.cycles.device = "GPU"
        print(f"[r3x] bake: devices {[(d.name, d.type) for d in prefs.devices if d.use]}")
    except Exception as e:  # no Metal: CPU works, just slower
        print(f"[r3x] bake: GPU unavailable ({e}); using CPU")
        scene.cycles.device = "CPU"
    scene.cycles.use_denoising = False
    scene.render.bake.margin_type = "EXTEND"


class BakeMaterials:
    """One emission material per class; use(<pass>) rewires what they all emit.

    Passes (float RGB, one texel per atlas pixel):
      pos    - world position (rest pose; = the mesh's own space in the GLB)
      nrm    - world shading normal
      mask   - R ambient occlusion (broad 12 cm x crevice 1.5 cm), G exposed-edge
               (1 - N_bevel.N from a 4 mm Bevel node), B class id
      noise1 - fBm at 5, 26 and 190 /m (large drift, mid, fine chips)
      noise2 - vertical streak fBm, horizontal-scratch noise, scuff patch fBm
    """

    def __init__(self, classes):
        self.mats, self.sockets, self.texs, self.emits = {}, [], [], []
        for idx, cls in enumerate(classes):
            m = bpy.data.materials.new(f"bake_{cls}")
            if m.node_tree is None:
                m.use_nodes = True
            nt = m.node_tree
            N, L = nt.nodes, nt.links
            N.clear()
            out = N.new("ShaderNodeOutputMaterial")
            emit = N.new("ShaderNodeEmission")
            L.new(emit.outputs["Emission"], out.inputs["Surface"])
            geo = N.new("ShaderNodeNewGeometry")

            def math_node(op, a, b=None, clamp=False):
                n = N.new("ShaderNodeMath")
                n.operation = op
                n.use_clamp = clamp
                for i, v in enumerate((a, b)):
                    if v is None:
                        continue
                    if isinstance(v, (int, float)):
                        n.inputs[i].default_value = v
                    else:
                        L.new(v, n.inputs[i])
                return n.outputs[0]

            def ao(distance):
                n = N.new("ShaderNodeAmbientOcclusion")
                n.samples = 16
                n.only_local = False
                n.inputs["Distance"].default_value = distance
                return n.outputs["AO"]

            def combine(a, b, c):
                n = N.new("ShaderNodeCombineXYZ")
                for i, v in enumerate((a, b, c)):
                    if isinstance(v, (int, float)):
                        n.inputs[i].default_value = v
                    else:
                        L.new(v, n.inputs[i])
                return n.outputs[0]

            def noise(scale, offset=0.0, detail=3.0):
                vm = N.new("ShaderNodeVectorMath")
                vm.operation = "MULTIPLY_ADD"
                L.new(geo.outputs["Position"], vm.inputs[0])
                vm.inputs[1].default_value = scale if isinstance(scale, tuple) else (scale,) * 3
                vm.inputs[2].default_value = (offset,) * 3
                n = N.new("ShaderNodeTexNoise")
                n.noise_dimensions = "3D"
                if hasattr(n, "noise_type"):
                    n.noise_type = "FBM"
                if hasattr(n, "normalize"):
                    n.normalize = True
                n.inputs["Scale"].default_value = 1.0
                n.inputs["Detail"].default_value = detail
                n.inputs["Roughness"].default_value = 0.5
                if "Lacunarity" in n.inputs:
                    n.inputs["Lacunarity"].default_value = 2.03
                n.inputs["Distortion"].default_value = 0.0
                L.new(vm.outputs[0], n.inputs["Vector"])
                return n.outputs["Fac"]

            occlusion = math_node("MULTIPLY", math_node("POWER", ao(0.12), 0.8), ao(0.015))
            bevel = N.new("ShaderNodeBevel")
            bevel.samples = 8
            bevel.inputs["Radius"].default_value = 0.004
            dot = N.new("ShaderNodeVectorMath")
            dot.operation = "DOT_PRODUCT"
            L.new(bevel.outputs["Normal"], dot.inputs[0])
            L.new(geo.outputs["Normal"], dot.inputs[1])
            edge = math_node("MULTIPLY", math_node("SUBTRACT", 1.0, dot.outputs["Value"]), 5.0, clamp=True)
            # Blender is Z-up: three's (x, y, z) = Blender (x, z, -y). Streaks run down
            # (fast across x/z, slow along up); scratches are horizontal (fast along up).
            sockets = {
                "pos": geo.outputs["Position"],
                "nrm": geo.outputs["Normal"],
                "mask": combine(occlusion, edge, (idx + 1) / 64.0),
                "noise1": combine(noise(5.0), noise(26.0, 3.1), noise(190.0, 7.3)),
                "noise2": combine(noise((70.0, 70.0, 5.0)), noise((18.0, 18.0, 700.0), 0.0, 0.0),
                                  noise(9.0, 11.0)),
            }
            tex = N.new("ShaderNodeTexImage")
            N.active = tex
            self.mats[cls] = m
            self.sockets.append(sockets)
            self.texs.append(tex)
            self.emits.append(emit)

    def set_image(self, img):
        for t in self.texs:
            t.image = img
            t.id_data.nodes.active = t

    def use(self, name):
        for s, e in zip(self.sockets, self.emits):
            s[name].id_data.links.new(s[name], e.inputs["Color"])

    def remove(self):
        for m in self.mats.values():
            bpy.data.materials.remove(m)


def image_array(img):
    import numpy as np
    w, h = img.size
    a = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(a)
    return a.reshape(h, w, 4)[..., :3].copy()


def downsample(arr, size, renormalize=False):
    """Box-filter an (h, w, 3) texture down to size x size (power-of-two factor)."""
    k = arr.shape[0] // size
    if k <= 1:
        return arr
    h, w, c = arr.shape
    out = arr.reshape(h // k, k, w // k, k, c).mean(axis=(1, 3))
    if renormalize:  # encoded unit normals: decode, renormalize, encode
        n = out * 2 - 1
        n /= (n ** 2).sum(-1, keepdims=True) ** 0.5 + 1e-6
        out = n * 0.5 + 0.5
    return out


def save_png(arr, path):
    """(h, w, 3) floats in 0..1, Blender row order (bottom row first) -> 8-bit PNG."""
    import numpy as np
    h, w, _ = arr.shape
    img = bpy.data.images.new(path.stem, w, h, alpha=False)
    img.colorspace_settings.name = "Non-Color"
    rgba = np.concatenate([np.clip(arr, 0, 1), np.ones((h, w, 1), np.float32)], -1)
    img.pixels.foreach_set(rgba.astype(np.float32).ravel())
    img.filepath_raw = str(path)
    img.file_format = "PNG"
    img.save()
    bpy.data.images.remove(img)


def surface_gap(low, high, n=12000):
    """How far the decimated surface strays from the full-resolution one (m, 99.5th pct).

    Distances from random points on the low mesh's triangles to the nearest point of the
    high mesh; both meshes are in world space with identity transforms.
    """
    import numpy as np
    from mathutils.bvhtree import BVHTree
    bvh = BVHTree.FromObject(high, bpy.context.evaluated_depsgraph_get())
    me = low.data
    me.calc_loop_triangles()
    nt = len(me.loop_triangles)
    co = np.empty(len(me.vertices) * 3)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    tri = np.empty(nt * 3, dtype=np.int64)
    me.loop_triangles.foreach_get("vertices", tri)
    v = co[tri.reshape(-1, 3)]
    area = np.linalg.norm(np.cross(v[:, 1] - v[:, 0], v[:, 2] - v[:, 0]), axis=1)
    rng = np.random.default_rng(5)
    pick = rng.choice(nt, size=n, p=area / area.sum())
    r = rng.random((n, 2))
    flip = r.sum(1) > 1
    r[flip] = 1 - r[flip]
    pts = v[pick, 0] + r[:, :1] * (v[pick, 1] - v[pick, 0]) + r[:, 1:] * (v[pick, 2] - v[pick, 0])
    d = [bvh.find_nearest(Vector(p))[3] or 0.0 for p in pts]
    return float(np.percentile(d, 99.5))


def bake_normals(pairs, res):
    """Tangent-space normal map from each high-poly twin onto its low mesh, one pair at a time.

    Baking pairs separately means a ray can only find the part's own full-resolution
    surface, never a neighbour it touches, so the cage can be as deep as that part's
    decimation needs: extrusion = its measured surface gap (+0.4 mm). Heavily decimated
    rings get a deep cage; lightly decimated hero parts (thin grille bars) a shallow one.
    Each bake writes only its own islands (margin 0, no clear); the margin is filled
    afterwards. Texels whose ray still found nothing sensible (z < 0.15: facing away)
    fall back to the low mesh's own normal.
    """
    import numpy as np
    scene = bpy.context.scene
    img = bpy.data.images.new("bake_normal", res, res, alpha=True, float_buffer=True)
    img.colorspace_settings.name = "Non-Color"
    img.generated_color = (0.0, 0.0, 0.0, 0.0)
    was = {o: o.hide_render for o in scene.objects}
    for o in scene.objects:
        o.hide_render = True
    scene.cycles.samples = 4
    seen = np.zeros((res, res), bool)
    try:
        for low, high in pairs:
            gap = surface_gap(low, high)
            ext = max(NORMAL_CAGE_MIN, gap + 0.0004)
            for m in low.data.materials:
                for nd in m.node_tree.nodes:
                    if nd.type == "TEX_IMAGE":
                        nd.image = img
                        m.node_tree.nodes.active = nd
            low.hide_render = high.hide_render = False
            bpy.ops.object.select_all(action="DESELECT")
            high.select_set(True)
            low.select_set(True)
            bpy.context.view_layer.objects.active = low
            bpy.ops.object.bake(type="NORMAL", normal_space="TANGENT", use_selected_to_active=True,
                                cage_extrusion=ext, max_ray_distance=2 * ext + 0.001,
                                margin=0, use_clear=False, target="IMAGE_TEXTURES")
            low.hide_render = high.hide_render = True
            dbg = ""
            if DIAG:  # per-pair diagnostics: coverage of this pair's islands, texels facing away
                a = np.empty(res * res * 4, dtype=np.float32)
                img.pixels.foreach_get(a)
                a = a.reshape(res, res, 4)
                cov = a[..., 3] > 0.5
                new_px = cov & ~seen
                bad_px = new_px & (a[..., 2] < 0.575)
                seen |= cov
                isl = bpy.data.images.new("isl", res, res, alpha=True, float_buffer=True)
                isl.generated_color = (0.0, 0.0, 0.0, 0.0)
                for m in low.data.materials:
                    for nd in m.node_tree.nodes:
                        if nd.type == "TEX_IMAGE":
                            nd.image = isl
                low.hide_render = False
                bpy.ops.object.select_all(action="DESELECT")
                low.select_set(True)
                bpy.ops.object.bake(type="EMIT", margin=0, use_clear=False, target="IMAGE_TEXTURES",
                                    use_selected_to_active=False)
                low.hide_render = True
                b = np.empty(res * res * 4, dtype=np.float32)
                isl.pixels.foreach_get(b)
                bpy.data.images.remove(isl)
                n_isl = (b.reshape(res, res, 4)[..., 3] > 0.5).sum()
                dbg = (f", {new_px.sum()}/{n_isl} texels covered, "
                       f"{bad_px.sum() / max(1, new_px.sum()) * 100:.1f}% facing away")
            print(f"[r3x]   normal {low.name:34s} gap {gap * 1000:.2f} mm, cage {ext * 1000:.2f} mm{dbg}")
        a = np.empty(res * res * 4, dtype=np.float32)
        img.pixels.foreach_get(a)
        a = a.reshape(res, res, 4)
    finally:
        bpy.data.images.remove(img)
        for o, h in was.items():
            o.hide_render = h
    covered = a[..., 3] > 0.5
    if DIAG:
        np.save(WORK / f"pass_raw_normal_{res}_{len(pairs)}.npy", a.astype(np.float16))
    n = a[..., :3] * 2 - 1
    bad = covered & (n[..., 2] < 0.15)
    n[bad] = (0.0, 0.0, 1.0)
    print(f"[r3x]   normal: {bad.sum() / max(1, covered.sum()) * 100:.2f}% of texels fell back to flat")
    return dilate(n * 0.5 + 0.5, covered, 16)


def dilate(arr, covered, px):
    """Extend covered texels outward by `px` (bake margin for the per-pair normal bake)."""
    import numpy as np
    arr = arr.copy()
    covered = covered.copy()
    for _ in range(px):
        acc = np.zeros_like(arr)
        cnt = np.zeros(covered.shape, np.float32)
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            m = np.roll(covered, (dy, dx), (0, 1))
            acc += np.roll(arr, (dy, dx), (0, 1)) * m[..., None]
            cnt += m
        grow = ~covered & (cnt > 0)
        arr[grow] = acc[grow] / cnt[grow][:, None]
        covered |= grow
    arr[~covered] = (0.5, 0.5, 1.0)
    return arr


def ensure_xatlas():
    """xatlas (pip, into .work/pydeps) packs UV charts ~3x tighter than Blender's packer."""
    import importlib
    import subprocess
    deps = WORK / "pydeps"
    if str(deps) not in sys.path:
        sys.path.insert(0, str(deps))
    try:
        return importlib.import_module("xatlas")
    except ImportError:
        pass
    print("[r3x] installing xatlas into .work/pydeps (UV packing)")
    r = subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--target", str(deps), "xatlas"])
    if r.returncode != 0:
        return None
    importlib.invalidate_caches()
    try:
        return importlib.import_module("xatlas")
    except ImportError:
        return None


def pack_uvs(objs, res, padding_px=4):
    """Pack the UV charts of `objs` into one atlas.

    The CAD parts unwrap into ~10k islands, many of them rings and thin arcs: Blender's
    packer fills ~20% of the square with those, xatlas (rasterised, fills holes) ~75%.
    Chart shapes and relative sizes (texel weights) are kept; only placement changes.
    """
    import numpy as np
    xatlas = ensure_xatlas()
    if xatlas is None:
        print("[r3x] xatlas unavailable: Blender's UV packer (much lower texel density)")
        edit_all(objs)
        bpy.ops.uv.pack_islands(udim_source="CLOSEST_UDIM", rotate=True, scale=True,
                                margin_method="FRACTION", margin=padding_px / res, shape_method="CONCAVE")
        bpy.ops.object.mode_set(mode="OBJECT")
        return
    inputs, meta = [], []
    for o in objs:
        me = o.data
        me.calc_loop_triangles()
        nl = len(me.loops)
        uv = np.empty(nl * 2, np.float32)
        me.uv_layers.active.data.foreach_get("uv", uv)
        uv = uv.reshape(-1, 2)
        lv = np.empty(nl, np.int64)
        me.loops.foreach_get("vertex_index", lv)
        # One chart vertex per (mesh vertex, UV): loops that share both are welded, so
        # charts follow the UV islands.
        key = np.stack([lv, *np.round(uv * 1e6).astype(np.int64).T], 1)
        _, inv = np.unique(key, axis=0, return_inverse=True)
        inv = inv.ravel()
        tl = np.empty(len(me.loop_triangles) * 3, np.int64)
        me.loop_triangles.foreach_get("loops", tl)
        tri = inv[tl].reshape(-1, 3)
        # Triangles squashed to ~zero UV area (skinny CAD facets, decimation) would drag the
        # corners they share with a real island across the atlas when xatlas places them:
        # give them their own corners so they only form (harmless) point-sized charts.
        t_uv = uv[tl].reshape(-1, 3, 2)
        e1, e2 = t_uv[:, 1] - t_uv[:, 0], t_uv[:, 2] - t_uv[:, 0]
        uv_area = np.abs(e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0]) / 2
        deg = uv_area < 1e-3 * max(1e-20, float(np.median(uv_area)))
        n_v = inv.max() + 1
        tri[deg] = n_v + np.arange(deg.sum() * 3).reshape(-1, 3)
        corner_uv = np.zeros((n_v + deg.sum() * 3, 2), np.float32)
        corner_uv[inv] = uv
        corner_uv[tri[deg].ravel()] = t_uv[deg].reshape(-1, 2)
        # The smart-project atlas spans every part, so islands are tiny in UV units; at that
        # scale xatlas treats real triangles as degenerate and splits charts without
        # splitting their vertices. Scaling is free: xatlas picks its own texel density.
        inputs.append((corner_uv * 1000.0, tri.astype(np.uint32)))
        meta.append((o, tl, nl))

    def generate(texels_per_unit):
        atlas = xatlas.Atlas()
        for c_uv, c_tri in inputs:
            atlas.add_uv_mesh(c_uv, c_tri)
        po = xatlas.PackOptions()
        po.resolution = res
        po.padding = padding_px
        po.bilinear = True
        po.rotate_charts = True
        po.texels_per_unit = texels_per_unit
        atlas.generate(xatlas.ChartOptions(), po)
        return atlas
    # xatlas's automatic density ignores `resolution`; search for the densest one that still
    # fits a single res x res page, so the padding is in real texels.
    auto = generate(0.0)
    hi = auto.texels_per_unit * res / max(auto.width, auto.height)
    lo, atlas = hi * 0.6, None
    for _ in range(6):
        mid = (lo + hi) / 2 if atlas is not None else lo
        a = generate(mid)
        if a.atlas_count == 1 and max(a.width, a.height) <= res:
            lo, atlas = mid, a
        else:
            hi = mid
    if atlas is None:
        raise RuntimeError("xatlas could not fit the charts on one page")
    # xatlas normalises u and v by the atlas width and height separately; keep it square.
    side = max(atlas.width, atlas.height)
    sx, sy = atlas.width / side, atlas.height / side
    used = 0.0
    for i, (o, tl, nl) in enumerate(meta):
        _v, idx, uvs = atlas[i]
        out = np.zeros((nl, 2), np.float32)
        out[tl] = uvs[idx.ravel()] * (sx, sy)
        o.data.uv_layers.active.data.foreach_set("uv", out.ravel())
        t = out[tl].reshape(-1, 3, 2)
        e1, e2 = t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]
        used += float(np.abs(e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0]).sum() / 2)
    # `used` > ~0.8 would mean overlapping triangles (a packing bug), not a dense atlas.
    print(f"[r3x] xatlas: {atlas.chart_count} charts, {atlas.utilization * 100:.0f}% of the atlas used "
          f"(UV area sum {used:.2f})")
    if used > 0.95:
        raise RuntimeError("xatlas packing overlaps")


def bake_textures(low_sets, high_of, work):
    """Bake the PBR texture sets, one per UV atlas (head, body).

    Per set: pack the set's UV islands into one atlas, then

    1. a tangent-space NORMAL bake from the undecimated, weighted-normal STL meshes onto
       the decimated ones, pair by pair - see bake_normals();
    2. on a joined copy of the set's meshes (Cycles re-syncs the scene per baked object,
       so one object bakes ~30x faster), emission bakes of the passes in BakeMaterials
       (masks, noise, position, normal);
    3. paint_texels() turns those into baseColor / ORM and adds the paint-chip relief to
       the normal map - the same weathering model the sim used to run per fragment.
    """
    import time
    import numpy as np
    scene = bpy.context.scene
    palette = load_palette()
    classes = list(palette["classes"])
    setup_cycles()
    mats = BakeMaterials(classes)
    all_lows = [o for objs in low_sets.values() for o in objs]
    try:
        for tag, lows in low_sets.items():
            if tag not in BAKE_SETS:
                continue
            t0 = time.time()
            res = BAKE_RES[tag]
            pack_uvs(lows, res)
            print(f"[r3x] bake {tag}: packed {len(lows)} meshes in {time.time() - t0:.1f}s")

            bpy.ops.object.select_all(action="DESELECT")
            copies = []
            for o in lows:
                c = o.copy()
                c.data = o.data.copy()
                c.parent = None
                c.matrix_world = o.matrix_world.copy()
                scene.collection.objects.link(c)
                for i, m in enumerate(c.data.materials):
                    c.data.materials[i] = mats.mats[m.name]
                copies.append((c, high_of[o]))
            passes = {}
            baker = None
            try:
                t = time.time()
                passes["normal"] = bake_normals(copies, res)
                print(f"[r3x] bake {tag}: normal from {sum(tri_count(h) for _, h in copies)} tris "
                      f"in {time.time() - t:.1f}s")
                baker = join([c for c, _ in copies], f"baker_{tag}")
                # Rays see the rest of the droid (AO from the other set) but not this set's
                # originals, which sit exactly where the baker is, nor the high-poly sources.
                for o in all_lows:
                    o.hide_render = o in lows
                for h in high_of.values():
                    h.hide_render = True
                for name, samples in (("mask", 24), ("noise1", 4), ("noise2", 4), ("pos", 1), ("nrm", 4)):
                    t = time.time()
                    img = bpy.data.images.new(f"bake_{name}", res, res, alpha=False, float_buffer=True)
                    img.colorspace_settings.name = "Non-Color"
                    mats.set_image(img)
                    mats.use(name)
                    scene.cycles.samples = samples
                    bpy.ops.object.select_all(action="DESELECT")
                    baker.select_set(True)
                    bpy.context.view_layer.objects.active = baker
                    bpy.ops.object.bake(type="EMIT", margin=16, use_clear=True, target="IMAGE_TEXTURES",
                                        use_selected_to_active=False)
                    passes[name] = image_array(img)
                    bpy.data.images.remove(img)
                    print(f"[r3x] bake {tag}: {name} {res}px in {time.time() - t:.1f}s")
            finally:
                for c in [baker] if baker else [c for c, _ in copies]:
                    bpy.data.objects.remove(c)
                for o in all_lows:
                    o.hide_render = False
            t = time.time()
            if KEEP_PASSES:
                for k, v in passes.items():
                    np.save(work / f"pass_{tag}_{k}.npy", v.astype(np.float16 if k != "pos" else np.float32))
            base, orm, normal = paint_texels(passes, palette, classes)
            del passes
            for kind, arr in (("basecolor", base), ("orm", orm), ("normal", normal)):
                save_png(downsample(arr, TEX_RES[tag, kind], kind == "normal"), work / f"r3x_{tag}_{kind}.png")
            print(f"[r3x] bake {tag}: painted + saved in {time.time() - t:.1f}s "
                  f"(set total {time.time() - t0:.1f}s)")
    finally:
        mats.remove()


# Normal bake: shallowest cage (m); each pair's cage is its measured surface gap + 0.4 mm.
NORMAL_CAGE_MIN = 0.0005
# Paint-chip relief in the normal map: chips are recessed by the paint thickness.
PAINT_THICKNESS_M = 0.00035
RELIEF_MAX_SLOPE = 1.2


def _value_noise_np(p):
    """numpy port of the sim's former GLSL value noise (look.ts wHash/wNoise)."""
    import numpy as np

    def h(q):
        q = np.mod(q * 0.3183099 + np.array([0.71, 0.113, 0.419]), 1.0) * 17.0
        return np.mod(q[:, 0] * q[:, 1] * q[:, 2] * (q[:, 0] + q[:, 1] + q[:, 2]), 1.0)
    i = np.floor(p)
    f = p - i
    f = f * f * (3 - 2 * f)
    c = {}
    for dx in (0, 1):
        for dy in (0, 1):
            for dz in (0, 1):
                c[dx, dy, dz] = h(i + np.array([dx, dy, dz]))
    x0 = [c[0, a, b] + (c[1, a, b] - c[0, a, b]) * f[:, 0] for a in (0, 1) for b in (0, 1)]
    y0 = x0[0] + (x0[2] - x0[0]) * f[:, 1]
    y1 = x0[1] + (x0[3] - x0[1]) * f[:, 1]
    return y0 + (y1 - y0) * f[:, 2]


def _fbm_np(p):
    s, a = 0.0, 0.5
    for _ in range(4):
        s = s + a * _value_noise_np(p)
        p = p * 2.03 + [1.7, 9.2, 3.1]
        a *= 0.5
    return s / 0.9375


def paint_texels(passes, palette, classes):
    """The weathering model, per texel. Returns (baseColor sRGB, ORM, normal) in 0..1.

    A port of the per-fragment shader the sim used to run (look.ts, before textures):
    colour drift and patchy fading, crevice grime and rain-down streaks, chipped paint
    (a primer rim around bare metal) on exposed edges, fine scuffs and a dust film on
    up-facing surfaces - each also moving roughness and metalness. Blender's Perlin fBm
    is remapped onto the old value-noise distribution (quantile match), so the
    thresholds keep their meaning.
    """
    import numpy as np
    f32 = np.float32
    mask, n1, n2 = passes["mask"], passes["noise1"], passes["noise2"]
    occ = mask[..., 0]
    edge = mask[..., 1]
    cid = np.clip(np.rint(mask[..., 2] * 64).astype(np.int32) - 1, 0, len(classes) - 1)
    pos_bl, nrm_bl = passes["pos"], passes["nrm"]
    P = np.stack([pos_bl[..., 0], pos_bl[..., 2], -pos_bl[..., 1]], -1)  # three.js axes
    Ny = nrm_bl[..., 2]

    rng = np.random.default_rng(3)
    probe = rng.uniform(0, 50, (200000, 3))
    qs = np.linspace(0, 1, 513)
    fbm_q = np.quantile(_fbm_np(probe), qs)
    val_q = np.quantile(_value_noise_np(probe), qs)
    sample = rng.integers(0, occ.size, 400000)

    def match(x, target):
        src = np.quantile(x.ravel()[sample], qs)
        src = np.maximum.accumulate(src + qs * 1e-7)  # strictly increasing for interp
        return np.interp(x, src, target).astype(f32)
    nBig, nMid, nFine = match(n1[..., 0], fbm_q), match(n1[..., 1], fbm_q), match(n1[..., 2], fbm_q)
    streak, scr, scuffP = match(n2[..., 0], fbm_q), match(n2[..., 1], val_q), match(n2[..., 2], fbm_q)

    def ss(e0, e1, x):
        t = np.clip((x - e0) / (e1 - e0), 0, 1)
        return t * t * (3 - 2 * t)

    def table(key, default=0.0, color=False):
        out = []
        for cls in classes:
            c = palette["classes"][cls]
            w = c.get("weather") or {}
            v = c.get(key, w.get(key, default)) if key in ("color", "roughness", "metalness") else w.get(key, default)
            if color:
                v = hex_linear(v) if v else (0.0, 0.0, 0.0)
            out.append(v)
        return np.array(out, dtype=f32)[cid]

    weathered = np.array([bool(palette["classes"][c].get("weather")) for c in classes])[cid]
    col = table("color", color=True)
    # Painted bands (the visor's cream bars), under the weathering.
    tex_m = np.median(np.linalg.norm(P[:, 1:] - P[:, :-1], axis=-1))  # ~ texel size, m
    for k, cls in enumerate(classes):
        st = palette["classes"][cls].get("stripes")
        if not st:
            continue
        sel = cid == k
        x = np.abs(P[..., 0]) if st["mirrorX"] else P[..., 0]
        u = (x * st["dir"][0] + P[..., 2] * st["dir"][1]) / st["periodM"]
        s = np.mod(u, 1.0)
        w = max(1e-3, 1.5 * tex_m / st["periodM"])
        band = (ss(0, w, s) * (1 - ss(st["duty"] - w, st["duty"], s)))[..., None]
        col = np.where(sel[..., None], col + (np.array(hex_linear(st["color"]), f32) - col) * band, col)

    def mix(a, b, t):
        return a + (b - a) * t

    exposed = ss(0.55, 0.9, occ)
    col = col * (1 + (nBig - 0.5) * 2 * table("variation"))[..., None]
    lum = (col @ np.array([0.2126, 0.7152, 0.0722], f32))[..., None]
    col = mix(col, lum * 1.08, (table("fade") * ss(0.35, 0.8, nBig) * exposed)[..., None])

    crevice = 1 - ss(0.45, 0.97, occ)
    grime = crevice * (0.55 + 0.9 * nMid)
    side = 1 - np.abs(Ny)
    grime += ss(0.52, 0.85, streak) * table("streaks") * side * (0.4 + nBig)
    grime += 0.16 * ss(0.4, 0.9, nMid * 0.6 + nBig * 0.4) * (1 - exposed * 0.4)
    grime = np.clip(grime * table("grime"), 0, 0.85)
    col = mix(col, table("grimeColor", color=True), grime[..., None])

    wear = table("wear")
    has_wear = (wear > 0.001).astype(f32)
    has_primer = np.array([(palette["classes"][c].get("weather") or {}).get("primerColor") is not None
                           for c in classes], dtype=f32)[cid]
    # Edge wear clusters where parts get handled (nMid, nBig) and breaks up into chips
    # (nFine), so some edges are worn through and others nearly clean.
    chipV = np.power(edge, 0.7) * exposed * (0.55 + 1.1 * nMid) * (0.7 + 0.6 * nBig) + (nFine - 0.5) * 0.8
    wT = 1 - wear * 0.6
    # Transitions a little softer than the old per-pixel shader's: a texel is ~0.25-1 mm,
    # and a hard step would show as stair-steps up close.
    metal = ss(wT - 0.01, wT + 0.04, chipV) * has_wear
    primer = ss(wT - 0.11, wT - 0.06, chipV) * has_primer * has_wear
    scuff = ss(0.93, 0.985, 1 - np.abs(scr - 0.5) * 2) * ss(0.55, 0.75, scuffP) * table("scuffs") * exposed
    primer = np.maximum(primer, scuff * 0.8)
    primer_c = np.where((has_primer > 0)[..., None], table("primerColor", color=True), table("wearColor", color=True))
    col = mix(col, primer_c, (primer * (1 - metal))[..., None])
    col = mix(col, table("wearColor", color=True) * (0.8 + 0.4 * nFine)[..., None], metal[..., None])

    dust_k = table("dust")
    dust = ss(0.35, 0.95, Ny) * exposed * dust_k * (0.35 + 0.65 * ss(0.3, 0.8, nMid))
    dust += crevice * dust_k * 0.25 * ss(0.0, 0.6, Ny)
    dust = np.clip(dust, 0, 0.6)
    col = mix(col, np.array(hex_linear(palette["dust"]), f32), dust[..., None])

    rough = np.clip(table("roughness") * (1 + (nMid - 0.5) * 2 * table("roughVar")), 0.05, 1.0)
    rough = mix(rough, 0.85, grime)
    rough = mix(rough, 0.7, primer * (1 - metal))
    rough = mix(rough, table("wearRoughness") + nFine * 0.15, metal)
    rough = mix(rough, 0.95, dust)
    metalness = table("metalness")
    metalness = mix(metalness, 0.0, np.maximum(grime * 0.8, dust))
    metalness = mix(metalness, 0.0, primer * (1 - metal))
    metalness = mix(metalness, table("wearMetalness"), metal)

    # Unweathered classes (eye lens, light pipe) keep their flat paint.
    flat = ~weathered
    col = np.where(flat[..., None], table("color", color=True), col)
    rough = np.where(flat, table("roughness"), rough)
    metalness = np.where(flat, table("metalness"), metalness)

    # Paint relief: chips sit a paint-thickness below the coat, primer a little less.
    # Texture-space slope -> tangent-space normal. (No all-over waviness: it would cost
    # most of the normal map's compressed size for a barely visible effect.)
    hgt = ((1 - metal) * PAINT_THICKNESS_M + primer * (1 - metal) * 0.00008) * weathered

    def slope(axis):
        a = [slice(None)] * 2
        b = [slice(None)] * 2
        a[axis], b[axis] = slice(2, None), slice(None, -2)
        dh = hgt[tuple(a)] - hgt[tuple(b)]
        ds = np.linalg.norm(P[tuple(a)] - P[tuple(b)], axis=-1)
        s = np.where((ds > 1e-7) & (ds < 8 * tex_m), dh / np.maximum(ds, 1e-7), 0.0)
        pad = [(0, 0), (0, 0)]
        pad[axis] = (1, 1)
        return np.clip(np.pad(s, pad), -RELIEF_MAX_SLOPE, RELIEF_MAX_SLOPE)
    su, sv = slope(1), slope(0)  # columns run along U, rows along V (Blender row order)
    nb = passes["normal"] * 2 - 1
    nb = np.stack([nb[..., 0] - su * nb[..., 2], nb[..., 1] - sv * nb[..., 2], nb[..., 2]], -1)
    nb /= np.maximum(np.linalg.norm(nb, axis=-1, keepdims=True), 1e-6)

    base = np.where(col <= 0.0031308, col * 12.92, 1.055 * np.power(np.maximum(col, 0), 1 / 2.4) - 0.055)
    orm = np.stack([occ, rough, metalness], -1)
    return base.astype(f32), orm.astype(f32), (nb * 0.5 + 0.5).astype(f32)


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
