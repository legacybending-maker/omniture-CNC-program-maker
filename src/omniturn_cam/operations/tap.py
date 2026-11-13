"""Tap / solid die: Tap, solid die holder or acorn die in a floating holder."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .base import FieldInfo, Operation, OpSpec

if TYPE_CHECKING:
    from ..post.omniturn import OmniTurnPost
    from ..tools import Tool


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


# Labels and help for this operation's fields (shared fields: fields.FIELDS).
FIELDS = {
    "pitch": FieldInfo("Pitch / lead", "len", "Lead per revolution, 1/TPI (e.g. .05 for 20 TPI)"),
    "feed_ratio": FieldInfo(
        "Feed ratio",
        "text_num",
        "Programmed feed = pitch x ratio; the floating holder takes up the rest (manual: ~95%)",
    ),
    "reverse_dwell": FieldInfo("Reverse dwell", "sec", "Time for the spindle to reverse at the bottom"),
    "x": FieldInfo("Diameter", "dia", "0 for a tap on centre"),
    "z_start": FieldInfo("Start Z", "z", "Just in front of the work"),
    "z_end": FieldInfo("End Z", "z", "Stop a little short of full depth; the holder floats"),
}


def emit(p: OmniTurnPost, op: Tap) -> None:
    """Floating-holder tapping as shown in the manual (p2.58)."""
    p.call_tool(op, op.x, p.job.stock.diameter)
    p.rapid(x=op.x)
    p.rapid(z=op.z_start)
    feed = op.pitch * op.feed_ratio
    p.cut(z=op.z_end, feed=feed)
    p.emit("m04" if op.spindle == "m03" else "m03")
    p.dwell(op.reverse_dwell)
    p.cut(z=op.z_start, feed=feed)
    p.emit(op.spindle)


def summary(op: Tap) -> list[str]:
    return [f"X{op.x:g} Z{op.z_start:g}>{op.z_end:g}"]


def tool_defaults(tool: Tool, vals: dict) -> None:
    if tool.kind in ("tap", "die_solid") and "pitch" in tool.params:
        vals["pitch"] = tool.params["pitch"]


SPEC = OpSpec(
    kind="tap",
    cls=Tap,
    menu=("Tap / solid die", "Tap, solid die holder or acorn die in a floating holder"),
    order=90,
    emit=emit,
    tool_kinds=("tap", "die_solid"),
    fields=FIELDS,
    summary=summary,
    tool_defaults=tool_defaults,
)
