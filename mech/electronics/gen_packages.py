"""Generate profiles/electronics/*.json (the electronics packages).

    python3 mech/electronics/gen_packages.py

The packages are data; this script only computes the LED layouts (positions in the model's
kit frame, see Units) from the geometry we already have, so they stay consistent:

- the chest logic panels' LED holes and windows: the r3x_native package's own chest layout
  (measured from the kit's MS_P_1_Full panels by sim/model/build_r3x.py), kept in
  profiles/electronics/r3x_native.json and read back from there;
- the grnwave body boards: mech/electronics/logic_panels.json (LED holes and window slots
  measured from the kit's logic panel STLs by logic_panels.py);
- the eye bulbs and the mouth: rig.json `anchors` (sim/model/build_r3x.py), copied below.

Everything about the grnwave boards that the product page, the manual and the sample sketch
(github.com/grnwaveworkshop/DJ-RexBody, DJLEDNanoV2.ino) do not state is marked
`inferred` with a note. Brandon: confirm those once the boards are in hand.

Units: metres in the model's kit frame - the GLB's own coordinates (rig.json's frame), Y up,
every joint at the kit pose. Each point belongs to its group's `link`: the sim parents it
there (Rig.kitToLocal), so it turns with the link and its canonical rest offset
(sim/web/src/show/rig_limits.json). Not the canonical body frame: that differs per ring.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "profiles" / "electronics"

# rig.json anchors (sim/web/public/model/rig.json, built by sim/model/build_r3x.py).
EYE_L = (0.04383, 0.79899, 0.11593)  # the droid's left eye (+X)
EYE_R = (-0.04235, 0.79902, 0.11653)
EYE_SIZE = (0.049, 0.049, 0.02699)
MOUTH = (-0.00008, 0.73164, 0.16148)
MOUTH_SIZE = (0.05325, 0.06477, 0.01904)
# leds.ts: 7-LED jewel ring radius, mouth V as fractions of the lit part's half size.
JEWEL_R = 0.0085
MOUTH_V = [(-0.82, 0.8), (-0.58, 0.3), (-0.34, -0.22), (-0.1, -0.74)]

MA_WS2812 = 60.0  # one WS2812/5050 at full white (3 x ~20 mA)
MA_PL9823 = 60.0  # 8 mm through-hole "NeoPixel" (PL9823 / WS2812D-F8)


def r(v, n=5):
    return [round(x, n) for x in v]


def px(kind, pos, normal=(0.0, 0.0, 1.0), w=0.005, h=0.005):
    return {"kind": kind, "pos": r(pos), "normal": r(normal, 4), "w": w, "h": h}


def serial(board, port_hint):
    return {"type": "serial_led", "board": board, "port_hint": port_hint, "protocol": "named_v1"}


def jewel(centre):
    """rex_face: LED 0 the centre, 1-6 clockwise seen from the front, 1 at the top."""
    x, y, z = centre
    z -= EYE_SIZE[2] / 2  # against the back of the diffusion bulb
    out = [px("jewel", (x, y, z), w=0.005, h=0.005)]
    for k in range(6):
        a = math.radians(60 * k)
        # seen from the front the viewer's right is -X, so clockwise = toward -X from the top
        out.append(px("jewel", (x - JEWEL_R * math.sin(a), y + JEWEL_R * math.cos(a), z), w=0.005, h=0.005))
    return out


def mouth_v(kind="mouth"):
    """0 at the top of the viewer-left arm (-X) down to 3 at the tip, 4-7 back up the right."""
    hw, hh = MOUTH_SIZE[0] / 2, MOUTH_SIZE[1] / 2
    z = MOUTH[2] - MOUTH_SIZE[2] / 2
    left = [(MOUTH[0] + fx * hw, MOUTH[1] + fy * hh, z) for fx, fy in MOUTH_V]
    right = [(MOUTH[0] - fx * hw, MOUTH[1] + fy * hh, z) for fx, fy in reversed(MOUTH_V)]
    return [px(kind, p) for p in left + right]


def panels(chest):
    """The native chest layout (strip order: panel dots bottom->top, then its 3 windows)."""
    out = []
    for p in range(3):
        seg = chest[p * 11:(p + 1) * 11]
        out.append({"dots": seg[:8], "windows": seg[8:]})
    return out


# ------------------------------------------------------------------------ r3x_native

def native(chest_layout):
    face_nano_t = (0.0, 0.76, 0.02)
    chest_nano_t = (-0.10, 0.41, 0.02)
    return {
        "id": "r3x_native",
        "label": "R3X native (face + chest Nanos, RP2040 servo controller)",
        "vendor": "this repo",
        "description": "Today's build: an Arduino Nano in the head drives two 7-LED WS2812 eye jewels and an "
                       "8-pixel mouth V (rex_face_v3_clean); a second Nano behind the middle ring drives the 33 "
                       "chest logic-panel pixels (rex_chest_v1); our RP2040 controller (firmware/servo) runs the "
                       "18 servo channels.",
        "support": "driven",
        "emulator": "native",
        "boards": [
            {
                "id": "face_nano", "model": "Arduino Nano (ATmega328P)", "role": "face LEDs: eyes + mouth",
                "url": "https://store.arduino.cc/products/arduino-nano",
                "dims_mm": [45.0, 18.0, 8.0],
                "mount": {"bracket": "head shell, inside the rear of the head", "link": "head_tilt",
                          "t": r(face_nano_t), "q": [0, 0, 0, 1], "inferred": True,
                          "inferred_note": "Where the face Nano sits in the head is not recorded; placed behind the eyes."},
                "connectors": [
                    {"id": "usb", "kind": "usb_mini_b", "pins": ["VBUS", "D-", "D+", "GND"], "note": "serial 115200 to the host"},
                    {"id": "d6", "kind": "dupont_3p_2.54", "pins": ["D6", "5V", "GND"], "note": "eye jewels DIN"},
                    {"id": "d5", "kind": "dupont_3p_2.54", "pins": ["D5", "5V", "GND"], "note": "mouth V DIN"},
                ],
                "volts": 5.0, "current": {"idle_ma": 20, "typical_ma": 25, "max_ma": 40},
                "firmware": "cantina_os/arduino/rex_face_v3_clean", "driver": "driver.face", "support": "driven",
            },
            {
                "id": "eye_jewels", "model": "2 x WS2812 7-LED jewel (23 mm)", "role": "eyes, one jewel per eye bulb",
                "url": "https://www.adafruit.com/product/2226",
                "dims_mm": [23.0, 23.0, 4.0],
                "mount": {"bracket": "eye distortion / diffusion bulb (H_*Eye_4), against its back", "link": "head_tilt",
                          "t": r(((EYE_L[0] + EYE_R[0]) / 2, EYE_L[1], EYE_L[2] - EYE_SIZE[2] / 2)), "q": [0, 0, 0, 1]},
                "connectors": [{"id": "din", "kind": "solder_pad", "pins": ["DIN", "5V", "GND"]},
                               {"id": "dout", "kind": "solder_pad", "pins": ["DOUT", "5V", "GND"], "note": "left jewel DOUT -> right jewel DIN"}],
                "volts": 5.0, "current": {"idle_ma": 7, "typical_ma": 180, "max_ma": 14 * MA_WS2812},
                "support": "driven",
            },
            {
                "id": "mouth_v", "model": "8 x WS2812B (60/m strip, cut into a V)", "role": "mouth, behind the light pipe",
                "dims_mm": [55.0, 65.0, 3.0],
                "mount": {"bracket": "Mic-Mouth-Split back mount", "link": "head_tilt", "t": r(MOUTH), "q": [0, 0, 0, 1]},
                "connectors": [{"id": "din", "kind": "solder_pad", "pins": ["DIN", "5V", "GND"]}],
                "volts": 5.0, "current": {"idle_ma": 4, "typical_ma": 90, "max_ma": 8 * MA_WS2812},
                "support": "driven",
            },
            {
                "id": "chest_nano", "model": "Arduino Nano (ATmega328P)", "role": "chest logic panels + machine status",
                "url": "https://store.arduino.cc/products/arduino-nano",
                "dims_mm": [45.0, 18.0, 8.0],
                "mount": {"bracket": "middle ring, behind the logic panels", "link": "torso_middle",
                          "t": r(chest_nano_t), "q": [0, 0, 0, 1], "inferred": True,
                          "inferred_note": "Placed inside the middle ring behind panel 2; not recorded."},
                "connectors": [
                    {"id": "usb", "kind": "usb_mini_b", "pins": ["VBUS", "D-", "D+", "GND"]},
                    {"id": "d6", "kind": "dupont_3p_2.54", "pins": ["D6", "5V", "GND"], "note": "chest strip DIN via 330 R"},
                ],
                "volts": 5.0, "current": {"idle_ma": 20, "typical_ma": 25, "max_ma": 40},
                "firmware": "cantina_os/arduino/rex_chest_v1", "driver": "driver.chest", "support": "driven",
            },
            {
                "id": "chest_strip", "model": "33 x WS2812B", "role": "3 panels x (8 LED holes + 3 windows)",
                "dims_mm": [120.0, 60.0, 3.0],
                "mount": {"bracket": "MS_P_1_Full logic panels (x3), from behind", "link": "torso_middle",
                          "t": r((-0.12, 0.453, 0.09)), "q": [0, 0, 0, 1]},
                "connectors": [{"id": "din", "kind": "solder_pad", "pins": ["DIN", "5V", "GND"]}],
                "volts": 5.0, "current": {"idle_ma": 17, "typical_ma": 250, "max_ma": 33 * MA_WS2812},
                "support": "driven",
            },
            {
                "id": "servo_ctl", "model": "Raspberry Pi Pico (RP2040) + INA219 + rail MOSFET",
                "role": "18 servo channels (16 PWM + 2 PIO), goals at event time",
                "url": "https://www.raspberrypi.com/products/raspberry-pi-pico/",
                "dims_mm": [51.0, 21.0, 4.0],
                "mount": {"bracket": "base electronics plate", "link": "torso_lower", "t": [0.0, 0.08, 0.0], "q": [0, 0, 0, 1],
                          "inferred": True, "inferred_note": "Mounting in the base is not recorded; placed on the base plate."},
                "connectors": [
                    {"id": "usb", "kind": "usb_micro_b", "pins": ["VBUS", "D-", "D+", "GND"], "note": "CDC, R3X_SERVO_PORT"},
                    {"id": "servo", "kind": "servo_3p_2.54 x18", "pins": ["GPIO0..GPIO17", "6V", "GND"]},
                    {"id": "i2c", "kind": "dupont_4p_2.54", "pins": ["GPIO20 SDA", "GPIO21 SCL", "3V3", "GND"], "note": "INA219 @ 0x40"},
                    {"id": "rail_en", "kind": "dupont_1p", "pins": ["GPIO22"], "note": "servo rail enable (active high)"},
                ],
                "volts": 5.0, "current": {"idle_ma": 25, "typical_ma": 30, "max_ma": 50},
                "firmware": "firmware/servo", "driver": "driver.servo", "support": "driven",
            },
        ],
        "lights": [
            {"name": "eyes", "pixels": 14, "layout": jewel(EYE_L) + jewel(EYE_R),
             "driver": serial("face", "ARDUINO_SERIAL_PORT"), "board": "eye_jewels", "link": "head_tilt",
             "data_line": "face_nano.D6", "chain_start": 0, "led": "WS2812 5050 (jewel)", "ma_per_led": MA_WS2812,
             "serves": ["eyes"],
             "inferred": True, "inferred_note": "Jewel wiring (LED 0 centre, 1-6 clockwise from the top; left jewel first) is the sim's assumption (leds.ts)."},
            {"name": "mouth", "pixels": 8, "layout": mouth_v(),
             "driver": serial("face", "ARDUINO_SERIAL_PORT"), "board": "mouth_v", "link": "head_tilt",
             "data_line": "face_nano.D5", "chain_start": 0, "led": "WS2812B 5050", "ma_per_led": MA_WS2812,
             "serves": ["mouth", "speech_amplitude"],
             "inferred": True, "inferred_note": "V order (viewer-left arm top -> tip -> right arm) is the sim's assumption (leds.ts)."},
            {"name": "chest", "pixels": 33, "layout": chest_layout,
             "driver": serial("chest", "CHEST_SERIAL_PORT"), "board": "chest_strip", "link": "torso_middle",
             "data_line": "chest_nano.D6", "chain_start": 0, "led": "WS2812B 5050", "ma_per_led": MA_WS2812,
             "serves": ["chest", "speech_amplitude", "tempo", "chest_status"]},
        ],
        "actuators": [
            {"actuators": ["*"], "board": "servo_ctl", "driver": "r3x_servo", "support": "driven",
             "note": "Every profile actuator with driver r3x_servo (channel = GPIO)."},
        ],
        "power": [
            {"name": "5V logic + LEDs", "volts": 5.0,
             "max_ma": 55 * MA_WS2812 + 40 + 40 + 50, "typical_ma": 600,
             "loads": ["face_nano", "eye_jewels", "mouth_v", "chest_nano", "chest_strip", "servo_ctl"],
             "psu": "5 V 4 A regulated (UBEC or bench supply); LED boards at brightness 128 stay under 2 A",
             "psu_amps": 4.0,
             "note": "Full white is 3.3 A at brightness 255; both Nanos cap at 128, so ~1.7 A worst case. USB alone is not enough for the LEDs."},
            {"name": "6V servo rail", "volts": 6.0, "max_ma": 6000, "typical_ma": 1500, "loads": ["servo_ctl"],
             "psu": "6 V 10 A (servo PSU), switched by the controller's rail MOSFET", "psu_amps": 10.0,
             "note": "The controller trips at 6 A for 300 ms (INA219), so 6 A is the rail's effective max. Typical is inferred."},
        ],
        "wiring": [
            {"from": "face_nano.d6", "to": "eye_jewels.din", "signal": "eyes DIN", "connector": "dupont_3p_2.54", "awg": 24, "note": "330 R in series"},
            {"from": "face_nano.d5", "to": "mouth_v.din", "signal": "mouth DIN", "connector": "dupont_3p_2.54", "awg": 24, "note": "330 R in series"},
            {"from": "chest_nano.d6", "to": "chest_strip.din", "signal": "chest DIN", "connector": "dupont_3p_2.54", "awg": 24, "note": "330 R in series, 1000 uF across the strip"},
            {"from": "face_nano.usb", "to": "host", "signal": "serial (ARDUINO_SERIAL_PORT)", "connector": "usb_mini_b", "awg": 28},
            {"from": "chest_nano.usb", "to": "host", "signal": "serial (CHEST_SERIAL_PORT)", "connector": "usb_mini_b", "awg": 28},
            {"from": "servo_ctl.usb", "to": "host", "signal": "CDC (R3X_SERVO_PORT)", "connector": "usb_micro_b", "awg": 28},
            {"from": "5 V 4 A supply", "to": "eye_jewels.din", "signal": "5V/GND", "connector": "dupont_3p_2.54", "awg": 22, "note": "LED power in at the jewels, not through the Nano"},
            {"from": "5 V 4 A supply", "to": "chest_strip.din", "signal": "5V/GND", "connector": "dupont_3p_2.54", "awg": 20},
            {"from": "6 V 10 A supply", "to": "servo_ctl.servo", "signal": "6V/GND servo rail", "connector": "XT30 -> servo bus", "awg": 14},
        ],
        "bom": [
            {"item": "Arduino Nano (ATmega328P)", "qty": 2, "category": "board", "url": "https://store.arduino.cc/products/arduino-nano", "unit_usd": 24.9},
            {"item": "WS2812 7-LED jewel", "qty": 2, "category": "led", "url": "https://www.adafruit.com/product/2226", "unit_usd": 5.95},
            {"item": "WS2812B strip 60/m (41 px: mouth 8 + chest 33)", "qty": 1, "category": "led", "note": "1 m reel"},
            {"item": "Raspberry Pi Pico", "qty": 1, "category": "board", "url": "https://www.raspberrypi.com/products/raspberry-pi-pico/", "unit_usd": 4.0},
            {"item": "INA219 breakout", "qty": 1, "category": "board", "url": "https://www.adafruit.com/product/904", "unit_usd": 9.95},
            {"item": "5 V 4 A supply", "qty": 1, "category": "power"},
            {"item": "6 V 10 A supply", "qty": 1, "category": "power"},
            {"item": "330 R resistor", "qty": 3, "category": "hardware"},
            {"item": "1000 uF 10 V capacitor", "qty": 2, "category": "hardware"},
        ],
        "notes": [
            "Two Nanos: set ARDUINO_SERIAL_PORT and CHEST_SERIAL_PORT whenever both are plugged in.",
        ],
    }


# ------------------------------------------------------------------------ grnwave

# DJLEDNanoV2.ino: each body board is 32 pixels - 8 small LEDs, then 6 groups of 4 x 5050.
# The sample animates 3 of the 6 groups per board (PanelA1..A3 = 12, 24, 28; B1..B3 = 40,
# 44, 48; C1..C3 = 84, 88, 92): read here as the groups behind that panel's 3 windows.
GRN_EXPOSED = [[1, 4, 5], [0, 1, 2], [3, 4, 5]]


# mech/electronics/logic_panels.py: the panels' LED holes and 2 x 3 window slots, measured
# from the kit STLs (model kit frame). Its open slots per panel come out as exactly
# GRN_EXPOSED - the sketch's animated groups are the ones behind the windows.
LOGIC_PANELS = json.loads((Path(__file__).resolve().parent / "logic_panels.json").read_text())["panels"]
PIPE_DEPTH = 0.001   # small LED under its light pipe: the pipe tip, just inside the hole
BLOCK_DEPTH = 0.003  # 5050s behind the window face
HIDDEN_DEPTH = 0.006  # behind the MS_LPI mount that covers the slot
BLOCK_PITCH = 0.006  # 2 x 2 5050s per group


def grnwave_body():
    out = []
    for b, p in enumerate(LOGIC_PANELS):
        n, across = p["normal"], p["across"]
        for h in p["holes"]:  # 8 small LEDs, light pipes into the panel's LED holes, bottom->top
            c = h["centre"]
            out.append(px("small", [c[i] - n[i] * PIPE_DEPTH for i in range(3)], n, 0.003, 0.003))
        slots = sorted(p["slots"], key=lambda s: s["group"])
        assert [s["group"] for s in slots if s["open"]] == GRN_EXPOSED[b], (b, slots)
        for s in slots:
            depth = BLOCK_DEPTH if s["open"] else HIDDEN_DEPTH
            for i in range(4):  # 2 x 2: bottom row first
                du = (i % 2 - 0.5) * BLOCK_PITCH
                dv = (i // 2 - 0.5) * BLOCK_PITCH
                pos = [s["centre"][k] + across[k] * du - n[k] * depth for k in range(3)]
                pos[1] += dv
                out.append(px("block" if s["open"] else "block_hidden", pos, n, 0.005, 0.005))
    return out


def grnwave(chest_layout):
    eyes = [px("eye", (EYE_L[0], EYE_L[1], EYE_L[2] - EYE_SIZE[2] / 2), w=0.008, h=0.008),
            px("eye", (EYE_R[0], EYE_R[1], EYE_R[2] - EYE_SIZE[2] / 2), w=0.008, h=0.008)]
    body_boards = []
    for b, p in enumerate(LOGIC_PANELS):
        pts = [h["centre"] for h in p["holes"]] + [s["centre"] for s in p["slots"]]
        c = [sum(v[i] for v in pts) / len(pts) for i in range(3)]
        n = p["normal"]
        c = (c[0] - n[0] * 0.012, c[1], c[2] - n[2] * 0.012)
        yaw = math.atan2(n[0], n[2])
        name = "ABC"[b]
        body_boards.append({
            "id": f"body_{name.lower()}", "model": "grnwave DJ Rex body LED board",
            "role": f"logic panel {b + 1} (chain {b * 32}-{b * 32 + 31}): 8 small LEDs + 6 x 4 5050",
            "url": "https://grnwave.com/product/dj-rex-body-led-set/",
            "dims_mm": [60.0, 40.0, 3.0],
            "mount": {"bracket": f"MS_P_1_Full logic panel {b + 1} on its MS_LPI mount, from behind (light pipes through the LED holes)",
                      "link": "torso_middle", "t": r(c),
                      "q": r([0.0, math.sin(yaw / 2), 0.0, math.cos(yaw / 2)], 5),
                      "inferred": True,
                      "inferred_note": "Board A/B/C = panels 1-3 (droid's right side, rear to front): the sketch's exposed groups match each panel's open window slots only in this order (mech/electronics/logic_panels.py). The board's depth behind the panel is inferred."},
            "connectors": [{"id": "in", "kind": "dupont_3p_2.54", "pins": ["DATA", "5V", "GND"]},
                           {"id": "out", "kind": "dupont_3p_2.54", "pins": ["DATA", "5V", "GND"]}],
            "volts": 5.0, "current": {"idle_ma": 10, "typical_ma": 180, "max_ma": 32 * MA_WS2812},
            "firmware": "firmware/grnwave_nano", "driver": "driver.grnwave", "support": "driven",
            "inferred": True,
            "inferred_note": "Board size is inferred (the shop lists 6 x 12 x 2 cm for the set of three, i.e. the parcel).",
        })
    return {
        "id": "grnwave_full_led",
        "label": "grnwave DJ Rex Full LED Set",
        "vendor": "grnwave workshop",
        "url": "https://grnwave.com/product/dj-rex-full-led-set/",
        "description": "3 body LED boards + 25 Bivar VLP-600-R light pipes, 1 mouth board, 1 eye board with 8 mm "
                       "NeoPixel-style LEDs. 5 V only; 3-pin 2.54 mm Dupont. Driven here by an Arduino Nano running "
                       "our firmware/grnwave_nano, which speaks the face/chest named commands, so the runtime drives "
                       "it unchanged.",
        "support": "driven",
        "emulator": "grnwave",
        "boards": [
            {
                "id": "nano", "model": "Arduino Nano (ATmega328P)", "role": "LED controller: body + eyes on D4, mouth on D5",
                "url": "https://store.arduino.cc/products/arduino-nano",
                "dims_mm": [45.0, 18.0, 8.0],
                "mount": {"bracket": "middle ring, behind the logic panels", "link": "torso_middle",
                          "t": [-0.10, 0.41, 0.02], "q": [0, 0, 0, 1], "inferred": True,
                          "inferred_note": "Anywhere within a short data run of body board A; placed behind panel 2."},
                "connectors": [
                    {"id": "usb", "kind": "usb_mini_b", "pins": ["VBUS", "D-", "D+", "GND"], "note": "serial 115200 to the host"},
                    {"id": "d4", "kind": "dupont_3p_2.54", "pins": ["D4", "5V", "GND"], "note": "body A DIN (grnwave sample: pin 4)"},
                    {"id": "d5", "kind": "dupont_3p_2.54", "pins": ["D5", "5V", "GND"], "note": "mouth board DIN (our choice)"},
                ],
                "volts": 5.0, "current": {"idle_ma": 20, "typical_ma": 25, "max_ma": 40},
                "firmware": "firmware/grnwave_nano", "driver": "driver.grnwave", "support": "driven",
            },
            *body_boards,
            {
                "id": "eyes", "model": "grnwave DJ Rex Eyes LED board (2 x 8 mm NeoPixel)",
                "role": "eyes: one 8 mm LED behind each eye bulb, daisy-chained after body C (96-97)",
                "url": "https://grnwave.com/product/dj-rex-head-led-board/",
                "dims_mm": [110.0, 20.0, 10.0],
                "mount": {"bracket": "head, behind the eye distortion / diffusion bulbs", "link": "head_tilt",
                          "t": r(((EYE_L[0] + EYE_R[0]) / 2, EYE_L[1], EYE_L[2] - EYE_SIZE[2] / 2 - 0.005)), "q": [0, 0, 0, 1],
                          "inferred": True, "inferred_note": "Board spans the eyes; exact outline not published."},
                "connectors": [{"id": "in", "kind": "dupont_3p_2.54", "pins": ["DATA", "5V", "GND"], "note": "no header fitted"}],
                "volts": 5.0, "current": {"idle_ma": 2, "typical_ma": 40, "max_ma": 2 * MA_PL9823},
                "support": "driven", "inferred": True,
                "inferred_note": "Two LEDs, droid's left first, from the sample sketch (RandomEyes on 96-97); size inferred.",
            },
            {
                "id": "mouth", "model": "grnwave DJ Rex Mouth LED board", "role": "mouth, its own data line (D5)",
                "url": "https://grnwave.com/product/dj-rex-mouth-led-board/",
                "dims_mm": [60.0, 70.0, 3.0],
                "mount": {"bracket": "mouth grille, from behind", "link": "head_tilt", "t": r(MOUTH), "q": [0, 0, 0, 1],
                          "inferred": True, "inferred_note": "Board outline from the shop listing (6 x 7 cm)."},
                "connectors": [{"id": "in", "kind": "dupont_3p_2.54", "pins": ["DATA", "5V", "GND"], "note": "no header fitted"}],
                "volts": 5.0, "current": {"idle_ma": 4, "typical_ma": 90, "max_ma": 8 * MA_WS2812},
                "support": "driven", "inferred": True,
                "inferred_note": "LED count (8, a V like ours) and order are inferred: the grnwave sample sketch does not drive the mouth.",
            },
        ],
        "lights": [
            {"name": "body", "pixels": 96, "layout": grnwave_body(),
             "driver": serial("grnwave", "GRNWAVE_SERIAL_PORT"), "board": "body_a", "link": "torso_middle",
             "data_line": "nano.D4", "chain_start": 0, "led": "WS2812B 5050 (small LEDs under VLP-600-R pipes)",
             "ma_per_led": MA_WS2812, "serves": ["chest", "speech_amplitude", "tempo", "chest_status"],
             "inferred": True,
             "inferred_note": "Per board: 0-7 small LEDs bottom->top in the panel's LED holes, 8-31 six groups of 4 in a 2 x 3 grid (group g: row g // 2 from the bottom, column g % 2 from the side away from the holes). Hole and slot centres are measured from the kit STLs (mech/electronics/logic_panels.json); with that grid the sample's animated groups are exactly each panel's 3 open windows. The grid order on the board is inferred; confirm on the boards."},
            {"name": "eyes", "pixels": 2, "layout": eyes,
             "driver": serial("grnwave", "GRNWAVE_SERIAL_PORT"), "board": "eyes", "link": "head_tilt",
             "data_line": "nano.D4", "chain_start": 96, "led": "8 mm PL9823 / WS2812D-F8", "ma_per_led": MA_PL9823,
             "serves": ["eyes"],
             "inferred": True, "inferred_note": "96 = the droid's left eye is assumed."},
            {"name": "mouth", "pixels": 8, "layout": mouth_v(),
             "driver": serial("grnwave", "GRNWAVE_SERIAL_PORT"), "board": "mouth", "link": "head_tilt",
             "data_line": "nano.D5", "chain_start": 0, "led": "WS2812B", "ma_per_led": MA_WS2812,
             "serves": ["mouth", "speech_amplitude"],
             "inferred": True, "inferred_note": "8 LEDs in a V (ours) until the board is counted."},
        ],
        "actuators": [],
        "power": [
            {"name": "5V LEDs", "volts": 5.0, "max_ma": 106 * MA_WS2812 + 40, "typical_ma": 800,
             "loads": ["nano", "body_a", "body_b", "body_c", "eyes", "mouth"],
             "psu": "5 V 4 A regulated, clean (grnwave: 5 V only, never 6 V); inject at body A and at the eyes",
             "psu_amps": 4.0,
             "note": "106 px x 60 mA = 6.4 A at full white; the firmware caps FastLED at 3 A (POWER_LIMIT_MA) and runs brightness 128."},
        ],
        "wiring": [
            {"from": "nano.d4", "to": "body_a.in", "signal": "DATA (330 R)", "connector": "dupont_3p_2.54", "awg": 24},
            {"from": "body_a.out", "to": "body_b.in", "signal": "DATA + 5V + GND", "connector": "dupont_3p_2.54", "awg": 22},
            {"from": "body_b.out", "to": "body_c.in", "signal": "DATA + 5V + GND", "connector": "dupont_3p_2.54", "awg": 22},
            {"from": "body_c.out", "to": "eyes.in", "signal": "DATA + 5V + GND (through the neck)", "connector": "dupont_3p_2.54", "awg": 22, "note": "long run: keep 5V/GND at 22 AWG"},
            {"from": "nano.d5", "to": "mouth.in", "signal": "DATA (330 R) + 5V + GND", "connector": "dupont_3p_2.54", "awg": 22},
            {"from": "5 V 4 A supply", "to": "body_a.in", "signal": "5V/GND injection", "connector": "dupont_3p_2.54 / screw terminal", "awg": 18, "note": "1000 uF across 5V/GND at the first board"},
            {"from": "nano.usb", "to": "host", "signal": "serial (GRNWAVE_SERIAL_PORT)", "connector": "usb_mini_b", "awg": 28},
        ],
        "bom": [
            {"item": "grnwave DJ Rex Full LED Set", "qty": 1, "category": "board", "url": "https://grnwave.com/product/dj-rex-full-led-set/", "unit_usd": 80.0,
             "note": "includes the 3 body boards, 25 light pipes, mouth board, eye board"},
            {"item": "Bivar VLP-600-R light pipe", "qty": 25, "category": "optics", "url": "https://www.digikey.com/en/products/result?keywords=VLP-600-R",
             "note": "in the set; 24 used (3 x 8), 1 spare"},
            {"item": "Arduino Nano (ATmega328P)", "qty": 1, "category": "board", "url": "https://store.arduino.cc/products/arduino-nano", "unit_usd": 24.9},
            {"item": "3-pin 2.54 mm Dupont jumper (servo lead)", "qty": 6, "category": "cable"},
            {"item": "5 V 4 A supply", "qty": 1, "category": "power"},
            {"item": "330 R resistor", "qty": 2, "category": "hardware"},
            {"item": "1000 uF 10 V capacitor", "qty": 1, "category": "hardware"},
        ],
        "notes": [
            "Sample code: github.com/grnwaveworkshop/DJ-RexBody (no licence; kept for reference under mech/vendor/grnwave/, not copied). Our firmware/grnwave_nano is written from its facts: FastLED on D4, 3 x 32 body then 2 eyes.",
            "RS-485 Nano breakouts (grnwave) suit bigger systems; this package uses USB serial like our other boards.",
            "The servos stay on the R3X servo controller: pair this package with r3x_native's servo_ctl, or add it here.",
        ],
    }


# ------------------------------------------------------------------------ community_morton

def morton():
    bracket = "Morton Frame Accessory Mounts / Electronics Mount Bracket (2).stl (172 x 85 x 172 mm, T-nuts on the 2020 frame)"
    mount = lambda t: {"bracket": bracket, "link": "torso_lower", "t": t, "q": [0, 0, 0, 1], "inferred": True,
                       "inferred_note": "The bracket's place on Morton's 2020 lower cage is not documented; boards laid out on the bracket face."}
    return {
        "id": "community_morton",
        "label": "Community: Sam Morton's stack (Kyber + Maestro 12 + HCR)",
        "vendor": "Sam Morton (community build)",
        "description": "Sam Morton's documented electronics: a Kyber controller, a Pololu Mini Maestro 12 and a Human "
                       "Cyborg Relations (HCR) sound board on his bespoke Electronics Mount Bracket, T-nutted to the "
                       "2020 lower cage (see his 'Readme Acc.txt'). We drive the Maestro; the Kyber and HCR are listed, "
                       "not driven. No LED boards are documented: the preview keeps the native face.",
        "support": "partial",
        "emulator": "none",
        "boards": [
            {"id": "kyber", "model": "Kyber droid controller", "role": "Morton's main controller (sound + servo scripting)",
             "dims_mm": [80.0, 60.0, 15.0], "mount": mount([0.03, 0.12, 0.0]),
             "connectors": [{"id": "maestro", "kind": "uart_3p", "pins": ["TX", "RX", "GND"], "note": "to the Maestro"},
                            {"id": "hcr", "kind": "uart_3p", "pins": ["TX", "RX", "GND"], "note": "to the HCR board"}],
             "volts": 5.0, "current": {"idle_ma": 80, "typical_ma": 120, "max_ma": 250},
             "support": "listed", "inferred": True, "inferred_note": "Dimensions and current are placeholders; no datasheet in the readme."},
            {"id": "maestro", "model": "Pololu Mini Maestro 12-Channel USB Servo Controller (#1352)",
             "role": "servo channels", "url": "https://www.pololu.com/product/1352",
             "dims_mm": [27.9, 35.6, 5.0], "mount": mount([-0.03, 0.12, 0.0]),
             "connectors": [{"id": "usb", "kind": "usb_mini_b", "pins": ["VBUS", "D-", "D+", "GND"]},
                            {"id": "servo", "kind": "servo_3p_2.54 x12", "pins": ["S0..S11", "V+", "GND"]},
                            {"id": "ttl", "kind": "dupont_3p_2.54", "pins": ["TX", "RX", "GND"]}],
             "volts": 5.0, "current": {"idle_ma": 30, "typical_ma": 40, "max_ma": 50},
             "driver": "driver.maestro", "support": "driven"},
            {"id": "hcr", "model": "Human Cyborg Relations sound board (HCR Vocalizer)", "role": "droid vocal sounds",
             "url": "https://humancyborgrelations.com/",
             "dims_mm": [70.0, 50.0, 15.0], "mount": mount([0.0, 0.06, 0.0]),
             "connectors": [{"id": "ttl", "kind": "uart_3p", "pins": ["TX", "RX", "GND"]},
                            {"id": "audio", "kind": "screw_2p", "pins": ["SPK+", "SPK-"]}],
             "volts": 5.0, "current": {"idle_ma": 60, "typical_ma": 300, "max_ma": 1500},
             "support": "listed", "inferred": True, "inferred_note": "Dimensions and current are placeholders."},
        ],
        "lights": [],
        "actuators": [
            {"actuators": ["*"], "board": "maestro", "driver": "maestro", "support": "driven",
             "note": "12 channels: set each actuator's driver to `maestro` and its channel in the profile; r3x-drivers' MaestroDriver streams frames (host follower)."},
        ],
        "power": [
            {"name": "5V logic + audio", "volts": 5.0, "max_ma": 1800, "typical_ma": 460, "loads": ["kyber", "maestro", "hcr"],
             "psu": "5 V 3 A UBEC", "psu_amps": 3.0, "note": "Placeholder figures; the readme lists no power budget."},
            {"name": "servo rail", "volts": 6.0, "max_ma": 12000, "typical_ma": 1500, "loads": ["maestro"],
             "psu": "6 V 10-15 A to the Maestro servo power", "psu_amps": 15.0, "note": "Placeholder: 12 channels of large servos."},
        ],
        "wiring": [
            {"from": "kyber.maestro", "to": "maestro.ttl", "signal": "TTL serial", "connector": "dupont_3p_2.54", "awg": 26},
            {"from": "kyber.hcr", "to": "hcr.ttl", "signal": "TTL serial", "connector": "dupont_3p_2.54", "awg": 26},
            {"from": "maestro.usb", "to": "host", "signal": "USB (our MaestroDriver; unplug from the Kyber to drive from R3X)", "connector": "usb_mini_b", "awg": 28},
        ],
        "bom": [
            {"item": "Kyber droid controller", "qty": 1, "category": "board"},
            {"item": "Pololu Mini Maestro 12-Channel", "qty": 1, "category": "board", "url": "https://www.pololu.com/product/1352", "unit_usd": 34.95},
            {"item": "HCR Vocalizer", "qty": 1, "category": "board", "url": "https://humancyborgrelations.com/"},
            {"item": "Morton Electronics Mount Bracket (printed)", "qty": 1, "category": "hardware",
             "note": "mech/vendor/dropbox/community/Sam Morton - Files/Morton Frame Accessory Mounts/"},
            {"item": "M5 T-nut + M5 x 10 BHCS (2020)", "qty": 4, "category": "hardware"},
        ],
        "notes": [
            "Readme: the Electronics bracket is bespoke (Kyber, Maestro12, HCR); the Plain bracket is the one to drill for your own boards.",
            "Morton leaves room above and below the brackets for wiring.",
        ],
    }


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    native_file = OUT / "r3x_native.json"
    if native_file.exists():
        chest = next(l for l in json.loads(native_file.read_text())["lights"] if l["name"] == "chest")["layout"]
    else:  # first run: the chest layout still lives in the robot profile
        prof = json.loads((REPO / "profiles/r3x/robot.json").read_text())
        chest = next(l for l in prof["lights"] if l["name"] == "chest")["layout"]
    assert len(chest) == 33
    for pkg in (native(chest), grnwave(chest), morton()):
        (OUT / f"{pkg['id']}.json").write_text(json.dumps(pkg, indent=2) + "\n")
        print("wrote", OUT / f"{pkg['id']}.json")


if __name__ == "__main__":
    main()
