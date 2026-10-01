# r3x-servo: motion controller firmware (Teensy 4.1)

The Teensy 4.1 build of the r3x_servo controller. Protocol, behaviour and the host side are
shared with the RP2040 build: [`../README.md`](../README.md). This crate is wiring only:
`src/main.rs` (RTIC app: USB CDC, INA219, rail enable, LED, the three tasks) and `src/pwm.rs`
(18 servo outputs).

## Stack

| Crate | Version | Role |
|---|---|---|
| `teensy4-bsp` (+ `imxrt-hal`, `imxrt-ral`, `imxrt-rt`) | 0.6 | board resources, clocks, pins, linker script, FlexSPI boot header |
| `rtic` (`thumbv7-backend`) | 2 | tasks and priorities |
| `rtic-monotonics` (`imxrt_gpt1`) | 2 | `Mono`: GPT1 at 1 MHz, 64-bit microseconds |
| `imxrt-usbd` + `usb-device` + `usbd-serial` | 0.4 / 0.3 / 0.2 | USB CDC ACM |
| `teensy4-panic` | 0.3 | panic = blink the LED |

Why: `teensy4-rs` / `imxrt-rs` is the maintained Rust line for the RT1062 (the BSP, HAL and
USB driver all released in 2026; the BSP's own examples are RTIC v2), and it ships a working
Teensy 4.1 boot image and linker script. Embassy has no released RT1062 support
(`embassy-imxrt` and `embassy-nxp` are 0.0.0 placeholders on crates.io), so the embassy-rp
structure of the Pico build does not carry over. RTIC v2's async software tasks map one-for-one onto the Pico's embassy tasks.

## Build

```bash
rustup target add thumbv7em-none-eabihf
rustup component add llvm-tools && cargo install cargo-binutils   # for the .hex
cd firmware/servo/teensy41
cargo build --release      # target/thumbv7em-none-eabihf/release/r3x-servo-fw-teensy41 (ELF)
cargo hex                  # alias: objcopy -> r3x-servo-teensy41.hex (gitignored)
```

`.cargo/config.toml` sets `target-cpu=cortex-m7`: the bare `thumbv7em-none-eabihf` target
assumes a single-precision FPU, which would put the f64 follower in soft float. Measured: 45 KB
of code, 63.5 KB in the hex (`teensy_loader_cli`: 0.8 % of flash).

Host tests (no board): `cd rust && cargo test -p r3x-motion -p r3x-servo-ctl -p r3x-drivers`.

## Flash

No debug probe on a Teensy; it loads over USB through its bootloader chip.

```bash
brew install teensy_loader_cli          # or the Teensy Loader app (File > Open HEX)
teensy_loader_cli --mcu=TEENSY41 -w -v r3x-servo-teensy41.hex   # then press the PROGRAM button
```

Without reaching the button: setting the port to 134 baud asks the firmware to reboot into
the bootloader (the Teensyduino convention; it turns the rail and every pulse off first):

```bash
stty -f /dev/cu.usbmodemXXXX 134        # Linux: stty -F /dev/ttyACM0 134
teensy_loader_cli --mcu=TEENSY41 -w -v r3x-servo-teensy41.hex
```

The board enumerates as VID:PID `16c0:0483` (PJRC's Teensy USB serial, so PJRC's udev rule
and loaders recognise it), product `r3x-servo`, serial `r3x-servo-1`. Set `R3X_SERVO_PORT`
to its port. There is no log output: the USB port is the protocol, and a panic blinks the
LED.

## Pin map (Teensy 4.1)

One timebase for every servo output: IPG 150 MHz / 64 = 2.34375 MHz. A 20 ms frame is
exactly 46 875 counts; a pulse is `round(us * 75 / 32)` counts, 0.43 us resolution (the
Pico's is 1 us). All submodules and timer channels run the same 50 Hz period, so every
output has its own independent width even where two share a FlexPWM submodule (A, B and X
each have their own compare registers).

| Channel | Teensy pin | Pad | Output | Profile actuator (`profiles/r3x/robot.json`) |
|---|---|---|---|---|
| 0 | 2 | GPIO_EMC_04 | FlexPWM4 SM2 A | neck |
| 1 | 3 | GPIO_EMC_05 | FlexPWM4 SM2 B | headlift |
| 2 | 4 | GPIO_EMC_06 | FlexPWM2 SM0 A | headtilt |
| 3 | 5 | GPIO_EMC_08 | FlexPWM2 SM1 A | visor |
| 4 | 6 | GPIO_B0_10 | FlexPWM2 SM2 A | elbow |
| 5 | 9 | GPIO_B0_11 | FlexPWM2 SM2 B | hand |
| 6 | 10 | GPIO_B0_00 | QuadTimer1 ch0 | lowarm |
| 7 | 11 | GPIO_B0_02 | QuadTimer1 ch2 | heroarm |
| 8 | 12 | GPIO_B0_01 | QuadTimer1 ch1 | hero_claw |
| 9 | 22 | GPIO_AD_B1_08 | FlexPWM4 SM0 A | throttle_shoulder |
| 10 | 23 | GPIO_AD_B1_09 | FlexPWM4 SM1 A | throttle_elbow |
| 11 | 24 | GPIO_AD_B0_12 | FlexPWM1 SM2 X | throttle_wrist |
| 12 | 25 | GPIO_AD_B0_13 | FlexPWM1 SM3 X | throttle_claw |
| 13 | 28 | GPIO_EMC_32 | FlexPWM3 SM1 B | poker_shoulder |
| 14 | 29 | GPIO_EMC_31 | FlexPWM3 SM1 A | poker_wrist |
| 15 | 33 | GPIO_EMC_07 | FlexPWM2 SM0 B | poker_claw |
| 16 | 36 | GPIO_B1_02 | FlexPWM2 SM3 A | middle_ring |
| 17 | 37 | GPIO_B1_03 | FlexPWM2 SM3 B | headroll (Hunter head mech, base build) |
| - | 18 / 19 | GPIO_AD_B1_01 / _00 | LPI2C1 SDA / SCL (Wire), 100 kHz | INA219 @ 0x40 on the servo rail |
| - | 30 | GPIO_EMC_37 | GPIO3_IO23, active high, low at boot | servo rail enable (MOSFET / relay gate) |
| - | 13 | GPIO_B0_03 | GPIO2_IO03 | onboard LED: on = following, off = holding |

Kept free: 0/1 (Serial1) and 7/8 (Serial2) for future bus servos (Feetech / Dynamixel);
14/15 (Serial3, and QuadTimer3 ch2/ch3 PWM if two more channels are ever needed).

How each row is verified:

- **FlexPWM A/B** (13 channels): muxed through `imxrt-iomuxc`'s `flexpwm::Pin`, whose types
  carry the module, submodule and output from the chip's pad table; `pwm.rs` asserts every
  row of this table against them at compile time.
- **FlexPWM X** (pins 24/25, ALT4) and **QuadTimer1** (pins 10/11/12, ALT1): the HAL has no
  pin trait or driver for these, so the ALT values and the register setup follow the RT1060
  reference manual's pad mux table and Teensyduino's `pwm.c`, which drives the same pins.
  Not compile-checked. The QuadTimer uses the NXP SDK's `QTMR_SetupPwm` scheme (toggle on
  alternating COMP1 / COMP2, counter re-initialised at each compare); its clock gate
  (CCGR6 CG13) is turned on in `Servos::new`.
- No pulse = output held low: the FlexPWM "on" compare is parked at a count the counter
  never reaches; a QuadTimer channel is stopped with its output forced low.
- FlexPWM values are double-buffered (LDOK) and load at the frame boundary; QuadTimer values
  load at the next edge. A new width never cuts or doubles a pulse.

## Tasks

| Task | Priority | Rate | Does |
|---|---|---|---|
| `usb` | 3 (USB_OTG1 IRQ; also woken every 5 ms by the USB GPT) | on USB events | CDC bytes -> `Controller::receive`; TX queue -> CDC |
| `control` | 2 | 200 Hz (`delay_until`, no drift) | `Controller::tick`, pulses out, rail enable, LED, telemetry (25 Hz) |
| `rail` | 1 | 100 Hz | INA219 shunt register -> `Controller::set_rail_ma` |

`rail` is lowest: a wedged I2C bus spins only that task, never the control loop. The TX
queue is 2 KB of whole frames, as on the Pico; frames are dropped while USB is unconfigured.

## Bring-up checklist (needs a real board)

Nothing below has been run: the firmware builds and the hex loads into `teensy_loader_cli`,
but no Teensy has been connected.

1. Flash with no servo power connected. The LED stays off (holding, no host). The host sees a
   CDC port `16c0:0483`, product `r3x-servo`. Set `R3X_SERVO_PORT` to it. **Unverified: USB
   enumeration and CDC data both ways.**
2. Start the runtime; health `driver.servo` = running, firmware string in the log.
3. Scope every servo pin: nothing until the first heartbeat, then a 1490 us pulse at 50.0 Hz
   (+-0.5 us); pin 6 (channel 4) starts 200 ms after pin 2 (channel 0): power-up is staggered
in groups of 4 channels. **Unverified:
   PWM timing, and especially the hand-written paths: pins 24/25 (PWM_X) and 10/11/12
   (QuadTimer).** Check each holds low when its channel is unpowered and starts with a
   whole pulse, not a runt or an inverted frame. Fallback if PWM_X misbehaves: move channels
   11/12 to pins 14/15 (QuadTimer3 ch2/ch3, same code path as pins 10-12).
4. INA219: **unverified I2C.** Confirm the shunt on the board, then read `rail_ma` in
   telemetry against a bench meter at idle and with one servo stalled by hand. Set
   `StallConfig` trip/clear from those numbers (defaults are placeholders).
5. Rail enable: pin 30 low from boot (fit the gate pull-down, `../README.md`), high only after
   the first enabling heartbeat.
6. One servo on the rail, Bench mode, Drive tab -> Calibrate: enable its output, jog, mark
   centre / direction / limit ends, save. Restart the runtime (CONFIG is sent on connect).
7. Kill the runtime mid-move: the joint must brake and hold within 250 ms (LED off,
   telemetry HOLDING). Unplug USB mid-move: same.
8. `stty ... 134` with servos connected: rail drops, then the bootloader takes over.
   **Unverified: the `bkpt #251` reboot.** If it faults instead, the PROGRAM button still works.
9. Connect all servos; watch the staggered power-up on the rail current (no brown-out).
10. Compare with the sim: replay the same show with the twin following telemetry; accept at
    the motion-control.md §7 criteria (RMS < 2 deg, no soft-limit overshoot).
