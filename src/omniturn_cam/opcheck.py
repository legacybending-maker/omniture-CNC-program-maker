"""Validation of a job's operation list: runs the three layers
(validation.py) over every operation, in order, and collects what they find.

Job.issues() in job.py calls this after its own job-level checks (name,
units, stock, stations, shapes). The post calls the same thing with the
machine profile, which adds layer 3. Nothing here writes G-code or changes an
operation: validation only reports, so running it twice gives the same
answer and the GUI can run it as often as it likes.
"""

from __future__ import annotations

from dataclasses import fields
from typing import TYPE_CHECKING

from .fields import info
from .gcode import fmt
from .operations import Operation, spec_for
from .validation import (
    MIN_FEED_INCH,
    MIN_FEED_METRIC,
    CheckContext,
    Issue,
    errors,
    is_whole,
    quantity_issues,
    type_issues,
)

if TYPE_CHECKING:
    from .job import Job
    from .post.omniturn import MachineConfig


def where_of(number: int, op: Operation) -> str:
    """'operation 3 (drill t2)': the same wording the post uses in its own
    errors (OmniTurnPost.here), so the operator sees one way of naming things."""
    t = getattr(op, "t", None)
    tool = f" t{t}" if op.uses_tool and is_whole(t) else ""
    return f"operation {number} ({op.label}{tool})"


def operation_issues(job: Job, machine: MachineConfig | None = None) -> list[Issue]:
    """Every problem in every operation. Layer 3 runs only when ``machine`` is
    given, and only for operations with no errors in layers 1 and 2."""
    out: list[Issue] = []
    # enumerate(..., 1) numbers from 1, the way the process list does.
    for number, op in enumerate(job.operations, 1):
        out += _check_one(number, op, job, machine)
    return out


def _check_one(number: int, op: Operation, job: Job, machine: MachineConfig | None) -> list[Issue]:
    try:
        spec = spec_for(op)
    except TypeError as e:
        return [Issue(f"operation {number}", str(e))]
    where = where_of(number, op)
    infos = {f.name: info(spec.kind, f.name) for f in fields(op)}

    def describe(name: str) -> str:
        # A nested function ("closure"): it can see `infos` from the function
        # around it, so each operation's context gets a describe() that
        # already knows that operation's labels.
        # The label the operator sees on the form, then the key in the job
        # file in [brackets]: labels already use (parentheses) for the
        # control's own letter, as in "Peck depth (K)".
        return f"{infos[name].label} [{name}]" if name in infos else name

    # Layer 1a: types. If any value has the wrong type, stop here for this
    # operation: every later rule compares numbers, and comparing a string
    # with a number raises TypeError instead of reporting the problem.
    found = type_issues(op, where, describe)
    if found:
        return found
    # Layer 1b: ranges that follow from the kind of quantity (feeds > 0...).
    found = quantity_issues(op, where, infos)

    station = None
    if op.uses_tool:
        station = job.setup.stations.get(op.t)
        if station is None:
            found.append(
                Issue(
                    where,
                    f"no tool is loaded at t{op.t}",
                    "Load one there on F3 Tool plate, or pick another station",
                    setting=describe("t"),
                    value=str(op.t),
                )
            )
    prof = getattr(op, "profile", None)
    if prof is not None and prof not in job.profiles:
        known = ", ".join(sorted(job.profiles)) or "none yet"
        found.append(Issue(where, f"there is no shape called {prof!r} (shapes: {known})", setting=describe("profile")))

    ctx = CheckContext(op, where, job, station, describe)
    found += _common(op, ctx)
    if errors(found):
        return found
    # Layer 2: the operation's own rules. list() runs the generator to the end.
    if spec.validate:
        found += list(spec.validate(op, ctx))
    # Layer 3: the machine. Skipped while the operation itself is wrong:
    # "feed too fast for the machine" is noise next to "feed is zero".
    if machine is not None and not errors(found):
        found += _common_machine(op, ctx, machine)
        if spec.validate_machine:
            found += list(spec.validate_machine(op, ctx, machine))
    return found


