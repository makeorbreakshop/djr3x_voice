"""How the shells hang on the column, modelled (not implied): the three 10 in lazy susans as their two
races, and every screw of the shell -> race -> carrier chain as a placed fastener with its mates.

    from assemblies.kit.fastening import fasten
    fasten(root)          # the droid's workbench tree (kit build()), after to_workbench

The chain, per the kit's guide (pages in the notes; its transcription is in assemblies/kit/_guide):

* lower ring: LS_M_Full <- 8 standoffs (6-32 button heads from below, the guide's) <- LS_SR_1 (8 flat
  heads into the standoffs) <- the lower lazy susan's OUTER race (4 x 10-32 x 1/2 flat heads down into
  LS_SR_1, 2 x 10-32 x 2 through LS_PA_M_1); its INNER race is clamped by LS_IC_1 (4 x 10-32 x 1) to the
  column's core plate (column internals) or to P_M_3 (Anderson's);
* middle ring: MS_Main_Full <- MS_SR_1 <- the middle lazy susan's OUTER race (6 x 10-32 x 3/4 flat heads
  from below into MS_Main's inserts); its INNER race on LS_IC_1's pillars (4 x 10-32 x 1/2 into their
  inserts);
* top ring: TR_RR_Full <- TR_SR_Full <- the top lazy susan's OUTER race (6 x 10-32 x 1/2 from below into
  TR_SR_Full); its INNER race on TR-MR_SC's flange, screwed on into the column's top-ring plate;
* pedestal cap P_M_3: 6 x M4 x 8 socket heads with washers down into P_M_1/P_M_2's inserts (each P_M_3
  variant has its own);
* base top: 4 x M4 button heads through B_T into heat-set inserts in the column's shell support ring
  (ours: the kit's B_T has no holes there; the column carries it).

Hole positions are the prints' (read from the meshes: the spacer rings', LS_IC_1's, TR-MR_SC's, P_M_3's);
the races' section is not stated by the kit: a 10 mm stack (the gap the kit leaves between the parts that
clamp them) split as two rings, drawn 0.1 mm clear of their seats (inferred). Every placed screw is `inferred` where the guide does not fix
its exact hole; each carries the guide's McMaster number.
"""

from __future__ import annotations

import numpy as np
import trimesh

from workbench.mates import Mate, axis, frame_on_axis, plane
from workbench.model import Assembly, Fastener, Part, Step

INCH = 25.4
R_IN = (98.5, 111.0)       # inner race: LS_IC_1's footprint (r 98.5-110.3) and on to the ball track
R_OUT = (111.4, 124.6)     # outer race: the spacer rings' footprint (r 111.8-124.6)
D10_32, D6_32 = 4.83, 3.51

# per lazy susan: (assembly, link) of each race, its y range, its screw holes (r, angles)
RACES = {
    "lower": dict(inner=("lower_ring", "lower_ring_mount", (342.2, 352.0), 104.4, [1.1 + 90 * k for k in range(4)]),
                  outer=("lower_ring", "lower_ring", (342.2, 352.0), 118.3, [60.0 * k for k in range(6)]),
                  step_in="lower_ring_s03", step_out="lower_ring_s04"),
    # the middle race's inner ring is bolted to LS_IC_1's pillars: it lives with LS_IC_1 (lower_ring_mount)
    "middle": dict(inner=("lower_ring", "lower_ring_mount", (407.3, 415.2), 104.4, [46.1 + 90 * k for k in range(4)]),
                   outer=("middle_ring", "middle_ring", (409.2, 417.1), 118.3, [28.7 + 60 * k for k in range(6)]),
                   step_in=None, step_out="middle_ring_s02"),
    "top": dict(inner=("top_ring", "top_ring_mount", (480.2, 488.0), 104.4, [78.0 + 90 * k for k in range(4)]),
                outer=("top_ring", "top_ring", (482.0, 489.9), 118.3, [28.3 + 60 * k for k in range(6)]),
                step_in="top_ring_s05", step_out="top_ring_s04"),
}


