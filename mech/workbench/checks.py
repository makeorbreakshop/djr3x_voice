"""Checks run on every build: linkage reach, interference per joint (first contact), shell
clearance across every joint range, and static torque margin per servo.

Distances come from dense surface samples (KD-trees), not booleans, so non-closed meshes
(most printed shells) work. Contact = two parts closer than CONTACT_MM. Pairs already that
close at the zero pose are mated (a bearing in its bore) and are excluded - they are listed
in the check's assumptions so nothing is hidden.
"""

from __future__ import annotations

import itertools
import math

import numpy as np
from scipy.spatial import cKDTree

from .geom import sample
from .kinematics import link_matrices, servo_jacobian, solve_linkages, apply
from .model import Assembly, Check

CONTACT_MM = 1.0
SPACING_MM = 1.5
SWEEP_BEYOND = 25.0  # degrees past each limit to look for the first contact
G = 9.81


class Geo:
    """Sampled points of each rigid part at the zero pose (== its link frame), with a KD-tree
    and bounds, so a pair's distance at any pose is one query of the smaller set."""

    def __init__(self, asm: Assembly):
        self.asm = asm
        self.pts = {}
        self.trees = {}
        self.box = {}
        for p in asm.parts:
            if p.linkage:
                continue
            pts = sample(p.mesh, SPACING_MM)
            self.pts[p.id] = pts
            self.box[p.id] = (pts.min(0), pts.max(0))

    def tree(self, pid):
        if pid not in self.trees:
            self.trees[pid] = cKDTree(self.pts[pid])
        return self.trees[pid]


def subtree_links(asm: Assembly, link: str) -> set[str]:
    out = {link}
    changed = True
    while changed:
        changed = False
        for j in asm.joints:
            if j.parent_link in out and j.child_link not in out:
                out.add(j.child_link)
                changed = True
    return out


def path_joints(asm: Assembly, la: str, lb: str) -> set[str]:
    """Joints whose value changes the relative pose of two links."""
    return set(asm.link_chain(la)) ^ set(asm.link_chain(lb))


def _box_corners(lo, hi):
    return np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])


def pair_distance(geo: Geo, a: str, ma: np.ndarray, b: str, mb: np.ndarray, far: float = 30.0) -> float:
    """Min distance between parts a and b placed by their link matrices (`far` if clearly apart)."""
    if len(geo.pts[a]) > len(geo.pts[b]):
        a, ma, b, mb = b, mb, a, ma
    rel = np.linalg.inv(mb) @ ma
    c = _box_corners(*geo.box[a]) @ rel[:3, :3].T + rel[:3, 3]
    lo, hi = geo.box[b]
    gap = np.max(np.maximum(c.min(0) - hi, lo - c.max(0)))
    if gap > far:
        return far
    pts = geo.pts[a]
    # coarse pass on every 8th point: if even that is far, the pair is (conservatively) apart
    coarse = pts[::8] @ rel[:3, :3].T + rel[:3, 3]
    d, _ = geo.tree(b).query(coarse, k=1, distance_upper_bound=far, workers=-1)
    dc = float(d.min())
    if dc > 12.0:
        return min(dc - 6.0, far)
    pa = pts @ rel[:3, :3].T + rel[:3, 3]
    d, _ = geo.tree(b).query(pa, k=1, distance_upper_bound=far, workers=-1)
    return float(min(d.min(), far))


