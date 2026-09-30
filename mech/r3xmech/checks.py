"""Phase A checks: mass properties, joint torque vs servo, interference sweeps.

Assumptions (also written into the results):
* Printed parts: solid density x effective fill (catalog.MATERIALS): kit shells PLA 1.24 g/cc
  x 0.45, mechanism parts PETG 1.27 x 0.65. Rubber gaskets solid 1.2. Purchased parts use the
  catalogue masses (servos, rails, carriages, bearings, tube, extrusion).
* Gravity torque = worst over the joint's own range with its descendants at min/0/max.
* Vertical-axis joints (pan, rings) carry no gravity torque: their load is inertia x the
  profile's a_max plus lazy-susan rolling friction (mu_eff 0.02 x weight x 110 mm race radius).
* Gear efficiency 0.85 (printed spur/sector), linkage 0.90.
* Servo status per motion-control.md section 2: static demand <= 50 % of stall = pass,
  <= 70 % = warn (outside the 30-50 % guidance), above = fail. The speed column checks the
  profile's v_max against 70 % of the loaded speed w0 (1 - tau/tau_stall).
* Interference: fcl on meshes decimated to ~3000 faces (contacts are good to ~1 mm), pairs of
  parts on different links only. Pairs already touching at rest are reported once as fit
  issues (a kit part to modify, or an inferred placement) and excluded from the sweep.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import trimesh

from . import catalog, frames
from .meshes import part_mesh_local
from .model import MECH, Asm

G = 9.80665
PROFILE = json.loads((MECH.parent / "profiles/r3x/robot.json").read_text())
PJ = {j["name"]: j for j in PROFILE["joints"]}
ETA_GEAR, ETA_LINK = 0.85, 0.90
MU_SUSAN, R_SUSAN = 0.02, 0.110

# joint -> (servo key, how joint torque maps to servo torque)
DRIVES = {
    "head_pan": ("SERVO_35KG_270", "gear"), "head_lift": ("SERVO_60KG_270", "rack"),
    "head_tilt": ("SERVO_35KG_270", "gear"), "visor": ("SERVO_35KG_270", "link"),
    "torso_lower": ("SERVO_35KG_270", "gear"), "torso_top": ("SERVO_35KG_270", "gear"),
    "hero_shoulder": ("DS3218_DUAL", "direct"), "hero_wrist": ("SERVO_7KG", "direct"),
}


# ------------------------------------------------------------------ mass
def part_mass(p):
    """(grams, com_local, note)."""
    m = part_mesh_local(p)
    try:
        mp = m.mass_properties
        vol = abs(mp.volume) / 1000.0  # cc
        com = np.asarray(mp.center_mass)
    except Exception:
        vol, com = abs(m.volume) / 1000.0, m.centroid
    if not np.all(np.isfinite(com)) or vol <= 0:
        com = m.bounds.mean(0)
    if p.mass_g is not None:
        return p.mass_g, com, "catalogue"
    mat = catalog.MATERIALS.get(p.material)
    if mat is None:
        mat = catalog.MATERIALS["aluminium"]
    fill = p.infill if p.infill is not None else mat["fill"]
    return vol * mat["density"] * fill, com, f"{p.material} {mat['density']} g/cc x {fill:.2f}"


def mass_table(root: Asm, parts):
    rows = {}
    for p in parts:
        g, com, note = part_mass(p)
        rows[p.id] = dict(link=p.link, g=g, com=frames.apply(p.T, com), note=note)
    return rows


def link_masses(rows):
    L = {}
    for r in rows.values():
        d = L.setdefault(r["link"], dict(g=0.0, m=np.zeros(3)))
        d["g"] += r["g"]
        d["m"] += r["g"] * r["com"]
    return {k: dict(g=v["g"], com=v["m"] / v["g"] if v["g"] else np.zeros(3)) for k, v in L.items()}


def subtree_points(root, rows, jid, pose):
    links = root.subtree_links(jid)
    pts = []
    for r in rows.values():
        if r["link"] in links:
            T = root.link_T(r["link"], pose)
            pts.append((r["g"] / 1000.0, frames.apply(T, r["com"])))
    return pts


def torque_about(pts, pivot, axis):
    axis = np.asarray(axis) / np.linalg.norm(axis)
    tau, inertia = 0.0, 0.0
    F = np.array([0, -G, 0])
    for m, c in pts:
        r = c - pivot
        tau += axis @ np.cross(r, m * F)
        rp = r - axis * (r @ axis)
        inertia += m * (rp @ rp)
    return tau, inertia / 1e6  # N.mm -> keep; inertia kg.mm^2 -> kg.m^2


def descendants(root, jid):
    links = root.subtree_links(jid)
    return [j for j in root.all_joints().values() if j.parent_link in links and j.id != jid and j.type != "fixed"
            and j.limits[0] != j.limits[1]]


def torque_checks(root: Asm, rows):
    J = root.all_joints()
    out = []
    for jid, (skey, kind) in DRIVES.items():
        if jid not in J:
            continue
        j = J[jid]
        pj = PJ.get(j.profile_joint or jid, {})
        lo, hi = j.limits
        worst, worst_pose, inert = 0.0, {}, 0.0
        desc = descendants(root, jid)[:3]
        combos = [{}]
        for d in desc:
            combos = [dict(c, **{d.id: v}) for c in combos for v in (d.limits[0], 0.0, d.limits[1])]
        for q in np.linspace(lo, hi, 13):
            for c in combos:
                pose = dict(c, **{jid: q})
                pts = subtree_points(root, rows, jid, pose)
                T = root.link_T(j.parent_link, pose)
                piv = frames.apply(T, j.pivot)
                ax = T[:3, :3] @ np.asarray(j.axis, float)
                if j.type == "prismatic":
                    tau = sum(m for m, _ in pts) * G  # N
                    I = sum(m for m, _ in pts)
                else:
                    tau, I = torque_about(pts, piv, ax)
                    tau = abs(tau) / 1000.0  # N.m
                if tau >= worst:
                    worst, worst_pose, inert = tau, pose, I
        mass = sum(m for m, _ in subtree_points(root, rows, jid, {}))
        a_max = pj.get("a_max", 300.0)
        v_max = pj.get("v_max", 60.0)
        stall = catalog.stall_kgcm(skey) * catalog.KGCM_TO_NM
        w0 = 60.0 / catalog.speed_s60(skey)  # deg/s no load
        drive = j.drive
        if j.type == "prismatic":
            r_p = drive.get("pinion_pitch_radius_mm", 27.2) / 1000.0
            F_dyn = mass * a_max / 1000.0
            fric = 2 * 2.0  # N, two MGN12 carriages (preload drag)
            F = worst + F_dyn + fric
            tau_servo = F * r_p / ETA_GEAR
            servo_speed_needed = v_max / (drive.get("mm_per_servo_deg", 0.475))
            detail = dict(load_N=round(worst, 1), dyn_N=round(F_dyn, 2), friction_N=fric)
        else:
            alpha = math.radians(a_max)
            tau_dyn = inert * alpha
            fric = 0.0
            if abs(np.asarray(j.axis) @ np.array([0, 1, 0])) > 0.99:  # vertical axis
                fric = MU_SUSAN * mass * G * R_SUSAN
            ratio = drive.get("servo_deg_per_joint_deg", 1.0) or 1.0
            if kind == "link":
                ratio = 1.0 / (drive.get("gear_ratio") or 1.0)
            eta = ETA_LINK if kind == "link" else (ETA_GEAR if kind == "gear" else 1.0)
            tau_j = worst + tau_dyn + fric
            tau_servo = tau_j / ratio / eta
            servo_speed_needed = v_max * ratio
            detail = dict(gravity_Nm=round(worst, 3), inertia_kgm2=round(inert, 5), dyn_Nm=round(tau_dyn, 4),
                          friction_Nm=round(fric, 3), ratio=round(ratio, 3), eta=eta)
        frac = tau_servo / stall
        loaded = w0 * max(0.0, 1 - frac)
        status = "pass" if frac <= 0.5 else ("warn" if frac <= 0.7 else "fail")
        speed_ok = servo_speed_needed <= 0.7 * loaded
        out.append(dict(joint=jid, servo=skey, subtree_kg=round(mass, 3), worst_pose={k: round(float(v), 1) for k, v in worst_pose.items()},
                        servo_torque_Nm=round(tau_servo, 3), servo_torque_kgcm=round(tau_servo / catalog.KGCM_TO_NM, 2),
                        stall_kgcm=round(catalog.stall_kgcm(skey), 1), fraction_of_stall=round(frac, 3),
                        status=status, margin_vs_70pct=round(0.7 * stall / tau_servo, 2) if tau_servo > 0 else None,
                        servo_speed_needed_dps=round(servo_speed_needed, 1), loaded_speed_dps=round(loaded, 1),
                        speed_ok=bool(speed_ok), **detail))
    return out


# ------------------------------------------------------------------ interference
_DEC = {}


def dec_mesh(p, faces=3000):
    if p.id not in _DEC:
        m = part_mesh_local(p)
        if len(m.faces) > faces:
            try:
                m = m.simplify_quadric_decimation(face_count=faces)
            except Exception:
                pass
        _DEC[p.id] = m
    return _DEC[p.id]


# intended contacts: gears in mesh, shafts in bores, parts bolted face to face across a joint
INTENDED = [
    ("pan_pinion", "pan_sector"), ("lift_pinion", "lift_rack"), ("tilt_pinion", "tilt_center_gear"),
    ("lower_pinion", "lower_sector"), ("top_pinion", "top_sector"),
    ("neck_tube", "*"), ("lift_carriage", "lift_rail"), ("neck_guide_carriage", "neck_guide_rail"),
    ("neck_guide_ring_inner", "neck_guide_ring_outer"), ("pan_turntable", "stage_ring"),
    ("neck_guide_rail", "neck_guide_ring_inner"), ("neck_guide_carriage", "neck_guide_platform"),
    ("pan_turntable", "pan_sector"), ("visor_rod", "visor_bearing_*"), ("visor_rod", "visor_frame"),
    ("neck_yoke", "head_plate"), ("tilt_center_gear", "head_plate"), ("visor_push_rod", "visor_horn"),
    ("visor_push_rod", "visor_tab"), ("hero_shoulder_servo", "hero_forearm"), ("hero_hand_arm_side", "hero_hand_finger_side"),
    ("hero_elbow_tube", "hero_forearm"), ("neck_clamp_*", "*"), ("pan_turntable", "morton_lower_frame"),
]


def _match(pat, s):
    return pat == "*" or pat == s or (pat.endswith("*") and s.startswith(pat[:-1]))


def intended(a, b):
    return any((_match(x, a) and _match(y, b)) or (_match(x, b) and _match(y, a)) for x, y in INTENDED)


def _manager(parts, root, pose):
    cm = trimesh.collision.CollisionManager()
    Tl = {}
    for p in parts:
        if p.link not in Tl:
            Tl[p.link] = root.link_T(p.link, pose)
        cm.add_object(p.id, dec_mesh(p), transform=Tl[p.link] @ p.T)
    return cm


def rest_overlaps(root, parts):
    link = {p.id: p.link for p in parts}
    origin = {p.id: p.origin for p in parts}
    cm = _manager(parts, root, {})
    hit, names = cm.in_collision_internal(return_names=True)
    out = []
    byid = {p.id: p for p in parts}
    for a, b in sorted(names):
        if link[a] == link[b] or intended(a, b):
            continue
        if origin[a] == "kit" and origin[b] == "kit":
            continue  # the kit's own nesting across links is not a mechanism finding
        if _full_hit(root, byid[a], byid[b], {}):
            out.append((a, b))
    return out


NO_SWEEP = ("neck_guide", "neck_clamp_upper")   # upper neck guide: geometry inferred, no sweep claims


def sweep(root, parts, jid, rest_pairs, step=None, lo=None, hi=None):
    J = root.all_joints()[jid]
    parts = [p for p in parts if not p.id.startswith(NO_SWEEP)]
    moving_links = root.subtree_links(jid)
    mov = [p for p in parts if p.link in moving_links]
    sta = [p for p in parts if p.link not in moving_links]
    skip = {tuple(sorted(x)) for x in rest_pairs}
    cm_s = _manager(sta, root, {})
    step = step or (1.0 if J.type != "prismatic" else 1.0)
    byid = {p.id: p for p in parts}
    near = {}
    res = {}
    for sgn, end in ((-1, lo), (1, hi)):
        q = 0.0
        first = None
        while (q + step * sgn) * sgn <= end * sgn + 1e-9:
            q += step * sgn
            cm_m = _manager(mov, root, {jid: q})
            hit, names = cm_m.in_collision_other(cm_s, return_names=True)
            new = [tuple(sorted(n)) for n in names if tuple(sorted(n)) not in skip and not intended(*n)]
            if new:
                conf = [n for n in sorted(set(new)) if _full_hit(root, byid[n[0]], byid[n[1]], {jid: q})]
                for n in set(new) - set(conf):
                    near.setdefault(n, round(q, 2))
                    skip.add(n)
                if conf:
                    first = (round(q, 2), conf[:6])
                    break
        res["min" if sgn < 0 else "max"] = first
    res["near_misses_under_1mm"] = [[a, b, q] for (a, b), q in near.items()]
    return res


def _full_hit(root, pa, pb, pose):
    cm = trimesh.collision.CollisionManager()
    for p in (pa, pb):
        cm.add_object(p.id, part_mesh_local(p), transform=root.link_T(p.link, pose) @ p.T)
    return cm.in_collision_internal()


def interference_checks(root, parts, quick=False):
    rest = rest_overlaps(root, parts)
    J = root.all_joints()
    out = {"rest_overlaps": rest, "sweeps": {}}
    for jid in DRIVES:
        if jid not in J:
            continue
        j = J[jid]
        pj = PJ.get(j.profile_joint or jid, {})
        prof = pj.get("hard", {"min": j.limits[0], "max": j.limits[1]})
        lo = min(j.limits[0], prof["min"])
        hi = max(j.limits[1], prof["max"])
        step = (2.0 if quick else 1.0)
        r = sweep(root, parts, jid, rest, step=step, lo=lo, hi=hi)
        r.update(swept=[lo, hi], derived_limits=list(j.limits), profile_hard=[prof["min"], prof["max"]],
                 profile_animation=[pj.get("animation", {}).get("min"), pj.get("animation", {}).get("max")])
        out["sweeps"][jid] = r
        print(f"  sweep {jid}: {r['min']} | {r['max']}", flush=True)
    return out


def run_checks(root, parts, quick=False):
    rows = mass_table(root, parts)
    L = link_masses(rows)
    total = sum(r["g"] for r in rows.values())
    print(f"mass: total {total / 1000:.2f} kg")
    for k, v in sorted(L.items(), key=lambda kv: -kv[1]["g"]):
        print(f"  {k:18s} {v['g'] / 1000:6.3f} kg  com {np.round(v['com'], 0)}")
    tq = torque_checks(root, rows)
    for t in tq:
        print(f"  torque {t['joint']:14s} {t['servo']:15s} need {t['servo_torque_kgcm']:6.2f} kg.cm of "
              f"{t['stall_kgcm']:5.1f} ({t['fraction_of_stall'] * 100:5.1f} %) {t['status']:4s} "
              f"speed need {t['servo_speed_needed_dps']:6.1f} vs loaded {t['loaded_speed_dps']:6.1f} deg/s")
    itf = interference_checks(root, parts, quick=quick)
    return dict(assumptions=__doc__, total_kg=total / 1000,
                links={k: dict(kg=v["g"] / 1000, com=v["com"].tolist()) for k, v in L.items()},
                parts={k: dict(link=v["link"], g=round(v["g"], 1), note=v["note"]) for k, v in rows.items()},
                torque=tq, interference=itf)
