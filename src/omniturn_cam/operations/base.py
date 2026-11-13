"""What every operation module provides: the Operation base dataclass, the
FieldInfo labels the GUI builds its forms from, and the OpSpec each module
registers. See operations/__init__.py and docs/ARCHITECTURE.md."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from ..post.omniturn import OmniTurnPost
    from ..tools import Tool


@dataclass(kw_only=True)
class Operation:
    t: int  # OmniTurn station number
    rpm: float | None = None  # G97 spindle speed
    sfm: float | None = None  # G96 constant surface speed (sfm or m/min)
    min_rpm: float | None = None  # G76 with CSS
    max_rpm: float | None = None  # G77 with CSS
    spindle: Literal["m03", "m04"] = "m03"
    coolant: bool = True
    comment: str = ""

    label = "operation"
    uses_tool = True


@dataclass(frozen=True)
class FieldInfo:
    label: str
    kind: str  # station profile dia z len feed rpm sfm sec bool text choice
    help: str = ""
    choices: tuple = ()
    group: str = "Cut"  # form section
    optional: bool = False  # blank allowed -> automatic
    blank: str = "auto"  # placeholder shown when left blank


@dataclass(frozen=True)
class OpSpec:
    """Everything the rest of the program needs to know about one operation type.

    Each module in this package defines one SPEC; the package finds them all, so
    adding an operation is adding a file."""

    kind: str  # the job file's `type` value; never rename (old job files use it)
    cls: type[Operation]  # the dataclass the operator fills in
    menu: tuple[str, str]  # (name, one-line description) in the GUI's add-process menu
    order: int  # position in that menu; unique (tests/test_operations.py)
    emit: Callable[[OmniTurnPost, Operation], None]  # writes the OmniTurn code
    tool_kinds: tuple[str, ...] = ()  # tool kinds that suit it; the first loaded one is picked
    fields: Mapping[str, FieldInfo] = field(default_factory=dict)  # this op's labels, over fields.FIELDS
    summary: Callable[[Operation], list[str]] | None = None  # extra words for the process list
    tool_defaults: Callable[[Tool, dict], None] | None = None  # pre-fill a new op from its tool
    default_rpm: float = 1500  # starting rpm for a new op
