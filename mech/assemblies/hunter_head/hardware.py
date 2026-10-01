"""Hunter's head: the real purchased hardware, placed by mates.

Everything here comes from the parts library (mech/parts: goBILDA vendor STEPs, ISO parametric
screws and inserts) and is *solved* from features measured on the parts it joins: a hole's
axis and entry face from a section of the placed mesh, a servo's spline and boss from its
geometry, the arm's hole circle from the vendor model. No hand-placed transforms: a screw's
matrix is its shank axis on the hole axis with its head on the entry face; its length is the
smallest standard length that engages the thread by the suite's minimum.

The push-rod geometry the STEP leaves open (which arm hole, which hub hole, horn clocking, ball
above or below the arm) is chosen by `design_linkage`: every candidate that real parts allow
(arm holes at 24/32/40/48 mm, the hub's 16 mm pattern, the 25T spline's 14.4 deg steps), kept
when the 50 mm rod's thread engagement, reach, servo travel, rod-end swivel and leverage all
pass over the tilt x roll range, best leverage first. It is reported as inferred: Brandon
confirms it against Hunter's build.
"""

from __future__ import annotations

import itertools
import math

import numpy as np
import trimesh

from parts.library import part as lib_part, spec_part
from workbench import geom
from workbench.geom import cached, _cache_key
from workbench.kinematics import horn_basis, link_matrices, servo_jacobian, solve_linkages
from workbench.mates import Mate, axis, ball, frame_on_axis, plane, spline, unit
from workbench.model import Fastener, Linkage, Part

UP = np.array([0.0, 1.0, 0.0])
STD_LEN = [5, 6, 8, 10, 12, 14, 16, 20, 25, 30, 35, 40, 45, 50]
TEETH = 25
SPLINE_ABOVE_BOSS = 4.1       # goBILDA 2000: spline top 16.9, case boss top 12.8 (vendor STEP)
HUB_H = 5.5                   # 1906 hub, servo face to arm face (vendor STEP)
ARM_T = 6.0                   # 1916 arm thickness (vendor STEP)
ARM_HOLES = (24.0, 32.0, 40.0, 48.0)   # 1916 arm: holes along +X (vendor STEP)
BALL_HALF = 3.745             # 2913 ball: 7.49 thick along its bore
BALL_THREAD = (10.2, 24.37)   # 2913 housing: female thread from/to, mm from the ball centre
WASHER_T = 0.8
SPACER = {"type": "washer", "thread": "M4", "id_mm": 4.2, "od_mm": 6.0, "t_mm": 4.0}
SPACER_T = 4.0                # under each ball: a 6 mm OD x 4 mm spacer keeps the housing off the arm/hub
ROD_L = 50.0                  # 2808 rod
ENGAGE_MIN = 6.0              # rod into each housing: 1.5 d
SWIVEL_MAX = 25.0
TRAVEL_MAX = 135.0
LEVER_MIN = 0.25


def _nom(thread):
    return float(thread[1:])


# the servo bosses' inserts by thread (None: Hw.insert's default, the kit's 6 x 6 mm M4)
SERVO_INSERT = {"M3": {"type": "insert", "thread": "M3", "length_mm": 5.7, "od_mm": 4.6,
                       "note": "Ruthex RX-M3x5.7 (hole 4.0; OD 4.6 from its datasheet, not on file)"}}


def screw_len(grip: float, need: float, avail: float | None = None) -> int | None:
    """Smallest standard length engaging at least `need` past the grip (and not bottoming)."""
    for L in STD_LEN:
        if L >= grip + need - 0.05 and (avail is None or L <= grip + avail + 0.05):
            return L
    return None


