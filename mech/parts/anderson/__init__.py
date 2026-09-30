"""Parametric models of Brian Anderson's printed R-3X internals (ours, build123d), with the same
conventions as parts/head: `make(params) -> Part` in the reference STEP's frame, named mate
features, params, reference and printability attached, regressed in tests/ against his STEPs
(mech/vendor/animation/r3x-internal-step, gitignored).

His head-tilt parts are not here: Hunter's gimbal replaces them.
"""

from pathlib import Path

# Anderson's printed hole sizes where they differ from the ISO table in parts/head/_common.py
HOLE_SIZES = {"M3": {"clearance": 3.5}}

REF_DIR = Path(__file__).resolve().parents[2] / "vendor" / "animation" / "r3x-internal-step"

# module -> what it is, grouped by the order they were modelled in
PARTS = {
    # the head's load path: neck support, pan (base rotation), head lift, tube clamps
    "parts.anderson.tube_clamp": "neck-rod-clamp / pipeclamp",
    "parts.anderson.neck_support_platform": "neck-support-platform",
    "parts.anderson.neck_support_ring_inner": "neck-support-ring-inner",
    "parts.anderson.neck_support_ring_outer": "neck-support-ring-outer",
    "parts.anderson.pan_gear": "head-rotate-servo-gea",
    "parts.anderson.pan_servo_mount": "neck-rotation-servo-mount",
    "parts.anderson.base_servo_top": "base-servo-top",
    "parts.anderson.lift_gear": "lift-gear",
    "parts.anderson.lift_rack": "head-lift-straight-gear",
    "parts.anderson.slide_platform": "slide-platform",
    "parts.anderson.base_center": "base-center",
    "parts.anderson.pan_ring_gear": "headlift-base-mount",
    "parts.anderson.base_ring": "headlift-base-mount (1)",
    # the visor drive still used with Hunter's head (the workbench's visor_horn / visor_push_rod / visor_tab)
    "parts.anderson.visor_horn": "visor-servo-horn",
    "parts.anderson.visor_push_rod": "visor-push-rod",
    "parts.anderson.visor_rod_tab": "visor-rod-tab",
    # the hero arm (the hero-arm-mods / new-wrist STLs are the same solids as these STEPs)
    "parts.anderson.mainarm": "mainarm",
    "parts.anderson.servomount": "servomount",
    "parts.anderson.bodytube": "bodytube",
    "parts.anderson.spacerblock": "spacerblock",
    "parts.anderson.wrist": "wrist",
    "parts.anderson.wrist_cap": "Part 1 (the STLs' Part 1 (1))",
    "parts.anderson.hand_arm_side": "hand-arm-side",
    "parts.anderson.hand_finger_side": "hand-finger-side",
}
# parts his STEP folder does not have: sourced from the STLs in their animation folders.
# module -> {variant name ("" = defaults): what it is}
STL_PARTS = {
    "parts.anderson.ring_servo_gear": {"": "top-ring-servo-gear", "LOWER": "lower-ring-servo-gear"},
    "parts.anderson.ring_gear": {"": "lower-ring-inner-gear", "TOP": "top-ring-inner-gear"},
    "parts.anderson.ring_servo_mount": {"": "lower-ring-servo-mount", "MAIN": "top-ring-servo-mount-main",
                                        "SPACER": "top-ring-servo-mount-spacer"},
}
STL_DIR = REF_DIR.parent

# not modelled: mgn12-rail-with-2020 is a stand-in for purchased parts (a 2020 extrusion + MGN12 rail);
# "Part 1 (1)" (the STLs' "Part 1") is a micro-servo stand-in (23 x 13 x 25.8 body, 2.5 mm tabs
# 29.8 across, a 6 mm output at x = 5.7) for the wrist's servo slot; the -dnp files are his
# do-not-print demo parts (servo arms, servo disk, elbow covers)
