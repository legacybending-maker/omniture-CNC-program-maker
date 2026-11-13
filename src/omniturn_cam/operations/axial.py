"""Axial pass: Box tool, releasing die head, reamer, burnishing - straight feed along Z."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from .base import FieldInfo, Operation, OpSpec

if TYPE_CHECKING:
    from ..post.omniturn import OmniTurnPost
    from ..tools import Tool


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


# Labels and help for this operation's fields (shared fields: fields.FIELDS).
FIELDS = {
    "sync": FieldInfo("Spindle sync (P)", "bool", "Start the feed in sync with the spindle: die heads, single-point"),
    "reverse_spindle_out": FieldInfo("Reverse spindle to back out", "bool", "Non-opening dies and taps"),
    "stop_after": FieldInfo(
        "Stop after",
        "choice",
        "m01 optional stop (only stops if the operator has optional stop ON) or m00 program stop (always "
        "stops), e.g. to reset a die head",
        ("none", "m01", "m00"),
    ),
    "stop_spindle_for_stop": FieldInfo(
        "Stop spindle for the stop",
        "bool",
        "m05 before the stop so the operator isn't reaching past a turning spindle; the next cut restarts it",
    ),
}


def emit(p: OmniTurnPost, op: AxialPass) -> None:
    p.call_tool(op, op.x, max(op.x, 1e-3) if op.x else p.job.stock.diameter)
    p.rapid(x=op.x)
    p.rapid(z=op.z_start)
    p.cut(z=op.z_end, feed=op.feed, extra="p" if op.sync else "")
    p.dwell(op.dwell)
    if op.reverse_spindle_out:
        p.emit("m04" if op.spindle == "m03" else "m03")
    if op.retract == "feed":
        p.cut(z=op.z_start, feed=op.retract_feed or op.feed)
    else:
        p.rapid(z=op.z_start)
    if op.reverse_spindle_out:
        p.emit(op.spindle)
    if op.stop_after != "none":
        p.rapid(z=p.m.tool_call_z)
        if op.stop_spindle_for_stop:
            p.emit("m05")
            p.spindle = None  # next cut restarts it
        p.emit(op.stop_after)


def summary(op: AxialPass) -> list[str]:
    return [f"X{op.x:g} Z{op.z_start:g}>{op.z_end:g}"]


def tool_defaults(tool: Tool, vals: dict) -> None:
    if tool.kind == "die_head":
        # releasing die head: feed to the trip point, dwell, rapid out, stop to reset
        vals.update(
            sync=True,
            feed=tool.params.get("pitch", vals.get("feed")),
            retract="rapid",
            dwell=0.2,
            stop_after="m01",
            comment="Reset die head",
        )


SPEC = OpSpec(
    kind="axial",
    cls=AxialPass,
    menu=("Axial pass", "Box tool, releasing die head, reamer, burnishing - straight feed along Z"),
    order=100,
    emit=emit,
    tool_kinds=("box_tool", "die_head", "reamer", "drill"),
    fields=FIELDS,
    summary=summary,
    tool_defaults=tool_defaults,
)
