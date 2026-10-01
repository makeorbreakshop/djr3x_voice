"""In-memory assembly model; serialises to the manifest in SCHEMA.md (r3x.mech.manifest v1).

An assembly module builds one `Assembly` (with nested children). Geometry rides along as
trimesh meshes in the assembly frame at the zero pose; the builder (build.py) turns those
into per-part files and the JSON.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

SCHEMA = "r3x.mech.manifest"
VERSION = 1
CLASSES = ("shell", "mech", "servo", "fastener", "bearing", "hardware")


def vec(v) -> list[float]:
    return [round(float(x), 4) for x in v]


@dataclass
class Transform:
    t: tuple = (0.0, 0.0, 0.0)
    q: tuple = (0.0, 0.0, 0.0, 1.0)  # x, y, z, w

    @staticmethod
    def from_matrix(m: np.ndarray) -> "Transform":
        from trimesh.transformations import quaternion_from_matrix

        w, x, y, z = quaternion_from_matrix(m)
        return Transform(tuple(m[:3, 3]), (x, y, z, w))

    def matrix(self) -> np.ndarray:
        from trimesh.transformations import quaternion_matrix

        x, y, z, w = self.q
        m = quaternion_matrix([w, x, y, z])
        m[:3, 3] = self.t
        return m

    def json(self):
        out: dict[str, Any] = {"t": vec(self.t)}
        if not np.allclose(self.q, (0, 0, 0, 1)):
            out["q"] = vec(self.q)
        return out


@dataclass
class Link:
    id: str
    name: str
    joint: Optional[str] = None


@dataclass
class Part:
    id: str
    name: str
    cls: str
    link: str
    mesh: Any  # trimesh.Trimesh, assembly frame, zero pose (full detail)
    source: dict
    material: str = ""
    printed: bool = False
    explode: tuple = (0.0, 1.0, 0.0)
    explode_mm: float = 40.0
    mass_g: Optional[float] = None
    mass_note: str = ""
    linkage: Optional[str] = None  # horn/rod parts are posed by a linkage, not rigidly
    role: Optional[str] = None  # "horn" | "rod" | "rod_end_a" | "rod_end_b"
    inferred: bool = False
    inferred_note: str = ""
    note: str = ""
    decimate_to: Optional[int] = None  # display triangle budget (None = builder default)
    features: dict = field(default_factory=dict)  # name -> feature (mates.py), assembly frame, zero pose
    cad: str = "mesh"  # mesh (a reference mesh) | vendor (vendor CAD) | parametric (ours) | placeholder
    catalog: Optional[str] = None  # parts catalog id ("gobilda:2913-0004-0241")
    stretch: Optional[dict] = None  # {"joint", "axis", "anchor", "rest_mm"}: scaled by a prismatic joint (SCHEMA.md)
    exposed: Optional[bool] = None  # seen from outside the droid (a shell is by default; SCHEMA.md "Part")
    replaced_by: Optional[str] = None  # "<assembly id>/<part id>" that supersedes it: not drawn, not in the suite
    finish: Optional[dict] = None  # {"paint", "print": {filament, color, color_name}, "color", "kit"} (workbench/finish.py)

    def __post_init__(self):
        assert self.cls in CLASSES, self.cls


@dataclass
class Joint:
    id: str
    name: str
    type: str
    parent_link: str
    child_link: str
    pivot: tuple
    axis: tuple
    limits: tuple
    unit: str = "deg"
    profile_joint: Optional[str] = None
    profile_limits: Optional[tuple] = None
    drive: dict = field(default_factory=dict)
    zero: dict = field(default_factory=dict)
    inferred: bool = False
    inferred_note: str = ""


@dataclass
class Linkage:
    """A push rod from a servo horn (on `horn_link`) to a ball on `ground_link`."""

    id: str
    servo: str
    horn_link: str
    centre: tuple
    axis: tuple
    radius: float
    zero_dir: tuple
    ball_offset: float
    ground_link: str
    ground_point: tuple
    rod_length: float
    servo_range: tuple = (-150.0, 150.0)
    ground_axis: tuple = (0.0, 1.0, 0.0)  # the ground ball's stud axis (swivel test)
    parts: list = field(default_factory=list)
    inferred: bool = False
    inferred_note: str = ""


@dataclass
class Gear:
    """Parts that turn about their own axle with a joint (SCHEMA.md "Gear"): a servo's pinion on a
    rack or a ring's sector, a coupler on a direct-drive spline. Posed by its link's matrix and a
    turn of `deg_per_unit` x the joint's value about `pivot`/`axis` (assembly frame, zero pose)."""

    id: str
    kind: str  # rack_pinion | internal | spur | direct
    joint: str  # the joint whose value turns it
    link: str  # the link its axle is fixed to
    pivot: tuple
    axis: tuple
    deg_per_unit: float  # turn (deg, right-hand about axis) per joint unit (deg or mm)
    parts: list = field(default_factory=list)
    fasteners: list = field(default_factory=list)
    joint_assembly: Optional[str] = None  # the assembly that declares `joint` (None = this one)
    servo: Optional[str] = None  # the servo whose output this is (its spline turns the same)
    servo_deg_per_unit: Optional[float] = None  # servo deg per joint unit (servo -> joint: divide)
    mesh_with: Optional[str] = None  # the part it meshes (a rack, a sector), for reference
    note: str = ""
    inferred: bool = False
    inferred_note: str = ""


