"""Read an OmniTurn program back and draw it: rapids, feeds, arcs, C/R corners,
and the contour each G75/G78 cycle roughs to. Interpreting the output (rather
than drawing from the job) means the plot shows what the machine will be told.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .geometry import Arc, Line, Point, expand_moves

WORD = re.compile(r"([a-z}])(-?\d*\.?\d*)")


@dataclass
class Path:
    kind: str                    # "rapid" | "feed" | "contour" | "drill"
    tool: int | None
    start: Point
    segments: list = field(default_factory=list)

    def points(self):
        return expand_moves(self.start, self.segments)


def parse(program: str):
    main, subs, cur = [], {}, None
    for raw in program.splitlines():
        code = re.sub(r"\([^)]*\)", "", raw).strip().lower().replace(" ", "").lstrip("/")
        if not code:
            continue
        if code.startswith("}"):
            cur = code[1:]
            subs[cur] = []
            continue
        if code == "m99":
            cur = None
            continue
        words = [(a, b) for a, b in WORD.findall(code)]
        (subs[cur] if cur else main).append(words)
    return main, subs


def interpret(program: str, arc_codes=None) -> list[Path]:
    arc_dir = {v.lstrip("g"): k for k, v in (arc_codes or {"ccw": "g02", "cw": "g03"}).items()}
    main, subs = parse(program)
    paths: list[Path] = []
    x = z = None
    motion = "rapid"
    tool = None
    current: Path | None = None

    def contour_path(lines, start_tool) -> Path | None:
        cx = cz = None
        p = None
        for words in lines:
            d = dict(words)
            g = [b for a, b in words if a == "g"]
            nx = float(d["x"]) if d.get("x") else cx
            nz = float(d["z"]) if d.get("z") else cz
            if p is None:
                p = Path("contour", start_tool, Point(nx, nz))
            elif any(c in arc_dir for c in g):
                code = next(c for c in g if c in arc_dir)
                p.segments.append(Arc(Point(nx, nz), float(d["r"]), arc_dir[code]))
            else:
                p.segments.append(Line(Point(nx, nz), float(d["c"]) if d.get("c") else None,
                                       float(d["r"]) if d.get("r") else None))
            cx, cz = nx, nz
        return p

    for words in main:
        d = dict(words)
        gs = [b for a, b in words if a == "g"]
        if "t" in d and d["t"]:
            tool = int(d["t"])
            current = None
            continue
        if "75" in gs or "78" in gs:
            if d.get("p") and d["p"] in subs:
                cp = contour_path(subs[d["p"]], tool)
                if cp:
                    paths.append(cp)
            current = None
            continue
        if "81" in gs or "83" in gs:
            zd = float(d["z"])
            paths.append(Path("drill", tool, Point(x, z), [Line(Point(x, zd)), Line(Point(x, z))]))
            current = None
            continue
        if "00" in gs:
            motion = "rapid"
        if "01" in gs or "95" in gs:
            motion = "feed"
        if "94" in gs and d.get("f") and float(d["f"]) >= 300:
            motion = "rapid"
        nx = float(d["x"]) if d.get("x") else x
        nz = float(d["z"]) if d.get("z") else z
        if nx == x and nz == z:
            continue
        if x is None or z is None:
            x, z = nx, nz
            continue
        arc_code = next((c for c in gs if c in arc_dir), None)
        kind = "feed" if (arc_code or motion == "feed") else "rapid"
        if current is None or current.kind != kind or current.tool != tool:
            current = Path(kind, tool, Point(x, z))
            paths.append(current)
        if arc_code:
            current.segments.append(Arc(Point(nx, nz), float(d["r"]), arc_dir[arc_code]))
        else:
            current.segments.append(Line(Point(nx, nz), float(d["c"]) if d.get("c") else None,
                                         float(d["r"]) if d.get("r") and kind == "feed" else None))
        x, z = nx, nz
    return paths


PALETTE = ["#2a6fdb", "#e0782c", "#2e9e5b", "#b03a9a", "#c9a227", "#5b5b5b"]


def draw(ax, program: str, stock_dia: float | None = None, profile=None) -> None:
    """Draw a backplot onto a matplotlib Axes (radius scale, print view)."""
    paths = interpret(program)
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
        ax.fill_between([zmin, max(profile.bounds()[3], 0)], 0, stock_dia / 2,
                        color="#d9dde3", alpha=0.6, lw=0)
    pts = profile.polyline()
    ax.plot([p[0] for p in pts], [p[1] / 2 for p in pts], lw=2, color="#2a6fdb")
    ax.plot([profile.start.z], [profile.start.r], "o", color="#2e9e5b", ms=5)
    if highlight is not None and 0 <= highlight < len(profile.segments):
        segs = profile.segments
        start = profile.start if highlight == 0 else segs[highlight - 1].end
        seg = segs[highlight]
        # draw the highlighted segment as a sharp line/arc on its own
        hp = expand_moves(start, [type(seg)(seg.end, seg.radius, seg.direction)] if isinstance(seg, Arc)
                          else [Line(seg.end)])
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


def plot(program: str, out_file: str, stock_dia: float | None = None, title: str = "") -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(10, 5))
    draw(ax, program, stock_dia)
    ax.set_title(title or "OmniTurn backplot - feeds solid, rapids dotted, G75/G78 contour grey")
    fig.tight_layout()
    fig.savefig(out_file, dpi=130)
    plt.close(fig)
