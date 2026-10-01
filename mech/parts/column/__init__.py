"""The central column internals (ours, build123d): a fixed 2020 column on the Gil plate, a lift
carriage on MGN12 rails driven by a GT2 belt from the column foot, the head-pan unit riding the
carriage, and the ring-drive brackets on the column. Every part is modelled in the body frame at
the rest pose (`_layout.py` holds the shared numbers); assemblies/column/assembly.py puts them
together with their hardware and mates.
"""

PARTS = {
    "parts.column.gil_plate": "Gil base plate (Ferreira), aluminium, inferred outline",
    "parts.column.foot_plate": "column foot plate, aluminium",
    "parts.column.top_plate": "column top plate, aluminium",
    "parts.column.base_ring": "shell support ring round the pedestal's foot (printed)",
    "parts.column.lift_bracket": "lift servo bracket on the foot plate (printed)",
    "parts.column.drive_pulley": "GT2 60T drive pulley on the lift servo's 1906 hub (printed)",
    "parts.column.idler_bracket": "belt idler bracket under the top plate (printed)",
    "parts.column.carriage": "lift carriage: bearing housing, pan-servo cradle, belt clamp (printed)",
    "parts.column.neck_hub": "pan hub in the carriage's bearings (aluminium, turned)",
    "parts.column.pan_gear": "pan gears, 25T module 2: on the hub, and on the pan servo (printed)",
    "parts.column.clockspring_case": "clock-spring cassette for the head's cables (printed)",
    "parts.column.ring_pinion": "ring-drive pinions (Anderson's teeth) on 1906 hubs (printed)",
    "parts.column.drive_bracket": "ring-drive brackets on the column's back posts (printed)",
    "parts.column.neck_tube": "the short neck, 26 x 1.5 aluminium, cross-drilled",
}
