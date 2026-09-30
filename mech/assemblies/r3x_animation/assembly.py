"""R-3X Animation mechanisms (community motorised build) attached to the kit backbone.

Source: Drive `MOB/Projects/DJ-R3X/R-3X Animation` (vendored to mech/vendor/animation/, never
committed): per-studio STLs (+ the same parts as STEP in r3x-internal-step/), assembly PNGs
01-06, `07 - r3x hardware.txt`, `08 - r3x maestro sample script.txt`.

Every Onshape part studio is Z-up with its own origin. The files carry no mates, so each part
is placed here by a mate written out in code, with the feature that defines it (`evidence`)
and whether it is measured (`placement="fitted"`: a feature-to-feature fit, e.g. hole
patterns that coincide) or chosen (`"inferred"`: plausible but not stated by any source).
Joint axes and pivots come from the defining geometry (sector centres, bores, rails), never
from the profile. Numbers quoted in evidence strings are reproduced by `r3xmech.measure`.
"""
from __future__ import annotations

import math

import numpy as np
import trimesh

from r3xmech import catalog, frames
from r3xmech.frames import ZUP, basis, roty, trans
from r3xmech.model import MECH, Asm, Joint, Link, Part

A = MECH / "vendor/animation"
F = {
    "base": A / "r3x - base rotation", "tilt": A / "r3x - head tilt", "hero": A / "r3x - hero arm mods",
    "wrist": A / "r3x - hero arm wrist", "lower": A / "r3x - lower ring animation",
    "neck": A / "r3x - neck support", "top": A / "r3x - top ring animation", "visor": A / "r3x - visor animation",
}


def f(folder, name):
    prefix = {"base": "base rotation - ", "tilt": "head tilt - ", "hero": "hero arm - ", "wrist": "new wrist - ",
              "lower": "r3x-lower-ring-animation - ", "neck": "upper support - ",
              "top": "r3x-top-ring-animation - ", "visor": "visor - "}[folder]
    p = F[folder] / f"{prefix}{name}.stl"
    if not p.exists():
        raise FileNotFoundError(p)
    return p


# ---------------------------------------------------------------------------------------
# Measured numbers (r3xmech.measure reproduces them from the vendored meshes / STEP)
# ---------------------------------------------------------------------------------------
GEAR = {
    # teeth counted on the STL sections; sectors: full-circle equivalent from angular pitch
    "pan_pinion": 15, "pan_sector": 60,                 # 24.0 deg / 6.00 deg pitch -> 4.00 : 1
    "lower_pinion": 25, "lower_sector": 95,             # 14.4 / 3.790 -> 3.80 : 1
    "top_pinion": 20, "top_sector": 85,                 # 18.0 / 4.238 -> 4.25 : 1
    "tilt_pinion": 19, "tilt_center": 360 / 18.33,      # tip-round centres every 18.95 / 18.33 deg -> 1.034 : 1
    "lift_pinion": 19, "lift_rack_pitch_mm": 9.0,       # rack tooth pitch 9.0 mm -> pitch radius 27.2 mm
}
LIFT_PITCH_R = GEAR["lift_pinion"] * GEAR["lift_rack_pitch_mm"] / (2 * math.pi)   # 27.22 mm
LIFT_MM_PER_DEG = LIFT_PITCH_R * math.pi / 180                                    # 0.475
SECTOR_SPAN_DEG = {"pan": 97.7, "lower": 79.6, "top": 59.4}   # toothed arc of each sector
PITCH_DEG = {"pan": 6.0, "lower": 3.79, "top": 4.238}


def sector_travel(key):
    """Ring travel the sector allows: toothed arc less one tooth pitch at each end (so at least
    one full tooth stays engaged). Symmetric about the middle of the arc."""
    return (SECTOR_SPAN_DEG[key] - 2 * PITCH_DEG[key]) / 2


# ---------------------------------------------------------------------------------------
# Heights in the kit (canonical y, mm) - the stack the mechanisms are fitted into
# ---------------------------------------------------------------------------------------
Y_HEAD_FLOOR_TOP = 735.0       # H_Main_1 up-facing floor faces at y 733-736
Y_HEAD_FLOOR_UNDER = 721.0     # H_Main_1 down-facing faces at y 720-726
Y_PEDESTAL_TOP = 342.0         # P_M_3 top
Y_TOP_SR_BOTTOM = 490.0        # TR_SR_Full (top-ring lazy-susan spacer) y 490-493
Y_BASE_STAGE = -76.2           # Morton build: the stage stands on the aluminium base plate (photo
                               # 20250810_165424), below the Lower Frame; plate top = the underside of
                               # Morton's 2020Mount post feet (y -76.2 in his kit-frame export)
TURNTABLE_YAW = 0.0            # clocking of the turntable (+ sector) about the pan axis: not stated
HUNTER_ORIGIN_Y = 738.3        # Hunter's gimbal centre in the body frame (assemblies/hunter_head fit)


def P(id_, name, cls, link, T, file=None, **kw):
    kw.setdefault("origin", "r3x_animation")
    kw.setdefault("kind", "stl" if file is not None else "generated")
    kw.setdefault("material", "PETG" if cls == "mech" else ("servo" if cls == "servo" else "purchased"))
    kw.setdefault("printed", cls == "mech")
    return Part(id=id_, name=name, cls=cls, link=link, T=T, file=file, **kw)


def servo_part(id_, key, link, T, file, evidence, placement="inferred", note=""):
    return P(id_, f"Servo {catalog.SERVOS[key]['listing']}", "servo", link, T, file,
             material=f"servo:{key}", mass_g=catalog.SERVOS[key]["mass_g"], printed=False,
             placement=placement, evidence=evidence, note=note or "stand-in body from the build CAD (DNP)",
             inferred=placement == "inferred", inferred_note="servo pocket position chosen to mesh the gear"
             if placement == "inferred" else "")


def tube_generator(length, r=13.0):
    def g():
        m = trimesh.creation.cylinder(radius=r, height=length, sections=48)
        m.apply_translation([0, 0, length / 2])
        return m
    return g


