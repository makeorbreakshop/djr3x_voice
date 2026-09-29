#!/usr/bin/env bash
# Rebuild the sim model from the DJ R3X v2 printable kit.
# Usage: sim/model/build.sh [path-to-kit.zip-or-dir] [--preview] [--no-bake] [--budget N]
# Needs Blender 4.2+, Node (sim/web deps installed) and basisu (brew install basis_universal).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
kit="${1:-$HOME/Desktop/DJ-R3X/3D Models/DJ R3X - v2.zip}"
shift || true
out="$here/../web/public/model"
work="$here/.work"
# 1. Blender: geometry, rig.json, UV atlases and baked texture sets -> .work/
PYTHONUNBUFFERED=1 blender -b -P "$here/build_r3x.py" -- --kit "$kit" --out "$out" --work "$work" "$@"
# 2. Node: textures into the glTF PBR slots, KTX2 + Draco -> public/model/r3x.glb
(cd "$here/../web" && node scripts/pack-model.mjs --work "$work" --out "$out")
# Masks from the old runtime-weathering build are no longer used.
rm -f "$out/r3x_occlusion.jpg" "$out/r3x_edges.jpg"
