# R3X mechanism options: what people have tried, and what to build

*2026-09-30. Brandon asked: "Look at all the options. What ideas have people had, what is best?"
Scope: a motorised DJ R3X on the Patrick Gray / David Ferreira v2 printed kit, with Hunter
Smoke's head gimbal, Brian Anderson's R-3X Animation internals and Sam Morton's 2020 cage.*

**Evidence key.** **[L]** = our own files and measurements (`mech/INVENTORY.md`, `mech/COMPARISON.md`
checks, `sim/docs/motion-control.md`). **[W]** = a cited web source. **[I]** = inferred
engineering judgement, not sourced. Few DJ R3X builds are documented in public; most of the
community work lives in the private RX builders' group and its Dropbox, which we already
have locally. So the web adds context from adjacent hobbies more than from R3X itself.

## 0. What is known

| Source | What it tells us |
|---|---|
| Kit guide v2 (78 pp.) [L] | A **static** build. There are no motor provisions. The only nods to motion are "DO NOT GLUE if you want to make it poseable" on the throttle arm, and the 10 in TamBee lazy susans under the rings. |
| Anderson, R-3X Animation [L] | 8 hobby servos on a Maestro 18, supplied 12 V into bucks. Pan uses a 15T pinion walking a 60T internal sector (4:1). Lift uses a 60 kg servo with a 19T pinion on a printed rack, on MGN12. Tilt uses a 19T pinion on a fixed gear. The visor is push-rod driven. The rings use servo pinions into internal sectors (3.8:1, 4.25:1). The hero shoulder is a direct 20 kg servo and the wrist a 7 kg micro. |
| Hunter Smoke head [L] | goBILDA U-joint gimbal. Two goBILDA 2000-0025-0002 servos on M4 ball-link push rods mix tilt and roll (±12° roll). The visor has its own servo. The coupler takes a **32 mm** tube; Anderson's tube is 26 mm. |
| Morton cage [L] | 7 lengths of 2020, T-nuts, and rivet nuts into the Ferreira Gil plate. He cut the extrusions short for "millimeter clearance" and added a nut under the susan to make a wire-chase gap. His electronics bracket is for **Kyber + Maestro12 + HCR**. |
| Bret Benz, `djr3x-v2` [W] | A second builder on Anderson's channel map with a Maestro and 11 channels. He added a **throttle arm with shoulder, elbow and wrist servos (ch 8–10)**. It has *coupled clearance constraints*: the arm can only move through tested clearance boxes. The elbow "falls" to its low end when unpowered. https://github.com/bretbenz84/djr3x-v2 |
| RX build team, "Gil" RX-91L [W] | Ferreira, Stroud, Gray, Zaharichuk. **~13 motors**, 3 arms, a head "with multiple axes", a drive base, and voice-driven mouth LEDs. There are no mechanism specs in public. https://www.starwars.com/news/star-wars-celebration-japan-2025-live-stage-droid · https://makezine.com/article/craft/cosplay-props/galaxy-of-droidsmiths-star-wars-droid-builders |
| Disney's Rex (Oga's Cantina, 2019) [W] | Built on the **A-1000** series. It is **all-electric**: no hydraulics, "precise movements", "start and stop nearly instantly", half the cabling. Its "torso and arms move to work the controls and dance" on a 3-hour loop. No DOF count or drive details are public. https://wdwnt.com/2019/02/photos-video-hondo-audio-animatronic-from-smugglers-run-in-star-wars-galaxys-edge-in-action-list-of-batuus-major-aa-figures/ |

Measured on our model [L]:

| Joint | Result |
|---|---|
| Pan | Loads are 2 % of stall. The limit is speed, **74°/s**, and range, **±34°**. |
| Lift | 16 % of stall. |
| Tilt | 25 % of stall. |
| Rings | 1–2 % of stall. |
| Hero shoulder | 27 % of stall. |
| Visor push rod | Hits the shell at −7° and the servo at +19°. The servo position is inferred. |
| Hero forearm | Touches the kit elbow block at rest. |

Motor torque is almost never the problem. **Speed, range, stiffness and fit are.**

## 1. Head pan