class Hw:
    """Collects the fasteners, extra parts, features and mates as they are solved."""

    def __init__(self, asm, parts_by_id):
        self.asm = asm
        self.p = parts_by_id
        self.fast: list[Fastener] = []
        self.mates: list[Mate] = []
        self.extra: list[Part] = []
        self.n = 0

    # ---------------------------------------------------------------- features on existing parts
    def hole(self, pid, name, entry, d, r):
        """An axis + its entry face on part `pid` (entry on the surface, d into the material)."""
        f = self.p[pid].features
        f[f"hole_{name}"] = axis(entry, d, r)
        f[f"face_{name}"] = plane(entry, -np.asarray(d, float))
        return f"hole_{name}", f"face_{name}"

    def mate(self, type_, a, b, solved=True, note="", **params):
        self.n += 1
        self.mates.append(Mate(f"m{self.n:03d}_{type_}", type_, a, b, params, solved, note))

    # ---------------------------------------------------------------- inserts and screws
    def insert(self, fid, pid, hole_name, step, spec=None):
        """A heat-set insert, top flush with the hole's entry face, pressed into the hole."""
        spec = spec or {"type": "insert", "thread": "M4", "length_mm": 6, "od_mm": 6}
        h = self.p[pid].features[f"hole_{hole_name}"]
        m = frame_on_axis(h["p"], h["d"])
        # how deep the hole runs below the insert (a blind boss): the screw may use all of it
        hits = geom.ray_depth(self.p[pid].mesh, np.asarray(h["p"]) + unit(h["d"]) * 0.3, h["d"])
        depth = (hits[0] + 0.3) if hits and hits[0] < 60 else None
        r = spec_part("insert", spec)
        d = np.asarray(h["d"])
        feats = {"bore": axis(h["p"], d, _nom(spec["thread"]) / 2), "outer": axis(h["p"], d, spec["od_mm"] / 2),
                 "top": plane(h["p"], -d), "thread": axis(h["p"], d, _nom(spec["thread"]) / 2)}
        if depth is not None:
            feats["thread"]["depth"] = round(float(depth), 2)
        key = f"insert-{spec['thread']}x{spec['length_mm']:g}"
        f = Fastener(fid, dict(spec, note="6 x 6 mm (BOM)") if key == "insert-M4x6" else dict(spec), key, [pid],
                     self.p[pid].link, step, m,
                     mesh=r.mesh, cad=r.status, features=feats)
        self.fast.append(f)
        self.mate("press", (fid, "outer"), (pid, f"hole_{hole_name}"), note="heat-set into the printed hole")
        self.mate("coplanar", (fid, "top"), (pid, f"face_{hole_name}"))
        return f

    def screw(self, fid, head_at, d, clamps, into, step, link, kind="shcs", thread="M4", catalog=None,
              inferred=False, note="", linkage=None, role=None, joins=None, length=None):
        """A screw whose head bears at `head_at`, shank along `d` through `clamps` [(part, hole name)]
        into `into` = (part id, feature name, 'insert'|'plastic'|'metal'|'nut', available depth).
        Its length is chosen from the grip and the minimum engagement."""
        d = unit(d)
        head_at = np.asarray(head_at, float)
        tpid, tfeat, kind_into, avail = into
        tf = (self.p.get(tpid) or next(x for x in self.fast if x.id == tpid)).features[tfeat]
        if kind_into == "insert" and "depth" in tf:
            avail = max(avail or 0.0, tf["depth"])  # the blind hole under the insert
        grip = float((np.asarray(tf["p"]) - head_at) @ d)
        need = {"insert": 1.5, "plastic": 2.0, "metal": 1.0, "nut": 1.05, "nut_thin": 0.5}[kind_into] * _nom(thread)
        L = length or screw_len(grip, need, avail)
        if L is None:
            fits = [x for x in STD_LEN if avail is None or x <= grip + avail + 1e-6]
            L = max(fits) if fits else STD_LEN[0]
            note = (note + f" Only {max(0.0, L - grip):.1f} mm of thread is available ({need:.1f} wanted).").strip()
        spec = {"type": kind, "thread": thread, "length_mm": L}
        r = lib_part(*catalog.split(":")) if catalog else spec_part("screw", spec)
        feats = {"shank": axis(head_at, d, _nom(thread) / 2), "head": plane(head_at, d)}
        key = f"{kind}-{thread}x{L:g}"
        f = Fastener(fid, spec, key, joins or [c[0] for c in clamps] + [tpid], link, step, frame_on_axis(head_at, d),
                     inferred, note, mesh=r.mesh, cad=r.status, catalog=catalog, features=feats,
                     linkage=linkage, role=role)
        self.fast.append(f)
        for pid, hn in clamps:
            self.mate("concentric", (fid, "shank"), (pid, f"hole_{hn}"))
        if clamps:
            self.mate("seated", (fid, "head"), (clamps[0][0], f"face_{clamps[0][1]}"))
        engage = min(L - grip, avail) if avail is not None else L - grip
        self.mate("threaded", (fid, "shank"), (tpid, tfeat), engage_mm=round(engage, 2), into=kind_into,
                  hole_depth_mm=avail)
        return f

    def nut(self, fid, at, d, pid, step, link, linkage=None, role=None, reason="clamp", kind="lock_nut",
            clock_ref=None):
        """A nut: `kind` lock_nut (nylon ring), nut (ISO 4032) or thin_nut (DIN 439, 2.2 mm); `reason` is
        why a nut and not an insert (fastening rule): clamp | pivot | captive."""
        spec = {"type": kind if kind != "thin_nut" else "nut", "thread": "M4", "reason": reason,
                "thickness_mm": NUT_T["M4"] if kind == "nut" else THIN_NUT_T if kind == "thin_nut" else 5.0}
        if kind == "thin_nut":
            spec["standard"] = "DIN 439"
            import manifold3d as mf

            hexa = mf.Manifold.cylinder(THIN_NUT_T, 7.0 / math.sqrt(3), 7.0 / math.sqrt(3), 6)
            bore = mf.Manifold.cylinder(THIN_NUT_T + 1, 1.7, 1.7, 32).translate((0, 0, -0.5))
            mm = (hexa - bore).to_mesh()
            mesh = trimesh.Trimesh(np.asarray(mm.vert_properties)[:, :3], np.asarray(mm.tri_verts))
            cad = "parametric"
        else:
            r = spec_part("nut", {"type": kind, "thread": "M4"})
            mesh, cad = r.mesh, r.status
        d = unit(d)
        feats = {"thread": axis(at, d, 2.0), "face": plane(at, -d)}
        key = {"lock_nut": "lock_nut-M4", "nut": "nut-M4", "thin_nut": "thin_nut-M4"}[kind]
        self.fast.append(Fastener(fid, spec, key, [pid], link, step, frame_on_axis(at, d, clock_ref), mesh=mesh,
                                  cad=cad, features=feats, linkage=linkage, role=role))
        return fid

    def washer(self, fid, at, d, pid, hole, step, link, linkage=None, role=None, spec=None):
        spec = spec or {"type": "washer", "thread": "M4"}
        r = spec_part("washer", spec)
        d = unit(d)
        t = spec.get("t_mm", WASHER_T)
        feats = {"bore": axis(at, d, 2.15), "bottom": plane(np.asarray(at) + d * t, d),
                 "top": plane(at, -d)}
        key = "washer-M4" if "od_mm" not in spec else f"spacer-M4-{spec['od_mm']:g}x{spec['t_mm']:g}"
        self.fast.append(Fastener(fid, spec, key, [pid], link, step, frame_on_axis(at, d), mesh=r.mesh,
                                  cad=r.status, features=feats, linkage=linkage, role=role))
        self.mate("concentric", (fid, "bore"), (pid, f"hole_{hole}"))
        self.mate("seated", (fid, "bottom"), (pid, f"face_{hole}"))
        return fid


# ------------------------------------------------------------------ measurement

def holes_at(mesh, ax, at, rmin, rmax):
    return [(c, r) for c, r in geom.section_holes(mesh, ax, at, rmin, rmax)]


def surface_along(mesh, origin, d, max_d=60.0):
    """First surface a ray from `origin` along `d` meets (point, distance) - finds hole entry faces."""
    hits = geom.ray_depth(mesh, origin, unit(d))
    hits = [h for h in hits if 0 <= h <= max_d]
    return (np.asarray(origin) + unit(d) * hits[0], hits[0]) if hits else (None, None)


def entry_point(mesh, c, d, r, probe=60.0):
    """The hole's entry on the surface: walk back from a point inside the hole (`c`) against `d`
    and take the first surface just outside the hole radius."""
    d = unit(d)
    side = np.cross(d, [1, 0, 0] if abs(d[0]) < 0.9 else [0, 0, 1])
    side = unit(side) * (r + 0.6)
    start = np.asarray(c) - d * probe + side
    p, dist = surface_along(mesh, start, d, 2 * probe)
    return None if p is None else p - side


# ------------------------------------------------------------------ push-rod design

CLEAR_MIN = 1.0   # rod + housings to every head-side part, across the range (print tolerance)
HOUSING_R = 4.6   # 2913 housing body radius around the rod axis


