"""Anderson's hero arm put together: where each of his parts sits, from his own files (no assembly
is published; the evidence is listed per mate). Two rigid groups:

* static (on the body, `sm` = the servomount's frame): servomount, the dual-shaft servo (servo.stl),
  the body tube in the servomount's collar.
* the arm (`ma` = the main arm's frame), turning about the hinge: main arm, the two elbow covers
  (his demo copies of the kit's elbow discs), wrist, wrist cap, the wrist servo, both hand halves.

The hinge is the servo's shaft axis: along sm +X through HINGE_SM, along ma +Y through HINGE_MA.
`arm_in_sm(theta)` places the arm group for a forearm direction (0, cos t, sin t) in sm: theta = 0
points the forearm straight out of the channel's front, 90 straight up. With his parts and no other
geometry, nothing touches from theta = -60 to 110 deg (tests/test_anderson_parts.py checks it).

Evidence:
- covers -> main arm: the covers' 3 holes (20 mm apart, 45.5 mm from the disc centre) are the foot's
  3 side pockets (x = -46, -26, -6 at z = 7) and the covers' tab end lands flush with the foot's
  top (z = 14): the hinge is 38.5 mm below the foot, under the arm's axis.
- servo in the servomount: servo.stl is 40 x 40 x 20 with 5 mm shaft stubs both ends (50 over the
  shafts), the channel is 50 wide: the shafts end at its open ends, the covers' 60 mm gap leaves 5
  mm each side for the 4 mm servo disks. The real servo's 54.5 mm ears span the channel's 55 mm
  between its walls, centring the body at y = -29.5; its shaft (10 off the body's centre) is then
  at y = -19.5, at the collar's height z = 27, so the body tube's axis meets the hinge.
  At that point every point of the servomount ahead of the hinge is within 38.0 mm of it, under
  the main arm foot's 38.5: the arm swings clear.
- body tube: its end face on the back wall (y = -62) inside the 33 mm collar, its four pockets on
  the wall's 10 mm square.
- wrist: its 25.5 mm floor bore on the main arm's 25 mm spigot, the floor flush with the spigot top.
- wrist cap: upside down, flange on the wrist's rim, spigot in the rim bore.
- wrist servo (the STLs' "Part 1", a stand-in): centred in the bridge's 24 mm slot, tabs on the
  bridge with their holes on its two 1.5 mm holes (14 mm either side), output on the wrist axis.
- hand: the arm side's collar in the cap's counterbore, resting on the cap's lip; the horn sits
  between the servo's output (z 214.5 in ma) and the pocket floor (216).
"""

from __future__ import annotations

import math

import numpy as np

HINGE_SM = (25.0, -19.5, 27.0)        # in the servomount's frame, the hinge along +X
HINGE_MA = (-26.0, -30.0, -38.5)      # in the main arm's frame, the hinge along +Y


def _T(rows, t):
    """4x4 with local x, y, z going to `rows` (each a 3-vector) and the local origin to `t`."""
    T = np.eye(4)
    T[:3, :3] = np.asarray(rows, float).T
    T[:3, 3] = t
    return T


# the static group, in sm
STATIC = {
    "servomount": np.eye(4),
    "servo": _T([[0, -1, 0], [1, 0, 0], [0, 0, 1]], [25.0, -29.5, 17.0]),    # servo.stl: shafts on its Y at (x -10, z 10)
    "bodytube": _T(np.eye(3), [25.0, -62.0, 27.0]),
}
# the arm group, in ma
ARM = {
    "mainarm": np.eye(4),
    "elbow_cover_a": _T([[0, 1, 0], [0, 0, -1], [-1, 0, 0]], [-26.0, 0.0, -38.5]),    # on the foot's y = 0 face
    "elbow_cover_b": _T([[0, -1, 0], [0, 0, -1], [1, 0, 0]], [-26.0, -60.0, -38.5]),  # on the y = -60 face
    "wrist": _T(np.eye(3), [-26.0, -30.0, 173.0]),
    "wrist_cap": _T([[1, 0, 0], [0, -1, 0], [0, 0, -1]], [-26.0, -30.0, 212.5]),
    "wrist_servo": _T(np.eye(3), [-31.7, -30.0, 184.5]),                                # the STLs' "Part 1"
    "hand_arm_side": _T(np.eye(3), [-26.0, -30.0, 213.0]),
    "hand_finger_side": _T(np.eye(3), [-26.0, -30.0, 213.0]),
}
WRIST_AXIS_MA = ((-26.0, -30.0, 0.0), (0.0, 0.0, 1.0))


def arm_in_sm(theta_deg: float) -> np.ndarray:
    """The main arm's frame in the servomount's, the forearm at `theta_deg` (see the module doc)."""
    c, s = math.cos(math.radians(theta_deg)), math.sin(math.radians(theta_deg))
    T = _T([[0.0, -s, c], [1.0, 0.0, 0.0], [0.0, c, s]], [0, 0, 0])
    T[:3, 3] = np.asarray(HINGE_SM) - T[:3, :3] @ np.asarray(HINGE_MA)
    return T


def sm_in_world(pivot, a_hat, inward) -> np.ndarray:
    """The servomount's frame in a droid frame: the hinge point `pivot` and direction `a_hat`, and
    `inward` (perpendicular to the hinge, toward the body: the tube's way in). Its +Z comes out as
    cross(a_hat, inward) - the channel's floor down when that is up."""
    a = np.asarray(a_hat, float) / np.linalg.norm(a_hat)
    w = np.asarray(inward, float) - a * (np.asarray(inward, float) @ a)
    w /= np.linalg.norm(w)
    x, y = -a, -w
    z = np.cross(x, y)
    T = _T([x, y, z], [0, 0, 0])
    T[:3, 3] = np.asarray(pivot, float) - T[:3, :3] @ np.asarray(HINGE_SM)
    return T


def theta_of(d_hat, a_hat, inward) -> float:
    """The arm angle for a forearm direction `d_hat` in the same droid frame."""
    T = sm_in_world((0, 0, 0), a_hat, inward)
    d = T[:3, :3].T @ np.asarray(d_hat, float)
    return math.degrees(math.atan2(d[2], d[1]))
