"""Texture transfer (workbench/uvtransfer.py) on a synthetic Original: per-region nearest points, the seam rule,
the registration, and a whole assembly run with its incremental skip. No kit data, no mech/out.

Run: cd mech && .venv/bin/python -m pytest workbench/tests/test_uvtransfer.py -q
"""

from __future__ import annotations

import json

import numpy as np
import trimesh

from workbench import uvtransfer as u
from workbench.build import _glb


def _grid(x0, x1, z0, z1, n, y=0.0):
    """A y = const plane of n x n quads as (P, F)."""
    xs, zs = np.linspace(x0, x1, n + 1), np.linspace(z0, z1, n + 1)
    X, Z = np.meshgrid(xs, zs, indexing="ij")
    P = np.column_stack([X.ravel(), np.full(X.size, y), Z.ravel()])
    i = np.arange(n)[:, None] * (n + 1) + np.arange(n)[None, :]
    a, b, c, d = i, i + (n + 1), i + (n + 1) + 1, i + 1
    F = np.concatenate([np.stack([a, b, c], -1).reshape(-1, 3), np.stack([a, c, d], -1).reshape(-1, 3)])
    return P, F


def _uv_left(p):
    return np.column_stack([p[:, 0] / 200, p[:, 2] / 100])


def _uv_right(p):
    return np.column_stack([0.5 + p[:, 0] / 200, p[:, 2] / 100])


def _original() -> u.Atlas:
    """A 100 x 100 mm floor in two UV islands split at x = 50 (vertices duplicated along the seam, as glTF
    does), fine triangles; plus a far-off huge pair of triangles that no part's region should reach."""
    Pl, Fl = _grid(0, 50, 0, 100, 20)
    Pr, Fr = _grid(50, 100, 0, 100, 20)
    Pf = np.array([[5000, 0, 5000], [9000, 0, 5000], [9000, 0, 9000], [5000, 0, 9000]], float)
    Ff = np.array([[0, 1, 2], [0, 2, 3]])
    P = np.concatenate([Pl, Pr, Pf])
    U = np.concatenate([_uv_left(Pl), _uv_right(Pr), np.zeros((4, 2))])
    F = np.concatenate([Fl, Fr + len(Pl), Ff + len(Pl) + len(Pr)])
    return u.Atlas("floor", P, U, F)


def test_nearest_points_match_brute_force_with_large_triangles():
    """Small triangles and one huge one (split into pieces): within the distance that matters (MAX_MM) the
    search agrees with trimesh's exhaustive one - nearly always exactly, never by more than a fraction of a mm."""
    rng = np.random.default_rng(3)
    Pg, Fg = _grid(0, 100, 0, 100, 25)  # small triangles
    big = np.array([[-400, 30, -400], [500, 30, -400], [500, 30, 500]], float)  # one huge one, above
    P = np.concatenate([Pg, big])
    F = np.concatenate([Fg, [[len(Pg), len(Pg) + 1, len(Pg) + 2]]])
    a = u.Atlas("mix", P, np.zeros((len(P), 2)), F)
    Q = rng.uniform([-20, -5, -20], [120, 40, 120], (2000, 3))
    r = a.region(Q.min(0) - 50, Q.max(0) + 50)
    _p, d, tri = r.closest(Q, None, limit=u.MAX_MM)
    _p2, d2, _t2 = trimesh.proximity.closest_point(trimesh.Trimesh(P, F, process=False), Q)
    near = d2 < u.MAX_MM
    err = (d - d2)[near]
    assert near.sum() > 500 and err.min() > -1e-9  # never nearer than the surface
    assert np.quantile(err, 0.99) < 1e-6 and err.max() < 0.25
    assert (tri[near] == len(F) - 1).any() and (tri[near] < len(F) - 1).any()  # both kinds won somewhere


def test_project_takes_the_floor_uvs_and_never_smears_a_seam():
    a = _original()
    # the part: a coarser plate 1 mm above, its faces straddling the seam
    P, F = _grid(3, 97, 3, 97, 7, y=1.0)
    region = a.region(P.min(0) - u.MARGIN_MM, P.max(0) + u.MARGIN_MM)
    assert region.n == len(a.F) - 2  # the far pair is cropped out
    uv, ok = u.project(a, P, F, region)
    assert ok.all()
    uv = uv.reshape(-1, 3, 2).astype(np.float64)
    corners = P[F]
    left = corners.mean(axis=1)[:, 0] < 50
    # every corner on its face's centre island's mapping (carried past the seam where it crosses)
    want = np.where(left[:, None, None], _uv_left(corners.reshape(-1, 3)).reshape(-1, 3, 2),
                    _uv_right(corners.reshape(-1, 3)).reshape(-1, 3, 2))
    assert np.abs(uv - want).max() < 1e-4


