"""Parametric models of purchased parts, to the published spec, for parts whose vendor CAD is
not in the cache. Every model is in its kind's canonical frame (library.py FRAMES).

Screws follow ISO 4762 / 7380 / 10642 / 1580 (bd_warehouse) or ASME B18.3 for inch socket
heads; inch button/flat/pan/truss heads use the ASME head proportions below. Threads are not
modelled (display and clearance checks only). Servo cases are the datasheet envelopes.
"""

from __future__ import annotations

import math

import numpy as np
import trimesh
from shapely.geometry import Point, Polygon, box
from shapely.ops import unary_union

INCH_D = {"#0": 1.524, "#1": 1.854, "#2": 2.184, "#3": 2.515, "#4": 2.845, "#5": 3.175, "#6": 3.505,
          "#8": 4.166, "#10": 4.826, "#12": 5.486, "1/4": 6.35}
PITCH = {"M1.6": "0.35", "M2": "0.4", "M2.5": "0.45", "M3": "0.5", "M4": "0.7", "M5": "0.8", "M6": "1", "M8": "1.25"}


def nominal_d(thread: str) -> float:
    if thread.upper().startswith("M"):
        return float(thread[1:].split("-")[0].split("X")[0])
    return INCH_D[thread.split("-")[0]]


def _rotz_to_y() -> np.ndarray:
    return trimesh.transformations.rotation_matrix(-math.pi / 2, [1, 0, 0])


def _cyl(r, h, z0=0.0, sections=32):
    m = trimesh.creation.cylinder(radius=r, height=h, sections=sections)
    m.apply_translation([0, 0, z0 + h / 2])
    return m


def _tube(ri, ro, h, z0=0.0, sections=32):
    m = trimesh.creation.annulus(r_min=ri, r_max=ro, height=h, sections=sections)
    m.apply_translation([0, 0, z0 + h / 2])
    return m


def _revolve(profile, sections=40):
    """profile: [(r, z)] closed outline in the rz half-plane -> solid about +Z."""
    return trimesh.creation.revolve(np.asarray(profile, float), sections=sections)


def _bd(shape) -> trimesh.Trimesh:
    v, t = shape.tessellate(0.05, 0.3)
    return trimesh.Trimesh(np.array([(p.X, p.Y, p.Z) for p in v]), np.array(t))


# ------------------------------------------------------------------ fasteners (head at 0, shank +Z)

def screw(spec: dict) -> trimesh.Trimesh:
    kind = spec.get("type", "shcs")
    thread = spec["thread"]
    L = float(spec.get("length_mm", 10))
    d = nominal_d(thread)
    metric = thread.upper().startswith("M")
    head = None
    if metric:
        size = f"{thread.upper()}-{PITCH.get(thread.upper(), '0.7')}"
        try:
            from bd_warehouse import fastener as F

            cls, std = {
                "shcs": (F.SocketHeadCapScrew, "iso4762"), "bhcs": (F.ButtonHeadScrew, "iso7380_1"),
                "fhcs": (F.CounterSunkScrew, "iso10642"), "pan": (F.PanHeadScrew, "iso1580"),
                "shcs_low": (getattr(F, "LowProfileScrew", F.SocketHeadCapScrew),
                             "din7984" if hasattr(F, "LowProfileScrew") else "iso4762"),
            }.get(kind, (F.SocketHeadCapScrew, "iso4762"))
            if std not in cls.types():
                std = sorted(cls.types())[0]
            m = _bd(cls(size, L, std, simple=True))
            m.apply_transform(trimesh.transformations.rotation_matrix(math.pi, [1, 0, 0]))
            return m
        except Exception:
            pass
    else:
        try:
            if kind in ("shcs", "shcs_low"):
                from bd_warehouse import fastener as F

                m = _bd(F.SocketHeadCapScrew(thread, L, "asme_b18.3", simple=True))
                m.apply_transform(trimesh.transformations.rotation_matrix(math.pi, [1, 0, 0]))
                return m
        except Exception:
            pass
    # ASME/ISO head proportions by nominal diameter d: (r, z) points of the head, z <= 0
    if kind == "fhcs":  # 82 deg flat head, top face at z = 0, the cone inside the countersink
        rk = d
        hk = (rk - d / 2) / math.tan(math.radians(41))
        return _revolve([(0, 0), (rk, 0), (d / 2, hk), (d / 2, L), (0, L)])
    head = {
        "bhcs": [(0.95 * d, 0), (0.95 * d, -0.25 * d), (0.6 * d, -0.55 * d), (0, -0.58 * d)],
        "pan": [(d, 0), (d, -0.45 * d), (0.7 * d, -0.7 * d), (0, -0.7 * d)],
        "truss": [(1.25 * d, 0), (1.1 * d, -0.35 * d), (0, -0.45 * d)],
        "shcs_low": [(0.85 * d, 0), (0.85 * d, -0.6 * d), (0, -0.6 * d)],
    }.get(kind, [(0.75 * d, 0), (0.75 * d, -d), (0, -d)])
    return _outline(head, d, L)


