"""The robot profile's joints, from the mech model.

Reads the built droid manifest (mech/out/r3x_droid/manifest.json, Hunter's head mounted by
ChildRef) and the current profile, and produces:

* `profiles/r3x/robot.generated.json` - the current profile with its joint tree, limits,
  speeds and actuator ratios re-derived from the mechanism, plus a `mech` block (ignored by
  the Rust contract, read by the sim) with each joint's pivot/axis, drive, servo rating and
  first contact, each link's mass/COM/inertia, and the push-rod linkages in the body frame.
* `profiles/r3x/robot.generated.diff.md` - every change against robot.json, the ones that
  change how existing clips play marked, and the clips they touch.

Nothing is written to robot.json unless `--apply` is passed (see __main__.py).

Rules (also written into the `mech.rules` block):
* hard = the mechanism's range (gear sector / rack / servo travel / design range).
* soft = hard, pulled in by a margin of max(1.5, 4 % of the span) per side.
* animation = soft, pulled in again to stay `margin` inside any first-contact angle.
* v_max (driven joints) = 70 % of the servo's no-load speed at the supply voltage, through the
  ratio (the motion-control.md 70 % rule); a_max/j_max are kept (the mech says nothing new).
* Joints the mechanism does not drive (kind none, or an empty range) keep the profile's
  limits; the diff says so.
"""

from __future__ import annotations

import copy
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from . import servos
from .inertia import fmt_inertia, link_inertials
from .tree import Joint, Tree, apply, load, r

ETA = {"gear": 0.85, "push_rod": 0.90, "push_rod_pair": 0.90, "direct": 1.0}


# ------------------------------------------------------------------ linkages
def solve_rod(lk: dict, horn_m: np.ndarray, ground_m: np.ndarray) -> float | None:
    """Servo angle (deg from the horn's zero direction) - SCHEMA.md "Linkage", body frame."""
    h = lk["horn"]
    n = np.asarray(h["axis"], float)
    n /= np.linalg.norm(n)
    u = np.asarray(h["zero_dir"], float)
    u = u - n * (u @ n)
    u /= np.linalg.norm(u)
    w = np.cross(n, u)
    R = horn_m[:3, :3]
    c = apply(horn_m, h["centre"])
    n2, u2, w2 = R @ n, R @ u, R @ w
    b = apply(ground_m, lk["ground"]["point"])
    d = c + h["ball_offset"] * n2 - b
    rr, L = h["radius"], lk["rod_length"]
    A, B = 2 * rr * (d @ u2), 2 * rr * (d @ w2)
    C = L * L - d @ d - rr * rr
    hh = math.hypot(A, B)
    if hh < 1e-9 or abs(C) > hh:
        return None
    base, off = math.atan2(B, A), math.acos(C / hh)
    wrap = lambda t: math.atan2(math.sin(t), math.cos(t))  # noqa: E731
    return math.degrees(min((wrap(base + off), wrap(base - off)), key=abs))


def servo_jacobian(tree: Tree, lks: list[dict], joints: list[str], pose: dict, eps=0.25) -> np.ndarray | None:
    """d(servo deg)/d(joint unit), rows = linkages, cols = profile joints (central differences)."""
    J = np.zeros((len(lks), len(joints)))
    for c, jn in enumerate(joints):
        vals = []
        for s in (+eps, -eps):
            ms = tree.link_matrices(dict(pose, **{jn: pose.get(jn, 0.0) + s}))
            row = [solve_rod(lk, ms[lk["horn"]["link"]], ms[lk["ground"]["link"]]) for lk in lks]
            if any(v is None for v in row):
                return None
            vals.append(np.array(row))
        J[:, c] = (vals[0] - vals[1]) / (2 * eps)
    return J


# ------------------------------------------------------------------ first contact
_CONTACT = re.compile(r"first contact ([+-]?\d+(?:\.\d+)?)[^;]*?(?:;\s*([+-]\d+(?:\.\d+)?))?")


