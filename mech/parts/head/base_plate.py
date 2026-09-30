"""RX Head Mech Base Plate V4 - the head's mount plate (Hunter Smoke), as design intent.

Frame = the reference STL's (mech/vendor/hunter_head/RX Head Mech Base Plate V4.stl): +Y up
(the servos' side), +Z forward, X across; the flange underside at y = 24.96. Hunter's STEP
(`Head Joint Asm.step`, entity Head_Mounting_Plate) is the same solid to 0.03 mm, and every
default below is read off its B-rep faces, not fitted to the mesh.

Features, in build order:
  1. hat      - a 4 mm bent "hat" section (two flanges, two walls leaning 61 deg, the top the
                pillow blocks bolt under), extruded front-to-back; r5 fillets on both top bends.
  2. flanges  - the six M4 clearance holes onto the head bottom's inserts (120 x 57.5 grid);
                r5 fillets on the two front outer corners.
  3. deck     - the 4 mm rear deck the servo cradles stand on: a circular back edge, a r12.5
                notch at the front for the neck post, r5 fillets where it meets the front edge.
  4. rib      - the joggle between the flange level and the deck (the deck's front strip dropped
                to the flange underside), r5 round on its top front edge.
  5. bridge   - the hat top: a 30 x 45 window, four M4 clearance holes on the pillow blocks'
                32 x 57.5 pattern.
  6. cradles  - per servo: a pocket sized by the servo catalogue, an inner wall and two bars
                (r5 top corner), an outer post with an arched cable tunnel, 45 deg gussets, r5
                foot fillets, and four heat-set insert holes (drill-pointed) on the servo's
                flange pattern.

    from parts.head import base_plate
    p = base_plate.make()                         # Hunter's V4
    p = base_plate.make(fit=0.2)                  # every hole and pocket opened 0.2 mm
    p = base_plate.make(servo="ds3218", servo_pattern_w=None)   # pocket/bosses for a DS3218
"""

from __future__ import annotations

import math

from ._common import Print, finish, hole_d, hole_features, plane
from .servos import servo as servo_spec

REFERENCE = "RX Head Mech Base Plate V4.stl"
LABEL = "RX Head Mech Base Plate V4 (parametric)"

DEFAULTS = dict(
    # print fit, mm added to every hole diameter and to the servo pocket (both ways)
    fit=0.0,
    # --- datums (reference frame, mm)
    y0=24.96,                # flange underside (seats on the head bottom's bosses)
    flange_t=4.0,            # flange thickness
    half_width=75.0,         # plate half-width (flange/deck outer edge)
    front_z=40.0,            # front edge
    flange_back_z=-40.0,     # back of the flange pads = back of the joggle rib
    flange_corner_r=5.0,     # front outer corners of the flange pads
    # --- hat (the pillow-block bridge)
    hat_top_y=56.11,         # top face of the hat
    hat_t=4.0,               # top and wall thickness
    hat_top_half=30.0,       # outer top edge, half-width (before the bend fillet)
    hat_base_half=45.0,      # outer wall foot on the flange top, half-width (-> 61.1 deg walls)
    hat_bend_r=5.0,          # both bends at the top
    hat_back_z=-55.0,        # back end of the hat (meets the servo cradles' front bars)
    window=(30.0, 45.0),     # bridge window, X x Z
    # --- deck
    deck_y=32.26,            # deck underside
    deck_t=4.0,
    deck_front_z=-35.0,      # front edge of the deck (and of the joggle rib)
    deck_back_z=-106.5,      # apex of the circular back edge
    deck_back_r=109.309,     # its radius
    notch_r=12.5,            # neck-post notch at the deck's front edge
    notch_fillet_r=5.0,
    rib_round_r=5.0,         # round on the joggle rib's top front edge
    # --- fasteners
    bolt="M4",
    head_holes_x=60.0,       # 6 x M4 clearance onto the head bottom's inserts, at x = +/-60
    head_holes_pitch=28.74,  # ... and z = 0, +/-pitch
    pillow_pattern=(32.0, 57.48),   # 4 x M4 clearance, the pillow blocks' posts (X x Z)
    # --- servo cradles
    servo="gobilda_2000",    # servos.SERVOS key: sizes the pocket and the insert pattern
    servo_pattern_w=9.9,     # Hunter's as-drawn flange pitch across (goBILDA's own is 10.0); None = catalogue
    servo_x=40.0,            # pocket centre, |x|
    servo_z=-65.0,           # pocket centre, z
    boss_top_y=64.26,        # servo flange seat (the bosses' top)
    bar_t=4.0,               # front/back bars
    wall_t=10.0,             # inner wall and outer post, along X
    boss_round_r=5.0,        # bars' outer top corner
    gusset_in=5.0,           # 45 deg gusset, inner wall foot
    gusset_bar=10.0,         # 45 deg gussets, bar and post feet
    foot_fillet_r=5.0,       # bars' front/back feet into the deck
    tunnel=(18.0, 20.0, 5.0),  # arched cable tunnel through each outer post: width (Z), height, corner r
    insert_depth=6.0,        # heat-set insert hole depth (+ a 118 deg drill point)
)


