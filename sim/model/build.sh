#!/usr/bin/env bash
# Rebuild the sim model from the DJ R3X v2 printable kit.
# Usage: sim/model/build.sh [path-to-kit.zip-or-dir] [--preview]
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
kit="${1:-$HOME/Desktop/DJ-R3X/3D Models/DJ R3X - v2.zip}"
shift || true
blender -b -P "$here/build_r3x.py" -- --kit "$kit" --out "$here/../web/public/model" "$@"
