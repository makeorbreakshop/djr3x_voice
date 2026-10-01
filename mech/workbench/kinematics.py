"""Forward kinematics of an assembly's link tree, and the closed-form push-rod solve.

The panel (sim/web/src/workbench/kinematics.ts) implements the same two functions; keep them in
step. Everything is in the assembly frame, mm and degrees, joint pivots/axes at zero pose.
"""

from __future__ import annotations

import math

import numpy as np

from .model import Assembly, Linkage


def rot(axis, deg) -> np.ndarray:
    a = np.asarray(axis, float)
    a = a / np.linalg.norm(a)
    t = math.radians(deg)
    c, s = math.cos(t), math.sin(t)
    x, y, z = a
    r = np.array([
        [c + x * x * (1 - c), x * y * (1 - c) - z * s, x * z * (1 - c) + y * s],
        [y * x * (1 - c) + z * s, c + y * y * (1 - c), y * z * (1 - c) - x * s],
        [z * x * (1 - c) - y * s, z * y * (1 - c) + x * s, c + z * z * (1 - c)],
    ])
    m = np.eye(4)
    m[:3, :3] = r
    return m


def trans(t) -> np.ndarray:
    m = np.eye(4)
    m[:3, 3] = t
    return m


def joint_matrix(j, value: float) -> np.ndarray:
    if j.type == "revolute":
        return trans(j.pivot) @ rot(j.axis, value) @ trans(-np.asarray(j.pivot, float))
    if j.type == "prismatic":
        a = np.asarray(j.axis, float)
        return trans(a / np.linalg.norm(a) * value)
    return np.eye(4)


def link_matrices(asm: Assembly, pose: dict) -> dict[str, np.ndarray]:
    """World (assembly frame) matrix of every link for a pose {joint: value}."""
    out: dict[str, np.ndarray] = {}
    by = {l.id: l for l in asm.links}

    def get(lid):
        if lid in out:
            return out[lid]
        l = by[lid]
        if not l.joint:
            m = np.eye(4)
        else:
            j = asm.joint(l.joint)
            m = get(j.parent_link) @ joint_matrix(j, pose.get(j.id, 0.0))
        out[lid] = m
        return m

    for lid in by:
        get(lid)
    return out


def apply(m: np.ndarray, p) -> np.ndarray:
    return (m @ np.append(np.asarray(p, float), 1.0))[:3]


def apply_dir(m: np.ndarray, d) -> np.ndarray:
    return m[:3, :3] @ np.asarray(d, float)


def horn_basis(lk: Linkage):
    n = np.asarray(lk.axis, float)
    n /= np.linalg.norm(n)
    u = np.asarray(lk.zero_dir, float)
    u = u - n * (u @ n)
    u /= np.linalg.norm(u)
    w = np.cross(n, u)
    return n, u, w


def solve_rod(lk: Linkage, horn_m: np.ndarray, ground_m: np.ndarray):
    """Servo angle (deg, from the horn's zero direction, right-hand about the horn axis) that
    keeps the rod at its length, plus the two ball centres. None when unreachable."""
    n, u, w = horn_basis(lk)
    c = apply(horn_m, lk.centre)
    n2, u2, w2 = apply_dir(horn_m, n), apply_dir(horn_m, u), apply_dir(horn_m, w)
    b = apply(ground_m, lk.ground_point)
    d = c + lk.ball_offset * n2 - b
    r, big_l = lk.radius, lk.rod_length
    a_ = 2 * r * (d @ u2)
    b_ = 2 * r * (d @ w2)
    c_ = big_l * big_l - d @ d - r * r
    h = math.hypot(a_, b_)
    if h < 1e-9 or abs(c_) > h:
        return None
    base = math.atan2(b_, a_)
    off = math.acos(c_ / h)
    cands = [base + off, base - off]
    th = min(cands, key=lambda t: abs(math.remainder(t, 2 * math.pi)))
    th = math.remainder(th, 2 * math.pi)
    a_pt = c + lk.ball_offset * n2 + r * (math.cos(th) * u2 + math.sin(th) * w2)
    return math.degrees(th), a_pt, b


def solve_linkages(asm: Assembly, pose: dict):
    """{linkage id: (servo deg, ball A, ball B) | None} for a pose."""
    ms = link_matrices(asm, pose)
    return {lk.id: solve_rod(lk, ms[lk.horn_link], ms[lk.ground_link]) for lk in asm.linkages}


def servo_jacobian(asm: Assembly, pose: dict, joints: list[str], eps: float = 0.25):
    """d(servo deg)/d(joint deg), rows = linkages, cols = joints (central difference)."""
    ids = [lk.id for lk in asm.linkages]
    jac = np.zeros((len(ids), len(joints)))
    for k, jid in enumerate(joints):
        hi = solve_linkages(asm, {**pose, jid: pose.get(jid, 0) + eps})
        lo = solve_linkages(asm, {**pose, jid: pose.get(jid, 0) - eps})
        for i, lid in enumerate(ids):
            if hi[lid] is None or lo[lid] is None:
                return None
            jac[i, k] = math.remainder(math.radians(hi[lid][0] - lo[lid][0]), 2 * math.pi) / math.radians(2 * eps)
    return jac


def gear_matrix(g, link_m: np.ndarray, joint_value: float) -> np.ndarray:
    """A gear's parts' matrix (SCHEMA.md "Gear"): its link's, turned about its own axle by
    deg_per_unit x the joint's value."""
    p = np.asarray(g.pivot, float)
    return link_m @ trans(p) @ rot(g.axis, g.deg_per_unit * joint_value) @ trans(-p)


def gear_matrices(asm: Assembly, pose: dict, joint_values: dict | None = None) -> dict[str, np.ndarray]:
    """{part or fastener id: matrix} for every gear of `asm` at a pose. A gear whose joint is another
    assembly's (`joint_assembly`) reads `joint_values[(assembly, joint)]`, else 0."""
    ms = link_matrices(asm, pose)
    out = {}
    for g in asm.gears:
        v = pose.get(g.joint, 0.0) if not g.joint_assembly else (joint_values or {}).get((g.joint_assembly, g.joint), 0.0)
        m = gear_matrix(g, ms[g.link], v)
        for pid in list(g.parts) + list(g.fasteners):
            out[pid] = m
    return out
