#!/usr/bin/env bash
# Install a `dj-r3x` command that runs ./r3x (CantinaOS + control panel) from anywhere.
#   ./setup-dj-r3x-command.sh            installs to /usr/local/bin (or ~/.local/bin if not writable)
# Any arguments to dj-r3x are passed through: dj-r3x --no-open, dj-r3x --panel-only.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_DIR=/usr/local/bin
# Replace an existing launcher we own even when the directory itself is root-owned.
[ -w "$TARGET_DIR" ] || [ -w "$TARGET_DIR/dj-r3x" ] || TARGET_DIR="$HOME/.local/bin"
mkdir -p "$TARGET_DIR"
TARGET="$TARGET_DIR/dj-r3x"

if [ -f "$TARGET" ] && ! grep -q "exec .*r3x" "$TARGET"; then
  cp "$TARGET" "$HOME/.dj-r3x.launcher.bak"
  echo "Saved the previous launcher as ~/.dj-r3x.launcher.bak"
fi

cat > "$TARGET" <<LAUNCHER
#!/usr/bin/env bash
# DJ R3X launcher, installed by $ROOT/setup-dj-r3x-command.sh
exec "$ROOT/r3x" "\$@"
LAUNCHER
chmod +x "$TARGET"

echo "Installed $TARGET -> $ROOT/r3x"
case ":$PATH:" in *":$TARGET_DIR:"*) ;; *) echo "Add $TARGET_DIR to your PATH to use it." ;; esac
