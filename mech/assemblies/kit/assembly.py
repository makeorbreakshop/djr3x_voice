"""DJ R3X printable kit ("DJ R3X - v2", Patrick Gray / David Ferreira) as the droid's backbone.

Every Large Cut STL is exported assembled (Y-up, mm) in a display pose; the sim's model build
measured the yaw that takes each rigid subtree to the canonical rest (r3xmech.frames.KIT_YAW_DEG).
This module places every kit part, groups them into the guide's sub-assemblies, declares the
links and joints, and (when the guide transcription exists in `_guide/`) attaches the guide's
steps and hardware BOM. The R-3X Animation mechanisms are attached by
`assemblies/r3x_animation/assembly.py`.

The kit files are licensed group-only (CC BY-NC): they are read from mech/vendor/ (gitignored)
and nothing derived from their geometry is committed.

    from assemblies.kit.assembly import build
    root = build()          # r3xmech.model.Asm tree (canonical frame, rest pose)
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import numpy as np

from r3xmech import frames
from r3xmech.model import MECH, Asm, Joint, Link, Part

KIT = MECH / "vendor/kit/unz/DJ R3X - v2/STLs/Large Cut"
GUIDE = Path(__file__).resolve().parent / "_guide"

# Superseded or duplicated files (the kit ships some panels/brackets twice).
SKIP = [
    r"\(Old\)",                                          # superseded LS_M_Full
    r"Midsection - Middle/STLs/MS_P_[12]\.stl$",         # duplicates of Logic Panels/MS_P_*_Full
    r"Midsection - Middle/STLs/MS_LPI_",                 # duplicates of LED Board Mounts/MS_LPI_*
    r"Midsection - Middle/STLs/MS_DB - x7",              # = Logic Panels/MS_DB_1x8
    r"Midsection - Middle/STLs/MS_DB_M - x4",            # = Logic Panels/MS_DB_M_1x4
    r"Midsection - Middle/STLs/MS_L - x4",               # = Logic Panels/MS_L_1x4
]
# Exported once, printed four times at 90 deg about the vertical axis (as build_r3x.py does).
REPLICATE_X4 = ["B_M_D - x4", "B_SM - x4", "B_S_C - x4", "B_S_O - x4", "B_S_PO - x4", "LS_V_1 - x4"]

# (sub-assembly, stem regex). First match wins.
GROUPS = [
    ("base", r"^B_"), ("base", r"^P_"),
    ("lower_ring", r"^(LS_|PA_)"),
    ("middle_ring", r"^(MS_|LED_B_|TA_)"),
    ("top_ring", r"^(TR|RR_|RX-24|HA_)"),
    ("head", r"^H_"),
]

# Kit part -> link (deepest match wins, as build_r3x.assign_joint does).
LINK_RULES = [
    ("base", r"^(B_|P_)"),
    ("lower_ring", r"^(LS_|PA_M_)"),
    ("lower_ring_mount", r"^LS_IC_"),   # static core: inner race of the lower AND middle lazy susans (guide p20, p33)
    ("poker_upper", r"^(PA_B_|PA_W_|PA_F_)"),
    ("poker_hand", r"^(PA_W_|PA_F_)"),
    ("middle_ring", r"^(MS_|LED_B_|TA_S_1$)"),
    ("throttle_upper", r"^(TA_S_[2-5]$|TA_B_|TA_FA|TA_W)"),
    ("throttle_fore", r"^(TA_FA|TA_W)"),
    ("throttle_hand", r"^(TA_FA_1$|TA_W)"),
    ("top_ring", r"^(TR[-_]|RR_|RX-24$|HA_EB_|HA_[LR]E_1$|HA_PJ_)"),
    ("top_ring_mount", r"^TR-MR_SC"),   # the top lazy susan's lower-race carrier, hung in the middle ring (guide p52)
    ("hero_arm", r"^(HA_SB_|HA_SP_|HA_PL_|HA_P_1$|HA_PS_|HA_W_|HA_[LRT]F_)"),
    ("hero_hand", r"^(HA_W_1$|HA_[LRT]F_)"),
    ("head", r"^H_"),
    ("visor", r"^H_V_"),
]
LINK_SUBTREE_YAW = {  # which rest yaw applies to a link's kit geometry
    "base": "static", "lower_ring_mount": "torso_lower", "lower_ring": "torso_lower", "poker_upper": "torso_lower", "poker_hand": "torso_lower",
    "middle_ring": "torso_middle", "throttle_upper": "torso_middle", "throttle_fore": "torso_middle",
    "throttle_hand": "torso_middle", "top_ring": "torso_top", "top_ring_mount": "torso_top", "hero_arm": "torso_top",
    "hero_hand": "torso_top",
    "head": "head", "visor": "head",
}

# Paint and print per kit part: finish.json (shared with sim/model/build_r3x.py, the Original rig's paint).
FINISH = Path(__file__).resolve().parent / "finish.json"

# Kit shells that sit inside the droid, behind the logic panels: the light diffusers (MS_DB, MS_DB_M, MS_L),
# the LED board (LED_B) and its mounts (MS_LPI). The kit exports one of each of the repeated ones (x4..x9 in
# the file name) in one slot, so drawn on the Exterior they read as stray pieces in one window. Not exposed
# (manifest `exposed: false`), as the sim's Original model leaves them out (sim/model/build_r3x.py SKIP).
INTERIOR = r"^(MS_DB|MS_L_|MS_LPI_|LED_B_)"


def pid(stem: str, k: int | None = None) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", stem.lower()).strip("_")
    return s if k is None else f"{s}_{k}"


_FINISH: dict | None = None


def _finish_doc() -> dict:
    global _FINISH
    if _FINISH is None:
        d = json.loads(FINISH.read_text())
        norm = lambda m: {pid(k): v for k, v in m.items()}  # noqa: E731
        _FINISH = {"print": d["print"], "print_overrides": norm(d.get("print_overrides", {})),
                   "not_printed": norm(d.get("not_printed", {})), "paint": norm(d["paint"]),
                   "codes": {pid(k): k for k in d["paint"]}, "regions": norm(d.get("paint_regions", {}))}
    return _FINISH


def kit_finish(code: str) -> tuple[dict | None, bool]:
    """(manifest `finish`, printed) for a kit part code ("H_LEye_4", "TR-MR_SC_Full_Fixed", "rx_24"):
    finish.json's paint and print. (None, True) when the code is not in the table (the finish check fails)."""
    from workbench.finish import finish

    d = _finish_doc()
    k = pid(code)
    if k not in d["paint"]:
        return None, True
    printed = k not in d["not_printed"]
    pr = d["print_overrides"].get(k, d["print"]) if printed else None
    pr = {a: b for a, b in pr.items() if not a.startswith("_")} if pr else None
    f = finish(paint=d["paint"][k], print=pr, kit=d["codes"][k], note=d["not_printed"].get(k, ""))
    if k in d["regions"]:
        f["regions"] = [{a: b for a, b in r.items() if not a.startswith("_")} for r in d["regions"][k]]
    return f, printed


