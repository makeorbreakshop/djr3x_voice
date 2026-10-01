# r3x-servo: motion controller firmware

Plan D6 / Phase 8. The host sends goals at event time plus a heartbeat
(`rust/crates/r3x-drivers/PROTOCOL.md`); this board runs the follower, calibration and pulses.

- **Logic**: `rust/crates/r3x-servo-ctl` (`no_std`, no alloc, no HAL). Host tests in
  `tests/sim.rs` drive it with a fake clock against the real host driver.
- **Trajectory + calibration**: `rust/crates/r3x-motion`, the same code the performer runs
  (the sim twin), so the robot and the sim compute the same pulses.
- **Board crates**: wiring only. Same protocol, tasks, rates and safety on both; the host
  cannot tell them apart except by the USB IDs.

| Board | Crate | Status |
|---|---|---|
| Teensy 4.1 (i.MX RT1062, Cortex-M7 600 MHz, f64 FPU) | [`teensy41/`](teensy41/README.md) | **the build target**; builds, not yet run on hardware |
| Raspberry Pi Pico (RP2040, Cortex-M0+) | [`rp2040/`](rp2040/README.md) | kept; builds |

Each crate is its own cargo workspace (not part of `rust/`), with its own target in
`.cargo/config.toml`, so `cargo build --release` in the crate directory is the whole build.

## Host side

The runtime's driver (`r3x-drivers`, `servo::r3x`) opens `R3X_SERVO_PORT`; it never
auto-detects the controller. The LED-board probes skip it on any board: a port whose USB
product string is `r3x-servo`, or whose VID:PID is one of the controller builds
(`2e8a:000a`, `16c0:0483`), is never probed (`link::is_servo_controller`, tested).

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
  INA219 -> rail reads 0, stall detection off.
- Status LED: on = following, off = holding.

Servo signal is 3.3 V on both boards; most hobby servos accept it. Level-shift (74AHCT125)
if one does not. Fit a pull-down (10-100 k) on the rail MOSFET gate: the enable pin floats
from reset until the firmware drives it low.