def design_linkage(asm, servos, hub_holes, hub_face_y, head_pts=None):
    """Pick the real-part push-rod geometry (see module doc). Returns (chosen, table).
    `head_pts`: surface samples of every head-side part (shell, plate, servos...) that the rods
    and their housings must clear by CLEAR_MIN through the whole range."""
    from scipy.spatial import cKDTree

    tree = cKDTree(head_pts) if head_pts is not None and len(head_pts) else None
    key = _cache_key("hunter-linkage-v4", CLEAR_MIN, HOUSING_R, 0 if head_pts is None else len(head_pts), SPACER_T, BALL_HALF, HUB_H, ARM_T, ARM_HOLES, ROD_L, ENGAGE_MIN,
                     SWIVEL_MAX, TRAVEL_MAX, LEVER_MIN, {k: np.round(v, 3).tolist() for k, v in servos.items()},
                     np.round(hub_holes, 3).tolist(), round(hub_face_y, 3),
                     [(j.id, j.limits) for j in asm.joints])

    def search():
        rows = []
        clocks = [k * 360.0 / TEETH for k in range(TEETH)]
        for r_arm, (hi, hole), ball_side in itertools.product(ARM_HOLES, enumerate(hub_holes), ("above", "below")):
            if hole[0] <= 0:  # the left servo takes a hole on its own side; the right mirrors it
                continue
            for phi in clocks:
                cand = _candidate(asm, servos, hole, hub_face_y, r_arm, phi, ball_side, tree)
                if cand is None:
                    continue
                rows.append(cand)
        return rows

    rows = cached(key, search)
    ok = [c for c in rows if c["pass"]]
    pick = max(ok, key=lambda c: (round(c["lever"], 2), -c["swivel"])) if ok else \
        max(rows, key=lambda c: (c["reach"], c["lever"]))
    return pick, rows


def _stack(r_arm, ball_side):
    """Ball-centre height above the servo boss: hub, arm, washer, half the ball (above), or
    under the arm (below)."""
    if ball_side == "above":
        return HUB_H + ARM_T + SPACER_T + BALL_HALF
    return HUB_H - SPACER_T - BALL_HALF


def _linkages(servos, hole, hub_face_y, r_arm, phi, ball_side):
    out = []
    for side, sx in (("l", 1), ("r", -1)):
        c = servos[side]
        boss = c - UP * SPLINE_ABOVE_BOSS
        ang = math.radians(phi if sx > 0 else 180.0 - phi)
        u = np.array([math.cos(ang), 0.0, math.sin(ang)])
        off = _stack(r_arm, ball_side)
        b = np.array([sx * abs(hole[0]), hub_face_y + SPACER_T + BALL_HALF, hole[2]])
        a = boss + UP * off + r_arm * u
        out.append(Linkage(f"rod_{side}", f"servo_{side}", "head", tuple(boss), (0, 1, 0), r_arm, tuple(u), off,
                           "neck", tuple(b), float(np.linalg.norm(a - b)), (-TRAVEL_MAX, TRAVEL_MAX), (0, 1, 0)))
    return out


def _candidate(asm, servos, hole, hub_face_y, r_arm, phi, ball_side, tree=None):
    lks = _linkages(servos, hole, hub_face_y, r_arm, phi, ball_side)
    L = lks[0].rod_length
    e = (ROD_L - (L - 2 * BALL_THREAD[1])) / 2  # rod engagement per end, centred
    if not (ENGAGE_MIN <= e <= BALL_THREAD[1] - BALL_THREAD[0]):
        return None
    saved = asm.linkages
    asm.linkages = lks
    try:
        reach, lever, travel, swivel, clear = 1.0, math.inf, 0.0, 0.0, math.inf
        tilt = asm.joint("head_tilt").limits
        roll = asm.joint("head_roll").limits
        poses = [{"head_tilt": t, "head_roll": r} for t in np.arange(tilt[0], tilt[1] + 1e-9, 5.0)
                 for r in np.arange(roll[0], roll[1] + 1e-9, 4.0)]
        bad = 0
        for pose in poses:
            sol = solve_linkages(asm, pose)
            if any(s is None for s in sol.values()):
                bad += 1
                continue
            ms = link_matrices(asm, pose)
            inv = np.linalg.inv(ms["head"])
            for lk in lks:
                ang, a, b = sol[lk.id]
                travel = max(travel, abs(ang))
                if tree is not None:
                    seg = a + np.linspace(0, 1, 30)[:, None] * (b - a)
                    loc = seg @ inv[:3, :3].T + inv[:3, 3]
                    dd, _ = tree.query(loc)
                    clear = min(clear, float(dd.min()) - HOUSING_R)
                n = ms["head"][:3, :3] @ UP
                rr = unit(b - a)
                swivel = max(swivel, math.degrees(math.asin(min(1, abs(rr @ n)))), math.degrees(math.asin(min(1, abs(rr @ UP)))))
            jac = servo_jacobian(asm, pose, ["head_tilt", "head_roll"])
            if jac is not None:
                lever = min(lever, float(np.min(np.max(np.abs(jac), axis=0))))
        reach = 1 - bad / len(poses)
        ok = (reach == 1.0 and travel <= TRAVEL_MAX and swivel <= SWIVEL_MAX and lever >= LEVER_MIN
              and clear >= CLEAR_MIN)
        return {"r_arm": r_arm, "hole": [float(x) for x in hole], "phi": round(phi, 2), "ball": ball_side,
                "L": round(L, 2), "engage": round(e, 2), "reach": round(reach, 3), "lever": round(lever, 3),
                "travel": round(travel, 1), "swivel": round(swivel, 1),
                "clear": round(clear, 2) if clear < math.inf else None, "pass": ok}
    finally:
        asm.linkages = saved


# ------------------------------------------------------------------ the whole head's hardware