def _outline(head, d, L):
    """Head points (r, z<=0), from the rim inward, + a shank to z=L: one closed rz outline."""
    pts = [(0.0, L), (d / 2, L), (d / 2, 0.0)] + list(head)
    return _revolve(pts)


def insert(spec: dict) -> trimesh.Trimesh:
    """Heat-set insert: top at 0, body +Z. McMaster tapered ones taper ~8% over the length."""
    d = nominal_d(spec["thread"])
    L = float(spec.get("length_mm", 1.5 * d))
    od = float(spec.get("od_mm") or 1.45 * d)
    taper = 0.92 if spec.get("tapered") else 1.0
    prof = [(d / 2, 0), (od / 2, 0), (od / 2 * taper, L), (d / 2, L)]
    return trimesh.creation.revolve(np.array(prof), sections=24)


def nut(spec: dict) -> trimesh.Trimesh:
    """ISO 4032 hex nut (M4: 7 across flats, 3.2 thick) with its bore; a lock nut adds the
    ISO 10511 nylon collar (M4: 5 mm overall)."""
    d = nominal_d(spec["thread"])
    s = 1.75 * d if d <= 4 else 1.6 * d
    m = 0.8 * d
    r = s / math.sqrt(3)
    hexa = Polygon([(r * math.cos(k * math.pi / 3), r * math.sin(k * math.pi / 3)) for k in range(6)])
    prof = hexa.difference(Point(0, 0).buffer(d * 0.42, resolution=16))  # minor diameter
    parts = [trimesh.creation.extrude_polygon(prof, m)]
    if spec.get("type") == "lock_nut":
        parts.append(_tube(d * 0.42, s * 0.45, 0.45 * d, m))
    return trimesh.util.concatenate(parts)


def disc_horn(spec: dict) -> trimesh.Trimesh:
    """Round aluminium servo horn: OD, thickness, a centre bore and M3 tapped holes on a square."""
    od, t = spec.get("od_mm", 20.0), spec.get("t_mm", 3.0)
    sq = spec.get("square_mm", 10.0) / 2
    prof = Point(0, 0).buffer(od / 2, resolution=32).difference(Point(0, 0).buffer(1.6, resolution=12))
    for x in (-sq, sq):
        for y in (-sq, sq):
            prof = prof.difference(Point(x, y).buffer(1.25, resolution=12))
    return trimesh.creation.extrude_polygon(prof, t)


def washer(spec: dict) -> trimesh.Trimesh:
    d = nominal_d(spec["thread"]) if "thread" in spec else float(spec.get("id_mm", 4))
    ri = float(spec.get("id_mm", d + 0.3)) / 2
    ro = float(spec.get("od_mm", 2.25 * d)) / 2
    return _tube(ri, ro, float(spec.get("t_mm", 0.8)))


def magnet(spec):
    return _cyl(spec["od_mm"] / 2, spec["t_mm"], -spec["t_mm"] / 2)


def pin(spec):
    return _cyl(spec["d_mm"] / 2, spec["length_mm"])


def standoff(spec):
    L = spec["length_mm"]
    if spec.get("shape") == "hex":
        m = trimesh.creation.cylinder(radius=spec["od_mm"] / math.sqrt(3), height=L, sections=6)
        m.apply_translation([0, 0, L / 2])
        return m
    return _tube(1.6, spec["od_mm"] / 2, L)


