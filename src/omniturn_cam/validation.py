"""The shared vocabulary of input validation: what a problem looks like, how
problems are reported, and the type checks every layer builds on.

Validation runs in three layers (docs/ARCHITECTURE.md, "Validation"):

1. **Scalar and type**: is each value the right type, finite, and in range for
   the kind of quantity it is (a feed, an rpm, a time)? Worked out from each
   field's type hint and its FieldInfo ``kind``, so a new operation is checked
   without writing anything.
2. **Operation geometry**: does this operation make sense on its own (a drill
   depth beyond its start, a thread whose minor is smaller than its major)?
   Each operation module's ``OpSpec.validate``.
3. **Machine**: does it fit the machine it will run on (feed over the
   control's maximum, a CSS floor above the spindle's top speed)? Each
   module's ``OpSpec.validate_machine``, plus machine-wide checks.

This module is the bottom of that stack. It imports nothing from the rest of
omniturn-cam, on purpose: the operation modules import it, and
``operations/__init__.py`` imports the operation modules while it is still
starting up. If this file imported ``fields`` or ``operations`` back, Python
would find a half-built module and fail (an *import cycle*). Keeping the
shared basics in a module with no project imports is the usual cure.
``jobcheck.py`` is the layer above: it walks a job and calls everything.
"""

from __future__ import annotations

import functools
import math
import types
import typing
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, fields
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from .job import Job
    from .operations.base import FieldInfo
    from .tools import Station

Severity = Literal["error", "warning"]


def show(value: Any) -> str:
    """A value the way an operator would type it, for messages.

    Floats use ``:g`` (``.003`` reads as ``0.003``, ``-0.25`` as ``-0.25``),
    blanks read as "blank", strings keep their quotes so a stray space shows,
    and booleans read as TOML spells them (``true``/``false``), because that's
    what the operator sees in a job file.
    """
    if value is None:
        return "blank"
    if isinstance(value, bool):  # before int: in Python, True is also an int
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, str):
        return repr(value)
    return str(value)


@dataclass(frozen=True)
class Issue:
    """One problem found in a job, tool library or machine profile.

    A small frozen dataclass rather than a bare string so the GUI and tests
    can look at the parts (which setting? error or warning?) instead of
    picking a message apart. ``frozen=True`` makes instances read-only, so an
    issue can't be changed after it was reported.

    where:    which thing, in operator terms: "operation 3 (drill t2)",
              "shape 'od' segment 2", "/home/me/.config/omniturn-cam/machine.toml".
    problem:  what is wrong, e.g. "must be greater than zero".
    fix:      what to do about it, e.g. "Type the feed per revolution".
    setting:  the setting, label and key: "Feed [feed]". Empty when the
              problem isn't about one setting.
    value:    the value as shown to the operator (``show()``), or None to
              leave it out of the message.
    severity: "error" refuses to write the program; "warning" is shown and
              the program is still written (ARCHITECTURE.md, invariant 3).
    """

    where: str
    problem: str
    fix: str = ""
    setting: str = ""
    value: str | None = None
    severity: Severity = "error"

    def __str__(self) -> str:
        text = self.where
        if self.setting:
            text += f": {self.setting}"
            if self.value is not None:
                text += f" is {self.value}"
        text += f": {self.problem}"
        if self.fix:
            text += f". {self.fix}"
        return text


def format_issues(issues: Iterable[Issue]) -> str:
    """One problem as one line; several as a count and a bulleted list, so
    the operator fixes them all in one go instead of one per attempt."""
    issues = list(issues)
    if len(issues) == 1:
        return str(issues[0])
    return f"{len(issues)} problems:\n" + "\n".join(f"- {i}" for i in issues)


class ValidationError(ValueError):
    """Raised when validation finds errors. ``issues`` has every one of them.

    A ValueError subclass so the code that already catches ValueError (the
    GUI, ``post()``) handles it without changes: callers can be as specific or
    as general as they need. Warnings are never put in here; they don't stop
    anything.
    """

    def __init__(self, issues: Iterable[Issue]):
        self.issues = list(issues)
        super().__init__(format_issues(self.issues))


def errors(issues: Iterable[Issue]) -> list[Issue]:
    return [i for i in issues if i.severity == "error"]