def add_hardware(asm, fit, visor):
    """Linkage hardware, servo horns, every fastener, and the features/mates that place them."""
    P = {p.id: p for p in asm.parts}
    hw = Hw(asm, P)
    notes = []

    # --- measured features on the STEP / fitted parts --------------------------------------
    servos = {}
    for s in ("l", "r"):
        sv = P[f"servo_{s}"].mesh
        V = sv.vertices
        top = V[V[:, 1] > V[:, 1].max() - 1.0]
        c = np.array([top[:, 0].mean(), V[:, 1].max(), top[:, 2].mean()])
        servos[s] = c
        P[f"servo_{s}"].features.update({"spline": spline(c, UP, TEETH),
                                         "boss": plane(c - UP * SPLINE_ABOVE_BOSS, UP)})
    plate = P["mount_plate"].mesh
    plate_top = float(plate.bounds[1][1])
    # the plate's 6 flange holes (M4 clearance, into the head bottom), servo insert holes, pillow holes
    flange = holes_at(plate, 1, -3.11 + 1.0, 1.8, 2.6)
    # the servo inserts: from the parametric plate's features (their size follows its `inserts` preset:
    # the kit's 6 mm M4, or M3 in 4.0 mm holes), else measured on the mesh
    pf = P["mount_plate"].features
    sins = [k for k in sorted(pf) if k.startswith("hole_sins")]
    servo_ins = [(np.asarray(pf[k]["p"], float), float(pf[k]["r"])) for k in sins] or \
        holes_at(plate, 1, plate_top - 2.0, 2.7, 3.3)
    servo_bolt = (pf[sins[0]].get("bolt") if sins else None) or "M4"
    pillow = holes_at(plate, 1, 25.0 - 0.0, 1.8, 2.6)
    bottom = P["head_bottom"].mesh
    b_ins = holes_at(bottom, 1, fit["boss_top_head"] - 1.5, 2.7, 3.3)
    top_hub = P["hub_top"].mesh
    hub_face = float(top_hub.bounds[1][1])
    hub_holes = sorted([c for c, r in holes_at(top_hub, 1, hub_face - 2.0, 1.5, 2.1)], key=lambda c: (c[0], c[2]))
    coupler = P["neck_coupler"].mesh
    c_top = float(coupler.bounds[1][1])
    # the coupler's four hub holes, from its parametric features (heat-set by the fastening rule)
    cf = P["neck_coupler"].features
    c_holes = [(np.asarray(cf[k]["p"], float), float(cf[k]["r"])) for k in sorted(cf) if k.startswith("hole_cp")]
    notes.append(f"Measured: {len(flange)} plate flange holes, {len(servo_ins)} servo insert holes, {len(pillow)} "
                 f"pillow holes, {len(b_ins)} head-bottom insert holes, {len(hub_holes)} top-hub holes, "
                 f"{len(c_holes)} coupler holes.")

    # --- s01 inserts ------------------------------------------------------------------------
    for i, (c, r) in enumerate(sorted(b_ins, key=lambda h: (h[0][0], h[0][2]))):
        hw.hole("head_bottom", f"ins{i + 1}", [c[0], fit["boss_top_head"], c[2]], -UP, r)
        hw.insert(f"ins_bottom_{i + 1}", "head_bottom", f"ins{i + 1}", "s01")
    for i, (c, r) in enumerate(sorted(servo_ins, key=lambda h: (h[0][0], h[0][2]))):
        hw.hole("mount_plate", f"sins{i + 1}", [c[0], plate_top, c[2]], -UP, r)
        hw.insert(f"ins_servo_{i + 1}", "mount_plate", f"sins{i + 1}", "s01", spec=SERVO_INSERT.get(servo_bolt))

    # the plate's underside rests on the head bottom's six boss tops
    P["head_bottom"].features["boss_top"] = plane([0, fit["boss_top_head"], 0], UP)
    P["mount_plate"].features["underside"] = plane([0, float(plate.bounds[0][1]), 0], -UP)
    hw.mate("seated", ("mount_plate", "underside"), ("head_bottom", "boss_top"), note="the plate on the bosses")
    # --- s10 plate onto the head bottom: 6 screws down through the 4 mm flange -----------------
    for i, (c, r) in enumerate(sorted(flange, key=lambda h: (h[0][0], h[0][2]))):
        top = entry_point(plate, c, -UP, r)
        hw.hole("mount_plate", f"fl{i + 1}", top, -UP, r)
        ins = min((f for f in hw.fast if f.id.startswith("ins_bottom")),
                  key=lambda f: np.linalg.norm(np.asarray(f.features["top"]["p"])[[0, 2]] - c[[0, 2]]))
        clamps = [("mount_plate", f"fl{i + 1}")]
        head = top
        # a visor bracket's foot on this hole: the same screw clamps it too
        for bid in ("visor_bracket_l", "visor_bracket_r"):
            if bid not in P:
                continue
            for k, f in P[bid].features.items():
                if k.startswith("hole_screw") and np.linalg.norm(np.asarray(f["p"])[[0, 2]] - np.asarray(top)[[0, 2]]) < 1.0:
                    clamps = [(bid, k[5:])] + clamps
                    head = np.array([top[0], f["p"][1], top[2]])
        hw.screw(f"scr_plate_bottom_{i + 1}", head, -UP, clamps,
                 (ins.id, "thread", "insert", 6.0), "s09", "head", joins=[c[0] for c in clamps] + ["head_bottom", ins.id])

    # --- s07 servos into the plate: flange holes over the inserts -------------------------------
    for i, f in enumerate([f for f in hw.fast if f.id.startswith("ins_servo")]):
        p = np.asarray(f.features["top"]["p"])
        side = "l" if p[0] > 0 else "r"
        sv = P[f"servo_{side}"].mesh
        fl_top = entry_point(sv, p + UP * 1.0, -UP, 2.0, probe=20.0)
        if fl_top is None:
            fl_top = p + UP * 2.5
        hw.hole(f"servo_{side}", f"fl{i + 1}", fl_top, -UP, 2.2)
        if servo_bolt == "M3":
            # the plate's inserts preset (option a, 2026-09-30): M3 socket heads through the servo's 4.5 mm
            # flange holes into the 5.7 mm M3 inserts; the length is chosen for 1.5 d of thread
            hw.screw(f"scr_servo_{i + 1}", fl_top, -UP, [(f"servo_{side}", f"fl{i + 1}")],
                     (f.id, "thread", "insert", 5.7), "s07", "head", thread="M3",
                     joins=[f"servo_{side}", "mount_plate", f.id])
            continue
        # low heads (DIN 7984: a socket head's rim hits the servo case beside the flange), M4 x 8: the
        # stock plate's 6 mm insert holes (a 10 would reach the drill point)
        hw.screw(f"scr_servo_{i + 1}", fl_top, -UP, [(f"servo_{side}", f"fl{i + 1}")], (f.id, "thread", "insert", 6.0),
                 "s07", "head", kind="shcs_low", thread=servo_bolt, length=8 if servo_bolt == "M4" else None,
                 joins=[f"servo_{side}", "mount_plate", f.id])

    # --- s05 pillow blocks: down through the plate's 4 mm bridge into the tapped posts ----------
    for i, (c, r) in enumerate(sorted(pillow, key=lambda h: (h[0][0], h[0][2]))):
        top = entry_point(plate, c, -UP, r)
        hw.hole("mount_plate", f"pb{i + 1}", top, -UP, r)
        side = "f" if c[2] > 0 else "b"
        pb = P[f"pillow_{side}"].mesh
        post = entry_point(pb, np.array([c[0], top[1] - 6, c[2]]), -UP, 1.7, probe=10.0)
        post = post if post is not None else np.array([c[0], top[1] - 4.0, c[2]])
        hw.hole(f"pillow_{side}", f"post{i + 1}", post, -UP, 1.7)
        hw.screw(f"scr_pillow_{i + 1}", top, -UP, [("mount_plate", f"pb{i + 1}")],
                 (f"pillow_{side}", f"hole_post{i + 1}", "metal", 8.0), "s05", "head",
                 inferred=True, note="goBILDA pillow-block posts are tapped M4 (thread depth 8 assumed).")

    # --- s03 bottom hub onto the coupler: 4 screws through the thru-hole hub into printed holes -
    # The U-joint's pattern mount sits on the thru-hole hub: one set of screws goes down through
    # the U-joint's base and the hub into the coupler's printed M4 holes (why the hub is the 1311).
    hub_b = P["hub_bottom"].mesh
    hb_top = float(hub_b.bounds[1][1])
    uj = P["ujoint"].mesh
    for i, (c, r) in enumerate(sorted(c_holes, key=lambda h: (h[0][0], h[0][2]))):
        # read the part's own hole (kind, depth) before hw.hole replaces it with the bare axis
        trapped = cf[f"hole_cp{i + 1}"].get("kind") == "nut_trap"
        depth = float(cf[f"hole_cp{i + 1}"].get("depth", c_top - cf["bore_stop"]["p"][1]))
        hw.hole("neck_coupler", f"cp{i + 1}", [c[0], c_top, c[2]], -UP, r)
        if trapped:
            # through-bolts with nuts in the plate's traps (fastening rule: the wall round an insert is
            # too thin here); the nut's far face 0.2 mm inside the plate, over the neck tube's top
            nut_t = NUT_T["M4"]
            nut_at = np.array([c[0], c_top - (depth - nut_t - 0.2), c[2]])
            # clocked to the trap's hex (5 deg steps over one 60 deg period): the least shared volume
            best = None
            for ref in ([math.cos(math.radians(a)), 0, math.sin(math.radians(a))] for a in range(0, 60, 5)):
                # pressed into its trap when the coupler is printed (s02), before the hub's screws (s04)
                nid = hw.nut(f"nut_coupler_cp{i + 1}", nut_at, -UP, "neck_coupler", "s02", "neck",
                             reason="clamp", kind="nut", clock_ref=ref)
                fo = hw.fast.pop()
                nm = fo.mesh.copy()
                nm.apply_transform(fo.matrix)
                v = _shared_volume(nm, P["neck_coupler"].mesh)
                if best is None or v < best[0]:
                    best = (v, fo)
            hw.fast.append(best[1])
        else:
            nid = hw.insert(f"ins_coupler_cp{i + 1}", "neck_coupler", f"cp{i + 1}", "s02").id
            # the hub plate is the whole thread: below it is the neck tube's top (the screw stops short)
            next(f for f in hw.fast if f.id == nid).features["thread"]["depth"] = round(depth - 0.5, 2)
        hw.hole("hub_bottom", f"cp{i + 1}", [c[0], hb_top, c[2]], -UP, 2.2)
        top = entry_point(uj, np.array([c[0], hb_top + 1.0, c[2]]), -UP, 2.0, probe=40.0)
        clamps = [("hub_bottom", f"cp{i + 1}")]
        head = np.array([c[0], hb_top, c[2]])
        if top is not None and top[1] > hb_top + 0.5:
            hw.hole("ujoint", f"base{i + 1}", top, -UP, 2.2)
            clamps = [("ujoint", f"base{i + 1}")] + clamps
            head = top
        if trapped:
            grip = float(head[1] - nut_at[1])
            hw.screw(f"scr_hub_coupler_{i + 1}", head, -UP, clamps + [("neck_coupler", f"cp{i + 1}")],
                     (nid, "thread", "nut", nut_t), "s04", "neck", inferred=True, length=round(grip + nut_t, 1),
                     joins=[c[0] for c in clamps] + ["neck_coupler", nid],
                     note="Through the U-joint's pattern mount, the thru-hole hub and the coupler's plate into a nut "
                          "in its trap, cut flush with the nut (fastening rule: the plate is too thin round an insert).")
        else:
            hw.screw(f"scr_hub_coupler_{i + 1}", head, -UP, clamps, (nid, "thread", "insert", None),
                     "s04", "neck", inferred=True, joins=[c[0] for c in clamps] + ["neck_coupler", nid],
                     note="Through the U-joint's pattern mount and the thru-hole hub into the coupler's heat-set "
                          "inserts.")

    # --- servo horns: 1906 hub on the spline, 1916 arm on the hub --------------------------------
    head_side = [p for p in asm.parts if p.link == "head" and not p.linkage]
    head_pts = np.vstack([geom.sample(p.mesh, 2.0, 12000) for p in head_side])
    pick, table = design_linkage(asm, servos, [np.asarray(h) for h in hub_holes], hub_face, head_pts)
    notes.append(f"Push-rod design: {sum(c['pass'] for c in table)} of {len(table)} real-part candidates pass; "
                 f"picked arm hole {pick['r_arm']:g} mm, hub hole {np.round(pick['hole'], 1).tolist()}, horn clocked "
                 f"{pick['phi']:g} deg, ball {pick['ball']} the arm: rod ball centres {pick['L']:g} mm "
                 f"({pick['engage']:g} mm thread each end), leverage >= {pick['lever']:g}, swivel <= {pick['swivel']:g} deg, "
                 f"travel <= {pick['travel']:g} deg, rods clear the head by >= {pick['clear']} mm.")
    asm.linkage_design = {"pick": pick, "candidates": len(table), "passing": sum(c["pass"] for c in table)}
    lks = _linkages(servos, np.asarray(pick["hole"]), hub_face, pick["r_arm"], pick["phi"], pick["ball"])
    for lk in lks:
        lk.inferred = True
        lk.inferred_note = ("Arm hole, hub hole, horn clocking and ball side chosen by design_linkage (every "
                            "real-part combination tested); Hunter's actual choice is not in the sources.")
    asm.linkages = lks
    for lk in lks:
        s = lk.id[-1]
        n, u, w = horn_basis(lk)
        boss = np.asarray(lk.centre)
        # canonical hub/arm: axis +Y, face at y = 0, arm +X  ->  rows: X->u, Y->up, Z->-w
        mh = np.eye(4)
        mh[:3, 0], mh[:3, 1], mh[:3, 2], mh[:3, 3] = u, UP, np.cross(u, UP), boss
        hub = lib_part("gobilda", "1906-0025-0032")
        arm = lib_part("gobilda", "1916-0014-0048")
        hub_m = hub.mesh.copy(); hub_m.apply_transform(mh)
        ma = mh.copy(); ma[:3, 3] = boss + UP * HUB_H
        # cut the arm just past the chosen hole (BOM: "these will need to be cut down")
        arm_c = arm.mesh.slice_plane([lk.radius + 6.0, 0, 0], [-1.0, 0, 0], cap=True)
        arm_m = arm_c.copy(); arm_m.apply_transform(ma)
        hub_p = Part(f"horn_hub_{s}", f"Servo hub 1906, 25T ({s.upper()})", "hardware", "head", hub_m,
                     {"kind": "step", "file": "vendor/parts_cad/gobilda/1906-0025-0032.step", "placement": "mates"},
                     "aluminium", False, (0, 1, 0), 25, 9.0, "catalogue", linkage=lk.id, role="horn",
                     cad=hub.status, catalog="gobilda:1906-0025-0032",
                     features={"spline": spline(boss, UP, TEETH), "servo_face": plane(boss, -UP),
                               "top": plane(boss + UP * HUB_H, UP), "axis": axis(boss, UP, 3.0)})
        arm_p = Part(f"horn_arm_{s}", f"Control arm 1916, 48 mm ({s.upper()})", "hardware", "head", arm_m,
                     {"kind": "step", "file": "vendor/parts_cad/gobilda/1916-0014-0048.step", "placement": "mates",
                      "cut": f"keep the {lk.radius:g} mm hole" + ("" if lk.radius >= 48 else ", cut beyond it")},
                     "nylon", False, (0, 1, 0), 30, 6.0, "catalogue", linkage=lk.id, role="horn",
                     cad=arm.status, catalog="gobilda:1916-0014-0048",
                     features={"bottom": plane(boss + UP * HUB_H, -UP), "bore": axis(boss + UP * HUB_H, UP, 7.0)})
        asm.parts += [hub_p, arm_p]
        P[hub_p.id], P[arm_p.id] = hub_p, arm_p
        hw.mate("spline", (hub_p.id, "spline"), (f"servo_{s}", "spline"), teeth=TEETH,
                clock_deg=pick["phi"], note="fitted at the servo's centre pulse (step s08)")
        hw.mate("seated", (hub_p.id, "servo_face"), (f"servo_{s}", "boss"))
        hw.mate("seated", (arm_p.id, "bottom"), (hub_p.id, "top"))
        hw.mate("concentric", (arm_p.id, "bore"), (hub_p.id, "axis"))
        # 4 pattern screws, arm into the hub's tapped holes (11.3 mm radius, on the arm axes)
        for k in range(4):
            dirk = u * math.cos(k * math.pi / 2) + np.cross(UP, u) * math.sin(k * math.pi / 2)
            top = boss + UP * (HUB_H + ARM_T) + dirk * 11.31
            hw.hole(arm_p.id, f"pat{k}", top, -UP, 2.02)
            hw.hole(hub_p.id, f"pat{k}", boss + UP * HUB_H + dirk * 11.31, -UP, 1.8)
            hw.screw(f"scr_arm_{s}{k + 1}", top, -UP, [(arm_p.id, f"pat{k}")], (hub_p.id, f"hole_pat{k}", "metal", 5.0),
                     "s08", "head", linkage=lk.id, role="horn", joins=[arm_p.id, hub_p.id])
        # ball stud A through the ball, a washer, the arm hole; lock nut on the far side
        n_dir = UP if pick["ball"] == "above" else -UP
        arm_face = boss + UP * (HUB_H + (ARM_T if pick["ball"] == "above" else 0.0)) + u * lk.radius
        hw.hole(arm_p.id, "ball", arm_face, -n_dir, 2.02)
        ball_c = arm_face + n_dir * (SPACER_T + BALL_HALF)
        stud_head = ball_c + n_dir * BALL_HALF
        far = arm_face - n_dir * ARM_T  # the arm's other face
        hw.washer(f"wsh_ball_a_{s}", arm_face + n_dir * SPACER_T, -n_dir, arm_p.id, "ball", "s10", "head",
                  linkage=lk.id, role="horn", spec=SPACER)
        nut_id = hw.nut(f"nut_ball_a_{s}", far, -n_dir, arm_p.id, "s10", "head", linkage=lk.id, role="horn")
        # ball-link parts: posed with the rod (the housing turns about its ball)
        link_a, link_b, rod = _rod_parts(lk, s)
        for p in (link_a, link_b, rod):
            asm.parts.append(p)
            P[p.id] = p
        hw.hole(link_a.id, "ball", stud_head, -n_dir, 2.05)
        hw.screw(f"stud_a_{s}", stud_head, -n_dir, [(link_a.id, "ball"), (arm_p.id, "ball")],
                 (nut_id, "thread", "nut", 8.0), "s10", "head", linkage=lk.id, role="horn",
                 joins=[link_a.id, arm_p.id])
        hw.fast[-1].features["ball_seat"] = ball(ball_c, 4.75)
        hw.mate("ball_link", (link_a.id, "ball_c"), (f"stud_a_{s}", "ball_seat"))
        link_a.features["ball_face"] = plane(ball_c - n_dir * BALL_HALF, -n_dir)
        sp = next(f for f in hw.fast if f.id == f"wsh_ball_a_{s}")
        hw.mate("seated", (link_a.id, "ball_face"), (sp.id, "top"), note="the ball on its spacer")
        # ground side: washer + ball on the top hub, stud into the hub's threaded hole
        b = np.asarray(lk.ground_point)
        face = np.array([b[0], hub_face, b[2]])
        hw.hole("hub_top", f"ball_{s}", face, -UP, 1.8)
        hw.washer(f"wsh_ball_b_{s}", face + UP * SPACER_T, -UP, "hub_top", f"ball_{s}", "s10", "neck", spec=SPACER)
        hw.hole(link_b.id, "ball", b + UP * BALL_HALF, -UP, 2.05)
        hw.screw(f"stud_b_{s}", b + UP * BALL_HALF, -UP, [(link_b.id, "ball")], ("hub_top", f"hole_ball_{s}", "metal", 10.0),
                 "s10", "neck", joins=[link_b.id, "hub_top"], note="Into the threaded sonic hub. Loctite.")
        hw.fast[-1].features["ball_seat"] = ball(b, 4.75)
        hw.mate("ball_link", (link_b.id, "ball_c"), (f"stud_b_{s}", "ball_seat"))
        link_b.features["ball_face"] = plane(b - UP * BALL_HALF, -UP)
        hw.mate("seated", (link_b.id, "ball_face"), (f"wsh_ball_b_{s}", "top"), note="the ball on its spacer")
        # rod into both housings
        hw.mate("threaded", (rod.id, "end_a"), (link_a.id, "thread"), engage_mm=pick["engage"], into="metal",
                hole_depth_mm=BALL_THREAD[1] - BALL_THREAD[0])
        hw.mate("threaded", (rod.id, "end_b"), (link_b.id, "thread"), engage_mm=pick["engage"], into="metal",
                hole_depth_mm=BALL_THREAD[1] - BALL_THREAD[0])
        lk.parts = [hub_p.id, arm_p.id, link_a.id, link_b.id, rod.id]

    # --- gimbal pivots: STEP-placed bearings and cross, verified ---------------------------------
    _pivots(hw, P)
    # --- hub clamps from the STEP, now library screws on the STEP's own axes ----------------------
    clamp_screws(hw, P, getattr(asm, "clamp_leaves", []))
    # no neck joint member (the same solid as the cross): the coupler's side hole takes an M4
    # insert, and on the droid an M4 set screw in it clamps Anderson's neck tube
    if "hole_side_f" in P["neck_coupler"].features:
        hw.insert("ins_coupler_side", "neck_coupler", "side_f", "s02")
    # --- joins the sources make without a fastener (verified contacts) ----------------------------
    for pid in ("hex_shaft", "ujoint"):
        P[pid].features["post_axis"] = axis([0, 0, 0], UP, 4.0)
    hw.mate("concentric", ("hex_shaft", "post_axis"), ("ujoint", "post_axis"), solved=False,
            note="U-joint body on the 8 mm REX post (STEP placement)")
    for hub in ("hub_bottom", "hub_top"):
        P[hub].features["bore"] = axis([0, 0, 0], UP, 4.0)
        hw.mate("concentric", ("hex_shaft", "post_axis"), (hub, "bore"), solved=False)

    contact_mates(hw, P, [("head_bottom", "side_left"), ("head_bottom", "side_right"), ("side_left", "head_top"),
                          ("side_right", "head_top")], "glue", "shell seam: alignment pins + glue (step s12)")
    # the visor drive is designed after the hardware exists (it must clear it); assembly.py adds
    # its parts and calls visor.visor_mates on this Hw
    asm._hw = hw
    asm.fasteners = hw.fast
    asm.mates = hw.mates
    return notes