def first_contacts(tree: Tree, phase_a: dict | None) -> dict[str, dict]:
    """profile joint -> {min, max, source}: the angles at which parts first touch (None = clear)."""
    out: dict[str, dict] = {}
    for a in tree.asms:
        for c in a.checks:
            if c.get("kind") != "interference" or not c.get("joint"):
                continue
            j = tree.joints.get(f"{a.id}/{c['joint']}")
            pj = j.profile_joint if j else c["joint"]
            m = _CONTACT.search(c.get("summary", ""))
            lo = hi = None
            if m:
                vals = [float(x) for x in m.groups() if x is not None]
                hi = next((v for v in vals if v > 0), None)
                lo = next((v for v in vals if v < 0), None)
            out[pj] = {"min": lo, "max": hi, "source": f"{a.id} check {c['id']}: {c.get('summary', '')[:160]}"}
    # Phase A (r3xmech, mech/out/checks.json) swept the kit + R-3X Animation model: its angles hold
    # only for joints of the droid's own (inline) assemblies, not a separately built head.
    own = {j.profile_joint for j in tree.joints.values() if j.profile_joint and not _asm(tree, j.asm).referenced}
    for jn, s in ((phase_a or {}).get("interference", {}).get("sweeps", {}) or {}).items():
        if jn in out or jn not in own:
            continue
        lo = s["min"][0] if s.get("min") else None
        hi = s["max"][0] if s.get("max") else None
        pairs = [p for x in (s.get("min"), s.get("max")) if x for p in x[1]]
        out[jn] = {"min": lo, "max": hi, "source": "mech/out/checks.json sweep" + (f" ({pairs[0][0]} x {pairs[0][1]})" if pairs else "")}
    return out


# ------------------------------------------------------------------ helpers
def _asm(tree: Tree, aid: str):
    return next(a for a in tree.asms if a.id == aid)


# Part names that identify a catalogue servo (the manifest's material is plain "servo" there).
_NAMED = {"2000-0025-0002": "GOBILDA_2000_25_2", "35 kg": "SERVO_35KG_270", "60 kg": "SERVO_60KG_270"}


def _servo_key(tree: Tree, j: Joint, actuator: dict | None) -> tuple[str | None, str]:
    ids = set((j.drive or {}).get("servos") or [])
    # the joint's own assembly first; then anywhere (a kit ring joint driven by the column's servo)
    for p in sorted(tree.parts, key=lambda q: q.asm != j.asm):
        if p.raw["id"] in ids:
            mat = p.raw.get("material") or ""
            if mat.startswith("servo:") and mat[6:] in servos.SERVOS:
                return mat[6:], f"part {p.raw['id']} material"
            for frag, key in _NAMED.items():
                if frag in (p.raw.get("name") or ""):
                    return key, f"part {p.raw['id']} name"
    if actuator and actuator.get("servo") in servos.SERVOS:
        return actuator["servo"], f"profile actuator {actuator['name']}"
    return None, "unknown"


def _shrink(lo: float, hi: float, m: float) -> tuple[float, float]:
    return lo + m, hi - m


def _rd(x: float, n=2) -> float:
    v = round(float(x), n)
    return 0.0 if v == 0 else v


def _profile_actuator(profile: dict, joint: str) -> dict | None:
    return next((a for a in profile.get("actuators", []) if joint in a.get("joints", {})), None)


