# Assembly test suite

`python -m workbench test <assembly>` (from `mech/`) builds the assembly without exporting,
runs every test below, prints a table, writes `out/<id>/test_report.{json,md}` and exits 1 on
any open failure. `python -m workbench build <assembly>` runs the same suite and puts the
results in the manifest's `checks` (kind `test`), so Build's Checks tab lists them, each with
the parts it names and a pose that shows it. In pytest: `workbench/tests/test_assemblies.py`
(set `R3X_MECH_SUITE=1`; it needs the vendored sources, so it skips without them).

**Speed.** Analytic tests (connectivity, mates, engagement, linkage closure, travel, leverage)
do no mesh work. Geometric tests run on `workbench/collide.py`: an FCL BVH per part (built once,
only transforms change per pose), a vectorised AABB broad phase, FCL distance / collision in
the narrow phase, and exact depth (manifold3d intersection) only for pairs that intersect.
Pairs on the same rigid link and mated pairs are never tested for clearance; the rest are
grouped by the joints that move them and each group is swept on its own grid (a clearance map);
show-clip keyframes are checked only for pairs the map finds within 3 mm. Every result is
cached per part pair by geometry hash (`out/.cache/suite/`), so an edit recomputes only the
pairs that involve the changed parts. Hunter's head, quick mode: ~30 s cold, ~0.3 s warm;
`--full` uses 1 deg sweeps and 5 deg grids. Each check reports its time (Build shows it).

A module can declare `EXPLAINED`: failures traced to a named cause, each with a proposed
fix, matched by test id and part-name patterns. A matched failure is reported as `explained`
with its cause and fix, never hidden; anything unmatched still fails. `TOLERANCES` overrides
the defaults below.

Everything is measured on real geometry: vendor STEPs (goBILDA), our parametric parts and
their reference meshes, and features measured from them (holes by section, faces by ray).

## Connected

| id | test | tolerance |
|---|---|---|
| `connected` | every part and placed fastener reaches the root part through mates (joints connect links only through their pivot/bearing mates) | none floating |
| `fasteners_used` | every placed fastener is in a mate; BOM fastener counts = placed + listed-unplaced, per key | exact |

## Mates hold

| mate | check | tolerance |
|---|---|---|
| `concentric`, `threaded`, `spline` | axis angle, axis-to-axis distance | 1.0 deg, 0.3 mm |
| `spline` | tooth counts equal | exact |
| `seated` | opposed normals, face gap | 1.0 deg, 0.3 mm |
| `coplanar` | equal normals, face gap (flush faces: an insert's top) | 1.0 deg, 0.3 mm |
| `ball_link` | ball centres coincide | 0.2 mm |
| `glue`, `press` (contact) | the two parts touch | 0.5 mm |

## Fasteners are real

| id | check | tolerance |
|---|---|---|
| `fasteners_real` | a screw's shank fits every clearance hole it passes (hole >= nominal d); its thread engages past the grip by at least 1.5 d into a heat-set insert, 2 d into printed plastic, 1 d into metal, 1.05 d through a lock nut (all threads + the nylon); the tip does not bottom out in a blind hole | as listed |
| `tool_access` | a straight line out of each screw head reaches 60 mm clear, against only the parts fitted up to that screw's step | 60 mm |

Screw lengths are chosen, not guessed: the smallest standard length that engages the minimum
without bottoming out (`hardware.screw_len`).

## No overlaps

| id | check | tolerance |
|---|---|---|
| `no_overlap` | every pair at rest: the deepest point of one closed mesh inside the other (exact distance to the surface). Mated pairs may only touch: press fits and tapped threads (modelled at the minor diameter: 0.38 mm under a nominal M4 shank) may overlap 0.45 mm; unmated pairs 0.1 mm | 0.45 / 0.1 mm |

## Moves correctly

Poses: every joint swept 1 deg at a time through its limits; the tilt x roll grid at 5 deg;
every keyframe of every show clip that moves these joints (`show/clips/*.json`, intensity 1).

| id | check | tolerance |
|---|---|---|
| `linkage_closure` | every push rod solves (closed form) and keeps its length | 0.01 mm |
| `servo_travel` | no servo turns past its travel from centre | +/- 135 deg (goBILDA 2000, standard mode) |
| `ball_link_angle` | the rod stays within its rod ends' swivel (angle out of the plane square to each ball stud) | 25 deg (inferred: goBILDA does not publish it) |
| `leverage` | the best rod keeps d(servo)/d(joint) on every pose (a fold-over or singularity fails) | >= 0.25 servo deg per joint deg |
| `clearance` | across all those poses, a gap between moving neighbours (different links, linkage parts) never closes below the print tolerance | 1.0 mm (configurable) |

## Printable

| id | check | tolerance |
|---|---|---|
| `printable_bed` | each printed part fits the bed in some axis-aligned orientation | Bambu H2D, 325 x 320 x 325 mm, until confirmed |
| `printable_wall` | 2nd-percentile wall thickness (rays inward from 3000 surface samples, closed meshes) | 0.8 mm (warn) |

## Remodel regressions (parts library)

`mech/parts/tests/test_parts.py` checks each parametric remodel against its reference STL at
default parameters: bounding box, volume ratio and two-way surface distance (Hausdorff, 95th
percentile, mean), and that every vendor STEP maps onto its canonical frame.
