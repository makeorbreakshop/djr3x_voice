"""Every purchased part across the droid's sources -> mech/parts/catalog.json.

    cd mech && .venv/bin/python -m parts.catalog

Sources (read when present; the catalog keeps what it last saw when a gitignored source is
missing on this machine):
- Hunter's head BOM (RX Head Mech BOM.pdf, as transcribed in assemblies/hunter_head),
- the kit guide's hardware list (assemblies/kit/_guide/hardware_bom.json, gitignored: only part
  numbers, descriptions and counts are carried here),
- the R-3X Animation list (vendor/animation/07 - r3x hardware.txt),
- Sam Morton's 2020 cage (assemblies/community).

Each entry: vendor, part number, description, quantity (per source), vendor URL, the parsed
spec the parametric fallback is built from, and `cad`: vendor | parametric | placeholder, as
resolved by library.py against the CAD cache right now.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
MECH = HERE.parent
OUT = HERE / "catalog.json"
GOBILDA = "https://www.gobilda.com/"

# ------------------------------------------------------------------ Hunter's head (RX Head Mech BOM.pdf)
HUNTER = [
    ("gobilda", "2000-0025-0002", "2000 Series Dual Mode Servo (25-2, Torque)", 2, "servo", {"case": "standard", "model": "gobilda_2000"},
     GOBILDA + "2000-series-dual-mode-servo-25-2-torque/"),
    ("gobilda", "4005-0016-0001", "4005 Series Pattern Mount Universal Joint (16-1)", 1, "hardware", {},
     GOBILDA + "4005-series-pattern-mount-universal-joint-16-1/"),
    ("gobilda", "1311-0016-4008", "1311 Series Thru-Hole Sonic Hub (8mm REX Bore)", 1, "hardware", {},
     GOBILDA + "1311-series-thru-hole-sonic-hub-8mm-rex-bore/"),
    ("gobilda", "1309-0016-4008", "1309 Series Sonic Hub (8mm REX Bore)", 1, "hardware", {},
     GOBILDA + "1309-series-sonic-hub-8mm-rex-bore/"),
    ("gobilda", "1602-0032-0006", "6mm Bore 1-Side 2-Post Pillow Block (24mm Height)", 2, "hardware", {},
     GOBILDA + "6mm-bore-1-side-2-post-pillow-block-24mm-height/"),
    ("gobilda", "1516-4008-0960", "1516 Series 8mm REX Standoff (M4, 96mm), 4-pack", 1, "hardware", {},
     GOBILDA + "1516-series-8mm-rex-standoff-m4-x-0-7mm-threads-96mm-length-4-pack/"),
    ("gobilda", "1906-0025-0032", "1906 Series Lightweight Servo Hub (25T spline, 32mm)", 2, "hardware", {},
     GOBILDA + "1906-series-lightweight-servo-hub-25-tooth-spline-32mm-diameter/"),
    ("gobilda", "1916-0014-0048", "Plastic Hub-Mount Control Arm (48mm), cut down", 2, "hardware", {},
     GOBILDA + "plastic-hub-mount-control-arm-48mm-length/"),
    ("gobilda", "2913-0004-0241", "2913 Series Steel Ball Linkage (Female, M4, 24.1mm), 2-pack", 2, "hardware", {},
     GOBILDA + "2913-series-steel-ball-linkage-female-m4-x-0-7mm-24-1mm-length-2-pack/"),
    ("gobilda", "2808-0004-0050", "2808 Series Stainless Threaded Rod (M4, 50mm), 2-pack", 1, "hardware", {"type": "threaded_rod", "thread": "M4", "length_mm": 50},
     GOBILDA + "2808-series-stainless-steel-threaded-rod-m4-x-0-7mm-50mm-length-2-pack/"),
    ("gobilda", "2800-0004-0014", "M4 x 14mm socket head screw (sonic hub clamp, in the STEP)", 4, "screw", {"type": "shcs", "thread": "M4", "length_mm": 14}, ""),
    ("iso", "insert-M4x6", "M4 heat-set insert, 6 x 6 mm", 14, "insert", {"type": "insert", "thread": "M4", "length_mm": 6, "od_mm": 6}, ""),
    ("iso", "iso4762-M4", "M4 socket head cap screws (lengths from the geometry)", 0, "screw", {"type": "shcs", "thread": "M4"}, ""),
    ("iso", "iso10511-M4", "M4 nylon-insert lock nuts", 0, "nut", {"type": "lock_nut", "thread": "M4"}, ""),
    ("iso", "iso7089-M4", "M4 washers (spacers on the pillow blocks and joint piece)", 0, "washer", {"type": "washer", "thread": "M4"}, ""),
]

# ------------------------------------------------------------------ Morton 2020 cage
MORTON = [
    ("misumi", "HFS5-2020", "2020 aluminium extrusion (T-slot, 5 series), cut to length", 7, "extrusion", {"profile": "2020"},
     "https://us.misumi-ec.com/vona2/detail/110302683830/"),
    ("misumi", "HNTT5-5", "T-nut, M5, for 2020 (Morton: 'a bunch')", 28, "t_nut", {"thread": "M5"},
     "https://us.misumi-ec.com/vona2/detail/110302246940/"),
]

# ------------------------------------------------------------------ R-3X Animation list: section -> kind
R3X_KINDS = [
    (r"60kg servo", "servo", {"case": "large", "model": "ds5160"}),
    (r"7kg servo", "servo", {"case": "micro", "model": "injs2065"}),
    (r"35kg servos", "servo", {"case": "standard", "model": "zoskay_35kg"}),
    (r"20kg double shaft", "servo", {"case": "standard", "model": "ds3218_dual", "dual_shaft": True}),
    (r"maestro", "electronics", {"board": "pololu_maestro_18", "size_mm": [55.9, 27.9, 1.6]}),
    (r'6" lazy susan', "lazy_susan", {"size_in": 6}),
    (r'10" lazy susan', "lazy_susan", {"size_in": 10}),
    (r"heat set inserts", "insert", {"type": "insert", "thread": "M3", "length_mm": 4, "od_mm": 4.5}),
    (r"M3 socket button", "screw", {"type": "bhcs", "thread": "M3"}),
    (r"M4 socket button", "screw", {"type": "bhcs", "thread": "M4"}),
    (r"M5 socket button", "screw", {"type": "bhcs", "thread": "M5"}),
    (r"rod end", "rod_end", {"thread": "M6", "bore_mm": 6}),
    (r"aluminum tube", "tube", {"od_mm": 8, "id_mm": 6, "length_mm": 300}),
    (r"F6001ZZ", "bearing", {"type": "flanged", "id_mm": 12, "od_mm": 28, "width_mm": 8, "flange_od_mm": 30.5, "flange_mm": 1.5}),
    (r"MGN12", "linear_rail", {"rail": "MGN12", "length_mm": 200, "carriage": "MGN12H"}),
]

INCH = {"#0": 1.524, "#2": 2.184, "#4": 2.845, "#6": 3.505, "#8": 4.166, "#10": 4.826, "1/4": 6.35}


def _inch(s: str) -> float:
    """'1-1/2' / '5/16' / '2' (inches) -> mm."""
    total = 0.0
    for part in s.replace("in", "").split("-"):
        if "/" in part:
            a, b = part.split("/")
            total += float(a) / float(b)
        elif part:
            total += float(part)
    return total * 25.4


def spec_from_desc(desc: str) -> tuple[str, dict]:
    """(kind, spec) from a McMaster/goBILDA description in the guide's hardware list."""
    d = desc.lower()
    m_metric = re.search(r"\bm(\d+(?:\.\d+)?)(?: x 0?\.?\d+ fine-thread)? x (\d+(?:\.\d+)?)mm", d)
    m_inch = re.search(r"(#?\d+)-(\d+) x ([\d/\-]+)in", d)
    thread, length = None, None
    if m_metric:
        thread, length = f"M{m_metric.group(1)}", float(m_metric.group(2))
    elif m_inch:
        num = m_inch.group(1)
        thread, length = f"#{num.lstrip('#')}-{m_inch.group(2)}", round(_inch(m_inch.group(3)), 2)
    if "heat-set insert" in d:
        t = re.search(r"\b(m\d+|\d+-\d+)\b", d).group(1)
        th = t.upper() if t.startswith("m") else f"#{t}"
        ln = re.search(r"([\d.]+)(mm|in)\s*$", d)
        L = float(ln.group(1)) * (25.4 if ln and ln.group(2) == "in" else 1) if ln else 5.0
        return "insert", {"type": "insert", "thread": th, "length_mm": round(L, 2), "tapered": True}
    if "magnet" in d:
        m = re.search(r"([\d/]+)in od x ([\d/]+)in", d)
        return "magnet", {"od_mm": round(_inch(m.group(1)), 2), "t_mm": round(_inch(m.group(2)), 2)} if m else {}
    if "standoff" in d:
        m = re.search(r"(\d+)mm od x (\d+)mm", d)
        if m:
            return "standoff", {"shape": "round", "od_mm": float(m.group(1)), "length_mm": float(m.group(2)), "thread": "M4"}
        m = re.search(r"([\d/]+)in (hex|od) x ([\d/\-]+)in", d)
        if m:
            return "standoff", {"shape": "hex" if m.group(2) == "hex" else "round", "od_mm": round(_inch(m.group(1)), 2),
                                "length_mm": round(_inch(m.group(3)), 2)}
    if "spacer" in d:
        m = re.search(r"(\d+)mm od x (\d+)mm", d)
        return "standoff", {"shape": "round", "od_mm": float(m.group(1)), "length_mm": float(m.group(2)), "thread": "M4"}
    if "locknut" in d or "lock nut" in d:
        return "nut", {"type": "lock_nut", "thread": re.search(r"\bm\d+", d).group(0).upper()}
    if "washer" in d:
        m = re.search(r"(\d+)mm id x (\d+)mm od", d)
        return "washer", {"type": "washer", "id_mm": float(m.group(1)), "od_mm": float(m.group(2))} if m else {"type": "washer"}
    if "shim" in d:
        return "washer", {"type": "washer", "id_mm": 4.0, "od_mm": 8.0, "t_mm": 1.0}
    if "dowel pin" in d:
        return "pin", {"d_mm": 3.175, "length_mm": 12.7}
    if "rod end" in d:
        return "rod_end", {"thread": "M6", "bore_mm": 6}
    if "ball linkage" in d:
        return "ball_link", {"thread": "M4"}
    if "sleeve bearing" in d:
        return "bearing", {"type": "sleeve", "id_mm": 6, "od_mm": 8, "width_mm": 12}
    if "tube" in d:
        return "tube", {"od_mm": 8, "id_mm": 6, "length_mm": 80}
    if thread and length:
        head = ("shcs_low" if "low-profile" in d else "shcs" if "socket head" in d else "bhcs" if "button head" in d
                else "fhcs" if "flat head" in d else "pan" if "pan head" in d else "truss" if "truss" in d else "shcs")
        return "screw", {"type": head, "thread": thread, "length_mm": length}
    return "other", {}


