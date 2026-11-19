"""G-code text helpers shared by the post and the operations: number format,
comment rules, how a program line and a subroutine declaration are read, and
the error the post raises when it refuses to write code."""

from __future__ import annotations

import re
from collections.abc import Iterator

# ------------------------------------------------------------------ reading lines
# Every part of the program that *reads* G-code (lint, the backplot, the
# include-file reader in job.py, the handoff analyser in post/external.py)
# goes through code_part() and subroutine_start(). They used to each have their
# own regex, and they disagreed: lint stripped comments before looking for
# `}n`, job.py didn't, so `}1 (LOADER)` was subroutine 1 to one and nothing to
# the other (issue #36). One definition, used everywhere, is the cure for
# "two parsers drift apart": the rule lives in one place and a fix reaches
# every reader at once.

_COMMENT = re.compile(r"\([^)]*\)")
# `}` then digits, and nothing else once comments and spaces are gone (p2.55:
# "}n begins subroutine n"). fullmatch() below, not match(): `}1x` must not
# count as subroutine 1.
_SUB_START = re.compile(r"\}(\d+)")


def code_part(line: str) -> str:
    """The code of one program line, the way the control reads it.

    Comments in parentheses removed (p2.6: a comment may sit on any line),
    lower case, spaces removed, and a leading ``/`` (block skip) removed: a
    skipped line still means the same thing when it runs. Returns "" for a
    line that is only a comment or blank.
    """
    return _COMMENT.sub("", line).lower().replace(" ", "").lstrip("/")


def subroutine_start(code: str) -> int | None:
    """The subroutine number if ``code`` (a ``code_part()``) declares one, else None.

    The number is returned as an ``int``, so ``}01`` and ``}1`` are the same
    subroutine, and so are ``m98p01`` and ``m98p1``. Comparing the digits as
    text made them different (issue #36); every reader now compares numbers.
    Whether the control itself treats ``}01`` as subroutine 1 isn't in the
    manual (**VERIFY**), so lint also warns about a leading zero.

    A line starting with ``}`` that isn't exactly ``}`` + digits returns None:
    the caller decides whether that's an error (lint) or just not a
    declaration. Malformed declarations are never guessed at.
    """
    m = _SUB_START.fullmatch(code)
    return int(m.group(1)) if m else None


def subroutine_numbers(text: str) -> Iterator[int]:
    """Every subroutine number declared in ``text``, in order, duplicates kept
    (a caller that wants to report duplicates needs to see them).

    A *generator* (``yield`` instead of building a list and ``return``-ing
    it): it hands back one number at a time as the caller asks for them.
    Wrap it in ``list()`` or ``set()`` to get them all at once.
    """
    for line in text.splitlines():
        n = subroutine_start(code_part(line))
        if n is not None:
            yield n


def fmt(v: float, decimals: int) -> str:
    """OmniTurn decimal-point format: no leading/trailing zeros, '0' for zero."""
    s = f"{round(v, decimals):.{decimals}f}".rstrip("0").rstrip(".")
    if s in ("", "-0", "-"):
        return "0"
    if s.startswith("0."):
        s = s[1:]
    elif s.startswith("-0."):
        s = "-" + s[2:]
    return s


def sanitize_comment(text: str, max_len: int = 32) -> str:
    """Comments may hold only letters, digits and spaces (manual p2.6)."""
    clean = re.sub(r"[^A-Za-z0-9 ]+", " ", text)
    clean = re.sub(r"\s+", " ", clean).strip().upper()
    return clean[:max_len].rstrip()


class PostError(ValueError):
    pass
