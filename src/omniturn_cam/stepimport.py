"""STEP import: turn a solid of revolution into OD and ID profiles.

How it works
1. Read the STEP file (OpenCascade via the `OCP` package) and list its solids.
2. Find the turning axis: the axis shared by the largest area of cylindrical,
   conical, toroidal and revolved faces.
3. Cut the solid with planes through that axis at several angles and keep the
   half-section (the side with r >= 0). Flats, cross holes and other features
   that are not turned make some cuts smaller, so the fullest cut is used and
   the features are reported.
4. Chain the cut's edges into a loop, put Z0 at the chosen end face, and split
   the loop at the face into the OD profile (outward, then toward the chuck)
   and the ID profile (into the bore).

Everything here works in the part's own (z, r) plane: z along the axis, r the
distance from it. Profiles come out with diameters, like the rest of the app.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

from .geometry import Arc, Line, Point, Profile

MM_PER_INCH = 25.4
TOL = 1e-4          # mm, for joining edge ends
ANGLES = [7 + 30 * i for i in range(12)]   # cut angles, degrees; avoids the usual seam at 0


class StepImportError(ValueError):
    pass


def _cast(kind: str, shape):
    """TopoDS.Solid / Face / Edge downcast; OCP 7.x names them Solid_s etc."""
    from OCP.TopoDS import TopoDS
    fn = getattr(TopoDS, kind + "_s", None) or getattr(TopoDS, kind)
    return fn(shape)


def _occ():
    try:
        import OCP  # noqa: F401
    except ImportError as e:
        raise StepImportError(
            "STEP import needs the OpenCascade bindings: pip install cadquery-ocp "
            "(or pip install -e \".[step]\")") from e


# ----------------------------------------------------------------------------- reading
@dataclass
class SolidInfo:
    index: int
    name: str
    volume: float          # mm^3
    size: tuple            # bounding box dx, dy, dz in mm
    shape: object = field(repr=False, default=None)


_CACHE: dict = {}


def read_solids(path: str | Path) -> list[SolidInfo]:
    """Solids in a STEP file. Cached per file (path, size, mtime): big assemblies
    take a while to read, and the import window re-imports on every flip."""
    st = Path(path).stat()
    key = (str(Path(path).resolve()), st.st_size, st.st_mtime)
    if key not in _CACHE:
        _CACHE.clear()
        _CACHE[key] = _read_solids(path)
    return _CACHE[key]


def _read_solids(path: str | Path) -> list[SolidInfo]:
    _occ()
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_Reader
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer

    reader = STEPControl_Reader()
    if reader.ReadFile(str(path)) != IFSelect_RetDone:
        raise StepImportError(f"could not read {path} as a STEP file")
    reader.TransferRoots()
    shape = reader.OneShape()
    out = []
    exp = TopExp_Explorer(shape, TopAbs_SOLID)
    while exp.More():
        solid = _cast("Solid", exp.Current())
        props = GProp_GProps()
        BRepGProp.VolumeProperties_s(solid, props)
        box = Bnd_Box()
        BRepBndLib.Add_s(solid, box)
        lo, hi = box.CornerMin(), box.CornerMax()
        x0, y0, z0, x1, y1, z1 = lo.X(), lo.Y(), lo.Z(), hi.X(), hi.Y(), hi.Z()
        out.append(SolidInfo(len(out), f"Solid {len(out) + 1}", abs(props.Mass()),
                             (x1 - x0, y1 - y0, z1 - z0), solid))
        exp.Next()
    if not out:
        raise StepImportError(f"{Path(path).name} has no solids (is it a surface model?)")
    return out


# ----------------------------------------------------------------------------- axis
@dataclass
class Axis:
    loc: tuple
    dir: tuple
    area: float = 0.0


def _v(p):
    return (p.X(), p.Y(), p.Z())


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _norm(a):
    n = math.sqrt(_dot(a, a))
    return (a[0] / n, a[1] / n, a[2] / n)


def _line_dist(p, axis: Axis):
    d = _sub(p, axis.loc)
    c = _cross(d, axis.dir)
    return math.sqrt(_dot(c, c))


def _faces(shape):
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        yield _cast("Face", exp.Current())
        exp.Next()


def find_axis(solid) -> tuple[Axis, list[str]]:
    """Turning axis plus a list of faces that are not turned features."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepGProp import BRepGProp
    from OCP.GeomAbs import (GeomAbs_Cone, GeomAbs_Cylinder, GeomAbs_Plane, GeomAbs_Sphere,
                             GeomAbs_SurfaceOfRevolution, GeomAbs_Torus)
    from OCP.GProp import GProp_GProps

    faces = []
    for f in _faces(solid):
        s = BRepAdaptor_Surface(f)
        props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(f, props)
        t = s.GetType()
        ax = None
        if t == GeomAbs_Cylinder:
            ax = s.Cylinder().Axis()
        elif t == GeomAbs_Cone:
            ax = s.Cone().Axis()
        elif t == GeomAbs_Torus:
            ax = s.Torus().Axis()
        elif t == GeomAbs_SurfaceOfRevolution:
            ax = s.AxeOfRevolution()
        info = {"type": t, "area": props.Mass(), "surf": s}
        if ax is not None:
            info["axis"] = (_v(ax.Location()), _norm(_v(ax.Direction())))
        faces.append(info)

    clusters: list[Axis] = []
    for f in faces:
        if "axis" not in f:
            continue
        loc, d = f["axis"]
        for c in clusters:
            if abs(abs(_dot(d, c.dir)) - 1) < 1e-7 and _line_dist(loc, c) < 1e-3:
                c.area += f["area"]
                break
        else:
            clusters.append(Axis(loc, d, f["area"]))
    if not clusters:
        raise StepImportError("no round faces found - this doesn't look like a turned part")
    axis = max(clusters, key=lambda c: c.area)

    odd = []
    for f in faces:
        t = f["type"]
        if "axis" in f:
            loc, d = f["axis"]
            if not (abs(abs(_dot(d, axis.dir)) - 1) < 1e-7 and _line_dist(loc, axis) < 1e-3):
                odd.append("round face off the turning axis (cross hole, off-centre feature)")
        elif t == GeomAbs_Plane:
            n = _norm(_v(f["surf"].Plane().Axis().Direction()))
            if abs(abs(_dot(n, axis.dir)) - 1) > 1e-6:
                odd.append("flat face not square to the axis (flat, slot, hex)")
        elif t == GeomAbs_Sphere:
            c = _v(f["surf"].Sphere().Location())
            if _line_dist(c, axis) > 1e-3:
                odd.append("sphere off the axis")
        else:
            odd.append("free-form face")
    return axis, sorted(set(odd))


