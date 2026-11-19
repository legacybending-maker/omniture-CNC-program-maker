"""Check an OmniTurn program against the formatting rules in the operator manual.

Works on any program, hand-written or generated:  omniturn-cam lint FILE
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .gcode import code_part, subroutine_start

ONE_SHOT_G = {"02", "03", "04", "33", "34", "81", "83", "92"}
KNOWN_M = {
    f"{n:02d}"
    for n in [
        0,
        1,
        2,
        3,
        4,
        5,
        8,
        9,
        10,
        11,
        12,
        13,
        17,
        18,
        19,
        21,
        23,
        27,
        30,
        32,
        33,
        38,
        *range(40, 60),
        60,
        61,
        62,
        64,
        65,
        84,
        85,
        86,
        87,
        91,
        92,
        93,
        94,
        95,
        97,
        98,
        99,
    ]
}
KNOWN_G = {
    "00",
    "01",
    "02",
    "03",
    "04",
    "10",
    "33",
    "34",
    "40",
    "41",
    "42",
    "70",
    "71",
    "72",
    "73",
    "74",
    "75",
    "76",
    "77",
    "78",
    "81",
    "83",
    "90",
    "91",
    "92",
    "94",
    "95",
    "96",
    "97",
}


@dataclass
class Problem:
    line: int
    severity: str  # "error" | "warning"
    message: str

    def __str__(self):
        return f"line {self.line}: {self.severity}: {self.message}"


def split_comment(line: str) -> tuple[str, list[str]]:
    comments = re.findall(r"\(([^)]*)\)", line)
    code = re.sub(r"\([^)]*\)", "", line)
    return code, comments


def lint(program: str, filename: str | None = None) -> list[Problem]:
    probs: list[Problem] = []
    add = lambda n, s, m: probs.append(Problem(n, s, m))

    if filename is not None:
        stem = filename.rsplit("/", 1)[-1]
        base, _, ext = stem.partition(".")
        if len(base) > 8:
            add(0, "error", f"file name {base!r} is longer than 8 characters (p2.8)")
        if ext and ext.lower() not in ("txt", "cnc"):
            add(0, "error", f"extension .{ext} not recognised; use .txt, .cnc or none (p2.8)")

    lines = program.splitlines()
    # Subroutine numbers are ints (gcode.subroutine_start), so `}01` and
    # `m98p1` refer to the same subroutine here, as in the post and the
    # backplot. They used to be compared as text (issue #36).
    defined_subs: set[int] = set()
    called_subs: list[tuple[int, int]] = []
    expect_xz_after_tool: int | None = None
    # The line of the }n whose m99 hasn't come yet; None outside a subroutine.
    open_sub_line: int | None = None

    for i, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line:
            continue
        if "," in line:
            add(i, "error", "commas are not allowed anywhere in the program (p2.6)")
        if line.count("(") != line.count(")"):
            add(i, "error", "unbalanced parentheses")
        code, comments = split_comment(line)
        for c in comments:
            if re.search(r"[^A-Za-z0-9 ]", c):
                add(i, "warning", "comments should hold only letters digits and spaces (p2.6)")
            if len(c) > 40:
                add(i, "warning", "long comment; keep comments short (p2.6)")
            if open_sub_line is not None:
                add(i, "warning", "avoid comments inside loops and subroutines (p2.6)")
        code_l = code_part(line)
        _check_characters(code_l, i, add)

        if code_l.startswith("}"):
            num = subroutine_start(code_l)
            if num is None:
                add(i, "error", "subroutine start must be '}n' on its own line (p2.55)")
            else:
                if open_sub_line is not None:
                    add(
                        i,
                        "error",
                        f"subroutine {num} starts before the subroutine on line {open_sub_line} ends with m99 (p2.55)",
                    )
                if re.fullmatch(r"\}0\d+", code_l):  # }01, not }0 itself
                    add(i, "warning", f"write }}{num}, not {code_l}: a leading zero isn't in the manual (p2.55)")
                if num in defined_subs:
                    add(
                        i,
                        "error",
                        f"subroutine {num} is defined twice - check pasted / included subroutine files",
                    )
                defined_subs.add(num)
            open_sub_line = i
            continue
        if code_l == "m99":
            if open_sub_line is None:
                # Whether a bare M99 in the main program does anything on the
                # G4 isn't in the manual (VERIFY). The post never writes one.
                add(i, "warning", "m99 with no subroutine open: m99 ends a subroutine (p2.55)")
            open_sub_line = None
            continue
        if "m99" in code_l:
            add(i, "error", "m99 must be alone on its line (p2.55)")
        if code_l == "rf" or not code_l:
            continue

        if expect_xz_after_tool is not None:
            if not ("x" in code_l and "z" in code_l):
                add(
                    i,
                    "error",
                    f"the line after the tool call on line {expect_xz_after_tool} must have both X and Z (p2.9)",
                )
            expect_xz_after_tool = None

        if "o" in (code_l[:-1] if code_l.startswith("g33") and code_l.endswith("o") else code_l):
            add(i, "error", "letter O found in code; use zero (p2.6). Only a G33 single pass ends in O")
        if re.search(r"(^|[^a-z])n\d", code_l):
            add(i, "error", "N sequence numbers are not allowed (p2.6)")

        gcodes = re.findall(r"g(\d+(?:\.\d+)?)", code_l)
        for g in gcodes:
            if len(g) != 2:
                add(i, "error", f"g{g}: G codes must be two digits (p2.6)")
            elif g not in KNOWN_G:
                add(i, "warning", f"g{g} is not in the OmniTurn code list")
        if sum(g in ONE_SHOT_G for g in gcodes) > 1:
            add(i, "error", "only one one-shot G code per line (p2.6)")
        mcodes = re.findall(r"m(\d+)", code_l)
        for mc in mcodes:
            if len(mc) != 2:
                add(i, "error", f"m{mc}: M codes must be two digits (p2.6)")
        if len(mcodes) > 1:
            add(i, "error", "only one M code per line (p2.6)")
        for mc in mcodes:
            if len(mc) == 2 and mc not in KNOWN_M:
                add(i, "warning", f"m{mc} is not in the OmniTurn code list")
        if "m95" in code_l:
            called_subs.append((i, 1))  # M95 jumps to subroutine 1 on the loader input

        tm = re.match(r"t(\d+)", code_l)
        if tm:
            t = int(tm.group(1))
            if not 1 <= t <= 32:
                add(i, "error", f"tool number t{t} out of range 1-32 (p2.9)")
            if code_l != tm.group(0):
                add(i, "warning", "put nothing but the tool number and a comment on a tool call line")
            expect_xz_after_tool = i

        for call in re.findall(r"(?:m98|g75|g78|m97)[^p]*p(\d+)", code_l):
            if call.startswith("0") and len(call) > 1:
                add(i, "warning", f"write p{int(call)}, not p{call}: a leading zero isn't in the manual (p2.55)")
            called_subs.append((i, int(call)))

    if open_sub_line is not None:
        add(open_sub_line, "error", "this subroutine never ends: m99 must end every subroutine (p2.55)")
    for i, num in called_subs:
        if num not in defined_subs:
            add(i, "error", f"subroutine {num} is called but never defined")
    return probs


# A word is one letter and the number after it (p2.6). The number part is
# matched loosely here ([-+.0-9]*) so a malformed one like "x1.2.3" or "f-"
# is *seen* and reported, rather than being split up and half-read.
_WORD = re.compile(r"([a-z])([-+.0-9]*)")
# What a well-formed number looks like: optional minus, then digits with at
# most one decimal point (".5", "5", "5.", "-.25").
_NUMBER = re.compile(r"-?(\d+\.?\d*|\.\d+)")
# Letters that always carry a number. Left out on purpose, because the manual
# uses them bare: P at the end of a move (spindle sync, p2.11, p2.30), O at
# the end of a G33 (single pass, 2.23-2.29), C alone in a G33 (29 degree
# infeed), RF and LF (contour and loop end), LS (loop start).
_NEEDS_NUMBER = set("dgikmstuwxz")


def _check_characters(code: str, line: int, add) -> None:
    """Refuse code the control can't read: characters that aren't part of a
    word, numbers that don't parse, and negative F or S.

    The backplot and the post's handoff reader skip anything that isn't a
    letter-number word, so without this check a stray character or a typo'd
    number would be silently ignored by the readers and be someone else's
    surprise at the machine (issue #36). ``add`` is lint's own reporting
    function, passed in so this helper doesn't need to know about the
    problem list (a small example of *dependency injection*).
    """
    if not code or code in ("rf", "lf") or code.startswith("}"):
        return
    for ch in sorted(set(code)):
        if not ch.isascii():
            add(line, "error", f"character {ch!r} isn't ASCII; the control can't read it")
    words = _WORD.findall(code)
    leftover = _WORD.sub("", code)
    if leftover:
        shown = " ".join(sorted({repr(c) for c in leftover if c.isascii()}))
        if shown:
            add(line, "error", f"{shown} isn't part of any OmniTurn word (a letter and its number, p2.6)")
    for letter, num in words:
        if not num:
            if letter in _NEEDS_NUMBER and not (letter == "s" and code.startswith("ls")):
                add(line, "error", f"{letter.upper()} has no number")
            continue
        if not _NUMBER.fullmatch(num):
            add(line, "error", f"{letter.upper()}{num}: can't read {num!r} as a number")
        elif letter in "fs" and num.startswith("-"):
            add(line, "error", f"{letter.upper()}{num}: a feed or spindle speed can't be negative")
