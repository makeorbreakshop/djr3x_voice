"""In-memory assembly model and its export to the workbench manifest (mech/workbench/SCHEMA.md).

Every sub-assembly here uses the canonical body frame directly (mount transform = identity),
so a part's `T` is already "part file -> body frame at the rest pose" and a joint's pivot and
axis are body-frame numbers. Forward kinematics composes joint motions down the link tree.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from . import frames

MECH = Path(__file__).resolve().parent.parent


@dataclass
class Part:
    id: str
    name: str
    cls: str                      # shell | mech | servo | bearing | hardware | fastener
    link: str
    T: np.ndarray                 # file frame -> body frame, rest pose
    file: Path | None = None      # vendored source (never committed)
    kind: str = "stl"             # stl | step | generated
    origin: str = "kit"           # kit | r3x_animation | catalog | ours
    placement: str = "kit"        # kit (assembled export) | step | fitted | inferred
    material: str = "PLA"
    printed: bool = True
    infill: float | None = None   # effective fill fraction for printed parts (see catalog)
    mass_g: float | None = None   # catalogue / override mass
    inferred: bool = False
    inferred_note: str = ""
    note: str = ""
    evidence: str = ""
    replaces: list[str] = field(default_factory=list)   # kit parts this build part supersedes
    generator: Callable | None = None                     # makes a trimesh when file is None
    cad: str = "mesh"             # mesh (a reference mesh) | vendor | parametric (ours) | placeholder
    catalog: str | None = None    # mech/parts/catalog.json id
    real: dict | None = None      # {"kind", "spec"}: the real part (mech/parts) fitted onto `file` (a stand-in)
    stretch: dict | None = None   # {"joint", "axis", "anchor", "rest_mm"}: scaled along axis by a prismatic joint
    features: dict = field(default_factory=dict)  # mate features (workbench/mates.py), in the file frame
    explode: tuple = (0, 1, 0)


@dataclass
class Joint:
    id: str
    name: str
    type: str                      # revolute | prismatic | fixed
    parent_link: str
    child_link: str
    pivot: tuple
    axis: tuple
    limits: tuple                  # design range the build supports (derived)
    unit: str = "deg"
    profile_joint: str | None = None
    drive: dict = field(default_factory=dict)
    zero: dict = field(default_factory=dict)
    evidence: list[str] = field(default_factory=list)
    confidence: str = "medium"
    inferred: bool = False
    inferred_note: str = ""


@dataclass
class Link:
    id: str
    name: str
    joint: str | None


@dataclass
class Asm:
    id: str
    name: str
    description: str = ""
    mount_link: str | None = None           # parent's link this sub-assembly rides on
    links: list[Link] = field(default_factory=list)
    parts: list[Part] = field(default_factory=list)
    joints: list[Joint] = field(default_factory=list)
    children: list = field(default_factory=list)   # Asm or dict (ChildRef)
    gears: list[dict] = field(default_factory=list)  # workbench.model.Gear fields (SCHEMA.md "Gear")
    steps: list[dict] = field(default_factory=list)
    fasteners: list[dict] = field(default_factory=list)
    bom: list[dict] = field(default_factory=list)
    checks: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    guide: dict | None = None
    variant: dict | None = None             # {"group", "id", "default"}: alternatives share a group
    frame_note: str = "Canonical body frame (show/SPEC.md): mm, +Y up, +Z front, +X droid's left; rest pose."

    # ---- traversal ------------------------------------------------------------------
    def walk(self):
        yield self
        for c in self.children:
            if isinstance(c, Asm):
                yield from c.walk()

    def all_parts(self):
        return [p for a in self.walk() for p in a.parts]

    def all_joints(self):
        return {j.id: j for a in self.walk() for j in a.joints}

    def all_links(self):
        return {l.id: l for a in self.walk() for l in a.links}

    def part(self, pid):
        for p in self.all_parts():
            if p.id == pid:
                return p
        raise KeyError(pid)

    # ---- kinematics -----------------------------------------------------------------
    def link_parent(self):
        """link -> (joint or None, parent link or None). A sub-assembly's ground link rides
        rigidly on its mount link."""
        joints = self.all_joints()
        out = {}
        for a in self.walk():
            for l in a.links:
                if l.joint is not None:
                    out[l.id] = (joints[l.joint], joints[l.joint].parent_link)
                else:
                    out[l.id] = (None, a.mount_link)
        return out

    def link_chain(self, link_id):
        """Joints from the root down to link_id (root first)."""
        par = self.link_parent()
        chain = []
        cur = link_id
        while cur is not None:
            j, cur = par[cur]
            if j is not None:
                chain.append(j)
        return chain[::-1]

    def link_T(self, link_id, pose: dict | None = None):
        pose = pose or {}
        T = np.eye(4)
        for j in self.link_chain(link_id):
            T = T @ frames.about(j.pivot, j.axis, pose.get(j.id, 0.0), j.type)
        return T

    def subtree_links(self, joint_id):
        """Links moved by a joint (its child link and everything below)."""
        par = self.link_parent()
        out = {self.all_joints()[joint_id].child_link}
        changed = True
        while changed:
            changed = False
            for l, (_, p) in par.items():
                if p in out and l not in out:
                    out.add(l)
                    changed = True
        return out

    # ---- export ---------------------------------------------------------------------
    def to_manifest(self, rel_to: Path | None = None, mesh_info: dict | None = None) -> dict:
        mesh_info = mesh_info or {}

        def src(p: Part):
            f = None
            if p.file is not None:
                f = str(Path(p.file).relative_to(MECH)) if Path(p.file).is_relative_to(MECH) else str(p.file)
            d = {"file": f, "kind": p.kind, "placement": p.placement, "origin": p.origin}
            if p.evidence:
                d["evidence"] = p.evidence
            return d

        def part(p: Part):
            d = {"id": p.id, "name": p.name, "class": p.cls, "link": p.link,
                 "transform": frames.to_tq(p.T), "source": src(p), "material": p.material,
                 "printed": p.printed, "explode": list(p.explode),
                 "inferred": p.inferred, "inferred_note": p.inferred_note, "note": p.note}
            if p.replaces:
                d["replaces"] = p.replaces
            d.update(mesh_info.get(p.id, {}))
            return d

        def joint(j: Joint):
            return {"id": j.id, "name": j.name, "type": j.type, "parent_link": j.parent_link,
                    "child_link": j.child_link, "pivot": [round(float(v), 3) for v in j.pivot],
                    "axis": [round(float(v), 5) for v in j.axis], "unit": j.unit,
                    "limits": {"min": j.limits[0], "max": j.limits[1]},
                    "profile_joint": j.profile_joint, "drive": j.drive, "zero": j.zero,
                    "evidence": j.evidence, "confidence": j.confidence,
                    "inferred": j.inferred, "inferred_note": j.inferred_note}

        out = {"id": self.id, "name": self.name, "description": self.description,
               "frame_note": self.frame_note}
        if self.guide:
            out["guide"] = self.guide
        if self.mount_link is not None:
            out["mount"] = {"parent_link": self.mount_link, "transform": {"t": [0, 0, 0], "q": [0, 0, 0, 1]},
                            "inferred": False}
        out.update(links=[{"id": l.id, "name": l.name, "joint": l.joint} for l in self.links],
                   parts=[part(p) for p in self.parts], joints=[joint(j) for j in self.joints],
                   linkages=[], fasteners=self.fasteners, steps=self.steps, bom=self.bom,
                   bom_rollup=rollup([a.bom for a in self.walk()]), checks=self.checks,
                   children=[c.to_manifest(rel_to, mesh_info) if isinstance(c, Asm) else c
                             for c in self.children],
                   notes=self.notes)
        return out


def rollup(boms):
    merged: dict[str, dict] = {}
    for bom in boms:
        for line in bom:
            k = line["key"]
            if k not in merged:
                merged[k] = dict(line)
                merged[k]["qty"] = line.get("qty") or 0
            else:
                merged[k]["qty"] = (merged[k]["qty"] or 0) + (line.get("qty") or 0)
    return sorted(merged.values(), key=lambda l: (l.get("category", ""), l["key"]))
