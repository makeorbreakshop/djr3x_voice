"""Adapter: r3xmech.model.Asm -> workbench.model.Assembly (mech/workbench, owned by the
workbench; see SCHEMA.md). Meshes are loaded here and handed over in the assembly frame at the
rest pose, which is what the workbench builder expects."""
from __future__ import annotations

import numpy as np

from .meshes import part_mesh
from .model import MECH, Asm


def _src(p):
    d = {"kind": p.kind, "placement": p.placement, "origin": p.origin}
    if p.file is not None:
        f = str(p.file)
        d["file"] = f[len(str(MECH)) + 1:] if f.startswith(str(MECH)) else f
    if p.evidence:
        d["evidence"] = p.evidence
    return d


def _mass(p):
    if p.mass_g is not None:
        return p.mass_g, "catalogue"
    return None, ""


def to_workbench(a: Asm, variant: dict | None = None):
    from workbench.mates import moved
    from workbench.model import Assembly, BomLine, Gear, Joint, Link, Part, Step

    parts = []
    for p in a.parts:
        m = part_mesh(p)
        mass, note = _mass(p)
        cls = p.cls if p.cls in ("shell", "mech", "servo", "fastener", "bearing", "hardware") else "hardware"
        src = _src(p)
        if p.real is not None:
            src.update({"kind": "parametric", "stand_in": src.get("file"), "real": dict(p.real)})
        cad = p.cad if p.real is None else ("vendor" if p.catalog and p.catalog.startswith("gobilda") else "parametric")
        if p.real is None and p.kind == "step":
            cad = "vendor"
        parts.append(Part(id=p.id, name=p.name, cls=cls, link=p.link, mesh=m, source=src, material=p.material,
                          printed=p.printed, explode=tuple(p.explode), mass_g=mass, mass_note=note,
                          inferred=p.inferred, inferred_note=p.inferred_note, note=p.note,
                          cad=cad, catalog=p.catalog, stretch=getattr(p, "stretch", None), exposed=p.exposed,
                          replaced_by=p.replaced_by, finish=dict(p.finish) if p.finish else None,
                          features={k: moved(v, p.T) for k, v in (getattr(p, "features", None) or {}).items()}))
    joints = [Joint(id=j.id, name=j.name, type=j.type, parent_link=j.parent_link, child_link=j.child_link,
                    pivot=tuple(float(v) for v in j.pivot), axis=tuple(float(v) for v in j.axis),
                    limits=tuple(j.limits), unit=j.unit, profile_joint=j.profile_joint,
                    drive=dict(j.drive, evidence=j.evidence, confidence=j.confidence), zero=j.zero,
                    inferred=j.inferred, inferred_note=j.inferred_note) for j in a.joints]
    steps = [Step(id=s["id"], title=s["title"], parts=s.get("parts", []), unplaced=s.get("unplaced", []),
                  notes=s.get("notes", []), guide_page=s.get("guide_page"), inferred=s.get("inferred", False),
                  inferred_note=s.get("inferred_note", ""), text=s.get("text", "")) for s in a.steps]
    bom = [BomLine(key=b["key"], item=b.get("item", ""), qty=b.get("qty") or 0, category=b.get("category", "hardware"),
                   spec=b.get("spec", {}), source=b.get("source", ""), inferred=b.get("inferred", False),
                   inferred_note=b.get("inferred_note", "")) for b in a.bom]
    mount = None
    if a.mount_link is not None:
        mount = {"parent_link": a.mount_link, "transform": {"t": [0, 0, 0], "q": [0, 0, 0, 1]}, "inferred": False}
        if getattr(a, "variant", None):
            mount["variant"] = a.variant
    children = [to_workbench(c) if isinstance(c, Asm) else c for c in a.children]
    return Assembly(id=a.id, name=a.name, description=a.description, frame_note=a.frame_note, mount=mount,
                    guide=a.guide, links=[Link(l.id, l.name, l.joint) for l in a.links], parts=parts, joints=joints,
                    steps=steps, bom=bom, children=children, notes=list(a.notes),
                    gears=[Gear(**g) for g in getattr(a, "gears", [])])
