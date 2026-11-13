"""Mesh import (STL, 3MF): the same OD/ID shapes as STEP import, from triangles.

Meshes are what's left when only a 3D-print file exists. They have no true arcs
or cylinders, so this module:

1. finds the turning axis from the triangle normals. On a turned part every
   facet's normal either crosses the axis (round faces) or runs along it
   (faces square to the axis). That is a linear condition on the axis'
   Plücker coordinates, solved by least squares with outliers (flats, slots)
   weighted out and reported;
2. cuts the mesh with planes through the axis. The cuts go through rings of
   mesh vertices, so the points land on the modelled surface rather than on
   the chords between vertices;
3. fits the jagged outline back to lines and arcs within a tolerance, then
   hands it to the same code the STEP import uses.

Accuracy is limited by the mesh: check the imported numbers against the print.
"""
from __future__ import annotations

import math
import re
import struct
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .stepimport import ImportResult, Seg, StepImportError, finish_loop

MESH_SUFFIXES = {".stl", ".3mf"}
FIT_TOL = 0.02          # mm: how far the fitted lines/arcs may stray from the mesh outline
UNIT_MM = {"micron": 0.001, "millimeter": 1.0, "centimeter": 10.0, "inch": 25.4, "foot": 304.8, "meter": 1000.0}


@dataclass
class MeshBody:
    index: int
    name: str
    vertices: np.ndarray = field(repr=False)     # (n, 3) mm
    triangles: np.ndarray = field(repr=False)    # (m, 3) int

    @property
    def size(self):
        v = self.vertices
        return tuple(float(x) for x in (v.max(0) - v.min(0)))

    @property
    def volume(self):
        v = self.vertices[self.triangles]
        return float(abs(np.einsum("ij,ij->i", v[:, 0], np.cross(v[:, 1], v[:, 2])).sum()) / 6)


