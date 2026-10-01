# R3X servo electronics: Teensy 4.1 wiring and parts list

Status: plan, 2026-10-01. Nothing here has been built or measured yet. The firmware port is
`firmware/` (see its README for the final channel → pin map); the runtime, animations and the
robot profile do not change with the board.

## The chain

```
Mac (dj-r3x runtime) ──USB──▶ Teensy 4.1 (motion controller, Rust firmware)
                                 │  smooths every move (same code as the sim), calibrates
                                 │  degrees → µs, watches the heartbeat and the rail current
                                 │  18 × 3.3 V servo pulses (50 Hz, 500–2500 µs)
                                 ▼
                         3 × 74AHCT245 (3.3 V → 5 V signal buffer)
                                 │  18 signal wires
                                 ▼
                         18 hobby servos (each one closes its own loop)
                                 ▲  power, never through the Teensy
12 V PSU ─ fuse ─ E-STOP ─ MOSFET (rail enable = "armed", Teensy pin) ─ INA219 shunt
                                 ├─ BEC A, 6.0 V 20 A ─ heavy servos (head, rings, hero arm)
                                 └─ BEC B, 6.0 V 10 A ─ arm servos + claws
12 V PSU ─ fuse ─ 5 V 5 A buck ─ Teensy VIN, face Nano, chest Nano, LED strips
All grounds meet at one star point (PSU −, BEC −, buck −, Teensy GND, Nanos GND).
```

**Why it looks like this**
- A hobby servo needs only power and a pulse, because the control loop is inside it. The
  Teensy is a pulse generator with a brain: smoothing, calibration and safety.
- The power for a servo never runs through a microcontroller. The Teensy only sends the
  signal wire.
- The software disarm (PS hold; the MOSFET is off at boot and on heartbeat loss) and the
  physical E-stop are separate. The robot must stop even if the software is wrong.
- Switching and measuring on the 12 V side carries half the current of the 6 V side, so the
  MOSFET, shunt and fuse are smaller. **Note for the firmware:** the INA219 then reads 12 V
  side current, roughly 0.55× the servo-side current. The stall trip (6 A for 300 ms in the
  profile today) must be re-set in 12 V terms and tuned on the bench.

## Power budget (from `profiles/r3x/robot.json` actuators)

| Servo | Count | Joints | Stall each (approx, at 6 V) |
|---|---|---|---|
| 35 kg 270° | 6 | neck pan, head tilt, visor, lower ring, top ring, middle ring | ~3–4 A |
| 60 kg 270° | 1 | head lift | ~5 A |
| DS3218 | 2 | hero shoulder (dual-shaft), throttle shoulder | ~2.5 A |
| MG996R | 2 | throttle elbow, poker shoulder | ~2.5 A |
| goBILDA 2000-0025-0002 | 1 | head roll | ~2.5 A |
| 7 kg micro | 1 | hero wrist | ~1.5 A |
| MG90S | 2 | throttle wrist, poker wrist | ~0.7 A |
| SG90 | 3 | hero, throttle and poker claws | ~0.65 A |

All 18 stalled at once is about 38 A. That never happens, but four heavy servos straining
together (a big head move during a ring turn) is about 15 A. Plan the heavy rail for 20 A,
and treat the 6 A trip in today's profile as too low once more than a few servos are fitted.

Status: only the first 9 rows' joints have servos on the current build. The claws, throttle
arm, poker arm and middle ring are reserved channels (`extended` in the profile).

## Parts list

