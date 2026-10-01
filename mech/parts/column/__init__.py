"""The central column internals (ours, build123d): a fixed V-slot 2020 column on the Gil plate, a lift
sled on V-wheels that climbs a fixed rack with its own servo and turns the neck from below with a
direct-drive pan servo (Jason Charlton's build), and the ring-drive brackets on the column. Every part is modelled in the body frame at
the rest pose (`_layout.py` holds the shared numbers); assemblies/column/assembly.py puts them
together with their hardware and mates.
"""

PARTS = {
    "parts.column.gil_plate": "Gil base plate (Ferreira), aluminium, inferred outline",
    "parts.column.foot_plate": "column foot plate, aluminium",
    "parts.column.top_plate": "column top plate, aluminium",
    "parts.column.base_ring": "shell support ring round the pedestal's foot (printed)",
    "parts.column.lift_rack": "the lift's rack, Mod 0.8, on the back-right post (aluminium, to spec)",
    "parts.column.lift_pinion": "the lift servo's brass servo gear, Mod 0.8 48T (to spec)",
    "parts.column.sled": "lift sled: grid plates, bottom plate (pan servo), top plate (neck bearings), lift-servo hanger",
    "parts.column.pan_coupler": "pan coupler: the pan servo's 1906 hub to the neck tube (aluminium, turned)",
    "parts.column.neck_hub": "neck bearing hub in the sled's bearings (aluminium, turned)",
    "parts.column.clockspring_case": "clock-spring cassette for the head's cables (printed)",
    "parts.column.ring_pinion": "ring-drive pinions (Anderson's teeth) on 1906 hubs (printed)",
    "parts.column.drive_bracket": "ring-drive brackets on the column's back posts (printed)",
    "parts.column.neck_tube": "the short neck, 26 x 1.5 aluminium, cross-drilled",
}