def hunter():
    for v, pn, desc, qty, kind, spec, url in HUNTER:
        yield dict(vendor=v, pn=pn, description=desc, used_by={"hunter_head": qty}, url=url, kind=kind, spec=spec)


def kit():
    f = MECH / "assemblies" / "kit" / "_guide" / "hardware_bom.json"
    if not f.exists():
        return
    for h in json.loads(f.read_text()):
        pn = h["mcmaster"]
        gob = bool(re.match(r"^\d{4}-\d{4}-\d{4}$", pn))
        kind, spec = spec_from_desc(h.get("desc", ""))
        yield dict(vendor="gobilda" if gob else "mcmaster", pn=pn, description=h.get("desc", ""),
                   used_by={"kit": h.get("qty_total") or 0}, kind=kind, spec=spec,
                   url=(f"https://www.gobilda.com/search.php?search_query={pn}" if gob else f"https://www.mcmaster.com/{pn}/"))


def r3x():
    f = MECH / "vendor" / "animation" / "07 - r3x hardware.txt"
    if not f.exists():
        return
    for line in f.read_text(errors="ignore").splitlines():
        m = re.match(r"\s*(\d+)x (.+?): (https?://\S+)", line)
        if not m:
            continue
        qty, desc, url = int(m.group(1)), m.group(2).strip(), m.group(3)
        kind, spec = "electronics", {}
        for pat, k, s in R3X_KINDS:
            if re.search(pat, desc, re.I):
                kind, spec = k, s
                break
        pn = "amzn-" + url.rstrip("/").split("/")[-1]
        yield dict(vendor="amazon", pn=pn, description=desc, used_by={"r3x_animation": qty}, url=url, kind=kind, spec=spec)