def raise_errors(issues: Iterable[Issue]) -> None:
    """Raise ValidationError if any of ``issues`` is an error; ignore warnings."""
    errs = errors(issues)
    if errs:
        raise ValidationError(errs)


# --------------------------------------------------------------------- numbers
def is_number(value: Any) -> bool:
    """True for int and float, False for everything else *including bool*.

    ``isinstance(True, int)`` is True in Python (bool is a subclass of int, a
    leftover from before Python had a bool type), so ``coolant = 1`` or
    ``rpm = true`` would sneak through a plain isinstance check. Excluding
    bool explicitly is the standard guard.
    """
    return isinstance(value, int | float) and not isinstance(value, bool)


def is_whole(value: Any) -> bool:
    """An int that isn't a bool. 2.0 is not whole here: a job file that says
    ``starts = 2.0`` was written by something that doesn't know it's a count."""
    return isinstance(value, int) and not isinstance(value, bool)


def is_finite(value: Any) -> bool:
    """A number that is not NaN or infinity. TOML can spell both (``nan``,
    ``inf``), and NaN is especially sneaky: every comparison with it is False,
    so ``feed <= 0`` doesn't catch it and it reaches the program as ``fnan``."""
    return is_number(value) and math.isfinite(value)


# --------------------------------------------------------------------- types from type hints
def _optional(hint: Any) -> tuple[bool, Any]:
    """Split ``X | None`` into (True, X); anything else into (False, hint).

    ``typing.get_origin`` tells what a hint is built from: for ``float | None``
    it is ``types.UnionType`` (the ``|`` syntax) or ``typing.Union`` (the older
    ``Optional[float]`` spelling); ``get_args`` gives the parts.
    """
    if typing.get_origin(hint) in (types.UnionType, typing.Union):
        rest = [a for a in typing.get_args(hint) if a is not type(None)]
        if len(rest) != len(typing.get_args(hint)) and len(rest) == 1:
            return True, rest[0]
    return False, hint


def type_problem(value: Any, hint: Any) -> str | None:
    """What's wrong with ``value`` for a field annotated ``hint``, or None if
    it fits. Understands float, int, bool, str, Literal[...] and ``X | None``.

    Raises TypeError for any other hint: a new field with a type this function
    can't check must fail the tests (tests/test_validation.py), not go
    unchecked at the machine.
    """
    allows_none, base = _optional(hint)
    if value is None:
        return None if allows_none else "must be filled in"
    if typing.get_origin(base) is Literal:
        choices = typing.get_args(base)
        # Check it's text first: `in` compares with ==, and a choice is
        # always a string here, so anything else is wrong however it compares.
        if isinstance(value, str) and value in choices:
            return None
        return "must be one of " + ", ".join(choices)
    if base is bool:
        return None if isinstance(value, bool) else "must be true or false"
    if base is int:
        return None if is_whole(value) else "must be a whole number"
    if base is float:
        if not is_number(value):
            return "must be a number"
        if not math.isfinite(value):
            return "must be a finite number, not nan or infinity"
        return None
    if base is str:
        return None if isinstance(value, str) else "must be text"
    raise TypeError(f"validation can't check values annotated {hint!r}; add it to validation.type_problem")


@functools.cache
def hints(cls: type) -> dict[str, Any]:
    """The resolved type hints of a dataclass, cached per class.

    With ``from __future__ import annotations`` (used across this project) the
    annotations are stored as strings; ``typing.get_type_hints`` evaluates
    them. ``@functools.cache`` remembers the answer per class, so a job with
    50 operations doesn't redo it 50 times. Only works because a class is
    hashable and its hints never change while the program runs.
    """
    return typing.get_type_hints(cls)


def type_issues(
    obj: Any,
    where: str,
    describe: Callable[[str], str] = lambda name: name,
    skip: Iterable[str] = (),
) -> list[Issue]:
    """Check every dataclass field of ``obj`` against its type hint.

    ``describe`` turns a field name into what the operator sees, e.g.
    "Feed [feed]". ``skip`` names fields checked some other way (a dict of
    tool params, say).
    """
    out = []
    skip = set(skip)
    h = hints(type(obj))
    for f in fields(obj):
        if f.name in skip:
            continue
        value = getattr(obj, f.name)
        problem = type_problem(value, h[f.name])
        if problem:
            out.append(Issue(where, problem, setting=describe(f.name), value=show(value)))
    return out


