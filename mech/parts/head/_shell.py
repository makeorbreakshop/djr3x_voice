"""The head shells' common frame and shapes.

Hunter's shell STLs (`RX Head Top.stl`, `RX Head Bottom with mount holes.stl`, `Left/Right Side w
alignment holes.stl`) are in the R-3X kit model's coordinates: the head sits at y ~ 738 and is
yawed -51.8 deg about +Y. The workbench's head frame (assemblies/hunter_head `fit_shell`: the
bottom's six insert holes onto the plate's) is origin = gimbal centre, +Y up, +Z forward, +X the
droid's left. Each shell is designed in its own frame, carried into the head frame, then into
the STL's by SHELL_FROM_HEAD, so a swap keeps every placement.
"""

from __future__ import annotations

from ._common import rot_y, trans

# head frame -> the shell STLs' (kit) frame, from fit_shell (6 insert holes, rms 0.05 mm)
SHELL_YAW_DEG = -51.81139
SHELL_ORIGIN = (1.284367, 738.316726, -0.978637)
SHELL_FROM_HEAD = trans(SHELL_ORIGIN) @ rot_y(SHELL_YAW_DEG)


def spheroid(a: float, b: float, yc: float, y_lo: float, axis=(0.0, 0.0)):
    """A solid of revolution about a vertical axis at (x, z) = `axis`: the upper half of an
    ellipse (semi-axes a across, b up, centre height yc), continued straight down as a cylinder
    of radius a to y_lo."""
    from build123d import Axis, BuildLine, BuildPart, BuildSketch, EllipticalCenterArc, Line, Plane, Pos, make_face, revolve

    with BuildPart() as bp:
        with BuildSketch(Plane.XY):
            with BuildLine():
                EllipticalCenterArc((0, yc), a, b, 0, arc_size=90)
                Line((0, yc + b), (0, y_lo))
                Line((0, y_lo), (a, y_lo))
                Line((a, y_lo), (a, yc))
            make_face()
        revolve(axis=Axis.Y)
    return Pos(axis[0], 0, axis[1]) * bp.part


def elliptic_prism_z(a: float, b: float, xc: float, yc: float, z0: float, z1: float):
    """An elliptic cylinder along Z (semi-axes a in X, b in Y), from z0 to z1."""
    from build123d import BuildPart, BuildSketch, Ellipse, Locations, Plane, extrude

    with BuildPart() as bp:
        with BuildSketch(Plane.XY.offset(z0)):
            with Locations((xc, yc)):
                Ellipse(a, b)
        extrude(amount=z1 - z0)
    return bp.part


def mesh_solid(path, to_design=None, simplify: float = 0.05):
    """A clearance-cut mesh (an STL in the shell's reference frame) as build123d Solids, carried
    into the part's design frame by `to_design` (4x4). The mesh is repaired first (merged, zero-volume
    debris dropped, holes filled, made manifold by manifold3d, simplified within `simplify` mm); a
    mesh that still is not a closed solid raises."""
    import numpy as np
    import trimesh

    m = trimesh.load(str(path), force="mesh")
    m.merge_vertices()
    m.update_faces(m.nondegenerate_faces())
    # drop zero-volume debris, close what is left, then let manifold3d make it a manifold
    parts = [c for c in m.split(only_watertight=False) if abs(c.volume) > 1e-3]
    if not parts:
        raise ValueError(f"clearance cut {path}: no volume in the mesh")
    m = trimesh.util.concatenate(parts)
    trimesh.repair.fill_holes(m)
    trimesh.repair.fix_normals(m)
    import manifold3d

    man = manifold3d.Manifold(manifold3d.Mesh(np.asarray(m.vertices, np.float32), np.asarray(m.faces, np.uint32)))
    if man.status() != manifold3d.Error.NoError or man.volume() <= 0:
        raise ValueError(f"clearance cut {path}: repair failed ({man.status()})")
    vol = man.volume()
    man = man.simplify(simplify)
    if abs(man.volume() - vol) > 0.005 * vol:
        raise ValueError(f"clearance cut {path}: simplifying changed the volume by more than 0.5 %")
    solids = []
    for piece in man.decompose():
        out = piece.to_mesh()
        pm = trimesh.Trimesh(out.vert_properties[:, :3], out.tri_verts, process=True)
        pm.merge_vertices()
        if pm.volume <= 1e-6:
            continue
        if to_design is not None:
            pm.apply_transform(to_design)
        solids.append(_sewn_solid(pm, path))
    if not solids:
        raise ValueError(f"clearance cut {path}: nothing left after repair")
    return solids


