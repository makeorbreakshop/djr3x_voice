#!/usr/bin/env bash
# Link mech/vendor to the shared vendor files in Google Drive.
#
# The kit, community and grnwave files are shared with us under terms that don't allow
# public redistribution, so they live in Google Drive, not in this (public) repo:
#   My Drive/MOB/Projects/DJ-R3X/r3x-vendor
# Run this once per machine (needs Google Drive for desktop). R3X_VENDOR_DIR overrides the
# source. An existing real mech/vendor folder is left alone.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
dest="$here/vendor"

src="${R3X_VENDOR_DIR:-}"
if [ -z "$src" ]; then
  for d in "$HOME"/Library/CloudStorage/GoogleDrive-*/"My Drive/MOB/Projects/DJ-R3X/r3x-vendor"; do
    [ -d "$d" ] && src="$d" && break
  done
fi
[ -n "$src" ] && [ -d "$src" ] || {
  echo "vendor files not found: install Google Drive for desktop, or set R3X_VENDOR_DIR" >&2
  exit 1
}

if [ -L "$dest" ]; then
  ln -sfn "$src" "$dest"
elif [ -e "$dest" ]; then
  echo "mech/vendor already exists as a real folder; leaving it (remove it to link to Drive)" >&2
  exit 0
else
  ln -s "$src" "$dest"
fi
echo "mech/vendor -> $src"
