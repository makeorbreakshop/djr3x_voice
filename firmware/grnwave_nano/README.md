# grnwave_nano: R3X firmware for the grnwave DJ Rex Full LED Set

An Arduino Nano sketch for the [grnwave workshop DJ Rex Full LED Set](https://grnwave.com/product/dj-rex-full-led-set/):
3 body boards, the eye board and the mouth board. It speaks our **named_v1** words, the
same ones the face (`rex_face_v3_clean`) and chest (`rex_chest_v1`) boards take, so the R3X
runtime drives it unchanged. Select it with `"electronics": "grnwave_full_led"` in
`profiles/r3x/robot.json`, or `R3X_ELECTRONICS=grnwave_full_led`. The package is
`profiles/electronics/grnwave_full_led.json`.

Its twin is `rust/crates/r3x-performer-core/src/leds/grnwave.rs`: the performer runs it,
and the sim shows what this board shows. When you change an effect here, change it there
too (same names, same parameters). `cargo test -p r3x-performer-core --test leds_grnwave`
covers it.

We wrote it from the facts in grnwave's sample sketch (`DJLEDNanoV2.ino`: FastLED on D4,
the chain order and LED counts). That repo has no licence, so none of its code is used here.
A reference copy is kept, gitignored, at `mech/vendor/grnwave/`.

## Build

```bash
arduino-cli core install arduino:avr
arduino-cli lib install FastLED              # tested with 3.10.3
arduino-cli compile --fqbn arduino:avr:nano firmware/grnwave_nano
arduino-cli upload  --fqbn arduino:avr:nano -p /dev/cu.usbserial-XXXX firmware/grnwave_nano
```

Clone Nanos with the old bootloader need `--fqbn arduino:avr:nano:cpu=atmega328old`. On the
Nano it uses 60 % of flash and 57 % of RAM (both `DIFFUSED` settings).

## Wiring

| From | To | Notes |
|---|---|---|
| Nano D4 | body A `in` (DATA) | 330 R in series |
| body A `out` -> body B `in` -> body C `in` | | 3-pin 2.54 mm Dupont (DATA, 5V, GND) |
| body C `out` | eye board `in` | through the neck; eyes are chain 96 (the droid's left) and 97 |
| Nano D5 | mouth board `in` | its own line (`MOUTH_ON_MAIN 1` chains it after the eyes instead) |
| 5 V supply | body A 5V/GND | **5 V only, never 6 V.** 1000 uF across it; 4 A supply |
| Nano USB | host | `GRNWAVE_SERIAL_PORT` (otherwise the port is probed) |

The chain: per body board, 0-7 are the small LEDs (light pipes, bottom to top), then 6
groups of 4 x 5050. Each panel shows 3 of the 6 groups through its windows (`EXPOSED` in
`config.h`, taken from the groups grnwave's sample animates). The rest stay dark.

## Protocol (115200 baud, newline-terminated)

| Word | Meaning | Reply |
|---|---|---|
| `SI SE SL ST SS` | idle / engaged / listening / thinking / speaking | `+` |
| `SF` | green "done" flash, then engaged | `+` |
| `Mnnn` | speech amplitude 000-255 | - |
| `Bnnn` | music tempo, 000 = none | - |
| `Xn` | 0 normal, 1 boot sweep, 2 sleep, 3 fault | `+` |
| `Hxxx` | health mask (hex), bit i = window i healthy (A0 A1 A2 B0 ...) | - |
| `R` | reset (silent, so the face driver's `R` probe can't mistake it) | - |
| `?` | identify | `Grnwave: ...` |

It boots printing `GRNWAVE READY`, and the face and chest drivers back off from it.
`driver.grnwave` merges the performer's two streams. It takes `S*`, `SF` and `M` from the
face stream and `B`, `X` and `H` from the chest stream. It sends at the profile's
`audio.mouth_hz`, send-on-change, and always sends `M000` before a non-speaking state.

## Customising

- **`effects.cpp` > `LOOKS`** is the registry: one row per state, with one effect each for
  the body dots, windows, bare-window blocks, eyes and mouth. To change a look, swap in a
  different function.
- **Windows are units.** Each window has an opal diffuser (the package's
  `lights.body.windows`; `DIFFUSED 1` in `config.h`), so it shows one colour. Window effects
  write one colour per window (`setWindow` / `win[]`, board-major = the health bits) and
  `presentWindows` puts it on the window's 4 x 5050. Idle/engaged blink the droid's blocks
  (`fx_win_blink`), thinking steps a cyan block through the windows (`fx_win_think`),
  speaking makes each panel's 3 windows a VU meter (`fx_win_level`), and music changes
  every window's colour on the beat (`fx_win_beat`). With `DIFFUSED 0` (no covers) the
  `blocks` column adds per-pixel detail inside a window (`fx_blk_spin` while thinking).
- **`fx_*.cpp`** has one effect per file. The parameters are named constants at the top of
  each file. To add an effect, copy one, rename it, declare it in `effects.h` and put it in
  `LOOKS`. Effects get a `Ctx` (`ms`, the speech `level` 0..1, `bpm`, `beat`) and write
  through the helpers in `state.h`: `setWindow`, `setEyes`, `smallIdx`, `mouthRank`,
  `scaled`.
- These run on top of every look, in this order: `fx_beat_chase` + `fx_win_beat` (while
  music plays), `fx_hidden_off`, `fx_health` (a down subsystem blinks its window red), and
  `fx_flash` (SF). The system states `fx_boot`, `fx_sleep` and `fx_fault` replace everything.
- `config.h` holds the pins, the counts, `EXPOSED`, `BRIGHTNESS` (128) and
  `POWER_LIMIT_MA` (3000).

## To confirm on the real boards (inferred until then)

- Which physical panel is board A, B or C. We assume our chest panels 1-3, rear to front on
  the droid's right side.
- That the 3 animated groups per board are the ones behind the windows, and which window
  each one sits behind.
- The eye order (96 = the droid's left).
- The mouth LED count and order. We assume 8 in a V like ours; grnwave's sample doesn't
  drive the mouth.
- The board outlines. The shop's 6 x 12 x 2 cm is the parcel.

There's no Pi Pico variant yet. FastLED supports the RP2040, so it should port by changing
the pins in `config.h`, but it's untested.