def interference(asm: Assembly, geo: Geo, explained: list | None = None) -> list[Check]:
    """First contact per joint. Pairs an EXPLAINED entry of test "clearance" covers (the suite's
    own explanations: a known near-miss with its fix) are left out and named in the summary."""
    from fnmatch import fnmatch

    def known(a, b):
        return next((e for e in explained or [] if e.get("test") in ("clearance", "*") and
                     all(any(fnmatch(x, pat) for pat in e["parts"]) for x in (a, b))), None)

    out = []
    rigid = [p for p in asm.parts if not p.linkage]
    ms0 = link_matrices(asm, {})
    for j in asm.joints:
        pairs, mated, skipped = [], [], []
        for a, b in itertools.combinations(rigid, 2):
            if j.id not in path_joints(asm, a.link, b.link):
                continue
            if known(a.id, b.id):
                skipped.append(f"{a.id}/{b.id}")
                continue
            if pair_distance(geo, a.id, ms0[a.link], b.id, ms0[b.link]) < CONTACT_MM:
                mated.append(f"{a.id}/{b.id}")
            else:
                pairs.append((a, b))
        lo, hi = j.limits

        def contact(v):
            ms = link_matrices(asm, {j.id: v})
            for a, b in pairs:
                if pair_distance(geo, a.id, ms[a.link], b.id, ms[b.link]) < CONTACT_MM:
                    return a.id, b.id
            return None

        first = {}
        for sign, lim in ((1, hi), (-1, lo)):
            v, prev, hit = 0.0, 0.0, None
            while abs(v) < abs(lim) + SWEEP_BEYOND:
                v = min(abs(v) + 2.0, abs(lim) + SWEEP_BEYOND) * sign
                if contact(v):
                    fine = prev
                    while abs(fine) < abs(v):  # refine the last interval at 0.5 deg
                        fine += 0.5 * sign
                        who = contact(fine)
                        if who:
                            hit = (fine, *who)
                            break
                    break
                prev = v
            first[sign] = hit
        parts, grades, notes = [], [], []
        for sign, lim in ((1, hi), (-1, lo)):
            h = first[sign]
            if h is None:
                notes.append(f"clear to {lim + sign * SWEEP_BEYOND:+g}")
                continue
            v, a, b = h
            parts += [a, b]
            inside = (v <= lim) if sign > 0 else (v >= lim)
            margin = abs(v) - abs(lim)
            grades.append("fail" if inside else "warn" if margin < 3 else "pass")
            notes.append(f"{v:+g} {a} x {b} " + ("(inside the limit)" if inside else f"({margin:g} past the limit)"))
        status = "fail" if "fail" in grades else "warn" if "warn" in grades else "pass"
        hits = [h for h in first.values() if h]
        worst = min(hits, key=lambda h: abs(h[0]) - abs(hi if h[0] > 0 else lo), default=None)
        out.append(Check(
            f"interference_{j.id}", "interference", status, f"{j.id}: first contact",
            f"range {lo:+g}..{hi:+g} {j.unit}; first contact " + "; ".join(notes)
            + (f"; {len(skipped)} explained near-miss pairs left out" if skipped else ""),
            joint=j.id, value=worst[0] if worst else None,
            pose={j.id: worst[0]} if worst else {j.id: hi},
            parts=sorted(set(parts)),
            assumptions=[f"contact = parts within {CONTACT_MM} mm (surface samples every {SPACING_MM} mm), "
                         "2 deg sweep refined to 0.5 deg",
                         "other joints at 0; fasteners and push-rod parts not included",
                         f"mated at zero, excluded: {', '.join(mated) or 'none'}"]))
    return out


def grid(asm: Assembly, joints: list[str], step=5.0):
    axes = []
    for jid in joints:
        j = asm.joint(jid)
        lo, hi = j.limits
        vals = sorted(set(np.round(np.append(np.arange(lo, hi + 1e-9, step), [lo, hi, 0.0]), 3)))
        axes.append(vals)
    for combo in itertools.product(*axes):
        yield dict(zip(joints, combo))


