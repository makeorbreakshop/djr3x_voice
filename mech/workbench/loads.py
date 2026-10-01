"""Beef-up candidates: where the droid's load path is most used, ranked, from the numbers the mech
model already has (profiles/r3x/robot.generated.json `mech`: link masses, COMs and inertias from
the CAD, the joint tree, drive ratios, servo stall torques; the profile's a_max).

    cd mech && .venv/bin/python -m workbench.loads      # -> out/r3x_droid/beef_up.md

Each row is a utilisation (demand / capacity) with how it was computed; nothing is changed. Not
modelled: friction (lazy susans, MGN rails, gear mesh losses beyond eta), impacts, fatigue.

* servos: worst holding torque over the joint's own range (the rest of the droid at rest) plus
  the inertia term at the profile's a_max, through the drive ratio and its efficiency, as a share
  of stall (the 70 % rule marks > 0.7);
* gear teeth: that servo torque on the pinion's pitch radius, one tooth as a cantilever at its
  root (height from the model, root width from the model or estimated from the circular pitch),
  against a printed-PETG bending allowable;
* the neck tube: bending at the upper neck guide from the head's weight at its worst tilt/roll
  offset plus the head's tilt inertia at a_max, against 6061 yield;
* thin walls on moving links: the suite's thinnest printed walls, where the part carries load.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

MECH = Path(__file__).resolve().parents[1]
PROFILE = MECH.parent / "profiles" / "r3x" / "robot.generated.json"
G = 9.81
PRINT_ALLOW_MPA = 25.0   # printed PETG/PLA tooth-root bending allowable (flexural ~60-70 MPa, /2.5 for layers)
AL_YIELD_MPA = 240.0     # 6061-T6
RULE = 0.7

# pinion tooth geometry from the parametric models (parts/anderson), mm
GEARS = {
    "pan_servo": {"part": "pan_gear (15T)", "pitch_r": 24.0, "root_w": 6.69, "height": 8.0, "face": 10.0, "src": "model"},
    "lift_servo": {"part": "lift_gear (19T) on the rack", "pitch_r": 27.5, "root_w": 5.0, "height": 5.0, "face": 12.0,
                   "src": "model"},
    "top_servo": {"part": "ring_servo_gear (20T)", "pitch_r": 25.8, "root_w": 4.85, "height": 5.6, "face": 10.0,
                  "src": "root width estimated (0.6 x circular pitch)"},
    "lower_servo": {"part": "ring_servo_gear (25T)", "pitch_r": 30.4, "root_w": 4.6, "height": 4.8, "face": 10.0,
                    "src": "root width estimated (0.6 x circular pitch)"},
}
# the central column's (assemblies/column): the pan pinion 25T m2 (printed involute, 8 mm face), Anderson's
# ring pinions on the column's servos; the lift is a GT2 belt (no tooth row: belt tension, see the drive)
GEARS.update({
    "col_pan_servo": {"part": "pan gear 25T m2 (column)", "pitch_r": 25.0, "root_w": 3.6, "height": 4.5, "face": 8.0,
                      "src": "involute m2: root width ~1.15 m, height 2.25 m"},
    "col_top_servo": dict(GEARS["top_servo"]),
    "col_lower_servo": dict(GEARS["lower_servo"]),
})
TUBE = {"od": 26.0, "wall": 1.5, "guide_y": 616.0}  # Anderson's neck tube; the upper guide's clamp height
# the column's short neck: cantilevered from the pan hub's top (the cross bolt, y 494; the upper bearing at 469..476)
TUBE_COLUMN = {"od": 26.0, "wall": 1.5, "guide_y": 476.0}


def _rot(axis, deg):
    a = np.asarray(axis, float) / np.linalg.norm(axis)
    t = math.radians(deg)
    K = np.array([[0, -a[2], a[1]], [a[2], 0, -a[0]], [-a[1], a[0], 0]])
    return np.eye(3) + math.sin(t) * K + (1 - math.cos(t)) * K @ K


def analyse(profile: dict) -> list[dict]:
    mech = profile["mech"]
    joints = mech["joints"]
    amax = {j["name"]: j.get("a_max") or 0.0 for j in profile["joints"]}
    links = [l for l in mech["links"] if l.get("com")]
    children = {}
    for name, j in joints.items():
        children.setdefault(j["parent"], []).append(name)

    def subtree(jn):
        out, stack = [], [jn]
        while stack:
            k = stack.pop()
            out.append(k)
            stack += children.get(k, [])
        return set(out)

    rows = []
    drives = {d["servo"]: d for d in mech["drives"]}
    for sname, d in drives.items():
        for jn in d["joints"]:
            j = joints[jn]
            sub = subtree(jn)
            ls = [l for l in links if l["joint"] in sub]
            if not ls:
                continue
            m = sum(l["kg"] for l in ls)
            piv = np.asarray(j["pivot"], float)
            ax = np.asarray(j["axis"], float)
            ax /= np.linalg.norm(ax)
            lo, hi = j["mech_limits"]["min"], j["mech_limits"]["max"]
            if j["type"] == "prismatic":
                a = amax.get(jn, 0.0) / 1000.0  # mm/s^2 -> m/s^2
                F = m * (G + a) * max(0.0, float(ax @ [0, 1, 0]))
                load = F * (d["mm_per_servo_deg"] or 1) * 180 / math.pi / 1000.0 / d["eta"]
                how = f"{m:.2f} kg lifted at (g + {a:.1f} m/s2): {F:.1f} N through {d['mm_per_servo_deg']} mm/servo deg"
            else:
                worst = 0.0
                for q in np.linspace(lo, hi, 25):
                    R = _rot(ax, q)
                    t = 0.0
                    for l in ls:
                        c = R @ (np.asarray(l["com"], float) - piv) / 1000.0
                        t += float(np.cross(c, [0, -l["kg"] * G, 0]) @ ax)
                    worst = max(worst, abs(t))
                I = 0.0
                for l in ls:
                    Ic = np.asarray(l["inertia"], float)
                    Imat = np.array([[Ic[0], Ic[3], Ic[4]], [Ic[3], Ic[1], Ic[5]], [Ic[4], Ic[5], Ic[2]]])
                    r = (np.asarray(l["com"], float) - piv) / 1000.0
                    rp = r - ax * (r @ ax)
                    I += float(ax @ Imat @ ax) + l["kg"] * float(rp @ rp)
                alpha = math.radians(amax.get(jn, 0.0))
                tau = worst + I * alpha
                if d["kind"] in ("push_rod_pair",):
                    J = [abs(x) for x in (j["drive"].get("jacobian_at_rest") or [1.0])]
                    load = tau / (sum(J)) / d["eta"]
                    how = f"{tau:.2f} N m at the joint ({worst:.2f} gravity + {I:.4f} kg m2 x {amax.get(jn, 0):g} deg/s2), shared by the pair (J {J})"
                else:
                    load = tau / (d["servo_deg_per_unit"] or 1.0) / d["eta"]
                    how = f"{tau:.2f} N m at the joint ({worst:.2f} gravity + {I:.4f} kg m2 x {amax.get(jn, 0):g} deg/s2) / {d['servo_deg_per_unit']} / eta {d['eta']}"
            u = load / d["stall_nm"] if d["stall_nm"] else float("nan")
            rows.append({"what": f"servo {sname} ({d['model']}) on {jn}", "kind": "servo torque", "util": u,
                         "value": f"{load:.2f} of {d['stall_nm']:.2f} N m stall", "how": how})
            g = GEARS.get(sname)
            if g:
                Ft = load / (g["pitch_r"] / 1000.0)  # N at the pitch circle (servo side, worst case)
                sigma = 6 * Ft * g["height"] / (g["face"] * g["root_w"] ** 2)
                rows.append({"what": f"gear tooth: {g['part']}", "kind": "tooth root bending", "util": sigma / PRINT_ALLOW_MPA,
                             "value": f"{sigma:.1f} of {PRINT_ALLOW_MPA:g} MPa",
                             "how": f"{Ft:.0f} N on one tooth at r {g['pitch_r']} mm; root {g['root_w']} x face {g['face']} mm, "
                                    f"height {g['height']} mm ({g['src']})"})
    # the neck tube: head (everything past head_lift that rides the tube) on the upper guide
    head = [l for l in links if l["joint"] in subtree("head_tilt") | {"head_lift"} and l["id"].startswith("hunter_head")]
    if head:
        m = sum(l["kg"] for l in head)
        com = sum(np.asarray(l["com"], float) * l["kg"] for l in head) / m
        piv = np.asarray(joints["head_tilt"]["pivot"], float)
        h = (com - piv)[1]
        off = max(math.hypot(*(com - piv)[[0, 2]]), h * math.sin(math.radians(25)))  # worst tilt 25 deg
        tube = TUBE_COLUMN if any(d.get("servo") == "col_pan_servo" for d in mech["drives"]) else TUBE
        lever = (piv[1] + h * math.cos(math.radians(25)) - tube["guide_y"]) / 1000.0
        I = sum(l["kg"] * ((np.asarray(l["com"]) - piv)[1] / 1000.0) ** 2 for l in head)
        M = m * G * off / 1000.0 + I * math.radians(amax.get("head_tilt", 0.0)) + m * 0.0 * lever
        tube = TUBE_COLUMN if any(d.get("servo") == "col_pan_servo" for d in mech["drives"]) else TUBE
        D, t = tube["od"], tube["wall"]
        d_in = D - 2 * t
        Z = math.pi * (D ** 4 - d_in ** 4) / (32 * D)  # mm3
        sigma = M * 1000.0 / Z
        rows.append({"what": f"neck tube 26 x 1.5 Al at y {tube['guide_y']:g} ({'the pan hub' if tube is TUBE_COLUMN else 'the upper guide'})",
                     "kind": "tube bending", "util": sigma / AL_YIELD_MPA,
                     "value": f"{sigma:.1f} of {AL_YIELD_MPA:g} MPa",
                     "how": f"head {m:.2f} kg, COM {off:.0f} mm off the axis at 25 deg tilt, + tilt inertia at a_max; Z {Z:.0f} mm3"})
    rows.sort(key=lambda r: -r["util"])
    return rows


def thin_walls(report: Path) -> list[dict]:
    try:
        tests = json.loads(report.read_text())["tests"]
    except Exception:
        return []
    t = next((x for x in tests if x["id"] == "test_printable_wall"), None)
    out = []
    for item in (t or {}).get("summary", "").split(";"):
        parts = item.strip().split()
        if len(parts) >= 2 and parts[-1] == "mm":
            out.append({"what": f"thin wall: {parts[0]}", "kind": "printed wall", "util": 0.8 / float(parts[1]),
                        "value": f"{parts[1]} mm (rule 0.8)", "how": "suite printable_wall (2nd-percentile ray thickness)"})
    return out


def main(out: Path | None = None) -> Path:
    prof = json.loads(PROFILE.read_text())
    rows = analyse(prof) + thin_walls(MECH / "out" / "r3x_droid" / "test_report.json")
    rows.sort(key=lambda r: -r["util"])
    out = out or MECH / "out" / "r3x_droid" / "beef_up.md"
    L = ["# Beef-up candidates (ranked by utilisation)", "",
         f"From {PROFILE.relative_to(MECH.parent)} ({prof['mech']['generated_at']}). Utilisation = demand / capacity; "
         f"servos over {RULE:.0%} break the 70 % rule. Nothing changed - a ranking.", "",
         "| # | what | kind | utilisation | value | how |", "|---|---|---|---|---|---|"]
    for k, r in enumerate(rows, 1):
        flag = " ⚠" if (r["kind"] == "servo torque" and r["util"] > RULE) or r["util"] > 1 else ""
        L.append(f"| {k} | {r['what']} | {r['kind']} | {r['util']:.0%}{flag} | {r['value']} | {r['how']} |")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L) + "\n")
    print("\n".join(L))
    return out


if __name__ == "__main__":
    main()
