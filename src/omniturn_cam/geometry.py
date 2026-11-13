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
from dataclasses import dataclass, field
from typing import Iterator, Literal, Union

ArcDir = Literal["cw", "ccw"]


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
    radius: float | None = None   # corner at `end`, blends into next line

    def __post_init__(self):
        if self.chamfer and self.radius:
            raise ValueError("a corner can have a chamfer or a radius, not both")


@dataclass(frozen=True)
class Arc:
    end: Point
    radius: float
    direction: ArcDir  # as seen on the print (Z+ right, X+ up)

    def center(self, start: Point) -> tuple[float, float]:
        """Arc centre as (z, r) in radius units, choosing the minor (<=180 deg) arc."""
        z1, r1 = start.z, start.r
        z2, r2 = self.end.z, self.end.r
        dz, dr = z2 - z1, r2 - r1
        chord = math.hypot(dz, dr)
        if chord == 0:
            raise ValueError("arc start and end are the same point")
        if chord > 2 * self.radius + 1e-9:
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


Segment = Union[Line, Arc]


@dataclass
class Profile:
    """An ordered contour: start point plus line/arc segments."""

    start: Point
    segments: list[Segment] = field(default_factory=list)
    name: str = "profile"
    side: Literal["od", "id"] = "od"

    # -- convenience builders -------------------------------------------------
    def line(self, x: float | None = None, z: float | None = None, *, c=None, r=None) -> "Profile":
        last = self.end
        self.segments.append(Line(Point(last.x if x is None else x, last.z if z is None else z), c, r))
        return self

    def arc(self, x: float, z: float, radius: float, direction: ArcDir) -> "Profile":
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

    def validate(self) -> None:
        segs = self.segments
        for i, (p0, seg) in enumerate(self.points()):
            if isinstance(seg, Arc):
                seg.center(p0)  # raises if impossible
            elif seg.chamfer or seg.radius:
                if i + 1 >= len(segs) or not isinstance(segs[i + 1], Line):
                    raise ValueError(
                        "C/R corners only blend two straight lines (OmniTurn rule, manual p2.14); "
                        f"segment {i + 1} ends at x{seg.end.x} z{seg.end.z}"
                    )

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
    return [(cz + rad * math.cos(a0 + (a1 - a0) * i / n), cr + rad * math.sin(a0 + (a1 - a0) * i / n))
            for i in range(1, n + 1)]


def corner_blend(p0, p1, p2, chamfer=None, radius=None, step_deg=5.0):
    """Points (z, r) replacing the sharp corner p1 between lines p0-p1 and p1-p2."""
    (z0, r0), (z1, r1), (z2, r2) = p0, p1, p2
    a = (z0 - z1, r0 - r1)
    b = (z2 - z1, r2 - r1)
    la, lb = math.hypot(*a), math.hypot(*b)
    ua = (a[0] / la, a[1] / la)
    ub = (b[0] / lb, b[1] / lb)
    if chamfer:
        # OmniTurn C value: distance back from the corner along each line
        return [(z1 + ua[0] * chamfer, r1 + ua[1] * chamfer), (z1 + ub[0] * chamfer, r1 + ub[1] * chamfer)]
    theta = math.acos(max(-1.0, min(1.0, ua[0] * ub[0] + ua[1] * ub[1])))
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
    for i, seg in enumerate(segments):
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
