"""OmniTurn G4 post-processor.

Every formatting rule here traces back to the OmniTurn operator manual; see
docs/omniturn-post-rules.md for the list with page references. The output is
checked by omniturn_cam.lint before it is returned.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..gcode import PostError, fmt, sanitize_comment  # noqa: F401  (re-exported)
from ..geometry import Arc, Point, Profile
from ..job import Job
from ..operations import Operation, spec_for
from ..tools import Station


@dataclass
class MachineConfig:
    # Default profile: Hardinge HLV-H toolroom lathe retrofitted with the OmniTurn
    # G4 control and CNC package (not a factory OmniTurn GT-75). Override per
    # machine with ~/.config/omniturn-cam/machine.toml.
    name: str = "Hardinge HLV-H OmniTurn G4"
    units: str = "inch"
    rapid_feed: float = 300.0  # G94 F at 'rapid' (300 IPM is the machine max)
    tool_call_z: float = 1.0  # safe Z for tool calls and between tools
    z_clear: float = 0.05  # approach clearance in front of features
    x_clear: float = 0.05  # radial clearance over stock (added on diameter x2)
    max_rpm: float = 4000.0  # HLV-H on the OmniTurn VFD
    x_travel: float | None = 10.0  # slide travel, inch (radial). VERIFY on the machine
    z_travel: float | None = 9.0  # slide travel, inch. VERIFY on the machine
    max_bar: float | None = 1.0625  # 5C round collet capacity (inch); None = no check
    decimals: int = 4  # .0001 on diameter in G72 (manual p2.6)
    feed_decimals: int = 5
    arc_format: str = "r"  # "r" or "ik"
    # Arc direction as seen on the print (Z+ right, X+ up) -> OmniTurn code.
    # Derived from the manual's I/K examples on p2.18-2.19, where a convex
    # face-to-OD corner (print-CCW) is G02. The OmniTurn's X+ points down.
    # Confirmed against the raised bead in the p2.43 drawing.
    arc_codes: dict = field(default_factory=lambda: {"ccw": "g02", "cw": "g03"})
    max_comment: int = 32
    # First subroutine number for generated contours. Subroutine 1 is special:
    # M95 jumps to it on the loader input (manual p2.55), and saved loader
    # subroutine files are usually numbered from 1. Contours are also moved
    # past any numbers used by the job's include file.
    first_sub: int = 1

    @classmethod
    def for_units(cls, units: str, overrides: dict | None = None) -> MachineConfig:
        if units == "metric":
            m = cls(
                units="metric",
                rapid_feed=7620.0,
                tool_call_z=25.0,
                z_clear=1.0,
                x_clear=1.0,
                decimals=3,
                feed_decimals=3,
                max_bar=27.0,
                x_travel=254.0,
                z_travel=228.6,
            )
        else:
            m = cls()
        for k, v in (overrides or {}).items():
            if k == "units" or k not in cls.__dataclass_fields__:
                continue
            setattr(m, k, v)
        return m

    @classmethod
    def load(cls, path, units: str) -> MachineConfig:
        """Read a machine.toml: top-level keys apply to both units, [inch]/[metric] tables
        override for one unit system."""
        import tomllib
        from pathlib import Path

        data = tomllib.loads(Path(path).read_text())
        common = {k: v for k, v in data.items() if not isinstance(v, dict)}
        return cls.for_units(units, common | data.get(units, {}))


class OmniTurnPost:
    def __init__(self, job: Job, machine: MachineConfig | None = None):
        self.job = job
        self.m = machine or MachineConfig.for_units(job.units)
        self.lines: list[str] = []
        self.x: float | None = None
        self.z: float | None = None
        self.motion: str | None = None  # "rapid" | "feed" | None (unknown)
        self.feed_mode = "g94"
        self.feed: float | None = None
        self.tool: int | None = None
        self.rpm: float | None = None
        self.spindle: str | None = None
        self.coolant = False
        self.subs: dict[str, int] = {}  # profile name -> subroutine number
        self.warnings: list[str] = []
        self.external_text, self.external_nums = job.external_subs()

    # ------------------------------------------------------------------ format
    def n(self, v: float) -> str:
        return fmt(v, self.m.decimals)

    def f(self, v: float) -> str:
        return fmt(v, self.m.feed_decimals)

    def check_travel(self, code: str) -> None:
        """Warn when the program's moves span more than the slide can travel. This only
        catches gross problems: where the travel sits relative to the part depends on
        the tool offsets, which the post doesn't know."""
        from ..backplot import interpret

        pts = [
            q
            for path in interpret(code, self.m.arc_codes)
            for q in path.points()
            if q[0] is not None and q[1] is not None
        ]
        if not pts:
            return
        zs = [q[0] for q in pts]
        xs = [q[1] for q in pts]
        z_span = max(zs) - min(zs)
        x_span = (max(xs) - min(xs)) / 2  # diameter -> slide travel
        if self.m.z_travel and z_span > self.m.z_travel:
            self.warn(f"program moves {z_span:.3f} in Z but the {self.m.name} has {self.m.z_travel:g} of Z travel")
        if self.m.x_travel and x_span > self.m.x_travel:
            self.warn(
                f"program moves {x_span:.3f} in X (radial) but the {self.m.name} has {self.m.x_travel:g} of X travel"
            )

    def next_sub(self) -> int:
        taken = set(self.subs.values()) | set(self.external_nums)
        n = self.m.first_sub
        while n in taken:
            n += 1
        return n

    def warn(self, text: str) -> None:
        if text not in self.warnings:
            self.warnings.append(text)

    def emit(self, line: str) -> None:
        self.lines.append(line)

    def comment(self, text: str) -> None:
        c = sanitize_comment(text, self.m.max_comment)
        if c:
            self.emit(f"({c})")

    def _axes(self, x, z, both=False) -> str:
        w = ""
        if x is not None and (both or self.x is None or abs(x - self.x) > 1e-9):
            w += "x" + self.n(x)
        if z is not None and (both or self.z is None or abs(z - self.z) > 1e-9):
            w += "z" + self.n(z)
        return w

    def _moved(self, x, z):
        if x is not None:
            self.x = x
        if z is not None:
            self.z = z

    # ------------------------------------------------------------------ motion
    def rapid(self, x=None, z=None, both=False) -> None:
        words = self._axes(x, z, both)
        if not words:
            return
        prefix = "" if self.motion == "rapid" else "g00"
        self.emit(prefix + words)
        self.motion = "rapid"
        self._moved(x, z)

    def _feed_prefix(self, feed: float) -> str:
        if self.feed_mode != "g95" or self.feed is None or abs(feed - self.feed) > 1e-12:
            self.feed_mode, self.feed = "g95", feed
            return "g95f" + self.f(feed)
        return "" if self.motion == "feed" else "g01"

    def set_feed(self, feed: float) -> None:
        """Establish IPR mode and feed on a line of its own (before cycles/arcs)."""
        if self.feed_mode != "g95" or self.feed is None or abs(feed - self.feed) > 1e-12:
            self.emit("g95f" + self.f(feed))
            self.feed_mode, self.feed = "g95", feed

    def cut(self, x=None, z=None, feed: float = 0.0, extra: str = "") -> None:
        words = self._axes(x, z)
        if not words:
            return
        self.emit(self._feed_prefix(feed) + words + extra)
        self.motion = "feed"
        self._moved(x, z)

    def dwell(self, seconds: float) -> None:
        if seconds <= 0:
            return
        self.emit("g04f" + fmt(max(seconds, 0.1), 2))
        # G04 uses the F word; force the next cutting move to restate its feed.
        self.feed = None

    # ------------------------------------------------------------------ geometry
    def arc_words(self, start: Point, seg: Arc) -> str:
        code = self.m.arc_codes[seg.direction]
        w = f"{code}x{self.n(seg.end.x)}z{self.n(seg.end.z)}"
        if self.m.arc_format == "ik":
            cz, cr = seg.center(start)
            # I and K are incremental from arc START to centre; I is a radial
            # distance even in diameter mode (manual p2.19 G03 example).
            w += f"i{self.n(cr - start.r)}k{self.n(cz - start.z)}"
        else:
            w += f"r{self.n(seg.radius)}"
        return w

    def contour(self, prof: Profile, feed: float | None, first_extra: str = "") -> None:
        """Emit the profile segments (tool already at prof.start)."""
        cur = prof.start
        for seg in prof.segments:
            if isinstance(seg, Arc):
                self.emit(self.arc_words(cur, seg))
                self.x, self.z = seg.end.x, seg.end.z
            else:
                corner = ""
                if seg.chamfer:
                    corner = "c" + self.n(seg.chamfer)
                elif seg.radius:
                    corner = "r" + self.n(seg.radius)
                if feed is None:
                    words = self._axes(seg.end.x, seg.end.z)
                    if words:
                        self.emit(words + corner)
                    self._moved(seg.end.x, seg.end.z)
                else:
                    self.cut(seg.end.x, seg.end.z, feed, corner)
            cur = seg.end

    # ------------------------------------------------------------------ spindle / tools
    def station(self, op: Operation) -> Station:
        return self.job.setup[op.t]

    def est_rpm(self, op: Operation, dia: float) -> float:
        if op.rpm:
            if op.rpm > self.m.max_rpm:
                self.warn(
                    f"T{op.t} {op.label}: {op.rpm:g} rpm is over the {self.m.max_rpm:g} rpm "
                    f"limit set for the {self.m.name}"
                )
            return op.rpm
        if op.sfm:
            k = 1000.0 if self.job.units == "metric" else 12.0
            rpm = op.sfm * k / (math.pi * max(dia, 1e-3))
            return min(rpm, op.max_rpm or self.m.max_rpm)
        raise PostError(f"t{op.t} {op.label}: give rpm or sfm")

    def spindle_on(self, op: Operation, dia: float) -> None:
        rpm = round(self.est_rpm(op, dia))
        if self.spindle != op.spindle or self.rpm != rpm:
            if self.spindle != op.spindle:
                self.emit(f"{op.spindle}s{rpm}")
            else:
                self.emit(f"s{rpm}")
            self.spindle, self.rpm = op.spindle, rpm
        if op.coolant and not self.coolant:
            self.emit("m08")
            self.coolant = True
        elif not op.coolant and self.coolant:
            self.emit("m09")
            self.coolant = False

    def css_on(self, op: Operation) -> None:
        if op.sfm and not op.rpm:
            self.emit(f"g96s{fmt(op.sfm, 1)}")
            if op.min_rpm:
                self.emit(f"g76s{round(op.min_rpm)}")
            self.emit(f"g77s{round(op.max_rpm or self.m.max_rpm)}")

    def css_off(self, op: Operation) -> None:
        if op.sfm and not op.rpm:
            self.emit("g97")
            self.emit(f"s{self.rpm}")

    def call_tool(self, op: Operation, approach_x: float, spindle_dia: float) -> None:
        st = self.station(op)
        if self.tool != op.t:
            if self.tool is not None:
                self.rapid(z=self.m.tool_call_z)
            if self.motion != "rapid":
                self.emit("g00")
                self.motion = "rapid"
            name = sanitize_comment(st.tool.comment, self.m.max_comment)
            self.emit(f"t{op.t}({name})" if name else f"t{op.t}")
            # The line after a tool call must carry BOTH X and Z (manual p2.9).
            self.x = self.z = None
            self.rapid(approach_x, self.m.tool_call_z, both=True)
            self.tool = op.t
        self.spindle_on(op, spindle_dia)
        if op.comment:
            self.comment(op.comment)

    # ------------------------------------------------------------------ shared by operations
    # Each operation writes its own code (operations/<kind>.py, its `emit`),
    # using the motion, spindle and tool helpers above and the ones below.
    @property
    def stock_x(self) -> float:
        return self.job.stock.diameter + 2 * self.m.x_clear

    def start_point(self, op, prof: Profile) -> tuple[float, float]:
        _, _, _, zmax = prof.bounds()
        sz = op.start_z if op.start_z is not None else zmax + self.m.z_clear
        if op.start_x is not None:
            sx = op.start_x
        elif prof.side == "od":
            sx = max(self.job.stock.diameter, prof.bounds()[1]) + 2 * self.m.x_clear
        elif self.job.stock.bore:
            sx = self.job.stock.bore - 2 * self.m.x_clear
        else:
            raise PostError(f"ID profile {prof.name!r}: set start_x (pre-drilled hole diameter)")
        return sx, sz

    @staticmethod
    def check_quadrant(sx, sz, prof: Profile) -> None:
        """G75/G78: contour start A and end B must be in the same quadrant of S (p2.42)."""

        def q(p: Point):
            return (
                math.copysign(1, p.x - sx) if abs(p.x - sx) > 1e-9 else 0,
                math.copysign(1, p.z - sz) if abs(p.z - sz) > 1e-9 else 0,
            )

        qa, qb = q(prof.start), q(prof.end)
        for a, b in zip(qa, qb, strict=True):
            if a and b and a != b:
                raise PostError(
                    f"profile {prof.name!r}: start x{prof.start.x} z{prof.start.z} and end "
                    f"x{prof.end.x} z{prof.end.z} are not in the same quadrant relative to the "
                    f"cycle start x{sx} z{sz} - the control will reject this (manual p2.42)"
                )

    def leave_known_state(self) -> None:
        """Before handing control to code we didn't write, park the slide at safe Z."""
        if self.tool is not None:
            self.rapid(z=self.m.tool_call_z)

    def forget_state(self) -> None:
        # the subroutine / raw code may move, change tools or spindle: forget what we know
        self.x = self.z = self.motion = self.feed = None
        self.spindle = self.rpm = self.tool = None

    # ------------------------------------------------------------------ program
    def header(self) -> None:
        j = self.job
        self.comment(j.name)
        if j.description:
            self.comment(j.description)
        if j.units == "metric":
            self.comment("UNITS METRIC MM G71")
        else:
            self.comment("UNITS INCH G70")
        self.comment("DIAMETER MODE G72 ABSOLUTE G90")
        self.comment(self.m.name)
        if self.m.max_bar and j.stock.diameter > self.m.max_bar:
            self.warn(
                f"bar diameter {j.stock.diameter:g} is over the {self.m.max_bar:g} collet "
                f"capacity set for the {self.m.name}"
            )
        # Safe start: cancel TNR comp, set units, absolute, diameter, IPM, RPM mode.
        self.emit("g40")
        self.emit("g71" if j.units == "metric" else "g70")
        self.emit(f"g90g72g94g97f{fmt(self.m.rapid_feed, 1)}")
        self.feed_mode, self.feed = "g94", self.m.rapid_feed

    def footer(self) -> None:
        self.rapid(z=self.m.tool_call_z)
        if self.coolant:
            self.emit("m09")
        self.emit("m05")
        self.emit("m30")
        for name, num in self.subs.items():
            prof = self.job.profiles[name]
            self.emit(f"}}{num}")
            self.x = self.z = None
            self.emit(self._axes(prof.start.x, prof.start.z, both=True))
            self.x, self.z = prof.start.x, prof.start.z
            self.contour(prof, feed=None)
            self.emit("m99")
        if self.external_text.strip():
            for line in self.external_text.splitlines():
                if line.strip():
                    self.emit(line.strip())

    def run(self) -> str:
        self.header()
        for op in self.job.operations:
            spec_for(op).emit(self, op)
        self.footer()
        return "\n".join(self.lines) + "\n"


def post(job: Job, machine: MachineConfig | None = None, check: bool = True, warnings: list | None = None) -> str:
    """Generate the program. Pass a list as `warnings` to collect non-fatal notes."""
    try:
        job.validate()
    except (KeyError, ValueError) as e:
        raise PostError(str(e).strip('"')) from None
    if machine is not None and machine.units != job.units:
        machine = MachineConfig.for_units(job.units, {k: getattr(machine, k) for k in ("name", "max_rpm")})
    p = OmniTurnPost(job, machine)
    code = p.run()
    p.check_travel(code)
    if warnings is not None:
        warnings.extend(p.warnings)
    if check:
        from ..lint import lint

        problems = [p for p in lint(code) if p.severity == "error"]
        if problems:
            raise PostError("generated code failed the OmniTurn rules check:\n" + "\n".join(str(p) for p in problems))
    return code