def at(r, deg, y):
    a = np.radians(deg)
    return np.array([r * np.sin(a), y, r * np.cos(a)])


def _ring(r0, r1, y0, y1, holes, hole_r):
    import manifold3d as mf

    def cyl(r, h, x=0.0, z=0.0, ylo=0.0):
        c = mf.Manifold.cylinder(h, r, r, 96 if r > 20 else 20)
        return c.rotate([-90, 0, 0]).translate([x, ylo, z])

    body = cyl(r1, y1 - y0, ylo=y0) - cyl(r0, y1 - y0 + 2, ylo=y0 - 1)
    for p in holes:
        body = body - cyl(hole_r, y1 - y0 + 2, p[0], p[2], y0 - 1)
    m = body.to_mesh()
    return trimesh.Trimesh(np.asarray(m.vert_properties)[:, :3], np.asarray(m.tri_verts), process=False)


def _index(root: Assembly) -> dict[str, Assembly]:
    out = {}

    def walk(a):
        out.setdefault(a.id, a)
        for c in a.children:
            if isinstance(c, Assembly):
                walk(c)

    walk(root)
    return out


def _step(asm: Assembly, sid: str | None) -> Step:
    st = next((s for s in asm.steps if s.id == sid), None) if sid else None
    return st


def _drop_line(asm: Assembly, key: str, sid: str | None = None):
    """Take a guide hardware line out of a step's `unplaced` (it is placed here)."""
    for s in asm.steps:
        if sid and s.id != sid:
            continue
        s.unplaced = [u for u in s.unplaced if u.get("key") != key]


class _Hw:
    def __init__(self, asm: Assembly):
        self.a = asm
        self.n = sum(1 for m in asm.mates if m.id.startswith("ksh"))

    def feat(self, pid, name, f):
        part = next((p for p in self.a.parts if p.id == pid), None)
        if part is not None:
            part.features[name] = f

    def mate(self, t, a, b, note="", **params):
        self.n += 1
        self.a.mates.append(Mate(f"ksh{self.n:03d}_{t}", t, a, b, params, True, note))

    def put(self, fid, spec, key, head, d, joins, link, step, note, clamps=(), into=None, into_kind="plastic",
            engage=None, inferred=True, mesh=None):
        """A piece of hardware, head at `head`, along d; `clamps` [(part, hole feature)], `into` (part, feature)."""
        from workbench.kitgeom import hardware_mesh

        d = np.asarray(d, float) / np.linalg.norm(d)
        head = np.asarray(head, float)
        sd = spec.get("d_mm", 4.0)
        # the head's bearing face looks along d, into the face it seats on (a seated mate wants opposed normals)
        feats = {"shank": axis(head, d, sd / 2), "head": plane(head, d)}
        f = Fastener(fid, dict(spec), key, list(joins), link, step.id, frame_on_axis(head, d), inferred,
                     note, mesh=mesh if mesh is not None else hardware_mesh(spec), cad="placeholder", features=feats)
        self.a.fasteners.append(f)
        step.fasteners.append(fid)
        for k, (pid, hn) in enumerate(clamps):
            self.mate("concentric", (fid, "shank"), (pid, f"hole_{hn}"))
            if k == 0:
                self.mate("seated", (fid, "head"), (pid, f"face_{hn}"))
        if into is not None:
            self.mate("threaded", (fid, "shank"), into, into=into_kind,
                      engage_mm=round(float(engage if engage is not None else spec.get("length_mm", 0)), 2))
        return f


def _hole(hw: _Hw, pid, name, p, d, r, depth=None):
    p = np.asarray(p, float)
    hw.feat(pid, f"hole_{name}", dict(axis(p, d, r), **({"depth": depth} if depth else {})))
    hw.feat(pid, f"face_{name}", plane(p, -np.asarray(d, float)))


