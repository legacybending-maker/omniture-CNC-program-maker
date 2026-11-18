"""Drill / peck drill: G81 or G83 on centre."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..gcode import fmt
from .base import FieldInfo, Operation, OpSpec

if TYPE_CHECKING:
    from ..post.omniturn import MachineConfig, OmniTurnPost
    from ..validation import CheckContext, Issue


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
    "retract_z": FieldInfo(
        "Peck retract Z (R)",
        "z",
        "Where the drill rapids out to clear chips. Blank = the start Z",
        optional=True,
        blank="start Z",
    ),
    "peck_clearance": FieldInfo("Peck return gap (C)", "len", "Stops this far short of the last depth on re-entry"),
    "peck_rapid": FieldInfo("Peck retract feed (L)", "feed_ipm", "Blank = rapid", optional=True),
    "z": FieldInfo("Drill depth Z", "z", "Absolute Z of the drill point at full depth"),
    "start_z": FieldInfo("Start Z", "z", "Blank = clearance in front of the face", optional=True),
}


def validate(op: Drill, ctx: CheckContext) -> Iterator[Issue]:
    """Layer 2. G81 and G83 feed from the start toward the chuck (-Z) and
    rapid back to the start (p2.48-2.50), so the depth must be below the start
    and the peck retract plane above the depth. This is the drill's own
    direction rule, not a rule for every operation."""
    if op.peck is not None and op.peck <= 0:
        # emit() tests `if op.peck:`, so 0 used to turn a peck drill into a
        # straight G81 without a word. Blank is how to ask for that.
        yield ctx.error("peck", "must be greater than zero", "Leave it blank for a straight G81 drill")
    if op.peck_clearance < 0:
        # C is how far short of the last depth the drill stops on the way
        # back in; negative would rapid into metal it hasn't cut yet.
        yield ctx.error("peck_clearance", "can't be negative: the drill would rapid into uncut metal (p2.49)")
    if op.start_z is not None and op.z >= op.start_z:
        yield ctx.error(
            "z",
            f"must be below the start Z {op.start_z:g}: the drill feeds toward the chuck (-Z) from its start (p2.48)",
        )
    if op.retract_z is not None and op.retract_z <= op.z:
        yield ctx.error("retract_z", f"must be above the drill depth Z {op.z:g}: G83 rapids out to it (p2.49)")


def validate_machine(op: Drill, ctx: CheckContext, m: MachineConfig) -> Iterator[Issue]:
    """Layer 3: a blank start Z is the clearance from machine.toml."""
    if op.start_z is None and op.z >= m.z_clear:
        yield ctx.error(
            "z",
            f"must be below the automatic start Z {m.z_clear:g} (z_clear in machine.toml): the drill feeds "
            "toward the chuck (p2.48)",
        )


def emit(p: OmniTurnPost, op: Drill) -> None:
    sz = op.start_z if op.start_z is not None else p.m.z_clear
    p.call_tool(op, 0.0, p.station(op).tool.diameter or p.job.stock.diameter)
    p.rapid(x=0.0)
    p.rapid(z=sz)
    # The cycle's F is per rev only in G95 (p2.11). set_feed_mode writes g95
    # unless the post *knows* it's already in effect; after raw code that may
    # have switched to G94 it doesn't know, so g95 is written again.
    p.set_feed_mode("g95")
    if op.peck:
        r = op.retract_z if op.retract_z is not None else sz
        l = op.peck_rapid or p.m.rapid_feed
        p.emit(f"g83z{p.n(op.z)}k{p.n(op.peck)}f{p.f(op.feed)}r{p.n(r)}c{p.n(op.peck_clearance)}l{fmt(l, 1)}")
    else:
        p.emit(f"g81z{p.n(op.z)}f{p.f(op.feed)}")
    p.state.feed = op.feed  # the cycle's F word is now the modal feed
    p.cycle_ended_at(0.0, sz)  # G81/G83 rapid back to the start (p2.48-2.50)


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
    metric_defaults={"feed": 0.08, "peck_clearance": 0.5},
    validate=validate,
    validate_machine=validate_machine,
)