def _sewn_solid(m, path):
    from OCP.BRepBuilderAPI import (BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon, BRepBuilderAPI_MakeSolid,
                                    BRepBuilderAPI_Sewing)
    from OCP.gp import gp_Pnt
    from OCP.TopoDS import TopoDS
    from build123d import Solid

    sew = BRepBuilderAPI_Sewing(1e-4)
    for tri in m.triangles:
        poly = BRepBuilderAPI_MakePolygon(*[gp_Pnt(*map(float, p)) for p in tri], True)
        sew.Add(BRepBuilderAPI_MakeFace(poly.Wire()).Face())
    sew.Perform()
    solid = Solid(BRepBuilderAPI_MakeSolid(TopoDS.Shell_s(sew.SewedShape())).Solid())
    if solid.volume < 0:
        solid = Solid(solid.wrapped.Reversed())
    if not solid.is_valid or abs(solid.volume - m.volume) > 1e-3 * abs(m.volume) + 1e-6:
        raise ValueError(f"clearance cut {path}: could not build a valid solid from the mesh")
    return solid


CUT_CACHE_ENV = "R3X_CUT_CACHE"        # directory override; "0" / "off" disables the cache


def _cut_cache_dir():
    import os
    from pathlib import Path

    v = os.environ.get(CUT_CACHE_ENV, "")
    if v.lower() in ("0", "off", "false", "no"):
        return None
    return Path(v) if v else Path.home() / ".cache" / "r3x-mech" / "clearance_cuts"


def clearance_cut_key(params: dict, cuts, export_frame, sources=()) -> str:
    """The cache key of a cut shell: the shell's params (the cut list aside), the sources that build
    it, and each cut mesh's contents (not its path or mtime) plus the frame it is carried through."""
    import hashlib
    import json
    from pathlib import Path

    import numpy as np

    h = hashlib.sha256()
    shell = {k: v for k, v in params.items() if k != "clearance_cuts"}
    h.update(json.dumps(shell, sort_keys=True, default=repr).encode())
    here = Path(__file__)
    for src in (*sources, here, here.with_name("_common.py")):   # plus the repair / sewing path here
        h.update(Path(src).read_bytes())
    h.update(np.round(np.asarray(export_frame, float), 9).tobytes())
    for c in cuts:
        h.update(hashlib.sha256(Path(c).read_bytes()).digest())
    return h.hexdigest()[:32]


def apply_clearance_cuts(body, cuts, export_frame, params: dict | None = None, sources=()):
    """Subtract each clearance-cut mesh (reference-STL frame) from a shell built in its design frame.

    The cut is slow (a 158k-triangle repair and a boolean, ~2 min for the top), so when `params` is
    given the result is cached as BREP, keyed by clearance_cut_key(): it reruns only when the shell's
    params, its building sources or a cut mesh's contents change."""
    import numpy as np

    cuts = list(cuts or [])
    if not cuts:
        return body
    cache = _cut_cache_dir() if params is not None else None
    path = None
    if cache is not None:
        from build123d import import_brep

        path = cache / f"{clearance_cut_key(params, cuts, export_frame, sources)}.brep"
        if path.exists():
            try:
                cached = import_brep(str(path))
                if cached.is_valid:
                    return cached
            except Exception:
                pass                        # a torn / foreign file: rebuild and overwrite it
    to_design = np.linalg.inv(export_frame)
    for c in cuts:
        for piece in mesh_solid(c, to_design):
            body = body - piece
    if path is not None:
        import os

        from build123d import export_brep

        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        export_brep(body, str(tmp))
        os.replace(tmp, path)               # atomic: concurrent builds never read a half-written file
    return body