# ----------------------------------------------------------------------------- section
@dataclass
class Seg:
    """A section edge in the part plane: z along the axis, r from it (mm)."""
    p0: tuple
    p1: tuple
    kind: str = "line"          # "line" | "arc"
    center: tuple | None = None
    radius: float = 0.0
    direction: str = ""         # arc: "ccw" / "cw" travelling p0 -> p1 (z right, r up)
    approx: bool = False        # a piece of a curve that wasn't a line or an arc

    def reversed(self) -> "Seg":
        d = {"ccw": "cw", "cw": "ccw"}.get(self.direction, "")
        return Seg(self.p1, self.p0, self.kind, self.center, self.radius, d, self.approx)

    def mirrored(self, zref: float) -> "Seg":
        """Mirror in z about zref (swaps which end is the face)."""
        m = lambda p: (2 * zref - p[0], p[1])
        c = m(self.center) if self.center else None
        d = {"ccw": "cw", "cw": "ccw"}.get(self.direction, "")
        return Seg(m(self.p0), m(self.p1), self.kind, c, self.radius, d, self.approx)

    def points(self, n=16):
        if self.kind == "line":
            return [self.p0, self.p1]
        a0 = math.atan2(self.p0[1] - self.center[1], self.p0[0] - self.center[0])
        a1 = math.atan2(self.p1[1] - self.center[1], self.p1[0] - self.center[0])
        if self.direction == "ccw":
            while a1 <= a0:
                a1 += 2 * math.pi
        else:
            while a1 >= a0:
                a1 -= 2 * math.pi
        return [(self.center[0] + self.radius * math.cos(a0 + (a1 - a0) * i / n),
                 self.center[1] + self.radius * math.sin(a0 + (a1 - a0) * i / n)) for i in range(n + 1)]


