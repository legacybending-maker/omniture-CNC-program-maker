"""Thread (G33): Single-point thread - the control works out the passes."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from ..gcode import fmt
from .base import FieldInfo, Operation, OpSpec

if TYPE_CHECKING:
    from ..post.omniturn import MachineConfig, OmniTurnPost
    from ..validation import CheckContext, Issue


@dataclass(kw_only=True)
class Thread(Operation):
    """G33 single-point threading cycle (manual p2.23-2.29). The control works out
    the passes, keeping the material removed per pass constant."""

    side: Literal["od", "id"] = "od"
    major: float = 0.5  # major diameter (OD: what you start from)
    minor: float = 0.4084  # minor diameter (OD: the X word, the finished depth)
    z_start: float = 0.0  # where the thread starts on the part
    z_end: float = -0.5  # Z word: end of the thread
    lead: float = 0.05  # K for a single start = pitch
    starts: int = 1  # multi-start: K = lead x starts, Z offset pitch per start
    first_depth: float = 0.02  # I word: first pass, 2x depth of cut in diameter mode
    runup: float = 0.2  # start this far in front of the thread to reach speed
    infeed_angle: float | None = None  # C: None = straight in, 29 = default angle infeed
    pullout: bool = False  # P: keep moving forward while pulling out (no undercut)
    taper_a: float | None = None  # A: X travel over the full Z travel incl. run-up
    spring_passes: int = 0  # extra single passes at finished depth (O)
    start_x: float | None = None  # override the computed start diameter
    lock_overrides: bool = True  # M84 before / M85 after: spindle override can't change rpm mid-thread
    label = "thread"


# Labels and help for this operation's fields (shared fields: fields.FIELDS).
FIELDS = {
    "side": FieldInfo("OD or ID", "choice", "External or internal thread", ("od", "id"), group="Shape"),
    "major": FieldInfo("Major diameter", "dia", "Thread major diameter", group="Shape"),
    "minor": FieldInfo("Minor diameter", "dia", "Thread minor diameter", group="Shape"),
    "lead": FieldInfo("Lead (K)", "len", "Advance per revolution. Single start: lead = pitch = 1/TPI", group="Shape"),
    "starts": FieldInfo(
        "Starts", "int", "Multi-start: each start is its own G33, offset in Z by the pitch", group="Shape"
    ),
    "first_depth": FieldInfo(
        "First pass (I)",
        "len",
        "Depth of the first pass, 2x depth of cut in diameter mode. The control keeps the material per pass "
        "constant; passes = (total depth / I) squared",
    ),
    "runup": FieldInfo(
        "Run-up",
        "len",
        "Start this far in front of the thread so the slide is up to speed. At least .1, more for coarse leads "
        "or high rpm",
    ),
    "infeed_angle": FieldInfo(
        "Angle infeed (C)",
        "text_num",
        "Blank = straight in. 29 = the control's default angle. 0-30 degrees",
        optional=True,
        blank="straight in",
    ),
    "pullout": FieldInfo(
        "Pull out while moving (P)", "bool", "Tool keeps travelling forward as it pulls out - no undercut needed"
    ),
    "taper_a": FieldInfo(
        "Taper (A)",
        "len",
        "X travel over the whole Z travel including run-up. Needs a start X. Blank = straight thread, no A word",
        optional=True,
        blank="none",
    ),
    "spring_passes": FieldInfo("Spring passes (O)", "int", "Extra single passes at finished depth"),
    "lock_overrides": FieldInfo(
        "Lock overrides (M84)",
        "bool",
        "Lock feed and spindle overrides at 100% for the thread so rpm can't change between passes; M85 unlocks after",
    ),
    "z_start": FieldInfo("Thread start Z", "z", "Where the thread begins on the part"),
    "z_end": FieldInfo("Thread end Z (Z)", "z", "Where the thread ends"),
    "start_x": FieldInfo("Start X", "dia", "Blank = major minus I (OD) / minor plus I (ID)", optional=True),
}


def thread_plan(op: Thread) -> dict:
    """Numbers the G33 cycle will use, for the post, the GUI and warnings."""
    depth = abs(op.major - op.minor)  # diameter terms
    if op.side == "od":
        final_x, start_x = op.minor, op.major - op.first_depth
    else:
        final_x, start_x = op.major, op.minor + op.first_depth
    if op.start_x is not None:
        start_x = op.start_x
    passes = (depth / op.first_depth) ** 2 if op.first_depth > 0 else float("inf")
    return {
        "depth": depth,
        "final_x": final_x,
        "start_x": start_x,
        "passes": passes,
        "k": op.lead * op.starts,
        "pitch": op.lead,
    }


def thread_clear_x(op: Thread, start_x: float, stock_x: float) -> float:
    """The X the tool waits at between starts and leaves the thread from.

    OD: outside the bar (``stock_x``, the bar plus clearance). ID: two first
    passes (2 x I on diameter) inside the smaller of the start X and the
    minor diameter, so it's clear of the thread's crests while it moves in Z.

    For an ID that arithmetic can come out below zero in a small bore with a
    deep first pass, which put the tool past the centreline. In a round hole
    the clearance from the wall is greatest at the centre (half the minor
    diameter) and shrinks again past it, so crossing is never better: the
    clearance stops at X0. Only an automatic crossing from the cutting side
    (start X above 0) is stopped; a negative X the operator gave as the start
    is left as given.
    """
    if op.side == "od":
        return stock_x
    if clear_x_stops_at_centre(op, start_x):
        return 0.0
    return min(start_x, op.minor) - 2 * op.first_depth


def clear_x_stops_at_centre(op: Thread, start_x: float) -> bool:
    """True when ``thread_clear_x`` stopped an ID clearance at X0 (it would
    otherwise have crossed the centreline). One function for the rule, so
    ``thread_clear_x`` and the warning in ``emit`` can't disagree."""
    return op.side == "id" and start_x > 0 and min(start_x, op.minor) - 2 * op.first_depth < 0