# ----------------------------------------------------------------------------- reading
def _weld(tris: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """STL stores corners per triangle; merge shared corners into vertices."""
    pts = tris.reshape(-1, 3)
    key = np.round(pts / 1e-6).astype(np.int64)
    _, first, inverse = np.unique(key, axis=0, return_index=True, return_inverse=True)
    return pts[first], inverse.reshape(-1, 3)


def read_stl(path) -> list[MeshBody]:
    data = Path(path).read_bytes()
    n = struct.unpack_from("<I", data, 80)[0] if len(data) >= 84 else 0
    if len(data) == 84 + 50 * n:
        rec = np.frombuffer(data, dtype=np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")]),
                            count=n, offset=84)
        tris = rec["v"].astype(np.float64)
    else:
        nums = re.findall(rb"vertex\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)", data)
        if not nums:
            raise StepImportError(f"{Path(path).name} doesn't look like an STL file")
        tris = np.array(nums, dtype=np.float64).reshape(-1, 3, 3)
    v, f = _weld(tris)
    return [MeshBody(0, Path(path).stem, v, f)]


def read_3mf(path) -> list[MeshBody]:
    with zipfile.ZipFile(path) as z:
        name = next((n for n in z.namelist() if n.lower().endswith(".model")), None)
        if name is None:
            raise StepImportError(f"{Path(path).name} has no 3D model inside")
        xml = z.read(name)
    unit = re.search(rb'<model[^>]*\bunit="([a-z]+)"', xml)
    scale = UNIT_MM.get(unit.group(1).decode() if unit else "millimeter", 1.0)
    objects = {}
    for m in re.finditer(rb'<object\b([^>]*)>(.*?)</object>', xml, re.S):
        attrs, body = m.group(1), m.group(2)
        oid = re.search(rb'\bid="(\d+)"', attrs).group(1)
        oname = re.search(rb'\bname="([^"]*)"', attrs)
        verts = re.findall(rb'<vertex\s+x="([^"]+)"\s+y="([^"]+)"\s+z="([^"]+)"', body)
        tris = re.findall(rb'<triangle\s+v1="(\d+)"\s+v2="(\d+)"\s+v3="(\d+)"', body)
        if verts and tris:
            objects[oid] = (oname.group(1).decode(errors="replace") if oname else f"Object {int(oid)}",
                            np.array(verts, dtype=np.float64) * scale, np.array(tris, dtype=np.int64))
    bodies = []
    items = re.findall(rb'<item\b[^>]*\bobjectid="(\d+)"(?:[^>]*\btransform="([^"]+)")?', xml)
    for oid, tr in items or [(k, b"") for k in objects]:
        if oid not in objects:
            continue
        oname, v, f = objects[oid]
        if tr:
            m = np.array(tr.split(), dtype=np.float64).reshape(4, 3)
            v = v @ m[:3] + m[3] * scale
        bodies.append(MeshBody(len(bodies), oname, v, f))
    if not bodies:
        raise StepImportError(f"{Path(path).name} has no mesh objects")
    return bodies


_CACHE: dict = {}


def read_mesh(path) -> list[MeshBody]:
    st = Path(path).stat()
    key = (str(Path(path).resolve()), st.st_size, st.st_mtime)
    if key not in _CACHE:
        _CACHE.clear()
        suffix = Path(path).suffix.lower()
        _CACHE[key] = read_3mf(path) if suffix == ".3mf" else read_stl(path)
    return _CACHE[key]


# ----------------------------------------------------------------------------- axis
def _facets(body: MeshBody):
    v = body.vertices[body.triangles]
    n = np.cross(v[:, 1] - v[:, 0], v[:, 2] - v[:, 0])
    area2 = np.linalg.norm(n, axis=1)
    ok = area2 > 1e-12
    return v[ok].mean(axis=1), n[ok] / area2[ok, None], area2[ok] / 2


def find_mesh_axis(body: MeshBody, tol_mm: float = 0.05):
    """Axis point c, unit direction d, and the fraction of area that isn't turned.

    1. Direction: area-weighted normal covariance. On a turned part the side
       normals spread evenly round the axis (two equal eigenvalues) and the
       end faces point along it, so the axis is the odd eigenvector out.
    2. Position: every side / cone / torus normal, seen end-on, points at the
       axis. Solve for the point they all aim at, weighting out facets that miss
       (flats, slots, clamp areas)."""
    p, n, a = _facets(body)
    M = (n * a[:, None]).T @ n
    vals, vecs = np.linalg.eigh(M)
    gaps = [abs(vals[0] - vals[1]) + abs(vals[0] - vals[2]),
            abs(vals[1] - vals[0]) + abs(vals[1] - vals[2]),
            abs(vals[2] - vals[0]) + abs(vals[2] - vals[1])]
    # the distinct eigenvalue is the one farthest from the other two
    k = int(np.argmax([min(abs(vals[i] - vals[j]) for j in range(3) if j != i) for i in range(3)]))
    d = vecs[:, k] / np.linalg.norm(vecs[:, k])
    if d[np.argmax(np.abs(d))] < 0:
        d = -d

    # Position from VERTEX normals: on a revolved tessellation a vertex's averaged normal
    # lies in its meridian plane, while a facet's normal only does at the facet's middle.
    V, F = body.vertices, body.triangles
    tv = V[F]
    fn = np.cross(tv[:, 1] - tv[:, 0], tv[:, 2] - tv[:, 0])          # area-weighted facet normals
    vn = np.zeros_like(V)
    for k in range(3):
        np.add.at(vn, F[:, k], fn)
    va = np.linalg.norm(vn, axis=1)
    okv = va > 1e-12
    p, n, a = V[okv], vn[okv] / va[okv, None], va[okv] / 6
    along = n @ d
    side = np.abs(along) < 0.999
    if side.sum() < 3:
        raise StepImportError("no round faces found in this mesh - it doesn't look like a turned part")
    ref = np.array([1.0, 0, 0]) if abs(d[0]) < 0.9 else np.array([0, 1.0, 0])
    e1 = np.cross(d, ref)
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(d, e1)
    q2 = np.stack([p[side] @ e1, p[side] @ e2], 1)
    n2 = np.stack([n[side] @ e1, n[side] @ e2], 1)
    n2 /= np.linalg.norm(n2, axis=1)[:, None]
    t2 = np.stack([-n2[:, 1], n2[:, 0]], 1)            # perpendicular to the normal, in the end view
    w = a[side].copy()
    c2 = (q2 * w[:, None]).sum(0) / w.sum()
    for _ in range(8):
        # distance from c2 to each normal line = t2 . (c2 - q2) -> linear least squares in c2
        A = t2 * np.sqrt(w)[:, None]
        b = (t2 * q2).sum(1) * np.sqrt(w)
        c2, *_ = np.linalg.lstsq(A, b, rcond=None)
        miss = np.abs((t2 * (c2 - q2)).sum(1))
        w = a[side] * np.where(miss < tol_mm, 1.0, tol_mm / np.maximum(miss, 1e-12))
    c2 = _refine_center(body.vertices, d, e1, e2, c2)
    c = c2[0] * e1 + c2[1] * e2 + ((p * a[:, None]).sum(0) / a.sum() @ d) * d
    # "not turned" share, measured on facets (vertex normals blur flats into their rims)
    pf, nf, af = _facets(body)
    qf = pf - c
    radial = qf - np.outer(qf @ d, d)
    tangential_miss = np.abs(np.einsum("ij,ij->i", np.cross(radial, nf), d[None, :].repeat(len(nf), 0)))
    rlen = np.maximum(np.linalg.norm(radial, axis=1), 1e-9)
    # a facet is turned if it faces along the axis, or its normal lies (nearly) in the meridian plane
    in_meridian = tangential_miss / rlen < math.sin(math.radians(12))
    turned = (np.abs(nf @ d) > 0.999) | in_meridian
    return c, d, float(af[~turned].sum() / af.sum())


def _refine_center(V, d, e1, e2, c2, rounds=3):
    """Revolved meshes are built from rings of vertices, each an exact circle round the
    axis. Group vertices into rings (same height, about the same radius), fit a circle
    to every ring that goes most of the way round, and take the median centre."""
    z = V @ d
    xy = np.stack([V @ e1, V @ e2], 1)
    for _ in range(rounds):
        r = np.linalg.norm(xy - c2, axis=1)
        span = max(float(r.max()), 1e-6)
        key_z = np.round(z / max(span * 1e-5, 1e-6)).astype(np.int64)
        key_r = np.round(r / max(span * 0.01, 1e-6)).astype(np.int64)
        keys = key_z * 1_000_003 + key_r
        order = np.argsort(keys)
        ks = keys[order]
        cuts = np.flatnonzero(np.diff(ks)) + 1
        centres, weights = [], []
        for grp in np.split(order, cuts):
            if len(grp) < 12:
                continue
            pts = xy[grp]
            ang = np.sort(np.arctan2(pts[:, 1] - c2[1], pts[:, 0] - c2[0]))
            gaps = np.diff(np.concatenate([ang, ang[:1] + 2 * np.pi]))
            if gaps.max() > np.pi / 2:          # ring doesn't go most of the way round
                continue
            A = np.stack([pts[:, 0], pts[:, 1], np.ones(len(pts))], 1)
            sol, *_ = np.linalg.lstsq(A, (pts ** 2).sum(1), rcond=None)
            cc = sol[:2] / 2
            rr = math.sqrt(max(sol[2] + cc @ cc, 0))
            if np.abs(np.linalg.norm(pts - cc, axis=1) - rr).max() > max(1e-3 * rr, 0.01):
                continue                         # not a clean circle (notch, slot)
            centres.append(cc)
            weights.append(len(grp))
        if not centres:
            break
        cs = np.array(centres)
        c2 = np.array([np.median(cs[:, 0]), np.median(cs[:, 1])])
    return c2


# ----------------------------------------------------------------------------- section
def _section(body: MeshBody, c, d, u):
    """Cut with the plane through the axis containing u. Returns chains of (z, r) points
    on the r >= 0 side, chained by shared mesh edges."""
    V, F = body.vertices, body.triangles
    nrm = np.cross(d, u)
    s = (V - c) @ nrm
    s = np.where(np.abs(s) < 1e-9, 1e-9, s)           # nothing exactly on the plane
    sf = s[F]
    cross_tri = ~((sf > 0).all(1) | (sf < 0).all(1))
    F, sf = F[cross_tri], sf[cross_tri]
    pts, keys = [], []
    for i, j in ((0, 1), (1, 2), (2, 0)):
        a, b = F[:, i], F[:, j]
        sa, sb = sf[:, i], sf[:, j]
        lo, hi = np.minimum(a, b), np.maximum(a, b)
        slo = np.where(a < b, sa, sb)
        shi = np.where(a < b, sb, sa)
        t = slo / (slo - shi)
        pt = V[lo] + (V[hi] - V[lo]) * t[:, None]
        hit = (sa > 0) != (sb > 0)
        pts.append(np.where(hit[:, None], pt, np.nan))
        keys.append(np.where(hit, lo * (len(V) + 1) + hi, -1))
    P = np.stack(pts, 1)          # (k, 3 edges, 3)
    K = np.stack(keys, 1)
    adj: dict[int, list[int]] = {}
    where: dict[int, tuple] = {}
    for ti in range(len(F)):
        ks = [K[ti, e] for e in range(3) if K[ti, e] >= 0]
        if len(ks) != 2:
            continue
        for e in range(3):
            if K[ti, e] >= 0:
                where[int(K[ti, e])] = tuple(P[ti, e])
        a_, b_ = int(ks[0]), int(ks[1])
        adj.setdefault(a_, []).append(b_)
        adj.setdefault(b_, []).append(a_)

    def local(p):
        q = np.asarray(p) - c
        return float(q @ d), float(q @ u)

    seen = set()
    chains = []
    starts = [k for k, v in adj.items() if len(v) == 1] + list(adj)
    for s0 in starts:
        if s0 in seen:
            continue
        chain = [s0]
        seen.add(s0)
        cur = s0
        while True:
            nxt = next((x for x in adj[cur] if x not in seen), None)
            if nxt is None:
                break
            chain.append(nxt)
            seen.add(nxt)
            cur = nxt
        if len(chain) > 2 and s0 in adj.get(chain[-1], []):
            chain.append(s0)   # closed
        chains.append([local(where[k]) for k in chain])

    # keep the r >= 0 half; split chains where they cross the axis
    out = []
    for ch in chains:
        piece = []
        for i, (z, r) in enumerate(ch):
            if r >= 0:
                if not piece and i > 0 and ch[i - 1][1] < 0:
                    zp, rp = ch[i - 1]
                    piece.append((zp + (z - zp) * (-rp) / (r - rp), 0.0))
                piece.append((z, r))
            elif piece:
                zp, rp = piece[-1]
                piece.append((zp + (z - zp) * rp / (rp - r), 0.0))
                out.append(piece)
                piece = []
        if len(piece) > 1:
            out.append(piece)
    # a closed loop split at its seam: rejoin the end to the start
    joined = []
    for pc in out:
        for other in joined:
            if math.dist(other[-1], pc[0]) < 1e-6:
                other.extend(pc[1:])
                break
            if math.dist(pc[-1], other[0]) < 1e-6:
                other[:0] = pc[:-1]
                break
        else:
            joined.append(pc)
    return joined


def _poly_area(pts):
    if math.dist(pts[0], pts[-1]) > 1e-6:
        pts = pts + [(pts[-1][0], 0.0), (pts[0][0], 0.0)]
    return abs(sum(pts[i][0] * pts[i + 1][1] - pts[i + 1][0] * pts[i][1] for i in range(-1, len(pts) - 1))) / 2


# ----------------------------------------------------------------------------- fitting
def _circle(a, b, c):
    ax, ay = a
    bx, by = b
    cx, cy = c
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:
        return None
    ux = ((ax**2 + ay**2) * (by - cy) + (bx**2 + by**2) * (cy - ay) + (cx**2 + cy**2) * (ay - by)) / d
    uy = ((ax**2 + ay**2) * (cx - bx) + (bx**2 + by**2) * (ax - cx) + (cx**2 + cy**2) * (bx - ax)) / d
    return (ux, uy), math.dist(a, (ux, uy))


def _lsq_circle(Q):
    """Algebraic least-squares circle through points Q (k, 2): centre, radius."""
    x, y = Q[:, 0], Q[:, 1]
    A = np.stack([x, y, np.ones_like(x)], 1)
    b = x * x + y * y
    sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    cx, cy = sol[0] / 2, sol[1] / 2
    r2 = sol[2] + cx * cx + cy * cy
    if r2 <= 0:
        return None
    return (float(cx), float(cy)), float(math.sqrt(r2))


def _line_ok(P, i, j, tol):
    if j - i >= 2:
        # a straight run doesn't turn: stop at the first chord that bends away (start of an arc)
        seg = np.diff(P[i:j + 1], axis=0)
        h = np.unwrap(np.arctan2(seg[:, 1], seg[:, 0]))
        if np.abs(np.diff(h)).max() > math.radians(3):
            return False
    a, b = P[i], P[j]
    L = np.linalg.norm(b - a)
    if L < 1e-9:
        return False
    seg = P[i:j + 1] - a
    dev = np.abs(seg[:, 0] * (b - a)[1] - seg[:, 1] * (b - a)[0]) / L
    return bool(dev.max() <= tol)


def _arc_ok(P, i, j, tol):
    if j - i < 4:
        return None
    circ = _lsq_circle(P[i:j + 1])
    if circ is None:
        return None
    (cx, cy), r = circ
    if r > 5000:
        return None
    dev = np.abs(np.hypot(P[i:j + 1, 0] - cx, P[i:j + 1, 1] - cy) - r)
    if dev.max() > tol:
        return None
    # the points must sweep steadily one way round, or it's not an arc
    ang = np.unwrap(np.arctan2(P[i:j + 1, 1] - cy, P[i:j + 1, 0] - cx))
    steps = np.diff(ang)
    if not ((steps > -1e-9).all() or (steps < 1e-9).all()):
        return None
    # ...in small steps with no sharp turns: a few points round a corner aren't an arc
    if np.abs(steps).max() > math.radians(20):
        return None
    seg = np.diff(P[i:j + 1], axis=0)
    # a straight face is one long gap between cut points; a curve is many short ones that
    # change gradually. Reject a run where one gap is far longer than the gap beside it.
    chord = np.linalg.norm(seg, axis=1)
    if len(chord) > 1 and (np.maximum(chord[1:], chord[:-1]) > 3 * np.minimum(chord[1:], chord[:-1])).any():
        return None
    heading = np.unwrap(np.arctan2(seg[:, 1], seg[:, 0]))
    if len(heading) > 1 and np.abs(np.diff(heading)).max() > math.radians(20):
        return None
    if abs(ang[-1] - ang[0]) > math.pi * 0.98:
        return None
    return (cx, cy), r, ("ccw" if ang[-1] > ang[0] else "cw")


def fit_polyline(pts, tol=FIT_TOL) -> list[Seg]:
    """Greedy: from each point take the longest line or arc that stays within tol."""
    # Cutting just beside a ring of vertices gives tight clusters of points (one per edge
    # leaving each vertex). Collapse anything closer than 0.001 mm to one point.
    raw = np.array(pts, dtype=np.float64)
    gaps = np.linalg.norm(np.diff(raw, axis=0), axis=1)
    merge = max(1e-3, 0.05 * float(np.median(gaps[gaps > 1e-6])) if (gaps > 1e-6).any() else 1e-3)
    keep = [raw[0]]
    for q in raw[1:]:
        if np.linalg.norm(q - keep[-1]) > merge:
            keep.append(q)
    if np.linalg.norm(raw[-1] - keep[-1]) > 0:
        keep[-1] = raw[-1]
    P = np.array(keep)
    segs: list[Seg] = []
    i, n = 0, len(P)
    while i < n - 1:
        j_line = i + 1
        while j_line + 1 < n and _line_ok(P, i, j_line + 1, tol):
            j_line += 1
        j_arc, arc = i, None
        j = i + 3
        while j < n:
            got = _arc_ok(P, i, j, tol)
            if got is None:
                if j - j_arc > 6:      # give up once several extensions in a row fail
                    break
            else:
                j_arc, arc = j, got
            j += 1
        if arc is not None and j_arc > j_line + 1:
            ctr, r, direction = arc
            segs.append(Seg(tuple(P[i]), tuple(P[j_arc]), "arc", ctr, r, direction, approx=False))
            segs[-1].span = (i, j_arc)
            i = j_arc
        else:
            segs.append(Seg(tuple(P[i]), tuple(P[j_line])))
            segs[-1].span = (i, j_line)
            i = j_line
    return _merge_arcs(segs, P, tol)


def _merge_arcs(segs: list[Seg], P, tol) -> list[Seg]:
    """Join neighbouring arcs that one circle fits within tol (a groove the greedy pass
    split in two), then pull arc ends onto their circle so the radius is consistent."""
    out: list[Seg] = []
    for s in segs:
        prev = out[-1] if out else None
        if prev is not None and prev.kind == "arc" and s.kind == "arc" and prev.direction == s.direction:
            i, j = prev.span[0], s.span[1]
            got = _arc_ok(P, i, j, tol)
            if got is not None:
                ctr, r, direction = got
                merged = Seg(prev.p0, s.p1, "arc", ctr, r, direction)
                merged.span = (i, j)
                out[-1] = merged
                continue
        out.append(s)
    # snap: move arc end points radially onto the fitted circle, and share them with neighbours
    for k, s in enumerate(out):
        if s.kind != "arc":
            continue
        def onto(p):
            dz, dr = p[0] - s.center[0], p[1] - s.center[1]
            L = math.hypot(dz, dr) or 1.0
            return (s.center[0] + dz / L * s.radius, s.center[1] + dr / L * s.radius)
        s.p0, s.p1 = onto(s.p0), onto(s.p1)
        if k > 0:
            out[k - 1].p1 = s.p0
        if k + 1 < len(out):
            out[k + 1].p0 = s.p1
    return out


# ----------------------------------------------------------------------------- import
def import_mesh(path, body_index: int | None = None, units: str = "inch", flip: bool = False,
                tol: float = FIT_TOL) -> ImportResult:
    bodies = read_mesh(path)
    if body_index is None:
        if len(bodies) > 1:
            raise StepImportError(f"{Path(path).name} has {len(bodies)} objects - choose one")
        body_index = 0
    body = bodies[body_index]
    c, d, odd = find_mesh_axis(body)
    warnings = ["imported from a mesh: sizes are only as good as the mesh. Check them against the print"]
    if odd > 0.002:
        warnings.append(f"about {odd * 100:.0f}% of the surface isn't turned (flats, slots, clamp areas). "
                        "The profile is taken where the part is fullest - machine those features separately")

    # cut through rings of vertices: aim the cut at the outermost vertices' angles
    ref = np.array([1.0, 0, 0]) if abs(d[0]) < 0.9 else np.array([0, 1.0, 0])
    u0 = np.cross(d, ref)
    u0 /= np.linalg.norm(u0)
    w0 = np.cross(d, u0)
    q = body.vertices - c
    radial = q - np.outer(q @ d, d)
    rad = np.linalg.norm(radial, axis=1)
    ang = np.arctan2(radial @ w0, radial @ u0)
    outer = ang[rad > rad.max() * 0.98]
    targets = [math.radians(7 + 30 * i) for i in range(12)]
    angles = []
    for t in targets:
        diff = np.angle(np.exp(1j * (outer - t)))
        angles.append(float(outer[np.argmin(np.abs(diff))]) + 1e-6)

    best = None
    areas = []
    for th in angles:
        u = math.cos(th) * u0 + math.sin(th) * w0
        chains = _section(body, c, d, u)
        if not chains:
            continue
        main = max(chains, key=_poly_area)
        a = _poly_area(main)
        areas.append(a)
        if best is None or a > best[0] + 1e-9:
            best = (a, th, main, chains)
    if best is None:
        raise StepImportError("cutting the mesh through its axis gave nothing")
    _, th, main, chains = best
    if areas and (max(areas) - min(areas)) > 1e-3 * max(areas) and odd <= 0.002:
        warnings.append("the outline changes around the axis - the fullest cut was used")
    loop = fit_polyline(main, tol)
    return finish_loop(loop, flip, units, warnings, body.name, round(math.degrees(th) % 360, 1),
                       tol=max(10 * tol, 0.05))
