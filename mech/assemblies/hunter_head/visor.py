"""The kit's visor on Hunter's head, driven by Brian Anderson's visor mechanism.

Brandon's call (2026-09-30): Hunter's own visor (DXF arm, servo, Visor Servo Mount) is out; the
head carries the kit's visor (H_V_1..5) and ears (H_LE/H_RE), exactly where the sim's model has
them, driven by Anderson's r3x - visor animation parts: a 25 mm servo horn, a 58 mm push rod and
an 18 mm rod tab on the visor axle (the tab's pin is 19.5 mm from the axis: its socket straddles
the axle 1.5 mm off its own origin).

Adaptation to Hunter's gimbal: Anderson's single 12 mm axle crosses the head's centre, where
Hunter's fixed hex post stands (and the head swings around it), so the axle is split in two stub
axles, each in an F6001ZZ bearing in our bracket (parts/head/visor_bracket.py) standing on the
mount plate's flange and clamped by the plate's own M4 screws. The left one carries the tab; each
ends in our hub (parts/head/visor_hub.py), bolted to the kit arm's own four holes - Anderson's
flipper adapter is 13 mm long and the bracket stands where that length would go. The kit visor
(brow + both side arms) is rigid and carries the right side. Everything rides the
head, inside tilt and roll.

Where the servo sits is chosen by `design_drive`: every rod direction, lever rest angle and
servo orientation Anderson's parts allow, kept when the linkage reaches the whole visor range
with leverage, and the servo, horn and rod clear the plate, shell, gimbal and visor through
tilt x roll x visor. Reported as inferred for Brandon to confirm.
"""

from __future__ import annotations

import itertools
import math

import numpy as np
import trimesh

from workbench import geom
from workbench.geom import _cache_key, cached
from workbench.kinematics import link_matrices, servo_jacobian, solve_linkages
from workbench.mates import axis, plane, spline
from workbench.model import Fastener, Linkage, Part

ANDERSON = "r3x - visor animation"
KIT_VISOR = ("h_v_1", "h_v_2", "h_v_3", "h_v_4", "h_v_5")
KIT_EARS = ("h_le_1", "h_le_2", "h_re_1", "h_re_2")
# the kit head's cosmetic parts on Hunter's head (Brandon, 2026-09-30: the head must read as R3X):
# face plate, eyes, mouth + mic, headband. Only the kit's own shells (H_Main_1..3) stay out:
# Hunter's shells replace them.
KIT_FACE = ("h_fp", "h_leye_1", "h_leye_2", "h_leye_3", "h_leye_4", "h_reye_1", "h_reye_2", "h_reye_3", "h_reye_4",
            "h_m_1", "h_m_2", "h_mb", "h_hp_1", "h_hp_2")
KIT_NAMES = {"h_v_1": "Kit visor brow (H_V_1)", "h_v_2": "Kit visor side arm L (H_V_2)",
             "h_v_3": "Kit visor side arm R (H_V_3)", "h_v_4": "Kit visor axle end R (H_V_4)",
             "h_v_5": "Kit visor axle end L (H_V_5)", "h_fp": "Kit face plate (H_FP)",
             "h_m_1": "Kit mouth (H_M_1)", "h_m_2": "Kit mic (H_M_2)", "h_mb": "Kit mouth bracket (H_MB)",
             "h_hp_1": "Kit headband (H_HP_1)", "h_hp_2": "Kit headband inner (H_HP_2)"}
HORN_R, LEVER_R, ROD_L = 25.0, 19.5, 58.0       # Anderson: visor-servo-horn, visor-rod-tab, visor-push-rod
TAB_AXIS = 1.5                                   # the tab's socket centre: 1.5 mm behind its own origin
TAB_T, ROD_T, HORN_T = 7.8, 5.0, 5.0             # their thicknesses (along the axle)
TAB_X = (55.0, 62.8)                             # the tab on the left stub axle, 1.2 mm inboard of the bracket
BEARING_X = 68.0                                 # the bracket's bearing (upright |x| 64..72)
HUB_L = 9.0                                      # our hub, up to the kit arm's inboard face (1.3 mm off the bracket)
SERVO_BOSS = 2.8                                 # the spline top above the servo's output boss (horn seat;
                                                 # parts/models.py servo: case top 4.3 below, boss 1.5 on it)
CLEAR = 1.0


def anderson(name):
    from assemblies.hunter_head.assembly import MECH

    return MECH / "vendor" / "animation" / ANDERSON / f"visor - {name}.stl"


def _R(ex, ey, ez):
    m = np.eye(4)
    m[:3, 0], m[:3, 1], m[:3, 2] = ex, ey, ez
    return m


def _T(R, t):
    m = R.copy()
    m[:3, 3] = t
    return m


def _xf(mesh, m):
    out = mesh.copy()
    out.apply_transform(m)
    return out


X = np.array([1.0, 0, 0])


def pivot_of(h_v_4: trimesh.Trimesh):
    """The visor axis from the kit's own axle piece: the centre of H_V_4's cylinder."""
    lo, hi = h_v_4.bounds
    return np.array([0.0, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2])


# ------------------------------------------------------------------ fixed parts