def shell_clearance(asm: Assembly, geo: Geo) -> Check:
    """Every shell part vs every mechanism part on another link (and the push rods), over a
    5 deg grid of the joints between them, within their limits."""
    shell = [p for p in asm.parts if p.cls == "shell" and not p.linkage]
    mech = [p for p in asm.parts if p.cls != "shell" and not p.linkage]
    results = []
    for s in shell:
        for m in mech:
            js = sorted(path_joints(asm, s.link, m.link))
            if not js:
                continue
            best = (math.inf, None)
            for pose in grid(asm, js):
                ms = link_matrices(asm, pose)
                d = pair_distance(geo, m.id, ms[m.link], s.id, ms[s.link])
                if d < best[0]:
                    best = (d, pose)
            results.append((best[0], best[1], s.id, m.id))
        for lk in asm.linkages:
            js = sorted({j.id for j in asm.joints if lk.id in (j.drive or {}).get("linkages", [])})
            best = (math.inf, None)
            for pose in grid(asm, js):
                ms = link_matrices(asm, pose)
                r = solve_linkages(asm, pose)[lk.id]
                if r is None:
                    continue
                _, a, b = r
                seg = a + np.linspace(0, 1, 40)[:, None] * (b - a)
                inv = np.linalg.inv(ms[s.link])
                d, _ = geo.tree(s.id).query(seg @ inv[:3, :3].T + inv[:3, 3], k=1)
                d = float(d.min()) - 4.6  # ball-link body radius
                if d < best[0]:
                    best = (d, pose)
            results.append((best[0], best[1], s.id, lk.id))
    results.sort(key=lambda r: r[0])
    d, pose, s, m = results[0]
    status = "fail" if d < CONTACT_MM else "warn" if d < 3.0 else "pass"
    fmt = lambda p: ", ".join(f"{k} {v:+g}" for k, v in (p or {}).items()) or "zero pose"
    near = "; ".join(f"{m2} to {s2} {d2:.1f} mm" for d2, _, s2, m2 in results[1:4])
    return Check("shell_clearance", "clearance", status, "Shell clearance, all joints",
                 f"closest: {m} to {s}, {d:.1f} mm at {fmt(pose)}. Next: {near}",
                 value=round(d, 2), pose=pose or {}, parts=[s, m],
                 assumptions=["each shell/mechanism pair swept over a 5 deg grid of the joints between them, "
                              "within the limits",
                              "rods as 9.2 mm (ball-link body) cylinders between the ball centres",
                              "the neck tube itself is not modelled (only the coupler on it)",
                              "warn under 3 mm: print tolerance and shell flex"])


def reach(asm: Assembly) -> Check:
    joints = sorted({j for lk in asm.linkages for j in _joints_of(asm, lk)})
    worst = {lk.id: 0.0 for lk in asm.linkages}
    fail = None
    for pose in grid(asm, joints, 2.5):
        sol = solve_linkages(asm, pose)
        for lid, r in sol.items():
            if r is None:
                fail = fail or (lid, pose)
                continue
            worst[lid] = max(worst[lid], abs(r[0]))
    rng = min(abs(lk.servo_range[0]) for lk in asm.linkages) if asm.linkages else 0
    status = "fail" if fail else "warn" if max(worst.values(), default=0) > rng else "pass"
    summary = (f"{fail[0]} cannot reach " + ", ".join(f"{k} {v:+g}" for k, v in fail[1].items()) if fail else
               "; ".join(f"{k}: servo within +/-{v:.1f} deg" for k, v in worst.items()) + f" (servo range +/-{rng:g})")
    return Check("reach_push_rods", "reach", status, "Push rods reach the whole range", summary,
                 pose=fail[1] if fail else {}, parts=[lk.servo for lk in asm.linkages],
                 assumptions=["closed-form rod solve on a 2.5 deg grid of every joint the rods drive"])


def authority(asm: Assembly, min_gain: float = 0.25) -> list[Check]:
    """Push-rod leverage along each driven joint (others at 0): where d(servo)/d(joint) of the
    best rod falls under `min_gain`, the servos can no longer move (or hold) that joint - a
    linkage singularity or fold-over."""
    out = []
    for j in asm.joints:
        lks = (j.drive or {}).get("linkages") or []
        if not lks:
            continue
        lo, hi = j.limits
        worst = None
        weak = []
        v = lo
        while v <= hi + 1e-9:
            jac = servo_jacobian(asm, {j.id: v}, [j.id])
            if jac is not None:
                g = float(np.max(np.abs(jac[:, 0])))
                if worst is None or g < worst[1]:
                    worst = (v, g)
                if g < min_gain:
                    weak.append(v)
            v += 1.0
        if worst is None:
            continue
        status = "fail" if weak else "warn" if worst[1] < 2 * min_gain else "pass"
        span = f"{min(weak):+g}..{max(weak):+g}" if weak else ""
        out.append(Check(
            f"authority_{j.id}", "reach", status, f"{j.id}: push-rod leverage",
            (f"leverage lost over {span} (servo moves < {min_gain} deg per joint deg); " if weak else "")
            + f"weakest {worst[1]:.2f} servo deg per joint deg at {worst[0]:+g}",
            joint=j.id, value=round(worst[1], 3), pose={j.id: worst[0]}, parts=[asm.linkages[0].servo] if asm.linkages else [],
            assumptions=["other joints at 0", "gain = the larger of the rods' d(servo)/d(joint), 1 deg steps",
                         "a fold-over means two joint angles share one servo angle: the joint cannot be held there"]))
    return out


