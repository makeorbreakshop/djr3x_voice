"""Extract exact features (cylinder axes, planes, bbox, volume) from the vendored STEP files.

Writes vendor-derived numbers to out/step_features.json (gitignored: it is a digest of
licensed geometry). Run: .venv/bin/python -m r3xmech.stepfeat
"""
from __future__ import annotations

import glob
import json
import os
import sys
from collections import defaultdict

import numpy as np
from build123d import GeomType, import_step
from OCP.BRepAdaptor import BRepAdaptor_Surface

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def features(path):
    s = import_step(path)
    bb = s.bounding_box()
    cyl = defaultdict(float)
    for f in s.faces():
        if f.geom_type != GeomType.CYLINDER:
            continue
        c = BRepAdaptor_Surface(f.wrapped).Cylinder()
        r = round(c.Radius(), 3)
        dv, lv = c.Axis().Direction(), c.Axis().Location()
        d = (round(dv.X(), 3), round(dv.Y(), 3), round(dv.Z(), 3))
        if d < (0, 0, 0):
            d = tuple(-x + 0.0 for x in d)
        # canonical point on the axis: projection of the origin
        pp = np.array([lv.X(), lv.Y(), lv.Z()]); dd = np.array(d, float); dd /= np.linalg.norm(dd)
        pp = pp - dd * (pp @ dd)
        key = (r, d, tuple(np.round(pp, 2)))
        cyl[key] += f.area
    return dict(
        bbox=[[bb.min.X, bb.min.Y, bb.min.Z], [bb.max.X, bb.max.Y, bb.max.Z]],
        volume_mm3=s.volume,
        n_solids=len(s.solids()),
        cylinders=sorted(
            [dict(r=k[0], axis=k[1], point=k[2], area=round(a, 1)) for k, a in cyl.items()],
            key=lambda c: -c["area"]),
    )


def main():
    out = {}
    files = sorted(glob.glob(os.path.join(ROOT, "vendor/animation/r3x-internal-step/*.step")))
    for f in files:
        name = os.path.basename(f).replace("r3x-internal - ", "").replace(".step", "")
        try:
            out[name] = features(f)
            print(name, "ok", flush=True)
        except Exception as e:  # keep going; report
            out[name] = dict(error=str(e)); print(name, "ERR", e, flush=True)
    os.makedirs(os.path.join(ROOT, "out"), exist_ok=True)
    json.dump(out, open(os.path.join(ROOT, "out/step_features.json"), "w"), indent=1)


if __name__ == "__main__":
    sys.exit(main())
