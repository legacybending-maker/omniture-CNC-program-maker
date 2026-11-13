"""Finish contour: One pass along the shape with nose radius comp."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from ..gcode import PostError
from .base import FieldInfo, Operation, OpSpec

if TYPE_CHECKING:
    from ..post.omniturn import OmniTurnPost


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


def emit(p: OmniTurnPost, op: Finish) -> None:
    prof = p.job.profiles[op.profile]
    st = p.station(op)
    sx, sz = p.start_point(op, prof)
    comp = op.comp
    toward_chuck = prof.end.z < prof.start.z
    if comp == "auto":
        if st.tool.nose_radius <= 0:
            comp = "none"
        elif prof.side == "od":
            comp = "g41" if toward_chuck else "g42"
        else:
            comp = "g42" if toward_chuck else "g41"
    if comp != "none" and st.d is None:
        raise PostError(
            f"t{op.t} finish with {comp}: give this station a d number (secondary offset holding the nose radius)"
        )
    p.call_tool(op, sx, p.job.stock.diameter)
    p.rapid(x=sx)
    p.rapid(z=sz)
    p.rapid(x=prof.start.x)
    p.css_on(op)
    if comp != "none":
        p.emit(comp)
    dword = f"d{st.d}" if comp != "none" else ""
    if abs(prof.start.z - sz) < 1e-9 and abs(prof.start.x - p.x) < 1e-9:
        raise PostError("finish start point equals approach point; nothing to turn comp on with")
    p.cut(prof.start.x, prof.start.z, op.feed, dword)
    p.contour(prof, op.feed)
    # Lead off the part by more than twice the nose radius before G40 (p2.32).
    lead = 2 * max(3 * st.tool.nose_radius, 0.01 if p.job.units == "inch" else 0.25)
    p.cut(x=prof.end.x + (lead if prof.side == "od" else -lead), feed=op.feed)
    p.rapid(z=sz)
    if comp != "none":
        p.emit("g40")
    p.css_off(op)


SPEC = OpSpec(
    kind="finish",
    cls=Finish,
    menu=("Finish contour", "One pass along the shape with nose radius comp"),
    order=40,
    emit=emit,
    tool_kinds=("turn", "bore"),
    fields=FIELDS,
)
