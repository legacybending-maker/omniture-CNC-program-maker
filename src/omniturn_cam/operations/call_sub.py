"""Call subroutine (M98): Run a loader / bar-feed or other saved subroutine."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from .base import FieldInfo, Operation, OpSpec

if TYPE_CHECKING:
    from ..post.omniturn import OmniTurnPost
    from ..validation import CheckContext, Issue


@dataclass(kw_only=True)
class CallSub(Operation):
    """M98 call to a subroutine - e.g. a ZipLoader / bar-load routine from the
    job's external subroutine file."""

    t: int | None = None
    sub: int = 1
    # Operator's declaration of where the slide is when the subroutine returns
    # (ENDS_AT in base.py). Default "check" assumes nothing.
    ends_at: Literal["check", "tool_change_z"] = "check"
    label = "call sub"
    uses_tool = False


# Labels and help for this operation's fields (shared fields: fields.FIELDS).
FIELDS = {
    "sub": FieldInfo("Subroutine number", "int", "M98 P number - must exist below M30 (e.g. in the include file)"),
}


def validate(op: CallSub, ctx: CheckContext) -> Iterator[Issue]:
    """Layer 2: a subroutine number the control can have (``}n``, p2.55).
    Whether that subroutine exists below M30 is the rules check's job
    (lint.py: "called but never defined")."""
    if op.sub < 1:
        yield ctx.error("sub", "must be 1 or more")


def emit(p: OmniTurnPost, op: CallSub) -> None:
    """M98 between a park and a resume.

    What the post forgets afterwards comes from reading the subroutine itself
    when it's in the job's include file. A subroutine the post can't read
    (pasted on the control, say) could do anything, so then it forgets
    everything, including where the slide is, unless the operator declared it.
    The call is read exactly as if it were the raw line ``m98pN``, so the two
    operations can't drift apart.
    """
    p.park_before_handoff()
    if op.comment:
        p.comment(op.comment)
    call = f"m98p{op.sub}"
    p.emit(call)
    p.resume_after([call], op.ends_at, f"operation {p.op_number} (call sub P{op.sub})")


def summary(op: CallSub) -> list[str]:
    return [f"P{op.sub}"]


SPEC = OpSpec(
    kind="call_sub",
    cls=CallSub,
    menu=("Call subroutine (M98)", "Run a loader / bar-feed or other saved subroutine"),
    order=60,
    emit=emit,
    fields=FIELDS,
    summary=summary,
    validate=validate,
)
