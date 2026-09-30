"""robot.generated.diff.md: the generated profile against the live one."""

from __future__ import annotations

from pathlib import Path


def _v(x) -> str:
    if isinstance(x, dict) and set(x) == {"min", "max"}:
        return f"{x['min']:g} .. {x['max']:g}"
    return "-" if x is None else f"{x:g}" if isinstance(x, float) else str(x)


def write_report(path: Path, gen: dict, old: dict, changes: list[dict], hits: list[dict]) -> None:
    mech = gen["mech"]
    L = []
    w = L.append
    w("# Robot profile: mech model vs `robot.json`")
    w("")
    w(f"Generated {mech['generated_at']} by `mech/.venv/bin/python -m rigsync` from `{mech['manifest']['path']}` "
      f"(built {mech['manifest']['generated_at']}; assemblies {', '.join(mech['manifest']['assemblies'])}; "
      f"not fitted: {', '.join(mech['manifest']['skipped']) or 'none'}).")
    w("")
    w("`robot.generated.json` is NOT live. To try it: runtime `R3X_PROFILE=profiles/r3x/robot.generated.json`, "
      "sim `?profile=generated`. To adopt it: `mech/.venv/bin/python -m rigsync --apply` (backs up robot.json).")
    w("")
    beh = [c for c in changes if c.get("behaviour")]
    w(f"**{len(changes)} changes; {len(beh)} change how existing clips play (marked ⚠).**")
    w("")
    w("## Changes")
    w("")
    w("| | joint | field | robot.json | generated | note |")
    w("|---|---|---|---|---|---|")
    for c in changes:
        if c["field"] == "-":
            continue
        w(f"| {'⚠' if c.get('behaviour') else ''} | {c['joint']} | {c['field']} | {_v(c['old'])} | {_v(c['new'])} | {c.get('note', '')} |")
    kept = [c["joint"] for c in changes if c["field"] == "-"]
    if kept:
        w("")
        w(f"Not in the mech model (values kept): {', '.join(kept)}.")
    w("")
    w("## Joint tree")
    w("")
    w("| joint | parent (robot.json) | parent (mech) | pivot mm | axis | drive | servo | first contact |")
    w("|---|---|---|---|---|---|---|---|")
    oldp = {j["name"]: j.get("parent") for j in old["joints"]}
    for n, j in mech["joints"].items():
        d = j["drive"]
        fc = j.get("first_contact") or {}
        fct = ", ".join(f"{k} {fc[k]:+g}" for k in ("min", "max") if fc.get(k) is not None) or "clear"
        w(f"| {n} | {oldp.get(n) or '-'} | {j['parent'] or '-'} | {', '.join(f'{x:g}' for x in j['pivot'])} | "
          f"{', '.join(f'{x:.3g}' for x in j['axis'])} | {d['kind']}{' - ' + d['how'] if d['how'] else ''} | {d['servo_model'] or '-'} | {fct} |")
    w("")
    w("## Mass properties (per link, from the CAD)")
    w("")
    w(f"Total {mech['total_kg']:g} kg. Printed parts: volume x density x fill; purchased parts: catalogue mass; "
      "inertia from each part's mesh as a uniform solid (a lower bound for printed parts).")
    w("")
    w("| link | moved by | kg | COM mm | Ixx Iyy Izz kg m² |")
    w("|---|---|---|---|---|")
    for l in mech["links"]:
        if not l["kg"]:
            continue
        I = l["inertia"] or [0, 0, 0]
        w(f"| {l['id']} | {l['joint'] or 'ground'} | {l['kg']:.3f} | {', '.join(f'{x:.0f}' for x in l['com'])} | "
          f"{I[0]:.2e} {I[1]:.2e} {I[2]:.2e} |")
    w("")
    w("## Servos")
    w("")
    w("| servo | model | drives | ratio | rated (stall) N m | no-load deg/s |")
    w("|---|---|---|---|---|---|")
    for d in mech["drives"]:
        ratio = (f"{d['mm_per_servo_deg']} mm/servo deg" if d.get("mm_per_servo_deg")
                 else f"{d['servo_deg_per_unit']}:1" if d.get("servo_deg_per_unit") else "linkage (live Jacobian)")
        w(f"| {d['servo']} | {d['model'] or '?'} | {', '.join(d['joints'])} | {ratio} | {d['stall_nm'] or '-'} | {d['no_load_dps'] or '-'} |")
    w("")
    w("## Clips that change")
    w("")
    if not hits:
        w("No authored key leaves a new animation range. (Additive tracks are checked about the rest pose; "
          "stacked on another pose they can still clamp.)")
    else:
        w("Authored keys outside the NEW animation range (inside the old one): these clamp once the generated "
          "profile is live. Additive tracks are checked about the rest pose.")
        w("")
        w("| clip | joint | mode | keys span | old range | new range |")
        w("|---|---|---|---|---|---|")
        for h in hits:
            w(f"| {h['clip']} | {h['joint']} | {h['mode']} | {h['range'][0]:g} .. {h['range'][1]:g} | "
              f"{h['old'][0]:g} .. {h['old'][1]:g} | {h['new'][0]:g} .. {h['new'][1]:g} |")
    w("")
    w("## Behaviour notes")
    w("")
    w("- Parent changes (⚠) move a joint to another branch. The mech model stands the head column on the "
      "base (the pan turntable is fixed to the base) and each ring on the static core, so turning a ring no "
      "longer turns the head or the ring above it. Body-yaw gestures lose their head motion, and the "
      "puppeteer's gaze spill (`show::puppeteer`: yaw past the neck limit goes to the rings) no longer "
      "extends the gaze.")
    w("- Narrower ranges clamp at the performer's follower; `look_around` above is the one committed clip "
      "that reaches past the new pan range.")
    w("- Actuator `gear`/`mm_per_deg` changes alter pulses on hardware only; the sim renders joint values.")
    w("- Torque per clip: Studio's lint and the Bench meter (sim/web/src/mechrig/torque.ts, mech/PHYSICS.md).")
    path.write_text("\n".join(L) + "\n")
