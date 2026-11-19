"""2D turned-part profile model.

Coordinate convention (used everywhere outside the post-processor):

* ``x`` is a DIAMETER value, ``z`` is axial position, Z0 normally at the part face.
* Arc direction ``"cw"`` / ``"ccw"`` is as seen on a normal print of the part:
  Z+ to the right (face on the right, chuck on the left) and X+ up (upper half
  of the part). The post-processor is responsible for turning that into the
  machine's G02/G03, so the geometry never has to know about the OmniTurn's
  flipped X axis.

A profile is a start point followed by segments. Line segments may carry a
corner chamfer (``c``) or radius (``r``) that blends into the NEXT line, the
same way the OmniTurn's own ``C``/``R`` words work.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Literal

from .validation import Issue, is_finite, raise_errors, show

ArcDir = Literal["cw", "ccw"]

# How close two lengths must be to count as equal in a shape, in the job's
# own units (inch or mm). It only absorbs floating-point noise: shapes are
# kept at full precision (rounding happens when the program is written), so
# a shape that's really invalid by more than this is refused. One name for it
# so stepimport.py can show its own rounding stays inside it (IMPORT_DECIMALS).
LENGTH_TOL = 1e-9


@dataclass(frozen=True)
class Point:
    x: float  # diameter
    z: float

    @property
    def r(self) -> float:
        return self.x / 2.0


@dataclass(frozen=True)
class Line:
    end: Point
    chamfer: float | None = None  # corner at `end`, blends into next line
    radius: float | None = None  # corner at `end`, blends into next line

    def __post_init__(self):
        if self.chamfer and self.radius:
            raise ValueError("a corner can have a chamfer or a radius, not both")


@dataclass(frozen=True)
class Arc:
    end: Point
    radius: float
    direction: ArcDir  # as seen on the print (Z+ right, X+ up)

    def center(self, start: Point, tol: float = LENGTH_TOL) -> tuple[float, float]:
        """Arc centre as (z, r) in radius units, choosing the minor (<=180 deg) arc.

        ``tol`` is how far the chord may exceed the diameter 2R and still be
        read as a half circle. The default, LENGTH_TOL, only absorbs floating-point
        noise: a shape typed in or imported at full precision must really fit.
        The backplot passes a larger one for numbers that were *rounded* when
        the program was written (an arc that fits exactly can come out a
        ten-thousandth too long once its end points are rounded, issue #36);
        it works that tolerance out from the decimals actually written.

        Raises ValueError when start and end are the same point or the radius
        can't reach.
        """
        z1, r1 = start.z, start.r
        z2, r2 = self.end.z, self.end.r
        dz, dr = z2 - z1, r2 - r1
        chord = math.hypot(dz, dr)
        if chord == 0:
            raise ValueError("arc start and end are the same point")
        if chord > 2 * self.radius + tol:
            raise ValueError(
                f"arc radius {self.radius} too small for chord {chord:.5f} "
                f"(from x{start.x} z{start.z} to x{self.end.x} z{self.end.z})"
            )
        h = math.sqrt(max(self.radius**2 - (chord / 2) ** 2, 0.0))
        mz, mr = (z1 + z2) / 2, (r1 + r2) / 2
        # unit normal to the chord, pointing LEFT of travel in (z, r) space
        nz, nr = -dr / chord, dz / chord
        # In a right-handed (z right, r up) view a CCW minor arc has its centre
        # to the left of the chord direction; CW has it on the right.
        sign = 1.0 if self.direction == "ccw" else -1.0
        return mz + sign * h * nz, mr + sign * h * nr


Segment = Line | Arc


@dataclass
class Profile:
    """An ordered contour: start point plus line/arc segments."""

    start: Point
    segments: list[Segment] = field(default_factory=list)
    name: str = "profile"
    side: Literal["od", "id"] = "od"

    # -- convenience builders -------------------------------------------------
    def line(self, x: float | None = None, z: float | None = None, *, c=None, r=None) -> Profile:
        last = self.end
        self.segments.append(Line(Point(last.x if x is None else x, last.z if z is None else z), c, r))
        return self

    def arc(self, x: float, z: float, radius: float, direction: ArcDir) -> Profile:
        self.segments.append(Arc(Point(x, z), radius, direction))
        return self

    @property
    def end(self) -> Point:
        return self.segments[-1].end if self.segments else self.start

    def points(self) -> Iterator[tuple[Point, Segment]]:
        """Yield (segment start point, segment)."""
        cur = self.start
        for seg in self.segments:
            yield cur, seg
            cur = seg.end

    def issues(self) -> list[Issue]:
        """Every problem with the shape, as Issues (validation.py); raises nothing.

        Checked in two passes. First the numbers themselves (finite, C/R and
        arc radii positive): the geometry pass divides by lengths and takes
        angles, and a NaN or a string there would raise ZeroDivisionError or
        TypeError instead of saying what's wrong. Then the geometry: segments
        that go nowhere, arcs that can't reach, and C/R corners that don't fit
        the lines they sit on (manual p2.14-2.15).

        No coordinate gets a sign rule: X is a diameter and Z runs negative
        toward the chuck, and both can legitimately be anything.
        """
        where = f"shape {self.name!r}"
        out: list[Issue] = []

        def bad(setting: str, value, problem: str, fix: str = "", raw: bool = False) -> None:
            # raw=True: `value` is already text for the message (a point
            # like "x.75 z-.2"), so show() mustn't put quotes round it.
            out.append(Issue(where, problem, fix, setting=setting, value=value if raw else show(value)))

        if self.side not in ("od", "id"):
            bad("side", self.side, "must be od or id")
        for i, pt in enumerate([self.start] + [seg.end for seg in self.segments]):
            label = "start" if i == 0 else f"segment {i}"
            for axis in ("x", "z"):
                v = getattr(pt, axis)
                if not is_finite(v):
                    bad(f"{label} {axis}", v, "must be a finite number")
        for i, seg in enumerate(self.segments, 1):
            if isinstance(seg, Arc):
                if not is_finite(seg.radius) or seg.radius <= 0:
                    bad(f"segment {i} arc radius", seg.radius, "must be a number greater than zero")
                if seg.direction not in ("cw", "ccw"):
                    bad(f"segment {i} arc direction", seg.direction, "must be cw or ccw")
            elif isinstance(seg, Line):
                for word, v in (("C", seg.chamfer), ("R", seg.radius)):
                    # None and 0 both mean "no corner": the job file and the
                    # post (`if seg.chamfer`) have always read 0 that way.
                    if v is None:
                        continue
                    if not is_finite(v) or v < 0:
                        bad(f"segment {i} corner {word}", v, "must be a number greater than zero, or blank")
            else:
                bad(f"segment {i}", seg, "must be a line or an arc")
        if out:
            return out

        segs = self.segments
        # Where each segment begins: the start, then each segment's end. The
        # slice [: len(segs)] drops the last end, which begins nothing.
        starts = ([self.start] + [seg.end for seg in segs])[: len(segs)]
        for i, (p0, seg) in enumerate(zip(starts, segs, strict=True), 1):
            if abs(seg.end.x - p0.x) < LENGTH_TOL and abs(seg.end.z - p0.z) < LENGTH_TOL:
                bad(
                    f"segment {i}",
                    f"x{seg.end.x:g} z{seg.end.z:g}",
                    "ends where it starts, so it goes nowhere",
                    "Delete it",
                    raw=True,
                )
            elif isinstance(seg, Arc):
                try:
                    seg.center(p0)  # raises ValueError when the radius can't span the chord
                except ValueError as e:
                    bad(f"segment {i}", f"an arc r{seg.radius:g}", str(e), raw=True)
        if out:
            return out

        # C/R corners. A corner on line i blends it into line i+1 (p2.14):
        # the blend starts `setback` before the sharp corner on line i and
        # ends `setback` after it on line i+1. A chamfer's setback is its size
        # (corner_blend: "distance back from the corner along each line"); a
        # radius's is r / tan(angle / 2), the tangent length.
        setback_end = [0.0] * len(segs)  # used up at the end of each line
        for i, seg in enumerate(segs):
            if not isinstance(seg, Line) or not (seg.chamfer or seg.radius):
                continue
            n = i + 1  # the operator's 1-based segment number
            word = "C" if seg.chamfer else "R"
            if i + 1 >= len(segs) or not isinstance(segs[i + 1], Line):
                bad(
                    f"segment {n} corner {word}",
                    seg.chamfer or seg.radius,
                    "C/R corners only blend two straight lines (OmniTurn rule, manual p2.14)",
                    "Use an arc segment instead, or put the corner where two lines meet",
                )
                continue
            a = (starts[i].z - seg.end.z, starts[i].r - seg.end.r)  # back along this line
            b = (segs[i + 1].end.z - seg.end.z, segs[i + 1].end.r - seg.end.r)  # on along the next
            la, lb = math.hypot(*a), math.hypot(*b)
            cos = (a[0] * b[0] + a[1] * b[1]) / (la * lb)
            if cos < -1 + 1e-9:
                bad(
                    f"segment {n} corner {word}",
                    seg.chamfer or seg.radius,
                    f"has no corner to blend: segments {n} and {n + 1} are in a straight line",
                    "Remove the C/R",
                )
                continue
            if cos > 1 - 1e-9:
                bad(
                    f"segment {n} corner {word}",
                    seg.chamfer or seg.radius,
                    f"can't be blended: segment {n + 1} doubles back along segment {n}",
                )
                continue
            # max/min keep acos inside its domain if rounding nudges cos past 1.
            angle = math.acos(max(-1.0, min(1.0, cos)))
            setback_end[i] = seg.chamfer if seg.chamfer else seg.radius / math.tan(angle / 2)
        for i, seg in enumerate(segs):
            if not isinstance(seg, Line):
                continue
            at_start = setback_end[i - 1] if i > 0 and isinstance(segs[i - 1], Line) else 0.0
            used = at_start + setback_end[i]
            length = math.hypot(seg.end.z - starts[i].z, seg.end.r - starts[i].r)
            if used > length + LENGTH_TOL:
                bad(
                    f"segment {i + 1}",
                    f"{length:.4g} long",
                    f"is too short for its corners: they need {used:.4g} of it "
                    f"({at_start:.4g} at its start, {setback_end[i]:.4g} at its end)",
                    "Make the chamfer or radius smaller. X is a diameter: a step in X is half the X difference long",
                    raw=True,
                )
        return out

    def validate(self) -> None:
        """Raise ValidationError (a ValueError) listing every problem in issues()."""
        raise_errors(self.issues())

    def undercut_z(self, tol: float = 1e-4) -> float | None:
        """Z where the shape first turns back toward the face (+Z) after heading toward
        the chuck - a feature a tool coming from the face can't reach. None if none."""
        lowest = self.start.z
        for seg in self.segments:
            if seg.end.z > lowest + tol:
                return round(lowest, 4)
            lowest = min(lowest, seg.end.z)
        return None

    # -- tessellation for plotting / checks ------------------------------------
    def polyline(self, step_deg: float = 5.0) -> list[tuple[float, float]]:
        """Return (z, x_diameter) points with C/R corners and arcs expanded."""
        return expand_moves(self.start, self.segments, step_deg)

    def bounds(self) -> tuple[float, float, float, float]:
        pts = self.polyline()
        zs = [p[0] for p in pts]
        xs = [p[1] for p in pts]
        return min(xs), max(xs), min(zs), max(zs)


def _arc_points(z0, r0, z1, r1, cz, cr, direction: ArcDir, step_deg: float):
    a0 = math.atan2(r0 - cr, z0 - cz)
    a1 = math.atan2(r1 - cr, z1 - cz)
    if direction == "ccw":
        while a1 <= a0:
            a1 += 2 * math.pi
    else:
        while a1 >= a0:
            a1 -= 2 * math.pi
    n = max(2, int(abs(a1 - a0) / math.radians(step_deg)) + 1)
    rad = math.hypot(z0 - cz, r0 - cr)
    return [
        (cz + rad * math.cos(a0 + (a1 - a0) * i / n), cr + rad * math.sin(a0 + (a1 - a0) * i / n))
        for i in range(1, n + 1)
    ]


def corner_blend(p0, p1, p2, chamfer=None, radius=None, step_deg=5.0):
    """Points (z, r) replacing the sharp corner p1 between lines p0-p1 and p1-p2.

    p0 is where the straight part of the first line begins (the previous
    corner's blend end, if it had one), so the lengths checked here are what
    is actually left for this corner.

    Raises ValueError, never ZeroDivisionError, for a corner the arithmetic
    below can't handle: a line of zero length, or a second line doubling back
    along the first. Profile.issues() refuses these for a shape before it
    gets here; this guard is for programs the backplot reads (hand-written,
    raw code), which never went through Profile (issue #36). The tolerances
    are the ones Profile.issues() uses.

    Lines that run on in a straight line have no corner to blend, so the
    sharp point is returned as it is. That case isn't an error here: a very
    shallow corner in a shape (which Profile accepts) can read back as exactly
    straight once the program's numbers are rounded, and the radius maths
    would then divide by zero (the bisector of two opposite directions has no
    length).

    Whether a C/R *fits* its lines is deliberately not checked here.
    Profile.issues() checks it at full precision before any code is written.
    Here the points come from a written program, rounded to the machine's
    decimals, and a corner that fits exactly can read back a ten-thousandth
    too big: refusing it would refuse a correct program. An oversized corner
    in a hand-written program is drawn as written.
    """
    (z0, r0), (z1, r1), (z2, r2) = p0, p1, p2
    a = (z0 - z1, r0 - r1)
    b = (z2 - z1, r2 - r1)
    la, lb = math.hypot(*a), math.hypot(*b)
    word = "C" if chamfer else "R"
    if la < LENGTH_TOL or lb < LENGTH_TOL:
        raise ValueError(f"corner {word}: a line next to it has no length")
    ua = (a[0] / la, a[1] / la)
    ub = (b[0] / lb, b[1] / lb)
    cos = ua[0] * ub[0] + ua[1] * ub[1]
    if cos < -1 + 1e-9:
        return [p1]
    if cos > 1 - 1e-9:
        raise ValueError(f"corner {word}: the second line doubles back along the first")
    if chamfer:
        # OmniTurn C value: distance back from the corner along each line
        return [(z1 + ua[0] * chamfer, r1 + ua[1] * chamfer), (z1 + ub[0] * chamfer, r1 + ub[1] * chamfer)]
    theta = math.acos(max(-1.0, min(1.0, cos)))
    t = radius / math.tan(theta / 2)
    s = (z1 + ua[0] * t, r1 + ua[1] * t)
    e = (z1 + ub[0] * t, r1 + ub[1] * t)
    bis = (ua[0] + ub[0], ua[1] + ub[1])
    lbis = math.hypot(*bis)
    d = radius / math.sin(theta / 2)
    c = (z1 + bis[0] / lbis * d, r1 + bis[1] / lbis * d)
    cross = (s[0] - c[0]) * (e[1] - c[1]) - (s[1] - c[1]) * (e[0] - c[0])
    direction: ArcDir = "ccw" if cross > 0 else "cw"
    return [s] + _arc_points(s[0], s[1], e[0], e[1], c[0], c[1], direction, step_deg)


def expand_moves(start: Point, segments: list[Segment], step_deg: float = 5.0) -> list[tuple[float, float]]:
    """Expand a start point + segments into (z, x_diameter) points."""
    pts_r: list[tuple[float, float]] = [(start.z, start.r)]
    prev = start
    pending_corner = None  # (chamfer, radius) to apply at the last point
    for seg in segments:
        if isinstance(seg, Line):
            if pending_corner:
                corner = pts_r.pop()
                before = pts_r[-1]
                pts_r.extend(corner_blend(before, corner, (seg.end.z, seg.end.r), *pending_corner, step_deg))
                pending_corner = None
            pts_r.append((seg.end.z, seg.end.r))
            if seg.chamfer or seg.radius:
                pending_corner = (seg.chamfer, seg.radius)
        else:
            pending_corner = None
            cz, cr = seg.center(prev)
            pts_r.extend(_arc_points(prev.z, prev.r, seg.end.z, seg.end.r, cz, cr, seg.direction, step_deg))
        prev = seg.end
    return [(z, r * 2) for z, r in pts_r]
