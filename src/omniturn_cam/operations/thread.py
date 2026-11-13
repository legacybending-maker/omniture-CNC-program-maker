"""Thread (G33): Single-point thread - the control works out the passes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from ..gcode import PostError, fmt
from .base import FieldInfo, Operation, OpSpec

if TYPE_CHECKING:
    from ..post.omniturn import OmniTurnPost


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
        "X travel over the whole Z travel including run-up. Needs a start X",
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


def emit(p: OmniTurnPost, op: Thread) -> None:
    inch = p.job.units == "inch"
    if not op.rpm or op.sfm:
        raise PostError(f"T{op.t} thread: threading needs a fixed RPM (G97), not surface speed")
    if op.first_depth <= 0 or op.lead <= 0 or op.starts < 1:
        raise PostError(f"T{op.t} thread: lead, starts and first-pass depth must be positive")
    if op.infeed_angle is not None and not 0 <= op.infeed_angle <= 30:
        raise PostError(f"T{op.t} thread: infeed angle must be 0-30 degrees (manual p2.24)")
    plan = thread_plan(op)
    k = plan["k"]
    feed_limit = p.m.rapid_feed
    if op.rpm * k > feed_limit + 1e-9:
        raise PostError(
            f"T{op.t} thread: {op.rpm:g} rpm x {k:g} lead = {op.rpm * k:g} "
            f"{'ipm' if inch else 'mm/min'}, over the {feed_limit:g} limit. "
            f"Slow the spindle to {int(feed_limit / k)} rpm or less (p2.23)"
        )
    min_runup = 0.1 if inch else 2.5
    if op.runup < min_runup:
        p.warn(
            f"T{op.t} thread: run-up {op.runup:g} is short; the manual says start at least "
            f"{min_runup:g} away so the slide reaches speed, more for coarse leads or high rpm"
        )
    if op.taper_a is not None and op.start_x is None:
        raise PostError(
            f"T{op.t} thread: give the start X for a tapered thread "
            "(the manual corrects the start for the taper over the run-up)"
        )
    if op.side == "od" and plan["start_x"] <= plan["final_x"] or op.side == "id" and plan["start_x"] >= plan["final_x"]:
        raise PostError(
            f"T{op.t} thread: start X {plan['start_x']:g} is already past the finished X {plan['final_x']:g}"
        )

    sx = plan["start_x"]
    clear_x = p.stock_x if op.side == "od" else min(sx, op.minor) - 2 * op.first_depth
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
        p.x, p.z, p.motion = sx, zs, None  # cycle returns to its start point
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
)
