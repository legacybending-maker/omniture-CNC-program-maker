"""Machining operations - the 'fill in the blank' screens of the conversational
front end map one-to-one onto these dataclasses.

All lengths are in the job's units (inch or mm). X values are diameters.
Feeds are per revolution (IPR / mm per rev) unless noted.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .geometry import Profile


@dataclass(kw_only=True)
class Operation:
    t: int                              # OmniTurn station number
    rpm: float | None = None            # G97 spindle speed
    sfm: float | None = None            # G96 constant surface speed (sfm or m/min)
    min_rpm: float | None = None        # G76 with CSS
    max_rpm: float | None = None        # G77 with CSS
    spindle: Literal["m03", "m04"] = "m03"
    coolant: bool = True
    comment: str = ""

    label = "operation"


@dataclass(kw_only=True)
class Face(Operation):
    """Face across the end of the part, optionally in several passes."""
    z: float = 0.0                      # finished face position
    stock: float = 0.0                  # material on the face to remove
    depth_per_pass: float | None = None
    x_start: float | None = None        # default: stock diameter + clearance
    x_end: float | None = None          # default: just past centre
    feed: float = 0.003
    label = "face"


@dataclass(kw_only=True)
class Rough(Operation):
    """Controller-side contour roughing: G75 (box contour) or G78 (contour-parallel).
    The profile is written once as a subroutine and called with P."""
    profile: str                        # key into Job.profiles
    cycle: Literal["g75", "g78"] = "g75"
    depth: float = 0.02                 # I word: max depth of cut per side (G75 only)
    u: float = 0.005                    # U word - see docs/omniturn-post-rules.md (VERIFY)
    feed: float = 0.003
    start_x: float | None = None        # cycle start point S (diameter)
    start_z: float | None = None
    label = "rough"


@dataclass(kw_only=True)
class Finish(Operation):
    """Finish pass along the profile, inline (not a subroutine) so TNR comp works."""
    profile: str
    comp: Literal["auto", "g41", "g42", "none"] = "auto"
    feed: float = 0.002
    start_x: float | None = None
    start_z: float | None = None
    label = "finish"


@dataclass(kw_only=True)
class Drill(Operation):
    """G81 drill, or G83 peck drill when peck is given. Drills on centre."""
    z: float                            # depth (absolute Z of the drill point)
    feed: float = 0.003
    start_z: float | None = None        # default: clearance in front of the face
    peck: float | None = None           # K word
    retract_z: float | None = None      # R word (G83): rapid-out plane
    peck_clearance: float = 0.02        # C word (G83): stop short of last depth on return
    peck_rapid: float | None = None     # L word (G83): retract feed, default = rapid
    label = "drill"


@dataclass(kw_only=True)
class AxialPass(Operation):
    """A straight feed along Z at a fixed diameter: box tools, die heads, reamers,
    roller burnishing, axial knurling. Retract behaviour is per tool type."""
    x: float                            # diameter the tool runs at (0 for on-centre tools)
    z_start: float
    z_end: float
    feed: float
    sync: bool = False                  # append P: start in sync with spindle (die heads)
    dwell: float = 0.0                  # seconds at the bottom (min .1)
    retract: Literal["rapid", "feed"] = "rapid"
    retract_feed: float | None = None
    reverse_spindle_out: bool = False   # e.g. non-opening dies/taps: m04 on the way out
    label = "axial"


@dataclass(kw_only=True)
class RadialPass(Operation):
    """A straight feed along X at a fixed Z: grooving, form tools, straddle knurls,
    cutoff."""
    z: float
    x_start: float
    x_end: float
    feed: float
    dwell: float = 0.0
    retract: Literal["rapid", "feed"] = "rapid"
    retract_feed: float | None = None
    label = "radial"


@dataclass(kw_only=True)
class Cutoff(RadialPass):
    x_start: float | None = None        # default: stock diameter + clearance
    x_end: float = -0.010               # just past centre
    stop_spindle_after: bool = False
    label = "cutoff"


OPERATION_TYPES = {
    "face": Face, "rough": Rough, "finish": Finish, "drill": Drill,
    "axial": AxialPass, "radial": RadialPass, "cutoff": Cutoff,
}
