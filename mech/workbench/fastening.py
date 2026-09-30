"""The workbench's fastening rule (Brandon, 2026-09-30): "Most of the printed parts use heat inserts
on the overall shell. Let's do that for internal stuff too when needed."

* A screw that threads into a **printed** part goes into a **heat-set insert**, never raw plastic.
* **Nuts stay** where they are the better joint: through-bolts that clamp (tube clamps, split
  rings), pivot bolts carrying shear with a locknut, and captive nuts the designer chose. A nut
  joint records its reason (`Fastener.inferred_note` or the mate's note).
* **Insert specs** come from one table, parts/head/_common.py INSERTS (the parametric parts cut
  their holes from it too), plus the kit's McMaster inserts from parts/catalog.json; where a
  datasheet hole size is not on file the check says so rather than guessing.
* **Hole and boss:** depth >= insert length + 1 mm (a through hole in a plate at least the insert
  long is fine); the wall round the insert >= max(1.5 mm, 0.5 x insert OD), measured to the part's
  nearest other surface (an edge, another hole).

The suite's `inserts` test applies it (suite.py).
"""

from __future__ import annotations

# One insert table for the whole workbench: the parametric parts' (parts/head/_common.py INSERTS:
# datasheet hole diameter and insert length; "M4-kit" is the R-3X head BOM's 6 x 6 mm M4, the lift
# rack's 5.5 mm M4 is entered there "as designed"). The kit's McMaster inserts are matched by
# thread + length from parts/catalog.json; their hole sizes are the table's where it has them.


def _table() -> dict:
    from parts.head._common import INSERTS

    return INSERTS


def _kit() -> dict:
    """{McMaster id: {thread, length_mm}} of the heat-set inserts in parts/catalog.json."""
    import json
    from pathlib import Path

    cat = json.loads((Path(__file__).resolve().parents[1] / "parts" / "catalog.json").read_text())
    items = cat.get("parts", cat) if isinstance(cat, dict) else cat
    if isinstance(items, list):
        items = {i["id"]: i for i in items}
    return {k: v["spec"] for k, v in items.items() if isinstance(v, dict) and v.get("kind") == "insert"}


HOLE_TOL_MM = 0.15       # printed hole diameter vs the spec (on the diameter)
DEPTH_EXTRA_MM = 1.0     # blind hole: insert length + this
WALL_MIN_MM = 1.5
WALL_OD_FRAC = 0.5
NUT_OK = ("clamp", "pivot", "captive")  # reasons a nut joint may carry (in its note)


def spec_of(fastener_spec: dict) -> dict | None:
    """The table's entry for an insert fastener (thread + length; the key names the entry), with
    `hole_d`, `length_mm`, `od_mm` (the fastener's own OD where it gives one) and `source`."""
    t = str(fastener_spec.get("thread", ""))
    L = float(fastener_spec.get("length_mm") or 0)
    table = _table()
    for key, e in table.items():
        if key.split("-")[0].upper() == t.upper() and abs(e["length"] - L) < 0.05:
            return {"key": key, "thread": t, "hole_d": e["d"], "length_mm": e["length"],
                    "od_mm": fastener_spec.get("od_mm"), "source": e["source"]}
    for pn, e in _kit().items():
        if str(e.get("thread", "")).upper() == t.upper() and abs(float(e.get("length_mm", 0)) - L) < 0.05:
            return {"key": pn, "thread": t, "hole_d": None, "length_mm": L, "od_mm": fastener_spec.get("od_mm"),
                    "source": "kit (McMaster; hole size not on file)"}
    return None


def wall_min(od: float | None) -> float:
    return max(WALL_MIN_MM, WALL_OD_FRAC * (od or 0.0))
