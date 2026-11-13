"""Drill / peck drill: G81 or G83 on centre."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..gcode import fmt
from .base import FieldInfo, Operation, OpSpec

if TYPE_CHECKING:
    from ..post.omniturn import OmniTurnPost


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


# Labels and help for this operation's fields (shared fields: fields.FIELDS).
FIELDS = {
    "peck": FieldInfo(
        "Peck depth (K)", "len", "Blank = straight G81 drill. Set for G83 peck", optional=True, blank="no peck"
    ),
    "retract_z": FieldInfo("Peck retract Z (R)", "z", "Where the drill rapids out to clear chips", optional=True),
    "peck_clearance": FieldInfo("Peck return gap (C)", "len", "Stops this far short of the last depth on re-entry"),
    "peck_rapid": FieldInfo("Peck retract feed (L)", "feed_ipm", "Blank = rapid", optional=True),
    "z": FieldInfo("Drill depth Z", "z", "Absolute Z of the drill point at full depth"),
    "start_z": FieldInfo("Start Z", "z", "Blank = clearance in front of the face", optional=True),
}


def emit(p: OmniTurnPost, op: Drill) -> None:
    sz = op.start_z if op.start_z is not None else p.m.z_clear
    p.call_tool(op, 0.0, p.station(op).tool.diameter or p.job.stock.diameter)
    p.rapid(x=0.0)
    p.rapid(z=sz)
    if p.feed_mode != "g95":
        p.emit("g95")
        p.feed_mode = "g95"
    if op.peck:
        r = op.retract_z if op.retract_z is not None else sz
        l = op.peck_rapid or p.m.rapid_feed
        p.emit(f"g83z{p.n(op.z)}k{p.n(op.peck)}f{p.f(op.feed)}r{p.n(r)}c{p.n(op.peck_clearance)}l{fmt(l, 1)}")
    else:
        p.emit(f"g81z{p.n(op.z)}f{p.f(op.feed)}")
    p.feed = op.feed
    p.x, p.z, p.motion = 0.0, sz, None


def summary(op: Drill) -> list[str]:
    return [f"Z{op.z:g}" + (" peck" if op.peck else "")]


SPEC = OpSpec(
    kind="drill",
    cls=Drill,
    menu=("Drill / peck drill", "G81 or G83 on centre"),
    order=50,
    emit=emit,
    tool_kinds=("center_drill", "spot_drill", "drill"),
    fields=FIELDS,
    summary=summary,
)