def coil_generator(r=21.0, wire_r=3.2, h=55.0, turns=5.5, n=240, sides=10):
    """A coil spring along +Y from y = 0 (the sim's neck spring, sim/web/src/rig.ts buildNeckSpring)."""
    def g():
        t = np.linspace(0, 1, n + 1)
        a = t * turns * 2 * np.pi
        c = np.stack([np.cos(a) * r, t * h, np.sin(a) * r], 1)
        tan = np.gradient(c, axis=0)
        tan /= np.linalg.norm(tan, axis=1)[:, None]
        nrm = np.stack([np.cos(a), np.zeros_like(a), np.sin(a)], 1)
        bin_ = np.cross(tan, nrm)
        k = np.linspace(0, 2 * np.pi, sides, endpoint=False)
        ring = c[:, None, :] + wire_r * (np.cos(k)[None, :, None] * nrm[:, None, :] + np.sin(k)[None, :, None] * bin_[:, None, :])
        v = ring.reshape(-1, 3)
        f = []
        for i in range(n):
            for j in range(sides):
                p0, p1 = i * sides + j, i * sides + (j + 1) % sides
                q0, q1 = p0 + sides, p1 + sides
                f += [[p0, q0, p1], [p1, q0, q1]]
        caps = [v[:sides].mean(0), v[-sides:].mean(0)]
        v = np.vstack([v, caps])
        c0, c1 = len(v) - 2, len(v) - 1
        for j in range(sides):
            f.append([c0, (j + 1) % sides, j])
            f.append([c1, n * sides + j, n * sides + (j + 1) % sides])
        return trimesh.Trimesh(v, np.array(f), process=True)
    return g


# Anderson's load-path parts, remodelled (mech/parts/anderson, each in its reference STEP's frame,
# which is the vendored STL's frame too - checked on every build, see swap_parametric)
PARAMETRIC = {
    "headlift-base-mount (1)": "parts.anderson.base_ring",
    "headlift-base-mount": "parts.anderson.pan_ring_gear",
    "base-center": "parts.anderson.base_center",
    "base-servo-top": "parts.anderson.base_servo_top",
    "neck-rotation-servo-mount": "parts.anderson.pan_servo_mount",
    "head-rotate-servo-gea": "parts.anderson.pan_gear",
    "lift-gear": "parts.anderson.lift_gear",
    "slide-platform": "parts.anderson.slide_platform",
    "head-lift-straight-gear": "parts.anderson.lift_rack",
    "pipeclamp": "parts.anderson.tube_clamp",
    "neck-rod-clamp": "parts.anderson.tube_clamp",
    "neck-support-ring-outer": "parts.anderson.neck_support_ring_outer",
    "neck-support-ring-inner": "parts.anderson.neck_support_ring_inner",
    "neck-support-platform": "parts.anderson.neck_support_platform",
    # the ring drives: (module, the module's preset dict for this variant)
    "lower-ring-inner-gear": "parts.anderson.ring_gear",
    "top-ring-inner-gear": ("parts.anderson.ring_gear", "TOP"),
    "top-ring-servo-gear": "parts.anderson.ring_servo_gear",
    "lower-ring-servo-gear": ("parts.anderson.ring_servo_gear", "LOWER"),
    "lower-ring-servo-mount": "parts.anderson.ring_servo_mount",
    "top-ring-servo-mount-main": ("parts.anderson.ring_servo_mount", "MAIN"),
    "top-ring-servo-mount-spacer": ("parts.anderson.ring_servo_mount", "SPACER"),
}
FRAME_TOL_MM = 0.5  # parametric vs vendored STL bounds: past this, the frames differ and the swap is refused


def swap_parametric(parts: list, report: list):
    """The PARAMETRIC hook: every part whose vendored STL has a parametric model takes the model's
    mesh (same frame), keeping its placement. The bounds against the STL are checked and noted."""
    from r3xmech.meshes import load_file
    from workbench.geom import parametric_mesh

    for p in parts:
        if p.file is None:
            continue
        stem = p.file.stem.split(" - ", 1)[-1]
        mod = PARAMETRIC.get(stem)
        if mod is None:
            continue
        preset = {}
        if isinstance(mod, tuple):
            import importlib

            mod, name = mod
            preset = dict(getattr(importlib.import_module(mod), name))
        m, feats, prm = parametric_mesh(mod, preset)
        mod = mod + (f"({', '.join(f'{k}={v}' for k, v in preset.items() if k == 'kind' or k == 'teeth')})" if preset else "")
        dev = float(np.abs(m.bounds - load_file(str(p.file)).bounds).max())
        if dev > FRAME_TOL_MM:
            report.append(f"{p.id}: {mod} NOT swapped (bounds {dev:.2f} mm off the STL: another frame)")
            continue
        ref = p.file.name
        p.generator = (lambda m=m: m.copy())
        p.file = None
        p.kind, p.origin, p.cad = "parametric", "ours", "parametric"
        p.evidence = (p.evidence + "; " if p.evidence else "") + f"{mod} (ref {ref}; bounds within {dev:.2f} mm)"
        report.append(f"{p.id}: {mod} ({dev:.2f} mm)")


MESHES = (("pan_pinion", "pan_sector", 15), ("lift_pinion", "lift_rack", 19), ("lower_pinion", "lower_sector", 25),
          ("top_pinion", "top_sector", 20))


def phase_gears(parts: list, report: list):
    """Each pinion turned about its own axis to mesh with its sector/rack at rest: the phase (within
    one tooth pitch, 0.25 deg steps) with the least shared volume. The placements came from the
    CAD's positions, not its tooth phase; unphased teeth sit 1-2 mm inside each other."""
    import manifold3d as mf

    from r3xmech.meshes import part_mesh
    from workbench.geom import _cache_key, cached

    by = {p.id: p for p in parts}

    def man(m):
        return mf.Manifold(mf.Mesh(vert_properties=np.asarray(m.vertices, np.float32), tri_verts=np.asarray(m.faces, np.uint32)))

    for pid, gid, teeth in MESHES:
        if pid not in by or gid not in by:
            continue
        P_, G_ = by[pid], by[gid]
        pm, gm = part_mesh(P_), part_mesh(G_)
        if not (pm.is_watertight and gm.is_watertight):
            report.append(f"{pid}: not phased (open mesh)")
            continue
        # the pinion's axis: its thinnest principal direction, through its centroid
        ev, vec = np.linalg.eigh(np.cov((pm.vertices - pm.centroid).T))
        ax = vec[:, 0]
        c = pm.centroid

        def turned(deg):
            return trimesh.transformations.rotation_matrix(math.radians(deg), ax, c)

        def shared(deg):
            m = pm.copy()
            m.apply_transform(turned(deg))
            return (man(m) ^ man(gm)).volume()

        key = _cache_key("gear-phase", pid, gid, np.round(pm.bounds, 3).tolist(), np.round(gm.bounds, 3).tolist(),
                         len(pm.faces), len(gm.faces))

        def best():
            steps = np.arange(0.0, 360.0 / teeth, 0.25)
            vols = [shared(float(d)) for d in steps]
            k = int(np.argmin(vols))
            return float(steps[k]), float(vols[0]), float(vols[k])

        deg, v0, v1 = cached(key, best)
        if deg:
            P_.T = turned(deg) @ P_.T
        report.append(f"{pid} turned {deg:g} deg on its axis: shared with {gid} {v0:.0f} -> {v1:.0f} mm3")


SPRING_Y, SPRING_H = 608.5, 55.0  # the sim's spring: on the top cap (TR_N), 55 mm at head lift 0


