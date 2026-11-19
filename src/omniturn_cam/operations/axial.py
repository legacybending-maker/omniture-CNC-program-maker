"""Axial pass: Box tool, releasing die head, reamer, burnishing - straight feed along Z."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from .base import FieldInfo, Operation, OpSpec

if TYPE_CHECKING:
    from ..post.omniturn import OmniTurnPost
    from ..tools import Tool
    from ..validation import CheckContext, Issue


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
    # m09 before the stop. M00 "does not cancel active M functions" (manual
    # p2.53, Codes Honored list), so without it coolant keeps running while
    # the operator reaches in to reset a die head. A new optional field with a
    # safe default, so job files saved before Stage 5 still load and get it.
    stop_coolant_for_stop: bool = True
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
    "stop_coolant_for_stop": FieldInfo(
        "Coolant off for the stop",
        "bool",
        "m09 before the stop: M00 and M01 don't cancel M functions (p2.53), so coolant would keep running. "
        "The next process that uses coolant turns it back on with m08",
    ),
}


def validate(op: AxialPass, ctx: CheckContext) -> Iterator[Issue]:
    """Layer 2. Only "the feed has to go somewhere": box tools and reamers feed
    toward the chuck, but a back-facing or pull-cut tool feeds the other way,
    so the direction is the operator's call."""
    if op.z_end == op.z_start:
        yield ctx.error("z_end", "is the same as the start Z: the tool wouldn't move")


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
            p.stop_spindle()  # recorded as M05, so the next cut restarts it
        if op.stop_coolant_for_stop:
            # Recorded as M09 (stop_coolant), so the next operation that wants
            # coolant writes M08 again. One M code per line (p2.6), so it's
            # its own line, after the M05 and before the stop.
            p.stop_coolant()
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
    validate=validate,
)