def threaded_rod(spec):
    return _cyl(nominal_d(spec["thread"]) / 2, float(spec["length_mm"]), sections=16)


def tube(spec):
    """Along +Z from 0."""
    return _tube(spec["id_mm"] / 2, spec["od_mm"] / 2, spec["length_mm"], sections=48)


# ------------------------------------------------------------------ motion hardware

def bearing(spec):
    """Axis +Z, centred."""
    ri, ro, w = spec["id_mm"] / 2, spec["od_mm"] / 2, spec["width_mm"]
    if spec.get("type") == "sleeve":
        return _tube(ri, ro, w, -w / 2)
    parts = [_tube(ri, ro, w, -w / 2)]
    if spec.get("type") == "flanged":
        parts.append(_tube(ri, spec["flange_od_mm"] / 2, spec["flange_mm"], w / 2 - spec["flange_mm"]))
    return trimesh.util.concatenate(parts)


def rod_end(spec):
    """Ball joint rod end (DIN ISO 12240-4 E, M6): ball centre at 0, shank +Z."""
    d = spec.get("bore_mm", 6)
    eye = trimesh.creation.annulus(r_min=d / 2, r_max=10.0, height=9.0, sections=32)
    eye.apply_transform(trimesh.transformations.rotation_matrix(math.pi / 2, [0, 1, 0]))
    neck = _cyl(4.5, 12.0, 8.0)
    shank = _cyl(3.0, 12.0, 20.0, 16)
    ball = trimesh.creation.icosphere(subdivisions=2, radius=6.2)
    return trimesh.util.concatenate([eye, neck, shank, ball])


def ball_link(spec):
    """Female ball linkage: ball centre at 0, body along +Z (goBILDA 2913: 24.1 mm overall)."""
    L = float(spec.get("length_mm", 24.1))
    ball = trimesh.creation.icosphere(subdivisions=2, radius=3.2)
    socket = trimesh.creation.annulus(r_min=3.0, r_max=4.6, height=6.0, sections=24)
    socket.apply_transform(trimesh.transformations.rotation_matrix(math.pi / 2, [0, 1, 0]))
    body = _cyl(3.4, L - 5.0, 3.0, 16)
    return trimesh.util.concatenate([ball, socket, body])


def linear_rail(spec):
    """MGN12 rail (12 x 8, holes at 25 mm pitch) along +Z from 0, with an MGN12H carriage at its middle."""
    L = float(spec.get("length_mm", 200))
    rail = trimesh.creation.box([12, 8, L])
    rail.apply_translation([0, 4, L / 2])
    if spec.get("carriage") is False:
        return rail
    car = trimesh.creation.box([27, 10, 45.4])
    car.apply_translation([0, 3 + 5, L / 2])
    return trimesh.util.concatenate([rail, car])


def carriage(spec):
    """MGN12H carriage alone (27 x 45.4 x 10 body, 13 mm over the rail base): centred, along +Z."""
    body = trimesh.creation.box([27.0, 10.0, 45.4])
    body.apply_translation([0, 8.0, 0])
    return body


def lazy_susan(spec):
    """Two square plates with the ball race between (6 in: 152 x 152 x 8; 10 in: 254 x 254 x 8), axis +Y."""
    s = spec["size_in"] * 25.4
    top = trimesh.creation.box([s, 1.2, s])
    top.apply_translation([0, 7.4, 0])
    bot = trimesh.creation.box([s, 1.2, s])
    bot.apply_translation([0, 0.6, 0])
    race = trimesh.creation.annulus(r_min=0.33 * s, r_max=0.42 * s, height=5.6, sections=64)
    race.apply_transform(_rotz_to_y())
    race.apply_translation([0, 4.0, 0])
    return trimesh.util.concatenate([top, bot, race])