# ---------------------------------------------------------------------------------------
def neck_drive(base_asm: Asm) -> tuple[Asm, dict]:
    """Head pan + head lift stage in the base, the neck tube and the upper neck guide."""
    a = Asm(id="r3x_neck_drive", name="R-3X neck drive: head pan (base turntable) + head lift (rack) + neck tube",
            mount_link="base",
            description="A 10 in lazy susan turntable in the base carries a 2020/MGN12 lift tower; a 35 kg "
                        "servo on the turntable walks a 15T pinion round a fixed 60T internal sector (pan); a "
                        "60 kg servo on the tower drives a 19T pinion on a rack on the carriage (lift). The "
                        "carriage clamps the neck tube, which runs up through all three rings to the head.",
            links=[Link("neck_stage", "Pan stage ring + sector (fixed to the base)", None),
                   Link("turntable", "Pan turntable + lift tower", "head_pan"),
                   Link("slide", "Lift carriage + neck tube", "head_lift")])
    # Frame B: base-rotation studio (Z up), origin on the pan axis, z = 0 at the underside of
    # the stage ring. Canonical: B -> trans(0, Y_BASE_STAGE, 0) @ ZUP (@ roty for the fixed ring).
    TB = trans(0, Y_BASE_STAGE, 0) @ ZUP
    TB_fixed = trans(0, Y_BASE_STAGE, 0) @ roty(38.0) @ ZUP
    zt = 9.5  # turntable disc sits on a ~9.5 mm 10 in lazy susan inside the stage ring (inferred)
    Tt = TB @ trans(0, 0, zt)
    ev_ring = ("headlift-base-mount (sector) and headlift-base-mount (1) share a 4-hole R118.5 bolt circle "
               "and the R111.5/R127.5 walls; base-center disc R110 turns inside R111.5")
    a.parts += [
        P("stage_ring", "headlift-base-mount (1): stage ring (fixed)", "mech", "neck_stage", TB_fixed,
          f("base", "headlift-base-mount (1)"), placement="fitted", evidence=ev_ring,
          note="Morton frame variant: its Lower Frame takes this slot (y 35.1-73.2) instead"),
        P("pan_sector", "headlift-base-mount: 60T internal pan sector (fixed)", "mech", "neck_stage",
          TB_fixed @ trans(0, 0, 38), f("base", "headlift-base-mount"), placement="fitted",
          evidence=ev_ring + "; stacked on the ring (same radial band)"),
        P("pan_turntable", "base-center: turntable + lift tower", "mech", "turntable", Tt,
          f("base", "base-center"), placement="inferred", evidence="disc R110 concentric with the sector (origin)",
          inferred=True, inferred_note="stage height in the base (y=35) and lazy-susan thickness assumed"),
        P("lift_servo_cap", "base-servo-top", "mech", "turntable", Tt, f("base", "base-servo-top"),
          placement="fitted", evidence="footprint equals the base-center servo column (x -88..-33, y 34..75)"),
        P("pan_servo_mount", "neck-rotation-servo-mount", "mech", "turntable", Tt @ trans(0, -80, 10),
          f("base", "neck-rotation-servo-mount"), placement="fitted",
          evidence="its 4 holes (+-25, +-15) coincide with base-center holes around (0, -80)"),
        servo_part("pan_servo", "SERVO_35KG_270", "turntable", Tt @ trans(0, -80, 14),
                   f("base", "servo-standard-dnp (1)"), placement="fitted",
                   evidence="flange holes (+-24.15, +-5.05) = mount holes; shaft at x=-9.8 -> R80.6 from the axis"),
        P("pan_horn", "servo-disc-dnp (horn disc)", "hardware", "turntable", Tt @ trans(-9.8, -80, 14 + 16.8 + 5),
          f("base", "servo-disc-dnp"), mass_g=catalog.PURCHASED["servo_horn_disc"]["mass_g"]),
        P("pan_pinion", "head-rotate-servo-gea: 15T pan pinion", "mech", "turntable",
          Tt @ trans(-9.8, -80, 14 + 16.8 + 7.5), f("base", "head-rotate-servo-gea"), placement="fitted",
          evidence="centre distance 80.6 = R_sector(107.5) - r_pinion(26.9) for 60T:15T at equal pitch"),
        P("lift_rail", "mgn12-rail-with-2020 (150 mm)", "hardware", "turntable", Tt @ trans(0, 51.5, 10),
          f("base", "mgn12-rail-with-2020"), placement="fitted", printed=False,
          mass_g=150 * (catalog.PURCHASED["mgn12_rail_per_mm"]["g_per_mm"] + catalog.PURCHASED["alu_2020_per_mm"]["g_per_mm"]),
          evidence="2020 sits in the base-center pocket x -10..10, y 41..62"),
    ]
    # lift servo (60 kg) standing in the column, shaft along -B y toward the rack
    # servo top (x=-42) under base-servo-top (B z 94.5); shaft (x=-19.5) at B z 72
    T_ls = Tt @ basis((0, 0, -1), (1, 0, 0), (0, -1, 0), origin_to=(-59.7, 45.0, 52.5 - zt))
    a.parts.append(servo_part("lift_servo", "SERVO_60KG_270", "turntable", T_ls, f("base", "servo-standard-dnp"),
                              evidence="top under base-servo-top (z 94.5); shaft on the pinion axis"))
    z_pin = 72.0   # pinion centre height in B = the lift servo's shaft
    a.parts.append(P("lift_pinion", "lift-gear: 19T lift pinion", "mech", "turntable",
                     TB @ trans(-59.7, 13.0, z_pin), f("base", "lift-gear"), placement="fitted",
                     evidence="pinion centre = rack pitch line (x -32.5) - pitch radius 27.2 -> x -59.7, the "
                              "centre of the base-center servo column (-60.5)"))
    # slide frame S -> B at rest (rack centred on the pinion): x->x (+19.5), y->z, z->-y
    TS = TB @ basis((1, 0, 0), (0, 0, 1), (0, -1, 0), origin_to=(19.5, 28.5, z_pin + 33.5))
    a.parts += [
        P("lift_slide", "slide-platform", "mech", "slide", TS, f("base", "slide-platform"), placement="fitted",
          evidence="20x20 hole square = MGN12H carriage; wall holes at 28.5 mm pitch = rack holes"),
        P("lift_rack", "head-lift-straight-gear: rack (9.0 mm pitch)", "mech", "slide",
          TS @ basis((-1, 0, 0), (0, 0, 1), (0, 1, 0), origin_to=(-43, -33.5, 21.5)),
          f("base", "head-lift-straight-gear"), placement="fitted",
          evidence="rack M5 holes (z -28.5, 0, 28.5) = slide wall holes (y -62, -33.5, -5; z 21.5)"),
        P("lift_carriage", "MGN12H carriage", "hardware", "slide",
          TS @ basis((-1, 0, 0), (0, 0, 1), (0, 1, 0), origin_to=(-19.5, -31, 0)),
          f("base", "mgn12h-dnp"), mass_g=catalog.PURCHASED["mgn12h"]["mass_g"], placement="fitted",
          evidence="carriage face on the rail at MGN12H height 13 mm; its 20x20 holes = slide holes"),
        P("neck_clamp_lower", "pipeclamp", "mech", "slide",
          TB @ basis((0, 1, 0), (-1, 0, 0), (0, 0, 1), origin_to=(0, 0, z_pin + 33.5 + 10 - 12)),
          f("base", "pipeclamp"), placement="fitted",
          evidence="bore R13 = tube; its reach (23 mm) meets the slide plate face at 23.5"),
    ]
    # the neck tube: from the slide bottom to the neck-main socket (see head(): socket top y)
    y_tube_bot = Y_BASE_STAGE + (z_pin + 33.5 - 77.0)          # slide bottom (S y = -77)
    # default head = Hunter's gimbal: coupler socket (R16 bore, y -67..-37 in its head frame) at body
    # y 738.3 - 37; the R-3X yoke variant needs the tube 15.7 mm shorter (socket top at 685.6)
    # the tube stops under the coupler's hub plate: Hunter's coupler top is 32 below the gimbal
    # centre, its plate `coupler_top_t` thick (hunter_head PARAMS): the droid suite checks this seat
    from assemblies.hunter_head.assembly import PARAMS as HUNTER_PARAMS

    y_tube_top = HUNTER_ORIGIN_Y - (32.0 + HUNTER_PARAMS["coupler_top_t"])
    L = y_tube_top - y_tube_bot
    a.parts.append(P("neck_tube", f"Neck tube 26 mm OD, {L:.0f} mm (CAD placeholder neck-dnp is 500 mm)",
                     "hardware", "slide", trans(0, y_tube_bot, 0) @ ZUP, None, generator=tube_generator(L),
                     kind="generated", origin="ours", material="aluminium", printed=False,
                     mass_g=L * catalog.PURCHASED["neck_tube"]["g_per_mm"], placement="fitted",
                     evidence="length = neck-main socket (head) - slide bottom (base stage)",
                     inferred=True, inferred_note="tube material/wall not in the parts list; stage height assumed",
                     features={"axis": {"type": "axis", "p": [0.0, 0.0, 0.0], "d": [0.0, 0.0, 1.0], "r": 13.0},
                               "top": {"type": "plane", "p": [0.0, 0.0, float(L)], "n": [0.0, 0.0, 1.0]}}))

    # the cosmetic neck spring (the real droid and the Hasbro figure have one; the kit does not
    # model it): the sim's Visual look draws it procedurally, so this is its twin, stretched by
    # the head lift like the sim's (manifest part `stretch`)
    a.parts.append(P("neck_spring", "Neck coil spring (cosmetic, the sim's)", "shell", "turntable",
                     trans(0, SPRING_Y, 0), None, generator=coil_generator(h=SPRING_H), kind="generated",
                     origin="ours", material="steel (painted, cosmetic)", printed=False, mass_g=40.0,
                     placement="fitted", evidence="sim/web/src/rig.ts buildNeckSpring: r 21, wire 3.2, 5.5 turns, "
                     "55 mm on the top cap at y 608.5",
                     stretch={"joint": "head_lift", "axis": [0, 1, 0], "anchor": [0, SPRING_Y, 0],
                              "rest_mm": SPRING_H}))

    # upper neck guide (neck support), mirroring the base lift design: 6 in lazy susan; its outer race
    # (neck-support-ring-outer) on the top ring floor, inner race (turns with the neck) carries a
    # vertical MGN12 rail; a carriage + platform + clamp ride on the tube. Heights inferred (PNG 01:
    # the plate sits at the hero-shoulder level).
    TU = ZUP
    y_or = 518.0            # on TR_RR (top-ring floor, y 493-518)
    y_ir = y_or + 12 + 8    # inner race above an ~8 mm 6 in lazy susan
    zc = 600.0              # carriage centre at rest (through TR_N_2's r50.8 opening)
    inf = dict(placement="inferred", inferred=True, inferred_note="upper guide heights/orientation inferred "
               "from assembly PNGs 01/03; the geometry mirrors the base lift (same clamp, platform, carriage)")
    a.parts += [
        P("neck_guide_ring_outer", "neck-support-ring-outer (6 in lazy susan outer race)", "mech", "neck_stage",
          TU @ trans(0, 0, y_or), f("neck", "neck-support-ring-outer"),
          evidence="R124.5 = the kit spacer rings (R124.6); on the top-ring floor", **inf),
        P("neck_guide_ring_inner", "neck-support-ring-inner (turns with the neck)", "mech", "turntable",
          TU @ trans(0, 0, y_ir), f("neck", "neck-support-ring-inner"), evidence="r57 inner race", **inf),
        P("neck_guide_rail", "mgn12rail-dnp (140 mm, on the inner race)", "hardware", "turntable",
          TU @ trans(0, 51.5, y_ir), f("neck", "mgn12rail-dnp"),
          mass_g=140 * catalog.PURCHASED["mgn12_rail_per_mm"]["g_per_mm"], evidence="rail face at r33.5 as in the base", **inf),
        P("neck_guide_carriage", "MGN12H carriage (rides the upper rail)", "hardware", "slide",
          TU @ basis((-1, 0, 0), (0, -1, 0), (0, 0, 1), origin_to=(0, 28.5, zc)),
          f("neck", "mgn12h-dnp"), mass_g=catalog.PURCHASED["mgn12h"]["mass_g"], evidence="as the base carriage", **inf),
        P("neck_guide_platform", "neck-support-platform (carriage plate)", "mech", "slide",
          TU @ basis((1, 0, 0), (0, 0, 1), (0, -1, 0), origin_to=(19.5, 28.5, zc - 25)),
          f("neck", "neck-support-platform"), evidence="20x20 carriage holes, the slide-platform without the rack wall", **inf),
        P("neck_clamp_upper", "neck-rod-clamp", "mech", "slide",
          TU @ basis((0, 1, 0), (-1, 0, 0), (0, 0, 1), origin_to=(0, 0, zc + 10)), f("neck", "neck-rod-clamp"),
          evidence="identical to the base pipeclamp; reaches the platform at 23.5", **inf),
    ]
    zpan = min(sector_travel("pan"), 135.0 / (GEAR["pan_sector"] / GEAR["pan_pinion"]))  # sector vs servo
    if TURNTABLE_YAW:
        Ry = roty(TURNTABLE_YAW)
        for p in a.parts:
            if p.link in ("turntable", "slide") or p.id in ("pan_sector", "stage_ring"):
                if p.id not in ("neck_tube",) and not p.id.startswith(("neck_guide", "neck_clamp_upper")):
                    p.T = Ry @ p.T
    a.joints = [
        Joint("head_pan", "Head pan (whole neck column turns on the base turntable)", "revolute",
              "neck_stage", "turntable", pivot=(0, 0, 0), axis=(0, 1, 0),
              limits=(-round(zpan, 1), round(zpan, 1)), profile_joint="head_pan",
              drive={"kind": "gear", "servos": ["pan_servo"], "gear_ratio": GEAR["pan_pinion"] / GEAR["pan_sector"],
                     "servo_deg_per_joint_deg": GEAR["pan_sector"] / GEAR["pan_pinion"],
                     "note": "15T pinion on the turntable walks round a fixed 60T internal sector (97.7 deg of teeth)"},
              zero={"how": "turntable centred in the sector with the head facing the base front; record the pulse"},
              evidence=["axis = centre of the 60T sector arc and of the base-center disc (both studio origins)",
                        "teeth 15 / 60 counted on sections (24.0 / 6.00 deg pitch)",
                        "travel: servo 270 deg / 4 = +-33.75 deg; sector allows +-42.9 deg"],
              confidence="high"),
        Joint("head_lift", "Head lift (rack and pinion on a vertical MGN12 rail)", "prismatic",
              "turntable", "slide", pivot=(0, 0, 0), axis=(0, 1, 0), unit="mm",
              limits=(-37.0, 37.0), profile_joint="head_lift",
              drive={"kind": "gear", "servos": ["lift_servo"], "mm_per_servo_deg": round(LIFT_MM_PER_DEG, 4),
                     "pinion_pitch_radius_mm": round(LIFT_PITCH_R, 2),
                     "note": "19T pinion, rack pitch 9.0 mm (module ~2.86); rack 87 mm long"},
              zero={"how": "rack centred on the pinion (inferred rest)"},
              evidence=["axis = MGN12 rail / 2020 extrusion direction (studio +Z)",
                        "rack 87 mm: +-(43.5 - 4.5 - 2) = +-37 mm with a full tooth engaged",
                        "servo 270 deg x 0.475 mm/deg = 128 mm: the rack, not the servo, limits travel"],
              confidence="high"),
    ]
    a.notes += [
        f"Neck tube must be ~{L:.0f} mm from the lift slide to Hunter's coupler ({L - 15.7:.0f} mm for the R-3X yoke); "
        "the CAD's neck-dnp is a 500 mm placeholder. Stage height: Morton's Lower Frame (y 35.1-73.2) sits in the "
        "R-3X stage-ring slot; the kit base has no floor, Morton adds a skid plate and 2020 posts.",
        "Hunter's neck coupler bore is 32 mm; the R-3X tube and clamps are 26 mm: sleeve the tube top or change "
        "the tube (and the two R13 clamps) to 32 mm.",
    ]
    base_asm.children.append(a)
    swapped: list[str] = []
    swap_parametric(a.parts, swapped)
    if swapped:
        a.notes.append("Parametric (mech/parts/anderson) in place of the vendored STLs: " + "; ".join(swapped))
    return a, dict(tube_top=y_tube_top, tube_len=L)


