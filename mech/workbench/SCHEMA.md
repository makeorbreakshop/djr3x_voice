# Mech manifest schema - `r3x.mech.manifest` v1

The contract between an assembly module (`mech/assemblies/<name>/assembly.py`), the
workbench service that builds it (`mech/workbench/`), and the panel's **Build** mode
(`sim/web/src/workbench/`). One JSON file per built assembly:
`mech/out/<assembly>/manifest.json`, next to its meshes. JSON for now; it is meant to be
generated into `r3x-contracts` later, so change it only by bumping `version` and noting
the change at the bottom of this file.

## Conventions

- **Units mm, degrees.** Every position is in the frame of the assembly that declares it.
- **Frame:** `+Y` up, `+Z` forward (the face / the base's front), `+X` the droid's left
  (show/SPEC.md "Frame and zeros"). A sub-assembly has its own frame; `mount` places it in
  its parent's.
- **Transforms** are `{"t": [x, y, z], "q": [x, y, z, w]}` (translation mm, unit quaternion),
  child-in-parent, applied rotate-then-translate. Omitted = identity.
- **Zero pose:** every transform and pivot is given with every joint at 0, which is the
  robot's rest pose (SPEC.md). Joint signs follow SPEC.md (`+head_tilt` tips down).
- **Paths** (`mesh`, `export`, `ref`) are relative to the manifest's own directory.
- **`inferred: true`** anywhere means the geometry/BOM did not state it; a person must
  confirm it. Always carry an `inferred_note` saying what was assumed.
- Unknown keys must be ignored by readers (forward compatible within a version).

## Top level

```jsonc
{
  "schema": "r3x.mech.manifest",
  "version": 1,
  "generated_at": "2026-09-30T12:00:00Z",
  "generator": "mech/workbench 0.1",
  "root": Assembly
}
```

## Assembly (recursive)

```jsonc
{
  "id": "hunter_head",                 // unique within the whole tree; [a-z0-9_]
  "name": "Hunter head mech",
  "description": "...",
  "guide": {"title": "R-3X kit assembly guide", "pages": [60, 71]},   // optional
  "frame_note": "origin = gimbal centre (head_tilt pivot) ...",
  "mount": {                           // where this frame sits in the parent (omit on the root)
    "parent_link": "head",             // the parent's link it rides on
    "transform": Transform,
    "mount_node": "hunter_head_mount", // optional: the parent's named mount point
    "inferred": false
  },
  "links":     [Link],
  "parts":     [Part],
  "joints":    [Joint],
  "linkages":  [Linkage],
  "gears":     [Gear],                 // optional: parts a joint turns through a gear ratio
  "fasteners": [Fastener],
  "steps":     [Step],
  "bom":       [BomLine],              // this assembly's own lines
  "bom_rollup":[BomLine],              // own + every descendant, merged by `key`
  "checks":    [Check],
  "children":  [Assembly | ChildRef],
  "variants":  [Variant],              // optional part choices (alternate eyes, mouth split, frame)
  "electronics": [Board],              // optional boards placed in this assembly
  "notes":     ["..."]
}
```

A child can be inlined, or referenced so two modules can be built separately:

```jsonc
// ChildRef - the reader loads `ref` and applies `mount` from here (it overrides the child's own)
{ "ref": "../hunter_head/manifest.json", "id": "hunter_head", "name": "Hunter head mech",
  "mount": { "parent_link": "head", "transform": Transform },
  // optional: how the child joins the parent, checked by the whole-droid suite (workbench/droid.py).
  // "<child id>/<part id>" names the child's side, a plain id the parent's.
  "interface": {
    "mates": [{"type": "concentric", "a": ["hunter_head/neck_coupler", "bore"], "b": ["neck_tube", "axis"]}],
    "fasteners": [{"id": "set_coupler_tube", "spec": {"type": "set_screw", "thread": "M4", "length_mm": 6},
                   "in": ["hunter_head/ins_coupler_side", "thread"], "onto": "neck_tube"}]
  } }
```

The root may carry `designs` (the published designs the build is assembled from, the Build view's
Library) and `ground` (the floor):

```jsonc
"designs": [{ "id": "hunter", "name": "Head gimbal", "author": "Hunter Smoke", "source": "...",
              "assemblies": ["hunter_head"],          // assembly ids that make it up
              "picks": {"internals": "column"},        // the variant picks that show it
              "deep": true,                            // optional: each assembly's whole subtree
              "only": ["shell"], "except": ["shell"],  // optional: part-class filters
              "look": "mechanism" }],                  // optional: the view it reads best in
"ground": { "y": -56.0,                               // the lowest modelled part, default picks (mm)
            "by_variant": {"internals:anderson_morton": -81.6},
            "inferred": true, "inferred_note": "the drive's wheels under the base plate are not modelled" }
```

A child mounted as a variant option carries `mount.variant = {group, id, default, reference?}`;
`reference: true` marks an option kept for comparison only ("reference, not engineered"): the panel
shows it, the whole-droid suite leaves it out.
A group may span several nodes (the droid's `internals`: the column under the base, Anderson's ring
drives under the rings); a `default: true` option anywhere in the tree wins.

A mated sub-assembly may name parts of its parent it stands on, `mount.rests_on: ["b_b_1"]` (with an
optional `rests_on_note`): the whole-droid suite joins them for its connected test (placement only).

The root may carry `couplings` (from `python -m workbench test <root>`, workbench/droid.py): coupled
joint limits, the dependent joint's range as a table over the driving one, profile joint names:

```jsonc
{ "joint": "throttle_elbow", "depends_on": "throttle_shoulder", "swept": ["throttle_wrist"],
  "clearance_mm": 1.0, "table": [[-50, -10, 45], [10, -10, 35], [50, -10, -10]],  // [a, b_min, b_max]; nulls: no b clears
  "because": ["ta_fa_1 / b_m_d_2"] }
```

## Link

A rigid body. Every part rides on exactly one link.

```jsonc
{ "id": "head", "name": "Head (plate + shell)", "joint": "head_roll" }  // joint that moves it; null = assembly ground
```

## Part

```jsonc
{
  "id": "head_mount_plate",
  "name": "Head Mounting Plate (RX Head Mech Base Plate V4)",
  "class": "shell | mech | servo | fastener | bearing | hardware",
  "link": "head",
  "transform": Transform,               // part mesh -> assembly frame at zero pose
  "mesh": "parts/head_mount_plate.glb", // display mesh, overview level (reduced), in the part's own frame
  "mesh_full": "parts/full/head_mount_plate.glb", // optional: the full-detail level, same frame; readers swap
                                         // it in for parts in focus or close (geom.display_lods)
  "mesh_sig": "3f1c0a9e2b7d",           // optional: the mesh's content hash (a reader may cache the GLB on it)
  "export": {"stl": "export/head_mount_plate.stl", "3mf": "export/head_mount_plate.3mf"}, // full detail
  "source": {"file": "RX Head Mech Base Plate V4.stl", "kind": "step | stl | dxf | generated",
             "entity": "Head_Mounting_Plate", "placement": "step | fitted | inferred"},
  "material": "PLA (printed)",
  "printed": true,
  "explode": [0, 1, 0],                 // unit direction the part leaves along
  "explode_mm": 60,                     // how far at explode = 1
  "bbox": [[minx,miny,minz],[maxx,maxy,maxz]],   // assembly frame, zero pose
  "triangles": {"display": 4000, "full": 12000, "source": 51000},
  "inferred": false, "inferred_note": "",
  "note": "",
  // optional: a part that stretches with a prismatic joint of this assembly (the neck spring with
  // head_lift): scaled along `axis` (the assembly's +Y only, in v1) about `anchor` (assembly frame)
  // by (rest_mm + joint value) / rest_mm. Readers without it draw the part rigid at rest.
  "stretch": {"joint": "head_lift", "axis": [0, 1, 0], "anchor": [0, 608.5, 0], "rest_mm": 55},
  // optional: seen from outside the droid (true) or hidden (false). Omitted = a shell is, nothing else is.
  // Set on the non-shell parts you see on the finished droid (the neck post, the visor, the hero arm), and
  // false on shells that sit inside it (the kit's logic-panel diffusers and LED boards).
  "exposed": true,
  // optional: another part of the build supersedes this one ("<assembly id>/<part id>"): the kit's hero
  // elbow, forearm and wrist under Anderson's arm. It stays in the manifest (the kit as published, a
  // library design, still shows it) but is not part of our build: readers do not draw it in the build's
  // model, and the whole-droid suite and BOM leave it out. The replacing part may list what it replaces
  // in `replaces: ["ha_eb_1", ...]` (informational).
  "replaced_by": "top_ring/hero_servomount",
  // optional: how the part looks on the finished droid and what it is printed in (see "Finish")
  "finish": {"paint": "paint_charcoal", "print": {"filament": "PLA", "color": "#1f2023", "color_name": "Black"}}
}
```

### Finish

Assigned where the part is declared (the assembly module or mech/parts), never in a reader:
`workbench/finish.py` has the helpers and filament presets, `assemblies/kit/finish.json` the kit's
table (which `sim/model/build_r3x.py` also bakes the Original rig from). `material` stays on the part.

```jsonc
"finish": {
  "paint": "paint_orange",   // sim/web/src/palette.json class it is painted in (Build's Exterior look);
                             // "none" = seen but deliberately bare; omitted = not painted (inside the droid)
  "print": {"filament": "PETG", "color": "#6b6f75", "color_name": "Grey"},  // printed parts: what it is printed
                             // in (Mechanism look, the print list); filament PLA | PETG | PA-CF | ...
  "color": "#3d4045", "color_name": "goBILDA servo grey",  // optional, purchased parts: their real colour
                             // (else a reader shows the material: aluminium, steel, brass)
  "kit": "H_LE_1",           // optional: the kit part code whose paint this is (a kit part, or what stands in for one)
  "note": ""
}
```

A part seen from outside (`exposed`, else a shell) that is printed or a shell must have `paint`; one
that is printed must have `print` with all three fields. The builder adds a `finish` check (kind
`finish`) to the root: `fail` lists the parts that break this. Readers show a missing paint in a
loud colour, never a guessed one.

## Joint

```jsonc
{
  "id": "head_tilt",
  "name": "Head tilt (nod)",
  "type": "revolute | prismatic | fixed",
  "parent_link": "cross", "child_link": "head",
  "pivot": [0, 0, 0], "axis": [1, 0, 0],   // assembly frame, zero pose; right-hand rule
  "unit": "deg | mm",
  "limits": {"min": -20, "max": 25},       // design range the build uses
  "profile_joint": "head_tilt",             // profiles/r3x/robot.json joint, or null (not in the profile)
  "profile_limits": {"min": -20, "max": 25},// the profile's hard range, for comparison
  "drive": {
    "kind": "direct | push_rod | push_rod_pair | gear | external | none",
    "servos": ["servo_l", "servo_r"],       // part ids
    "linkages": ["rod_l", "rod_r"],
    "gear_ratio": 1.0,                      // joint deg per servo deg when linear; null when not
    "servo_deg_per_unit": 1.0,              // optional, linear drives (gear/direct): servo deg per joint
                                            // unit (deg or mm); servo -> joint = servo / this
    "note": "pitch = both horns together, roll = differential"
  },
  // optional: when the joint's drive depends on a variant pick (the rings' drives follow `internals`), every
  // option's full drive, each with its `variant` {group, id}; `drive` itself is the default's
  // "drive": {..., "variants": [{"kind": "gear", "servos": ["col_lower_servo"], ..., "variant": {"group": "internals", "id": "column"}}]}
  "zero": {"how": "text: what 0 looks like and how it is set", "step": "s06"},
  "inferred": false, "inferred_note": ""
}
```

`external` = the motion exists in the robot but not in this mechanism (e.g. the head pan
comes from the neck tube). A profile joint with no joint here is listed in `notes`.

## Linkage (closed form in both Python and the panel)

```jsonc
{
  "id": "rod_l", "kind": "push_rod",
  "servo": "servo_l",
  "horn": {"link": "head", "centre": [x,y,z], "axis": [0,1,0], "radius": 24,
           "zero_dir": [1,0,0],            // horn direction when the servo is at its centre pulse
           "ball_offset": 6},              // ball centre above the horn plane, along axis
  "ground": {"link": "ground", "point": [x,y,z]}, // the rod's other ball, on the parent side
  "rod_length": 71.2,                       // ball centre to ball centre, set at zero pose
  "servo_range_deg": [-150, 150],
  "parts": ["rod_l_rod", "rod_l_end_a", "rod_l_end_b"]
}
```

Given the child link's pose, the servo angle `th` solves `|A(th) - B| = L`, with
`A(th) = C' + o n' + r (cos th u' + sin th w')`: `a cos th + b sin th = c`, with
`a = 2 r D.u'`, `b = 2 r D.w'`, `c = L^2 - |D|^2 - r^2`, `D = C' + o n' - B`,
`th = atan2(b, a) +/- acos(c / hypot(a, b))`, the branch nearest the zero direction.
No real root = the pose is unreachable (reported, never clamped silently).

## Gear (parts a joint turns through a ratio)

The push rod's sibling for gear and direct drives: the parts that turn about their own axle as a
joint moves - a servo's pinion climbing a rack, a pinion in a ring's sector, a coupler on a direct-drive
spline - so moving the joint visibly turns the servo's output, and a servo slider can drive the joint.

```jsonc
{ "id": "g_lift", "kind": "rack_pinion | internal | spur | direct",
  "joint": "head_lift",                   // the joint whose value turns it
  "joint_assembly": "lower_ring",         // optional: the assembly (id) that declares `joint`, when it is
                                          // not this one (the column's ring pinions turn with the kit's rings)
  "link": "sled",                         // the link its axle is fixed to (the parts' `link` too)
  "pivot": [x, y, z], "axis": [x, y, z],  // the axle, assembly frame, zero pose
  "deg_per_unit": -2.984,                 // turn (deg, right-hand about axis) per joint unit (deg or mm)
  "servo": "col_lift_servo",              // the servo whose spline this is (optional)
  "servo_deg_per_unit": -2.984,           // servo deg per joint unit; servo -> joint = servo deg / this
  "parts": ["col_lift_pinion"], "fasteners": ["col_scr_liftgear"],
  "mesh_with": "col_lift_rack" }          // what it meshes (reference)
```

A gear part's pose: `link matrix x T(pivot) R(axis, deg_per_unit x value) T(-pivot)`. Readers without
gears draw the parts rigid on their link (as before). Python: `workbench/kinematics.py gear_matrix`.
The sign is set from the geometry (the teeth stay in mesh through the sweep), not assumed.

## Fastener

```jsonc
{
  "id": "f_plate_shell_1",
  "spec": {"type": "shcs | bhcs | fhcs | insert | nut | lock_nut | washer | threaded_rod | ball_link | set_screw",
           "thread": "M4", "length_mm": 12, "standard": "ISO 4762", "mcmaster": "91290A146"},
  "key": "shcs-M4x12",                  // BOM merge key
  "joins": ["head_mount_plate", "head_bottom"],
  "link": "head",
  "placed": true,                       // false = named by the guide but not located in 3D
  "transform": Transform,               // +Z of the fastener = insertion direction; head at origin
  "mesh": "fasteners/shcs-M4x12.glb",   // shared per spec
  "step": "s03",
  "inferred": false, "inferred_note": ""
}
```

Unplaced fasteners omit `transform`/`mesh`; the panel lists them under their step.

## Step

```jsonc
{
  "id": "s03", "n": 3,
  "title": "Bolt the mount plate to the head bottom",
  "parts": ["head_mount_plate"],        // parts that arrive in this step
  "context": ["head_bottom"],           // parts to show solid as context (optional)
  "fasteners": ["f_plate_shell_1"],     // placed fasteners used here
  "unplaced": [{"key": "shcs-M3x8", "spec": {...}, "count": 4, "note": "fan screws"}],
  "tools": ["2.5 mm hex key"],
  "notes": ["Heat-set the inserts before this step."],
  "joint": "head_tilt",                 // a step that sets a joint's zero (servo centring)
  "pose": {"head_tilt": 0},             // pose to show the step in (optional)
  "guide_page": 64,                     // page in the source guide (optional)
  "inferred": true, "inferred_note": "order not stated by the source"
}
```

## BomLine

```jsonc
{ "key": "shcs-M4x12", "item": "M4 x 12 socket head cap screw", "qty": 6,
  "category": "fastener | servo | bearing | hardware | printed | electronics",
  "spec": {...}, "source": "https://...", "parts": ["..."], "fasteners": ["..."],
  "inferred": false, "inferred_note": "" }
```

## Variant

Mutually exclusive options, grouped. Parts named by no variant always show; a part named by
an option shows only while that option is selected. The panel shows one selector per
`group`; the option with `default: true` (else the first) is selected on load.

```jsonc
{ "id": "eyes_hunter_v2", "group": "eyes", "name": "Hunter eyes v2",
  "default": false,
  "parts": ["eye_l_v2", "eye_r_v2"],    // shown when selected
  "steps": ["s14b"],                   // steps that only apply to this option (optional)
  "children": ["mic_mouth_split"],     // child assemblies that only apply (optional)
  "source": {"file": "community/Hunter/eyes v2.stl", "author": "Hunter"},
  "inferred": false }
```

Electronics packages are variants too (`group: "electronics"`), so a package swaps its boards
in and out like any other option.

## Board (electronics)

```jsonc
{
  "id": "maestro18", "name": "Pololu Mini Maestro 18",
  "package": "maestro_basic",           // the electronics variant that brings it (optional)
  "link": "chest_frame",                // rides this link
  "transform": Transform,               // board frame: origin at a corner on the bottom face,
                                        // +Z out of the component side, +X along the long edge
  "size_mm": [45.7, 27.9, 1.6],
  "mesh": "boards/maestro18.glb",       // optional; else the panel draws a PCB-green box
  "mounting": {"holes": [[2.5, 2.5], [43.2, 2.5]], "fastener": "shcs-M2x6", "standoff_mm": 5},
  "connectors": [
    {"id": "ch0", "kind": "servo_pwm | power | usb | i2c | uart | gpio | audio | led",
     "at": [3.0, 20.0, 1.6],            // board frame
     "to": {"part": "servo_l"} | {"board": "pi5", "connector": "usb1"},
     "note": "head_tilt L"}
  ],
  "power": {"in_v": [5, 16], "max_a": 3.0, "from": {"board": "psu", "connector": "out1"}},
  "inferred": false, "inferred_note": ""
}
```

## Check

```jsonc
{
  "id": "interf_head_tilt", "kind": "interference | clearance | torque | reach",
  "status": "pass | warn | fail",
  "title": "head_tilt: first contact",
  "summary": "First contact at +31.5 deg (limit +25): 6.5 deg margin",
  "joint": "head_tilt", "value": 31.5,
  "pose": {"head_tilt": 31.5},          // the pose that shows it; the panel applies it on click
  "parts": ["head_mount_plate", "neck_coupler"],
  "assumptions": ["..."]
}
```

## Real parts, features and mates (added 2026-09-30, still v1: all optional)

Parts and fasteners carry:

```jsonc
"cad": "vendor | parametric | mesh | placeholder",  // vendor CAD (B-rep); our parametric model;
                                                  // a reference mesh; a sized box
"catalog": "gobilda:2913-0004-0241",              // mech/parts/catalog.json id
"features": { "hole_fl1": {"type": "axis", "p": [x,y,z], "d": [0,-1,0], "r": 2.24},
              "face_fl1": {"type": "plane", "p": [x,y,z], "n": [0,1,0]},
              "spline": {"type": "spline", "p": [...], "d": [...], "teeth": 25},
              "ball_c": {"type": "ball", "c": [...], "r": 4.75} }   // assembly frame, zero pose
```

A parametric part's `source` holds `{"kind": "parametric", "model": "parts/hunter.py:base_plate",
"params": {...}, "reference": "<STL>", "regression": {bbox_mm, volume_ratio, hausdorff_mm,
p95_mm, mean_mm}}`. Fasteners may also carry `linkage`/`role` (a ball stud turns with its horn).

The assembly carries its connections (workbench/mates.py):

```jsonc
"mates": [ { "id": "m031_threaded", "type": "concentric | seated | coplanar | spline | ball_link | threaded | press | glue",
             "a": {"part": "scr_plate_bottom_1", "feature": "shank"},
             "b": {"part": "ins_bottom_3", "feature": "thread"},
             "params": {"engage_mm": 6.0, "into": "insert | plastic | metal | nut", "hole_depth_mm": 6.0},
             "solved": true,          // placed from this mate (false: a vendor assembly placed it; verified)
             "note": "" } ]
```

`checks` also holds the test suite's results (`kind: "test"`, TESTS.md); their `status` may be
`explained` (a traced failure with its cause and fix in the summary).

## Changes

- v1 (2026-09-30): first version; `variants` and `electronics` added the same day, before any
  consumer shipped, so still v1 (both optional). Same day: `cad`, `catalog`, `features`,
  `mates`, test checks and the `explained` status (all optional).
- v1 (2026-10-01): `gears` (optional) and `drive.servo_deg_per_unit` (optional): gear and direct
  drives connected the way push rods are (a joint turns its pinions; a servo maps to its joint). Same
  day: part `exposed`, joint `drive.variants`, root `designs` and `ground` (all optional). Same day: part
  `replaced_by` (optional; a reader that does not know it draws a replaced part, as before).
- v1 (2026-10-01): part `finish` (optional: paint, print filament and colour, purchased colour) and the
  root's `finish` check. Readers that do not know it colour by `material`, as before.