def make(params: dict | None = None, **kw):
    """Hunter's V4 base plate from named parameters (see DEFAULTS; mm)."""
    from build123d import (Align, Axis, Box, BuildLine, BuildPart, BuildSketch, Circle, Cone, Cylinder,
                           Locations, Mode, Plane, Polyline, Pos, Rectangle, Rot, extrude, fillet,
                           make_face, mirror)

    P = dict(DEFAULTS)
    P.update(params or {})
    P.update(kw)
    unknown = set(P) - set(DEFAULTS)
    if unknown:
        raise TypeError(f"unknown base_plate parameters: {sorted(unknown)}")
    fit = P["fit"]
    sv = servo_spec(P["servo"])
    pat_l = sv["pattern_l"]
    pat_w = P["servo_pattern_w"] if P["servo_pattern_w"] is not None else sv["pattern_w"]
    pocket_l, pocket_w = sv["body_l"] + 2 * fit, sv["body_w"] + 2 * fit

    y0, ft, hw = P["y0"], P["flange_t"], P["half_width"]
    fy = y0 + ft
    zf, zb = P["front_z"], P["flange_back_z"]
    ty, t = P["hat_top_y"], P["hat_t"]
    dy0, dt = P["deck_y"], P["deck_t"]
    dy1 = dy0 + dt
    zd = P["deck_front_z"]
    by = P["boss_top_y"]

    # ------------------------------------------------ 1. the hat section (sketch XY, extruded along Z)
    # outer wall line: (hat_base_half, flange top) -> (hat_top_half, hat top); inner = offset by t
    bx, tx = P["hat_base_half"], P["hat_top_half"]
    dx, dyw = bx - tx, ty - fy
    L = math.hypot(dx, dyw)
    nx, ny = dyw / L, dx / L                      # outward normal of the right wall
    c_out = nx * bx + ny * fy
    c_in = c_out - t

    def x_on(c, y):                               # x of a wall line at height y
        return (c - ny * y) / nx

    prof = [(hw, y0), (hw, fy), (bx, fy), (tx, ty), (-tx, ty), (-bx, fy), (-hw, fy), (-hw, y0),
            (-x_on(c_in, y0), y0), (-x_on(c_in, ty - t), ty - t), (x_on(c_in, ty - t), ty - t), (x_on(c_in, y0), y0)]
    hat_len = zf - P["hat_back_z"]
    with BuildPart() as bp:
        with BuildSketch(Plane.XY.offset(P["hat_back_z"])) as sk:
            with BuildLine():
                Polyline(*prof, close=True)
            make_face()
            bends = [(tx, ty), (x_on(c_in, ty - t), ty - t)]      # outer and inner top bends
            fillet([v for v in sk.vertices() if any(abs(abs(v.X) - bx_) < 1e-6 and abs(v.Y - by_) < 1e-6
                                                     for bx_, by_ in bends)], P["hat_bend_r"])
        extrude(amount=hat_len)
        # the flange only exists ahead of the rib: under the deck it is cut away
        with Locations((0, (y0 - 1 + dy0) / 2, (P["hat_back_z"] - 1 + zb) / 2)):
            Box(2 * hw + 2, dy0 - y0 + 1, zb - P["hat_back_z"] + 1, mode=Mode.SUBTRACT)
        # r5 on the flange pads' front outer corners (vertical edges at x = +/-hw, z = front)
        fillet(bp.edges().filter_by(Axis.Y).filter_by(lambda e: abs(abs(e.center().X) - hw) < 1e-3
                                                      and abs(e.center().Z - zf) < 1e-3), P["flange_corner_r"])
    hat = bp.part

    # ------------------------------------------------ 3/4. deck + joggle rib (one plan sketch, two heights)
    # sketch plane at y: local (u, v) = (x, -z)
    def plane_y(y):
        return Plane(origin=(0, y, 0), x_dir=(1, 0, 0), z_dir=(0, 1, 0))

    cz = P["deck_back_z"] + P["deck_back_r"]      # the back arc's centre (z)

    def deck_face(z_back_limit=None):
        with BuildSketch(plane_y(0)) as s:
            zlo = P["deck_back_z"] - 1 if z_back_limit is None else z_back_limit
            with Locations((0, -(zd + zlo) / 2)):
                Rectangle(2 * hw, zd - zlo)
            if z_back_limit is None:
                with Locations((0, -cz)):
                    Circle(P["deck_back_r"], mode=Mode.INTERSECT)
            with Locations((0, -zd)):
                Circle(P["notch_r"], mode=Mode.SUBTRACT)
            corners = [v for v in s.vertices() if abs(v.Y + zd) < 1e-6 and abs(abs(v.X) - P["notch_r"]) < 1e-6]
            fillet(corners, P["notch_fillet_r"])
        return s.sketch_local

    deck_sk = deck_face()
    deck = extrude(plane_y(dy0) * deck_sk, amount=dt)
    # the rib: the deck's front strip [flange_back_z, deck_front_z], from the flange underside up
    strip = deck_sk & (Pos(0, -(zd + zb) / 2) * Rectangle(2 * hw + 2, zd - zb))
    rib = extrude(plane_y(y0) * strip, amount=dy1 - y0)
    body = hat + deck + rib
    # r5 on the rib's top front edge, outboard of the hat walls (inside the hat it stays square)
    xw = x_on(c_out, dy1)
    rr = [e for e in body.edges().filter_by(Axis.X)
          if abs(e.center().Y - dy1) < 1e-3 and abs(e.center().Z - zd) < 1e-3 and abs(e.center().X) > xw]
    body = fillet(rr, P["rib_round_r"])

    # ------------------------------------------------ 6. servo cradles (profiles in XY, extruded along Z)
    sx0 = P["servo_x"] - pocket_l / 2             # pocket inner x (inner wall's outer face)
    sx1 = P["servo_x"] + pocket_l / 2             # pocket outer x (outer post's inner face)
    zc = P["servo_z"]
    zpf, zpb = zc + pocket_w / 2, zc - pocket_w / 2  # pocket front/back faces
    bt, wt = P["bar_t"], P["wall_t"]
    gi, gb = P["gusset_in"], P["gusset_bar"]
    # the walls' outside faces stay where the nominal (catalogue) pocket puts them: the print fit
    # opens the pocket into the walls, it does not move them
    xi = P["servo_x"] - sv["body_l"] / 2 - wt      # inner wall's inner face
    xo = P["servo_x"] + sv["body_l"] / 2 + wt      # outer post's outer face

    def prism(pts, z0, z1, round_vertex=None):
        with BuildSketch(Plane.XY.offset(z0)) as s:
            with BuildLine():
                Polyline(*pts, close=True)
            make_face()
            if round_vertex is not None:
                fillet([v for v in s.vertices() if abs(v.X - round_vertex[0]) < 1e-6 and abs(v.Y - round_vertex[1]) < 1e-6],
                       P["boss_round_r"])
        return extrude(s.sketch, amount=z1 - z0)

    bar_x1 = xi + 2 * wt                           # the bars' outer top corner (Hunter: x 30)
    # the bar profiles (x > 0 side). The back bar has a 45 deg gusset on both feet; the front bar's
    # outer side runs down the hat's inner wall face instead (it is merged into the hat there).
    y_meet = (c_in - nx * bar_x1) / ny             # where x = bar_x1 meets the hat's inner wall face
    back_bar = [(xi - gi, dy1), (xi, dy1 + gi), (xi, by), (bar_x1, by), (bar_x1, dy1 + gb), (bar_x1 + gb, dy1)]
    front_bar = [(xi - gi, dy1), (xi, dy1 + gi), (xi, by), (bar_x1, by), (bar_x1, y_meet), (x_on(c_in, dy1), dy1)]
    inner_wall = [(xi - gi, dy1), (xi, dy1 + gi), (xi, by), (sx0, by), (sx0, dy1)]
    post_prof = [(sx1 - gb, dy1), (sx1, dy1 + gb), (sx1, by), (xo, by), (xo, dy1)]
    zf_bar, zb_bar = zc + sv["body_w"] / 2 + bt, zc - sv["body_w"] / 2 - bt   # the bars' outer faces (nominal)

    cradle = prism(front_bar, zpf, zf_bar, round_vertex=(bar_x1, by))
    cradle += prism(back_bar, zb_bar, zpb, round_vertex=(bar_x1, by))
    cradle += prism(inner_wall, zb_bar, zf_bar)
    post = prism(post_prof, zb_bar, zf_bar)
    tw, th, tr = P["tunnel"]
    with BuildSketch(Plane.YZ.offset(sx1 - gb - 1)) as ts:      # local (u, v) = (y, z)
        with Locations(((dy1 - 1 + dy1 + th) / 2, zc)):
            Rectangle(th + 1, tw)
        fillet([v for v in ts.vertices() if abs(v.X - (dy1 + th)) < 1e-6], tr)
    post -= extrude(ts.sketch, amount=xo - (sx1 - gb) + 2)
    cradle += post
    # r5 foot fillets where the bars' outer faces meet the deck top, bounded by each bar's own
    # profile (so they die out up the gussets, as Hunter's do). OCC fillets these one at a time
    # but not together, so each is built as its fillet section (r x r less the quarter circle)
    # swept along the foot, then clipped to the bar profile.
    fr = P["foot_fillet_r"]
    deck_plan = extrude(plane_y(dy0) * deck_sk, amount=by - dy0)
    for prof, zface, sgn in ((front_bar, zf_bar, 1), (back_bar, zb_bar, -1), (post_prof, zf_bar, 1), (post_prof, zb_bar, -1)):
        xs = [p[0] for p in prof]
        lo, hi = min(xs), max(xs)
        cove = Pos((lo + hi) / 2, dy1 + fr / 2, zface + sgn * fr / 2) * Box(hi - lo, fr, fr)
        cove -= Pos((lo + hi) / 2, dy1 + fr, zface + sgn * fr) * Rot(0, 90, 0) * Cylinder(fr, hi - lo + 1)
        cove &= prism(prof, min(zface, zface + sgn * fr) - 0.5, max(zface, zface + sgn * fr) + 0.5)
        cove &= deck_plan                          # and to the deck's outline (the back arc)
        cradle += cove
    cradles = cradle + mirror(cradle, Plane.YZ)
    body = body + cradles

    # ------------------------------------------------ 2/5/6. holes (hole features, drilled from their entry face)
    feats: dict = {}
    bolt = P["bolt"]
    d_clear = hole_d(bolt, "clearance", fit)
    d_ins = hole_d(bolt, "heatset", fit)

    def through_y(x, z, ytop, depth):
        return Pos(x, ytop - depth / 2, z) * Rot(90, 0, 0) * Cylinder(d_clear / 2, depth + 0.02)

    # 2. head holes: 6 x clearance through the flange, entered from the top (screws go down)
    heads = sorted([(sx * P["head_holes_x"], k * P["head_holes_pitch"]) for sx in (-1, 1) for k in (-1, 0, 1)])
    for i, (x, z) in enumerate(heads):
        body -= through_y(x, z, fy, ft)
        hole_features(feats, f"fl{i + 1}", (x, fy, z), (0, -1, 0), d_clear / 2, depth=ft, bolt=bolt, kind="clearance")
    # 5. bridge window and pillow-block holes
    ww, wz = P["window"]
    body -= Pos(0, ty - t / 2, 0) * Box(ww, t + 0.2, wz)
    pw, pz = P["pillow_pattern"]
    pillows = sorted([(sx * pw / 2, sz * pz / 2) for sx in (-1, 1) for sz in (-1, 1)])
    for i, (x, z) in enumerate(pillows):
        body -= through_y(x, z, ty, t)
        hole_features(feats, f"pb{i + 1}", (x, ty, z), (0, -1, 0), d_clear / 2, depth=t, bolt=bolt, kind="clearance")
    # 6. servo inserts: blind, 118 deg drill point
    depth = P["insert_depth"]
    tip = (d_ins / 2) / math.tan(math.radians(59))
    ins = sorted([(s * (P["servo_x"] + dxp), zc + dzp) for s in (-1, 1)
                  for dxp in (-pat_l / 2, pat_l / 2) for dzp in (-pat_w / 2, pat_w / 2)])
    for i, (x, z) in enumerate(ins):
        drill = Pos(x, by - depth / 2 + 0.01, z) * Rot(90, 0, 0) * Cylinder(d_ins / 2, depth + 0.02)
        drill += Pos(x, by - depth, z) * Rot(90, 0, 0) * Cone(d_ins / 2, 0, tip, align=(Align.CENTER, Align.CENTER, Align.MIN))
        body -= drill
        hole_features(feats, f"sins{i + 1}", (x, by, z), (0, -1, 0), d_ins / 2, depth=depth, bolt=bolt, kind="heatset")

    feats["underside"] = plane((0, y0, 0), (0, -1, 0))
    feats["top"] = plane((0, ty, 0), (0, 1, 0))
    feats["boss_top_l"] = plane((P["servo_x"], by, zc), (0, 1, 0))
    feats["boss_top_r"] = plane((-P["servo_x"], by, zc), (0, 1, 0))
    for s, sx in (("l", 1), ("r", -1)):
        feats[f"pocket_{s}"] = {"type": "axis", "p": [sx * P["servo_x"], by, zc], "d": [0.0, -1.0, 0.0],
                                "r": 0.0, "size": [pocket_l, pocket_w]}
    feats["roll_axis"] = {"type": "axis", "p": [0.0, (ty - t) - 0.0, 0.0], "d": [0.0, 0.0, 1.0], "r": 0.0}

    return finish(body, label=LABEL, params=P, features=feats, reference=REFERENCE,
                  printability=Print("flange underside (y0) on the bed, cradles up", "underside", True,
                                     "the hat's leaning walls self-support (29 deg from vertical) but the bridge top "
                                     "(y 52-56) and the cradle posts' tunnels span open air: supports under the bridge "
                                     "and in the tunnels, or print top-down on the hat's top face with supports "
                                     "under the flanges and deck"))
