"""Finish contour: One pass along the shape with nose radius comp."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from ..gcode import PostError
from .base import FieldInfo, Operation, OpSpec

if TYPE_CHECKING:
    from ..post.omniturn import OmniTurnPost
    from ..validation import CheckContext, Issue


@dataclass(kw_only=True)
class Finish(Operation):
    """Finish pass along the profile, inline (not a subroutine) so TNR comp works."""

    profile: str
    comp: Literal["auto", "g41", "g42", "none"] = "auto"
    feed: float = 0.002
    start_x: float | None = None
    start_z: float | None = None
    label = "finish"


# Labels and help for this operation's fields (shared fields: fields.FIELDS).
FIELDS = {
    "comp": FieldInfo(
        "Nose radius comp",
        "choice",
        "auto picks G41/G42 from the cut direction; needs a D number on the station",
        ("auto", "g41", "g42", "none"),
        group="Shape",
    ),
}


def resolve_comp(op: Finish, prof, nose_radius: float) -> str:
    """G41/G42/none for this pass: the operator's choice, or for "auto" the
    side the tool is on relative to the direction of cut (p2.31-2.36)."""
    if op.comp != "auto":
        return op.comp
    if nose_radius <= 0:
        return "none"
    toward_chuck = prof.end.z < prof.start.z
    if prof.side == "od":
        return "g41" if toward_chuck else "g42"
    return "g42" if toward_chuck else "g41"


def validate(op: Finish, ctx: CheckContext) -> Iterator[Issue]:
    """Layer 2: a shape to follow, and a D number when comp is on. The D check
    was a PostError in emit(); it's a setup problem, so it's found here now."""
    prof = ctx.job.profiles[op.profile]
    if not prof.segments:
        yield ctx.error("profile", "has no segments yet", "Draw the shape on F2 Part shape")
        return  # `return` in a generator just ends it: nothing more to check
    if ctx.station is not None:
        comp = resolve_comp(op, prof, ctx.station.tool.nose_radius)
        if comp != "none" and ctx.station.d is None:
            yield ctx.error(
                "comp",
                f"is {comp}, which needs a D number on station t{op.t} (the secondary offset holding the "
                "nose radius, p2.35)",
                "Give the station a D number on F3 Tool plate, or set comp to none",
            )


def emit(p: OmniTurnPost, op: Finish) -> None:
    prof = p.job.profiles[op.profile]
    st = p.station(op)
    sx, sz = p.start_point(op, prof)
    comp = resolve_comp(op, prof, st.tool.nose_radius)
    p.call_tool(op, sx, p.job.stock.diameter)
    p.rapid(x=sx)
    p.rapid(z=sz)
    p.rapid(x=prof.start.x)
    p.css_on(op)
    if comp != "none":
        # set_comp records it, so the state knows comp is on until the G40
        # below; nothing else may turn it on or off behind the post's back.
        p.set_comp(comp)
    dword = f"d{st.d}" if comp != "none" else ""
    if abs(prof.start.z - sz) < 1e-9 and abs(prof.start.x - p.state.x) < 1e-9:
        raise PostError("finish start point equals approach point; nothing to turn comp on with")
    p.cut(prof.start.x, prof.start.z, op.feed, dword)
    p.contour(prof, op.feed)
    # Lead off the part by more than twice the nose radius before G40 (p2.32).
    lead = 2 * max(3 * st.tool.nose_radius, 0.01 if p.job.units == "inch" else 0.25)
    p.cut(x=prof.end.x + (lead if prof.side == "od" else -lead), feed=op.feed)
    p.rapid(z=sz)
    if comp != "none":
        p.set_comp("g40")
    p.css_off(op)


SPEC = OpSpec(
    kind="finish",
    cls=Finish,
    menu=("Finish contour", "One pass along the shape with nose radius comp"),
    order=40,
    emit=emit,
    tool_kinds=("turn", "bore"),
    fields=FIELDS,
    metric_defaults={"feed": 0.05},
    validate=validate,
)
