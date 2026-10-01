# r3x_servo wire protocol v1

Host (`r3x-drivers`, `servo::r3x`) <-> motion controller firmware (`firmware/servo`: Teensy 4.1,
or the RP2040 build; logic in `rust/crates/r3x-servo-ctl`, host-tested against this driver).
Implements plan D6 / `sim/docs/motion-control.md` §8: the host sends **goals at event time**
plus a heartbeat; the controller runs the follower, calibration and pulses. Reference codec:
`src/servo/proto.rs` (its tests are the conformance vectors).

## Framing

USB CDC serial (baud ignored; 115200 on a UART bridge). Exception, Teensy 4.1 build only:
134 baud = reboot into the bootloader for flashing (rail and pulses off first).

```
wire   = COBS(packet) 0x00
packet = type:u8  seq:u16  payload  crc:u16
```

- Little-endian throughout; `f32` is IEEE-754.
- `crc` = CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF, no reflection, no xorout) over
  `type..payload`. Check value: `"123456789"` -> `0x29B1`.
- COBS: 0x00 never appears inside a frame, so a receiver resyncs at the next 0x00. Drop any
  frame that fails COBS, CRC or length checks. Max packet 1024 bytes.
- `seq` increments per host packet (wraps at 65535). The controller echoes the last applied
  host `seq` in telemetry and in `NAK`. Controller packets carry their own counter.

## Host -> controller

| type | name | payload | notes |
|---|---|---|---|
| 0x01 | HELLO | - | Controller answers HELLO_REPLY. Host retries 3x, 1 s each, then runs mock. |
| 0x02 | CONFIG | `ch:u8 center_us:f32 center_value:f32 trim_us:f32 invert:u8 gear:f32 mm_per_deg:f32 pulse_min_us:u16 pulse_max_us:u16 range_deg:f32 soft_min:f32 soft_max:f32 v_max:f32 a_max:f32 j_max:f32` (50 B) | Sent for every channel after HELLO. Values come from the Robot Profile (actuator calibration + its primary joint). `mm_per_deg` 0 = revolute. |
| 0x03 | GOAL | `ch:u8 target:f32 v_max:f32 a_max:f32 j_max:f32` | Target in **joint units** (deg or mm). A limit of 0 = the channel's configured one; a nonzero limit is still capped by the configured one. |
| 0x04 | HEARTBEAT | `flags:u8` (bit0 = outputs enabled) `[mask:u32]` | Every 100 ms, and immediately when an enable changes. `mask` bit n = channel n enabled (the stage's per-actuator outputs); absent = all. A masked channel holds. |
| 0x05 | DIRECT | `ch:u8 us:u16` | Direct pulse (legacy shows, the Bench calibration jog): clamped to the pulse range, converted to joint units and followed like a goal (soft limits, v/a/j), never a jump. |
| 0x06 | PARK | - | Ramp every channel to its park pose (`center_value`). |

## Controller -> host

| type | name | payload |
|---|---|---|
| 0x81 | HELLO_REPLY | `version:u8 channels:u8 firmware:utf8...` (version = 1) |
| 0x82 | TELEMETRY | `flags:u8 rail_ma:u16 last_seq:u16 n:u8` then `n` x `us:u16 x:f32 flags:u8`, in channel order 0..n-1 |
| 0x83 | NAK | `seq:u16 code:u8` (1 bad channel, 2 unconfigured, 3 bad length, 4 unknown type, 5 bad value: non-finite, empty range, limit <= 0) |

Telemetry at 20-50 Hz. `us` = commanded pulse (0 = off), `x` = follower position in joint
units. Status `flags` (and per-channel `flags`, same bits where they apply):

| bit | meaning |
|---|---|
| 0 | HOLDING: heartbeat lost or outputs disabled; ramped to hold, goals ignored |
| 1 | OVERCURRENT (rail INA219; identifies *a* stall, not which channel) |
| 2 | UNCONFIGURED: status = no channel configured since boot; per channel = that channel |
| 3 | PARKED |
| 4 | SOFT_LIMIT: a goal was clamped since the last telemetry |

## Controller behaviour (firmware contract)

- Boot: outputs off, write park, enable, power up in staggered groups; UNCONFIGURED until
  CONFIG arrives; no motion before the first HEARTBEAT with outputs enabled.
- Per channel at >= 200 Hz: clamp to soft limits, two-stage jerk-limited follower
  (`r3x-motion` `trajectory`, shared with the performer), calibration (`r3x-motion` `calib`,
  bit-identical to the performer's `Channel::value_to_us`),
  1 us pulse resolution, hardware PWM phase-aligned.
- Heartbeat silent > 250 ms, or `outputs enabled` = 0: ramp to hold, set HOLDING, ignore
  GOAL/DIRECT/PARK until a heartbeat with outputs enabled arrives. Ramp to hold = brake at
  `a_max` to where the joint can stop (no reversal); the pulse stays on so the servo holds.
- OVERCURRENT (rail above the trip level for 300 ms) holds every channel the same way until
  the rail has been below the clear level for 1 s.
- Staggered power-up: the rail enables at the first enabling heartbeat; channel n starts
  pulsing (at its park pose) `(n / 4) * 200 ms` later and ignores goals until then.
- Accepted packets (including goals ignored while holding) advance `last_seq`; NAKed and
  corrupt ones do not.
- Frame log parity (`{frame, t, targets[]}`) comes from telemetry on the host: `us` per
  channel at 25 Hz is the controller's commanded pulse. The MCU keeps no log.

## Host behaviour

- Port: `R3X_SERVO_PORT` (never probed: opening an LED Nano resets it). `FORCE_MOCK_SERVO`.
- Fail-open: no answer -> mock (goals dropped, health `degraded`).
- Health: telemetry older than 1 s -> `error`; OVERCURRENT -> `error`; HOLDING while enabled or
  UNCONFIGURED -> `degraded`.
- Enables are per channel: the stage's actuator outputs become the heartbeat `mask`; goals and
  jogs for a disabled channel are not sent. All outputs off = `outputs enabled` 0.
- Bench calibration jog (`perf cal_jog`) is a DIRECT; `perf cal_save` rewrites the actuator's
  calibration in the profile (`servo::calibrate`), applied at the next connect.
