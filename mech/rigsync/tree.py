"""The built mech manifest as one body-frame tree (r3x.mech.manifest v1, workbench/SCHEMA.md).

ChildRefs are loaded and mounted, non-default variant children dropped, and every link,
joint and linkage re-expressed in the droid's body frame (mm, +Y up, +Z front, rest pose).
Link ids are qualified `<assembly>/<link>` because two assemblies may reuse a name.

At the zero pose every link matrix is the identity (SCHEMA: pivots/transforms are given
with every joint at 0), so an assembly's frame in the body frame is just the product of the
mount transforms down to it.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np


def quat_mat(q) -> np.ndarray:
    x, y, z, w = q
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])


def xform(t) -> np.ndarray:
    m = np.eye(4)
    if not t:
        return m
    m[:3, :3] = quat_mat(t.get("q") or [0, 0, 0, 1])
    m[:3, 3] = t.get("t") or [0, 0, 0]
    return m


def apply(m: np.ndarray, p) -> np.ndarray:
    return m[:3, :3] @ np.asarray(p, float) + m[:3, 3]


def rot(axis, deg) -> np.ndarray:
    a = np.asarray(axis, float)
    a = a / np.linalg.norm(a)
    t = math.radians(deg)
    K = np.array([[0, -a[2], a[1]], [a[2], 0, -a[0]], [-a[1], a[0], 0]])
    return np.eye(3) + math.sin(t) * K + (1 - math.cos(t)) * K @ K


@dataclass
class Link:
    gid: str
    asm: str
    id: str
    name: str
    joint: str | None          # gid of the joint that moves it (None: rigid on `parent`)
    parent: str | None         # gid of the link it hangs off when `joint` is None (mounts)


@dataclass
class Joint:
    gid: str
    asm: str
    id: str
    name: str
    type: str
    parent_link: str           # gid
    child_link: str            # gid
    pivot: np.ndarray          # body frame, mm, zero pose
    axis: np.ndarray           # body frame, unit
    unit: str
    limits: tuple[float, float]
    profile_joint: str | None
    drive: dict
    inferred: bool = False
    raw: dict = field(default_factory=dict)


@dataclass
class Linkage:
    gid: str
    asm: str
    raw: dict                  # the manifest's own linkage (assembly frame)
    T: np.ndarray              # assembly frame -> body frame (mm)
    horn_link: str             # gid
    ground_link: str           # gid

    def body(self) -> dict:
        """The linkage re-expressed in the body frame (mm), same keys as the manifest."""
        h, g, R = self.raw["horn"], self.raw["ground"], self.T[:3, :3]
        out = dict(self.raw)
        out["horn"] = dict(h, link=self.horn_link, centre=r(apply(self.T, h["centre"])),
                           axis=r(R @ np.asarray(h["axis"], float), 6), zero_dir=r(R @ np.asarray(h["zero_dir"], float), 6))
        out["ground"] = dict(g, link=self.ground_link, point=r(apply(self.T, g["point"])))
        if "ground_axis" in out:
            out["ground_axis"] = r(R @ np.asarray(out["ground_axis"], float), 6)
        return out


@dataclass
class Part:
    asm: str
    raw: dict
    base: Path                 # directory the part's paths resolve against
    T: np.ndarray              # part mesh frame -> body frame (mm), zero pose
    link: str                  # gid


@dataclass
class Asm:
    id: str
    name: str
    T: np.ndarray
    raw: dict
    base: Path
    checks: list
    referenced: bool = False   # loaded through a ChildRef (a separately built manifest)


@dataclass
class Tree:
    root: Path
    manifest: dict
    asms: list[Asm] = field(default_factory=list)
    links: dict[str, Link] = field(default_factory=dict)
    joints: dict[str, Joint] = field(default_factory=dict)
    linkages: dict[str, Linkage] = field(default_factory=dict)
    parts: list[Part] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    # ---------------------------------------------------------------- queries
    def link_joint(self, gid: str) -> Joint | None:
        j = self.links[gid].joint
        return self.joints[j] if j else None

    def parent_of_link(self, gid: str) -> str | None:
        """The link this one hangs off (through its joint, or its mount)."""
        j = self.link_joint(gid)
        return j.parent_link if j else self.links[gid].parent

    def moving_ancestor(self, gid: str | None) -> Joint | None:
        """The nearest joint at or above link `gid` that moves (has a range) - its profile
        parent. A joint with an empty range (not motorised) still counts: it is a real
        turntable in the chain, just one the build does not drive."""
        seen = 0
        while gid is not None and seen < 64:
            j = self.link_joint(gid)
            if j is not None and j.type != "fixed":
                return j
            gid = self.parent_of_link(gid)
            seen += 1
        return None

    def by_profile(self) -> dict[str, Joint]:
        return {j.profile_joint: j for j in self.joints.values() if j.profile_joint}

    def link_matrices(self, pose: dict[str, float]) -> dict[str, np.ndarray]:
        """Every link's body-frame matrix for a pose keyed by profile joint (or joint gid)."""
        out: dict[str, np.ndarray] = {}

        def get(gid: str, depth=0) -> np.ndarray:
            if gid in out:
                return out[gid]
            j = self.link_joint(gid)
            if j is None:
                p = self.links[gid].parent
                m = get(p, depth + 1) if p else np.eye(4)
            else:
                v = pose.get(j.profile_joint or "", pose.get(j.gid, 0.0))
                m = get(j.parent_link, depth + 1) @ joint_matrix(j, v)
            out[gid] = m
            return m

        for gid in self.links:
            get(gid)
        return out