| Option | Range / speed | Backlash, noise | Cost, effort | Failure modes | Who uses it |
|---|---|---|---|---|---|
| **A. 270° servo, 15T→60T sector, 4:1 (current)** | ±34° (servo-limited; the sector allows ±43°), 74°/s | Printed-tooth backlash; coreless servo whine | Built | Speed is below the profile's 150°/s. Printed teeth wear. | Anderson [L] |
| **B. Same servo at 2:1: 30T pinion on a full 60T ring** | ±67°, ~150°/s at the 70 % rule; torque 4 % of stall | Same as A | 1 ring + 1 pinion reprint; the servo mount moves inward | Same as A | [I] from [L] numbers |
| C. NEMA17 + TMC2209, GT2 belt to a printed pulley on the turntable, hall homing | ±90° or more on a cable loop, 300°/s or more | Near-zero backlash (belt). StealthChop is near-silent at low speed [W] | ~$25 motor + driver, a homing magnet, firmware for step/dir | Missed steps if stalled (no feedback); needs homing on boot | Dome/turntable practice [W] |
| D. DC gearmotor + encoder + hall home | Unlimited with a slip ring | Gear backlash, some motor whine | Pololu 37D with 64 CPR encoder + RoboClaw [W] | Friction drive slips and loses home, so gear it [W] | R2 dome drive: https://github.com/thePunderWoman/Amidala/wiki/Dome-Drive-RoboClaw |
| E. Direct large servo on the tube | ±135°, fast | No gear stage, but the servo bearing carries the column side-load | Cheap | Side-load on the servo output shaft [I] | none found |
| F. Slewing bearing instead of the 10 in susan | Same as the drive fitted | Stiffer and lower wobble than a stamped susan [I] | igus PRT polymer ring, $$ https://www.igus.ca/product/22717 | Cost; redesign of the stage | [W] catalogue |

**Pick: B now, C as the end state.** B meets the profile's 150°/s and ±60°+ with one reprint and
no new electronics. C is the right answer if the RP2040 board gains step/dir outputs: it is
quiet, low-backlash and has more range. Its cost is homing logic and no position feedback.
A slip ring is unnecessary under ±180°: route the head loom down the tube with a service loop.

## 2. Head lift and neck stiffness

| Option | Speed, stroke | Stiffness, backlash | Holds when off? | Noise | Notes |
|---|---|---|---|---|---|
| **A. 60 kg servo, 19T pinion on a printed rack, MGN12 (current)** | ±37 mm, fast (84 of 253°/s needed) | Rack backlash is preloaded by gravity [I] | **No.** The head drops when power is cut [I]. Park before power-off. | Servo hum while holding | 16 % of stall [L] |
| B. NEMA17 + T8 lead screw on MGN12 | 8 mm lead ≈ 50–80 mm/s; 2 mm lead ≈ ¼ of that | Anti-backlash nut gives zero play [W] | Yes with a 2 mm lead (self-locking); 8 mm can drop [W] | Quietest with StealthChop | Heavier; needs a home switch. https://blog.igus.co.uk/?p=16312 |
| C. Linear actuator (12 V, pot feedback) | Slow (typically 5–15 mm/s) [I] | Stiff | Yes | Loud gearbox | Too slow for a "bob" |
| D. Belt carriage | Fast | A vertical belt needs a brake or counterweight | No | Quiet | No gain over A |
| E. Scissor | Long stroke | Floppy laterally | Varies | — | Wrong for a narrow tube |

**The wobble is not the drive; it is the column.** A 26 × 1.5 mm aluminium tube
cantilevers the head (≈1–1.6 kg) above the upper MGN12 guide [L]. Stiffness goes as
roughly OD³ × wall, so a **32 × 2 mm** tube is about 2.4× stiffer [I]. A 32 mm tube is also
exactly Hunter's coupler bore [L]. Keep the two guides as far apart as the rings allow, and
put the upper guide as high as possible so the overhang is short. Carbon tube at 32 mm is
lighter and stiffer again, but its clamps need to be longer [I].

**Pick: keep A. Move to a 32 mm tube and print the rack and pinion in PA-CF or PETG-CF**
(see section 10). Always park before power-off. Move to B only if the servo hum proves
audible over speech.

## 3. Head tilt, roll and nod

| Option | DOF | Pros | Cons |
|---|---|---|---|
| **A. Hunter's U-joint + 2 push-rod servos (current)** | tilt + roll | Proven on this kit. Both servos share the load. goBILDA 25-2: 21.6 kg·cm and 0.20 s/60° at 6 V [W]. Off-the-shelf ball links. | Needs a tilt/roll mix on channels 2 + 17 [L]. Ball-link slop. |
| B. Anderson's 19T pinion on a fixed gear | tilt | Simple, one servo | No roll; printed-tooth backlash; 25 % of stall [L] |
| C. Direct servos in a yoke | tilt + roll | No linkage slop | The servo carries head weight on its bearing; stacked servos add mass high on the column [I] |
| D. 3-DOF / 6-DOF Stewart "neck" | up to 6 | Organic motion; Cogley-style necks [W] | 3–6 actuators and IK for motion pan and lift already cover; packaging inside a 26–32 mm neck is hard. https://github.com/MarginallyClever/stewart-platform-animatronic |

**Pick: A.** It is already the chosen head, and a push-rod differential is the standard hobby
answer. Add the mix on the controller. Use goBILDA's steel ball links, not printed ones.

