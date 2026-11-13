"""Radial pass: Groove, form tool, straddle knurl - straight feed along X."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from .base import FieldInfo, Operation, OpSpec

if TYPE_CHECKING:
    from ..post.omniturn import OmniTurnPost


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


# Labels and help for this operation's fields (shared fields: fields.FIELDS).
FIELDS = {
    "z": FieldInfo("Z position", "z", "Z of the tool's reference edge"),
    "x_start": FieldInfo("Start diameter", "dia", "Approach diameter"),
    "x_end": FieldInfo("End diameter", "dia", "Diameter the tool feeds to"),
}


def emit(p: OmniTurnPost, op: RadialPass) -> None:
    xs = op.x_start if op.x_start is not None else p.stock_x
    p.call_tool(op, xs, p.job.stock.diameter)
    p.rapid(x=xs)
    p.rapid(z=op.z)
    p.css_on(op)
    p.cut(x=op.x_end, feed=op.feed)
    p.dwell(op.dwell)
    if op.retract == "feed":
        p.cut(x=xs, feed=op.retract_feed or op.feed)
    else:
        p.rapid(x=xs)
    p.css_off(op)


def summary(op: RadialPass) -> list[str]:
    return [f"Z{op.z:g}"]


SPEC = OpSpec(
    kind="radial",
    cls=RadialPass,
    menu=("Radial pass", "Groove, form tool, straddle knurl - straight feed along X"),
    order=110,
    emit=emit,
    tool_kinds=("knurl_straddle", "knurl_bump", "groove", "form"),
    fields=FIELDS,
    summary=summary,
)