def validate(op: Thread, ctx: CheckContext) -> Iterator[Issue]:
    """Layer 2: a thread that can be cut at all, whatever the machine.

    Until Stage 2 these checks sat at the top of emit(), so they ran only
    when the program was being written and only one at a time. They run
    with the rest of validation now (opcheck.py), before any code exists.
    This is a *generator*: each ``yield`` hands one problem back and carries
    on, so one pass reports everything that's wrong with the thread.
    """
    if not op.rpm or op.sfm:
        # G33's K is per spindle rev; CSS would change the rpm with the
        # diameter between passes and the passes wouldn't line up.
        field = "sfm" if op.sfm else "rpm"
        yield ctx.error(
            field, "threading needs a fixed RPM (G97), not surface speed", "Give the RPM; leave surface speed blank"
        )
    if op.lead <= 0:
        yield ctx.error("lead", "must be greater than zero", "Lead per rev: 1/TPI, or the pitch in mm")
    if op.starts < 1:
        yield ctx.error("starts", "must be 1 or more")
    if op.first_depth <= 0:
        yield ctx.error("first_depth", "must be greater than zero (the control works out the passes from it, p2.25)")
    if op.spring_passes < 0:
        yield ctx.error("spring_passes", "can't be negative", "0 for none")
    if op.infeed_angle is not None and not 0 <= op.infeed_angle <= 30:
        yield ctx.error("infeed_angle", "must be 0-30 degrees (manual p2.24)", "Leave it blank to feed straight in")
    if op.major <= op.minor:
        # thread_plan() takes abs(major - minor), so swapped diameters would
        # otherwise give a believable depth on the wrong side of the work.
        yield ctx.error("minor", f"must be smaller than the major diameter {op.major:g}", "Check they aren't swapped")
    if op.runup <= 0:
        yield ctx.error("runup", "must be greater than zero: the slide needs a run-up to reach speed (p2.23)")
    elif op.runup < ctx.per_unit(0.1, 2.5):
        yield ctx.warning(
            "runup",
            f"is shorter than the {ctx.per_unit(0.1, 2.5):g} the manual asks for so the slide reaches speed "
            "(p2.23); more for coarse leads or high rpm",
        )
    if op.z_end >= op.z_start:
        # emit() adds the run-up in +Z and offsets extra starts in +Z, so it
        # cuts toward the chuck for both OD and ID threads.
        yield ctx.error(
            "z_end",
            f"must be below the thread start Z {op.z_start:g}: G33 cuts toward the chuck (-Z), "
            "starting the run-up in front of the start Z",
        )
    if op.taper_a is not None and op.start_x is None:
        yield ctx.error(
            "start_x",
            "is needed for a tapered thread (the manual corrects the start for the taper over the run-up, p2.29)",
        )
    if op.side == "id" and op.major > op.minor and op.first_depth > 0:
        plan = thread_plan(op)
        # p2.24: between passes the cycle "will back away from the starting
        # position plus two times the amount of I". For an OD that is away
        # from the centre. For an ID the manual doesn't show which way; if it
        # is toward the centre, as it must be to leave the wall, a deep first
        # pass in a small bore takes the tool past the centreline, toward
        # the far wall. That's the control's own move, so the post can't
        # change it; it can only say so. VERIFY on the machine (F4 Verify
        # and single block) before trusting it.
        retract = plan["start_x"] - 2 * op.first_depth
        if plan["start_x"] > 0 and retract < 0:
            yield ctx.warning(
                "first_depth",
                f"the G33 cycle retracts 2 x I from its start between passes (p2.24), to about X{retract:.4g}, "
                "past the centreline for this ID thread (VERIFY: the manual doesn't show the ID direction)",
                "Use a smaller first pass (I), or check the retract on F4 Verify and in single block",
            )
    if op.major > op.minor and op.first_depth > 0:
        plan = thread_plan(op)
        # OD threads cut down toward the minor, ID threads out toward the major:
        # the start has to be on the far side of the finished X or G33 has
        # nothing to cut (or cuts the wrong way).
        already_past = (op.side == "od" and plan["start_x"] <= plan["final_x"]) or (
            op.side == "id" and plan["start_x"] >= plan["final_x"]
        )
        if already_past:
            given = op.start_x is not None
            yield ctx.error(
                "start_x" if given else "first_depth",
                f"start X {plan['start_x']:g} is already past the finished X {plan['final_x']:g}",
                "Put the start X on the uncut side of the thread"
                if given
                else "The first pass is deeper than the thread; make I smaller",
            )