def _section_segments(solid, axis: Axis, angle_deg: float) -> tuple[list[Seg], list[str]]:
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Section
    from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Line
    from OCP.gp import gp_Dir, gp_Pln, gp_Pnt
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    a = axis.dir
    ref = (1, 0, 0) if abs(a[0]) < 0.9 else (0, 1, 0)
    u0 = _norm(_cross(a, ref))
    w0 = _cross(a, u0)
    th = math.radians(angle_deg)
    u = tuple(math.cos(th) * u0[i] + math.sin(th) * w0[i] for i in range(3))   # in-plane radial dir
    n = _cross(a, u)                                                            # plane normal
    sec = BRepAlgoAPI_Section(solid, gp_Pln(gp_Pnt(*axis.loc), gp_Dir(*n)))
    sec.Build()
    if not sec.IsDone():
        raise StepImportError("OpenCascade could not cut the part through its axis")

    def local(p):
        d = _sub(p, axis.loc)
        return (_dot(d, a), _dot(d, u))

    segs, notes = [], []
    exp = TopExp_Explorer(sec.Shape(), TopAbs_EDGE)
    while exp.More():
        e = _cast("Edge", exp.Current())
        exp.Next()
        c = BRepAdaptor_Curve(e)
        t0, t1 = c.FirstParameter(), c.LastParameter()
        f = lambda t: local(_v(c.Value(t)))
        r0, r1 = f(t0)[1], f(t1)[1]
        # keep only the r >= 0 half; split edges that cross the axis
        tm = (t0 + t1) / 2
        if r0 < -TOL and r1 < -TOL and f(tm)[1] < -TOL:
            continue
        if (r0 < -TOL) != (r1 < -TOL):
            lo, hi = (t0, t1) if r0 < r1 else (t1, t0)
            for _ in range(80):
                mid = (lo + hi) / 2
                if f(mid)[1] < 0:
                    lo = mid
                else:
                    hi = mid
            if r0 < -TOL:
                t0 = hi
            else:
                t1 = hi
        p0, p1, pm = f(t0), f(t1), f((t0 + t1) / 2)
        if math.dist(p0, p1) < TOL and c.GetType() == GeomAbs_Line:
            continue
        p0 = (p0[0], max(p0[1], 0.0))
        p1 = (p1[0], max(p1[1], 0.0))
        typ = c.GetType()
        if typ == GeomAbs_Line:
            segs.append(Seg(p0, p1))
        elif typ == GeomAbs_Circle:
            circ = c.Circle()
            ctr = local(_v(circ.Location()))
            cross = (pm[0] - p0[0]) * (p1[1] - pm[1]) - (pm[1] - p0[1]) * (p1[0] - pm[0])
            d = "ccw" if cross > 0 else "cw"
            seg = Seg(p0, p1, "arc", ctr, circ.Radius(), d)
            # split arcs of 180 deg or more so every piece is a minor arc
            sweep = abs(t1 - t0)
            if sweep > math.pi * 0.98:
                segs.append(Seg(p0, pm, "arc", ctr, circ.Radius(), d))
                segs.append(Seg(pm, p1, "arc", ctr, circ.Radius(), d))
            else:
                segs.append(seg)
        else:
            # Some exporters / transforms hand back B-splines for what is really a
            # line or an arc: recognise them by shape, otherwise use short lines.
            k = 24
            pts = [f(t0 + (t1 - t0) * i / k) for i in range(k + 1)]
            pts = [(p[0], max(p[1], 0.0)) for p in pts]
            fitted = _fit(pts)
            if fitted is not None:
                segs.extend(fitted)
            else:
                segs.extend(Seg(pts[i], pts[i + 1], approx=True) for i in range(k))
    return segs, notes


def _fit(pts, tol=2e-3) -> list[Seg] | None:
    """Return a line or arc(s) matching the sampled points within tol (mm), else None."""
    a, m, b = pts[0], pts[len(pts) // 2], pts[-1]
    # line?
    L = math.dist(a, b)
    if L > TOL:
        dev = max(abs((b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])) / L for p in pts)
        if dev < tol:
            return [Seg(a, b)]
    # circle through start, middle, end
    ax, ay = a
    bx, by = m
    cx, cy = b
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:
        return None
    ux = ((ax**2 + ay**2) * (by - cy) + (bx**2 + by**2) * (cy - ay) + (cx**2 + cy**2) * (ay - by)) / d
    uy = ((ax**2 + ay**2) * (cx - bx) + (bx**2 + by**2) * (ax - cx) + (cx**2 + cy**2) * (bx - ax)) / d
    r = math.dist(a, (ux, uy))
    if max(abs(math.dist(p, (ux, uy)) - r) for p in pts) > tol:
        return None
    cross = (m[0] - a[0]) * (b[1] - m[1]) - (m[1] - a[1]) * (b[0] - m[0])
    direction = "ccw" if cross > 0 else "cw"
    # split at the middle sample so each piece is a minor arc
    return [Seg(a, m, "arc", (ux, uy), r, direction), Seg(m, b, "arc", (ux, uy), r, direction)]