def _deepest(rules, name):
    hit = None
    for tag, pat in rules:
        if re.search(pat, name):
            hit = tag
    return hit


def kit_files():
    out = []
    for p in sorted(KIT.rglob("*.stl")):
        s = p.as_posix()
        if any(re.search(pat, s) for pat in SKIP):
            continue
        out.append(p)
    return out


def kit_T(link: str) -> np.ndarray:
    return frames.roty(frames.KIT_YAW_DEG[LINK_SUBTREE_YAW[link]])


def kit_parts() -> list[Part]:
    parts = []
    for f in kit_files():
        stem = f.stem
        clean = stem.replace(" (New)", "").replace(" (DNP)", "_DNP").replace(" - x4", "")
        link = _deepest(LINK_RULES, clean)
        if link is None:
            raise ValueError(f"kit part {stem} matches no link rule")
        fin, printed = kit_finish(clean)
        copies = 4 if any(stem.startswith(r) for r in REPLICATE_X4) else 1
        m = re.search(r"x(\d+)$", stem.replace(" ", ""))
        for k in range(copies):
            T = kit_T(link) @ frames.roty(90.0 * k)
            parts.append(Part(
                id=pid(clean, k + 1 if copies > 1 else None), name=stem + (f" #{k + 1}" if copies > 1 else ""),
                cls="shell", link=link, T=T, file=f, origin="kit", placement="kit",
                material="PLA" if printed else "rubber (floor mat)", printed=printed, finish=fin,
                note=("print qty in the file name: " + m.group(0)) if (m and copies == 1) else "",
                exposed=False if re.search(INTERIOR, clean) else None,
            ))
    return parts


# ---- joints measured on the kit (canonical frame) -------------------------------------
def _kit_pt(p, link):
    return tuple(np.round(frames.apply(kit_T(link), p), 2))


def _kit_dir(d, link):
    return tuple(np.round(frames.apply_dir(kit_T(link), d), 5))