# ------------------------------------------------------------------ generate
def generate(manifest: Path, profile_path: Path, phase_a_path: Path | None = None) -> dict:
    tree = load(manifest)
    profile = json.loads(Path(profile_path).read_text())
    phase_a = json.loads(phase_a_path.read_text()) if phase_a_path and phase_a_path.exists() else None
    volts = (profile.get("servo_controller") or {}).get("supply_volts", 6.0)
    contacts = first_contacts(tree, phase_a)
    byp = tree.by_profile()
    gen = copy.deepcopy(profile)
    gen["label"] = profile.get("label", "") + " [generated from the mech model]"
    gen["_doc"] = ("GENERATED by `mech/.venv/bin/python -m rigsync` from " + str(manifest.relative_to(manifest.parents[2]))
                   + " - do not edit; see robot.generated.diff.md. " + profile.get("_doc", ""))
    changes: list[dict] = []
    mech_joints: dict[str, dict] = {}
    rest = {}

    # Linkages in the body frame, and the joints each one serves.
    lk_body = {g: lk.body() for g, lk in tree.linkages.items()}

    for pj in gen["joints"]:
        name = pj["name"]
        j = byp.get(name)
        act = _profile_actuator(gen, name)
        if j is None:
            changes.append(dict(joint=name, field="-", old="-", new="-", behaviour=False,
                                note="not in the mech model (arm claws / extended build): profile values kept"))
            continue
        anc = tree.moving_ancestor(j.parent_link)
        parent = anc.profile_joint if anc else None
        kind = (j.drive or {}).get("kind", "none")
        lo, hi = j.limits
        driven = kind not in ("none", "external") and hi > lo
        # --- drive ratio (servo deg per joint unit) ---
        ratio, mm_per_deg, how = None, None, ""
        lks = [lk_body[f"{j.asm}/{x}"] for x in (j.drive or {}).get("linkages", []) if f"{j.asm}/{x}" in lk_body]
        jac = None
        if kind == "gear" and j.type == "prismatic":
            mm_per_deg = j.drive.get("mm_per_servo_deg")
            how = f"rack: {mm_per_deg} mm per servo deg"
        elif kind == "gear":
            ratio = j.drive.get("servo_deg_per_joint_deg") or (1.0 / j.drive["gear_ratio"] if j.drive.get("gear_ratio") else None)
            how = f"gear {ratio:.4g}:1"
        elif kind == "direct":
            ratio = j.drive.get("servo_deg_per_unit") or 1.0 / (j.drive.get("gear_ratio") or 1.0)
            how = "direct"
            by = j.drive.get("servo_deg_per_unit_by_servo")
            if by and len(by) > 1:
                how = "direct, " + ", ".join(f"{k} {v:+g}" for k, v in by.items()) + " servo deg per joint deg"
        elif kind in ("push_rod", "push_rod_pair") and lks:
            jac = servo_jacobian(tree, lks, [name], {})
            if jac is not None:
                ratio = float(np.max(np.abs(jac[:, 0])))
                how = f"{kind} at rest: " + ", ".join(f"{lk['servo']} {jac[i, 0]:+.3f}" for i, lk in enumerate(lks)) + " servo deg per joint deg"
        # --- limits ---
        c = contacts.get(name, {})
        if driven:
            hard = (lo, hi)
            span = hi - lo
            m = max(1.5, 0.04 * span)
            soft = _shrink(*hard, m)
            anim_lo, anim_hi = soft
            if c.get("max") is not None:
                anim_hi = min(anim_hi, c["max"] - m)
            if c.get("min") is not None:
                anim_lo = max(anim_lo, c["min"] + m)
            anim = (anim_lo, anim_hi)
        else:
            hard = (pj["hard"]["min"], pj["hard"]["max"])
            soft = (pj["soft"]["min"], pj["soft"]["max"])
            anim = (pj["animation"]["min"], pj["animation"]["max"])
        # --- speed ---
        key, key_src = _servo_key(tree, j, act)
        v_max = pj["v_max"]
        if driven and key:
            w0 = servos.no_load_dps(key, volts)
            if mm_per_deg:
                v_max = 0.7 * w0 * mm_per_deg
            elif ratio:
                v_max = 0.7 * w0 / ratio
        new = dict(pj)
        new.update(parent=parent,
                   hard={"min": _rd(hard[0]), "max": _rd(hard[1])},
                   soft={"min": _rd(soft[0]), "max": _rd(soft[1])},
                   animation={"min": _rd(anim[0]), "max": _rd(anim[1])},
                   v_max=_rd(v_max, 1))
        for f in ("parent", "hard", "soft", "animation", "v_max"):
            if new[f] != pj.get(f):
                changes.append(dict(joint=name, field=f, old=pj.get(f), new=new[f], behaviour=_behaviour(f, pj.get(f), new[f])))
        pj.clear()
        pj.update(new)
        # --- the mech block ---
        mech_joints[name] = {
            "gid": j.gid, "type": j.type, "parent": parent,
            "pivot": r(j.pivot, 3), "axis": r(j.axis, 6),
            "child_link": j.child_link,
            "mech_limits": {"min": lo, "max": hi},
            "drive": {"kind": kind, "servos": list((j.drive or {}).get("servos") or []), "servo_model": key,
                      "servo_deg_per_unit_by_servo": (j.drive or {}).get("servo_deg_per_unit_by_servo"),
                      "servo_model_source": key_src, "servo_deg_per_unit": _rd(ratio, 4) if ratio else None,
                      "mm_per_servo_deg": mm_per_deg, "eta": ETA.get(kind, 1.0),
                      "linkages": [lk["id"] for lk in lks], "how": how,
                      "jacobian_at_rest": [_rd(x, 4) for x in jac[:, 0]] if jac is not None else None},
            "first_contact": c or None,
            "inferred": j.inferred,
            "driven": driven,
        }
        # --- actuator calibration ---
        if act and driven:
            cal = act["calibration"]
            if mm_per_deg and cal.get("mm_per_deg") != mm_per_deg:
                changes.append(dict(joint=name, field=f"actuator {act['name']}.mm_per_deg", old=cal.get("mm_per_deg"),
                                    new=mm_per_deg, behaviour=True, note="servo pulse per mm changes: the same clip moves the lift a different distance on hardware"))
                cal["mm_per_deg"] = mm_per_deg
            if ratio and not mm_per_deg and abs(cal.get("gear", 1.0) - ratio) > 1e-3:
                note = "servo pulse per joint degree changes (hardware only; the sim is unaffected)"
                if kind == "push_rod_pair":
                    note = ("push-rod PAIR: tilt and roll both move servo_l and servo_r; the profile still models one "
                            "channel per joint, so this is the rest-pose magnitude only (see mech.drives)")
                changes.append(dict(joint=name, field=f"actuator {act['name']}.gear", old=cal.get("gear"), new=_rd(ratio, 4),
                                    behaviour=True, note=note))
                cal["gear"] = _rd(ratio, 4)
            if key and act.get("servo") != key:
                changes.append(dict(joint=name, field=f"actuator {act['name']}.servo", old=act.get("servo"), new=key, behaviour=False))
                act["servo"] = key
        rest[name] = 0.0

    # Drives: one entry per servo part (a push-rod pair is two servos serving two joints).
    drives = []
    seen = set()
    for name, mj in mech_joints.items():
        d = mj["drive"]
        if not mj["driven"] or not d["servos"]:
            continue
        if d["kind"] == "push_rod_pair":
            group = tuple(sorted(n for n, x in mech_joints.items() if x["drive"]["linkages"] == d["linkages"]))
            if group in seen:
                continue
            seen.add(group)
            for s, lk in zip(d["servos"], d["linkages"]):
                drives.append(dict(servo=s, model=d["servo_model"], kind="push_rod_pair", joints=list(group),
                                   linkage=f"{mj['gid'].split('/')[0]}/{lk}", eta=d["eta"]))
        elif d.get("servo_deg_per_unit_by_servo") and len(d["servos"]) > 1:
            # several servos on one joint at once (Hunter's visor: one each side, mirrored): one drive each, signed
            for sv in d["servos"]:
                drives.append(dict(servo=sv, model=d["servo_model"], kind=d["kind"], joints=[name], linkage=None,
                                   servo_deg_per_unit=d["servo_deg_per_unit_by_servo"].get(sv, d["servo_deg_per_unit"]),
                                   mm_per_servo_deg=d["mm_per_servo_deg"], eta=d["eta"]))
        else:
            drives.append(dict(servo=d["servos"][0], model=d["servo_model"], kind=d["kind"], joints=[name],
                               linkage=(f"{mj['gid'].split('/')[0]}/{d['linkages'][0]}" if d["linkages"] else None),
                               servo_deg_per_unit=d["servo_deg_per_unit"], mm_per_servo_deg=d["mm_per_servo_deg"], eta=d["eta"]))
    for dv in drives:
        k = dv["model"]
        dv["stall_nm"] = _rd(servos.stall_nm(k, volts), 4) if k else None
        dv["no_load_dps"] = _rd(servos.no_load_dps(k, volts), 1) if k else None

    # Links: mass properties and the profile joint that moves each one.
    inert = link_inertials(tree)
    links = []
    for gid, l in tree.links.items():
        mv = tree.moving_ancestor(gid)
        li = inert.get(gid)
        links.append({
            "id": gid, "name": l.name, "joint": mv.profile_joint if mv else None,
            "kg": _rd(li["kg"], 4) if li else 0.0,
            "com": r(li["com"], 2) if li else None,
            "inertia": fmt_inertia(li["I"]) if li else None,
            "parts": li["parts"] if li else 0,
            "catalogue_parts": li["catalogue_parts"] if li else 0,
        })

    gen["mech"] = {
        "_doc": ("Extension block (serde ignores it): the mech model this profile was generated from. Body frame "
                 "(show/SPEC.md): mm, +Y up, +Z front, +X the droid's left, rest pose. inertia = [Ixx, Iyy, Izz, Ixy, "
                 "Ixz, Iyz] kg m^2 about the link COM, body axes. servo_deg_per_unit = servo deg per joint deg "
                 "(the profile's calibration.gear). Links of joint null are ground."),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "manifest": {"path": str(manifest.relative_to(manifest.parents[2])), "generated_at": tree.manifest.get("generated_at"),
                     "assemblies": [a.id for a in tree.asms], "skipped": tree.skipped},
        "supply_volts": volts,
        "rules": {"soft_margin": "max(1.5, 4% of span) per side", "animation": "soft, and margin inside first contact",
                  "v_max": "0.7 x servo no-load speed at supply / ratio (driven joints)",
                  "eta": ETA, "rated": "stall torque at supply_volts (servos.rs); the 70% rule flags load > 0.7 x stall"},
        "joints": mech_joints,
        "drives": drives,
        "links": links,
        "linkages": {g: lk_body[g] for g in lk_body},
        # coupled joint limits from the whole-droid suite (workbench/droid.py derive_couplings): the
        # dependent joint's [min, max] as a function of the driving one, profile joint names; for the
        # performer's safety layer to enforce
        "couplings": tree.manifest.get("root", {}).get("couplings", []),
        "total_kg": _rd(sum(x["kg"] for x in links), 3),
    }
    return {"profile": gen, "changes": changes, "tree": tree}


