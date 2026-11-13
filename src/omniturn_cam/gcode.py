"""G-code text helpers shared by the post and the operations: number format,
comment rules, and the error the post raises when it refuses to write code."""

from __future__ import annotations

import re


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
