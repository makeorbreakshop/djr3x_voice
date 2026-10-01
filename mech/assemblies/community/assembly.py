"""Community additions to the R-3X Animation build (vendored under mech/vendor/dropbox/community/,
never committed): Sam Morton's 2020 lower cage (default structural frame), Riane Randall's
printed frame (alternate), Trevor Zaharichuk's Mic-Mouth-Split (alternate mouth).

Morton's and Trevor's files are exported in the kit's own assembled frame (Y up, mm, display
pose), so they take the kit's static/head yaw exactly like kit parts. Randall's frame is the
kit frame with Z up (heights 8 .. 326.6 match the pedestal stack) and is placed the same way.
"""
from __future__ import annotations

import numpy as np
import trimesh

from r3xmech import frames
from r3xmech.frames import ZUP, roty, trans
from r3xmech.model import MECH, Asm, Link, Part

C = MECH / "vendor/dropbox/community"
MORTON = C / "Sam Morton - Files"
RANDALL = C / "Riane Randall - Files"
TREVOR = C / "Trevor Zaharichuk - Files/Mic-Mouth-Split/Mic-Mouth-Split"

STATIC = roty(frames.KIT_YAW_DEG["static"])
HEAD = roty(frames.KIT_YAW_DEG["head"])


def extrusion_2020(length):
    """20 x 20 mm extrusion as a solid box along +Y from y=0 (mass: 0.50 g/mm)."""
    def g():
        m = trimesh.creation.box((20, length, 20))
        m.apply_translation([0, length / 2, 0])
        return m
    return g


def _p(id_, name, cls, T, file=None, **kw):
    kw.setdefault("origin", "community")
    kw.setdefault("placement", "kit")
    kw.setdefault("material", "PETG" if cls == "mech" else "aluminium")
    kw.setdefault("printed", cls == "mech")
    kw.setdefault("kind", "stl" if file is not None else "generated")
    return Part(id=id_, name=name, cls=cls, link=kw.pop("link", "frame"), T=T, file=file, **kw)


def morton_frame() -> Asm:
    lc = MORTON / "Morton Lower Cage"
    a = Asm(id="morton_frame", name="Sam Morton 2020 lower cage (default frame)", mount_link="base",
            variant={"group": "base_frame", "id": "morton", "default": True},
            description="Lower Frame (y 35-73, takes the place of the R-3X stage ring), Upper Frame inside the "
                        "pedestal (y 209-237), Henley B_M_3 mod in place of P_M_3, seven 2020 extrusions on T-nuts.",
            links=[Link("frame", "Frame (static)", None)])
    y_lf_top, y_uf_top, y_skid = 73.2, 236.7, -48.0
    a.parts += [
        _p("morton_lower_frame", "Morton Lower Frame (replaces the R-3X stage ring)", "mech", STATIC,
           lc / "Lower Frame.stl", replaces=["stage_ring"],
           evidence="kit frame; y 35.1-73.2 = the R-3X stage ring slot (38 mm); the pan sector sits on it on "
                    "tube standoffs (Morton readme)"),
        _p("morton_upper_frame", "Morton Upper Frame", "mech", STATIC, lc / "Upper Frame.stl"),
        _p("henley_b_m_3_mod", "HENLEY B_M_3_mod v7 (replaces pedestal top P_M_3)", "mech", STATIC,
           lc / "HENLEY B_M_3_mod v7.stl", replaces=["p_m_3"], evidence="same slot as P_M_3: y 322-342, R110"),
    ]
    # posts: 4 lower at R147 on the kit axes (Lower Frame sockets), 3 upper at R75.3 (Upper Frame sockets)
    lower = [(0.1, 146.9), (-0.3, -147.1), (-147.0, 0.1), (147.1, -0.1)]
    upper = [(1.0, -75.4), (63.5, 40.5), (-63.5, 40.5)]
    for k, (x, z) in enumerate(lower):
        L = y_lf_top - y_skid
        a.parts.append(_p(f"morton_post_low_{k + 1}", f"2020 extrusion {L:.0f} mm (lower post)", "hardware",
                          STATIC @ trans(x, y_skid, z), None, generator=extrusion_2020(L), mass_g=0.5 * L,
                          placement="fitted", evidence="Lower Frame 20.4 mm socket centre",
                          inferred=True, inferred_note="post runs from the skid plate (y -48) to the frame top; "
                                                       "exact length not stated"))
    for k, (x, z) in enumerate(upper):
        ang = np.degrees(np.arctan2(x, z)) - 180.0
        L = y_uf_top - y_lf_top
        a.parts.append(_p(f"morton_post_up_{k + 1}", f"2020 extrusion {L:.0f} mm (upper post)", "hardware",
                          STATIC @ trans(x, y_lf_top, z) @ roty(ang), None, generator=extrusion_2020(L),
                          mass_g=0.5 * L, placement="fitted", evidence="Upper Frame socket centre (R75.3)",
                          inferred=True, inferred_note="stands on the Lower Frame (LBracket), top flush with the Upper Frame"))
        a.parts.append(_p(f"morton_lbracket_{k + 1}", "Morton LBracket", "mech", STATIC @ roty(ang),
                          lc / "LBracket.stl", placement="fitted" if k == 0 else "inferred",
                          evidence="file sits at the (0,-75) post" if k == 0 else "copied to the other posts",
                          inferred=k > 0, inferred_note="rotated copy" if k else ""))
    a.bom += [
        {"key": "extrusion-2020", "item": "2020 aluminium extrusion (7 pieces; Morton readme)", "qty": 7,
         "category": "hardware", "inferred": False, "inferred_note": ""},
        {"key": "t-nut-m5-2020", "item": "T-nuts for 2020 (Morton: 'a bunch')", "qty": 28, "category": "fastener",
         "inferred": True, "inferred_note": "4 per post assumed"},
    ]
    a.notes += ["SkidPlate Outer.stl is not placed: at its file height (y -53..-48) it cuts through the pan/lift "
                "stage under either reading of the stage height; Gil-Drive base plate is in a separate frame.",
                "2020Mount.stl is not placed: its file frame does not match the kit frame (y -76..-61).",
                "Morton readme: standoffs from aluminium tube raise the big head-turn gear (the pan sector) to "
                "height; a nut under the lazy susan leaves a wire chase; the rail extrusion is cut short for "
                "clearance (millimetres otherwise); Henley B_M_3 replaces the pedestal top."]
    return a