def kit_arm_joints() -> dict[str, Joint]:
    """Arm joints of the poseable kit. Pivots/axes are kit-geometry measurements (hinge discs,
    hub bores) - the poker/throttle ones from build_r3x.py JOINTS, the hero ones re-measured
    here (r3xmech.kitmeasure) and equal to build_r3x's to 0.1 mm."""
    J = {}

    def rj(id_, name, parent, child, link, pivot, axis, lim, prof, drive_kind="none", **kw):
        J[id_] = Joint(id=id_, name=name, type="revolute", parent_link=parent, child_link=child,
                       pivot=_kit_pt(pivot, link), axis=_kit_dir(axis, link), limits=lim,
                       profile_joint=prof, drive={"kind": drive_kind}, **kw)

    rj("poker_shoulder", "Poker arm shoulder", "lower_ring", "poker_upper", "lower_ring",
       (-4.2, 372.0, 200.6), (1, 0, 0), (-45, 35), "poker_shoulder",
       evidence=["kit hub PA_M_1/PA_M_3 (build_r3x.py)"], confidence="medium")
    rj("poker_wrist", "Poker arm wrist", "poker_upper", "poker_hand", "lower_ring",
       (-8.0, 465.7, 328.0), (1, 0, 0), (-40, 40), "poker_wrist", evidence=["build_r3x.py"], confidence="medium")
    rj("throttle_shoulder", "Throttle arm shoulder", "middle_ring", "throttle_upper", "middle_ring",
       (-6.3, 448.6, -211.2), (0, 0, 1), (-50, 50), "throttle_shoulder",
       evidence=["kit shaft TA_S_1 axis (build_r3x.py)"], confidence="medium")
    rj("throttle_elbow", "Throttle arm elbow", "throttle_upper", "throttle_fore", "middle_ring",
       (-8.05, 242.1, -211.2), (0, 0, 1), (-60, 45), "throttle_elbow", evidence=["hubs TA_FA_I_3/I_4"], confidence="medium")
    rj("throttle_wrist", "Throttle arm wrist", "throttle_fore", "throttle_hand", "middle_ring",
       (-187.3, 321.6, -205.8), (0, 0, 1), (-60, 60), "throttle_wrist", evidence=["hubs TA_FA_I_1/I_2"], confidence="medium")
    return J


def _guide():
    f = GUIDE / "guide_steps.json"
    return json.loads(f.read_text()) if f.exists() else None


def attach_guide(asms: dict[str, Asm], parts_by_stem: dict[str, list[str]]):
    """Guide steps and hardware -> schema steps / unplaced fasteners / BOM, per sub-assembly.
    Fasteners are not located in 3D yet: every one is listed as `unplaced` under its step."""
    g = _guide()
    if g is None:
        for a in asms.values():
            a.notes.append("Guide steps not attached: run the guide transcription into assemblies/kit/_guide/.")
        return
    section_to_asm = {"base": "base", "lower_ring_poker_arm": "lower_ring", "middle_ring_throttle_arm": "middle_ring",
                      "top_ring_hero_arm": "top_ring", "head": "head"}
    desc = {}
    for sec in g.get("sections", []):
        for h in sec.get("hardware", []):
            desc[h.get("mcmaster")] = h.get("desc", "")
    for sec in g.get("sections", []):
        a = asms.get(section_to_asm.get(sec["id"], ""), None)
        if a is None:
            a = asms["base"]
            a.notes.append(f"guide section {sec['id']} not mapped to a sub-assembly")
        a.guide = {"title": g.get("source", "DJ R3X v2 Guide.pdf"), "pages": sec.get("pages")}
        for st in sec.get("steps", []):
            n = len(a.steps) + 1
            parts, foreign = [], []
            own = {p.id for p in a.parts}
            for stem in st.get("parts", []):
                for pid_ in parts_by_stem.get(stem, []):
                    (parts if pid_ in own else foreign).append(pid_)
            a.steps.append({
                "id": f"{a.id}_s{n:02d}", "n": n, "title": (st.get("notes") or "")[:80] or f"Step {st.get('n')}",
                "parts": parts, "fasteners": [],
                "unplaced": [{"key": f"mcmaster-{fz.get('mcmaster')}",
                              "spec": {"mcmaster": fz.get("mcmaster"), "desc": desc.get(fz.get("mcmaster"), "")},
                              "count": fz.get("qty"), "note": fz.get("where", "")}
                             for fz in st.get("fasteners", [])],
                "notes": [x for x in [st.get("notes")] + [f"consumables: {', '.join(st['consumables'])}"
                                                          if st.get("consumables") else None]
                          + [f"also uses (other sub-assembly): {', '.join(foreign)}" if foreign else None] if x],
                # the transcription's own short paraphrase (licensed, read here at build time, never committed)
                "text": st.get("notes") or "",
                "guide_page": st.get("page"), "subassembly": st.get("subassembly"),
                "inferred": False, "inferred_note": "",
            })
        for h in sec.get("hardware", []):
            a.bom.append({"key": f"mcmaster-{h.get('mcmaster')}", "item": h.get("desc", ""), "qty": h.get("qty"),
                          "category": "fastener", "spec": {"mcmaster": h.get("mcmaster")},
                          "source": f"https://www.mcmaster.com/{h.get('mcmaster')}", "inferred": False,
                          "inferred_note": ""})