# --------------------------------------------------------------------- rules every operation shares
def _common(op: Operation, ctx: CheckContext) -> list[Issue]:
    """Layer 2 for the fields every operation inherits (operations/base.py)."""
    out = []
    if op.uses_tool and op.rpm is None and op.sfm is None:
        # Used to be a PostError from deep in the post (est_rpm); now found
        # with the rest, before any code is written.
        out.append(ctx.error("rpm", "a cut needs a spindle speed", "Give the RPM, or a surface speed for CSS"))
    if op.min_rpm is not None and op.max_rpm is not None and op.min_rpm > op.max_rpm:
        out.append(
            ctx.error(
                "min_rpm",
                f"is above the CSS max RPM {op.max_rpm:g}: the G76 floor would be over the G77 ceiling (manual p2.52)",
                "Make the min lower than the max",
            )
        )
    return out


def _feed_words(ctx: CheckContext) -> tuple[str, str]:
    return ("in/rev", "ipm") if ctx.inch else ("mm/rev", "mm/min")


def _common_machine(op: Operation, ctx: CheckContext, m: MachineConfig) -> list[Issue]:
    """Layer 3 for every operation: CSS limits against the machine's top speed,
    and every feed against the control's feed range (manual p2.11, p2.51)."""
    if not op.uses_tool:
        return []
    out: list[Issue] = []
    css = bool(op.sfm and not op.rpm)  # the same test css_on() uses
    top = op.max_rpm or m.max_rpm  # G77 ceiling: the op's, else the machine's
    if css and op.min_rpm and op.min_rpm > top:
        source = "CSS max RPM" if op.max_rpm else "max_rpm in machine.toml"
        out.append(
            ctx.error("min_rpm", f"is above the top spindle speed {top:g} rpm ({source})", "Lower the CSS min RPM")
        )
    if op.max_rpm and op.max_rpm > m.max_rpm:
        out.append(
            ctx.warning(
                "max_rpm",
                f"is over the {m.max_rpm:g} rpm limit set for the {m.name}; the spindle won't get there",
                "Lower it to the machine's max or less",
            )
        )
    per_rev, per_min = _feed_words(ctx)
    slowest = MIN_FEED_INCH if ctx.inch else MIN_FEED_METRIC
    spec = spec_for(op)
    for f in fields(op):
        kind = info(spec.kind, f.name).kind
        v = getattr(op, f.name)
        if v is None or kind not in ("feed", "feed_ipm"):
            continue
        if v < slowest - 1e-12:
            unit = per_rev if kind == "feed" else per_min
            out.append(ctx.error(f.name, f"is below the control's slowest feed, {slowest:g} {unit} (manual p2.51)"))
            continue
        if kind == "feed" and fmt(v, m.feed_decimals) == "0":
            out.append(ctx.error(f.name, f"would be written as F0 at {m.feed_decimals} decimal places (feed_decimals)"))
            continue
        if kind == "feed_ipm":
            if v > m.rapid_feed + 1e-9:
                out.append(
                    ctx.error(
                        f.name,
                        f"is over the control's fastest feed, {m.rapid_feed:g} {per_min} (manual p2.11)",
                        f"Use {m.rapid_feed:g} or less",
                    )
                )
            continue
        # A feed per rev becomes a feed per minute at the spindle speed; the
        # control's limit is per minute (p2.11: "at 4000 rpm the fastest G95
        # setting is F.075"). At a fixed rpm that is certain, so it refuses.
        # With CSS the rpm depends on the diameter, so it's a warning.
        rpm = op.rpm if op.rpm else top
        if v * rpm > m.rapid_feed + 1e-9:
            fastest = m.rapid_feed / rpm
            problem = (
                f"at {rpm:g} rpm that is {v * rpm:g} {per_min}, over the control's fastest feed of "
                f"{m.rapid_feed:g} {per_min} (manual p2.11). The fastest at {rpm:g} rpm is {fastest:.5g} {per_rev}"
            )
            if op.rpm:
                out.append(ctx.error(f.name, problem, "Check for a misplaced decimal point"))
            else:
                out.append(ctx.warning(f.name, "under CSS the spindle can reach the top speed: " + problem))
    return out
