"""What the post knows about the machine while it writes a program.

The post writes code that only makes sense if the control is in the mode the
post thinks it is in: ``g95f.003`` is a feed per revolution only in G95, ``x.5``
is a diameter only in G72, ``s1500`` is an rpm only in G97. So the post keeps a
record of every mode it relies on, and of where the slide is. That record is
``MachineState``.

The rule this module exists for: **every field can say "I don't know"**, with the
value ``UNKNOWN``. Before this module the post used ``None`` for some fields,
``False`` for coolant (which can't tell "off" from "don't know") and nothing at
all for others (units, G90/G91, comp...). After a handoff to raw code or a
subroutine those fields kept their old values, so the post went on believing
things that might no longer be true. See docs/omniturn-post-rules.md,
"Handing control to code the post didn't write".
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, fields
from typing import Final, Literal


class Unknown(enum.Enum):
    """The type of ``UNKNOWN``: "the post doesn't know this".

    Why an enum with one member rather than ``None``: ``None`` already means
    "not given" all over the post (``rapid(x=None)`` means "don't move X"). A
    separate value can't be confused with that, and a type checker can tell
    ``float | Unknown`` from ``float | None``.

    ``__bool__`` raises, on purpose. Python normally treats any object as true
    in ``if state.coolant:``, which is exactly the bug this module fixes: an
    unknown coolant state would quietly count as "on". Making it an error
    forces every check to say what it means (``is UNKNOWN``, ``== "m08"``).
    This is the "fail loudly" practice: a mistake that raises is found in the
    test suite; a mistake that guesses is found at the machine.
    """

    UNKNOWN = "unknown"

    def __bool__(self) -> bool:
        raise TypeError("machine state is UNKNOWN: compare it with `is UNKNOWN` or `==`, don't test its truth")

    def __repr__(self) -> str:
        return "UNKNOWN"


# `Final` tells a type checker (and the reader) that this name is never rebound.
# There is exactly one UNKNOWN, so `x is UNKNOWN` is always the right test.
UNKNOWN: Final = Unknown.UNKNOWN

# Field names grouped by what changes them. external.py says which groups a
# piece of raw code or a subroutine can change; MachineState.forget() takes them.
POSITION: Final = frozenset({"x", "z", "clear"})


@dataclass
class MachineState:
    """Everything the post relies on about the control, each field known or UNKNOWN.

    The string values are the G/M words themselves ("g95", "m08"), so the code
    that writes a mode and the code that checks it use the same spelling.
    ``Literal[...]`` in a type hint lists the only values allowed; the
    ``| Unknown`` part says UNKNOWN is allowed too.

    Fields start UNKNOWN: a new MachineState knows nothing. The post fills in
    what its program header establishes (``after_header``).
    """

    # Slide position, in the coordinates of the active tool: X diameter, Z.
    x: float | Unknown = UNKNOWN
    z: float | Unknown = UNKNOWN
    # True when the slide is known to be at the tool-change Z, clear of the
    # work, so a tool call (whose next line moves X and Z together, p2.9) is
    # safe. Known three ways: the post moved there itself; the program has
    # just started (the operator starts every program from there); or the
    # operator declared that raw code / a subroutine ends there. A bool and
    # not UNKNOWN because "not known to be clear" and "unknown" are the same
    # thing here: either way the post may not make a tool-call move.
    clear: bool = False
    motion: Literal["rapid", "feed"] | Unknown = UNKNOWN  # G00 / G01 modal
    feed_mode: Literal["g94", "g95"] | Unknown = UNKNOWN  # per minute / per rev
    feed: float | Unknown = UNKNOWN  # the modal F value
    units: Literal["g70", "g71"] | Unknown = UNKNOWN  # inch / metric
    coords: Literal["g90", "g91"] | Unknown = UNKNOWN  # absolute / incremental
    diameter: Literal["g72", "g73"] | Unknown = UNKNOWN  # X as diameter / radius
    spindle_mode: Literal["g96", "g97"] | Unknown = UNKNOWN  # CSS / rpm
    spindle: Literal["m03", "m04", "m05"] | Unknown = UNKNOWN
    rpm: float | Unknown = UNKNOWN  # the G97 S value
    coolant: Literal["m08", "m09"] | Unknown = UNKNOWN
    comp: Literal["g40", "g41", "g42"] | Unknown = UNKNOWN  # nose radius comp
    tool: int | Unknown = UNKNOWN

    @property
    def position_known(self) -> bool:
        """True when both X and Z are known numbers."""
        return self.x is not UNKNOWN and self.z is not UNKNOWN

    def forget(self, names) -> None:
        """Mark the named fields UNKNOWN (``clear`` becomes False).

        Forgetting is the only thing the handoff code does to the state: it
        never writes a guessed value, so a mistake in it can make the post
        write a redundant line but can't make it trust something false.

        Raises KeyError for a name that isn't a field, so a typo in a group
        can't silently forget nothing.
        """
        valid = {f.name for f in fields(self)}
        for name in names:
            if name not in valid:
                raise KeyError(f"MachineState has no field {name!r}")
            setattr(self, name, False if name == "clear" else UNKNOWN)

    def forget_everything(self) -> None:
        """Know nothing at all (as after code the post can't read)."""
        self.forget(f.name for f in fields(self))

    def unknown_fields(self) -> list[str]:
        """Names of the fields that are UNKNOWN, for messages and tests."""
        return [f.name for f in fields(self) if getattr(self, f.name) is UNKNOWN]