# --------------------------------------------------------------------- quantity kinds
# Layer 1 rules that follow from *what kind of quantity* a field is, keyed by
# FieldInfo.kind (the same kind that picks the units label on the form). A
# feed is a feed in every operation, so the rule lives here once instead of
# in each emitter. Coordinates ("dia", "z") and plain lengths ("len") have no
# rule here on purpose: a Z of -.5 or a cutoff end diameter of -.01 is
# normal, and which direction is right depends on the operation (layer 2).
#
# Each entry: (test the value must pass, what's wrong if it doesn't).
QUANTITY_RULES: dict[str, tuple[Callable[[float], bool], str]] = {
    "rpm": (lambda v: v > 0, "must be greater than zero"),
    "sfm": (lambda v: v > 0, "must be greater than zero"),
    "feed": (lambda v: v > 0, "must be greater than zero"),
    "feed_ipm": (lambda v: v > 0, "must be greater than zero"),
    "sec": (lambda v: v >= 0, "can't be negative"),
    # p2.9: tool numbers are T1-T32.
    "station": (lambda v: 1 <= v <= 32, "must be 1-32 (OmniTurn tool numbers, manual p2.9)"),
}


def quantity_issues(obj: Any, where: str, infos: Mapping[str, FieldInfo]) -> list[Issue]:
    """Apply QUANTITY_RULES to every field of ``obj`` that has a value.

    Only call this after ``type_issues`` found nothing: the rules compare
    numbers, and a string in a number field would raise TypeError here.
    Blank optional fields (None) are skipped; whether blank is allowed was the
    type check's job.
    """
    out = []
    for f in fields(obj):
        info = infos[f.name]
        rule = QUANTITY_RULES.get(info.kind)
        value = getattr(obj, f.name)
        if rule is None or not is_number(value):
            continue
        test, problem = rule
        if not test(value):
            fix = f"Leave it blank for {info.blank}" if info.optional else ""
            out.append(Issue(where, problem, fix, setting=f"{info.label} [{f.name}]", value=show(value)))
    return out


# --------------------------------------------------------------------- what an operation check is handed
@dataclass
class CheckContext:
    """What an operation's ``validate``/``validate_machine`` gets besides the
    operation: the job around it, its station, and helpers that write the
    message in the standard form.

    The context is built by jobcheck.py for each operation, so the operation
    modules never format "operation 3 (drill t2)" themselves. Handing a
    function one object with everything it may need, instead of a growing list
    of arguments, means a new piece of context doesn't change every
    operation's signature.
    """

    op: Any
    where: str  # "operation 3 (drill t2)"
    job: Job
    station: Station | None  # the op's station; None if it has none or it isn't loaded
    describe: Callable[[str], str]  # field name -> "Feed [feed]"

    @property
    def inch(self) -> bool:
        return self.job.units == "inch"

    def per_unit(self, inch: float, metric: float) -> float:
        """Pick a threshold by the job's units, e.g. ``ctx.per_unit(0.1, 2.5)``."""
        return inch if self.inch else metric

    def issue(self, field: str, problem: str, fix: str = "", severity: Severity = "error") -> Issue:
        return Issue(
            self.where,
            problem,
            fix,
            setting=self.describe(field),
            value=show(getattr(self.op, field)),
            severity=severity,
        )

    def error(self, field: str, problem: str, fix: str = "") -> Issue:
        return self.issue(field, problem, fix)

    def warning(self, field: str, problem: str, fix: str = "") -> Issue:
        return self.issue(field, problem, fix, "warning")


# The control's feed range (manual p2.51, "G94 and G95 Feedrate selections"):
# slowest .00005 in/min or in/rev, fastest 300 in/min (in G95 the 300 ipm
# limit is per minute, so the fastest feed per rev depends on the rpm, p2.11).
MIN_FEED_INCH = 0.00005
MIN_FEED_METRIC = MIN_FEED_INCH * 25.4