def fixed_parts(kit_parts, V):
    """Kit visor + ears, our brackets and stub axles, the bearings, Anderson's adapters and tab."""
    from parts.head import visor_bracket, visor_stub_axle
    from parts.head._common import mesh as b_mesh
    from parts.library import spec_part

    out = []
    for pid, mesh, link, fname in kit_parts:
        name = KIT_NAMES.get(pid) or (f"Kit ear {pid.upper()}" if pid in KIT_EARS else
                                      f"Kit eye {pid.upper()}" if "eye" in pid else f"Kit {pid.upper()}")
        out.append(Part(pid, name, "mech" if pid in KIT_VISOR else "shell", link, mesh,
                        {"file": f"vendor/kit/.../{fname}", "kind": "stl", "placement": "kit (the sim's own)"},
                        "PLA (printed)", True, (0, 1, 0) if link == "visor" else (1, 0, 0) if pid.startswith("h_le") else
                        (-1, 0, 0) if pid.startswith("h_re") else (0, 0, 1),
                        40, None, "", cad="mesh"))
    for s, tag in ((1, "l"), (-1, "r")):
        br = visor_bracket.make(side=s, axis_y=float(V[1]), axis_z=float(V[2]))
        out.append(Part(f"visor_bracket_{tag}", f"Visor bearing bracket {tag.upper()} (ours)", "mech", "head",
                        _cached_mesh(br, f"vbracket{s}{V.round(3)}"),
                        {"kind": "parametric", "model": "parts/head/visor_bracket.py", "params": br.params,
                         "placement": "mates"}, "PLA (printed)", True, (s, 0, 0), 40, 18.0, "estimate",
                        cad="parametric", features=dict(br.features)))
        brg = spec_part("bearing", {"type": "flanged", "id_mm": 12, "od_mm": 28, "width_mm": 8,
                                    "flange_od_mm": 30.5, "flange_mm": 1.5}).mesh
        c = np.array([s * BEARING_X, V[1], V[2]])
        mb = _T(_R(np.cross([0, 1.0, 0], s * X), [0, 1.0, 0], s * X), c)
        out.append(Part(f"visor_bearing_{tag}", f"F6001ZZ flanged bearing 12x28x8 ({tag.upper()})", "bearing", "head",
                        _xf(brg, mb), {"kind": "parametric", "model": "parts/models.py:bearing", "placement": "mates"},
                        "steel", False, (s, 0, 0), 25, 20.0, "catalogue", cad="parametric",
                        features={"outer": axis(c + s * X * 4.0, -s * X, 14.0), "bore": axis(c + s * X * 4.0, -s * X, 6.0)}))
    out += axle_and_hub_parts(V, np.array([0.0, -1.0, 0.0]), kit_parts)
    return out


CHANNEL_MM = 1.5          # design default: the shells clear the kit visor's sweep by this much
CHANNEL_SHELLS = ("head_top", "side_left", "side_right")


