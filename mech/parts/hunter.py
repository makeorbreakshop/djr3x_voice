"""Parametric remodels of Hunter's printed head-mech parts (ours, build123d), each authored
against the reference STL in mech/vendor/hunter_head/ and checked against it by
`parts/tests/test_remodels.py` (bbox, volume, two-way surface distance). Every model is in the
reference STL's own frame, so placements do not change; each returns its B-rep, a mesh, and
the features its mates use (holes as axes with entry points, faces as planes).

    from parts.hunter import neck_coupler
    m = neck_coupler()                      # Hunter's: 32 mm tube bore
    m = neck_coupler(bore_d=26.0)           # Anderson's 26 mm neck tube
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import trimesh

TAP_M4 = 3.3      # printed hole an M4 screw self-threads into
CLEAR_M4 = 4.5    # M4 clearance
INSERT_M4 = 6.0   # 6 x 6 mm M4 heat-set insert hole


@dataclass
class Remodel:
    name: str
    params: dict
    shape: object                     # build123d Part
    features: dict = field(default_factory=dict)
    reference: str = ""

    def mesh(self, tol: float = 0.02, ang: float = 0.1) -> trimesh.Trimesh:
        v, t = self.shape.tessellate(tol, ang)
        return trimesh.Trimesh(np.array([(p.X, p.Y, p.Z) for p in v]), np.array(t), process=True)


def _axis(p, d, r):
    return {"type": "axis", "p": [float(x) for x in p], "d": [float(x) for x in d], "r": float(r)}


def _plane(p, n):
    return {"type": "plane", "p": [float(x) for x in p], "n": [float(x) for x in n]}


# ------------------------------------------------------------------ RXNeckCouplerV1

def neck_coupler(od=40.0, height=35.0, bore_d=32.0, top_t=5.0, centre_hole_d=15.0, pattern=16.0,
                 pattern_hole_d=TAP_M4, side_hole_d=TAP_M4, side_hole_y=15.0) -> Remodel:
    """A cup over the neck tube: bore from below, a top plate with the sonic hub's 16 mm pattern
    (M4 tap-drill holes) and a centre hole, one side hole (+X) to pin it to the tube.
    Axis +Y, bottom at y = 0 (the reference STL's frame)."""
    from build123d import Align, Axis, Cylinder, Location, Pos, Rot

    body = Cylinder(od / 2, height, align=(Align.CENTER, Align.CENTER, Align.MIN))
    body -= Cylinder(bore_d / 2, height - top_t, align=(Align.CENTER, Align.CENTER, Align.MIN))
    body -= Cylinder(centre_hole_d / 2, height, align=(Align.CENTER, Align.CENTER, Align.MIN))
    for x in (-pattern / 2, pattern / 2):
        for y in (-pattern / 2, pattern / 2):
            body -= Pos(x, y, 0) * Cylinder(pattern_hole_d / 2, height, align=(Align.CENTER, Align.CENTER, Align.MIN))
    # side hole along +X at side_hole_y, through the +X wall only
    body -= Pos(od / 2 - (od - bore_d) / 4, 0, side_hole_y) * Rot(0, 90, 0) * Cylinder(side_hole_d / 2, (od - bore_d) / 2 + 2)
    # build123d builds along +Z; the reference is +Y up: rotate -90 about X ((x, y, z) -> (x, z, -y))
    shape = Rot(-90, 0, 0) * body
    feats = {"top": _plane([0, height, 0], [0, 1, 0]), "bore": _axis([0, 0, 0], [0, 1, 0], bore_d / 2),
             "side_hole": _axis([od / 2, side_hole_y, 0], [-1, 0, 0], side_hole_d / 2)}
    for i, (x, z) in enumerate([(-pattern / 2, -pattern / 2), (-pattern / 2, pattern / 2),
                                (pattern / 2, -pattern / 2), (pattern / 2, pattern / 2)]):
        feats[f"hub_hole_{i + 1}"] = _axis([x, height, z], [0, -1, 0], pattern_hole_d / 2)
    return Remodel("RX Neck Coupler V1", dict(od=od, height=height, bore_d=bore_d, top_t=top_t,
                                              centre_hole_d=centre_hole_d, pattern=pattern,
                                              pattern_hole_d=pattern_hole_d, side_hole_d=side_hole_d,
                                              side_hole_y=side_hole_y),
                   shape, feats, "RXNeckCouplerV1.stl")


# ------------------------------------------------------------------ RX Neck Joint Member V1

def neck_joint_member(length=50.0, width=20.0, thick=8.0, wall=4.0, hole_d=TAP_M4, corner_r=3.0,
                      window_r=3.0) -> Remodel:
    """A rectangular ring (the reference's 42 x 12 window) with a hole through each end wall
    (along its length) and each side wall (across), at mid-thickness. Centred; length along Z,
    width along X, thickness along Y (the reference STL's frame)."""
    from build123d import Align, Axis, Box, Cylinder, Pos, Rot, fillet

    outer = Box(width, thick, length)
    outer = fillet(outer.edges().filter_by(Axis.Y), corner_r)
    window = Box(width - 2 * wall, thick + 2, length - 2 * wall)
    window = fillet(window.edges().filter_by(Axis.Y), window_r)
    ring = outer - window
    ring -= Cylinder(hole_d / 2, length + 2)                      # along Z (build123d default axis)
    ring -= Rot(0, 90, 0) * Cylinder(hole_d / 2, width + 2)       # along X
    feats = {"end_hole_a": _axis([0, 0, length / 2], [0, 0, -1], hole_d / 2),
             "end_hole_b": _axis([0, 0, -length / 2], [0, 0, 1], hole_d / 2),
             "side_hole": _axis([width / 2, 0, 0], [-1, 0, 0], hole_d / 2)}
    return Remodel("RX Neck Joint Member V1", dict(length=length, width=width, thick=thick, wall=wall, hole_d=hole_d,
                                                   corner_r=corner_r, window_r=window_r),
                   ring, feats, "RX Neck Joint Member V1.stl")


REMODELS = {"neck_coupler": neck_coupler, "neck_joint_member": neck_joint_member}


def compare(model: trimesh.Trimesh, ref: trimesh.Trimesh, n: int = 20000) -> dict:
    """Parametric vs reference: bbox deltas, volume ratio, two-way surface distance (mm)."""
    pa, _ = trimesh.sample.sample_surface(model, n, seed=1)
    pb, _ = trimesh.sample.sample_surface(ref, n, seed=2)
    _, da, _ = trimesh.proximity.closest_point(ref, pa)
    _, db, _ = trimesh.proximity.closest_point(model, pb)
    d = np.concatenate([da, db])
    return {
        "bbox_mm": float(np.abs(model.bounds - ref.bounds).max()),
        "volume_ratio": float(model.volume / ref.volume) if ref.is_watertight and model.is_watertight else None,
        "hausdorff_mm": float(d.max()),
        "p95_mm": float(np.percentile(d, 95)),
        "mean_mm": float(d.mean()),
    }


# ------------------------------------------------------------------ RX Head Mech Base Plate V4

def base_plate(width=150.0, front_z=40.0, back_z=-106.5, back_arc_start=-76.7, flange_t=4.0, deck_t=4.0,
               wall=4.5, y0=24.96, rail_x=(39.6, 44.1), deck_rail_x=(37.6, 42.2), notch_r=12.5,
               head_holes_x=60.0, head_holes_z=(-28.71, 0.03, 28.77), clear_d=CLEAR_M4,
               servo_holes=((16.0, 64.0), (-60.11, -69.89)), insert_d=INSERT_M4, servo_boss_top=64.26,
               insert_hole_depth=8.1,
               pillow_holes=(16.0, (-28.69, 28.79)), bridge_y=(52.11, 56.11), bridge_half=(31.1, 15.0, 22.5),
               cradle=(10.0, 20.0, 30.0, -79.0, -51.0, 60.0, 70.0)) -> Remodel:
    """Hunter's mount plate, rebuilt from its interfaces up (STL frame: +Y up, the gimbal side
    down, servos toward -Z). Layers: two 4 mm flanges carrying the six M4 clearance holes onto
    the head bottom (120 x 28.7 grid), a 4.5 mm rail wall on each, the 4 mm deck (curved back,
    centre notch), the side walls and servo cradles up to the pillow bridge (4 mm, window, the
    pillow blocks' 32 x 57.5 M4 pattern), and the servo bosses with their 6 mm insert holes on
    goBILDA's 48 x 9.8 mm flange pattern. The interfaces are exact; the walls between them are
    straight where the reference leans (see the regression numbers)."""
    from build123d import (Align, Box, BuildLine, BuildPart, BuildSketch, Cylinder, Line, Locations, Mode,
                           Plane, Polyline, Pos, RadiusArc, Rot, add, extrude, make_face)

    fy1 = y0 + flange_t
    ry1 = 32.26
    dy1 = ry1 + deck_t
    hw = width / 2

    def slab(x0, x1, z0, z1, ya, yb):
        return Pos((x0 + x1) / 2, (ya + yb) / 2, (z0 + z1) / 2) * Box(abs(x1 - x0), yb - ya, abs(z1 - z0))

    parts = []
    for s in (1, -1):
        # flange: the outer block + a strip along the back edge
        parts += [slab(s * 41.5, s * hw, -40, front_z, y0, fy1), slab(s * 11.5, s * 41.5, -40, -35, y0, fy1)]
        # rail wall + back strip up to the deck
        parts += [slab(s * rail_x[0], s * rail_x[1], -35, front_z, fy1, ry1), slab(s * 11.5, s * hw, -40, -35, fy1, ry1)]
        # deck rails to the front
        parts += [slab(s * deck_rail_x[0], s * deck_rail_x[1], -35.9, front_z, ry1, dy1)]
    # deck: back region with the arc (a circle through the corners and the back point)
    half = hw
    zc = back_z + ((half ** 2 + (back_arc_start - back_z) ** 2) / (2 * (back_arc_start - back_z)))
    R = zc - back_z
    deck = slab(-hw, hw, back_arc_start, -35.9, ry1, dy1)
    disc = Pos(0, (ry1 + dy1) / 2, zc) * Rot(90, 0, 0) * Cylinder(R, deck_t)
    band = slab(-hw, hw, back_z, back_arc_start, ry1, dy1)
    parts += [deck, disc & band]
    # centre notch at the deck's front edge
    # side walls (deck -> bridge) and the servo cradles (deck -> bridge -> bosses)
    x_in, x_mid, x_back, zb, zf, xo0, xo1 = cradle
    for s in (1, -1):
        parts += [slab(s * 31.6, s * 36.1, -55, front_z, dy1, bridge_y[0])]                    # side wall
        parts += [slab(s * x_in, s * 36.1, -55, -51, dy1, bridge_y[0]),                       # cradle front bar
                  slab(s * x_in, s * x_mid, -75, -55, dy1, bridge_y[0]),                      # cradle inner block
                  slab(s * x_in, s * 31.3, zb, -75, dy1, bridge_y[0])]                        # cradle back bar
        parts += [slab(s * xo0, s * xo1, zb, -73.1, dy1, servo_boss_top),                     # outer bosses
                  slab(s * xo0, s * xo1, -56.9, zf, dy1, servo_boss_top),
                  slab(s * x_in, s * x_back, zb, zf, bridge_y[1], servo_boss_top)]            # inner boss
    # pillow bridge
    bh, wx, wz = bridge_half
    parts += [slab(-bh, bh, -55, front_z, bridge_y[0], bridge_y[1])]
    for s in (1, -1):
        parts += [slab(s * x_in, s * 30.0, zb, -55, bridge_y[0], bridge_y[1])]
    body = parts[0]
    for p in parts[1:]:
        body = body + p
    # centre notch (semicircle at the deck's inner edge) and the bridge window
    body -= Pos(0, (ry1 + dy1) / 2, -35.0) * Rot(90, 0, 0) * Cylinder(notch_r, deck_t + 0.2)
    body -= slab(-16.8, 16.8, -35.0, front_z, ry1 - 0.1, dy1 + 0.1)
    body -= slab(-wx, wx, -wz, wz, bridge_y[0] - 0.1, bridge_y[1] + 0.1)
    feats = {}
    for s in (1, -1):
        for i, z in enumerate(head_holes_z):
            body -= Pos(s * head_holes_x, (y0 + fy1) / 2, z) * Rot(90, 0, 0) * Cylinder(clear_d / 2, flange_t + 0.4)
            feats[f"head_hole_{'l' if s > 0 else 'r'}{i + 1}"] = _axis([s * head_holes_x, fy1, z], [0, -1, 0], clear_d / 2)
        for i, x in enumerate(servo_holes[0]):
            for j, z in enumerate(servo_holes[1]):
                body -= Pos(s * x, servo_boss_top - insert_hole_depth / 2, z) * Rot(90, 0, 0) * Cylinder(insert_d / 2, insert_hole_depth + 0.01)
                feats[f"servo_insert_{'l' if s > 0 else 'r'}{i * 2 + j + 1}"] = _axis([s * x, servo_boss_top, z], [0, -1, 0], insert_d / 2)
    for i, z in enumerate(pillow_holes[1]):
        for s in (1, -1):
            body -= Pos(s * pillow_holes[0], sum(bridge_y) / 2, z) * Rot(90, 0, 0) * Cylinder(clear_d / 2, 4.4)
            feats[f"pillow_hole_{'l' if s > 0 else 'r'}{i + 1}"] = _axis([s * pillow_holes[0], bridge_y[1], z], [0, -1, 0], clear_d / 2)
    feats["bottom"] = _plane([0, y0, 0], [0, -1, 0])
    params = dict(width=width, front_z=front_z, back_z=back_z, flange_t=flange_t, deck_t=deck_t, wall=wall,
                  head_holes_x=head_holes_x, head_holes_z=list(head_holes_z), clear_d=clear_d,
                  servo_holes=[list(x) for x in servo_holes], insert_d=insert_d, insert_hole_depth=insert_hole_depth,
                  pillow_holes=[pillow_holes[0], list(pillow_holes[1])],
                  bridge_y=list(bridge_y))
    return Remodel("RX Head Mech Base Plate V4", params, body, feats, "RX Head Mech Base Plate V4.stl")


# ------------------------------------------------------------------ Visor Servo Mount

def visor_servo_mount(pattern=(48.0, 9.9), pattern_x0=0.65, pattern_y0=41.8, pocket=(20.0, 40.3), depth=20.0,
                      wall=6.1, height=70.3, bridge=(12.5, 22.0, 62.3), insert_d=INSERT_M4, insert_depth=6.0,
                      foot=(35.0, 6.0), slot=(4.5, 14.4, 28.75), gusset=7.0) -> Remodel:
    """A bridge that holds a standard servo on its flange: two uprights around the servo pocket,
    a bottom and a top bar carrying the four 6 mm insert holes on the servo's flange pattern,
    and two slotted feet. Squared frame: X across (feet), Y up (feet at y = 0), Z through the
    pocket (the flange face at z = depth). `library` maps it onto the reference STL."""
    from build123d import Box, Cylinder, Pos, Rot, SlotCenterToCenter, extrude, Plane

    px, py = pocket
    xin0, xin1 = pattern_x0 - px / 2, pattern_x0 + px / 2
    yb0, yb1, yt0 = bridge
    def slab(x0, x1, y0_, y1_, z0=0.0, z1=depth):
        return Pos((x0 + x1) / 2, (y0_ + y1_) / 2, (z0 + z1) / 2) * Box(x1 - x0, y1_ - y0_, z1 - z0)
    body = slab(xin0 - wall, xin0, foot[1], height) + slab(xin1, xin1 + wall, foot[1], height)
    body += slab(xin0 - wall, xin1 + wall, yb0, yb1) + slab(xin0 - wall, xin1 + wall, yt0, height)
    fx = foot[0]
    body += slab(-fx, xin0 - wall + 2.0, 0, foot[1]) + slab(xin1 + wall - 2.0, fx, 0, foot[1])
    # gussets: 45 deg chamfer blocks between the feet and the uprights
    for s, xw in ((-1, xin0 - wall), (1, xin1 + wall)):
        g = Pos(xw, foot[1], depth / 2) * Rot(0, 0, 45) * Box(gusset * 1.414, gusset * 1.414, depth)
        body += g & slab(min(xw, xw + s * gusset), max(xw, xw + s * gusset), foot[1], foot[1] + gusset)
    # feet slots (along Z)
    sw, sl, sx = slot
    for s in (-1, 1):
        body -= Pos(s * sx, foot[1] / 2, depth / 2) * Rot(90, 0, 0) * (
            Box(sw, sl - sw, foot[1] + 1) + Pos(0, (sl - sw) / 2, 0) * Cylinder(sw / 2, foot[1] + 1)
            + Pos(0, -(sl - sw) / 2, 0) * Cylinder(sw / 2, foot[1] + 1))
    feats = {}
    k = 0
    for dy in (-pattern[0] / 2, pattern[0] / 2):
        for dx in (-pattern[1] / 2, pattern[1] / 2):
            x, y = pattern_x0 + dx - 0.35, pattern_y0 + dy
            body -= Pos(x, y, depth - insert_depth / 2) * Cylinder(insert_d / 2, insert_depth + 0.01)
            k += 1
            feats[f"boss{k}"] = _axis([x, y, depth], [0, 0, -1], insert_d / 2)
    feats["face"] = _plane([pattern_x0, pattern_y0, depth], [0, 0, 1])
    params = dict(pattern=list(pattern), pocket=list(pocket), depth=depth, wall=wall, height=height,
                  bridge=list(bridge), insert_d=insert_d, insert_depth=insert_depth, foot=list(foot), slot=list(slot))
    return Remodel("Visor Servo Mount", params, body, feats, "Visor Servo Mount.stl")


REMODELS.update({"base_plate": base_plate, "visor_servo_mount": visor_servo_mount})