def head_mech(head: Asm):
    """Head tilt (servo + gear at the neck yoke) and visor (servo + push rod on the ear axle)."""
    # neck-main + centre gear (N frame): tilt bore X at z=25; tube socket z -15..15.6
    y_tilt = Y_HEAD_FLOOR_UNDER - 26.0         # baseplate top (z=7) under the head floor; bore at z=-19
    TN = trans(0, y_tilt - 25, 0) @ ZUP
    TNB = trans(0, y_tilt + 19, 0) @ ZUP       # neck-baseplate: bore at (y=0, z=-19)
    head.parts += [
        P("neck_yoke", "neck-main: yoke on the tube top", "mech", "head_mount", TN, f("tilt", "neck-main"),
          placement="fitted", evidence="socket R13.1 = tube; tilt bore R3.25 on X at z=25"),
        P("tilt_center_gear", "neck-center-gear (fixed to the yoke)", "mech", "head_mount", TN,
          f("tilt", "neck-center-gear"), placement="fitted",
          evidence="its bore and pin holes coincide with neck-main's (same studio frame)"),
        P("head_plate", "neck-baseplate: head mounting plate (under the head floor)", "mech", "head", TNB,
          f("tilt", "neck-baseplate"), placement="fitted",
          evidence="tilt bore R3.25/R18 boss on X at z=-19 = yoke bore; top z=7 at the head floor underside (y 721)"),
    ]
    # tilt servo: shaft || X, 36.3 mm behind the tilt axis (pitch radii 17.9 + 18.5)
    T_ts = TNB @ basis((0, 1, 0), (0, 0, 1), (1, 0, 0), origin_to=(-12 - 16.8, 36.3 + 9.8, -19))
    head.parts += [
        servo_part("tilt_servo", "SERVO_35KG_270", "head", T_ts, f("tilt", "servo-standard-dnp"),
                   evidence="shaft at the gear centre distance 36.3 mm on the plate's servo side"),
        P("tilt_pinion", "neck-servo-gear: 19T tilt pinion", "mech", "head",
          TNB @ basis((0, 1, 0), (0, 0, 1), (1, 0, 0), origin_to=(-4.5, 36.3, -19)), f("tilt", "neck-servo-gear"),
          placement="inferred", evidence="meshes the centre gear in its plane x -4.5..4.5", inferred=True,
          inferred_note="angular position of the servo round the tilt axis not stated (placed behind)"),
    ]
    # visor: V frame = visor-base studio; bearing axis X at (y=-26, z=33); head axis at (0, -26)
    # the visor-base stands on the head floor (y 735) with its bearing axis 33 mm up (768.0); the kit's
    # ear cups (the visible visor hinge) are on y 770.6, so the frame is shimmed 2.6 mm to put the axle there
    Y_EAR = 770.6
    TV = trans(0, Y_EAR - 33, -26) @ ZUP
    y_vis = Y_EAR
    ev_v = "axle on the kit ear-cup axis (y 770.6); frame on the head floor would give 768.0: 2.6 mm shim"
    head.parts += [
        P("visor_frame", "visor-base: visor frame on the head floor", "mech", "head", TV, f("visor", "visor-base"),
          placement="fitted", evidence="stands on the head floor top (y 735); " + ev_v),
        P("visor_post", "visor-mount-main (sleeve)", "mech", "head", TV @ trans(0, -26, 51), f("visor", "visor-mount-main"),
          placement="fitted", evidence="R20 4-hole circle = visor-base holes round (0, -26); bore R40 = kit H_Main_2 R40"),
        P("visor_post_cap", "visor-mount-cap", "mech", "head", TV @ trans(0, -26, 51), f("visor", "visor-mount-cap"),
          placement="fitted", evidence="same frame as the sleeve (rims overlap z 44-50)"),
    ]
    for s, x in (("l", 1), ("r", -1)):
        head.parts.append(P(f"visor_bearing_{s}", "F6001ZZ flanged bearing 12x28x8", "bearing", "head",
                            basis((0, 1, 0) if x > 0 else (0, -1, 0), (0, 0, 1), (x, 0, 0), origin_to=(x * 42, y_vis, 0)),
                            f("visor", "flanged-bearing-dnp"), mass_g=catalog.PURCHASED["f6001zz"]["mass_g"],
                            placement="fitted", evidence="R14/R15.5 bore in the visor-base side walls (x +-42..50)"))
    Trod = trans(-83, y_vis, 0)   # rod x 3..163 -> centred; cross hole at x 87.25 -> +4.25
    head.parts += [
        P("visor_rod", "visor-center-rod (12 mm axle)", "mech", "visor", Trod, f("visor", "visor-center-rod"),
          placement="fitted", evidence="R5.85 = bearing bore R6; centred between the side walls"),
        P("visor_tab", "visor-rod-tab (lever, 18 mm)", "mech", "visor",
          basis((1, 0, 0), (0, 0, 1), (0, -1, 0), origin_to=(4.25, y_vis, 0)), f("visor", "visor-rod-tab"),
          placement="inferred", evidence="pinned through the rod's cross hole (x 87.25)", inferred=True,
          inferred_note="lever points down at rest (not stated)"),
    ]
    for s, x in (("l", 1), ("r", -1)):
        head.parts.append(P(f"visor_flipper_{s}", "visor-flipper-adapter (visor-arm hub)", "mech", "visor",
                            basis((x, 0, 0), (0, 1, 0), (0, 0, x), origin_to=(x * 80, y_vis, 0)),
                            f("visor", "visor-flipper-adapter"), placement="fitted",
                            evidence="R5.85 socket on the rod ends (x +-80); 4 holes R11.5 = visor-demo-arm hub"))
    # linkage: horn 25 mm, push rod 58 mm, lever 18 mm (parallel cranks at rest)
    shaft = np.array([4.25, y_vis - 18 + 25, -58.0])
    T_vs = basis((0, 0, -1), (0, 1, 0), (1, 0, 0), origin_to=shaft + np.array([-16.8 - 5, 0, 0])) @ trans(9.8, 0, 0)
    head.parts += [
        servo_part("visor_servo", "SERVO_35KG_270", "head", T_vs, f("visor", "servo-standard-dnp"),
                   evidence="horn 25 mm + rod 58 mm + lever 18 mm closed with parallel cranks at rest"),
        P("visor_horn", "visor-servo-horn (25 mm)", "mech", "head",
          basis((0, 1, 0), (0, 0, 1), (1, 0, 0), origin_to=shaft + np.array([-5, 0, 0])),
          f("visor", "visor-servo-horn"), placement="inferred", inferred=True,
          inferred_note="servo position chosen to close the linkage"),
        P("visor_push_rod", "visor-push-rod (58 mm)", "mech", "visor",
          basis((0, 0, 1), (1, 0, 0), (0, 1, 0), origin_to=(4.25, y_vis - 18, 0)), f("visor", "visor-push-rod"),
          placement="inferred", inferred=True, inferred_note="rides between horn and lever (shown on the visor link)"),
    ]
    head.joints += [
        Joint("head_tilt", "Head tilt (nod), servo gear on a fixed centre gear", "revolute", "head_mount", "head",
              pivot=(0.0, y_tilt, 0.0), axis=(1.0, 0.0, 0.0), limits=(-20, 25), profile_joint="head_tilt",
              drive={"kind": "gear", "servos": ["tilt_servo"],
                     "gear_ratio": round(GEAR["tilt_pinion"] / GEAR["tilt_center"], 4),
                     "servo_deg_per_joint_deg": round(GEAR["tilt_center"] / GEAR["tilt_pinion"], 4),
                     "note": "servo on the head plate, 19T pinion rolls on the fixed centre gear (18.33 deg pitch)"},
              zero={"how": "head level; record the pulse"},
              evidence=["axis = R3.25 bore shared by neck-main (z=25) and neck-baseplate (z=-19), parallel to the "
                        "visor axle (assembly PNG 04)", f"pivot y={y_tilt:.0f}: baseplate top under the head floor",
                        "range: no hard stop in the CAD; the centre gear's teeth span 270 deg"],
              confidence="medium"),
        Joint("visor", "Visor (brow + side arms) on the ear axle", "revolute", "head", "visor",
              pivot=(0.0, y_vis, 0.0), axis=(1.0, 0.0, 0.0), limits=(-15, 30), profile_joint="visor",
              drive={"kind": "push_rod", "servos": ["visor_servo"], "gear_ratio": round(25 / 18, 3),
                     "note": "horn 25 mm / lever 18 mm = 1.39 visor deg per servo deg near the parallel-crank rest"},
              zero={"how": "visor at the bottom of its flap"},
              evidence=["axis = F6001ZZ bearing bore in visor-base (X at y=-26, z=33)", ev_v,
                        "horn hole 25 mm (visor-servo-horn), lever 18 mm (visor-rod-tab), rod 58 mm between pins"],
              confidence="medium"),
    ]
    head.notes += [
        "Visor axle + adapters span x +-93 mm; the head shell's inner radius at the ear line is ~97 mm and the kit "
        "visor arms' roots are at x ~+-110..127: the shell must be pierced at the ear line and the arms joined to "
        "the adapters inside the ear cups (the build's magenta 'visor-demo-arm' is a stand-in).",
        "The head floor (H_Main_1) is clamped between the visor frame (above) and the neck-baseplate (below); its "
        "centre opening (r 38-45) must pass the yoke and clear it over the tilt range.",
    ]


