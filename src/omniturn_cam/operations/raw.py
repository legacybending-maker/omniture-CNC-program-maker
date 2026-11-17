"""Raw program lines: Loader M-codes, input waits, anything not covered above."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from .base import FieldInfo, Operation, OpSpec

if TYPE_CHECKING:
    from ..post.omniturn import OmniTurnPost


@dataclass(kw_only=True)
class RawCode(Operation):
    """Lines passed straight into the program (loader M-codes, M91-M94 waits,
    LS/LF loops...). Still checked by the rules linter. Afterwards the post
    forgets whatever the lines could change and re-establishes it; if they can
    move the slide, it won't move it again unless ``ends_at`` declares where
    the slide is (docs/omniturn-post-rules.md, "Handing control...")."""

    t: int | None = None
    code: str = ""
    # Operator's declaration of where the slide is afterwards (ENDS_AT in base.py).
    # Default "check" assumes nothing. A new optional key with a default, so
    # old job files load unchanged (CONTRIBUTING: Compatibility).
    ends_at: Literal["check", "tool_change_z"] = "check"
    label = "raw code"
    uses_tool = False


# Labels and help for this operation's fields (shared fields: fields.FIELDS).
FIELDS = {
    "code": FieldInfo("Program lines", "multiline", "Copied into the program as written, then rules-checked"),
}


def emit(p: OmniTurnPost, op: RawCode) -> None:
    """Copy the lines into the program between a park and a resume.

    The post is handed the same lines it writes, so what it forgets afterwards
    is decided by exactly the code that runs (post/external.py).
    """
    p.park_before_handoff()
    if op.comment:
        p.comment(op.comment)
    # A list comprehension: the stripped, non-blank lines, in order.
    lines = [line.strip() for line in op.code.splitlines() if line.strip()]
    for line in lines:
        p.emit(line)
    p.resume_after(lines, op.ends_at, p.here)


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