@dataclass
class Fastener:
    id: str
    spec: dict
    key: str
    joins: list
    link: str
    step: str
    matrix: Optional[np.ndarray] = None  # +Z = insertion direction, head at origin; None = unplaced
    inferred: bool = False
    inferred_note: str = ""
    mesh: Any = None  # canonical-frame mesh (parts library); None = the builder's own
    linkage: Optional[str] = None  # posed with a linkage's horn (ball studs on a servo arm)
    role: Optional[str] = None
    cad: str = "parametric"
    catalog: Optional[str] = None
    features: dict = field(default_factory=dict)


@dataclass
class Step:
    id: str
    title: str
    parts: list = field(default_factory=list)
    fasteners: list = field(default_factory=list)
    unplaced: list = field(default_factory=list)
    tools: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    context: list = field(default_factory=list)
    joint: Optional[str] = None
    pose: dict = field(default_factory=dict)
    guide_page: Optional[int] = None
    inferred: bool = False
    inferred_note: str = ""
    # The step as the Instructions read it: one or two plain imperative sentences, in our words
    # (never the kit guide's text; SCHEMA.md "Step").
    text: str = ""
    # Grouped by workbench/steps.py from the structure, not written by anyone.
    derived: bool = False
    # The order things go in and each one's approach path (workbench/paths.py; SCHEMA.md "Step").
    sequence: list = field(default_factory=list)


@dataclass
class BomLine:
    key: str
    item: str
    qty: float
    category: str
    spec: dict = field(default_factory=dict)
    source: str = ""
    parts: list = field(default_factory=list)
    fasteners: list = field(default_factory=list)
    inferred: bool = False
    inferred_note: str = ""


@dataclass
class Check:
    id: str
    kind: str
    status: str
    title: str
    summary: str
    joint: Optional[str] = None
    value: Optional[float] = None
    pose: dict = field(default_factory=dict)
    parts: list = field(default_factory=list)
    assumptions: list = field(default_factory=list)
    seconds: Optional[float] = None  # how long the check took (suite timing)


@dataclass
class Assembly:
    id: str
    name: str
    description: str = ""
    frame_note: str = ""
    mount: Optional[dict] = None
    guide: Optional[dict] = None
    links: list = field(default_factory=list)
    parts: list = field(default_factory=list)
    joints: list = field(default_factory=list)
    linkages: list = field(default_factory=list)
    gears: list = field(default_factory=list)  # Gear: parts turned with a joint by a gear ratio
    fasteners: list = field(default_factory=list)
    steps: list = field(default_factory=list)
    bom: list = field(default_factory=list)
    checks: list = field(default_factory=list)
    children: list = field(default_factory=list)  # Assembly | dict (ChildRef)
    notes: list = field(default_factory=list)
    mates: list = field(default_factory=list)  # mates.Mate
    tests: list = field(default_factory=list)  # suite results (Check with kind "test")
    tolerances: dict = field(default_factory=dict)  # overrides for the suite (TESTS.md)
    designs: list = field(default_factory=list)  # root only: the published designs it is assembled from (SCHEMA.md)
    ground: Optional[dict] = None  # root only: {"y", ...} the floor the droid stands on (SCHEMA.md)

    # ------------------------------------------------------------------ lookups
    def part(self, pid: str) -> Part:
        return next(p for p in self.parts if p.id == pid)

    def joint(self, jid: str) -> Joint:
        return next(j for j in self.joints if j.id == jid)

    def link_chain(self, link: str) -> list[str]:
        """Joints from the ground down to `link` (outermost first)."""
        by = {l.id: l for l in self.links}
        out = []
        while by[link].joint:
            j = self.joint(by[link].joint)
            out.append(j.id)
            link = j.parent_link
        return out[::-1]

    def validate(self):
        ids = [p.id for p in self.parts] + [f.id for f in self.fasteners]
        dup = {i for i in ids if ids.count(i) > 1}
        assert not dup, f"duplicate ids {dup}"
        links = {l.id for l in self.links}
        for p in self.parts:
            assert p.link in links, f"{p.id}: unknown link {p.link}"
        for j in self.joints:
            assert j.parent_link in links and j.child_link in links, j.id
        step_ids = {s.id for s in self.steps}
        for f in self.fasteners:
            assert f.step in step_ids, f"{f.id}: unknown step {f.step}"
        pids = {p.id for p in self.parts}
        fids = {f.id for f in self.fasteners}
        for s in self.steps:
            for p in s.parts + s.context:
                assert p in pids, f"{s.id}: unknown part {p}"
            for f in s.fasteners:
                assert f in fids, f"{s.id}: unknown fastener {f}"
        known = pids | fids
        for m in self.mates:
            for pid, feat in (m.a, m.b):
                assert pid in known, f"mate {m.id}: unknown part {pid}"
                owner = next((p for p in self.parts if p.id == pid), None) or next(f for f in self.fasteners if f.id == pid)
                assert feat in owner.features, f"mate {m.id}: {pid} has no feature {feat}"
        for c in self.children:
            if isinstance(c, Assembly):
                c.validate()


def rollup(asm: Assembly, loaded: dict | None = None) -> list[BomLine]:
    """Own BOM plus every inline child's, merged by key (quantities summed)."""
    merged: dict[str, BomLine] = {}
    lines = list(asm.bom)
    for c in asm.children:
        if isinstance(c, Assembly):
            lines += rollup(c)
        elif loaded and c.get("id") in loaded:
            lines += [BomLine(**{k: v for k, v in b.items() if k in BomLine.__dataclass_fields__})
                      for b in loaded[c["id"]]]
    for b in lines:
        if b.key in merged:
            m = merged[b.key]
            m.qty += b.qty
            m.inferred = m.inferred or b.inferred
        else:
            merged[b.key] = BomLine(**{**b.__dict__, "parts": list(b.parts), "fasteners": list(b.fasteners)})
    return sorted(merged.values(), key=lambda b: (b.category, b.key))
