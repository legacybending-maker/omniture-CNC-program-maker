"""Raw program lines: Loader M-codes, input waits, anything not covered above."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .base import FieldInfo, Operation, OpSpec

if TYPE_CHECKING:
    from ..post.omniturn import OmniTurnPost


@dataclass(kw_only=True)
class RawCode(Operation):
    """Lines passed straight into the program (loader M-codes, M91-M94 waits,
    LS/LF loops...). Still checked by the rules linter."""

    t: int | None = None
    code: str = ""
    label = "raw code"
    uses_tool = False


# Labels and help for this operation's fields (shared fields: fields.FIELDS).
FIELDS = {
    "code": FieldInfo("Program lines", "multiline", "Copied into the program as written, then rules-checked"),
}


def emit(p: OmniTurnPost, op: RawCode) -> None:
    p.leave_known_state()
    if op.comment:
        p.comment(op.comment)
    for line in op.code.splitlines():
        if line.strip():
            p.emit(line.strip())
    p.forget_state()


def summary(op: RawCode) -> list[str]:
    first = next((line.strip() for line in op.code.splitlines() if line.strip()), "")
    return [first + (" ..." if op.code.count("\n") else "")]


SPEC = OpSpec(
    kind="raw",
    cls=RawCode,
    menu=("Raw program lines", "Loader M-codes, input waits, anything not covered above"),
    order=70,
    emit=emit,
    fields=FIELDS,
    summary=summary,
)
