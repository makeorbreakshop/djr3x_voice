# Proposal: electronics packages in `r3x.mech.manifest` v1

For the workbench agent (`mech/workbench/SCHEMA.md`, "Board (electronics)" + variants with
`group: "electronics"`). Brandon relays it.

## What exists now

The runtime has a data file for each electronics package: `profiles/electronics/<id>.json`.
The schema is the Rust type `ElectronicsPackage` in `rust/crates/r3x-contracts/src/electronics.rs`,
with JSON Schema at `rust/contracts-schema/electronics_package.schema.json` and TS at
`sim/web/src/generated/ElectronicsPackage.ts`. There are three packages: `r3x_native`,
`grnwave_full_led` and `community_morton`. `profiles/r3x/robot.json` selects one
(`"electronics": "r3x_native"`), and the package's LED groups become the profile's light
groups. So the performer, the drivers and the sim all read these files.

Each package carries: boards (model, `dims_mm`, mount = bracket + link + centre pose, connectors,
volts, current idle/typical/max, firmware, driver, support), LED groups (pixel positions in
the body frame, data line, chain start), actuator drives, power rails, wiring and a BOM.

## Proposal: one source of truth, the manifest references it

1. **A variant names its package.** Add an optional field to Variant:
   `"electronics_package": "grnwave_full_led"`. It's only valid on `group: "electronics"`. The
   variant `id` can simply equal the package id.
2. **The workbench generates `Board` entries from the package, rather than authoring them twice.**
   For each `package.boards[i]`:
   | manifest `Board` | from the package |
   |---|---|
   | `id`, `name` | `id`, `model` |
   | `package` | the package `id` |
   | `link` | `mount.link` (the rig/profile joint names: `torso_middle`, `head_tilt`, `torso_lower`); map to your link ids |
   | `transform` | `mount.t` is the **board centre in metres, body frame**, and `mount.q` is its orientation. Convert to mm and move to your corner origin: `t_mm = 1000*t + R(q)·(-L/2, -W/2, -H/2)`, with `[L, W, H] = dims_mm` |
   | `size_mm` | `dims_mm` |
   | `connectors[].id/kind` | `connectors[].id/kind` (our kinds are physical, e.g. `dupont_3p_2.54`; add yours as `signal`) |
   | `connectors[].to` | from `wiring[]`: `from: "board.connector"` -> `to` |
   | `power` | `{in_v: [volts, volts], max_a: current.max_ma/1000}`; `from` from the wiring's supply line |
   | `inferred`, `inferred_note` | `inferred` \|\| `mount.inferred`, notes joined |
3. **Fields to add to Board (optional, so still v1)**:
   - `"current": {"idle_ma", "typical_ma", "max_ma"}`: the panel's power budget needs typical as
     well as max.
   - `"support": "driven | partial | listed"`: whether our runtime talks to the board. The
     Kyber and HCR boards are `listed`.
   - `"driver"` (e.g. `driver.grnwave`) and `"firmware"` (a repo path).
   - `"leds"`: `{"group": "body", "count": 96, "data_line": "nano.D4", "chain_start": 0}`.
     The pixel positions stay in the package, so the manifest doesn't duplicate 96 points.
     The panel reads them from `profile.package.lights[]` (the gateway's hello carries the
     resolved profile), or from the file.
4. **Package-level data stays in the package.** The panel's Electronics section reads the
   power rails, the wiring list with AWG, and the BOM with purchase links from
   `profile.package`. The manifest should add the package's `bom` lines to `bom_rollup`
   under `category: "electronics"`, keyed by `item`.
5. **Brackets.** `mount.bracket` is free text today, e.g. "Morton Frame Accessory Mounts /
   Electronics Mount Bracket (2).stl". Once the kit assembly has part ids for brackets, we
   would add `mount.part` (a manifest part id) next to it.

## Panel hook (already in place)

`sim/web/src/electronics.ts` exports:

- `mountElectronics(el, pkg, {boards})`: the selector plus the boards, LEDs, power, wiring and
  BOM tables;
- `BoardsView`: translucent boxes at each board's mount;
- `PackageLeds`: LEDs at their real positions.

Build mode can mount these as its Electronics section. Until then they live in Bench, in the
Electronics tab (`data-modes="bench build"`).

## Open questions for the workbench

- **Link ids.** Do your links reuse the profile joint names (`torso_middle`, `head_tilt`)? If
  not, a small map in the workbench would do.
- **Wiring.** Should the manifest show wiring as 3D polylines? The package only has endpoints.
  Routing would need waypoints: a `wiring[].via` list of `[x, y, z]` in metres, body frame.
