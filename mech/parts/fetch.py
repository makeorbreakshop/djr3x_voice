"""Fetch vendor CAD into mech/vendor/parts_cad/<vendor>/<part number>.step (gitignored).

    cd mech && .venv/bin/python -m parts.fetch            # every catalog part with a fetchable vendor
    cd mech && .venv/bin/python -m parts.fetch --dry-run

Polite by design: one request at a time, DELAY_S between requests, a file on disk is never
fetched again, a vendor that answers with a login, a CAPTCHA or a bot wall is stopped for the
rest of the run and its parts are reported as missing. Vendor CAD is distributed under each
vendor's terms: it stays in the cache and is never committed.

Files dropped by hand into the cache are picked up by part number (library.py), whatever
their name case, as .step/.stp (or a .zip holding one).

goBILDA publishes every product's STEP at /content/step_files/<sku>.zip (linked from the
product page). McMaster-Carr's CAD download runs through its signed-in JavaScript client, not
a plain URL, so it is not scripted here: those parts are listed for a manual download.
"""

from __future__ import annotations

import argparse
import io
import json
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE.parent / "vendor" / "parts_cad"
CATALOG = HERE / "catalog.json"
DELAY_S = 2.0
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) r3x-mech-parts/0.1 (single requests, cached)"
WALL = ("captcha", "are you a robot", "access denied", "please sign in", "log in to")


def cached_file(vendor: str, pn: str) -> Path | None:
    """A STEP for this part already in the cache (fetched or dropped in by hand)."""
    d = CACHE / vendor
    if not d.exists():
        return None
    want = pn.lower()
    for f in d.iterdir():
        stem = f.name.lower().rsplit(".", 1)[0]
        if stem == want and f.suffix.lower() in (".step", ".stp"):
            return f
    for f in d.iterdir():  # a hand-dropped zip: unpack it once
        if f.suffix.lower() == ".zip" and f.stem.lower() == want:
            return _unzip(f.read_bytes(), d, pn)
    return None


def _unzip(data: bytes, d: Path, pn: str) -> Path | None:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        steps = [n for n in z.namelist() if n.lower().endswith((".step", ".stp")) and not n.startswith("__MACOSX")]
        if not steps:
            return None
        out = d / f"{pn}.step"
        out.write_bytes(z.read(max(steps, key=lambda n: z.getinfo(n).file_size)))
        return out


def get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def fetch_gobilda(pn: str) -> tuple[str, str]:
    d = CACHE / "gobilda"
    d.mkdir(parents=True, exist_ok=True)
    try:
        data = get(f"https://www.gobilda.com/content/step_files/{pn}.zip")
    except urllib.error.HTTPError as e:
        return ("missing", f"HTTP {e.code}")
    if data[:2] != b"PK":
        text = data[:4000].decode("utf8", "ignore").lower()
        if any(w in text for w in WALL):
            return ("blocked", "login/bot wall")
        return ("missing", "not a zip")
    f = _unzip(data, d, pn)
    return ("vendor", f.name) if f else ("missing", "zip had no STEP")


FETCHERS = {"gobilda": fetch_gobilda}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    cat = json.loads(CATALOG.read_text())
    blocked: set[str] = set()
    log = []
    first = True
    for p in cat["parts"]:
        v, pn = p["vendor"], p["pn"]
        if cached_file(v, pn):
            log.append((v, pn, "cached"))
            continue
        if v not in FETCHERS or v in blocked:
            continue
        if a.dry_run:
            log.append((v, pn, "would fetch"))
            continue
        if not first:
            time.sleep(DELAY_S)
        first = False
        status, note = FETCHERS[v](pn)
        if status == "blocked":
            blocked.add(v)
        log.append((v, pn, f"{status} {note}"))
        print(f"{v:8} {pn:18} {status} {note}", flush=True)
    for v in sorted(blocked):
        print(f"stopped for {v}: it asked for a login / showed a bot wall")
    print(f"{sum(1 for x in log if x[2] == 'cached')} cached, {len(log)} considered")


if __name__ == "__main__":
    main()