# The droid's internals (a variant group, `internals`): "column" = our central column
# (assemblies/column, the default), "anderson_morton" = Anderson's base turntable, rack lift and
# long neck tube on Morton's 2020 cage. Both are built; the default is what the suite and rigsync use.
# R3X_INTERNALS=anderson_morton makes the other one the default.
INTERNALS = os.environ.get("R3X_INTERNALS", "column")


def build_model(with_r3x: bool = True, community: bool = True, internals: str | None = None) -> Asm:
    internals = internals or INTERNALS
    if internals not in ("column", "anderson_morton"):
        raise ValueError(f"internals {internals!r}: column | anderson_morton")
    parts = kit_parts()
    J = kit_arm_joints()

    root = Asm(id="r3x_droid", name="DJ R3X - our build (configurable)",
               description="The printable kit's shells as the backbone, with a pick of mechanisms per system (internals: "
                           "our central column or Anderson's turntable on Morton's / Randall's frame; head: Hunter's "
                           "gimbal or Anderson's tilt; mouth, side panels): see `designs`.",
               links=[Link("ground", "Ground (floor / base plate)", None)])
    base = Asm(id="base", name="Base + pedestal", mount_link="ground",
               links=[Link("base", "Base skirt, top and pedestal (static)", None)])
    lower = Asm(id="lower_ring", name="Lower ring + poker arm", mount_link="base",
                links=[Link("lower_ring_mount", "Lower-ring lazy susan (static race)", None),
                       Link("lower_ring", "Lower ring", "torso_lower"),
                       Link("poker_upper", "Poker arm", "poker_shoulder"),
                       Link("poker_hand", "Poker hand", "poker_wrist")])
    # The middle ring's lazy susan has its inner race on LS_IC_1, the static core that also carries
    # the lower ring's inner race (guide p20, p33): the middle ring does not ride the lower ring.
    middle = Asm(id="middle_ring", name="Middle ring + throttle arm", mount_link="lower_ring_mount",
                 links=[Link("middle_ring_mount", "Middle-ring lazy susan (lower race)", None),
                        Link("middle_ring", "Middle ring (logic panels)", "torso_middle"),
                        Link("throttle_upper", "Throttle arm upper", "throttle_shoulder"),
                        Link("throttle_fore", "Throttle forearm", "throttle_elbow"),
                        Link("throttle_hand", "Throttle hand", "throttle_wrist")])
    top = Asm(id="top_ring", name="Top ring + hero arm", mount_link="middle_ring",
              links=[Link("top_ring_mount", "Top-ring lazy susan (lower race)", None),
                     Link("top_ring", "Top ring (RX-24)", "torso_top"),
                     Link("hero_arm", "Hero arm", "hero_shoulder"),
                     Link("hero_hand", "Hero hand", "hero_wrist")])
    head = Asm(id="head", name="Head (shell, headband, ears, visor)", mount_link="head_mount",
               links=[Link("head", "Head", "head_tilt"), Link("visor", "Visor (brow + side arms)", "visor")])

    # vertical ring axes: all rings turn about body +Y through the origin (kit ring centres at x=z=0)
    def ring():  # fresh evidence list per joint (drives append to it)
        return dict(type="revolute", pivot=(0.0, 0.0, 0.0), axis=(0.0, 1.0, 0.0),
                    evidence=["kit ring solids are centred on x=z=0 (LS_M_Full, MS_Main_Full, TR_NR_Full bbox centres)"],
                    confidence="high")
    lower.joints = [Joint("torso_lower", "Lower ring", parent_link="lower_ring_mount", child_link="lower_ring",
                          limits=(-35, 35), profile_joint="torso_lower", **ring()), J["poker_shoulder"], J["poker_wrist"]]
    middle.joints = [Joint("torso_middle", "Middle ring", parent_link="middle_ring_mount", child_link="middle_ring",
                           limits=(0, 0), profile_joint="torso_middle",
                           drive={"kind": "none", "note": "not motorised in the R-3X Animation build"}, **ring()),
                     J["throttle_shoulder"], J["throttle_elbow"], J["throttle_wrist"]]
    top.joints = [Joint("torso_top", "Top ring", parent_link="top_ring_mount", child_link="top_ring",
                        limits=(-30, 30), profile_joint="torso_top", **ring())]

    asms = {"base": base, "lower_ring": lower, "middle_ring": middle, "top_ring": top, "head": head}
    link_to_asm = {"base": base, "lower_ring_mount": lower, "lower_ring": lower, "poker_upper": lower, "poker_hand": lower,
                   "middle_ring": middle, "throttle_upper": middle, "throttle_fore": middle, "throttle_hand": middle,
                   "top_ring": top, "top_ring_mount": top, "hero_arm": top, "hero_hand": top, "head": head, "visor": head}
    by_stem: dict[str, list[str]] = {}
    for p in parts:
        link_to_asm[p.link].parts.append(p)
        stem = Path(p.file).stem
        for key in {stem, stem.replace(" (New)", "").replace(" (DNP)", "").replace(" - x4", "")}:
            by_stem.setdefault(key, []).append(p.id)
    attach_guide(asms, by_stem)

    # The base's four side panels come three ways in the kit (B_S_C / B_S_O / B_S_PO, each x4 in the
    # same four slots, nested inside each other): options, not a stack. One is built.
    panels = {"c": ("closed", "Side panels: closed (B_S_C)", True), "o": ("open", "Side panels: open (B_S_O)", False),
              "po": ("port", "Side panels: port opening (B_S_PO)", False)}
    for code, (vid, name, dflt) in panels.items():
        mine = [p for p in base.parts if p.id.startswith(f"b_s_{code}_")]
        if not mine:
            continue
        for p in mine:
            base.parts.remove(p)
            p.link = "side_panels"
        ids = {p.id for p in mine}
        steps = []
        for st in base.steps:  # the guide's step for them goes with them
            if ids & set(st.get("parts", [])):
                steps.append(dict(st, id=f"{st['id']}_{vid}", parts=[x for x in st["parts"] if x in ids], unplaced=[],
                                  title=f"{st.get('title', '')} ({name})"))
                st["parts"] = [x for x in st["parts"] if x not in ids]
        base.children.append(Asm(id=f"base_panels_{vid}", name=name, mount_link="base",
                                 variant={"group": "base_side_panels", "id": vid, "default": dflt},
                                 links=[Link("side_panels", "Side panels (static)", None)], parts=mine, steps=steps))

    root.children = [base, lower]
    lower.children = [middle]
    middle.children = [top]
    base.notes.append("The head is not carried by the rings in the R-3X build: the neck tube runs from the "
                      "pan/lift stage in the base, through all three rings, to the head (see r3x_neck_drive).")
    anderson = Asm(id="internals_anderson", name="Internals: Anderson's turntable + rack lift + long neck on Morton's cage",
                   mount_link="base", links=[],
                   variant={"group": "internals", "id": "anderson_morton", "default": internals == "anderson_morton"},
                   description="The R-3X Animation neck drive (base turntable, rack lift, 744 mm neck tube) on Sam "
                               "Morton's 2020 lower cage (or Randall's printed frame), as modelled before the column.")
    if with_r3x:
        from assemblies.r3x_animation.assembly import attach
        attach(root, anderson, lower, middle, top, head, default=internals == "anderson_morton")
        base.children.append(anderson)
        _lower_pinion_relief(lower)
    if community:
        from assemblies.community.assembly import morton_frame, mouth_split_parts, randall_frame
        anderson.children += [morton_frame(), randall_frame()]
        if internals == "anderson_morton":
            for p in base.parts:
                if p.id == "p_m_3":
                    p.note = "superseded by Henley B_M_3_mod in the Morton frame variant"
        mouth = Asm(id="mouth_split", name="Trevor Zaharichuk Mic-Mouth-Split (alternate mouth)", mount_link="head",
                    variant={"group": "mouth", "id": "mic_mouth_split", "default": False},
                    links=[Link("mouth", "Mouth insert", None)])
        mouth.parts = mouth_split_parts(link="mouth")
        head.children.append(mouth)
    else:
        # kit-only: the head sits on a static neck on the top ring
        head.mount_link = "top_ring"
        head.links.insert(0, Link("head_mount", "Static neck", None))
        ear = (0.0, 770.6, 0.0)
        head.joints = [Joint("head_tilt", "Head tilt (poseable)", "revolute", "head_mount", "head", (0.0, 740.0, 0.0),
                             (1.0, 0.0, 0.0), (-20, 25), profile_joint="head_tilt", drive={"kind": "none"}),
                       Joint("visor", "Visor (poseable)", "revolute", "head", "visor", ear, (1.0, 0.0, 0.0), (-15, 30),
                             profile_joint="visor", drive={"kind": "none"})]
        top.children.append(head)
    use_real_parts(root)
    if with_r3x and community:
        from assemblies.column.assembly import build_column, ring_drive

        base.children.append(build_column("base", {"group": "internals", "id": "column", "default": internals == "column"},
                                          in_droid=True))
        # the rings' drives follow the `internals` pick: the column's (same pinion places, same ratios) or
        # Anderson's; the joint's drive is the default's, `variants` holds both (SCHEMA.md "Joint")
        for a, jid in ((lower, "torso_lower"), (top, "torso_top")):
            j = next(j for j in a.joints if j.id == jid)
            anderson = dict(j.drive)
            column = ring_drive(jid, anderson)
            opts = [dict(column, variant={"group": "internals", "id": "column"}),
                    dict(anderson, variant={"group": "internals", "id": "anderson_morton"})]
            j.drive = dict(column if internals == "column" else anderson, variants=opts)
        _pedestal_cap(base, internals)
    return root


