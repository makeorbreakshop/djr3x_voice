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
}