def _joints_of(asm, lk):
    return [j.id for j in asm.joints if lk.id in (j.drive or {}).get("linkages", [])]


def torque(asm: Assembly, servo: dict, profile_amax: dict, extra_loads: dict) -> list[Check]:
    """Static (+ profile a_max) torque on each servo over each driven joint's range."""
    out = []
    parts = [p for p in asm.parts if p.mass_g]
    ms0 = link_matrices(asm, {})
    working = servo["stall_kgcm"] * 0.0980665 * 0.5  # N m, 50 % of stall
    mass_note = sorted({p.mass_note for p in parts if p.mass_note})

    def load(pose, jids):
        """Joint torques (N m) the servos must hold, gravity + inertia at a_max."""
        ms = link_matrices(asm, pose)
        tau = []
        for jid in jids:
            j = asm.joint(jid)
            sub = subtree_links(asm, j.child_link)
            m_j = ms[j.parent_link]
            piv = apply(m_j, j.pivot)
            ax = m_j[:3, :3] @ np.asarray(j.axis, float)
            t_g, inertia = 0.0, 0.0
            items = [(p.mass_g, apply(ms[p.link], p.mesh.centroid)) for p in parts if p.link in sub]
            items += [(g, apply(ms[lnk], pt)) for lnk, pt, g in extra_loads.values() if lnk in sub]
            for m_g, c in items:
                r = (c - piv) / 1000.0
                f = np.array([0, -m_g / 1000.0 * G, 0])
                t_g += float(np.cross(r, f) @ ax)
                r_perp = r - ax * (r @ ax)
                inertia += m_g / 1000.0 * float(r_perp @ r_perp)
            alpha = math.radians(profile_amax.get(j.profile_joint or jid, profile_amax.get("_default", 0)))
            tau.append((t_g, inertia * alpha))
        return tau

    # push-rod pair: tau_servo = J^-T tau_joint
    paired = {}
    for j in asm.joints:
        if j.drive.get("kind") == "push_rod_pair":
            paired.setdefault(tuple(j.drive["linkages"]), []).append(j.id)
    for links, jids in paired.items():
        worst = {lk: (0.0, 0.0, None) for lk in links}
        skipped = 0
        for pose in grid(asm, jids, 5.0):
            jac = servo_jacobian(asm, pose, jids)
            ids = [lk.id for lk in asm.linkages]
            if jac is not None:  # only this pair's rows (other linkages, e.g. a visor rod, ride along)
                jac = jac[[ids.index(lk) for lk in links], :]
            # where the rods lose leverage the torque is unbounded: the authority check owns that
            if jac is None or np.min(np.linalg.svd(jac, compute_uv=False)) < 0.25:
                skipped += 1
                continue
            ld = load(pose, jids)
            for use_dyn in (False, True):
                tq = np.array([g + (math.copysign(d, g) if use_dyn else 0) for g, d in ld])
                ts = np.linalg.solve(jac.T, tq)
                for k, lk in enumerate(links):
                    v = abs(float(ts[k]))
                    s, d, p = worst[lk]
                    if not use_dyn and v > s:
                        worst[lk] = (v, d, pose)
                    if use_dyn and v > d:
                        worst[lk] = (worst[lk][0], v, worst[lk][2])
        for lk_id in links:
            s, d, pose = worst[lk_id]
            lk = next(l for l in asm.linkages if l.id == lk_id)
            margin = working / s if s else math.inf
            margin_d = working / d if d else math.inf
            status = "fail" if margin_d < 1 or margin < 1 else "warn" if margin < 2 else "pass"
            out.append(Check(
                f"torque_{lk.servo}", "torque", status, f"{lk.servo}: torque margin",
                f"static peak {s * 10.197:.1f} kg-cm -> margin {margin:.1f}x of the {working * 10.197:.1f} kg-cm working "
                f"torque; with profile a_max {d * 10.197:.1f} kg-cm ({margin_d:.1f}x)",
                value=round(margin, 2), pose=pose or {}, parts=[lk.servo],
                assumptions=[f"servo {servo['model']}: {servo['stall_kgcm']} kg-cm stall at {servo['volts']} V; "
                             "working torque = 50 % of stall", *mass_note,
                             "load shared through the rod Jacobian (J^-T); rod and ball-joint friction ignored",
                             f"{skipped} grid poses excluded where the rods lose leverage (see the leverage checks)",
                             "inertia from part masses at their centroids (a lower bound)"]))
    for j in asm.joints:
        if j.drive.get("kind") != "direct":
            continue
        peak_s, peak_d, at = 0.0, 0.0, None
        for pose in grid(asm, [j.id], 2.5):
            g, dyn = load(pose, [j.id])[0]
            ratio = j.drive.get("gear_ratio") or 1.0
            if abs(g) * ratio > peak_s:
                peak_s, at = abs(g) * ratio, pose
            peak_d = max(peak_d, (abs(g) + dyn) * ratio)
        margin = working / peak_s if peak_s else math.inf
        margin_d = working / peak_d if peak_d else math.inf
        status = "fail" if margin_d < 1 else "warn" if margin < 2 else "pass"
        out.append(Check(
            f"torque_{j.drive['servos'][0]}", "torque", status, f"{j.drive['servos'][0]}: torque margin",
            f"static peak {peak_s * 10.197:.2f} kg-cm -> margin {margin:.0f}x; with a_max {margin_d:.0f}x",
            joint=j.id, value=round(margin, 2), pose=at or {}, parts=list(j.drive["servos"]),
            assumptions=[f"same servo class as the gimbal ({servo['stall_kgcm']} kg-cm stall), working = 50 %",
                         *[f"extra load: {k} {g:g} g" for k, (_, _, g) in extra_loads.items()],
                         *mass_note]))
    return out


