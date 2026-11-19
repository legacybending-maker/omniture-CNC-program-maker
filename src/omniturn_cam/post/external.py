"""What raw program lines or a subroutine could change, read from their text.

Raw code and M98 subroutine calls run code the post didn't write. Afterwards
the post has to forget whatever that code might have changed. This module
reads the code and answers one question: **which parts of MachineState could
it have changed?** It answers with field names to *forget*, never with new
values. It does not work out where the slide ended up, what the spindle is
doing now, or anything else positive. That keeps it safe even when it's
incomplete: the worst a gap in this table can do is make the post forget
something, and forgetting only costs a restated line (or a refusal), never a
wrong assumption. Any word it doesn't recognise makes it forget everything.

The table below is the only place the post decides what a G or M code can
affect. Each entry comes from the manual's code lists (p2.1-2.2, 2.6) and the
loader/PLC page (p2.54-2.56); see docs/omniturn-post-rules.md.

Repetition can't add effects: a loop (LS/LF, p2.20) or a block-skip line runs
some number of times, but "may change X" is the same after one pass or ten,
and the same if the line was skipped. So loops and skipped lines are read
once, like any other line.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, fields

from ..gcode import code_part, subroutine_start
from .state import POSITION, MachineState

# Every field of MachineState: "forget everything".
EVERYTHING = frozenset(f.name for f in fields(MachineState))

# What each G code can change (two-digit, as the control requires, p2.6).
# A code that's missing here makes the analysis forget everything.
G_EFFECTS: dict[str, frozenset[str]] = {
    # Rapid / feed modal. G00/G01 alone don't move; the axis words do.
    "00": frozenset({"motion"}),
    "01": frozenset({"motion"}),
    # One-shot moves and canned cycles: the slide moves and the motion modal
    # after them isn't something the post wants to rely on.
    **{g: frozenset({"motion"}) | POSITION for g in ("02", "03", "33", "34", "74", "81", "83")},
    # G04 dwell takes its time in the F word (p2.20), so the modal F is gone.
    "04": frozenset({"feed"}),
    "40": frozenset({"comp"}),
    "41": frozenset({"comp"}),
    "42": frozenset({"comp"}),
    "70": frozenset({"units"}),
    "71": frozenset({"units"}),
    "72": frozenset({"diameter"}),
    "73": frozenset({"diameter"}),
    "90": frozenset({"coords"}),
    "91": frozenset({"coords"}),
    "94": frozenset({"feed_mode"}),
    "95": frozenset({"feed_mode"}),
    # Switching CSS on or off changes what the S word means, so the rpm the
    # post remembers stops being the rpm the spindle is running at.
    "96": frozenset({"spindle_mode", "rpm"}),
    "97": frozenset({"spindle_mode", "rpm"}),
    # G76/G77 are the CSS min/max rpm limits. MachineState doesn't track them
    # (see "Remaining limitations" in the post rules), so nothing to forget.
    "76": frozenset(),
    "77": frozenset(),
    # Deliberately absent: G75/G78 (they call a contour subroutine by P, which
    # could be anything), G10, G92 and any code not in the manual's list.
}

# M codes that can't change anything MachineState tracks: loader functions,
# input waits, PLC outputs, collet, override lock (p2.54-2.56, "Program end").
_NO_TRACKED_EFFECT_M = {
    "10",
    "11",
    "12",
    "13",
    "32",
    "33",
    "38",
    *(f"{n:02d}" for n in range(40, 60)),  # M40-M59 loader functions
    *(f"{n:02d}" for n in range(60, 66) if n != 63),  # M60-M65 PLC outputs (no M63 in the list)
    "84",
    "85",
    "91",
    "92",
    "93",
    "94",
}
M_EFFECTS: dict[str, frozenset[str]] = {
    "03": frozenset({"spindle", "rpm"}),
    "04": frozenset({"spindle", "rpm"}),
    "05": frozenset({"spindle", "rpm"}),
    "08": frozenset({"coolant"}),
    "09": frozenset({"coolant"}),
    **{m: frozenset() for m in _NO_TRACKED_EFFECT_M},
    # Deliberately absent, so they forget everything:
    #   M00/M01 stop: the operator may jog or switch things at the panel while
    #     the program is stopped (VERIFY what the G4 allows during a stop).
    #   M02/M30 end, M95/M97 jumps, M99 return: control goes somewhere else.
    #   M98 is handled below: the subroutine's own text is read if the post has it.
}

# Address letters that are arguments of a move or a cycle. Seen anywhere,
# they mean the slide may move.
_MOTION_LETTERS = set("xzuikrcaolp")
LETTER_EFFECTS: dict[str, frozenset[str]] = {
    **{c: POSITION for c in _MOTION_LETTERS},
    "f": frozenset({"feed"}),
    "s": frozenset({"rpm"}),
    # A tool call applies new offsets and must be followed by an X and Z move (p2.9).
    "t": frozenset({"tool"}) | POSITION,
    # A D word adds a secondary offset to moves (p2.59) and turns comp on (p2.31).
    "d": frozenset({"comp"}) | POSITION,
}

# A word is a letter and an optional number, e.g. g01, x-.5, p, t12.
_WORD = re.compile(r"([a-z])([-+]?[0-9]*\.?[0-9]*)")
_LOOP = re.compile(r"ls\d*|lf")  # LSn / LF loop start and finish (p2.20-2.21)


@dataclass(frozen=True)
class Effects:
    """The result of reading some external code.

    ``forget``: MachineState field names the code could have changed.
    ``reason``: when the slide may have moved, the first reason why, in words
    the operator can act on (e.g. "line 'x0z-1' moves the slide"). Empty when
    the code can't move the slide.

    ``frozen=True`` makes instances read-only, so a result can be passed
    around without anyone changing it on the way.
    """

    forget: frozenset[str] = frozenset()
    reason: str = ""

    @property
    def moves_slide(self) -> bool:
        return bool(self.forget & POSITION)

    def __or__(self, other: Effects) -> Effects:
        """``a | b``: everything either could change; the first reason found."""
        return Effects(self.forget | other.forget, self.reason or other.reason)


NONE = Effects()


def _all(reason: str) -> Effects:
    return Effects(EVERYTHING, reason)


# The code part of a line, read the same way lint and the backplot read it
# (gcode.code_part: no comments, lower case, no spaces, no block skip).
_clean = code_part


def subroutine_bodies(text: str) -> dict[int, list[str]]:
    """Map subroutine number -> its lines, from a subroutine file (``}n`` ... ``m99``).

    Lines outside any subroutine are ignored. A number defined twice keeps the
    first body; Job.issues and the linter report the duplicate (p2.55).
    Declarations are read with gcode.subroutine_start, so ``}1 (LOADER)`` is
    subroutine 1 here as everywhere else (issue #36).
    """
    bodies: dict[int, list[str]] = {}
    current: list[str] | None = None
    for line in text.splitlines():
        num = subroutine_start(_clean(line))
        if num is not None:
            current = []
            bodies.setdefault(num, current)
            continue
        if current is not None:
            if _clean(line) == "m99":
                current = None
            else:
                current.append(line)
    return bodies


def analyse(
    lines: Iterable[str], subs: Mapping[int, list[str]] | None = None, _calling: frozenset[int] = frozenset()
) -> Effects:
    """What ``lines`` could change. ``subs`` are the subroutines the post can
    read (the job's include file); an M98 to one of them is read too, an M98
    to anything else forgets everything.

    ``_calling`` holds the subroutines already being read, so a subroutine
    that calls itself (directly or round a loop) forgets everything instead of
    recursing forever. The leading underscore marks it as internal: callers
    don't pass it.
    """
    subs = subs or {}
    result = NONE
    for raw in lines:
        code = _clean(raw)
        if not code or _LOOP.fullmatch(code):
            continue
        result = result | _line_effects(code, raw.strip(), subs, _calling)
        if result.forget == EVERYTHING:
            break  # nothing more to learn
    return result


def _line_effects(code: str, shown: str, subs: Mapping[int, list[str]], calling: frozenset[int]) -> Effects:
    words = _WORD.findall(code)
    # findall skips characters that aren't part of a word. If what it found
    # doesn't add back up to the whole line, something in the line isn't a
    # letter-number word (a '}' or stray symbol): don't try to understand it.
    if "".join(a + n for a, n in words) != code:
        return _all(f"line {shown!r} isn't plain G-code words")
    if code == "rf":
        return _all("line 'rf' ends a contour, which only belongs after a G75/G78 line")
    effects = NONE
    moves = Effects(POSITION, f"line {shown!r} moves the slide")
    for i, (letter, num) in enumerate(words):
        if letter == "g":
            eff = G_EFFECTS.get(num)
            if eff is None:
                return _all(f"line {shown!r}: g{num} isn't one the post can account for")
            effects = effects | (moves if eff & POSITION else NONE) | Effects(eff)
        elif letter == "m":
            if num == "98":
                effects = effects | _call(words[i + 1 :], shown, subs, calling)
                break  # the P word belonged to the call
            eff = M_EFFECTS.get(num)
            if eff is None:
                return _all(f"line {shown!r}: m{num} hands control on or stops the program")
            effects = effects | Effects(eff)
        elif letter in LETTER_EFFECTS:
            eff = LETTER_EFFECTS[letter]
            effects = effects | (moves if eff & POSITION else NONE) | Effects(eff)
        else:
            return _all(f"line {shown!r}: the post doesn't know what {letter.upper()} does")
    return effects


def _call(rest: list[tuple[str, str]], shown: str, subs: Mapping[int, list[str]], calling: frozenset[int]) -> Effects:
    """Effects of an M98 call: the words after ``m98`` must be exactly ``p<n>``."""
    if len(rest) != 1 or rest[0][0] != "p" or not rest[0][1].isdigit():
        return _all(f"line {shown!r}: M98 needs exactly one P number")
    n = int(rest[0][1])
    if n in calling:
        return _all(f"subroutine {n} calls itself")
    if n not in subs:
        return _all(f"subroutine {n} isn't in the job's include file, so the post can't read it")
    inner = analyse(subs[n], subs, calling | {n})
    if inner.reason:
        inner = Effects(inner.forget, f"{inner.reason} (in subroutine {n})")
    return inner