def _pedestal_cap(base: Asm, internals: str):
    """P_M_3 per internals: the kit's (Anderson's internals clamp the lower race on it, guide p20) or, under
    the column, 6.3 mm shorter - its top band is where the column's core plate carries that race (guide p11:
    "a working build would instead mount to an internal frame"). Both are children of the base in the
    `internals` group; the base's guide step that placed P_M_3 goes with each."""
    import copy as _copy

    from assemblies.r3x_animation.assembly import relief
    from parts.column import _layout as CL

    cap = next((p for p in base.parts if p.id == "p_m_3"), None)
    if cap is None:
        return
    base.parts.remove(cap)
    steps = []
    for st in base.steps:
        if "p_m_3" in st.get("parts", []):
            steps.append(dict(st))
            st["parts"] = [x for x in st["parts"] if x != "p_m_3"]
    cut = _copy.copy(cap)
    y1 = CL.SUPPORT["core"]["y_top"] + 0.5
    y0 = CL.SUPPORT["core"]["y_top"] - CL.SUPPORT["t"] - 0.3

    def gen():
        import trimesh as _tm

        m = _tm.creation.cylinder(radius=CL.SUPPORT["core"]["r"] + 1.0, height=y1 - y0, sections=96)
        m.apply_transform(_tm.transformations.rotation_matrix(-np.pi / 2, [1, 0, 0]))
        m.apply_translation([0, (y0 + y1) / 2, 0])
        return m

    env = Part(id="core_plate_envelope", name="the column's core plate (6 mm + 0.3)", cls="mech", link="base",
               T=np.eye(4), generator=gen, kind="generated", origin="ours")
    relief(cut, env, 0.0, "the column's core plate carries the lower race in its place (guide p11)")
    cut.note = (cut.note + "; " if cut.note else "") + "6.3 mm shorter: the column's core plate takes its top band"
    for vid, part, dflt in (("anderson_morton", cap, internals == "anderson_morton"), ("column", cut, internals == "column")):
        part.link = "pedestal_cap"
        # first among the base's children: a viewer that names an `internals` option by its node lists the
        # column / Anderson's nodes after these
        base.children.insert(0, Asm(
            id=f"pedestal_cap_{'kit' if vid == 'anderson_morton' else 'column'}",
            name=f"Pedestal cap P_M_3 ({'the kit' if vid == 'anderson_morton' else 'cut for the core plate'})",
            mount_link="base", variant={"group": "internals", "id": vid, "default": dflt},
            links=[Link("pedestal_cap", "Pedestal cap (static)", None)], parts=[part],
            steps=[dict(st, id=f"{st['id']}_cap", parts=["p_m_3"], unplaced=[],
                        title="Pedestal cap P_M_3 onto the pedestal: 6 x M4 x 8 socket heads + washers")
                   for st in steps[:1]]))