def validate_machine(op: Thread, ctx: CheckContext, m: MachineConfig) -> Iterator[Issue]:
    """Layer 3: rpm x K is the feed per minute while threading, and it must stay
    within the machine's maximum feed (p2.23). Moved from emit() unchanged."""
    k = thread_plan(op)["k"]
    if op.rpm * k > m.rapid_feed + 1e-9:
        unit = "ipm" if ctx.inch else "mm/min"
        yield ctx.error(
            "rpm",
            f"{op.rpm:g} rpm x {k:g} lead = {op.rpm * k:g} {unit}, over the {m.rapid_feed:g} limit (p2.23)",
            f"Slow the spindle to {int(m.rapid_feed / k)} rpm or less",
        )


def emit(p: OmniTurnPost, op: Thread) -> None:
    # Every check that used to be here is in validate()/validate_machine()
    # above; the post runs them before it writes anything (OmniTurnPost.run).
    plan = thread_plan(op)
    k = plan["k"]

    sx = plan["start_x"]
    clear_x = thread_clear_x(op, sx, p.stock_x)
    if clear_x_stops_at_centre(op, sx):
        # thread_clear_x stopped at the centreline (see its docstring). The
        # operator should know the clearance is smaller than usual.
        p.warn(
            f"{p.here}: the clearance position stops at the centreline (X0), {op.minor / 2:.4g} off the "
            f"thread's minor diameter, instead of crossing it; check it in single block"
        )
    z0 = op.z_start + op.runup
    p.call_tool(op, clear_x, p.job.stock.diameter)
    p.comment(f"THREAD ABOUT {max(1, round(plan['passes']))} PASSES")
    if op.lock_overrides:
        p.emit("m84")  # overrides locked at 100% so rpm can't drift between passes
    words = f"g33x{p.n(plan['final_x'])}z{p.n(op.z_end)}i{p.n(op.first_depth)}k{p.n(k)}"
    if op.taper_a is not None:
        words += f"a{p.n(op.taper_a)}"
    tail = ""
    if op.pullout:
        tail += "p"
    if op.infeed_angle is not None:
        # Always write the angle: "c" alone = 29 deg is only legible in the 2026 manual,
        # and an explicit value is valid on every version (manual: "add C27").
        tail += f"c{fmt(op.infeed_angle, 2)}"
    for s_i in range(op.starts):
        zs = z0 + s_i * plan["pitch"]
        p.rapid(x=clear_x)
        p.rapid(z=zs)
        p.rapid(x=sx)
        p.emit(words + tail)
        # Spring passes repeat the same cycle with O from the same start point,
        # same rpm, same P/C (p2.26).
        for _ in range(op.spring_passes):
            p.emit(words + tail + "o")
        p.cycle_ended_at(sx, zs)  # cycle returns to its start point
    p.rapid(x=clear_x)
    if op.lock_overrides:
        p.emit("m85")


def summary(op: Thread) -> list[str]:
    return [f"{op.side.upper()} {op.major:g} K{op.lead * op.starts:g}" + (f" x{op.starts}" if op.starts > 1 else "")]


SPEC = OpSpec(
    kind="thread",
    cls=Thread,
    menu=("Thread (G33)", "Single-point thread - the control works out the passes"),
    order=80,
    emit=emit,
    tool_kinds=("thread",),
    fields=FIELDS,
    summary=summary,
    default_rpm=500,
    # M10 x 1.5 (minor 8.16): the metric counterpart of the inch defaults.
    # Run-up 5 mm is past the 2.5 mm minimum.
    metric_defaults={
        "major": 10.0,
        "minor": 8.16,
        "z_end": -12.0,
        "lead": 1.5,
        "first_depth": 0.4,
        "runup": 5.0,
    },
    validate=validate,
    validate_machine=validate_machine,
)
