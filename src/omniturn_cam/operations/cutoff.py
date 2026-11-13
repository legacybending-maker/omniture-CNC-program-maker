"""Cutoff: Part off."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .base import FieldInfo, OpSpec
from .radial import RadialPass
from .radial import emit as emit_radial

if TYPE_CHECKING:
    from ..post.omniturn import OmniTurnPost


@dataclass(kw_only=True)
class Cutoff(RadialPass):
    x_start: float | None = None  # default: stock diameter + clearance
    x_end: float = -0.010  # just past centre
    stop_spindle_after: bool = False
    label = "cutoff"


# Labels and help for this operation's fields (shared fields: fields.FIELDS).
FIELDS = {
    "stop_spindle_after": FieldInfo("Stop spindle after", "bool", "m05 once the part is off"),
    "z": FieldInfo("Cutoff Z", "z", "Z of the blade's reference edge"),
    "x_end": FieldInfo("End diameter", "dia", "Just past centre, e.g. -.01"),
}


def emit(p: OmniTurnPost, op: Cutoff) -> None:
    emit_radial(p, op)
    if op.stop_spindle_after:
        p.emit("m05")
        p.spindle = None


def summary(op: Cutoff) -> list[str]:
    return [f"Z{op.z:g}"]


SPEC = OpSpec(
    kind="cutoff",
    cls=Cutoff,
    menu=("Cutoff", "Part off"),
    order=120,
    emit=emit,
    tool_kinds=("cutoff",),
    fields=FIELDS,
    summary=summary,
)