def _rod_parts(lk, s):
    """Two 2913 ball links and the 2808 rod between the solved ball centres (zero pose)."""
    n, u, w = horn_basis(lk)
    a = np.asarray(lk.centre) + lk.ball_offset * n + lk.radius * u
    b = np.asarray(lk.ground_point)
    d = unit(b - a)

    def placed(p0, zdir):
        # canonical ball link: ball at 0, housing +Z, stud axis Y -> stud vertical
        z = unit(zdir)
        x = unit(np.cross(UP, z))
        y = np.cross(z, x)
        m = np.eye(4)
        m[:3, 0], m[:3, 1], m[:3, 2], m[:3, 3] = x, y, z, p0
        return m

    L = lib_part("gobilda", "2913-0004-0241")
    parts = []
    for tag, p0, zdir in (("a", a, d), ("b", b, -d)):
        m = placed(p0, zdir)
        mesh = L.mesh.copy()
        mesh.apply_transform(m)
        feats = {"ball_c": ball(p0, 4.75), "thread": axis(p0 + unit(zdir) * BALL_THREAD[0], zdir, 1.7)}
        parts.append(Part(f"link_{tag}_{s}", f"Ball linkage 2913, {'horn' if tag == 'a' else 'post'} end ({s.upper()})",
                          "hardware", "head", mesh,
                          {"kind": "step", "file": "vendor/parts_cad/gobilda/2913-0004-0241.step", "placement": "mates"},
                          "steel", False, (0, 1, 0), 25, 5.0, "catalogue", linkage=lk.id, role="rod",
                          cad=L.status, catalog="gobilda:2913-0004-0241", features=feats))
    e = (ROD_L - (lk.rod_length - 2 * BALL_THREAD[1])) / 2
    start = a + d * (BALL_THREAD[1] - e)
    R = lib_part("gobilda", "2808-0004-0050")
    mesh = R.mesh.copy()
    mesh.apply_transform(frame_on_axis(start, d))
    rod = Part(f"rod_{s}", f"Threaded rod 2808, M4 x 50 ({s.upper()})", "hardware", "head", mesh,
               {"kind": "step", "file": "vendor/parts_cad/gobilda/2808-0004-0050.step", "placement": "mates"},
               "stainless", False, (0, 1, 0), 25, 5.0, "catalogue", linkage=lk.id, role="rod",
               cad=R.status, catalog="gobilda:2808-0004-0050",
               features={"end_a": axis(start, d, 2.0), "end_b": axis(start + d * ROD_L, -d, 2.0)})
    return parts[0], parts[1], rod