def _lower_pinion_relief(lower: Asm):
    """The lower ring's pinion (Anderson's, and the column's in the same place) turns through the
    kit's static core LS_IC_1 (r 98.5..110, y 352..407): a relief round its swept disc (r 33 + 1)."""
    import trimesh as _tm

    from assemblies.r3x_animation.assembly import relief

    core = next((p for p in lower.parts if p.id == "ls_ic_1"), None)
    if core is None:
        return

    def gen():
        m = _tm.creation.cylinder(radius=34.0, height=12.0, sections=64)
        m.apply_transform(_tm.transformations.rotation_matrix(-np.pi / 2, [1, 0, 0]))
        m.apply_translation([-9.8, 373.3 + 5.0, -83.0])
        return m

    cut = Part(id="lower_pinion_envelope", name="lower pinion's swept disc (r 34, y 372.3..384.3)", cls="mech",
               link="lower_ring_mount", T=np.eye(4), generator=gen, kind="generated", origin="ours")
    relief(core, cut, 0.0, "the lower ring's pinion meshes Anderson's sector through the static core")


SERVO_CASE = {"SERVO_60KG_270": "large", "SERVO_35KG_270": "standard", "DS3218_DUAL": "standard", "SERVO_7KG": "micro"}


def use_real_parts(root: Asm):
    """Swap the builders' stand-ins ("-dnp" placeholders, box generators) for the real parts from
    mech/parts: parametric servos to their datasheet case, horns, MGN12 rail/carriage, the
    F6001ZZ bearing, the neck tube and the 2020 extrusions - each fitted onto its stand-in so
    placement is unchanged (the fit error is reported per part)."""
    from parts import models

    def ext(p):
        from r3xmech.meshes import load_file
        m = load_file(str(p.file)) if p.file is not None else p.generator()
        return sorted(m.extents)

    for p in root.all_parts():
        stem = Path(p.file).stem.lower() if p.file is not None else ""
        key = p.material.split(":", 1)[1] if p.material.startswith("servo:") else None
        if p.cls == "servo" and key in SERVO_CASE:
            p.real = {"kind": "servo", "spec": {"case": SERVO_CASE[key], "dual_shaft": key == "DS3218_DUAL", "model": key}}
            p.cad = "parametric"
        elif "disc-dnp" in stem or "disk-dnp" in stem:
            e = ext(p)
            p.real = {"kind": "disc_horn", "spec": {"od_mm": round(e[2], 1), "t_mm": round(e[0], 1), "square_mm": 10.0}}
            p.cad = "parametric"
        elif "mgn12h-dnp" in stem:
            p.real = {"kind": "carriage", "spec": {"rail": "MGN12", "carriage": "MGN12H"}}
            p.cad = "parametric"
        elif "mgn12rail-dnp" in stem:
            p.real = {"kind": "linear_rail", "spec": {"rail": "MGN12", "length_mm": round(ext(p)[2]), "carriage": False}}
            p.cad = "parametric"
        elif "flanged-bearing-dnp" in stem:
            p.real = {"kind": "bearing", "spec": {"type": "flanged", "id_mm": 12, "od_mm": 28, "width_mm": 8,
                                                   "flange_od_mm": 30.5, "flange_mm": 1.5}}
            p.cad = "parametric"
        elif p.id == "neck_tube" and p.generator is not None:
            L = ext(p)[2]
            p.generator = (lambda L=L: models.tube({"od_mm": 26.0, "id_mm": 23.0, "length_mm": L}))
            p.cad = "parametric"
        elif p.generator is not None and "2020" in p.name:
            L = ext(p)[2]
            p.generator = (lambda L=L: models.extrusion({"profile": "2020", "length_mm": L}))
            p.cad = "parametric"
            p.catalog = "misumi:HFS5-2020"
        elif p.generator is not None and p.kind == "generated":
            p.cad = "placeholder"


