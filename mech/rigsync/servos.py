"""Servo ratings: a copy of rust/crates/r3x-performer-core/src/actuation/servos.rs (the one
the performer and the servo plant use). (volts, s/60deg) and (volts, stall kg.cm) at two
rated voltages; values in between are linear. Keep in step with servos.rs."""

from __future__ import annotations

KGCM_TO_NM = 0.0980665

SERVOS = {
    "MG996R": ([(4.8, 0.17), (6.0, 0.14)], [(4.8, 9.4), (6.0, 11.0)]),
    "DS3218": ([(5.0, 0.16), (6.8, 0.14)], [(5.0, 19.0), (6.8, 21.5)]),
    "DS3218_270": ([(5.0, 0.16), (6.8, 0.14)], [(5.0, 19.0), (6.8, 21.5)]),
    "MG90S": ([(4.8, 0.1), (6.0, 0.08)], [(4.8, 1.8), (6.0, 2.2)]),
    "SERVO_35KG_270": ([(5.0, 0.16), (7.4, 0.11)], [(5.0, 25.0), (7.4, 35.0)]),
    "SERVO_60KG_270": ([(6.0, 0.2), (8.4, 0.15)], [(6.0, 50.0), (8.4, 60.0)]),
    "DS3218_DUAL": ([(5.0, 0.16), (6.8, 0.14)], [(5.0, 19.0), (6.8, 21.5)]),
    "SERVO_7KG": ([(4.8, 0.12), (6.0, 0.1)], [(4.8, 6.0), (6.0, 7.0)]),
    "GOBILDA_2000_25_2": ([(4.8, 0.25), (6.0, 0.2)], [(4.8, 20.2), (6.0, 25.2)]),
    "SG90": ([(4.8, 0.133), (6.0, 0.12)], [(4.8, 1.8), (6.0, 1.8)]),
}


def _interp(t, v):
    (a0, a1), (b0, b1) = t
    k = min(1.0, max(0.0, (v - a0) / (b0 - a0)))
    return a1 + (b1 - a1) * k


def stall_nm(key: str, volts: float) -> float:
    return _interp(SERVOS[key][1], volts) * KGCM_TO_NM


def no_load_dps(key: str, volts: float) -> float:
    return 60.0 / _interp(SERVOS[key][0], volts)