## 4. Visor

| Option | Pros | Cons |
|---|---|---|
| **A. Push rod (Anderson; current in `hunter_head/visor.py`)** | The servo can sit off-axis, out of the way | ~1.39:1 and non-linear. Hits the shell at −7° and the servo at +19° in our model [L]. |
| B. Direct servo on the visor axle (Hunter ships a visor servo mount) | Linear, no rod, fewest parts | Needs the servo in line with the ear axis |
| C. Cable (push-pull or Bowden) | Servo anywhere | Stretch and friction; overkill for a 0.15 kg visor [I] |

**Pick: B if the servo fits on the axle in Hunter's shell; otherwise A with the servo
relocated.** Either way, a 10–15 kg servo is enough; 35 kg is 4 % loaded [L]. A smaller servo is quieter
and lighter at the top of the column [I].

## 5. Ring rotation

| Option | Range | Backlash, noise | Effort | Failure modes |
|---|---|---|---|---|
| **A. Servo pinion into a printed internal sector on the 10 in susan (current)** | ±36° lower, ±25.5° top (the sector limits it) | Printed-tooth backlash; fine at 1–2 % load | Built | Pinion runs off the sector if over-commanded, so set hard limits |
| B. GT2 belt wrapped round the ring OD | Unlimited | Low | Needs a tensioner and belt clearance inside the shells | Belt skips if slack |
| C. Friction wheel on the ring | Unlimited | Quiet | Easy | **Slips and loses position** [W, dome-drive wiki] |
| D. Stepper + slewing bearing | Unlimited | Lowest | High | Cost; needs homing |

