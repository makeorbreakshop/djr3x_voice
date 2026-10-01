"""Display levels of detail (geom.display_lods): round things stay round, nothing becomes a blob."""
import numpy as np
import trimesh

from workbench import geom


def _radial_error(m: trimesh.Trimesh, r: float) -> float:
    """Worst distance (mm) of the side vertices from a radius-r cylinder about Z."""
    v = np.asarray(m.vertices)
    side = np.abs(v[:, 2]) < 4.9
    return float(np.abs(np.hypot(v[side, 0], v[side, 1]) - r).max()) if side.any() else 0.0


def test_parametric_overview_stays_within_tolerance_and_full_is_the_tessellation():
    wheel = trimesh.creation.cylinder(radius=12, height=10, sections=256)
    over, full = geom.display_lods(wheel, 300, parametric=True)
    assert len(over.faces) < len(wheel.faces)
    assert _radial_error(over, 12) < geom.PARAMETRIC_TOL + 1e-3  # still a circle
    assert full is wheel  # never reduced


def test_vendor_mesh_reduces_per_component_without_clustering():
    # two disjoint round parts, finely tessellated: a STEP leaf pair
    a = trimesh.creation.cylinder(radius=10, height=10, sections=512)
    b = trimesh.creation.cylinder(radius=6, height=10, sections=512)
    b.apply_translation([40, 0, 0])
    m = trimesh.util.concatenate([a, b])
    over = geom.reduce_mesh(m, 600)
    assert len(over.faces) <= 1.3 * 600 or len(over.faces) < len(m.faces) / 4
    assert len(over.split(only_watertight=False)) == 2  # both parts kept, neither a hull blob