def _races(A: dict[str, Assembly]):
    """The six races as parts, each with its screw holes as features."""
    out = {}
    for key, R in RACES.items():
        for side in ("inner", "outer"):
            aid, link, (y0, y1), rh, angs = R[side]
            a = A[aid]
            r0, r1 = R_IN if side == "inner" else R_OUT
            holes = [at(rh, g, y0) for g in angs]
            mesh = _ring(r0, r1, y0, y1, holes, 2.45)
            pid = f"ls_race_{key}_{side}"
            feats = {"top": plane((0, y1, (r0 + r1) / 2), (0, 1, 0)), "bottom": plane((0, y0, (r0 + r1) / 2), (0, -1, 0))}
            for i, g in enumerate(angs):
                feats[f"hole_h{i + 1}"] = axis(at(rh, g, y1), (0, -1, 0), 2.45)
                feats[f"face_h{i + 1}"] = plane(at(rh, g, y1), (0, 1, 0))
                feats[f"hole_u{i + 1}"] = axis(at(rh, g, y0), (0, 1, 0), 2.45)
                feats[f"face_u{i + 1}"] = plane(at(rh, g, y0), (0, -1, 0))
            name = {"lower": "Lower", "middle": "Middle", "top": "Top"}[key]
            p = Part(pid, f"{name} lazy susan (10 in): {side} race", "bearing", link, mesh,
                     {"kind": "generated", "placement": "between the parts that clamp it (kit guide)",
                      "evidence": "kit guide p20 (lower), p33-34 (middle), p51-52 (top): 'lazy_susan_10in'"},
                     "steel (zinc plated)", False, (0, -1, 0) if side == "inner" else (0, 1, 0), 30.0, 250.0,
                     "inferred (a 10 in ring turntable ~0.5 kg, half per race)", cad="placeholder",
                     inferred=True, inferred_note="the races' section is not in the kit: a 10 mm stack (the gap the kit "
                                                  "leaves) split into two rings; holes drilled to the kit's screw circles",
                     note=("static: on the column's side" if side == "inner" else "turns with its ring"),
                     features=feats)
            a.parts.append(p)
            out[(key, side)] = p
    return out


