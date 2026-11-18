"""Box rough (G74): Remove a rectangle of material in passes."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .base import FieldInfo, Operation, OpSpec

if TYPE_CHECKING:
    from ..post.omniturn import MachineConfig, OmniTurnPost
    from ..validation import CheckContext, Issue


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


# Labels and help for this operation's fields (shared fields: fields.FIELDS).
FIELDS = {
    "x": FieldInfo("Box corner X", "dia", "Diameter to rough down to (leave finish stock here)"),
    "z": FieldInfo("Box corner Z", "z", "Shoulder Z to rough to (leave finish stock here)"),
    "depth": FieldInfo("Depth of cut (I)", "len", "Per side, per pass"),
    "u": FieldInfo("U (ignored)", "len", "G74 ignores U but the control requires the word"),
}


def _start(op: BoxRough, stock_x: float, z_clear: float) -> tuple[float, float]:
    """The cycle start point: the one given, or the automatic one. One
    function for emit() and validate_machine(), so the check can't drift
    from the start point the program really uses."""
    sx = op.start_x if op.start_x is not None else stock_x
    sz = op.start_z if op.start_z is not None else z_clear
    return sx, sz


def validate(op: BoxRough, ctx: CheckContext) -> Iterator[Issue]:
    """Layer 2. G74 removes the rectangle between its start point and X/Z
    (p2.38-2.39), so a start level with the corner in X or Z leaves no box."""
    if op.depth <= 0:
        yield ctx.error("depth", "must be greater than zero (G74 I is the depth per pass, p2.39)")
    if op.start_x is not None and op.start_x == op.x:
        yield ctx.error("x", "is the same as the cycle start X: there is no box to remove")
    if op.start_z is not None and op.start_z == op.z:
        yield ctx.error("z", "is the same as the cycle start Z: there is no box to remove")


def validate_machine(op: BoxRough, ctx: CheckContext, m: MachineConfig) -> Iterator[Issue]:
    """Layer 3: with the start left blank it comes from machine.toml clearances."""
    sx, sz = _start(op, ctx.job.stock.diameter + 2 * m.x_clear, m.z_clear)
    if op.start_x is None and sx == op.x:
        yield ctx.error("x", f"is the same as the automatic start X {sx:g}: there is no box to remove")
    if op.start_z is None and sz == op.z:
        yield ctx.error("z", f"is the same as the automatic start Z {sz:g}: there is no box to remove")


def emit(p: OmniTurnPost, op: BoxRough) -> None:
    """G74: the control removes the rectangle between the start point and X/Z (p2.39)."""
    sx, sz = _start(op, p.stock_x, p.m.z_clear)
    p.call_tool(op, sx, p.job.stock.diameter)
    p.rapid(x=sx)
    p.rapid(z=sz)
    p.css_on(op)
    p.set_feed(op.feed)
    # U is ignored by G74 but the control still requires the word.
    p.emit(f"g74x{p.n(op.x)}z{p.n(op.z)}i{p.n(op.depth)}u{p.n(op.u)}f{p.f(op.feed)}")
    p.cycle_ended_at(sx, sz)  # G74 returns to its start point (p2.39)
    p.css_off(op)


SPEC = OpSpec(
    kind="box_rough",
    cls=BoxRough,
    menu=("Box rough (G74)", "Remove a rectangle of material in passes"),
    order=20,
    emit=emit,
    tool_kinds=("turn", "bore"),
    fields=FIELDS,
    metric_defaults={"depth": 0.5, "u": 0.25, "feed": 0.08},
    validate=validate,
    validate_machine=validate_machine,
)
