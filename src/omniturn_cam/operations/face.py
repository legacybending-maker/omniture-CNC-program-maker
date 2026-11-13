"""Face: Face the end of the part."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .base import FieldInfo, Operation, OpSpec

if TYPE_CHECKING:
    from ..post.omniturn import OmniTurnPost


@dataclass(kw_only=True)
class Face(Operation):
    """Face across the end of the part, optionally in several passes."""

    z: float = 0.0  # finished face position
    stock: float = 0.0  # material on the face to remove
    depth_per_pass: float | None = None
    x_start: float | None = None  # default: stock diameter + clearance
    x_end: float | None = None  # default: just past centre
    feed: float = 0.003
    label = "face"


# Labels and help for this operation's fields (shared fields: fields.FIELDS).
FIELDS = {
    "stock": FieldInfo("Face stock", "len", "Material left on the face to remove"),
    "depth_per_pass": FieldInfo("Depth per pass", "len", "Blank = one pass", optional=True),
}


def emit(p: OmniTurnPost, op: Face) -> None:
    st = p.station(op)
    xs = op.x_start if op.x_start is not None else p.stock_x
    nr = st.tool.nose_radius
    xe = op.x_end if op.x_end is not None else -max(2 * nr, 0.005 if p.job.units == "inch" else 0.1)
    p.call_tool(op, xs, p.job.stock.diameter)
    passes = [op.z]
    if op.stock > 0:
        dpp = op.depth_per_pass or op.stock
        k = max(1, math.ceil(op.stock / dpp - 1e-9))
        passes = [op.z + op.stock - (i + 1) * op.stock / k for i in range(k)]
    p.rapid(x=xs)
    p.rapid(z=passes[0] + p.m.z_clear)
    p.css_on(op)
    for zp in passes:
        p.rapid(x=xs)
        p.rapid(z=zp)
        p.cut(x=xe, feed=op.feed)
        p.rapid(z=zp + p.m.z_clear)
    p.rapid(x=xs)
    p.css_off(op)


SPEC = OpSpec(
    kind="face",
    cls=Face,
    menu=("Face", "Face the end of the part"),
    order=10,
    emit=emit,
    tool_kinds=("turn", "face"),
    fields=FIELDS,
)
