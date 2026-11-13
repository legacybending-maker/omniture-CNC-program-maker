"""Box rough (G74): Remove a rectangle of material in passes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .base import FieldInfo, Operation, OpSpec

if TYPE_CHECKING:
    from ..post.omniturn import OmniTurnPost


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


def emit(p: OmniTurnPost, op: BoxRough) -> None:
    """G74: the control removes the rectangle between the start point and X/Z (p2.39)."""
    sx = op.start_x if op.start_x is not None else p.stock_x
    sz = op.start_z if op.start_z is not None else p.m.z_clear
    p.call_tool(op, sx, p.job.stock.diameter)
    p.rapid(x=sx)
    p.rapid(z=sz)
    p.css_on(op)
    p.set_feed(op.feed)
    # U is ignored by G74 but the control still requires the word.
    p.emit(f"g74x{p.n(op.x)}z{p.n(op.z)}i{p.n(op.depth)}u{p.n(op.u)}f{p.f(op.feed)}")
    p.x, p.z, p.motion = sx, sz, None
    p.css_off(op)


SPEC = OpSpec(
    kind="box_rough",
    cls=BoxRough,
    menu=("Box rough (G74)", "Remove a rectangle of material in passes"),
    order=20,
    emit=emit,
    tool_kinds=("turn", "bore"),
    fields=FIELDS,
)