def lower_drive(lower: Asm):
    TL = trans(0, Y_PEDESTAL_TOP + 3, 0) @ ZUP      # mount underside (z=-3) on the pedestal top
    lower.parts += [
        P("lower_servo_mount", "lower-ring-servo-mount (on the pedestal top)", "mech", "lower_ring_mount", TL,
          f("lower", "lower-ring-servo-mount"), placement="inferred",
          evidence="mount spans r 69..111 over P_M_3's top ring (r 88..110); servo hangs into the pedestal",
          inferred=True, inferred_note="which static part it bolts to is not stated"),
        servo_part("lower_servo", "SERVO_35KG_270", "lower_ring_mount", TL @ trans(0, 83, 4),
                   f("lower", "servo-placeholder-dnp"), placement="fitted",
                   evidence="mount window centred (0, 83); shaft x=-9.8 -> R83.6 = (95-25)/2 x module 2.39"),
        P("lower_pinion", "lower-ring-servo-gear: 25T", "mech", "lower_ring_mount", TL @ trans(-9.8, 83, 4 + 16.8 + 7.5),
          f("lower", "lower-ring-servo-gear"), placement="fitted", evidence="on the servo shaft"),
        P("lower_sector", "lower-ring-inner-gear: 95T-pitch internal sector (79.6 deg)", "mech", "lower_ring",
          TL @ frames.rot((0, 0, 1), 6.7), f("lower", "lower-ring-inner-gear"), placement="fitted",
          evidence="outer arc R124.5 against the ring's inner wall (LS_M r125.4); centred on the pinion at rest"),
    ]
    j = next(j for j in lower.joints if j.id == "torso_lower")
    t = min(sector_travel("lower"), 135 / (GEAR["lower_sector"] / GEAR["lower_pinion"]))
    j.limits = (-round(t, 1), round(t, 1))
    j.drive = {"kind": "gear", "servos": ["lower_servo"], "gear_ratio": GEAR["lower_pinion"] / GEAR["lower_sector"],
               "servo_deg_per_joint_deg": GEAR["lower_sector"] / GEAR["lower_pinion"],
               "note": "25T pinion (static) into a 95T-pitch internal sector on the ring"}
    j.evidence += ["teeth 25 / 95 (14.4 / 3.790 deg pitch)",
                   f"travel: sector +-{sector_travel('lower'):.1f}, servo 270/3.8 = +-35.5"]
    j.zero = {"how": "vent at the front, poker arm out to the left; pinion mid-sector"}