def _behaviour(field: str, old, new) -> bool:
    """Does this change alter how an existing clip plays (in the sim or on the robot)?"""
    if field == "parent":
        return True
    if field in ("hard", "soft", "animation"):
        return bool(old) and (new["min"] > old["min"] or new["max"] < old["max"])  # narrower clamps
    if field == "v_max":
        return new < old  # a lower cap slows fast moves; a higher one only allows more
    return False


# ------------------------------------------------------------------ clips
def clip_hits(show_dir: Path, profile: dict, old: dict) -> list[dict]:
    """Clips whose authored values leave a joint's new animation range but sat inside the old."""
    newj = {j["name"]: j for j in profile["joints"]}
    oldj = {j["name"]: j for j in old["joints"]}
    hits = []
    for f in sorted((show_dir / "clips").glob("*.json")):
        try:
            doc = json.loads(f.read_text())
        except Exception:
            continue
        for jn, tr in (doc.get("tracks") or {}).items():
            if jn not in newj or not tr.get("keys"):
                continue
            vals = [k[1] for k in tr["keys"]]
            lo, hi = min(vals), max(vals)
            na, oa = newj[jn]["animation"], oldj[jn]["animation"]
            if (lo < na["min"] or hi > na["max"]) and not (lo < oa["min"] or hi > oa["max"]):
                hits.append(dict(clip=doc.get("id", f.stem), joint=jn, mode=tr.get("mode", "absolute"), range=[lo, hi],
                                 new=[na["min"], na["max"]], old=[oa["min"], oa["max"]]))
    return hits