**Pick: A.** The loads are trivial and ±25–36° is what Rex does. No slip rings are needed: the
rings never pass ±180°, so give the hero-arm loom a service loop. The head does not ride the
rings anyway [L]. Full ring gears (unlimited range) are only worth it if you want spins. Then
use a 12-wire, 2 A capsule slip ring (~$40–67 [W], https://sparkfun.com/products/13065).

## 6. Arms

| Arm | Community state | Servo | Recommendation |
|---|---|---|---|
| Hero: shoulder + wrist | Anderson, motorised [L] | 20 kg direct (27 % of stall), 7 kg micro wrist | Keep. Rework the kit elbow block around the servo (interference at rest [L]). |
| Throttle: shoulder, elbow, wrist | Bret Benz motorised all three; clearance is coupled and the elbow drops when unpowered [W] | Not specified | **Defer.** If added, put the servos in the shoulder/torso and drive the wrist by push-pull cable. |
| Poker: shoulder, wrist | Nobody found [L][W] | — | Optional single shoulder servo later |
| Claws | Rigid in every build found | — | Push-pull cable from a torso servo if wanted |

Push-pull (bike-brake) cables that keep servos out of limbs are standard film practice
(https://forums.stanwinstonschool.com/discussion/comment/14599/). They cut arm mass and inertia,
at the cost of friction and some stretch [I].

## 7. Structure

| Option | Pros | Cons |
|---|---|---|
| **Morton 2020 cage (current)** | Metal load path from the base plate to the pan stage and lift guide. T-nut adjustable; cable-tie slots. Rivet-nuts into the Gil plate. | Millimetre clearances. The extrusions had to be cut short. |
| Randall printed frame (cagebottom / frame_mid / frame_top + top-ring brackets) | Fully printed; goes higher, up to the top ring | Printed members creep under a sustained head load [I] |
| Hybrid | Morton cage below; 2020 posts or Randall-style brackets up to the **upper neck guide** | More parts |

**Load paths [L][I].** The head's weight and moment go down the neck tube into the base
turntable, then the Morton cage, then the base plate. The upper neck guide takes the
tube's side-load. Tie it to the cage with metal, not only to the printed spacer rings, which
are cosmetic shells. The rings' weight sits on their susans and the static core
(LS_IC_1), then the pedestal. **Pick: hybrid.**

## 8. Actuators and controllers

| Actuator | Feedback | Noise | Cost | Fit |
|---|---|---|---|---|
| Hobby PWM servo (current) | None to the host | Buzz and whine when holding under load [I] | $15–40 | Everything except pan |
| Smart serial servo, Feetech STS3215 | Position, speed, load, current, temperature; 12-bit | Similar gear noise | ~$17–22; 19.5 kg·cm at 6 V (30 kg·cm 12 V variant) [W] https://core-electronics.com.au/feetech-sts3215-smart-servo.html | Visor, wrists, tilt. Stall detection comes for free. |
| Dynamixel | Same, better build | Quiet | Several times the Feetech price [I] | Not worth it at this scale |
| Stepper + TMC2209 | None unless an encoder is added | StealthChop is near-silent at low speed; ~60 dB at speed on a CNC [W] https://community.carbide3d.com/t/adventures-with-silent-stepper-drivers/22417/8 | ~$25 | Pan; lift if hum matters |
| DC gearmotor + encoder | Encoder | Gear whine | $40–60 + driver | Only if continuous rotation is wanted |

| Controller | Notes |
|---|---|
| Pololu Maestro | Community norm: USB, TTL, onboard scripts, 0.25 µs resolution [W] https://www.pololu.com/docs/0J40/1. PWM only, no steppers. |
| Kyber | RC (sBus) buttons into sounds and animations; drives a Maestro and MarcDuino [W]. It is a show trigger, not a motion planner. |
| HCR | Human-Cyborg Relations vocalizer: sound, not motion [I]. |
| **Our RP2040** | PIO does phase-aligned PWM, step/dir and a half-duplex UART for STS servos on one chip [I]. Trajectories run on-board (motion-control.md §8). **This is the only option that can mix Hunter's tilt/roll and run a stepper pan.** |

## 9. Power and wiring

| Item | Recommendation |
|---|---|
| Supply | 12 V in, as Anderson does. Separate bucks: (a) lift + tilt pair + shoulder, (b) rings + visor + wrist, (c) logic. Size each for **stall** current: 35–60 kg servos stall at several amps each [I]. |
| Protection | Fuse each branch. Add an INA219 per rail for stall detection (motion-control.md §8). Add an E-stop that cuts servo power but not logic. |
| Wiring | Star ground. Run the head loom down the neck tube with a service loop sized for ±67–90° pan plus 74 mm lift. No slip rings anywhere in the recommended build. |
| Power-up and shutdown | Staggered power-up. **Park before cutting power**, because the lift and hero elbow drop [W/I]. |

## 10. Recommended build

| Subsystem | Choice | Why |
|---|---|---|
| Head pan | Servo at **2:1** on a full 60T printed ring now; **NEMA17 + TMC2209 + GT2 belt + hall home** later | 2:1 meets the 150°/s profile and gives ±67°. The stepper adds quiet, range and low backlash. |
| Head lift | Keep the 60 kg servo + rack on MGN12; **32 × 2 mm tube**; widest guide spacing | The drive is fine at 16 % of stall. The wobble is tube bending. 32 mm matches Hunter's coupler. |
| Tilt / roll | Hunter's U-joint + 2 goBILDA push rods, mixed on the controller | Already chosen; a proven differential |
| Visor | Direct servo on the axle, 10–15 kg; push rod only if packaging forces it | Removes the modelled rod collisions and the non-linearity |
| Rings | Keep the servo + internal sectors; hard limits; service loops, no slip rings | 1–2 % load, and the range matches the character |
| Arms | Hero as built; throttle and poker deferred; cables for any claws | Bret Benz's throttle arm shows the clearance cost |
| Structure | Morton cage + metal tie to the upper neck guide | The metal load path carries the head moment |
| Actuators | PWM servos; one stepper (pan); STS3215 optional on small joints | Quiet where it matters; feedback where it is cheap |
| Control | Custom RP2040 (PWM + step/dir + serial bus) | Mixing, steppers and on-board trajectories |
| Gears | Pinions and sectors in PA-CF or PETG-CF, 5–6 walls, 35–50 % infill, teeth flat on the bed; pinion on a metal horn disc | Printed teeth are the weakest link. PETG is "mediocre for gears"; nylon is the best choice [W] https://howtomechatronics.com/how-it-works/how-to-3d-print-gears-the-ultimate-guide/ |

### Top 3 upgrades to the current plan (impact per effort)

1. **Re-gear the pan to 2:1: a 30T pinion on a full 60T ring.** It doubles both range and speed (±34°→±67°,
   74→~150°/s) for two reprinted parts and a moved servo mount. Torque is still 4 % of stall.
2. **Use a 32 mm neck tube (2 mm wall or carbon) and maximise the guide spacing.** This attacks the wobble
   at its source and removes the 26→32 mm adapter for Hunter's coupler. It needs three clamps redrawn.
3. **Reprint every gear in PA-CF or PETG-CF with gear print settings, and clamp the pinions to metal horn discs.**
   A cheap fix for the tooth-strength concern.

### Decisions for Brandon

- **Pan: servo at 2:1, or go straight to a stepper?** A stepper needs step/dir on the RP2040
  board and homing at boot.
- **Visor: direct or push rod?** It depends on whether a servo fits on the axle inside Hunter's shell.
- **Arm scope:** hero only, or throttle too? Throttle means 3 servos and coupled clearance limits.
- **Power-off behaviour:** accept the head dropping (park first), or pay for a self-locking lead
  screw on the lift.
- **Anderson's build lists 7 servos for 8 channels.** A fifth 35 kg servo is needed [L].
- **Carbon or aluminium for the 32 mm tube.**
