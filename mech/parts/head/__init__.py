"""Parametric models of the printed parts in Hunter Smoke's head mech (ours, build123d).

Each module exposes `make(params: dict | None = None, **kw) -> build123d.Part` built as design
intent (sketches, extrudes, fillets, hole features), in its reference file's frame, with named
mate features, the resolved parameters, the reference and printability metadata attached
(`_common.finish`). Defaults reproduce Hunter's part; `tests/` regresses each one against its
reference (volume, bbox, surface deviation, every hole found on the reference by feature).

    from parts.head import base_plate
    plate = base_plate.make()                       # Hunter's V4
    plate.features["hole_fl1"]                      # an M4 clearance hole onto the head bottom
    base_plate.make(fit=0.2, servo="ds3218", servo_pattern_w=None)

`PARTS` maps the head assembly's part ids (assemblies/hunter_head) to the module that models them,
for the workbench's swap hook (`PARAMETRIC` in the assembly module).
"""

PARTS = {
    "mount_plate": "parts.head.base_plate",
    "neck_coupler": "parts.head.neck_coupler",
    "neck_joint_member": "parts.head.neck_joint_member",
    "custom_joint_piece": "parts.head.custom_joint_piece",
}