def test_far_and_disagreeing_faces_are_not_covered():
    a = _original()
    Pw, Fw = _grid(10, 40, 10, 40, 4, y=0)  # a wall: the plate turned on edge, standing on the floor
    wall = Pw[:, [1, 0, 2]] + [20, 0, 0]
    high, Fh = _grid(10, 40, 10, 40, 4, y=u.MAX_MM + 5)  # parallel, but too far
    _uv, ok_wall = u.project(a, wall, Fw)
    _uv, ok_high = u.project(a, high, Fh)
    assert not ok_wall.any() and not ok_high.any()


def test_registration_recovers_an_offset():
    rng = np.random.default_rng(0)
    P, F = _grid(0, 100, 0, 100, 30)
    bump = np.linalg.norm(P[:, [0, 2]] - 50, axis=1) < 25
    P[bump, 1] += 10 * np.cos(np.linalg.norm(P[bump][:, [0, 2]] - 50, axis=1) / 25 * np.pi / 2)  # a dome
    a = u.Atlas("dome", P, np.zeros((len(P), 2)), F)
    W = P[rng.choice(len(P), 600, replace=False)] + [1.5, -2.0, 1.0]
    T, rms, share = u.register(a, W)
    assert np.allclose(T[:3, 3], [-1.5, 2.0, -1.0], atol=0.3), T[:3, 3]
    assert rms < 0.5 and share > 0.8


def test_transfer_writes_sidecars_per_folder_and_skips_what_did_not_change(tmp_path, monkeypatch):
    """An assembly with a nested sub-assembly: each mesh folder gets its uvtransfer.json (where the viewer
    reads it), the .uv.bin follows the GLB's index order, and a second run redoes nothing."""
    out = tmp_path / "out"
    asm = out / "toy"
    monkeypatch.setattr(u, "OUT", out)
    plate = trimesh.Trimesh(*_grid(-20, 20, -20, 20, 6, y=0.0), process=False)
    _glb(plate, asm / "parts" / "plate.glb")
    _glb(plate, asm / "sub" / "parts" / "plate2.glb")
    finish = {"paint": "paint_orange", "kit": "X"}
    root = {"parts": [{"id": "plate", "class": "shell", "transform": {"t": [30, 1, 50]},
                       "mesh": "parts/plate.glb", "finish": finish}],
            "children": [{"id": "sub", "mount": {"transform": {"t": [0, 0, 0]}},
                          "parts": [{"id": "plate2", "class": "shell", "transform": {"t": [70, 1, 50]},
                                     "mesh": "sub/parts/plate2.glb", "finish": finish}]}]}
    asm.mkdir(parents=True, exist_ok=True)
    (asm / "manifest.json").write_text(json.dumps({"root": root}))

    class Orig:  # the synthetic Original in Original's shape
        sig = "test"
        atlas = {u.BODY_ATLAS: _original()}

    logs = []
    s = u.transfer("toy", Orig(), log=lambda *a, **k: logs.append(" ".join(map(str, a))))
    assert {v["id"]: v["coverage"] for v in s["parts"].values()} == {"plate": 1.0, "plate2": 1.0}
    top, sub = json.loads((asm / "uvtransfer.json").read_text()), json.loads((asm / "sub" / "uvtransfer.json").read_text())
    assert top["parts"]["plate"]["textured"] and sub["parts"]["plate2"]["textured"]
    assert "plate2" not in top["parts"]
    V, F = u.read_glb(asm / "parts" / "plate.glb")
    uv = np.frombuffer((asm / "parts" / "plate.uv.bin").read_bytes(), np.float32).reshape(-1, 3, 2)
    assert len(uv) == len(F)
    centre = (V[F] + [30, 1, 50]).mean(axis=1)
    assert np.allclose(uv.mean(axis=1), _uv_left(centre), atol=2e-3)  # x 10..50: the left island

    logs.clear()
    u.transfer("toy", Orig(), log=lambda *a, **k: logs.append(" ".join(map(str, a))))
    assert any("0 transferred, 2 unchanged" in m for m in logs), logs
    logs.clear()
    u.transfer("toy", Orig(), only={"sub"}, force=True, log=lambda *a, **k: logs.append(" ".join(map(str, a))))
    assert any("1 transferred, 0 unchanged" in m for m in logs), logs
    assert json.loads((asm / "uvtransfer.json").read_text())["parts"]["plate"]["textured"]  # kept by --only