def joint_matrix(j: Joint, v: float) -> np.ndarray:
    m = np.eye(4)
    if j.type == "revolute":
        R = rot(j.axis, v)
        m[:3, :3] = R
        m[:3, 3] = j.pivot - R @ j.pivot
    elif j.type == "prismatic":
        m[:3, 3] = j.axis * v
    return m


def r(v, n=4):
    return [round(float(x), n) + 0.0 for x in v]


def _variant_ok(mount: dict | None) -> bool:
    v = (mount or {}).get("variant")
    return not v or bool(v.get("default"))


def load(manifest_path: Path) -> Tree:
    manifest_path = Path(manifest_path)
    m = json.loads(manifest_path.read_text())
    if m.get("schema") != "r3x.mech.manifest" or m.get("version") != 1:
        raise ValueError(f"{manifest_path}: not an r3x.mech.manifest v1")
    tree = Tree(root=manifest_path, manifest=m)

    def walk(node: dict, base: Path, T: np.ndarray, parent_asm: str | None, depth: int, referenced=False):
        aid = node["id"]
        tree.asms.append(Asm(aid, node.get("name", aid), T, node, base, node.get("checks") or [], referenced))
        q = lambda lid: f"{aid}/{lid}"  # noqa: E731
        mount_parent = None
        if parent_asm is not None:
            pl = (node.get("mount") or {}).get("parent_link")
            mount_parent = f"{parent_asm}/{pl}" if pl else None  # resolved after the walk
        R = T[:3, :3]
        for l in node.get("links") or []:
            tree.links[q(l["id"])] = Link(q(l["id"]), aid, l["id"], l.get("name", l["id"]),
                                          q(l["joint"]) if l.get("joint") else None,
                                          None if l.get("joint") else mount_parent)
        for j in node.get("joints") or []:
            lim = j.get("limits") or {"min": 0, "max": 0}
            ax = R @ np.asarray(j.get("axis", [0, 1, 0]), float)
            tree.joints[q(j["id"])] = Joint(
                q(j["id"]), aid, j["id"], j.get("name", j["id"]), j.get("type", "fixed"),
                q(j["parent_link"]), q(j["child_link"]), apply(T, j.get("pivot", [0, 0, 0])),
                ax / (np.linalg.norm(ax) or 1), j.get("unit", "deg"), (float(lim["min"]), float(lim["max"])),
                j.get("profile_joint"), j.get("drive") or {}, bool(j.get("inferred")), j)
        for lk in node.get("linkages") or []:
            tree.linkages[q(lk["id"])] = Linkage(q(lk["id"]), aid, lk, T, q(lk["horn"]["link"]), q(lk["ground"]["link"]))
        for p in node.get("parts") or []:
            tree.parts.append(Part(aid, p, base, T @ xform(p.get("transform")), q(p["link"])))
        for c in node.get("children") or []:
            if not _variant_ok(c.get("mount")):
                tree.skipped.append(c.get("id", "?"))
                continue
            cbase = base
            if "ref" in c and "parts" not in c:
                if depth > 6:
                    continue
                ref = (base / c["ref"]).resolve()
                if not ref.exists():
                    tree.skipped.append(f"{c.get('id')} (not built: {c['ref']})")
                    continue
                child = json.loads(ref.read_text())["root"]
                if c.get("mount"):
                    child = dict(child, mount=c["mount"])
                cbase = ref.parent
                ref_ = True
            else:
                child = c
                ref_ = referenced
            walk(child, cbase, T @ xform((child.get("mount") or {}).get("transform")), aid, depth + 1, ref_)

    walk(m["root"], manifest_path.parent, np.eye(4), None, 0)
    # A mount's parent_link names a link of the parent assembly, or (the droid does this) of
    # another assembly in the tree: resolve by plain id when the qualified one is not there.
    plain: dict[str, list[str]] = {}
    for gid, l in tree.links.items():
        plain.setdefault(l.id, []).append(gid)
    for l in tree.links.values():
        if l.parent and l.parent not in tree.links:
            hits = plain.get(l.parent.split("/", 1)[1], [])
            if len(hits) != 1:
                raise ValueError(f"{l.gid}: mount parent_link {l.parent!r} matches {hits or 'no link'}")
            l.parent = hits[0]
    for lk in tree.linkages.values():
        for attr in ("horn_link", "ground_link"):
            if getattr(lk, attr) not in tree.links:
                raise ValueError(f"linkage {lk.gid}: unknown link {getattr(lk, attr)}")
    return tree