def mass_summary(asm: Assembly) -> Check:
    parts = [p for p in asm.parts if p.mass_g]
    total = sum(p.mass_g for p in parts)
    ms0 = link_matrices(asm, {})
    moving = subtree_links(asm, asm.joints[0].child_link) if asm.joints else set()
    mv = [p for p in parts if p.link in moving]
    m = sum(p.mass_g for p in mv)
    cog = sum(p.mass_g * apply(ms0[p.link], p.mesh.centroid) for p in mv) / max(m, 1e-9)
    return Check("mass", "torque", "pass", "Mass and centre of gravity",
                 f"total {total:.0f} g; moving head {m:.0f} g, CoG {np.round(cog, 1).tolist()} mm from the pivot",
                 value=round(total, 1), parts=[],
                 assumptions=sorted({p.mass_note for p in parts if p.mass_note}))


def run_all(asm: Assembly, servo: dict | None = None, profile_amax: dict | None = None,
            extra_loads: dict | None = None, explained: list | None = None) -> list[Check]:
    geo = Geo(asm)
    checks = []
    if asm.linkages:
        checks.append(reach(asm))
        checks += authority(asm)
    checks += interference(asm, geo, explained)
    if any(p.cls == "shell" for p in asm.parts):
        checks.append(shell_clearance(asm, geo))
    if servo:
        checks += torque(asm, servo, profile_amax or {}, extra_loads or {})
    checks.append(mass_summary(asm))
    return checks
