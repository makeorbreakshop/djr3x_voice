"""Servo catalogue for the parts that hold servos (pockets, flange bosses). Dimensions in mm.

    body_l, body_w   the case below the flange (the pocket it drops into)
    pattern_l/_w     flange mounting-hole pitch along / across the case
    flange_l         flange tip-to-tip length
    spline_off       output spline centre from the case centre, along the long side
    hole_d           flange hole diameter (the screw's clearance through the flange)

`gobilda_2000` is measured from goBILDA's own STEP (mech/vendor/parts_cad/gobilda/
2000-0025-0002.step): case 40 x 20, flange 54.3, holes 4.5 on 48 x 10.0. Hunter's V4 plate
drew the pattern 48 x 9.9 - the plate keeps that as its default (`servo_pattern_w`); the others
are datasheet values for the standard-size servos the R-3X builds use.
"""

SERVOS = {
    "gobilda_2000": dict(body_l=40.0, body_w=20.0, pattern_l=48.0, pattern_w=10.0, flange_l=54.3,
                         spline_off=10.0, hole_d=4.5, source="goBILDA 2000-0025-0002 STEP (measured)"),
    "ds3218": dict(body_l=40.0, body_w=20.0, pattern_l=49.5, pattern_w=10.0, flange_l=54.5,
                   spline_off=10.0, hole_d=4.3, source="DS3218 datasheet"),
    "mg996r": dict(body_l=40.7, body_w=19.7, pattern_l=49.5, pattern_w=10.0, flange_l=54.5,
                   spline_off=10.2, hole_d=4.3, source="MG996R datasheet"),
    "ds5160": dict(body_l=65.0, body_w=30.0, pattern_l=73.0, pattern_w=20.0, flange_l=84.0,
                   spline_off=17.0, hole_d=4.5, source="DS5160 60 kg datasheet"),
}


def servo(model: str) -> dict:
    try:
        return SERVOS[model]
    except KeyError:
        raise KeyError(f"servo model {model!r}: one of {sorted(SERVOS)}") from None
