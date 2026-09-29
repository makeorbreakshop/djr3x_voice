# The `dj-r3x` command

```bash
./setup-dj-r3x-command.sh
```

Installs `dj-r3x` into `/usr/local/bin` (or `~/.local/bin` when that isn't writable). It is a
two-line wrapper that runs this checkout's `./r3x`, so arguments pass straight through:

```bash
dj-r3x               # CantinaOS + control panel, opens http://localhost:5391
dj-r3x --no-open
dj-r3x --panel-only
```

Moved the repo? Run the setup script again from the new location. A launcher it didn't write
is saved to `~/.dj-r3x.launcher.bak` before being replaced.

Prerequisites are the ones in the README: the `venv/` at the repo root, a `.env` with API
keys, Node.js for the panel, and VLC.app for music.
