"""The head shells' common frame and shapes.

Hunter's shell STLs (`RX Head Top.stl`, `RX Head Bottom with mount holes.stl`, `Left/Right Side w
alignment holes.stl`) are in the R-3X kit model's coordinates: the head sits at y ~ 738 and is
yawed -51.8 deg about +Y. The workbench's head frame (assemblies/hunter_head `fit_shell`: the
bottom's six insert holes onto the plate's) is origin = gimbal centre, +Y up, +Z forward, +X the
droid's left. Each shell is designed in its own frame, carried into the head frame, then into
the STL's by SHELL_FROM_HEAD, so a swap keeps every placement.
"""

from __future__ import annotations

from ._common import rot_y, trans

# head frame -> the shell STLs' (kit) frame, from fit_shell (6 insert holes, rms 0.05 mm)
SHELL_YAW_DEG = -51.81139
SHELL_ORIGIN = (1.284367, 738.316726, -0.978637)
SHELL_FROM_HEAD = trans(SHELL_ORIGIN) @ rot_y(SHELL_YAW_DEG)


def spheroid(a: float, b: float, yc: float, y_lo: float, axis=(0.0, 0.0)):
    """A solid of revolution about a vertical axis at (x, z) = `axis`: the upper half of an
    ellipse (semi-axes a across, b up, centre height yc), continued straight down as a cylinder
    of radius a to y_lo."""
    from build123d import Axis, BuildLine, BuildPart, BuildSketch, EllipticalCenterArc, Line, Plane, Pos, make_face, revolve

    with BuildPart() as bp:
        with BuildSketch(Plane.XY):
            with BuildLine():
                EllipticalCenterArc((0, yc), a, b, 0, arc_size=90)
                Line((0, yc + b), (0, y_lo))
                Line((0, y_lo), (a, y_lo))
                Line((a, y_lo), (a, yc))
            make_face()
        revolve(axis=Axis.Y)
    return Pos(axis[0], 0, axis[1]) * bp.part


def elliptic_prism_z(a: float, b: float, xc: float, yc: float, z0: float, z1: float):
    """An elliptic cylinder along Z (semi-axes a in X, b in Y), from z0 to z1."""
    from build123d import BuildPart, BuildSketch, Ellipse, Locations, Plane, extrude

    with BuildPart() as bp:
        with BuildSketch(Plane.XY.offset(z0)):
            with Locations((xc, yc)):
                Ellipse(a, b)
        extrude(amount=z1 - z0)
    return bp.part
