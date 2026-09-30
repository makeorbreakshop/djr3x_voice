"""Anderson's insert pocket as drawn: a blind pocket for a short heat-set insert with a narrower
relief beyond it for the screw's tip (and optionally a 45 deg lead-in at the mouth). Any other hole
type goes through the shared `cut_hole`."""

from __future__ import annotations

from parts.head._common import INSERTS, cut_hole, hole_features


def insert_pocket(body, feats: dict, name: str, entry, d_into, *, pd: float, pdep: float, rd: float, rdep: float,
                  bolt: str, insert_key: str, hole_type: str = "heat_set", insert: str | None = None,
                  fit: float = 0.0, lead_in: float = 0.0):
    """`rdep` is the total depth (pocket + relief) from `entry`; `lead_in` a 45 deg countersink of
    that depth at the mouth (the pocket's diameter grows by 2 x lead_in there)."""
    from build123d import Align, Cone, Cylinder, Plane

    e = tuple(float(v) for v in entry)
    d = tuple(float(v) for v in d_into)

    def at(t):
        return Plane(origin=tuple(e[i] + d[i] * t for i in range(3)), z_dir=d).location

    base = (Align.CENTER, Align.CENTER, Align.MIN)
    if hole_type != "heat_set" or insert:
        return cut_hole(body, feats, name, e, d, bolt, hole_type, pdep, fit, insert=insert, through=rdep)
    r = (pd + fit) / 2
    body -= at(-0.01) * Cylinder(r, pdep + 0.01, align=base)
    if lead_in:
        body -= at(-0.01) * Cone(r + lead_in + 0.01, r, lead_in + 0.01, align=base)
    if rdep > pdep:
        body -= at(pdep - 0.01) * Cylinder((rd + fit) / 2, rdep - pdep + 0.01, align=base)
    hole_features(feats, name, e, d, r, depth=pdep, bolt=bolt, kind="heat_set")
    feats[f"hole_{name}"]["insert"] = INSERTS[insert_key]["source"]
    if lead_in:
        feats[f"hole_{name}"]["lead_in"] = lead_in
    if rdep <= pdep:
        return body
    relief = tuple(e[i] + d[i] * pdep for i in range(3))
    hole_features(feats, f"{name}_relief", relief, d, (rd + fit) / 2, depth=rdep - pdep, bolt=bolt, kind="clearance")
    return body
