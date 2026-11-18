"""Cutoff: Part off."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .base import FieldInfo, OpSpec
from .radial import RadialPass
from .radial import emit as emit_radial

if TYPE_CHECKING:
    from ..post.omniturn import MachineConfig, OmniTurnPost
    from ..validation import CheckContext, Issue


@dataclass(kw_only=True)
class Cutoff(RadialPass):
    """Part off: a radial pass to just past centre, optionally stopping the
    spindle once the part is off."""

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


def validate(op: Cutoff, ctx: CheckContext) -> Iterator[Issue]:
    """Layer 2. A cutoff blade only feeds in toward the centre."""
    if op.x_start is not None and op.x_end >= op.x_start:
        yield ctx.error(
            "x_end", f"must be below the start diameter {op.x_start:g}: a cutoff feeds in toward the centre"
        )
    elif op.x_end > 0:
        # Legal, and sometimes meant (leave a pip to break off), so a warning.
        yield ctx.warning("x_end", "is above centre, so the part won't come off", "Use just past centre, e.g. -.01")


def validate_machine(op: Cutoff, ctx: CheckContext, m: MachineConfig) -> Iterator[Issue]:
    """Layer 3: a blank start diameter is the bar plus the machine's X clearance."""
    sx = ctx.job.stock.diameter + 2 * m.x_clear
    if op.x_start is None and op.x_end >= sx:
        yield ctx.error("x_end", f"must be below the automatic start diameter {sx:g} (bar plus x_clear)")


def emit(p: OmniTurnPost, op: Cutoff) -> None:
    emit_radial(p, op)
    if op.stop_spindle_after:
        p.stop_spindle()


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
    metric_defaults={"x_end": -0.25},
    validate=validate,
    validate_machine=validate_machine,
)
