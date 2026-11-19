"""Finish contour: One pass along the shape with nose radius comp."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from ..gcode import PostError
from .base import FieldInfo, Operation, OpSpec

if TYPE_CHECKING:
    from ..post.omniturn import MachineConfig, OmniTurnPost
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
        nose = ctx.station.tool.nose_radius
        comp = resolve_comp(op, prof, nose)
        if comp != "none" and ctx.station.d is None:
            yield ctx.error(
                "comp",
                f"is {comp}, which needs a D number on station t{op.t} (the secondary offset holding the "
                "nose radius, p2.35)",
                "Give the station a D number on F3 Tool plate, or set comp to none",
            )
        # An ID shape ending in a bore smaller than four nose radii (two on
        # each side of the centre) can't be led off by twice the nose radius
        # (p2.32) at all: the most clearance a round hole offers is at the
        # centre, D/2, and crossing the centre only closes on the far wall
        # (lead_off_x). With comp on that breaks the manual's rule whatever
        # the post writes, so refuse instead of writing a lead-off that can't
        # comply. Without comp the rule doesn't apply and lead_off_x just
        # stops at X0.
        if comp != "none" and stops_at_centre(prof, nose, ctx.job.units) and prof.end.x / 2 + 1e-9 < 2 * nose:
            yield ctx.error(
                "profile",
                f"ends in a bore of X{prof.end.x:g}, too small to lead off twice the t{op.t} nose radius "
                f"({2 * nose:g}) before G40 (manual p2.32) without crossing the centreline",
                "End the shape on a larger diameter, use a tool with a smaller nose radius, or set comp to none",
            )


def lead_off_length(nose_radius: float, units: str) -> float:
    """How far (on diameter) the pass leads off the shape before G40.

    p2.32: the clearance move off the part must be at least twice the nose
    radius. This uses three times (six on diameter, because X is a diameter),
    with a floor of .01 in / .25 mm radial so a tool with no nose radius still
    leaves the wall. Unchanged from before Stage 5; only moved into a function
    so ``lead_off_x`` and the validation share it.
    """
    return 2 * max(3 * nose_radius, 0.01 if units == "inch" else 0.25)


def lead_off_x(prof, nose_radius: float, units: str) -> float:
    """The X the pass leads off to: out from an OD, in toward the centre from an ID.

    An ID lead-off never crosses the centreline. For a bore of diameter D the
    tool's clearance from the wall is greatest at the centre, D/2; past the
    centre it is moving *toward* the far wall again. So in a bore too small
    for the usual lead-off, stopping at X0 gives more clearance than the
    old arithmetic (which went to a negative X). This is geometry of a round
    hole, not a machine rule; it doesn't stop negative X anywhere else.

    A shape that ends on the centreline itself (X0 or below, e.g. finishing
    across the bottom of a blind hole) is left alone: like the Face operation,
    which runs just past centre on purpose, carrying on along the face there
    isn't a move toward a wall.
    """
    lead = lead_off_length(nose_radius, units)
    if prof.side == "od":
        return prof.end.x + lead
    if stops_at_centre(prof, nose_radius, units):
        return 0.0
    return prof.end.x - lead


def stops_at_centre(prof, nose_radius: float, units: str) -> bool:
    """True when an ID shape ends on a bore (X above 0) too small for the
    usual lead-off, so ``lead_off_x`` stops at X0 instead of crossing it."""
    return prof.side == "id" and prof.end.x > 0 and prof.end.x - lead_off_length(nose_radius, units) < 0


def lead_in_length(op: Finish, prof, z_clear: float) -> float:
    """Length of the move that turns comp on, the one that carries the D word.

    ``emit`` below rapids to X = the shape's start X at the approach Z, then
    feeds in Z alone onto the shape's start point with ``d<n>`` on the line.
    So the move is purely in Z, and its length is the gap between the
    approach Z and the start Z. The approach Z is the operator's Start Z, or
    (blank) the front of the shape plus ``z_clear`` from machine.toml, the
    same rule as ``OmniTurnPost.start_point``.

    Kept as its own function so the validation below and the tests measure
    the lead-in exactly the way ``emit`` makes it, instead of each working it
    out again and drifting apart.
    """
    _, _, _, zmax = prof.bounds()
    sz = op.start_z if op.start_z is not None else zmax + z_clear
    return abs(sz - prof.start.z)


def validate_machine(op: Finish, ctx: CheckContext, m: MachineConfig) -> Iterator[Issue]:
    """Layer 3: the comp lead-in must be at least the nose radius (p2.32).

    The manual, p2.31-2.32 (the bullet list on those pages): G41/G42 goes on
    a line by itself, the D word goes on the linear move after it, and when
    comp turns on or off the tool must be off the part by "no less than the
    size of the radius". The lead-*off* rule
    on the same page is stricter, twice the nose radius, and ``emit`` already
    meets it; the two rules are different and are kept different here.

    What this measures: the length of the D-word move. That move ends on the
    shape, so the tool can't be further off the part than the move is long:
    a lead-in shorter than the nose radius certainly breaks the rule. A long
    enough lead-in is necessary, not proof: material the shape doesn't
    describe (a bigger bar end, an earlier operation's shoulder) isn't
    modelled. It's an error, not a warning, because the manual states it as
    a requirement of the control (CLAUDE.md: errors refuse, they don't guess).

    Needs the machine profile because a blank Start Z comes from ``z_clear``.
    """
    if ctx.station is None:
        return  # no tool, so no nose radius: the station check reports that
    prof = ctx.job.profiles[op.profile]
    if not prof.segments:
        return  # layer 2 already refused the empty shape
    nose = ctx.station.tool.nose_radius
    if resolve_comp(op, prof, nose) == "none" or nose <= 0:
        return  # no comp, so no turn-on rule
    lead = lead_in_length(op, prof, m.z_clear)
    # A small tolerance (1e-9) so a lead-in typed as exactly the nose radius
    # isn't refused because of floating-point noise: "no less than" includes
    # equal.
    if lead + 1e-9 < nose:
        where = "Start Z" if op.start_z is not None else f"the automatic start (z_clear {m.z_clear:g})"
        yield ctx.error(
            "start_z",
            f"gives a comp lead-in of {lead:.4g}, less than the t{op.t} nose radius {nose:g}: when G41/G42 "
            f"turns on, the tool must be off the part by at least the nose radius (manual p2.32). From {where}",
            f"Set Start Z at least {prof.start.z + nose:.4g} (the shape starts at Z{prof.start.z:g}), "
            "or set comp to none",
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
    # Lead off the part by at least twice the nose radius before G40 (p2.32):
    # outward from an OD, toward the centre (never past it) from an ID.
    off_x = lead_off_x(prof, st.tool.nose_radius, p.job.units)
    if stops_at_centre(prof, st.tool.nose_radius, p.job.units):
        # lead_off_x stopped at the centreline (see its docstring). Say so:
        # the clearance off the bore is less than usual, which is worth a
        # look in single block, and the operator should know why the program
        # differs from what they'd expect.
        p.warn(
            f"{p.here}: the lead-off stops at the centreline (X0), {prof.end.x / 2:.4g} off the bore at "
            f"X{prof.end.x:g}, instead of crossing it; check it in single block"
        )
    p.cut(x=off_x, feed=op.feed)
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
    validate_machine=validate_machine,
)