def _chains(segs: list[Seg]) -> list[list[Seg]]:
    """Join segments end to end into chains (closed loops or open axis-to-axis chains)."""
    def key(p):
        return (round(p[0] / TOL), round(p[1] / TOL))

    def near(p, q):
        return math.dist(p, q) < TOL * 5

    unused = list(segs)
    chains = []
    while unused:
        chain = [unused.pop(0)]
        grown = True
        while grown:
            grown = False
            for i, s in enumerate(unused):
                if near(chain[-1].p1, s.p0):
                    chain.append(unused.pop(i)); grown = True; break
                if near(chain[-1].p1, s.p1):
                    chain.append(unused.pop(i).reversed()); grown = True; break
                if near(chain[0].p0, s.p1):
                    chain.insert(0, unused.pop(i)); grown = True; break
                if near(chain[0].p0, s.p0):
                    chain.insert(0, unused.pop(i).reversed()); grown = True; break
        chains.append(chain)
    return chains


def _area(chain: list[Seg]) -> float:
    pts = [p for s in chain for p in s.points()]
    if math.dist(pts[0], pts[-1]) > TOL * 5:      # open chain: closed by the axis
        pts = pts + [(pts[-1][0], 0.0), (pts[0][0], 0.0)]
    return abs(sum(pts[i][0] * pts[i + 1][1] - pts[i + 1][0] * pts[i][1]
                   for i in range(-1, len(pts) - 1))) / 2


def _merge_collinear(chain: list[Seg]) -> list[Seg]:
    out: list[Seg] = []
    for s in chain:
        if out and s.kind == "line" and out[-1].kind == "line":
            a, b, c = out[-1].p0, out[-1].p1, s.p1
            cross = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
            if abs(cross) < 1e-6 * max(1.0, math.dist(a, c) ** 2):
                out[-1] = Seg(a, c)
                continue
        if math.dist(s.p0, s.p1) > TOL:
            out.append(s)
    return out


# ----------------------------------------------------------------------------- result
@dataclass
class ImportResult:
    od: Profile
    id: Profile | None
    max_dia: float
    length: float
    units: str
    warnings: list[str]
    solid: str
    angle: float


def _to_profile(chain: list[Seg], name: str, side: str, scale: float, decimals: int) -> Profile:
    def P(p):
        return Point(round(2 * p[1] * scale, decimals) + 0.0, round(p[0] * scale, decimals) + 0.0)

    prof = Profile(P(chain[0].p0), name=name, side=side)
    for s in chain:
        end = P(s.p1)
        if s.kind == "arc":
            prof.segments.append(Arc(end, round(s.radius * scale, decimals), s.direction))
        else:
            prof.segments.append(Line(end))
    return prof