def top_drive(middle: Asm, top: Asm):
    Tz = 462.0   # sector z 0..14 hangs under the top ring, below the neck guide ring (inferred)
    TT = trans(0, Tz, 0) @ ZUP
    middle.parts += [
        P("top_servo_mount", "top-ring-servo-mount-main", "mech", "middle_ring", TT @ trans(0, 0, -10),
          f("top", "top-ring-servo-mount-main"), placement="inferred", inferred=True,
          inferred_note="needs a bracket to the middle ring wall (not in the CAD); servo kept on the static side "
                        "like the lower ring so its cable does not wind through a lazy susan"),
        P("top_servo_spacer", "top-ring-servo-mount-spacer", "mech", "middle_ring", TT @ trans(0, 0, -10),
          f("top", "top-ring-servo-mount-spacer"), placement="fitted", evidence="same studio frame as the mount"),
        servo_part("top_servo", "SERVO_35KG_270", "middle_ring", TT @ trans(0, 83, -16),
                   f("top", "servo-placeholder-dnp"), evidence="same window as the lower mount (0, 83)"),
        P("top_pinion", "top-ring-servo-gear: 20T", "mech", "middle_ring", TT @ trans(-9.8, 83, 0),
          f("top", "top-ring-servo-gear"), placement="fitted",
          evidence="R83.6 = (85-20)/2 x module 2.57"),
    ]
    top.parts.append(P("top_sector", "top-ring-inner-gear: 85T-pitch internal sector (59.4 deg)", "mech", "top_ring",
                       TT @ frames.rot((0, 0, 1), 51.7), f("top", "top-ring-inner-gear"),
                       placement="inferred", evidence="centred on the pinion at rest", inferred=True,
                       inferred_note="height (hung under the top ring) and fixing not stated"))
    j = next(j for j in top.joints if j.id == "torso_top")
    t = min(sector_travel("top"), 135 / (GEAR["top_sector"] / GEAR["top_pinion"]))
    j.limits = (-round(t, 1), round(t, 1))
    j.drive = {"kind": "gear", "servos": ["top_servo"], "gear_ratio": GEAR["top_pinion"] / GEAR["top_sector"],
               "servo_deg_per_joint_deg": GEAR["top_sector"] / GEAR["top_pinion"],
               "note": "20T pinion into an 85T-pitch internal sector"}
    j.evidence += ["teeth 20 / 85 (18.0 / 4.238 deg pitch)",
                   f"travel: sector +-{sector_travel('top'):.1f} (limits), servo 270/4.25 = +-31.8"]
    j.zero = {"how": "RX-24 plate at the front, hero arm up at the front-left; pinion mid-sector"}