def _shared_volume(a, b) -> float:
    import manifold3d as mf

    def man(m):
        return mf.Manifold(mf.Mesh(vert_properties=np.asarray(m.vertices, np.float32), tri_verts=np.asarray(m.faces, np.uint32)))
    try:
        return float((man(a) ^ man(b)).volume())
    except Exception:
        return float("inf")


CROSS_WALL = 4.0  # the cross's walls (parts/head/joint_ring.py wall)
NUT_T = {"M4": 3.2}  # ISO 4032
THIN_NUT_T = 2.2     # DIN 439 M4


def _pivots(hw, P):
    """Tilt pivots through the U-joint bearings into the cross; roll pivots through the pillow
    bearings into the cross ends. The bearings and cross are STEP-placed: these mates are verified."""
    cj = P["custom_joint_piece"].mesh
    for s, sx in (("l", 1), ("r", -1)):
        b = P[f"uj_bearing_{s}"].mesh
        c = (b.bounds[0] + b.bounds[1]) / 2
        d = np.array([-sx, 0.0, 0.0])
        outer = np.array([b.bounds[1][0] if sx > 0 else b.bounds[0][0], c[1], c[2]])
        P[f"uj_bearing_{s}"].features.update({"bore": axis(outer, d, 2.0), "outer": axis(outer, d, 5.0)})
        P["ujoint"].features[f"seat_{s}"] = axis(outer, d, 5.0)
        hw.mate("press", (f"uj_bearing_{s}", "outer"), ("ujoint", f"seat_{s}"), solved=False,
                note="STEP placement, verified")
        cj_face = entry_point(cj, c + d * 4.0, d, 1.6, probe=6.0)
        cj_face = cj_face if cj_face is not None else np.array([sx * 10.0, c[1], c[2]])
        hw.hole("custom_joint_piece", f"tilt_{s}", cj_face, d, 2.25)
        wsh_at = cj_face - d * WASHER_T
        hw.fast.append(Fastener(f"wash_tilt_{s}", {"type": "washer", "thread": "M4"}, "washer-M4",
                                ["ujoint", "custom_joint_piece"], "neck", "s04b", frame_on_axis(wsh_at, d),
                                mesh=spec_part("washer", {"type": "washer", "thread": "M4"}).mesh, cad="parametric",
                                features={"bore": axis(wsh_at, d, 2.15), "bottom": plane(cj_face, d)}))
        hw.mate("concentric", (f"wash_tilt_{s}", "bore"), ("custom_joint_piece", f"hole_tilt_{s}"))
        # fastening rule: a pivot carrying shear is a through-bolt with a lock nut (the cross's
        # pivot holes are clearance, `pivot_hole`), the nut on the wall's inner face
        nut_at = cj_face + d * CROSS_WALL
        # a thin nut (DIN 439, 2.2 mm): 2.3 mm between the wall and the hex post's corners; the bolt
        # cut to end flush with it
        nid = hw.nut(f"nut_tilt_{s}", nut_at, d, "custom_joint_piece", "s04b", "cross", reason="pivot", kind="thin_nut")
        L_flush = round(float((nut_at - outer) @ d) + THIN_NUT_T, 1)
        hw.screw(f"pin_tilt_{s}", outer, d, [], (nid, "thread", "nut_thin", THIN_NUT_T), "s04b", "cross", inferred=True,
                 length=L_flush,
                 joins=[f"uj_bearing_{s}", "custom_joint_piece", nid],
                 note="Pivot bolt through the flanged bearing and the cross wall, lock nut inside (shear joint); "
                      "washer spacer (BOM).")
        hw.mate("concentric", (f"pin_tilt_{s}", "shank"), ("custom_joint_piece", f"hole_tilt_{s}"))
        hw.mate("concentric", (f"pin_tilt_{s}", "shank"), (f"uj_bearing_{s}", "bore"), solved=False)
    for s, sz in (("f", 1), ("b", -1)):
        b = P[f"pillow_bearing_{s}"].mesh
        c = (b.bounds[0] + b.bounds[1]) / 2
        d = np.array([0.0, 0.0, -sz])
        outer = np.array([c[0], c[1], b.bounds[1][2] if sz > 0 else b.bounds[0][2]])
        P[f"pillow_bearing_{s}"].features.update({"bore": axis(outer, d, 3.0), "outer": axis(outer, d, 7.5)})
        P[f"pillow_{s}"].features["seat"] = axis(outer, d, 7.5)
        hw.mate("press", (f"pillow_bearing_{s}", "outer"), (f"pillow_{s}", "seat"), solved=False)
        cj_face = entry_point(cj, c + d * 4.0, d, 1.6, probe=6.0)
        cj_face = cj_face if cj_face is not None else np.array([c[0], c[1], sz * 25.0])
        hw.hole("custom_joint_piece", f"roll_{s}", cj_face, d, 2.25)
        nid = hw.nut(f"nut_roll_{s}", cj_face + d * CROSS_WALL, d, "custom_joint_piece", "s05", "cross", reason="pivot")
        # the bolt is clamped in the cross by its nut; the pillow block's bearing turns round it: it rides the cross
        hw.screw(f"pin_roll_{s}", outer, d, [], (nid, "thread", "nut", 8.0), "s05", "cross",
                 inferred=True, joins=[f"pillow_bearing_{s}", "custom_joint_piece", nid],
                 note="Pivot bolt through the pillow-block bearing (6 mm bore) and the cross end, lock nut inside "
                      "(shear joint).")
        hw.mate("concentric", (f"pin_roll_{s}", "shank"), ("custom_joint_piece", f"hole_roll_{s}"))
        hw.mate("concentric", (f"pin_roll_{s}", "shank"), (f"pillow_bearing_{s}", "bore"), solved=False)