def import_step(path: str | Path, solid_index: int | None = None, units: str = "inch",
                flip: bool = False) -> ImportResult:
    """Read a STEP file and return OD/ID profiles with Z0 at the face end.

    flip=False puts Z0 at the end the turning axis points to; flip=True uses the
    other end. Arcs come out in print terms (face right, X+ up)."""
    solids = read_solids(path)
    if solid_index is None:
        if len(solids) > 1:
            raise StepImportError(f"{Path(path).name} has {len(solids)} solids - choose one")
        solid_index = 0
    info = solids[solid_index]
    axis, odd = find_axis(info.shape)
    warnings = []

    best = None
    areas = []
    for ang in ANGLES:
        segs, notes = _section_segments(info.shape, axis, ang)
        if not segs:
            continue
        chains = _chains(segs)
        main = max(chains, key=_area)
        a = _area(main)
        areas.append(a)
        if best is None or a > best[0] + 1e-9:
            best = (a, ang, main, chains, notes)
    if best is None:
        raise StepImportError("cutting the part through its axis gave nothing")
    area, angle, loop, chains, notes = best
    if len(chains) > 1:
        warnings.append(f"the cut has {len(chains) - 1} separate piece(s) besides the main outline "
                        "(cross holes, internal pockets) - ignored")
    if odd or (max(areas) - min(areas)) > 1e-3 * max(areas):
        warnings.append("part has features that are not turned (" + "; ".join(odd or ["varies around the axis"])
                        + "). The profile is taken where the part is fullest - machine those features separately")

    zs = [p[0] for s in loop for p in s.points()]
    zmin, zmax = min(zs), max(zs)
    if flip:
        loop = [s.mirrored(0.0) for s in loop]
        zmin, zmax = -zmax, -zmin
    # shift so the face end is Z0
    loop = [Seg((s.p0[0] - zmax, s.p0[1]), (s.p1[0] - zmax, s.p1[1]), s.kind,
                (s.center[0] - zmax, s.center[1]) if s.center else None, s.radius, s.direction, s.approx)
            for s in loop]
    zmin, zmax = zmin - zmax, 0.0
    length = zmax - zmin

    od_chain, id_chain = _split(loop, zmin)
    if any(s.approx for s in od_chain + id_chain):
        warnings.append("part of the outline isn't made of lines and arcs; it was approximated with "
                        "short lines - check the shape on F2")
    scale = 1 / MM_PER_INCH if units == "inch" else 1.0
    dec = 6 if units == "inch" else 4
    od = _to_profile(_merge_collinear(od_chain), "od", "od", scale, dec)
    idp = _to_profile(_merge_collinear(id_chain), "id", "id", scale, dec) if id_chain else None
    for prof in (od, idp):
        if prof is not None and prof.undercut_z() is not None:
            warnings.append(f"the {prof.side.upper()} turns back toward the face at Z{prof.undercut_z():g} "
                            "(a groove or slot cut from the back) - it can't be reached from the face; "
                            "edit the shape on F2 or machine that feature from the other end")
    rmax = max(p[1] for s in loop for p in s.points())
    return ImportResult(od, idp, round(2 * rmax * scale, dec), round(length * scale, dec), units,
                        warnings, info.name, angle)


def extend_to_stock(prof: Profile, stock_dia: float) -> Profile:
    """Finish an OD profile at the bar diameter, so the G75/G78 cycle has a closed
    area to rough (the manual's example ends the same way: X.75 = stock)."""
    if prof.side == "od" and prof.segments and prof.end.x < stock_dia - 1e-9:
        prof.segments.append(Line(Point(stock_dia, prof.end.z)))
    return prof


def _split(loop: list[Seg], zmin: float) -> tuple[list[Seg], list[Seg]]:
    """Split the half-section at the face into the OD path and the ID path."""
    near = lambda a, b: abs(a - b) < TOL * 5
    closed = math.dist(loop[0].p0, loop[-1].p1) < TOL * 5
    # vertices: start of each segment (+ end of the last for an open chain)
    verts = [s.p0 for s in loop] + ([] if closed else [loop[-1].p1])
    face_idx = [i for i, p in enumerate(verts) if near(p[0], 0.0)]
    if not face_idx:
        raise StepImportError("could not find the face end in the section")
    start = min(face_idx, key=lambda i: verts[i][1])          # innermost point on the face

    n = len(loop)

    def walk(i, forward):
        out = []
        while True:
            if forward:
                if i >= n and not closed:
                    break
                s = loop[i % n]
                out.append(s)
                end = s.p1
                i += 1
            else:
                if i <= 0 and not closed:
                    break
                s = loop[(i - 1) % n].reversed()
                out.append(s)
                end = s.p1
                i -= 1
            if near(end[0], zmin) or end[1] < TOL * 5 or len(out) > n:
                break
        return out

    fwd = walk(start, True) if (closed or start < n) else []
    bwd = walk(start, False) if (closed or start > 0) else []
    # the OD path is the one that heads away from the axis first
    def first_r(path):
        return path[0].p1[1] if path else -1
    if first_r(fwd) >= first_r(bwd):
        od, idp = fwd, bwd
    else:
        od, idp = bwd, fwd
    # an ID path that runs straight back to the axis at the face is no bore at all
    if idp and all(p[1] < TOL * 5 for p in (idp[-1].p1,)) and verts[start][1] < TOL * 5:
        idp = []
    return od, idp
