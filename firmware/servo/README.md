# r3x-servo: motion controller firmware (RP2040)

Plan D6 / Phase 8. The host sends goals at event time plus a heartbeat
(`rust/crates/r3x-drivers/PROTOCOL.md`); this board runs the follower, calibration and pulses.

- **Logic**: `rust/crates/r3x-servo-ctl` (`no_std`, no alloc, no HAL). Host tests in
  `tests/sim.rs` drive it with a fake clock against the real host driver.
- **Trajectory + calibration**: `rust/crates/r3x-motion`, the same code the performer runs
  (the sim twin), so the robot and the sim compute the same pulses.
- **This crate**: wiring only (`src/main.rs`): USB CDC, PWM/PIO, INA219, rail enable.

## Build

```bash
rustup target add thumbv6m-none-eabi
cargo install flip-link            # linker (stack-overflow protection)
cargo install probe-rs-tools       # only to flash/debug
cd firmware/servo
cargo build --release              # target/thumbv6m-none-eabi/release/r3x-servo-fw
cargo run --release                # flash over SWD (probe-rs, e.g. a Pico as debug probe)
```

Host tests (no board): `cd rust && cargo test -p r3x-motion -p r3x-servo-ctl -p r3x-drivers`.

## Pin map (Raspberry Pi Pico)

| Channel | GPIO | Source | Profile actuator (`profiles/r3x/robot.json`) |
|---|---|---|---|
| 0-15 | GPIO n | hardware PWM slice n/2, A (even) / B (odd) | neck, headlift, headtilt, visor, elbow, hand, lowarm, heroarm, hero_claw, throttle_*, poker_* |
| 16 | GPIO16 | PIO0 SM0 | middle_ring |
| 17 | GPIO17 | PIO0 SM1 | (spare) |
| - | GPIO20 / GPIO21 | I2C0 SDA / SCL | INA219 @ 0x40 on the servo rail |
| - | GPIO22 | output, active high | servo rail enable (MOSFET / relay gate) |
| - | GPIO25 | output | onboard LED: on = following, off = holding |

- PWM: 125 MHz / 125 = 1 MHz counter, TOP 19999: 50 Hz, compare = pulse in whole us. All
  eight slices start in one `PWM_EN` write (phase-aligned).
- PIO: level = `clk_sys * us / 3e6` cycles (the HAL's `write` rounds to ~1.6 % short, so it
  is not used); its frame is ~19.7 ms (HAL `set_period` rounding, harmless for servos).
- Servo signal is 3.3 V; most hobby servos accept it. Level-shift (74AHCT125) if one does not.

## Behaviour (all in `r3x-servo-ctl`)

- 200 Hz control loop: soft-limit clamp -> two-stage jerk-limited follower (braking
  `v <= sqrt(2 a d)` toward each soft limit) -> calibration -> 1 us pulse.
- Boot: rail off, no pulses. First heartbeat with outputs enabled: rail on, channels start at
  their park pose in groups of 4, 200 ms apart; goals to an unpowered channel are ignored.
- Heartbeat silent > 250 ms, outputs disabled, a channel masked off, or rail overcurrent
  (> 6 A for 300 ms, clears < 4 A for 1 s): ramp to hold (brake to a stop, no reversal,
  pulse kept on) and ignore goals.
- Telemetry at 25 Hz: flags, rail mA, last applied seq, per channel pulse / position / flags.
- INA219: shunt register only (10 uV/LSB), `SHUNT_MILLIOHM = 10` (32 A full scale). No
  INA219 -> rail reads 0, stall detection off, a warning on RTT.

## Bring-up checklist (needs a real board)

1. Flash with no servo power connected. `probe-rs` RTT shows `r3x-servo 0.1.0`; the host
   sees a CDC port (VID 2e8a / PID 000a). Set `R3X_SERVO_PORT` to it.
2. Start the runtime; health `driver.servo` = running, firmware string in the log. Scope
   GPIO0: nothing until the first heartbeat, then a 1490 us pulse at 50.0 Hz; GPIO4 starts
   200 ms after GPIO0. Check PIO channels 16/17 read the commanded width within +-1 us.
3. Measure the control loop: toggle a spare GPIO around `tick` (soft-float f64 on the M0+;
   expect well under 5 ms for 17 channels). If tight, enable embassy-rp's
   `intrinsics` / `rom-v2-intrinsics` ROM float routines and re-check parity.
4. INA219: confirm the shunt value on the board, then read `rail_ma` in telemetry against a
   bench meter at idle and with one servo stalled by hand. Set `StallConfig` trip/clear from
   those numbers (defaults are placeholders).
5. One servo on the rail, Bench mode, Drive tab -> Calibrate: enable its output, jog, mark
   centre / direction / limit ends, save. Restart the runtime (CONFIG is sent on connect).
6. Kill the runtime mid-move: the joint must brake and hold within 250 ms (LED off,
   telemetry HOLDING). Unplug USB mid-move: same.
7. Connect all servos; watch the staggered power-up on the rail current (no brown-out).
8. Compare with the sim: replay the same show with the twin following telemetry; accept at
   the motion-control.md §7 criteria (RMS < 2 deg, no soft-limit overshoot).
