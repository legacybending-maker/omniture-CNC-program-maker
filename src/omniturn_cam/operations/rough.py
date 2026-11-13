"""Rough contour: G75/G78 roughing to a shape - the control works out the passes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from .base import FieldInfo, Operation, OpSpec

if TYPE_CHECKING:
    from ..post.omniturn import OmniTurnPost


@dataclass(kw_only=True)
class Rough(Operation):
    """Controller-side contour roughing: G75 (box contour) or G78 (contour-parallel).
    The profile is written once as a subroutine and called with P."""

    profile: str  # key into Job.profiles
    cycle: Literal["g75", "g78"] = "g75"
    depth: float = 0.02  # I word: max depth of cut per side (G75 only)
    u: float = 0.005  # U: stock left per side (G75) / over the whole contour (G78)
    feed: float = 0.003
    start_x: float | None = None  # cycle start point S (diameter)
    start_z: float | None = None
    label = "rough"


# Labels and help for this operation's fields (shared fields: fields.FIELDS).
FIELDS = {
    "cycle": FieldInfo(
        "Roughing cycle",
        "choice",
        "G75 box contour: passes parallel to Z.  G78: passes follow the contour",
        ("g75", "g78"),
        group="Shape",
    ),
}


def emit(p: OmniTurnPost, op: Rough) -> None:
    prof = p.job.profiles[op.profile]
    sx, sz = p.start_point(op, prof)
    p.check_quadrant(sx, sz, prof)
    uz = prof.undercut_z()
    if uz is not None:
        p.warn(
            f"T{op.t} {op.cycle.upper()}: shape {prof.name!r} turns back toward the face at Z{uz:g}; "
            "a roughing cycle working from the face can't cut that - check it on F4 Verify"
        )
    p.call_tool(op, sx, p.job.stock.diameter)
    p.rapid(x=sx)
    p.rapid(z=sz)
    p.css_on(op)
    p.set_feed(op.feed)
    sub = p.subs.setdefault(op.profile, p.next_sub())
    if op.cycle == "g75":
        p.emit(f"g75i{p.n(op.depth)}u{p.n(op.u)}f{p.f(op.feed)}p{sub}")
    else:
        p.emit(f"g78u{p.n(op.u)}f{p.f(op.feed)}p{sub}")
    # The cycle returns the tool to its start point.
    p.x, p.z, p.motion = sx, sz, None
    p.css_off(op)


def summary(op: Rough) -> list[str]:
    return [op.cycle.upper()]


SPEC = OpSpec(
    kind="rough",
    cls=Rough,
    menu=("Rough contour", "G75/G78 roughing to a shape - the control works out the passes"),
    order=30,
    emit=emit,
    tool_kinds=("turn", "bore"),
    fields=FIELDS,
    summary=summary,
)
