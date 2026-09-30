"""Catalogue data: servos, purchased hardware, print materials.

Servo torques are the sim/performer catalogue (`rust/crates/r3x-performer-core/src/actuation/
servos.rs`), interpolated to the 6.0 V supply in profiles/r3x/robot.json. Masses are typical
listing values for the exact products in `07 - r3x hardware.txt` (Amazon ASINs resolved
2026-09-30); weigh the real ones and correct.
"""
from __future__ import annotations

SUPPLY_V = 6.0
KGCM_TO_NM = 0.0980665


def _interp(pts, v):
    (v0, t0), (v1, t1) = pts
    return t0 + (t1 - t0) * (v - v0) / (v1 - v0)


SERVOS = {
    # key: listing, stall torque points (V, kg.cm), no-load speed (V, s/60deg), range, mass g
    "SERVO_60KG_270": dict(listing="Wishiot DS5160SSG 60 kg, 270 deg (B08HYX5SX3)",
                           torque=[(6.0, 50.0), (8.4, 60.0)], speed=[(6.0, 0.20), (8.4, 0.15)],
                           range_deg=270, mass_g=160, size="65 x 30 x 48 mm (84 mm over flanges)"),
    "SERVO_35KG_270": dict(listing="ZOSKAY 35 kg coreless, 270 deg (B07S9XZYN2)",
                           torque=[(5.0, 25.0), (7.4, 35.0)], speed=[(5.0, 0.16), (7.4, 0.11)],
                           range_deg=270, mass_g=70, size="40 x 20 x 40.5 mm standard"),
    "DS3218_DUAL": dict(listing="20 kg dual-shaft, 180 deg (B07CMBMWZW; list says double shaft)",
                        torque=[(5.0, 19.0), (6.8, 21.5)], speed=[(5.0, 0.16), (6.8, 0.14)],
                        range_deg=180, mass_g=65, size="40 x 20 x 40.5 mm standard"),
    "SERVO_7KG": dict(listing="INJORA INJS2065 7 kg HV micro (B0BLCSMJ9S)",
                      torque=[(4.8, 6.0), (6.0, 7.0)], speed=[(4.8, 0.12), (6.0, 0.10)],
                      range_deg=180, mass_g=22, size="micro (~23 x 12 x 25 mm)"),
}


def stall_kgcm(key, v=SUPPLY_V):
    return _interp(SERVOS[key]["torque"], v)


def speed_s60(key, v=SUPPLY_V):
    return _interp(SERVOS[key]["speed"], v)


# Printed parts: solid density x effective fill. The kit shells follow the sim's estimate
# (build_r3x.py: 3 perimeters + 15-20 % infill ~ 45 % of solid). Mechanism parts (gears,
# mounts, clamps) should be printed stronger: 5 walls + 40 % infill ~ 65 % of solid.
MATERIALS = {
    "PLA": dict(density=1.24, fill=0.45, note="kit shells, 3 walls + 15-20 % infill"),
    "PETG": dict(density=1.27, fill=0.65, note="mechanism parts, 5 walls + 40 % infill"),
    "rubber": dict(density=1.20, fill=1.0, note="chair-mat gasket rings (kit DNP)"),
    "aluminium": dict(density=2.70, fill=1.0, note="solid"),
    "steel": dict(density=7.85, fill=1.0, note="solid"),
}

# Purchased / DNP items with a catalogue mass (g) instead of a volume estimate.
PURCHASED = {
    "neck_tube": dict(item="Neck tube 26 mm OD x 1.5 mm wall aluminium (CAD: neck-dnp, 500 mm)",
                      g_per_mm=0.312, note="material not in the parts list; aluminium assumed"),
    "mgn12_rail_per_mm": dict(item="MGN12 rail", g_per_mm=0.65),
    "mgn12h": dict(item="MGN12H carriage", mass_g=45),
    "alu_2020_per_mm": dict(item="2020 extrusion", g_per_mm=0.50),
    "f6001zz": dict(item="F6001ZZ flanged bearing 12x28x8", mass_g=20),
    "lazy_susan_10": dict(item="10 in lazy susan (TamBee, drilled)", mass_g=450),
    "lazy_susan_6": dict(item="6 in lazy susan", mass_g=180),
    "rod_end_6": dict(item="6 mm rod end", mass_g=8),
    "servo_horn_disc": dict(item="servo horn disc", mass_g=6),
}