def clamp_screws(hw, P, leaves):
    """leaves: [(hub id, head point, direction)] measured from the STEP's 2800 leaves."""
    for i, (hub, head, d) in enumerate(leaves):
        tag = f"clamp_{hub}_{i % 2 + 1}"
        hw.hole(hub, f"clamp{i % 2 + 1}", head, d, 2.2)
        P[hub].features[f"thread_clamp{i % 2 + 1}"] = axis(np.asarray(head) + unit(d) * 5.0, d, 2.0)
        hw.screw(tag, head, d, [(hub, f"clamp{i % 2 + 1}")], (hub, f"thread_clamp{i % 2 + 1}", "metal", 10.0),
                 "s03" if hub == "hub_bottom" else "s06", "neck", catalog="gobilda:2800-0004-0014",
                 joins=[hub, "hex_shaft"], note="Sonic hub clamp screw (the STEP's own, goBILDA 2800).")
        hw.mates[-3].solved = False
        hw.mates[-2].solved = False
        hw.mates[-1].solved = False


def neck_member_pin(hw, P):
    """The neck joint member: an M4 screw self-tapped through the coupler's side hole (3.3 mm, an
    M4 tap drill - measured) into the member's centre hole (also 3.3 mm)."""
    cp = P["neck_coupler"].mesh
    holes = [(c, r) for c, r in holes_at(cp, 2, float(cp.bounds[1][2]) - 1.0, 1.3, 2.0)]
    if not holes:
        return
    c, r = min(holes, key=lambda h: abs(h[0][0]))
    d = np.array([0.0, 0.0, -1.0])
    front = np.array([c[0], c[1], float(cp.bounds[1][2])])
    member_face = np.array([c[0], c[1], float(P["neck_joint_member"].mesh.bounds[1][2])])
    hw.hole("neck_coupler", "side_f", front, d, r)
    hw.hole("neck_joint_member", "pin", member_face, d, 1.65)
    hw.screw("pin_neck_member", front + d * -0.0, d, [], ("neck_joint_member", "hole_pin", "plastic", 20.0), "s02",
             "neck", inferred=True, joins=["neck_coupler", "neck_joint_member"],
             note="Self-tapped through the coupler wall into the member (both holes are M4 tap drill, 3.3 mm).")
    hw.mate("press", ("pin_neck_member", "shank"), ("neck_coupler", "hole_side_f"),
            note="self-tapped through the coupler wall (engagement counted in the member)")


def contact_mates(hw, P, pairs, kind="glue", note=""):
    """Parts the sources join without a fastener (glued shell seams, a servo pressed in its
    pocket): a mate at their closest points, so the suite measures the real gap."""
    from scipy.spatial import cKDTree

    from workbench.geom import sample

    for a, b in pairs:
        A, B = P[a].mesh, P[b].mesh
        pa, pb = sample(A, 1.5, 8000), sample(B, 1.5, 8000)
        d, i = cKDTree(pb).query(pa)
        k = int(np.argmin(d))
        qa, qb = pa[k], pb[i[k]]
        n = unit(qb - qa) if d[k] > 1e-6 else unit(B.centroid - A.centroid)
        P[a].features[f"seam_{b}"] = plane(qa, n)
        P[b].features[f"seam_{a}"] = plane(qb, -n)
        hw.mate(kind, (a, f"seam_{b}"), (b, f"seam_{a}"), solved=False, note=note, gap_mm=round(float(d[k]), 2))
