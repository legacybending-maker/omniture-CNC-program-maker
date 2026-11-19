"""Read an OmniTurn program back and draw it: rapids, feeds, arcs, C/R corners,
and the contour each G75/G78 cycle roughs to. Interpreting the output (rather
than drawing from the job) means the plot shows what the machine will be told.

The interpreter is not only for pictures. ``post()`` runs it on every
generated program for the travel check (``OmniTurnPost.check_travel``), and
``omniturn-cam plot`` runs it on hand-written files. So it never guesses:

* A line it can't read (a stray character, a number like ``x1.2.3``, a G code
  that isn't two digits) raises ``ProgramError`` with the line number.
* A move is drawn only from a position the program has actually given. The
  first X and Z just *set* the position; nothing is drawn from "unknown".
* Arcs are drawn exactly as written: from the start, around the centre the R
  or the I/K words give. An arc whose words don't make a possible arc (the R
  can't reach, the I/K centre isn't the same distance from both ends) is a
  ``ProgramError``, judged with a tolerance worked out from the program's
  precision: the most a number written to that many decimals can be off by
  (see ``_precision``).
* Code that may be valid but that this reader can't follow (a move made in
  G91 incremental or G73 radius mode, a negative R) raises ``UnsupportedProgram``, a kind of
  ProgramError, so the travel check can say "not checked" instead of refusing
  a legal program, while a broken one is still refused.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from .gcode import code_part, subroutine_start
from .geometry import Arc, ArcDir, Line, Point, expand_moves


class ProgramError(ValueError):
    """The program can't be interpreted because something in it is wrong.

    ``line`` is the 1-based line number in the program text (0 when the
    problem isn't on one line). A ValueError subclass, so code that already
    catches ValueError (the GUI, the CLI) handles it without changes.
    """

    def __init__(self, line: int, message: str):
        self.line = line
        super().__init__(f"line {line}: {message}" if line else message)


class UnsupportedProgram(ProgramError):
    """Code that may well be valid, but that this interpreter doesn't follow.

    A subclass of ProgramError, so ``except ProgramError`` still catches it
    and a caller that doesn't care about the difference doesn't have to. The
    travel check does care: it warns for this one and refuses for the rest.
    """


# A word is one letter and its number (p2.6). The number is matched loosely
# here so a malformed one is seen whole and refused, not split and half-read.
_WORD = re.compile(r"([a-z])([-+.0-9]*)")
_NUMBER = re.compile(r"-?(\d+\.?\d*|\.\d+)")
# Lines that mark something rather than move: RF ends an inline G75/G78
# contour (2.41), LSn and LF start and finish a loop (2.20-2.21). Their
# letters carry no number, so they mustn't be read as an R, F, L or S word.
_MARKER = re.compile(r"rf|lf|ls\d*")


@dataclass
class _Block:
    """One program line, read: its line number and its words in order.

    A small class instead of the bare list of (letter, number) pairs the
    interpreter used before, so "is this word here?" and "what's its value?"
    are asked one way everywhere, and a
    malformed number is reported with its line instead of raising a bare
    ``ValueError: could not convert string to float``.
    """

    line: int
    words: list[tuple[str, str]]
    code: str = ""  # the line's code_part(), for recognising whole-line markers

    @property
    def marker(self) -> bool:
        """RF, LF or LSn: a line that marks a place, with no move in it."""
        return bool(_MARKER.fullmatch(self.code))

    def has(self, letter: str) -> bool:
        return any(a == letter for a, _ in self.words)

    def text(self, letter: str) -> str | None:
        """The number as written after ``letter`` (last one wins), or None."""
        found = None
        for a, b in self.words:
            if a == letter:
                found = b
        return found

    def num(self, letter: str) -> float | None:
        """The value of ``letter``'s word, None if the line has no such word.
        Raises ProgramError if the word is there but its number isn't one."""
        t = self.text(letter)
        if t is None:
            return None
        if not _NUMBER.fullmatch(t):
            what = "has no number" if t == "" else f"can't be read as a number ({letter.upper()}{t})"
            raise ProgramError(self.line, f"{letter.upper()} {what}")
        return float(t)

    def gcodes(self) -> list[str]:
        return [b for a, b in self.words if a == "g"]


def parse(program: str) -> tuple[list[_Block], dict[int, list[_Block]]]:
    """Split a program into its main part and its subroutines.

    Returns (main blocks, {subroutine number: its blocks}). Subroutine
    declarations are read with gcode.subroutine_start, the same reader lint
    and the post use, so ``}1 (LOADER)`` is subroutine 1 and ``}01`` is too.

    Raises ProgramError for a line with a character that isn't part of a
    word, a malformed ``}`` line, a subroutine declared twice, one that starts
    before the last one ended, and one that never ends. (Lint reports the
    same things for a person to read; this raises because a program with any
    of them can't be drawn truthfully.)
    """
    main: list[_Block] = []
    subs: dict[int, list[_Block]] = {}
    cur: int | None = None
    cur_line = 0
    for n, raw in enumerate(program.splitlines(), 1):
        code = code_part(raw)
        if not code:
            continue
        if code.startswith("}"):
            num = subroutine_start(code)
            if num is None:
                raise ProgramError(n, f"{raw.strip()!r} isn't a subroutine start: write }}n (p2.55)")
            if cur is not None:
                raise ProgramError(n, f"subroutine {num} starts before subroutine {cur} ends with m99 (p2.55)")
            if num in subs:
                # Before #36 the second body silently replaced the first.
                raise ProgramError(n, f"subroutine {num} is defined twice (p2.55)")
            subs[num] = []
            cur, cur_line = num, n
            continue
        if code == "m99":
            # Outside a subroutine an M99 ends nothing; lint warns about it.
            cur = None
            continue
        words = _WORD.findall(code)
        leftover = _WORD.sub("", code)
        if leftover:
            raise ProgramError(n, f"can't read {raw.strip()!r}: {leftover!r} isn't part of any word")
        (subs[cur] if cur is not None else main).append(_Block(n, words, code))
    if cur is not None:
        raise ProgramError(cur_line, f"subroutine {cur} never ends: m99 must end it (p2.55)")
    return main, subs


@dataclass
class Path:
    kind: str  # "rapid" | "feed" | "contour" | "drill"
    tool: int | None
    start: Point
    segments: list = field(default_factory=list)
    lines: list[int] = field(default_factory=list)  # program line of each segment

    def points(self):
        return expand_moves(self.start, self.segments)


@dataclass(frozen=True)
class CenteredArc(Arc):
    """An arc drawn around the centre the program gave, not one recomputed
    from the radius.

    A *subclass* of geometry.Arc: everything that takes an Arc
    (expand_moves, the plot) takes this too, and only ``center()`` differs.
    Keeping the centre as written is what "don't approximate the arc" means:
    for an I/K arc the program states the centre, and for an R arc near a
    half circle a tiny rounding in R moves the recomputed centre a long way.
    """

    centre: tuple[float, float]  # (z, r), radius units like Arc.center()

    def center(self, start: Point, tol: float = 1e-9) -> tuple[float, float]:
        return self.centre


@dataclass
class _Position:
    """Where the slide is, as far as the program has said. x is a diameter;
    None means the program hasn't given it yet."""

    x: float | None = None
    z: float | None = None

    @property
    def known(self) -> bool:
        return self.x is not None and self.z is not None

    def point(self) -> Point:
        return Point(self.x, self.z)

    def moved_by(self, blk: _Block) -> _Position:
        """The position after ``blk``'s X and Z words (a missing one stays)."""
        x, z = blk.num("x"), blk.num("z")
        return _Position(self.x if x is None else x, self.z if z is None else z)


# The control's own resolution when a program doesn't say more: 4 decimals in
# inch (the axes resolve .0001 in on diameter, G4 Diagnostics; the post's
# `decimals`) and 3 in mm (the post's metric `decimals`).
_DEFAULT_DECIMALS = {"inch": 4, "metric": 3}
_SIZE_WORDS = set("xzikrc")  # the words whose rounding can bend geometry


def _precision(main: list[_Block], subs: dict[int, list[_Block]], decimals: int | None) -> float:
    """Half a step in the last decimal place: the most a coordinate, R, I or
    K can differ from the value it was rounded from.

    ``decimals`` is how many the program was written with; the post passes
    its machine's (MachineConfig.decimals). For a program from elsewhere it
    is the most decimals any size word in it has, and at least the control's
    own resolution for its units (G70/G71; inch if neither). You can't tell
    from one word: the OmniTurn format drops trailing zeros, so ``x.5`` in a
    4-decimal program means .5000, and ``z0`` means 0.0000 (gcode.fmt).
    """
    blocks = main + [b for body in subs.values() for b in body]
    if decimals is None:
        metric = any("71" in b.gcodes() for b in blocks)
        decimals = _DEFAULT_DECIMALS["metric" if metric else "inch"]
        for b in blocks:
            for letter, number in b.words:
                if letter in _SIZE_WORDS and "." in number:
                    decimals = max(decimals, len(number.split(".", 1)[1]))
    return 0.5 * 10.0**-decimals


def _arc(blk: _Block, start: _Position, end: _Position, direction: ArcDir, step: float) -> CenteredArc:
    """The arc ``blk`` asks for, from ``start`` to ``end``, or ProgramError.

    R format: the minor arc of radius R, like the post writes (p2.17).
    I/K format: centre = start + (I, K). Both I and K are incremental from
    the arc start, and I is a radial distance even in G72 diameter mode
    (p2.19), the same convention ``OmniTurnPost.arc_words`` writes.

    ``step`` is the rounding allowance of one written number (_precision).
    A point is off by at most hypot(step/2, step) in radius terms (X is a
    diameter, so its rounding counts half), and R, I and K by ``step`` each.
    The tolerances below add up the worst case of every number involved, so
    an arc that was possible before its numbers were rounded is accepted, and
    one that's wrong by more than rounding can explain is refused.
    """
    s, e = start.point(), end.point()
    chord = math.hypot(e.z - s.z, e.r - s.r)
    if chord == 0:
        raise ProgramError(blk.line, "arc ends where it starts (full circles aren't drawn)")
    has_r, has_ik = blk.has("r"), blk.has("i") or blk.has("k")
    if has_r and has_ik:
        raise ProgramError(blk.line, "arc has both R and I/K: give one or the other (p2.17-2.19)")
    point_err = math.hypot(step / 2, step)
    if has_r:
        r = blk.num("r")
        if r == 0:
            raise ProgramError(blk.line, "arc radius R0 is not an arc")
        if r < 0:
            raise UnsupportedProgram(blk.line, f"negative arc radius R{blk.text('r')} isn't drawn (VERIFY)")
        # chord moves by up to 2 * point_err, 2R by up to 2 * step
        tol = 2 * point_err + 2 * step
        try:
            centre = Arc(e, r, direction).center(s, tol)
        except ValueError:
            raise ProgramError(
                blk.line,
                f"arc R{r:g} can't reach: its ends are {chord:.5f} apart, more than 2R ({2 * r:g})",
            ) from None
        return CenteredArc(e, r, direction, centre)
    if not (blk.has("i") and blk.has("k")):
        raise ProgramError(blk.line, "arc needs R, or both I and K (write I0 or K0 for a zero offset, p2.19)")
    i, k = blk.num("i"), blk.num("k")
    cz, cr = s.z + k, s.r + i
    r_start = math.hypot(i, k)
    r_end = math.hypot(e.z - cz, e.r - cr)
    if r_start == 0:
        raise ProgramError(blk.line, "I0 K0 puts the arc centre on its start point")
    # r_start = |(I, K)| is off by up to hypot(step, step); r_end by that plus
    # both points' errors. Their difference, by the sum.
    tol = 2 * point_err + 2 * math.hypot(step, step)
    if abs(r_end - r_start) > tol:
        raise ProgramError(
            blk.line,
            f"the I/K centre is {r_start:.5f} from the arc start but {r_end:.5f} from its end: "
            "no arc joins them (I and K are incremental from the start; I is radial, p2.19)",
        )
    return CenteredArc(e, r_start, direction, (cz, cr))


def _line_to(blk: _Block, end: Point, radius_allowed: bool = True) -> Line:
    """A straight move to ``end`` with the line's C (chamfer) or R (radius)
    corner, if any (p2.14). Refuses both at once, or a negative size, with the
    line number instead of letting them reach the corner arithmetic."""
    c = blk.num("c") or None
    r = (blk.num("r") or None) if radius_allowed else None
    if c and r:
        raise ProgramError(blk.line, "a corner can have C or R, not both (p2.14)")
    if (c or 0) < 0 or (r or 0) < 0:
        raise ProgramError(blk.line, "a C or R corner size can't be negative (p2.14)")
    return Line(end, c, r)


def _check_gcodes(blk: _Block) -> None:
    """G codes are two digits (p2.6). A one-digit ``g2`` would otherwise not
    be recognised as an arc and be drawn as a straight line."""
    for g in blk.gcodes():
        if len(g) != 2:
            raise ProgramError(blk.line, f"g{g}: G codes must be two digits (p2.6)")


@dataclass
class _Modes:
    """The modes that change what an X or Z *means*. The interpreter reads X
    and Z as absolute (G90) diameters (G72); while G91 (incremental) or G73
    (radius mode) is in effect it can't place a move, so a move then is
    UnsupportedProgram. Tracked as modes, not refused on sight, so raw code
    that switches to G91 and back before moving draws normally."""

    incremental: bool = False
    radius: bool = False

    def update(self, blk: _Block) -> None:
        gs = blk.gcodes()
        if "91" in gs:
            self.incremental = True
        if "90" in gs:
            self.incremental = False
        if "73" in gs:
            self.radius = True
        if "72" in gs:
            self.radius = False

    def check_move(self, blk: _Block) -> None:
        if self.incremental:
            raise UnsupportedProgram(blk.line, "a move in G91 (incremental): the backplot reads X and Z as absolute")
        if self.radius:
            raise UnsupportedProgram(blk.line, "a move in G73 (radius mode): the backplot reads X as a diameter")


def interpret(program: str, arc_codes=None, decimals: int | None = None) -> list[Path]:
    """Turn a program into drawable paths. ``arc_codes`` maps "ccw"/"cw" to
    the G codes for them (MachineConfig.arc_codes; print-CCW = G02 on the
    OmniTurn, docs/omniturn-post-rules.md). ``decimals`` is how many decimal
    places the program's sizes were written with (MachineConfig.decimals);
    None works it out from the program (_precision).

    Raises ProgramError (or its subclass UnsupportedProgram) for anything it
    can't draw truthfully; see the module docstring.
    """
    arc_dir = {v.lstrip("g"): k for k, v in (arc_codes or {"ccw": "g02", "cw": "g03"}).items()}
    main, subs = parse(program)
    step = _precision(main, subs, decimals)
    paths: list[Path] = []
    pos = _Position()
    modes = _Modes()
    motion = "rapid"
    tool = None
    current: Path | None = None

    def arc_code(blk: _Block) -> str | None:
        codes = [c for c in blk.gcodes() if c in arc_dir]
        if len(codes) > 1:
            raise ProgramError(blk.line, "two arc directions on one line (one-shot G codes, p2.6)")
        return codes[0] if codes else None

    def contour_path(num: int, call: _Block) -> Path:
        """The contour a G75/G78 roughs to: subroutine ``num``'s moves.

        Lines with no X or Z (an M10, a G code on its own) carry no position
        and are passed over; the contour starts at the first line that gives
        both X and Z. Before #36 a first line like ``m10`` became
        ``Point(None, None)`` and crashed later arithmetic.
        """
        if num not in subs:
            raise ProgramError(call.line, f"G75/G78 P{num}: subroutine {num} isn't in the program")
        cpos = _Position()
        cmodes = _Modes(modes.incremental, modes.radius)  # the contour runs in the caller's modes
        p: Path | None = None
        for blk in subs[num]:
            if blk.marker:
                continue
            _check_gcodes(blk)
            cmodes.update(blk)
            if not (blk.has("x") or blk.has("z")):
                continue
            cmodes.check_move(blk)
            nxt = cpos.moved_by(blk)
            code = arc_code(blk)
            if p is None:
                if not nxt.known:
                    raise ProgramError(
                        blk.line,
                        f"subroutine {num}'s first move gives only {'X' if blk.has('x') else 'Z'}: "
                        "a contour must start with both X and Z",
                    )
                if code:
                    raise ProgramError(blk.line, f"subroutine {num} starts with an arc: it has no start point")
                p = Path("contour", tool, nxt.point(), lines=[])
            elif code:
                p.segments.append(_arc(blk, cpos, nxt, arc_dir[code], step))
                p.lines.append(blk.line)
            else:
                p.segments.append(_line_to(blk, nxt.point()))
                p.lines.append(blk.line)
            cpos = nxt
        if p is None:
            raise ProgramError(call.line, f"G75/G78 P{num}: subroutine {num} has no X/Z moves to make a contour from")
        return p

    for blk in main:
        if blk.marker:
            # A loop body is drawn once; the moves are the same each time round.
            continue
        _check_gcodes(blk)
        modes.update(blk)
        gs = blk.gcodes()
        if blk.has("t"):
            t = blk.num("t")
            if t != int(t):
                raise ProgramError(blk.line, f"T{blk.text('t')}: tool numbers are whole (p2.9)")
            tool = int(t)
            current = None
            continue
        if "75" in gs or "78" in gs:
            modes.check_move(blk)
            if blk.has("p"):
                p_num = blk.num("p")
                if p_num != int(p_num) or p_num < 0:
                    raise ProgramError(blk.line, f"P{blk.text('p')}: a subroutine number is a whole number (p2.55)")
                paths.append(contour_path(int(p_num), blk))
            current = None
            continue
        if "33" in gs:
            # Threading cycle: show the finished-depth pass and the return to start.
            modes.check_move(blk)
            if not pos.known:
                raise ProgramError(blk.line, "G33 before the program has given the slide's X and Z")
            xf, zf = blk.num("x"), blk.num("z")
            if xf is None or zf is None:
                raise ProgramError(blk.line, "G33 needs both X (finished diameter) and Z (thread end), p2.23")
            x, z = pos.x, pos.z
            paths.append(Path("feed", tool, Point(x, z), [Line(Point(xf, z)), Line(Point(xf, zf))], [blk.line] * 2))
            paths.append(Path("rapid", tool, Point(xf, zf), [Line(Point(x, zf)), Line(Point(x, z))], [blk.line] * 2))
            current = None
            continue
        if "81" in gs or "83" in gs:
            modes.check_move(blk)
            if not pos.known:
                raise ProgramError(blk.line, "drill cycle before the program has given the slide's X and Z")
            zd = blk.num("z")
            if zd is None:
                raise ProgramError(blk.line, "drill cycle needs a Z (the depth), p2.47")
            x, z = pos.x, pos.z
            paths.append(Path("drill", tool, Point(x, z), [Line(Point(x, zd)), Line(Point(x, z))], [blk.line] * 2))
            current = None
            continue
        if "00" in gs:
            motion = "rapid"
        if "01" in gs or "95" in gs:
            motion = "feed"
        f = blk.num("f")
        if "94" in gs and f is not None and f >= 300:
            motion = "rapid"
        code = arc_code(blk)
        if not (blk.has("x") or blk.has("z")):
            if code:
                raise ProgramError(blk.line, "arc with no X or Z: it has nowhere to go")
            continue  # M codes, S, F, G modes on their own: no position here
        modes.check_move(blk)
        nxt = pos.moved_by(blk)
        if not pos.known:
            # The first X and Z only tell us where the slide is. Nothing is
            # drawn from a position the program never gave.
            if code:
                raise ProgramError(blk.line, "arc before the program has given the slide's X and Z")
            pos = nxt
            continue
        if nxt.x == pos.x and nxt.z == pos.z and not code:
            continue  # a line to where the slide already is: nothing to draw
        kind = "feed" if (code or motion == "feed") else "rapid"
        if current is None or current.kind != kind or current.tool != tool:
            current = Path(kind, tool, pos.point(), lines=[])
            paths.append(current)
        if code:
            current.segments.append(_arc(blk, pos, nxt, arc_dir[code], step))
        else:
            # An R on a rapid isn't a corner (the old reader ignored it too).
            current.segments.append(_line_to(blk, nxt.point(), radius_allowed=kind == "feed"))
        current.lines.append(blk.line)
        pos = nxt

    # Expand every path once here, so a corner that can't be blended (a line
    # doubling back, a zero-length line beside a C/R; geometry.corner_blend)
    # is reported with its line numbers now, as a ProgramError, rather than
    # wherever the points are next used (the plot, the travel check).
    for path in paths:
        try:
            path.points()
        except ValueError as e:
            span = f"{path.lines[0]}-{path.lines[-1]}" if path.lines else "?"
            raise ProgramError(path.lines[0] if path.lines else 0, f"(lines {span}) {e}") from None
    return paths


PALETTE = ["#2a6fdb", "#e0782c", "#2e9e5b", "#b03a9a", "#c9a227", "#5b5b5b"]


def draw(ax, program: str, stock_dia: float | None = None, profile=None, arc_codes=None) -> None:
    """Draw a backplot onto a matplotlib Axes (radius scale, print view).

    ``arc_codes`` is the machine's (MachineConfig.arc_codes); leave it None
    for the OmniTurn's own (print-CCW = G02). Raises ProgramError, before
    anything is drawn, for a program interpret() can't read.
    """
    paths = interpret(program, arc_codes)
    tools = sorted({p.tool for p in paths if p.tool is not None})
    color = {t: PALETTE[i % len(PALETTE)] for i, t in enumerate(tools)}
    labelled = set()
    if stock_dia:
        _stock(ax, stock_dia, paths)
    for p in paths:
        pts = p.points()
        zs = [q[0] for q in pts]
        rs = [q[1] / 2 for q in pts]  # plot radius so the part looks to scale
        c = color.get(p.tool, "k")
        if p.kind == "rapid":
            ax.plot(zs, rs, ls=":", lw=0.8, color="#999999")
        elif p.kind == "contour":
            ax.plot(zs, rs, lw=3, color="black", alpha=0.18)
        else:
            lab = f"T{p.tool}" if p.tool not in labelled else None
            labelled.add(p.tool)
            ax.plot(zs, rs, lw=1.6, color=c, label=lab)
    _finish_axes(ax)
    if labelled:
        ax.legend(loc="best", fontsize=8)


def draw_profile(ax, profile, stock_dia: float | None = None, highlight: int | None = None) -> None:
    """Draw a part shape for the shape-entry screen. highlight = segment index."""
    if stock_dia:
        zmin = min(profile.bounds()[2], 0) - 0.1 * stock_dia
        ax.fill_between([zmin, max(profile.bounds()[3], 0)], 0, stock_dia / 2, color="#d9dde3", alpha=0.6, lw=0)
    pts = profile.polyline()
    ax.plot([p[0] for p in pts], [p[1] / 2 for p in pts], lw=2, color="#2a6fdb")
    ax.plot([profile.start.z], [profile.start.r], "o", color="#2e9e5b", ms=5)
    if highlight is not None and 0 <= highlight < len(profile.segments):
        segs = profile.segments
        start = profile.start if highlight == 0 else segs[highlight - 1].end
        seg = segs[highlight]
        # draw the highlighted segment as a sharp line/arc on its own
        hp = expand_moves(
            start, [type(seg)(seg.end, seg.radius, seg.direction)] if isinstance(seg, Arc) else [Line(seg.end)]
        )
        ax.plot([p[0] for p in hp], [p[1] / 2 for p in hp], lw=4, color="#e0782c", alpha=0.7)
    _finish_axes(ax)


def _stock(ax, stock_dia, paths):
    zs = [q[0] for p in paths for q in p.points()]
    if zs:
        ax.fill_between([min(zs), 0], 0, stock_dia / 2, color="#d9dde3", alpha=0.5, lw=0)


def _finish_axes(ax):
    ax.axhline(0, color="#444444", lw=0.6, ls="-.")
    ax.set_aspect("equal", adjustable="box")
    lo, hi = ax.get_ylim()
    ax.set_ylim(min(lo, -0.05 * hi), hi * 1.08)
    ax.set_xlabel("Z")
    ax.set_ylabel("X radius (print view, X+ up)")
    ax.grid(True, lw=0.3)


def plot(program: str, out_file: str, stock_dia: float | None = None, title: str = "", arc_codes=None) -> None:
    """Draw ``program`` to an image file. Raises ProgramError (see draw())
    before matplotlib is even loaded if the program can't be read."""
    interpret(program, arc_codes)  # fail fast: no empty image for a bad program
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(10, 5))
    draw(ax, program, stock_dia, arc_codes=arc_codes)
    ax.set_title(title or "OmniTurn backplot - feeds solid, rapids dotted, G75/G78 contour grey")
    fig.tight_layout()
    fig.savefig(out_file, dpi=130)
    plt.close(fig)