def randall_frame() -> Asm:
    rf = RANDALL / "R3X_Frame/R3X_Frame"
    a = Asm(id="randall_frame", name="Riane Randall printed frame - reference (not engineered)", mount_link="base",
            variant={"group": "base_frame", "id": "randall", "default": False, "reference": True},
            links=[Link("frame_randall", "Frame (static)", None)])
    T = STATIC @ ZUP
    for n in ("cagebottom", "frame_mid", "frame_top"):
        a.parts.append(_p(f"randall_{n}", f"Randall {n}", "mech", T, rf / f"{n}.stl", link="frame_randall",
                          placement="inferred", inferred=True,
                          inferred_note="Z-up file read as the kit frame (heights 8..326.6 match the pedestal)"))
    for k in range(4):
        a.parts.append(_p(f"randall_bracket_{k + 1}", "Randall bracket", "mech", T @ frames.rot((0, 0, 1), 90 * k),
                          rf / "bracket_x4.stl", link="frame_randall", placement="inferred", inferred=True,
                          inferred_note="x4 copies at 90 deg"))
    return a


def mouth_split_parts(link="head"):
    """Trevor's holed grille + light pipe + back mount; build_r3x.py's fit: y' = 1471.3 - y, z' = -z,
    grille extent = kit H_M_1 (both 83.5 mm tall)."""
    flip = np.array([[1, 0, 0, 0], [0, -1, 0, 1471.3], [0, 0, -1, 0], [0, 0, 0, 1]], float)
    out = []
    for n, id_ in (("Grill", "mouth_grill"), ("LightPipe", "mouth_light_pipe"), ("BackMount", "mouth_back_mount")):
        out.append(_p(id_, f"Mic-Mouth-Split {n} (replaces H_M_1)", "mech", HEAD @ flip, TREVOR / f"{n}.stl",
                      link=link, material="PLA", placement="fitted", replaces=["h_m_1"],
                      evidence="sim/model/build_r3x.py MOUTH_FLIP_Y fit", exposed=n != "BackMount"))
    return out
