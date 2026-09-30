# mech/ - DJ R3X engineering model

Code-CAD for the physical droid: the printable kit shell as the backbone, with the R-3X
Animation mechanisms (servos, gears, linkages) and the community frame parts attached. Everything
is in the canonical body frame from `show/SPEC.md` "Frame and zeros" (mm, +Y up, +Z front,
+X the droid's left, every joint at 0 = rest).

Phase A (this state) assembles what exists and checks it. No new parts yet. Read
`INVENTORY.md` (what every source file is) and `COMPARISON.md` (geometry vs the profile/sim)
first.

## Setup

```bash
cd mech
uv venv .venv --python 3.12          # gitignored
uv pip install --python .venv/bin/python -r requirements.txt
```

The vendored sources are **not in git** (see Licensing). Fetch them from the Mac Studio
(read-only there) into `mech/vendor/`:

```bash
S="ssh -o BatchMode=yes -i ~/.ssh/morpho_macstudio_ed25519 brandoncullum@100.124.169.68"
$S 'cd ~/Library/CloudStorage/GoogleDrive-brandon@makeorbreakshop.com/My\ Drive/MOB/Projects/DJ-R3X/R-3X\ Animation && tar cf - --exclude .DS_Store .' | tar xf - -C vendor/animation
$S 'cat ~/Desktop/DJ-R3X/3D\ Models/DJ\ R3X\ -\ v2.zip' > vendor/kit/DJ_R3X_v2.zip && (cd vendor/kit && unzip -q DJ_R3X_v2.zip -d unz)
$S 'cd ~/Desktop/DJ-R3X/Reference\ Photos && tar cf - --exclude venue-video .' | tar xf - -C vendor/refs
```

(zsh does not word-split `$S`; use a function `S(){ ssh ... "$@"; }` there.) The community
files (`vendor/dropbox/`) and Hunter's head (`vendor/hunter_head/`) came from Brandon directly.

## Run

```bash
.venv/bin/python assembly.py                  # joint table + evidence for every axis/pivot
.venv/bin/python assembly.py --render         # proof renders -> out/renders/ (local only)
.venv/bin/python assembly.py --checks         # mass, torque vs servo, interference sweeps -> out/checks.json
.venv/bin/python -m workbench build kit       # manifest + per-part GLBs -> out/kit/ (workbench, SCHEMA.md)
.venv/bin/python -m r3xmech.stepfeat          # exact cylinder axes from the R-3X STEPs -> out/step_features.json
.venv/bin/python -m r3xmech.inventory         # regenerates the per-file tables in INVENTORY.md
```

`--checks` takes ~5 min (1 deg / 1 mm sweeps); `--quick` halves the resolution.

## Layout

| path | what | in git |
|---|---|---|
| `assembly.py` | Phase A entry: summary, renders, checks | yes |
| `assemblies/kit/` | kit backbone: every Large Cut part placed, sub-assembly tree, links/joints, guide steps + BOM | yes (code) |
| `assemblies/kit/_guide/` | transcription of the kit guide (steps, McMaster hardware, BOM) | **no** (licensed) |
| `assemblies/r3x_animation/` | R-3X Animation mechanisms placed onto the kit, with evidence per mate | yes |
| `assemblies/community/` | Morton 2020 cage (default frame), Randall frame (alternate), Mic-Mouth-Split (alternate) | yes |
| `assemblies/hunter_head/`, `workbench/` | Hunter's head mech and the manifest builder - owned by the workbench agent | yes |
| `r3xmech/` | our library: frames, model + FK, loaders, catalogue, checks, renderer, feature extraction | yes |
| `requirements.txt` | pinned venv | yes |
| `vendor/` | kit, R-3X Animation, community, Hunter, reference photos | **no** |
| `out/` | renders, checks, caches, workbench output | **no** |

`assemblies/kit/assembly.py` exposes `build_model()` (our `r3xmech.model.Asm`, used by the
checks) and `build()` (the workbench's `Assembly`, for `python -m workbench build kit`).
Variants are marked on a child's `mount.variant = {group, id, default}`: `head_mech`
(hunter = default, r3x_anderson), `base_frame` (morton = default, randall), `mouth`.

## Licensing

The kit ("DJ R3X - v2", Patrick Gray / David Ferreira) is CC BY-NC and group-only; the
R-3X Animation build (Brian Anderson) and the community files are other builders' work. They
live only in `mech/vendor/` (gitignored). Never commit them, renders of them, meshes derived
from them (`out/`), or the guide transcription (`assemblies/kit/_guide/`). What is ours and in
git: the code, the placements/mates as numbers with their evidence, and the docs.

## build123d-mcp

`.mcp.json` registers `build123d` (`mech/.venv/bin/build123d-mcp`, v0.3.90, stdio): execute
build123d scripts, `render_view`, `measure`, `find_holes`, `cross_sections`, `mesh_section`,
`import_cad_file`. It needs the venv above; Claude Code starts it from the repo root. Tested:
initialize + tools/list (48 tools).

## Conventions for new parts (Phase B)

- New parametric parts in build123d under `parts/`, one module per part, parameters at the
  top, exported STEP + STL into `out/`.
- Mesh boolean/clearance work on vendored meshes with manifold3d/trimesh.
- Every placement carries `evidence` and `placement = fitted | inferred`; every joint axis
  comes from geometry, never from the profile.