def hero_arm(top: Asm):
    from assemblies.kit.assembly import kit_T
    Tk = kit_T("top_ring")
    pivot = frames.apply(Tk, (40.8, 528.4, 227.45))                      # HA_LE_1 / HA_RE_1 disc centres
    a_hat = frames.apply_dir(Tk, (85.2, 0.0, -13.1))                      # line through both disc centres
    d_kit = np.array([0.105, 0.664, 0.74])
    d_hat = frames.apply_dir(Tk, d_kit)                                   # HA_SP_2 long axis
    d_hat = d_hat - a_hat * (d_hat @ a_hat); d_hat /= np.linalg.norm(d_hat)
    x_hat = np.cross(a_hat, d_hat)
    p0 = frames.apply(Tk, (60.5, 635.9, 336.8))
    foot = p0 + d_hat * ((pivot - p0) @ d_hat)
    radial = np.array([pivot[0], 0, pivot[2]]); radial /= np.linalg.norm(radial)
    inward = -(radial - a_hat * (radial @ a_hat)); inward /= np.linalg.norm(inward)
    down = np.cross(inward, a_hat)
    # dual-shaft servo (servo.stl: 40x50x20, shafts on Y at x=-10, z=10) centred on the hinge
    T_hs = basis(inward, a_hat, down) @ trans(10, 0, -10)
    T_hs = trans(pivot) @ T_hs
    Tm = basis(x_hat, a_hat, d_hat, origin_to=foot, origin_from=(-26, -30, 0))
    on_axis = lambda s: basis(x_hat, a_hat, d_hat, origin_to=foot + d_hat * s)  # noqa: E731
    top.parts += [
        servo_part("hero_shoulder_servo", "DS3218_DUAL", "top_ring", T_hs, f("hero", "servo"),
                   evidence="output shafts (Y) on the kit hinge-disc axis HA_LE_1-HA_RE_1"),
        P("hero_forearm", "mainarm (61 mm forearm, replaces HA_SB_1/HA_SP_1/HA_SP_2)", "mech", "hero_arm", Tm,
          f("hero", "mainarm"), placement="fitted", replaces=["ha_sb_1", "ha_sp_1", "ha_sp_2"],
          evidence="bore axis (-26, -30) on the kit forearm axis (HA_SP_2 PCA); horn holes along Y = hinge axis"),
        P("hero_wrist_ring", "Part 1 (wrist ring)", "mech", "hero_arm", on_axis(180), f("wrist", "Part 1"),
          placement="inferred", inferred=True, inferred_note="stacked on the forearm end"),
        P("hero_wrist_body", "wrist (houses the 7 kg servo)", "mech", "hero_arm", on_axis(186), f("wrist", "wrist"),
          placement="inferred", replaces=["ha_w_2"], inferred=True, inferred_note="stacked on the forearm end"),
        P("hero_wrist_servo_holder", "Part 1 (1) micro-servo holder", "mech", "hero_arm", on_axis(190),
          f("wrist", "Part 1 (1)"), placement="inferred", inferred=True, inferred_note="inside the wrist"),
        P("hero_wrist_servo", "Servo " + catalog.SERVOS["SERVO_7KG"]["listing"], "servo", "hero_arm", on_axis(195),
          None, generator=lambda: trimesh.creation.box((23, 12, 25)), kind="generated", origin="catalog",
          material="servo:SERVO_7KG", mass_g=catalog.SERVOS["SERVO_7KG"]["mass_g"], printed=False,
          placement="inferred", inferred=True, inferred_note="micro servo stand-in box, not in the CAD"),
        P("hero_hand_arm_side", "hand-arm-side (wrist bearing half, fixed)", "mech", "hero_arm", on_axis(229),
          f("wrist", "hand-arm-side"), placement="inferred", inferred=True, inferred_note="stacked"),
        P("hero_hand_finger_side", "hand-finger-side (turns; carries the fingers)", "mech", "hero_hand", on_axis(229),
          f("wrist", "hand-finger-side"), placement="fitted", replaces=["ha_w_1"],
          evidence="shares the hand-arm-side frame (z -5..7.8 | 7.8..44); finger pin holes on X"),
        P("hero_elbow_tube", "bodytube (32 mm, along the hinge axis)", "mech", "top_ring",
          trans(pivot) @ basis(x_hat, -a_hat, -d_hat) @ trans(0, -25, 0), f("hero", "bodytube"),
          placement="inferred", inferred=True, inferred_note="axle housing toward the body (assembly PNG 03)"),
    ]
    js = Joint("hero_shoulder", "Hero arm shoulder (20 kg dual-shaft servo in the elbow disc)", "revolute",
               "top_ring", "hero_arm", pivot=tuple(np.round(pivot, 2)), axis=tuple(np.round(a_hat, 5)),
               limits=(-35, 45), profile_joint="hero_shoulder",
               drive={"kind": "direct", "servos": ["hero_shoulder_servo"], "gear_ratio": 1.0},
               zero={"how": "arm up at the front-left (kit pose)"},
               evidence=["axis through the kit hinge-disc centres HA_LE_1 (83.4,528.4,220.9) / HA_RE_1 (-1.8,528.4,234.0), "
                         "disc normals agree (PCA)", "servo.stl double output shaft on that axis (elbow-covers-dnp = the kit discs)"],
               confidence="high")
    jw = Joint("hero_wrist", "Hero wrist roll (7 kg servo in the new wrist)", "revolute", "hero_arm", "hero_hand",
               pivot=tuple(np.round(foot + d_hat * 229, 2)), axis=tuple(np.round(d_hat, 5)), limits=(-90, 90),
               profile_joint="hero_wrist", drive={"kind": "direct", "servos": ["hero_wrist_servo"], "gear_ratio": 1.0},
               zero={"how": "claw as in the kit pose"},
               evidence=["axis = R30 wrist discs (hand-arm-side / hand-finger-side) on the forearm axis (HA_SP_2 PCA)"],
               confidence="high")
    top.joints += [js, jw]


