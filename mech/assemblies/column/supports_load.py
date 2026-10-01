"""What the column's ring plates carry, and the brackets' slip margin (a quick estimate, not the suite).

    cd mech && .venv/bin/python -m assemblies.column.supports_load

Masses: r3xmech.checks.part_mass (the kit's printed parts at their effective fill, catalogue masses
for servos and purchased parts), grouped by the link each part rides; superseded kit parts left out.
A plate's four brackets (_layout.SUPPORT / BRACKET) share the weight, and the moment of its centre
of mass about the column's axis, as a rigid plate on four points; each bracket holds on one M4 into
a drop-in T-nut by friction (_layout.CLAMP, inferred). Dynamic: x2 (the rings' a_max, a bump).
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

G = 9.81
LAZY_SUSAN_KG = 0.7   # a 10 in steel lazy susan (inferred: catalogue range 0.5-0.9 kg)
GROUPS = {
    "core": ("lower_ring_mount", "lower_ring", "poker_upper", "poker_hand", "middle_ring", "throttle_upper",
             "throttle_fore", "throttle_hand"),
    "top": ("top_ring_mount", "top_ring", "hero_arm", "hero_hand"),
}


def estimate():
    from assemblies.kit.assembly import build_model
    from parts.column import _layout as L
    from parts.column.support import bracket_points
    from r3xmech.checks import part_mass

    root = build_model()
    sup = {r for p in root.all_parts() for r in p.replaces}
    out = {}
    for lv, links in GROUPS.items():
        m_tot, mom = 0.0, np.zeros(3)
        by = {}
        for a in root.walk():
            v = getattr(a, "variant", None)
            if v and v.get("group") == "internals" and v.get("id") != "column":
                continue
            for p in a.parts:
                if p.link not in links or p.id in sup:
                    continue
                m, com, _ = part_mass(p)            # com in the part's own frame
                m /= 1000.0
                m_tot += m
                mom += m * (np.asarray(p.T) @ np.append(np.asarray(com, float), 1.0))[:3]
                by[p.link] = by.get(p.link, 0.0) + m
        n_ls = 2 if lv == "core" else 1
        m_tot += n_ls * LAZY_SUSAN_KG
        com = mom / m_tot   # the lazy susans on the axis add mass, no moment
        W = m_tot * G
        pts = np.array(bracket_points(lv))
        F = W / 4 + W * (com[0] * pts[:, 0] / np.sum(pts[:, 0] ** 2) + com[2] * pts[:, 1] / np.sum(pts[:, 1] ** 2))
        slip = L.CLAMP["mu"] * L.CLAMP["preload_n"]
        out[lv] = dict(kg=round(m_tot, 2), by_link={k: round(v, 2) for k, v in sorted(by.items())},
                       lazy_susans_kg=n_ls * LAZY_SUSAN_KG, com_xz=[round(float(com[0]), 1), round(float(com[2]), 1)],
                       weight_n=round(W, 1), bracket_n=[round(float(f), 1) for f in F], slip_n=slip,
                       margin_static=round(slip / max(F), 1), margin_dynamic=round(slip / (2 * max(F)), 1),
                       margin_all_four=round(4 * slip / (2 * W), 1))
    return out


if __name__ == "__main__":
    import json

    print(json.dumps(estimate(), indent=1))