def extrusion(spec):
    """2020 T-slot (5 series: 6.2 mm slot, 4.2 mm centre bore) along +Y from 0; profile "vslot2020":
    OpenBuilds V-slot (the slot's mouth a 90 deg V, 9.2 wide at the face, for V-wheels)."""
    L = float(spec.get("length_mm", 100))
    sq = box(-10, -10, 10, 10)
    slots = []
    v = spec.get("profile") == "vslot2020"
    for ang in (0, 90, 180, 270):
        # 6.2 mm opening, 1.8 mm lips, the T cavity tapering to the 7.5 mm core (webs to the corners stay)
        mouth = [(-4.6, 10.01), (4.6, 10.01), (3.1, 8.5)] if v else [(-3.1, 10.01), (3.1, 10.01)]
        t = Polygon(mouth + [(3.1, 8.2), (5.5, 8.2), (5.5, 6.2), (3.2, 4.8), (-3.2, 4.8),
                             (-5.5, 6.2), (-5.5, 8.2), (-3.1, 8.2)] + ([(-3.1, 8.5)] if v else []))
        from shapely import affinity

        slots.append(affinity.rotate(t, ang, origin=(0, 0)))
    prof = sq.difference(unary_union(slots)).difference(Point(0, 0).buffer(2.1, resolution=16))
    if prof.geom_type == "MultiPolygon":
        prof = max(prof.geoms, key=lambda g: g.area)
    m = trimesh.creation.extrude_polygon(prof, L)
    m.apply_transform(_rotz_to_y())
    if m.bounds[0][1] < -1:
        m.apply_translation([0, L, 0])
    return m


def t_nut(spec):
    d = nominal_d(spec.get("thread", "M5"))
    body = trimesh.creation.box([10, 4, 11.5])  # slides in the slot, thread along +Y
    body.apply_translation([0, 2, 0])
    wing = trimesh.creation.box([6, 2.5, 11.5])
    wing.apply_translation([0, 5.25, 0])
    return trimesh.util.concatenate([body, wing])


# ------------------------------------------------------------------ servos (spline top at 0, axis +Y)

CASES = {
    # body L x W x H (top of case below the spline), flange length/thickness/height from the case
    # bottom, spline offset from the body centre along the long axis, spline dia/height
    "standard": dict(L=40.0, W=20.0, H=37.2, fl=54.5, ft=2.5, fh=26.5, off=10.0, sd=5.9, sh=4.3, horn=None),
    "large": dict(L=65.0, W=30.0, H=48.0, fl=84.0, ft=3.0, fh=37.0, off=17.0, sd=7.0, sh=5.0, horn=None),
    "micro": dict(L=23.0, W=12.2, H=24.5, fl=32.5, ft=2.0, fh=16.0, off=6.0, sd=4.8, sh=3.0, horn=None),
}


def servo(spec):
    c = CASES[spec.get("case", "standard")]
    top = -c["sh"]  # case top below the spline top
    body = trimesh.creation.box([c["L"], c["H"], c["W"]])
    body.apply_translation([c["off"], top - c["H"] / 2, 0])
    fl = trimesh.creation.box([c["fl"], c["ft"], c["W"]])
    fl.apply_translation([c["off"], top - c["H"] + c["fh"], 0])
    boss = _cyl(c["sd"] * 1.2, 1.5, 0).apply_transform(_rotz_to_y())
    boss.apply_translation([0, top, 0])
    spline = _cyl(c["sd"] / 2, c["sh"] - 1.5, 0, 20).apply_transform(_rotz_to_y())
    spline.apply_translation([0, top + 1.5, 0])
    parts = [body, fl, boss, spline]
    if spec.get("dual_shaft"):
        back = _cyl(c["sd"] / 2, 4.0, 0, 20).apply_transform(_rotz_to_y())
        back.apply_translation([0, top - c["H"] - 4.0, 0])
        parts.append(back)
    return trimesh.util.concatenate(parts)


def board(spec):
    x, y, z = spec.get("size_mm", [50, 30, 1.6])
    m = trimesh.creation.box([x, z, y])
    m.apply_translation([0, z / 2, 0])
    return m


GENERATORS = {
    "screw": screw, "insert": insert, "nut": nut, "washer": washer, "magnet": magnet, "pin": pin,
    "standoff": standoff, "threaded_rod": threaded_rod, "tube": tube, "bearing": bearing, "rod_end": rod_end,
    "ball_link": ball_link, "linear_rail": linear_rail, "lazy_susan": lazy_susan, "extrusion": extrusion,
    "t_nut": t_nut, "servo": servo, "disc_horn": disc_horn, "carriage": carriage,
}
# Kinds that are only a box of the right size: shown as `placeholder`.
PLACEHOLDERS = {"electronics": board}