def attach(root: Asm, base: Asm, lower: Asm, middle: Asm, top: Asm, head: Asm):
    nd, info = neck_drive(base)
    # the guide's outer race is bolted to the top ring floor: it rides the top ring
    ring = next(p for p in nd.parts if p.id == "neck_guide_ring_outer")
    nd.parts.remove(ring)
    ring.link = "top_ring"
    top.parts.append(ring)
    # head: default = Hunter's gimbal (separate module, ChildRef); alternate = the R-3X Animation
    # tilt + visor with the kit shells (inline, built here)
    head.mount_link = "slide"
    head.links.insert(0, Link("head_mount", "Neck top (yoke on the tube)", None))
    head.id, head.name = "head_r3x", "Head: kit shells + R-3X Animation tilt/visor - reference (not engineered)"
    head.variant = {"group": "head_mech", "id": "r3x_anderson", "default": False, "reference": True}
    nd.children.append({
        "ref": "../hunter_head/manifest.json", "id": "hunter_head", "name": "Head: Hunter's gimbal mech (default)",
        "mount": {"parent_link": "slide", "transform": {"t": [0, HUNTER_ORIGIN_Y, 0], "q": [0, 0, 0, 1]},
                  "variant": {"group": "head_mech", "id": "hunter", "default": True}, "inferred": False,
                  "note": "the coupler clamps the neck tube top; the tube turns (head_pan) and slides (head_lift)"},
        # the head <-> neck interface, as mates the droid suite checks (workbench/droid.py): the
        # 26 mm coupler bore on the tube, the tube's top on the bore stop (the height stop), and an
        # M4 set screw in the coupler's side insert clamping the tube
        "interface": {
            "mates": [
                {"type": "concentric", "a": ["hunter_head/neck_coupler", "bore"], "b": ["neck_tube", "axis"],
                 "note": "coupler bore (26 mm) on Anderson's neck tube"},
                {"type": "seated", "a": ["hunter_head/neck_coupler", "bore_stop"], "b": ["neck_tube", "top"],
                 "note": "height stop: the tube's top against the coupler's hub plate"},
            ],
            "fasteners": [
                {"id": "set_coupler_tube", "spec": {"type": "set_screw", "thread": "M4", "length_mm": 6},
                 "in": ["hunter_head/ins_coupler_side", "thread"], "onto": "neck_tube", "link": "hunter_head:neck",
                 "note": "cup-point set screw through the coupler's side insert onto the tube"},
            ],
        },
    })
    nd.children.append(head)
    # move the kit head/visor parts: they are already on links 'head' / 'visor'
    head_mech(head)
    lower_drive(lower)
    top_drive(middle, top)
    for ring in (lower, middle, top):
        done: list[str] = []
        swap_parametric(ring.parts, done)
        if done:
            ring.notes.append("Parametric (mech/parts/anderson) in place of the vendored STLs: " + "; ".join(done))
    for a_, pool in ((nd, nd.parts), (lower, lower.parts), (middle, middle.parts + top.parts)):
        ph: list[str] = []
        phase_gears(pool, ph)  # the top pinion rides the middle ring, its sector the top ring
        if ph:
            a_.notes.append("Gear phase: " + "; ".join(ph))
    hero_arm(top)
    # parts the build supersedes
    sup = {r for p in root.all_parts() for r in p.replaces}
    for p in root.all_parts():
        if p.id in sup:
            p.note = (p.note + "; " if p.note else "") + "superseded by the R-3X build (excluded from mass and checks)"
    root.notes.append("Profile joints with no mechanism in the R-3X build: torso_middle (not motorised), poker_* / "
                      "throttle_* arms (poseable kit joints only), all claws (rigid).")