| # | Part | Example | Qty | ~Price | Why |
|---|---|---|---|---|---|
| 1 | Teensy 4.1 | PJRC Teensy 4.1 (without Ethernet is fine) | 1 | $32 | 600 MHz, 30+ hardware PWM pins, spare serial ports for bus servos later |
| 2 | Signal buffer | 74AHCT245 octal buffer, DIP-20 | 3 | $3 | lifts 3.3 V pulses to 5 V; robust on long leads to the head |
| 3 | 12 V PSU | Mean Well LRS-350-12 (12 V 29 A) | 1 | $40 | feeds both BECs and the logic buck |
| 4 | BEC A, heavy rail | Castle Creations BEC 2.0 (20 A, adjustable, set to 6.0 V) | 1 | $40 | head, rings, hero arm |
| 5 | BEC B, arm rail | 10 A adjustable UBEC set to 6.0 V | 1 | $20 | arms and claws (micro servos dislike the heavy rail's noise) |
| 6 | Logic buck | 5 V 5 A buck (e.g. Pololu D36V50F5) | 1 | $25 | Teensy, two Nanos, LEDs (~1.7 A worst case at brightness 128) |
| 7 | Rail switch | logic-level N-MOSFET on a breakout (e.g. IRLZ44N or a 30 A MOSFET module) | 1 | $8 | the software arm/disarm |
| 8 | Current sense | INA219 breakout, shunt swapped to 10 mΩ (R010, 2 W) | 1 | $10 | stall detection (the firmware's `set_rail_ma`) |
| 9 | E-stop | latching mushroom switch, rated ≥ 30 A DC, or one driving a 40 A relay | 1 | $20 | physical stop on the 12 V servo feed |
| 10 | Fuses | inline blade fuse holders + 25 A (servo feed) and 5 A (logic) | 2 | $10 | |
| 11 | Capacitors | 2200 µF 16 V low-ESR at each BEC output; 470 µF at the head and ring servo clusters | 2 + 3 | $10 | absorb servo current spikes and brownouts |
| 12 | Distribution | Wago 221 lever nuts or a servo power distribution board; 14 AWG trunk, 20 AWG to the heavy servos | - | $20 | |
| 13 | Servo extensions | 3-pin servo leads, 22 AWG signal, sized per joint | ~18 | $15 | the head runs up the column, so route and strain-relieve |
| 14 | Powered USB hub | 4-port, externally powered | 1 | $20 | Teensy, face Nano, chest Nano and the DS3 to the Mac |
| 15 | Perfboard or proto shield | for the Teensy, buffers, headers | 1 | $10 | |

About $280 before servos. Prices are rough, 2026.

## Wiring rules
1. **Cut the Teensy's VUSB–VIN pad** when it is powered from the 5 V buck, so USB and the buck
   never feed each other. (Leave it intact while testing on USB power alone.)
2. **One star ground**: PSU −, both BEC −, buck −, Teensy GND, buffer GND, Nano GNDs and
   every servo − meet at one point. Pulses mean nothing without a shared ground.
3. **Servo power never touches the Teensy or the Nanos.** Servo red goes to a BEC rail, brown
   to ground, and only the signal goes to a buffer output.
4. **Rail enable low at boot.** The MOSFET gate gets a 10 kΩ pull-down, so servos stay limp
   until the firmware arms them (the runtime's PS hold).
5. **Buffers**: 74AHCT245 VCC at 5 V, DIR tied high (A → B), OE tied low; Teensy pins to A,
   servo signal leads from B, an optional 220 Ω series resistor per output.
6. **Capacitors at the load**: big ones at the BEC outputs, smaller ones where a group of
   servos is a long lead away (head, rings).
7. **Bring up one servo at a time** (see below). Never connect all 18 and arm on the first try.

## Teensy pins (summary; the firmware README is the authority)
- 18 servo outputs on hardware PWM pins (chosen by the firmware port).
- Pins 0/1 (Serial1) and 7/8 (Serial2) are kept free for smart bus servos later
  (Feetech STS / Dynamixel, half-duplex serial: position, load and temperature feedback).
- Pins 18/19: I2C to the INA219.
- One GPIO for the rail MOSFET; pin 13 is the status LED.

## Bring-up order
1. Teensy on USB only, no servo power: flash, check that the runtime finds it (`dj-r3x`,
   service `driver.servo` running) and that the pulses look right on a scope or logic
   analyser (50 Hz, ~1500 µs at centre).
2. One light servo (the visor): wire it to BEC A, arm with PS hold, calibrate in Bench mode
   (centre, direction, limits), and drive it from the controller.
3. The head (neck, tilt, roll), then the lift; watch the rail current in the panel.
4. The rings, then the hero arm. Set the stall trip from what step 3 measured.
5. Extended joints (arms, claws, middle ring) as their servos are fitted: fill in the
   channel and calibration in `profiles/r3x/robot.json`; nothing else changes.

## LED boards stay Arduino
The face (eyes and mouth) and chest Nanos keep their firmware and USB serial links
(`profiles/electronics/r3x_native.json`); they only move to the 5 V buck for power. The
Teensy could take over LEDs later (OctoWS2811), but there is no need now.
