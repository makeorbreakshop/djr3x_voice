"""Generate the per-file tables in mech/INVENTORY.md from the vendored folders + the model.

    .venv/bin/python -m r3xmech.inventory > out/inventory_tables.md
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

MECH = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(MECH))

A = MECH / "vendor/animation"
ROLE = {  # file stem (after the folder prefix) -> (role, joint(s), what it is)
    # base rotation
    "base-center": ("mech", "head_pan, head_lift", "pan turntable disc R110 + lift tower (2020 pocket, lift-servo column)"),
    "base-servo-top": ("mech", "head_lift", "clamp over the 60 kg lift servo in the tower column"),
    "head-lift-straight-gear": ("mech", "head_lift", "rack, 87 mm, tooth pitch 9.0 mm"),
    "head-rotate-servo-gea": ("mech", "head_pan", "15T pan pinion on the 35 kg servo"),
    "headlift-base-mount (1)": ("mech", "head_pan", "stage ring (fixed), R155 flange; not used in Morton's build"),
    "headlift-base-mount": ("mech", "head_pan", "60T-pitch internal pan sector, 97.7 deg of teeth (the 'big gear')"),
    "lift-gear": ("mech", "head_lift", "19T lift pinion (pitch radius 27.2 mm)"),
    "mgn12-rail-with-2020": ("DNP", "head_lift", "MGN12 rail on 2020 extrusion, 150 mm"),
    "mgn12h-dnp": ("DNP", "head_lift", "MGN12H carriage"),
    "neck-dnp": ("DNP", "head_pan, head_lift", "neck tube 26 mm OD x 500 mm (placeholder length)"),
    "neck-rotation-servo-mount": ("mech", "head_pan", "pan servo mount on the turntable"),
    "pipeclamp": ("mech", "head_lift", "tube clamp on the lift slide"),
    "servo-disc-dnp": ("DNP", "head_pan", "servo horn disc"),
    "servo-standard-dnp": ("DNP", "head_lift / head_tilt / visor", "servo body stand-in (60 kg size in base rotation, standard elsewhere)"),
    "servo-standard-dnp (1)": ("DNP", "head_pan", "35 kg pan servo stand-in"),
    "slide-platform": ("mech", "head_lift", "lift slide: carriage plate + rack wall"),
    # head tilt
    "neck-baseplate": ("mech", "head_tilt", "head mounting plate under the head floor, tilt bore + servo side"),
    "neck-center-gear": ("mech", "head_tilt", "fixed centre gear on the yoke (18.33 deg tooth pitch)"),
    "neck-main": ("mech", "head_tilt", "yoke on the tube top (socket R13.1, tilt bore)"),
    "neck-servo-disk-dnp": ("DNP", "head_tilt", "horn disc"),
    "neck-servo-gear": ("mech", "head_tilt", "19T tilt pinion"),
    # hero arm mods / wrist
    "bodytube": ("mech", "hero_shoulder", "32 mm tube along the hinge axis"),
    "Part 1 (1)": ("mech", "hero_wrist", "micro-servo holder in the wrist"),
    "Part 1": ("mech", "hero_wrist", "wrist ring"),
    "servo-arm-dnp": ("DNP", "hero_shoulder", "servo horn"),
    "servo-arm-2-dnp": ("DNP", "hero_shoulder", "servo horn (second shaft)"),
    "servo": ("DNP", "hero_shoulder", "20 kg double-shaft servo stand-in"),
    "wrist": ("mech", "hero_wrist", "wrist body (houses the 7 kg servo)"),
    "hand-finger-side": ("mech", "hero_wrist", "turning half of the wrist, carries the fingers"),
    "hand-arm-side": ("mech", "hero_wrist", "fixed half of the wrist"),
    "servo-disk-dnp": ("DNP", "hero_shoulder / hero_wrist", "horn disc"),
    "servomount": ("mech", "hero_shoulder", "servo mount in the elbow block"),
    "mainarm": ("mech", "hero_shoulder", "61 mm forearm, replaces kit HA_SB_1/HA_SP_*"),
    "elbow-covers-dnp": ("DNP", "hero_shoulder", "the kit's elbow discs (HA_LE_1/HA_RE_1) as reference"),
    "spacerblock": ("mech", "hero_shoulder", "horn spacer"),
    # lower / top ring
    "lower-ring-servo-mount": ("mech", "torso_lower", "servo mount on the pedestal top"),
    "servo-placeholder-dnp": ("DNP", "torso_lower / torso_top", "35 kg servo stand-in"),
    "lower-ring-inner-gear": ("mech", "torso_lower", "95T-pitch internal sector, 79.6 deg of teeth"),
    "lower-ring-servo-gear": ("mech", "torso_lower", "25T pinion"),
    "top-ring-servo-mount-main": ("mech", "torso_top", "servo mount"),
    "top-ring-servo-mount-spacer": ("mech", "torso_top", "servo mount spacer"),
    "top-ring-inner-gear": ("mech", "torso_top", "85T-pitch internal sector, 59.4 deg of teeth"),
    "top-ring-servo-gear": ("mech", "torso_top", "20T pinion"),
    # neck support
    "neck-support-platform": ("mech", "head_lift", "upper carriage plate"),
    "neck-rod-clamp": ("mech", "head_lift", "upper tube clamp"),
    "neck-support-ring-inner": ("mech", "head_pan", "6 in lazy-susan inner race (turns with the neck)"),
    "mgn12rail-dnp": ("DNP", "head_lift", "MGN12 rail 140 mm (upper guide)"),
    "neck-support-ring-outer": ("mech", "head_pan", "6 in lazy-susan outer race (R124.5 = the kit spacer rings)"),
    # visor
    "visor-rod-tab": ("mech", "visor", "18 mm lever pinned to the axle"),
    "visor-demo-arm-dnp": ("DNP", "visor", "stand-in for the kit visor side arm"),
    "visor-base": ("mech", "visor", "visor frame on the head floor, F6001ZZ bearing housings"),
    "visor-push-rod": ("mech", "visor", "push rod, 58 mm between pins"),
    "pin-dnp": ("DNP", "visor", "pin"),
    "visor-flipper-adapter": ("mech", "visor", "hub joining the axle to the visor arm"),
    "flanged-bearing-dnp": ("DNP", "visor", "F6001ZZ 12x28x8"),
    "visor-servo-horn": ("mech", "visor", "25 mm horn"),
    "visor-mount-main": ("mech", "visor", "sleeve over the kit's H_Main_2 post"),
    "visor-mount-cap": ("mech", "visor", "sleeve cap"),
    "visor-center-rod": ("mech", "visor", "12 mm axle, 160 mm"),
}
FOLDER_SUB = {"r3x - base rotation": "Base: pan turntable + head lift", "r3x - head tilt": "Neck top: head tilt",
              "r3x - hero arm mods": "Top ring: hero arm", "r3x - hero arm wrist": "Top ring: hero wrist (new)",
              "r3x - lower ring animation": "Lower ring drive", "r3x - neck support": "Upper neck guide",
              "r3x - top ring animation": "Top ring drive", "r3x - visor animation": "Head: visor"}


def animation_rows():
    rows = []
    for d in sorted(A.iterdir()):
        if not d.is_dir() or d.name not in FOLDER_SUB:
            continue
        for f in sorted(d.glob("*.stl")):
            stem = f.stem.split(" - ", 1)[1]
            role, joint, what = ROLE.get(stem, ("?", "?", "?"))
            rows.append(f"| {d.name} / {stem} | {FOLDER_SUB[d.name]} | {role} | STL (+ STEP twin) | {joint} | {what} |")
    return rows


def kit_rows():
    from assemblies.kit.assembly import KIT, kit_files, LINK_RULES, _deepest
    link_joint = {"base": "(static)", "lower_ring": "torso_lower", "poker_upper": "poker_shoulder (poseable)",
                  "poker_hand": "poker_wrist (poseable)", "middle_ring": "torso_middle (not motorised)",
                  "throttle_upper": "throttle_shoulder (poseable)", "throttle_fore": "throttle_elbow (poseable)",
                  "throttle_hand": "throttle_wrist (poseable)", "top_ring": "torso_top", "hero_arm": "hero_shoulder",
                  "hero_hand": "hero_wrist", "head": "head_tilt", "visor": "visor"}
    rows = []
    for f in kit_files():
        stem = f.stem
        clean = stem.replace(" (New)", "").replace(" (DNP)", "_DNP").replace(" - x4", "")
        link = _deepest(LINK_RULES, clean)
        sub = f.parent.parent.name if f.parent.name == "STLs" else f"{f.parent.parent.parent.name} / {f.parent.name}"
        role = "shell (DNP gasket)" if "DNP" in stem or stem == "TR_RR_Full" else "shell"
        rows.append(f"| {stem} | {sub} | {role} | STL | {link_joint.get(link, link)} |")
    return rows


if __name__ == "__main__":
    print("| file | subsystem | role | format | joint | what |\n|---|---|---|---|---|---|")
    print("\n".join(animation_rows()))
    print("\n| kit file | subsystem | role | format | rides on |\n|---|---|---|---|---|")
    print("\n".join(kit_rows()))