def morton():
    for v, pn, desc, qty, kind, spec, url in MORTON:
        yield dict(vendor=v, pn=pn, description=desc, used_by={"community": qty}, url=url, kind=kind, spec=spec)


def build() -> dict:
    from .library import resolve

    old = {}
    if OUT.exists():
        old = {p["id"]: p for p in json.loads(OUT.read_text())["parts"]}
    merged: dict[str, dict] = {}
    seen_sources = set()
    for src in (hunter, kit, r3x, morton):
        for e in src():
            seen_sources.update(e["used_by"])
            pid = f"{e['vendor']}:{e['pn']}"
            if pid in merged:
                for k, q in e["used_by"].items():
                    merged[pid]["used_by"][k] = merged[pid]["used_by"].get(k, 0) + q
            else:
                merged[pid] = {"id": pid, **e}
    # keep parts from sources this machine cannot read (gitignored transcriptions)
    for pid, p in old.items():
        if pid not in merged and not (set(p["used_by"]) & seen_sources):
            merged[pid] = p
    parts = sorted(merged.values(), key=lambda p: (p["vendor"], p["pn"]))
    for p in parts:
        p["qty"] = sum(p["used_by"].values())
        r = resolve(p)
        p["cad"] = {"status": r.status, **({"file": r.file} if r.file else {}), **({"note": r.note} if r.note else {})}
    counts = {s: sum(1 for p in parts if p["cad"]["status"] == s) for s in ("vendor", "parametric", "placeholder")}
    return {"schema": "r3x.mech.parts", "version": 1, "counts": counts, "parts": parts}


def main():
    cat = build()
    OUT.write_text(json.dumps(cat, indent=1) + "\n")
    print(f"{len(cat['parts'])} parts -> {OUT}: {cat['counts']}")


if __name__ == "__main__":
    main()