def visor_channel(parts_by_id, V, limits, clear=CHANNEL_MM, step_deg=0.5, cell=0.6):
    """The clearance channel the shells need along the kit visor's path: the visor parts swept
    about the visor axis over `limits`, grown by `clear`, kept only where a shell comes within
    `clear` + 2 mm of the sweep (so it is the channel, not the whole sweep). Returns a list of
    watertight trimesh envelopes (one per region), in the head frame. Cutting them out of the
    shells leaves >= ~`clear` everywhere along the path (the 0.5 deg step scallops < 0.15 mm)."""
    import manifold3d as mf
    from scipy.spatial import cKDTree

    visor = [p for p in parts_by_id.values() if p.id in KIT_VISOR]
    shells = [parts_by_id[k] for k in CHANNEL_SHELLS if k in parts_by_id]
    near_r = clear + 2.0
    # the sweep, sampled
    base = np.vstack([trimesh.sample.sample_surface(p.mesh, int(p.mesh.area / 0.16), seed=1)[0] for p in visor])
    angles = np.arange(limits[0], limits[1] + 1e-9, step_deg)
    # only where a shell is near: shell samples against the swept cloud (coarse first)
    shell_pts = np.vstack([trimesh.sample.sample_surface(p.mesh, int(p.mesh.area / 1.0), seed=2)[0] for p in shells])
    coarse = np.vstack([_rot_pts(base[::20], V, a) for a in angles[::4]])
    d, _ = cKDTree(coarse).query(shell_pts, distance_upper_bound=near_r + 8)
    hot = shell_pts[d < near_r + 6]
    if not len(hot):
        return []
    # regions: cluster the hot shell points (single linkage, 10 mm)
    from scipy.cluster.hierarchy import fcluster, linkage

    lab = fcluster(linkage(hot[::max(1, len(hot) // 4000)], "single"), 10.0, "distance") if len(hot) > 1 else np.ones(1, int)
    sub = hot[::max(1, len(hot) // 4000)]
    out = []
    for lb in np.unique(lab):
        pts = sub[lab == lb]
        lo, hi = pts.min(0) - (near_r + 2), pts.max(0) + (near_r + 2)
        sw = np.vstack([_rot_pts(base, V, a) for a in angles])
        sw = sw[np.all((sw > lo - clear) & (sw < hi + clear), axis=1)]
        if not len(sw):
            continue
        tree = cKDTree(sw)

        def sdf(x, y, z, tree=tree):
            dd, _ = tree.query((x, y, z), distance_upper_bound=clear + 1.0)
            return clear - min(dd, clear + 1.0)

        man = mf.Manifold.level_set(sdf, [*lo, *hi], cell)
        m = man.to_mesh()
        env = trimesh.Trimesh(np.asarray(m.vert_properties)[:, :3], np.asarray(m.tri_verts), process=True)
        if len(env.faces):
            out.append(env)
    return out


def cut_meshes(mesh, cuts):
    """mesh minus every cut (manifold booleans); the mesh itself if the boolean fails."""
    import manifold3d as mf

    def to_mf(m):
        return mf.Manifold(mf.Mesh(vert_properties=np.asarray(m.vertices, np.float32),
                                   tri_verts=np.asarray(m.faces, np.uint32)))

    try:
        res = to_mf(mesh)
        for c in cuts:
            res = res - to_mf(c)
        m = res.to_mesh()
        return trimesh.Trimesh(np.asarray(m.vert_properties)[:, :3], np.asarray(m.tri_verts), process=True), True
    except Exception:
        return mesh, False


def _rot_pts(pts, V, deg):
    R = trimesh.transformations.rotation_matrix(math.radians(deg), [1.0, 0, 0], V)
    return pts @ R[:3, :3].T + R[:3, 3]


SHIM_MIN_GAP = 0.5  # the glue-contact tolerance: a larger gap under the kit mouth gets our shim


def mouth_shim_part(parts_by_id):
    """Our shim under the kit mouth (parts/head/mouth_shim.py), at the closest approach of H_M_1
    and Hunter's head bottom, as thick as the gap there."""
    from scipy.spatial import cKDTree

    from parts.head import mouth_shim
    from workbench.geom import parametric_mesh, sample
    from workbench.mates import moved

    A, B = parts_by_id["h_m_1"].mesh, parts_by_id["head_bottom"].mesh
    pa, pb = sample(A, 0.5, 40000), sample(B, 0.5, 40000)
    d, i = cKDTree(pb).query(pa)
    k = int(np.argmin(d))
    qa, qb = pa[k], pb[i[k]]
    gap = float(d[k])
    if gap <= SHIM_MIN_GAP:  # the mouth already sits on the head bottom (glue): no shim
        return None
    n = _unit(qa - qb)  # head bottom -> mouth
    t = round(max(gap, 0.3), 2)
    mesh, feats, prm = parametric_mesh("parts.head.mouth_shim", {"t": t})
    # pad frame: +Y along n, centred on the head bottom's point
    ref = np.array([1.0, 0, 0]) if abs(n[0]) < 0.9 else np.array([0, 0, 1.0])
    ex = _unit(np.cross(ref, n))
    ez = np.cross(ex, n)
    M = _T(_R(ex, n, ez), qb)
    return Part("mouth_shim", f"Mouth shim {t:g} mm (ours)", "mech", "head", _xf(mesh, M),
                {"kind": "parametric", "model": "parts/head/mouth_shim.py", "params": prm, "placement": "mates",
                 "fit": f"the gap between H_M_1 and the head bottom at their closest: {gap:.2f} mm"},
                "PLA (printed)", True, tuple(n), 20, 0.1, "estimate", cad="parametric",
                features={k2: moved(v, M) for k2, v in feats.items()})


def arm_holes(mesh, V, rh=1.95):
    """The kit arm's four holes round the visor axis ((y, z) centres, head frame), measured in the
    middle of the arm's thickness: the empty patch of about a Ø3.9 hole nearest each of four sites
    11.3 mm from the axis."""
    from scipy.cluster.hierarchy import fcluster, linkage

    lo, hi = mesh.bounds
    x = (lo[0] + hi[0]) / 2
    g = np.arange(-3.5, 3.51, 0.1)
    out = []
    for sy, sz in ((11.3, 0.0), (0.0, 11.3), (-11.3, 0.0), (0.0, -11.3)):
        site = np.array([V[1] + sy, V[2] + sz])
        Q = np.array([[x, site[0] + a, site[1] + b] for a in g for b in g])
        emp = Q[~mesh.contains(Q)][:, 1:]
        best = None
        if len(emp) > 1:
            lab = fcluster(linkage(emp, "single"), 0.15, "distance")
            for lb in np.unique(lab):
                pts = emp[lab == lb]
                if abs(len(pts) * 0.01 - math.pi * rh ** 2) < 0.35 * math.pi * rh ** 2:
                    c = pts.mean(0)
                    if best is None or np.linalg.norm(c - site) < np.linalg.norm(best - site):
                        best = c
        if best is not None:
            out.append((round(float(best[0]), 2), round(float(best[1]), 2)))
    return out


def axle_and_hub_parts(V, lev, kit_parts):
    """The stub axles (the left with the tab's 8 mm section, its D flat along the lever at rest)
    and our hubs on the kit arms' holes."""
    from parts.head import visor_hub, visor_stub_axle

    kit = {pid: m for pid, m, _, _ in kit_parts}
    flat = (float(lev[1]), float(lev[2]))
    out = []
    for s, tag, arm in ((1, "l", "h_v_2"), (-1, "r", "h_v_3")):
        lo, hi = kit[arm].bounds
        x1 = float(lo[0]) if s > 0 else float(-hi[0])  # the arm's inboard face, |x|
        holes = cached(_cache_key("vholes", arm, np.round(kit[arm].bounds, 3).tolist(), V.round(3).tolist()),
                       lambda m=kit[arm]: arm_holes(m, V))
        hub = visor_hub.make(side=s, axis_y=float(V[1]), axis_z=float(V[2]), x=(x1 - HUB_L, x1), flat_dir=flat,
                             holes=tuple(holes))
        out.append(Part(f"visor_hub_{tag}", f"Visor hub {tag.upper()} (ours)", "mech", "visor",
                        _cached_mesh(hub, f"vhub{s}{V.round(3)}{x1:.3f}{flat}{holes}"),
                        {"kind": "parametric", "model": "parts/head/visor_hub.py", "params": hub.params,
                         "placement": "mates"}, "PETG (printed)", True, (s, 0, 0), 30, 6.0, "estimate",
                        cad="parametric", features=dict(hub.features)))
        xs = (TAB_X[0] + 1.2, x1 - 0.3) if s > 0 else (63.0, x1 - 0.3)  # 1.2 mm short of the rod's face
        ax = visor_stub_axle.make(side=s, axis_y=float(V[1]), axis_z=float(V[2]), x=xs, flat_dir=flat,
                                  tab=TAB_X if s > 0 else None)
        out.append(Part(f"visor_axle_{tag}", f"Visor stub axle {tag.upper()} (12 mm D, ours)", "hardware", "visor",
                        _cached_mesh(ax, f"vaxle{s}{V.round(3)}{xs}{flat}"),
                        {"kind": "parametric", "model": "parts/head/visor_stub_axle.py", "params": ax.params,
                         "placement": "mates"}, "steel", False, (s, 0, 0), 30, 12.0, "estimate",
                        cad="parametric", features=dict(ax.features)))
    return out


def _cached_mesh(part, key):
    from parts.head._common import mesh as b_mesh

    from assemblies.hunter_head.assembly import code_sig

    sig = code_sig("parts.head._common")  # every module in parts/head + parts/*.py: an edited model rebuilds
    v, f = cached(_cache_key("headpart", key, sig, sorted((k, str(v)) for k, v in part.params.items())),
                  lambda: (lambda m: (np.asarray(m.vertices), np.asarray(m.faces)))(b_mesh(part)))
    return trimesh.Trimesh(v, f, process=False)


# ------------------------------------------------------------------ the drive

def _geometry(V, alpha, theta, orient, phi=0):
    """Lever rest direction (alpha, deg in the YZ plane from +Z toward +Y), rod direction
    (theta), servo long-side direction (orient in 0..3): the drive's frames and points."""
    xr = TAB_X[0] - ROD_T / 2                            # the rod's mid-plane
    lev = np.array([0.0, math.sin(math.radians(alpha)), math.cos(math.radians(alpha))])
    L = np.array([xr, V[1], V[2]]) + LEVER_R * lev
    rod = np.array([0.0, math.sin(math.radians(theta)), math.cos(math.radians(theta))])
    Tt = L + ROD_L * rod
    # the horn at rest: parallel to the lever (Anderson) or turned `phi` from it (a 4-bar, not a
    # parallelogram: the ratio then varies over the range, which the leverage test covers)
    ph = math.radians(phi)
    h = np.array([0.0, lev[1] * math.cos(ph) + lev[2] * math.sin(ph), lev[2] * math.cos(ph) - lev[1] * math.sin(ph)])
    C = Tt - HORN_R * h
    u = [np.array([0, 1.0, 0]), np.array([0, 0, 1.0]), np.array([0, -1.0, 0]), np.array([0, 0, -1.0])][orient]
    x_spline = xr - ROD_T / 2 - HORN_T                   # the servo side of the horn
    m_servo = _T(_R(u, X, np.cross(u, X)), [x_spline + SERVO_BOSS, C[1], C[2]])
    return dict(xr=xr, lev=lev, L=L, T=Tt, h=h, C=C, rod=rod, m_servo=m_servo, x_spline=x_spline)


_STL = {}


def _anderson_mesh(name):
    """Anderson's STL, loaded once per process (the drive search places it thousands of times)."""
    if name not in _STL:
        _STL[name] = geom.stl(anderson(name))
    return _STL[name]


def _meshes(g, V):
    from parts.library import spec_part

    servo = _xf(spec_part("servo", {"case": "standard", "model": "SERVO_35KG_270"}).mesh, g["m_servo"])
    horn = _xf(_anderson_mesh("visor-servo-horn"),
               _T(_R(-g["h"], np.cross(X, -g["h"]), X), [g["x_spline"], g["C"][1], g["C"][2]]))
    rod = _xf(_anderson_mesh("visor-push-rod"),
              _T(_R(-g["rod"], X, np.cross(-g["rod"], X)), [g["xr"] + ROD_T / 2, g["L"][1], g["L"][2]]))
    tab = _xf(_anderson_mesh("visor-rod-tab"),
              _T(_R(np.cross(X, g["lev"]), X, g["lev"]), np.array([TAB_X[1], V[1], V[2]]) + TAB_AXIS * g["lev"]))
    return servo, horn, rod, tab


def _linkage(g):
    return Linkage("rod_visor", "visor_servo", "head", (g["xr"], g["C"][1], g["C"][2]), (1.0, 0.0, 0.0), HORN_R,
                   tuple(g["h"]), 0.0, "visor", (g["xr"], g["L"][1], g["L"][2]), ROD_L, (-135.0, 135.0), (1.0, 0.0, 0.0))


def design_drive(asm, V, visor_limits):
    """Search Anderson's drive placement (see the module doc). Returns (pick, table)."""
    from workbench.collide import Scene, mesh_hash

    from scipy.spatial import cKDTree

    head_parts = [p for p in asm.parts if p.link in ("head",) and not p.linkage]
    for f in asm.fasteners:  # the head's screws, nuts and inserts where they are
        if f.matrix is not None and f.link == "head" and not f.linkage:
            m = (f.mesh if f.mesh is not None else geom.fastener_mesh(f.spec)).copy()
            m.apply_transform(f.matrix)
            head_parts.append(Part(f.id, f.id, "fastener", "head", m, {}))
    visor_parts = [p for p in asm.parts if p.link == "visor"]
    gimbal = [p for p in asm.parts if p.link in ("neck", "cross")]
    # the gimbal's rods, ball links and arms sweep through the head as it tilts and rolls: their
    # positions over the tilt x roll grid, in the head's own frame, are obstacles too
    swept = []
    lks = [lk for lk in asm.linkages if lk.id != "rod_visor"]
    tl, rl = asm.joint("head_tilt").limits, asm.joint("head_roll").limits
    for t in np.linspace(tl[0], tl[1], 7):
        for r in np.linspace(rl[0], rl[1], 5):
            pose = {"head_tilt": float(t), "head_roll": float(r)}
            ms = link_matrices(asm, pose)
            inv = np.linalg.inv(ms["head"])
            for lk, sol in zip(lks, [solve_linkages(asm, pose).get(lk.id) for lk in lks]):
                if sol is None:
                    continue
                _, a, b = sol
                c = (ms[lk.horn_link] @ np.append(lk.centre, 1.0))[:3]
                pts = np.vstack([a + np.linspace(0, 1, 25)[:, None] * (b - a), c + np.linspace(0, 1, 12)[:, None] * (a - c)])
                swept.append(pts @ inv[:3, :3].T + inv[:3, 3])
    swept = np.vstack(swept) if swept else np.zeros((0, 3))
    key = _cache_key("visor-drive-v7", V.round(3).tolist(), visor_limits, np.round(swept, 1).tobytes(),
                     sorted(mesh_hash(p.mesh) for p in head_parts + visor_parts + gimbal))

    def search(candidates=None):
        # the bracket screws' heads (placed later, but they are there)
        heads = []
        for q in head_parts:
            if q.id.startswith("visor_bracket"):
                for k, f in q.features.items():
                    if k.startswith("hole_screw"):
                        h = trimesh.creation.cylinder(radius=3.6, height=4.2, sections=24)
                        h.apply_transform(trimesh.transformations.rotation_matrix(math.pi / 2, [1, 0, 0]))
                        h.apply_translation(np.asarray(f["p"]) + [0, 2.1, 0])
                        heads.append(Part(f"{q.id}_{k}_head", "", "hardware", "head", h, {}))
        scene = Scene({p.id: p.mesh for p in head_parts + visor_parts + gimbal + heads})
        tree_sw = cKDTree(swept) if len(swept) else None
        rows = []
        for phi, alpha, theta, orient in candidates or itertools.product((0, -30, 30, -60, 60), range(-180, 180, 15),
                                                                         range(0, 360, 15), range(4)):
            g = _geometry(V, alpha, theta, orient, phi)
            servo, horn, rod, tab = _meshes(g, V)
            mine = Scene({"servo": servo, "horn": horn, "rod": rod, "cradle": _cradle_proxy(asm, g)})
            eye = np.eye(4)
            # at rest: the servo and horn against everything on the head and the visor
            worst, who = math.inf, ""
            # the gimbal's swept rods and arms (6.5 mm: housing, nut) against servo, horn and cradle
            if len(swept):
                for k in ("servo", "horn", "cradle"):
                    pk = geom.sample(mine.meshes[k], 2.0, 4000)
                    dsw, _ = tree_sw.query(pk)
                    if float(dsw.min()) - 6.5 < worst:
                        worst, who = float(dsw.min()) - 6.5, f"{k}~gimbal sweep"
            for q in head_parts + visor_parts + heads:
                if worst < CLEAR:
                    break
                for k in ("servo", "horn", "cradle"):
                    if k == "cradle" and q.id in ("mount_plate",):
                        continue
                    d = _dist(mine, k, scene, q.id)
                    if d < worst:
                        worst, who = d, f"{k}~{q.id}"
                if worst < CLEAR:
                    break
            if worst < CLEAR:
                rows.append(dict(phi=phi, alpha=alpha, theta=theta, orient=orient, ok=False, why=f"rest clearance {worst:.1f} ({who})"))
                continue
            # the linkage over the visor range
            saved = asm.linkages
            asm.linkages = [lk for lk in saved if lk.id != "rod_visor"] + [_linkage(g)]
            try:
                vals = np.arange(visor_limits[0], visor_limits[1] + 1e-9, 2.5)
                sols = [solve_linkages(asm, {"visor": float(v)}).get("rod_visor") for v in vals]
                if any(s is None for s in sols):
                    rows.append(dict(phi=phi, alpha=alpha, theta=theta, orient=orient, ok=False, why="unreachable"))
                    continue
                travel = max(abs(s[0]) for s in sols)
                gains = []
                for v in vals:
                    J = servo_jacobian(asm, {"visor": float(v)}, ["visor"])
                    gains.append(abs(float(J[-1, 0])) if J is not None else 0.0)
                lever = min(gains)
                # the moving rod (and horn) against the head and the visor through the range
                clr = worst
                for v, s in zip(vals, sols):
                    ms = link_matrices(asm, {"visor": float(v)})
                    ang, a, b = s
                    # the rod's four long edges (7 mm wide in the plane, 5 mm thick along X)
                    seg = a + np.linspace(0, 1, 20)[:, None] * (b - a)
                    perp = np.cross(X, _unit(b - a))
                    edges = np.vstack([seg + sx * 2.5 * X + sp_ * 3.5 * perp for sx in (-1, 1) for sp_ in (-1, 1)])
                    for q in head_parts + visor_parts:
                        inv = np.linalg.inv(ms[q.link])
                        loc = edges @ inv[:3, :3].T + inv[:3, 3]
                        dq = _point_dist(scene, q.id, loc)
                        if dq < clr:
                            clr, who = dq, f"rod~{q.id}"
                ok = travel <= 135 and lever >= 0.25 and clr >= CLEAR
                rows.append(dict(phi=phi, alpha=alpha, theta=theta, orient=orient, ok=ok, travel=round(travel, 1),
                                 lever=round(lever, 3), clear=round(float(clr), 2), worst=who,
                                 ratio=round(1.0 / gains[len(gains) // 2], 3) if gains[len(gains) // 2] else None))
            finally:
                asm.linkages = saved
        return rows

    # incremental: when the geometry changed, re-check the placements that passed last time first
    # (seconds); the full search (~10 min) runs only when none of them passes any more, or with
    # WB_RESEARCH=1. The table then says which it was.
    import os

    from workbench.geom import CACHE

    last_f = CACHE / f"visor-drive-last-{_cache_key(V.round(3).tolist(), visor_limits)}.json"
    hit = (CACHE / f"{key}.pkl").exists()
    rows = None
    if not hit and last_f.exists() and not os.environ.get("WB_RESEARCH"):
        import json

        prev = json.loads(last_f.read_text())
        cand = [(r["phi"], r["alpha"], r["theta"], r["orient"]) for r in prev]
        rows = search(cand)
        if any(r.get("ok") for r in rows):
            for r in rows:
                r["revalidated"] = True
            cached(key, lambda: rows)
        else:
            rows = None
    if rows is None:
        rows = cached(key, search)
    ok = [r for r in rows if r.get("ok")]
    if ok:
        import json

        last_f.write_text(json.dumps([{k: r[k] for k in ("phi", "alpha", "theta", "orient")} for r in ok]))
    pick = max(ok, key=lambda r: (r["clear"] >= 3.0, round(r["lever"], 2), r["clear"])) if ok else None
    return pick, rows


def _unit(v):
    v = np.asarray(v, float)
    return v / np.linalg.norm(v)


def _cradle_proxy(asm, g):
    """A box stand-in for the cradle during the search: the sleeve round the servo body and a
    12 mm column from it down to the plate."""
    sleeve = trimesh.creation.box([55.6, 20.0, 27.0])
    sleeve.apply_translation([10.0, -26.9, 0.0])
    sleeve.apply_transform(g["m_servo"])
    lo, hi = sleeve.bounds
    c = (lo + hi) / 2
    plate = next(p for p in asm.parts if p.id == "mount_plate").mesh
    hits = [h for h in geom.ray_depth(plate, [c[0], lo[1], c[2]], [0.0, -1.0, 0.0]) if h > 0]
    parts = [sleeve]
    if hits and hits[0] < 60:
        col = trimesh.creation.box([12.0, hits[0], 12.0])
        col.apply_translation([c[0], lo[1] - hits[0] / 2, c[2]])
        parts.append(col)
    return trimesh.util.concatenate(parts)


def _dist(mine, k, scene, qid):
    eye = np.eye(4)
    import fcl

    oa, ob = mine.obj(k), scene.obj(qid)
    mine._set(oa, eye)
    scene._set(ob, eye)
    res = fcl.DistanceResult()
    return max(0.0, float(fcl.distance(oa, ob, fcl.DistanceRequest(), res)))


def _point_dist(scene, qid, pts):
    from scipy.spatial import cKDTree

    if not hasattr(scene, "_trees"):
        scene._trees = {}
    if qid not in scene._trees:
        scene._trees[qid] = cKDTree(geom.sample(scene.meshes[qid], 1.5, 15000))
    d, _ = scene._trees[qid].query(pts)
    return float(d.min())


def drive_parts(asm, V, pick):
    """The servo, horn, push rod, tab and pins at the chosen placement; the linkage."""
    from parts.library import spec_part

    g = _geometry(V, pick["alpha"], pick["theta"], pick["orient"], pick.get("phi", 0))
    servo, horn, rod, tab = _meshes(g, V)
    lk = _linkage(g)
    lk.inferred = True
    lk.inferred_note = ("Anderson's horn/rod/tab; the servo's place on Hunter's head is chosen by design_drive "
                        f"(lever rest {pick['alpha']} deg, horn {pick.get('phi', 0)} deg off it, rod {pick['theta']} deg, "
                        f"servo orientation {pick['orient']}).")
    lk.parts = ["visor_horn", "visor_push_rod"]
    sp = np.array([g["x_spline"] + SERVO_BOSS, g["C"][1], g["C"][2]])
    src = lambda n: {"file": f"vendor/animation/{ANDERSON}/visor - {n}.stl", "kind": "stl", "placement": "mates"}
    parts = [
        Part("visor_servo", "Visor servo 35 kg standard (Anderson's; datasheet case)", "servo", "head", servo,
             {"kind": "parametric", "model": "parts/models.py:servo", "placement": "mates"}, "servo", False,
             (0, 0, 1), 40, 70.0, "catalogue", cad="parametric", inferred=True, inferred_note=lk.inferred_note,
             features={"spline": spline(sp, X, 25), "boss": plane(sp - X * SERVO_BOSS, X)}),
        Part("visor_horn", "Visor servo horn 25 mm (Anderson)", "mech", "head", horn, src("visor-servo-horn"),
             "PETG (printed)", True, (1, 0, 0), 20, 3.0, "estimate", linkage="rod_visor", role="horn", cad="mesh",
             features={"spline": spline(sp, X, 25), "servo_face": plane(sp - X * SERVO_BOSS, -X),
                       "tip": axis([g["x_spline"], g["T"][1], g["T"][2]], X, 1.5)}),
        Part("visor_push_rod", "Visor push rod 58 mm (Anderson)", "mech", "head", rod, src("visor-push-rod"),
             "PETG (printed)", True, (0, 1, 0), 20, 3.0, "estimate", linkage="rod_visor", role="rod", cad="mesh",
             features={"pin_a": axis([g["xr"] - ROD_T / 2, g["L"][1], g["L"][2]], X, 1.75),
                       "pin_b": axis([g["xr"] - ROD_T / 2, g["T"][1], g["T"][2]], X, 1.75)}),
        Part("visor_tab", "Visor rod tab 18 mm (Anderson)", "mech", "visor", tab, src("visor-rod-tab"),
             "PETG (printed)", True, (1, 0, 0), 20, 3.0, "estimate", cad="mesh",
             features={"socket": axis([TAB_X[0], V[1], V[2]], X, 4.0),
                       "tip": axis([TAB_X[0], g["L"][1], g["L"][2]], X, 1.75),
                       "cross": axis(np.array([(TAB_X[0] + TAB_X[1]) / 2, V[1], V[2]]) - 8.75 * np.cross(X, g["lev"]),
                                     np.cross(X, g["lev"]), 1.75)}),
    ]
    return parts, lk, g


def cradle_part(asm, g):
    """Our cradle under the visor servo: its leg runs down to the first surface below (the plate)."""
    from parts.head import visor_servo_cradle

    R = g["m_servo"][:3, :3]
    down_c = R.T @ np.array([0.0, -1.0, 0.0])
    k = int(np.argmax(np.abs(down_c[[0, 2]])))
    leg_dir = np.zeros(3)
    leg_dir[[0, 2][k]] = np.sign(down_c[[0, 2][k]])
    base = visor_servo_cradle.make()
    P0 = base.params
    # the sleeve's side toward the plate, in the head frame, then a ray down to the plate
    x0 = P0["body_x0"] - P0["fit"] / 2
    x1 = P0["body_x0"] + P0["body"][0] + P0["fit"] / 2
    zw = P0["body"][1] / 2 + P0["fit"] / 2
    side = np.array([(x1 + 7.5) if leg_dir[0] > 0 else (x0 - 7.5) if leg_dir[0] < 0 else (x0 + x1) / 2,
                     P0["flange_y"] - P0["sleeve_h"] / 2,
                     (zw + 3.0) if leg_dir[2] > 0 else (-zw - 3.0) if leg_dir[2] < 0 else 0.0])
    start = (g["m_servo"] @ np.append(side, 1.0))[:3]
    plate = next(p for p in asm.parts if p.id == "mount_plate").mesh
    hits = [h for h in geom.ray_depth(plate, start, [0.0, -1.0, 0.0]) if h > 0]
    L = float(hits[0]) if hits and hits[0] < 60 else 0.0
    c = visor_servo_cradle.make(leg_dir=tuple(leg_dir), leg_len=L)
    mesh = _cached_mesh(c, f"vcradle{np.round(g['m_servo'], 3).tolist()}{L:.2f}")
    mesh = _xf(mesh, g["m_servo"])
    from workbench.mates import moved

    feats = {k2: moved(v, g["m_servo"]) for k2, v in c.features.items()}
    return Part("visor_servo_cradle", "Visor servo cradle (ours)", "mech", "head", mesh,
                {"kind": "parametric", "model": "parts/head/visor_servo_cradle.py", "params": c.params,
                 "placement": "mates"}, "PLA (printed)", True, (0, 1, 0), 30, 12.0, "estimate",
                cad="parametric", features=feats, inferred=True,
                inferred_note="new part: where it bolts to the plate needs two holes in the plate (to add)")


def visor_mates(hw, P, ctx):
    """Mates for the visor: bearings in the brackets, stub axles in the bearings, adapters and
    the tab on the axles, the kit visor on the adapters, the servo in its cradle, the horn on the
    spline, the rod's pins."""
    from workbench.geom import fastener_mesh  # noqa: F401
    from workbench.mates import frame_on_axis
    from parts.library import spec_part

    from assemblies.hunter_head.hardware import contact_mates

    g = dict(ctx["geo"], V=ctx["V"])
    for s in ("l", "r"):
        hw.mate("press", (f"visor_bearing_{s}", "outer"), (f"visor_bracket_{s}", "bearing_seat"))
        hw.mate("concentric", (f"visor_axle_{s}", "axis"), (f"visor_bearing_{s}", "bore"))
        hw.mate("concentric", (f"visor_hub_{s}", "bore"), (f"visor_axle_{s}", "axis"), note="on the D, set screw on the flat")
    hw.mate("concentric", ("visor_tab", "socket"), ("visor_axle_l", "axis"),
            note="straddles the axle's 8 mm section, D flat under the tab's bridge, 3 mm cross pin")
    contact_mates(hw, P, [("h_v_5", "h_v_2"), ("h_v_4", "h_v_3"), ("h_v_2", "h_v_1"), ("h_v_3", "h_v_1")], "glue",
                  "the kit visor's printed joints (glued in the kit)")
    # the hubs onto the kit arms: M3 inserts in the hub, M3 screws from the arm's outboard face
    for s, sg, arm in (("l", 1, "h_v_2"), ("r", -1, "h_v_3")):
        hub = P[f"visor_hub_{s}"]
        hw.mate("seated", (f"visor_hub_{s}", "face_out"), (arm, "arm_in"), note="hub face on the arm's inboard face")
        lo, hi = P[arm].mesh.bounds
        out_x = float(hi[0]) if sg > 0 else float(lo[0])
        P[arm].features["arm_in"] = plane([float(lo[0]) if sg > 0 else float(hi[0]), g["V"][1], g["V"][2]], [-sg, 0, 0])
        k = 0
        for name in sorted(n[5:] for n in hub.features if n.startswith("hole_ins")):
            k += 1
            h = hub.features[f"hole_{name}"]
            ins = hw.insert(f"ins_vhub_{s}{k}", f"visor_hub_{s}", name, "s12a",
                            spec={"type": "insert", "thread": "M3", "length_mm": 5.7, "od_mm": 4.6})
            hn = hw.hole(arm, f"vhub{k}", [out_x, h["p"][1], h["p"][2]], [-sg, 0, 0], 1.95)[0][5:]
            hw.screw(f"scr_vhub_{s}{k}", [out_x, h["p"][1], h["p"][2]], [-sg, 0, 0], [(arm, hn)],
                     (ins.id, "thread", "insert", None), "s12a", "visor", thread="M3",
                     note="head in the axle end's Ø5.8 pocket (H_V_4/5 glued over it)")
    contact_mates(hw, P, [("h_le_1", "side_left"), ("h_re_1", "side_right"), ("h_le_2", "h_le_1"),
                          ("h_re_2", "h_re_1")], "glue", "the kit's ear cups on Hunter's side pieces")
    # the kit's face parts: each glued to whatever it touches first (the face plate to Hunter's
    # shell, the eyes and mouth to the face plate / each other), as the kit's head goes together
    from scipy.spatial import cKDTree

    from workbench.geom import sample

    held = [q for q in ("head_top", "side_left", "side_right", "head_bottom", "mount_plate") if q in P]
    todo = [q for q in KIT_FACE if q in P]
    if "mouth_shim" in P:  # the mouth sits on our shim, the shim on the head bottom
        hw.mate("glue", ("mouth_shim", "face_bottom"), ("head_bottom", "shim_seat"), solved=False,
                note="the shim glued where the mouth comes closest", gap_mm=0.0)
        P["head_bottom"].features["shim_seat"] = dict(P["mouth_shim"].features["face_bottom"],
                                                      n=[-x for x in P["mouth_shim"].features["face_bottom"]["n"]])
        hw.mate("glue", ("h_m_1", "shim_top"), ("mouth_shim", "face_top"), solved=False,
                note="the kit mouth glued on the shim", gap_mm=0.0)
        P["h_m_1"].features["shim_top"] = dict(P["mouth_shim"].features["face_top"],
                                               n=[-x for x in P["mouth_shim"].features["face_top"]["n"]])
        held.append("h_m_1")
        todo.remove("h_m_1")
    pts = {q: sample(P[q].mesh, 1.5, 6000) for q in held + todo}
    while todo:
        best = None
        for q in todo:
            tq = cKDTree(pts[q])
            for h in held:
                d = float(tq.query(pts[h])[0].min())
                # kit parts glue to kit parts where they touch (as the kit head goes together); a
                # Hunter shell only takes the part that touches nothing of the kit's
                key = (d > 0.5, h not in KIT_FACE, d)
                if best is None or key < best[0]:
                    best = (key, q, h)
        _, q, h = best
        contact_mates(hw, P, [(q, h)], "glue", "the kit's face parts, glued as the kit head goes together")
        held.append(q)
        todo.remove(q)
    hw.mate("spline", ("visor_horn", "spline"), ("visor_servo", "spline"), teeth=25)
    hw.mate("seated", ("visor_horn", "servo_face"), ("visor_servo", "boss"))
    if "visor_servo_cradle" in P:
        P["visor_servo"].features["flange_under"] = {
            "type": "plane", "p": list((g["m_servo"] @ np.array([10.0, -16.9, 0.0, 1.0]))[:3]),
            "n": list(g["m_servo"][:3, :3] @ np.array([0.0, -1.0, 0.0]))}
        hw.mate("seated", ("visor_servo", "flange_under"), ("visor_servo_cradle", "flange_seat"))
        if "leg_foot" in P["visor_servo_cradle"].features:
            contact_mates(hw, P, [("visor_servo_cradle", "mount_plate")], "glue",
                          "the cradle's leg on the plate (2 x M4 into the plate: holes to add)")
    # the rod's pins: 3 mm x 12.7 (Anderson's pin), through the tab tip + rod, and rod + horn tip
    pin = spec_part("pin", {"d_mm": 3.0, "length_mm": 12.7}).mesh
    for name, at, link, lk, role, a, b in (
            ("pin_visor_tab", [TAB_X[1], g["L"][1], g["L"][2]], "visor", None, None, "visor_tab", "tip"),
            ("pin_visor_horn", [g["x_spline"] + HORN_T + ROD_T + 2.7, g["T"][1], g["T"][2]], "head", "rod_visor", "horn",
             "visor_horn", "tip")):
        m = frame_on_axis(np.asarray(at, float), [-1.0, 0.0, 0.0])
        f = Fastener(name, {"type": "pin", "d_mm": 3.0, "length_mm": 12.7}, "pin-3x12.7", [a, "visor_push_rod"],
                     link, "s12", m, True, "Anderson's 3 mm pin (pin-dnp)", mesh=pin, cad="parametric",
                     features={"shank": axis(at, [-1.0, 0.0, 0.0], 1.5)}, linkage=lk, role=role)
        hw.fast.append(f)
        hw.mate("concentric", (name, "shank"), (a, b))
        hw.mate("concentric", (name, "shank"), ("visor_push_rod", "pin_a" if a == "visor_tab" else "pin_b"))
    # the tab's cross pin through the axle's 8 mm section (Anderson pins his tab the same way)
    n = np.cross(X, g["lev"])
    at = np.array([(TAB_X[0] + TAB_X[1]) / 2, g["V"][1], g["V"][2]]) - 8.0 * n
    pin16 = spec_part("pin", {"d_mm": 3.0, "length_mm": 16.0}).mesh
    hw.fast.append(Fastener("pin_visor_tab_axle", {"type": "pin", "d_mm": 3.0, "length_mm": 16.0}, "pin-3x16",
                            ["visor_tab", "visor_axle_l"], "visor", "s12", frame_on_axis(at, n), True,
                            "3 mm dowel pin across the tab's jaws and the axle", mesh=pin16, cad="parametric",
                            features={"shank": axis(at, n, 1.5)}))
    hw.mate("concentric", ("pin_visor_tab_axle", "shank"), ("visor_tab", "cross"))
    hw.mate("concentric", ("pin_visor_tab_axle", "shank"), ("visor_axle_l", "tab_pin"))