# The published designs the droid is assembled from (manifest `designs`, the Build view's Library): each
# names its assemblies and the variant picks that show it; `deep` takes each assembly's whole subtree,
# `only` / `except` filter by part class, `look` is the view it reads best in.
DESIGNS = [
    dict(id="kit", name="Kit shells", author="Patrick Gray / David Ferreira", source="DJ R3X - v2 printable kit",
         assemblies=["base", "base_panels_closed", "lower_ring", "middle_ring", "top_ring", "head_r3x"],
         picks={"internals": "anderson_morton", "head_mech": "r3x_anderson", "base_side_panels": "closed"},
         only=["shell"], look="exterior"),
    dict(id="anderson", name="R-3X Animation", author="Brian Anderson", source="R-3X Animation (Onshape studios)",
         assemblies=["r3x_neck_drive", "head_r3x", "r3x_lower_drive", "r3x_top_drive", "r3x_neck_guide_race"],
         picks={"internals": "anderson_morton", "head_mech": "r3x_anderson"}, **{"except": ["shell"]}, look="mechanism"),
    dict(id="hunter", name="Head gimbal", author="Hunter Smoke", source="Hunter Smoke head mech files",
         assemblies=["hunter_head"], picks={"internals": "column"}, deep=True, look="mechanism"),
    dict(id="morton", name="Lower cage", author="Sam Morton", source="Morton 2020 lower cage",
         assemblies=["morton_frame"], picks={"internals": "anderson_morton", "base_frame": "morton"}, look="mechanism"),
    dict(id="randall", name="Printed frame", author="Riane Randall", source="Randall printed frame (reference)",
         assemblies=["randall_frame"], picks={"internals": "anderson_morton", "base_frame": "randall"}, look="mechanism"),
    dict(id="mouth", name="Mic-Mouth-Split", author="Trevor Zaharichuk", source="Mic-Mouth-Split",
         assemblies=["mouth_split"], picks={"internals": "anderson_morton", "head_mech": "r3x_anderson",
                                            "mouth": "mic_mouth_split"}, look="mechanism"),
    dict(id="column", name="Central column", author="Ours (after Jason Charlton's lift and pan)",
         source="mech/assemblies/column", assemblies=["column_internals"], picks={"internals": "column"},
         look="mechanism"),
]


