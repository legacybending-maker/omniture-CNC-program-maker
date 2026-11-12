"""Check an OmniTurn program against the formatting rules in the operator manual.

Works on any program, hand-written or generated:  omniturn-cam lint FILE
"""
from __future__ import annotations

import re
from dataclasses import dataclass

ONE_SHOT_G = {"02", "03", "04", "33", "34", "81", "83", "92"}
KNOWN_G = {"00", "01", "02", "03", "04", "10", "33", "34", "40", "41", "42", "70", "71", "72",
           "73", "74", "75", "76", "77", "78", "81", "83", "90", "91", "92", "94", "95", "96", "97"}


@dataclass
class Problem:
    line: int
    severity: str   # "error" | "warning"
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
    defined_subs: set[str] = set()
    called_subs: list[tuple[int, str]] = []
    expect_xz_after_tool: int | None = None
    in_sub = False

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
            if in_sub:
                add(i, "warning", "avoid comments inside loops and subroutines (p2.6)")
        code_l = code.lower().replace(" ", "").lstrip("/")

        if code_l.startswith("}"):
            m = re.fullmatch(r"\}(\d+)", code_l)
            if not m:
                add(i, "error", "subroutine start must be '}n' on its own line (p2.55)")
            else:
                defined_subs.add(m.group(1))
            in_sub = True
            continue
        if code_l == "m99":
            in_sub = False
            continue
        if "m99" in code_l:
            add(i, "error", "m99 must be alone on its line (p2.55)")
        if code_l == "rf" or not code_l:
            continue

        if expect_xz_after_tool is not None:
            if not ("x" in code_l and "z" in code_l):
                add(i, "error", f"the line after the tool call on line {expect_xz_after_tool} "
                                "must have both X and Z (p2.9)")
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

        tm = re.match(r"t(\d+)", code_l)
        if tm:
            t = int(tm.group(1))
            if not 1 <= t <= 32:
                add(i, "error", f"tool number t{t} out of range 1-32 (p2.9)")
            if code_l != tm.group(0):
                add(i, "warning", "put nothing but the tool number and a comment on a tool call line")
            expect_xz_after_tool = i

        for call in re.findall(r"(?:m98|g75|g78|m97)[^p]*p(\d+)", code_l):
            called_subs.append((i, call))

    for i, s in called_subs:
        if s not in defined_subs:
            add(i, "error", f"subroutine {s} is called but never defined")
    return probs
