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
    t: int  # OmniTurn station number
    rpm: float | None = None  # G97 spindle speed
    sfm: float | None = None  # G96 constant surface speed (sfm or m/min)
    min_rpm: float | None = None  # G76 with CSS
    max_rpm: float | None = None  # G77 with CSS
    spindle: Literal["m03", "m04"] = "m03"
    coolant: bool = True
    comment: str = ""

    label = "operation"
    uses_tool = True


@dataclass(kw_only=True)
class Face(Operation):
    """Face across the end of the part, optionally in several passes."""

    z: float = 0.0  # finished face position
    stock: float = 0.0  # material on the face to remove
    depth_per_pass: float | None = None
    x_start: float | None = None  # default: stock diameter + clearance
    x_end: float | None = None  # default: just past centre
    feed: float = 0.003
    label = "face"


@dataclass(kw_only=True)
class Rough(Operation):
    """Controller-side contour roughing: G75 (box contour) or G78 (contour-parallel).
    The profile is written once as a subroutine and called with P."""

    profile: str  # key into Job.profiles
    cycle: Literal["g75", "g78"] = "g75"
    depth: float = 0.02  # I word: max depth of cut per side (G75 only)
    u: float = 0.005  # U: stock left per side (G75) / over the whole contour (G78)
    feed: float = 0.003
    start_x: float | None = None  # cycle start point S (diameter)
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

    z: float  # depth (absolute Z of the drill point)
    feed: float = 0.003
    start_z: float | None = None  # default: clearance in front of the face
    peck: float | None = None  # K word
    retract_z: float | None = None  # R word (G83): rapid-out plane
    peck_clearance: float = 0.02  # C word (G83): stop short of last depth on return
    peck_rapid: float | None = None  # L word (G83): retract feed, default = rapid
    label = "drill"


@dataclass(kw_only=True)
class AxialPass(Operation):
    """A straight feed along Z at a fixed diameter: box tools, die heads, reamers,
    roller burnishing, axial knurling. Retract behaviour is per tool type."""

    x: float  # diameter the tool runs at (0 for on-centre tools)
    z_start: float
    z_end: float
    feed: float
    sync: bool = False  # append P: start in sync with spindle (die heads)
    dwell: float = 0.0  # seconds at the bottom (min .1)
    retract: Literal["rapid", "feed"] = "rapid"
    retract_feed: float | None = None
    reverse_spindle_out: bool = False  # e.g. non-opening dies/taps: m04 on the way out
    stop_after: Literal["none", "m01", "m00"] = "none"  # e.g. operator resets a die head
    stop_spindle_for_stop: bool = True  # m05 before the stop so hands aren't near a turning spindle
    label = "axial"


@dataclass(kw_only=True)
class Tap(Operation):
    """Taps, solid die holders and acorn dies in a floating / self-releasing holder.
    Programmed the way the manual shows (p2.58): feed in a little under pitch,
    reverse the spindle, dwell while it reverses, feed back out."""

    x: float = 0.0
    z_start: float = 0.1
    z_end: float = -0.4  # stop short of full depth; the holder floats the rest
    pitch: float = 0.05  # lead per rev (1/TPI)
    feed_ratio: float = 0.95  # program feed = pitch x ratio, holder takes up the rest
    reverse_dwell: float = 0.5  # seconds for the spindle to reverse
    label = "tap"


@dataclass(kw_only=True)
class Thread(Operation):
    """G33 single-point threading cycle (manual p2.23-2.29). The control works out
    the passes, keeping the material removed per pass constant."""

    side: Literal["od", "id"] = "od"
    major: float = 0.5  # major diameter (OD: what you start from)
    minor: float = 0.4084  # minor diameter (OD: the X word, the finished depth)
    z_start: float = 0.0  # where the thread starts on the part
    z_end: float = -0.5  # Z word: end of the thread
    lead: float = 0.05  # K for a single start = pitch
    starts: int = 1  # multi-start: K = lead x starts, Z offset pitch per start
    first_depth: float = 0.02  # I word: first pass, 2x depth of cut in diameter mode
    runup: float = 0.2  # start this far in front of the thread to reach speed
    infeed_angle: float | None = None  # C: None = straight in, 29 = default angle infeed
    pullout: bool = False  # P: keep moving forward while pulling out (no undercut)
    taper_a: float | None = None  # A: X travel over the full Z travel incl. run-up
    spring_passes: int = 0  # extra single passes at finished depth (O)
    start_x: float | None = None  # override the computed start diameter
    lock_overrides: bool = True  # M84 before / M85 after: spindle override can't change rpm mid-thread
    label = "thread"


@dataclass(kw_only=True)
class BoxRough(Operation):
    """G74 box roughing: removes the rectangle between the start point and X/Z."""

    x: float  # finished diameter of the box (leave finish stock here)
    z: float  # finished Z of the shoulder (leave finish stock here)
    depth: float = 0.02  # I: depth per pass, per side
    u: float = 0.01  # ignored by the control but must be present
    feed: float = 0.003
    start_x: float | None = None
    start_z: float | None = None
    label = "box rough"


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
    x_start: float | None = None  # default: stock diameter + clearance
    x_end: float = -0.010  # just past centre
    stop_spindle_after: bool = False
    label = "cutoff"


@dataclass(kw_only=True)
class CallSub(Operation):
    """M98 call to a subroutine - e.g. a ZipLoader / bar-load routine from the
    job's external subroutine file."""

    t: int | None = None
    sub: int = 1
    label = "call sub"
    uses_tool = False


@dataclass(kw_only=True)
class RawCode(Operation):
    """Lines passed straight into the program (loader M-codes, M91-M94 waits,
    LS/LF loops...). Still checked by the rules linter."""

    t: int | None = None
    code: str = ""
    label = "raw code"
    uses_tool = False


OPERATION_TYPES = {
    "face": Face,
    "box_rough": BoxRough,
    "rough": Rough,
    "finish": Finish,
    "drill": Drill,
    "thread": Thread,
    "tap": Tap,
    "axial": AxialPass,
    "radial": RadialPass,
    "cutoff": Cutoff,
    "call_sub": CallSub,
    "raw": RawCode,
}