def _ground(asm) -> dict:
    """The floor: the lowest modelled part with the default picks, and per `internals` option. The Gil
    drive (the wheels) under the column's Gil plate and Anderson's stage is not modelled, so the real floor
    is lower by its height."""
    from workbench.model import Assembly

    groups: dict[str, str] = {}

    def picks(a):  # the viewer's rule: the first option seen, unless a `default` one exists anywhere
        v = (a.mount or {}).get("variant")
        if v:
            if v.get("default"):
                groups[v["group"]] = v["id"]
            else:
                groups.setdefault(v["group"], v["id"])
        for c in a.children:
            if isinstance(c, Assembly):
                picks(c)

    picks(asm)
    defaulted = {}

    def fix(a):
        v = (a.mount or {}).get("variant")
        if v and v.get("default"):
            defaulted[v["group"]] = v["id"]
        for c in a.children:
            if isinstance(c, Assembly):
                fix(c)

    fix(asm)
    groups.update(defaulted)

    def lowest(picks):
        ys = []

        def walk(a):
            v = (a.mount or {}).get("variant")
            if v and picks.get(v["group"]) != v["id"]:
                return
            ys.extend(float(p.mesh.bounds[0][1]) for p in a.parts)
            for c in a.children:
                if isinstance(c, Assembly):
                    walk(c)

        walk(asm)
        return round(min(ys), 1)

    by = {f"internals:{o}": lowest({**groups, "internals": o}) for o in ("column", "anderson_morton")}
    return {"y": lowest(groups), "by_variant": by, "inferred": True,
            "inferred_note": "the lowest modelled part; the Gil drive's wheels under the base plate are not modelled, "
                             "so the real floor is lower by their height"}


def build():
    """Workbench entry point (`python -m workbench build kit`): the whole droid as a
    workbench.model.Assembly with meshes at the rest pose."""
    from r3xmech.wb import to_workbench
    from assemblies.kit.fastening import fasten

    asm = to_workbench(build_model())
    fasten(asm, INTERNALS)
    asm.designs = [dict(d) for d in DESIGNS]
    asm.ground = _ground(asm)
    return asm


def kit_head_parts(origin_y: float = 738.3):
    """Kit head parts in a head frame whose origin is (0, origin_y, 0) in the body frame (e.g.
    Hunter's gimbal centre), for a head module that carries the kit's details on its own links:
    [(id, stl_path, 4x4 file->head frame, link 'head' | 'visor', note)]."""
    out = []
    for p in kit_parts():
        if p.link in ("head", "visor"):
            T = frames.trans(0, -origin_y, 0) @ p.T
            out.append((p.id, p.file, T, p.link, p.note))
    return out


if __name__ == "__main__":
    r = build_model()
    print(sum(1 for _ in r.all_parts()), "parts;", len(r.all_joints()), "joints")
