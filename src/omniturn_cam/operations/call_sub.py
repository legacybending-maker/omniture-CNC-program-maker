"""Call subroutine (M98): Run a loader / bar-feed or other saved subroutine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .base import FieldInfo, Operation, OpSpec

if TYPE_CHECKING:
    from ..post.omniturn import OmniTurnPost


@dataclass(kw_only=True)
class CallSub(Operation):
    """M98 call to a subroutine - e.g. a ZipLoader / bar-load routine from the
    job's external subroutine file."""

    t: int | None = None
    sub: int = 1
    label = "call sub"
    uses_tool = False


# Labels and help for this operation's fields (shared fields: fields.FIELDS).
FIELDS = {
    "sub": FieldInfo("Subroutine number", "int", "M98 P number - must exist below M30 (e.g. in the include file)"),
}


def emit(p: OmniTurnPost, op: CallSub) -> None:
    p.leave_known_state()
    if op.comment:
        p.comment(op.comment)
    p.emit(f"m98p{op.sub}")
    p.forget_state()


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
)