def fasten(root: Assembly, internals: str = "column"):
    A = _index(root)
    if not all(k in A for k in ("lower_ring", "middle_ring", "top_ring", "base")):
        return
    # the kit's assemblies stay placement models for the connected test (their shells are not mated) under the
    # mates modelled here (workbench/droid.py)
    for aid in ("base", "lower_ring", "middle_ring", "top_ring", "pedestal_cap_kit", "pedestal_cap_column"):
        if aid in A:
            A[aid].mount = dict(A[aid].mount or {}, placement_model=True)
    races = _races(A)
    LR, MR, TR = A["lower_ring"], A["middle_ring"], A["top_ring"]
    # races into the guide's steps that fit them (the guide names the lazy susan there)
    for (key, side), p in races.items():
        sid = RACES[key]["step_in" if side == "inner" else "step_out"]
        a = A[RACES[key][side][0]]
        st = _step(a, sid)
        if st is None:
            st = Step(f"{a.id}_ls_{key}", f"{key.title()} lazy susan's inner race onto LS_IC_1's pillars (kit guide p33-34)",
                      [], [], notes=["The middle ring's race: its inner ring screwed down to LS_IC_1's pillar inserts from "
                                     "inside MS_Main_Full (4 x 10-32 x 1/2 flat heads); its outer ring goes with the middle "
                                     "ring (middle_ring_s02)."], guide_page=34)
            a.steps.append(st)
        st.parts.append(p.id)
    fhcs10 = lambda L: {"type": "fhcs", "thread": "10-32", "d_mm": D10_32, "length_mm": round(L, 2)}  # noqa: E731

    # -- lower ring: outer race -> LS_SR_1 (4 x 1/2, 2 x 2 in through LS_PA_M_1), LS_SR_1 -> 8 standoffs
    hw = _Hw(LR)
    st4, st2 = _step(LR, "lower_ring_s04"), _step(LR, "lower_ring_s02")
    for i, g in enumerate(RACES["lower"]["outer"][4]):
        long = g in (60.0, 120.0)
        y_sr = 342.1
        _hole(hw, "ls_sr_1", f"race{i + 1}", at(118.3, g, y_sr), (0, -1, 0), 2.02, depth=3.2)
        if long:
            _hole(hw, "ls_pa_m_1", f"race{i + 1}", at(118.3, g, 390.2), (0, -1, 0), 2.49)
            head, L, mc, clamps = at(118.3, g, 390.2), 2 * INCH, "91500A360", [("ls_pa_m_1", f"race{i + 1}"),
                                                                              ("ls_race_lower_outer", f"h{i + 1}")]
        else:
            head, L, mc, clamps = at(118.3, g, 352.1), INCH / 2, "91500A829", [("ls_race_lower_outer", f"h{i + 1}")]
        hw.put(f"ksh_lower_race_out_{i + 1}", fhcs10(L), f"mcmaster-{mc}", head, (0, -1, 0),
               [c[0] for c in clamps] + ["ls_sr_1"], "lower_ring", st4,
               "kit guide p20: through the lazy susan's outer race into LS_SR_1" + (" (and LS_PA_M_1 above)" if long else ""),
               clamps=clamps, into=("ls_sr_1", f"hole_race{i + 1}"), engage=342.1 - (head[1] - L))
    for mc in ("mcmaster-91500A829", "mcmaster-91500A360"):
        _drop_line(LR, mc, "lower_ring_s04")
    so_spec = {"type": "standoff", "thread": "6-32", "d_mm": 6.35, "length_mm": round(7 / 16 * INCH, 2)}
    for i, g in enumerate([15.0, 75.0, 105.0, 165.0, 195.0, 255.0, 285.0, 345.0]):
        top = at(115.9, g, 338.9)
        _hole(hw, "ls_sr_1", f"so{i + 1}", at(115.9, g, 342.1), (0, -1, 0), 1.83)
        sid = f"ksh_lower_standoff_{i + 1}"
        # a female standoff: bored through at the 6-32 tap drill (#36, 2.7 mm), the way the suite models a thread
        so_mesh = trimesh.creation.annulus(r_min=1.35, r_max=3.175, height=so_spec["length_mm"], sections=16)
        so_mesh.apply_translation([0, 0, so_spec["length_mm"] / 2])
        f = hw.put(sid, so_spec, "mcmaster-93330A516", top, (0, -1, 0), ["ls_sr_1", "ls_m_full"], "lower_ring", st2,
                   "kit guide p19: standoffs in LS_M_Full's recesses, LS_SR_1 on top", mesh=so_mesh)
        f.features["thread_top"] = dict(axis(top, (0, -1, 0), 1.75), depth=5.0)
        f.features["top"] = plane(top, (0, 1, 0))
        hw.feat("ls_sr_1", f"so_under{i + 1}", plane(top, (0, -1, 0)))
        hw.mate("seated", (sid, "top"), ("ls_sr_1", f"so_under{i + 1}"), note="LS_SR_1 on the standoff")
        hw.put(f"ksh_lower_sr_{i + 1}", {"type": "fhcs", "thread": "6-32", "d_mm": D6_32, "length_mm": 6.35},
               "mcmaster-91771A144", at(115.9, g, 342.1), (0, -1, 0), ["ls_sr_1", sid], "lower_ring", st2,
               "kit guide p19: LS_SR_1 screwed down onto the standoff below it", clamps=[("ls_sr_1", f"so{i + 1}")],
               into=(sid, "thread_top"), into_kind="metal", engage=3.15)
    for mc in ("mcmaster-93330A516", "mcmaster-91771A144"):
        _drop_line(LR, mc, "lower_ring_s02")
    # -- the lower inner race: the kit's four 1 in screws through LS_IC_1 and the race (into the column's core
    #    plate: the column's col_scr_rpcore_race; into P_M_3 with Anderson's internals: placed with that P_M_3)
    _drop_line(LR, "mcmaster-91500A833")
    _drop_line(LR, "mcmaster-93365A154")
    # -- the middle inner race on LS_IC_1's pillars (4 x 10-32 x 1/2 into the pillars' inserts)
    st_mi = _step(LR, "lower_ring_ls_middle")
    for i, g in enumerate(RACES["middle"]["inner"][4]):
        _hole(hw, "ls_ic_1", f"mid{i + 1}", at(104.4, g, 407.2), (0, -1, 0), 2.42, depth=5.7)
        hw.put(f"ksh_middle_race_in_{i + 1}", fhcs10(INCH / 2), "mcmaster-91500A829", at(104.4, g, 415.2), (0, -1, 0),
               ["ls_race_middle_inner", "ls_ic_1"], "lower_ring_mount", st_mi,
               "kit guide p34: driven from within the middle ring, the inner race onto LS_IC_1's pillar inserts",
               clamps=[("ls_race_middle_inner", f"h{i + 1}")], into=("ls_ic_1", f"hole_mid{i + 1}"), into_kind="insert",
               engage=4.7)
    _drop_line(MR, "mcmaster-91500A829", "middle_ring_s04")
    st = _step(MR, "middle_ring_s04")
    if st is not None:
        st.notes.append("The four screws into LS_IC_1's inserts are placed with LS_IC_1 (lower ring: "
                        "'Middle lazy susan's inner race onto LS_IC_1's pillars').")
    # -- middle outer race: 6 x 10-32 x 3/4 from below through the race and MS_SR_1 into MS_Main's inserts
    hw = _Hw(MR)
    st_mo = _step(MR, "middle_ring_s02")
    for i, g in enumerate(RACES["middle"]["outer"][4]):
        _hole(hw, "ms_sr_1", f"race{i + 1}", at(118.3, g, 417.2), (0, 1, 0), 2.38)
        _hole(hw, "ms_main_full", f"race{i + 1}", at(118.3, g, 420.4), (0, 1, 0), 2.4, depth=5.7)
        hw.put(f"ksh_middle_race_out_{i + 1}", fhcs10(0.75 * INCH), "mcmaster-91500A831", at(118.3, g, 409.2), (0, 1, 0),
               ["ls_race_middle_outer", "ms_sr_1", "ms_main_full"], "middle_ring", st_mo,
               "kit guide p33: from below, the outer race and MS_SR_1 onto MS_Main_Full's inserts",
               clamps=[("ls_race_middle_outer", f"u{i + 1}"), ("ms_sr_1", f"race{i + 1}")],
               into=("ms_main_full", f"hole_race{i + 1}"), into_kind="insert", engage=0.75 * INCH - 11.2)
    _drop_line(MR, "mcmaster-91500A831", "middle_ring_s02")
    # -- top outer race: 6 x 10-32 x 1/2 from below through the race into TR_SR_Full
    hw = _Hw(TR)
    st_to = _step(TR, "top_ring_s04")
    for i, g in enumerate(RACES["top"]["outer"][4]):
        _hole(hw, "tr_sr_full", f"race{i + 1}", at(118.3, g, 490.0), (0, 1, 0), 2.02, depth=3.2)
        hw.put(f"ksh_top_race_out_{i + 1}", fhcs10(INCH / 2), "mcmaster-91500A829", at(118.3, g, 482.0), (0, 1, 0),
               ["ls_race_top_outer", "tr_sr_full"], "top_ring", st_to,
               "kit guide p52: through the turntable's race into TR_SR_Full's holes",
               clamps=[("ls_race_top_outer", f"u{i + 1}")], into=("tr_sr_full", f"hole_race{i + 1}"), engage=3.2)
    _drop_line(TR, "mcmaster-91500A829", "top_ring_s04")
    st = _step(TR, "top_ring_s05")
    if st is not None:
        st.notes.append("Column internals: three of the inner race's four screws are the column's (col_scr_rptop_race: on "
                        "through TR-MR_SC's flange into the top-ring plate); the fourth, over the plate's open back, and all "
                        "four with Anderson's internals, go into TR-MR_SC as the guide has them (not placed).")
    # -- P_M_3 to the pedestal: 6 x M4 x 8 socket heads + washers, per P_M_3 variant; Anderson's P_M_3 also takes
    #    the lower inner race's four 1 in screws
    _drop_line(A["base"], "mcmaster-91292A108")
    _drop_line(LR, "mcmaster-91292A108")  # the guide's p20 line is the same six, "counted in base list"
    _drop_line(A["base"], "mcmaster-96505A112")
    for cid in ("pedestal_cap_kit", "pedestal_cap_column"):
        a = A.get(cid)
        if a is None:
            continue
        hw = _Hw(a)
        st = a.steps[0] if a.steps else None
        if st is None:
            st = Step(f"{cid}_s01", "Pedestal cap P_M_3", ["p_m_3"])
            a.steps.append(st)
        for i, g in enumerate([45.0 + 60 * k for k in range(6)]):
            _hole(hw, "p_m_3", f"ped{i + 1}", at(88.9, g, 325.0), (0, -1, 0), 2.0)
            w = hw.put(f"ksh_{cid}_wsh_{i + 1}", {"type": "washer", "thread": "M4", "d_mm": 8.0, "length_mm": 0.8},
                       "mcmaster-96505A112", at(88.9, g, 325.8), (0, -1, 0), ["p_m_3"], "pedestal_cap", st,
                       "kit guide p11: under the screw's head",
                       mesh=trimesh.creation.annulus(r_min=2.1, r_max=4.0, height=0.8).apply_translation([0, 0, 0.4]))
            # the washer bears on P_M_3 with its underside; the screw's head bears on the washer's top
            w.features["head"] = plane(at(88.9, g, 325.0), (0, -1, 0))
            w.features["face_w"] = plane(at(88.9, g, 325.8), (0, 1, 0))
            w.features["hole_w"] = axis(at(88.9, g, 325.8), (0, -1, 0), 2.1)
            hw.mate("seated", (w.id, "head"), ("p_m_3", f"face_ped{i + 1}"))
            hw.put(f"ksh_{cid}_scr_{i + 1}", {"type": "shcs", "thread": "M4", "d_mm": 4.0, "length_mm": 8.0},
                   "mcmaster-91292A108", at(88.9, g, 325.8), (0, -1, 0), ["p_m_3", "p_m_1" if g >= 180 else "p_m_2"],
                   "pedestal_cap", st, "kit guide p11: P_M_3 down onto P_M_1/P_M_2, into their rim's heat-set inserts; "
                   "its head 6.3 mm under the column's core plate",
                   clamps=[(w.id, "w"), ("p_m_3", f"ped{i + 1}")])
        if cid == "pedestal_cap_kit":
            for i, g in enumerate(RACES["lower"]["inner"][4]):
                hw.put(f"ksh_lower_race_in_{i + 1}", fhcs10(INCH), "mcmaster-91500A833", at(104.4, g, 361.1), (0, -1, 0),
                       ["ls_ic_1", "ls_race_lower_inner", "p_m_3"], "pedestal_cap", st,
                       "kit guide p20: through LS_IC_1's recess and the lazy susan's inner race into P_M_3's insert "
                       "(the model's P_M_3 has its inserts 44 deg round from LS_IC_1's holes: the kit's exports are "
                       "clocked differently; placed on LS_IC_1's holes)")
    return races
