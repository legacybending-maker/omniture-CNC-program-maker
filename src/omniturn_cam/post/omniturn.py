"""OmniTurn G4 post-processor.

Every formatting rule here traces back to the OmniTurn operator manual; see
docs/omniturn-post-rules.md for the list with page references. The output is
checked by omniturn_cam.lint before it is returned.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from ..geometry import Arc, Line, Point, Profile
from ..job import Job
from ..ops import AxialPass, BoxRough, Cutoff, Drill, Face, Finish, Operation, RadialPass, Rough, Tap, Thread, CallSub, RawCode
from ..tools import Station


@dataclass
class MachineConfig:
    # Default profile: Hardinge HLV-H toolroom lathe retrofitted with the OmniTurn
    # G4 control and CNC package (not a factory OmniTurn GT-75). Override per
    # machine with ~/.config/omniturn-cam/machine.toml.
    name: str = "Hardinge HLV-H OmniTurn G4"
    units: str = "inch"
    rapid_feed: float = 300.0          # G94 F at 'rapid' (300 IPM is the machine max)
    tool_call_z: float = 1.0           # safe Z for tool calls and between tools
    z_clear: float = 0.05              # approach clearance in front of features
    x_clear: float = 0.05              # radial clearance over stock (added on diameter x2)
    max_rpm: float = 3000.0            # HLV-H stock spindle: 125-3000 rpm. VERIFY after retrofit
    max_bar: float | None = 1.0625     # 5C round collet capacity (inch); None = no check
    decimals: int = 4                  # .0001 on diameter in G72 (manual p2.6)
    feed_decimals: int = 5
    arc_format: str = "r"              # "r" or "ik"
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
    def for_units(cls, units: str, overrides: dict | None = None) -> "MachineConfig":
        if units == "metric":
            m = cls(units="metric", rapid_feed=7620.0, tool_call_z=25.0, z_clear=1.0,
                    x_clear=1.0, decimals=3, feed_decimals=3, max_bar=27.0)
        else:
            m = cls()
        for k, v in (overrides or {}).items():
            if k == "units" or k not in cls.__dataclass_fields__:
                continue
            setattr(m, k, v)
        return m

    @classmethod
    def load(cls, path, units: str) -> "MachineConfig":
        """Read a machine.toml: top-level keys apply to both units, [inch]/[metric] tables
        override for one unit system."""
        import tomllib
        from pathlib import Path
        data = tomllib.loads(Path(path).read_text())
        common = {k: v for k, v in data.items() if not isinstance(v, dict)}
        return cls.for_units(units, common | data.get(units, {}))


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


class OmniTurnPost:
    def __init__(self, job: Job, machine: MachineConfig | None = None):
        self.job = job
        self.m = machine or MachineConfig.for_units(job.units)
        self.lines: list[str] = []
        self.x: float | None = None
        self.z: float | None = None
        self.motion: str | None = None      # "rapid" | "feed" | None (unknown)
        self.feed_mode = "g94"
        self.feed: float | None = None
        self.tool: int | None = None
        self.rpm: float | None = None
        self.spindle: str | None = None
        self.coolant = False
        self.subs: dict[str, int] = {}       # profile name -> subroutine number
        self.warnings: list[str] = []
        self.external_text, self.external_nums = job.external_subs()

    # ------------------------------------------------------------------ format
    def n(self, v: float) -> str:
        return fmt(v, self.m.decimals)

    def f(self, v: float) -> str:
        return fmt(v, self.m.feed_decimals)

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
                self.warn(f"T{op.t} {op.label}: {op.rpm:g} rpm is over the {self.m.max_rpm:g} rpm "
                          f"limit set for the {self.m.name}")
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

    # ------------------------------------------------------------------ operations
    @property
    def stock_x(self) -> float:
        return self.job.stock.diameter + 2 * self.m.x_clear

    def op_face(self, op: Face) -> None:
        st = self.station(op)
        xs = op.x_start if op.x_start is not None else self.stock_x
        nr = st.tool.nose_radius
        xe = op.x_end if op.x_end is not None else -max(2 * nr, 0.005 if self.job.units == "inch" else 0.1)
        self.call_tool(op, xs, self.job.stock.diameter)
        passes = [op.z]
        if op.stock > 0:
            dpp = op.depth_per_pass or op.stock
            k = max(1, math.ceil(op.stock / dpp - 1e-9))
            passes = [op.z + op.stock - (i + 1) * op.stock / k for i in range(k)]
        self.rapid(x=xs)
        self.rapid(z=passes[0] + self.m.z_clear)
        self.css_on(op)
        for zp in passes:
            self.rapid(x=xs)
            self.rapid(z=zp)
            self.cut(x=xe, feed=op.feed)
            self.rapid(z=zp + self.m.z_clear)
        self.rapid(x=xs)
        self.css_off(op)

    def _start_point(self, op, prof: Profile) -> tuple[float, float]:
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
    def _check_quadrant(sx, sz, prof: Profile) -> None:
        """G75/G78: contour start A and end B must be in the same quadrant of S (p2.42)."""
        def q(p: Point):
            return (math.copysign(1, p.x - sx) if abs(p.x - sx) > 1e-9 else 0,
                    math.copysign(1, p.z - sz) if abs(p.z - sz) > 1e-9 else 0)
        qa, qb = q(prof.start), q(prof.end)
        for a, b in zip(qa, qb):
            if a and b and a != b:
                raise PostError(
                    f"profile {prof.name!r}: start x{prof.start.x} z{prof.start.z} and end "
                    f"x{prof.end.x} z{prof.end.z} are not in the same quadrant relative to the "
                    f"cycle start x{sx} z{sz} - the control will reject this (manual p2.42)"
                )

    def op_rough(self, op: Rough) -> None:
        prof = self.job.profiles[op.profile]
        sx, sz = self._start_point(op, prof)
        self._check_quadrant(sx, sz, prof)
        self.call_tool(op, sx, self.job.stock.diameter)
        self.rapid(x=sx)
        self.rapid(z=sz)
        self.css_on(op)
        self.set_feed(op.feed)
        sub = self.subs.setdefault(op.profile, self.next_sub())
        if op.cycle == "g75":
            self.emit(f"g75i{self.n(op.depth)}u{self.n(op.u)}f{self.f(op.feed)}p{sub}")
        else:
            self.emit(f"g78u{self.n(op.u)}f{self.f(op.feed)}p{sub}")
        # The cycle returns the tool to its start point.
        self.x, self.z, self.motion = sx, sz, None
        self.css_off(op)

    def op_finish(self, op: Finish) -> None:
        prof = self.job.profiles[op.profile]
        st = self.station(op)
        sx, sz = self._start_point(op, prof)
        comp = op.comp
        toward_chuck = prof.end.z < prof.start.z
        if comp == "auto":
            if st.tool.nose_radius <= 0:
                comp = "none"
            elif prof.side == "od":
                comp = "g41" if toward_chuck else "g42"
            else:
                comp = "g42" if toward_chuck else "g41"
        if comp != "none" and st.d is None:
            raise PostError(f"t{op.t} finish with {comp}: give this station a d number "
                            "(secondary offset holding the nose radius)")
        self.call_tool(op, sx, self.job.stock.diameter)
        self.rapid(x=sx)
        self.rapid(z=sz)
        self.rapid(x=prof.start.x)
        self.css_on(op)
        if comp != "none":
            self.emit(comp)
        dword = f"d{st.d}" if comp != "none" else ""
        if abs(prof.start.z - sz) < 1e-9 and abs(prof.start.x - self.x) < 1e-9:
            raise PostError("finish start point equals approach point; nothing to turn comp on with")
        self.cut(prof.start.x, prof.start.z, op.feed, dword)
        self.contour(prof, op.feed)
        # Lead off the part by more than twice the nose radius before G40 (p2.32).
        lead = 2 * max(3 * st.tool.nose_radius, 0.01 if self.job.units == "inch" else 0.25)
        self.cut(x=prof.end.x + (lead if prof.side == "od" else -lead), feed=op.feed)
        self.rapid(z=sz)
        if comp != "none":
            self.emit("g40")
        self.css_off(op)

    def op_drill(self, op: Drill) -> None:
        sz = op.start_z if op.start_z is not None else self.m.z_clear
        self.call_tool(op, 0.0, self.station(op).tool.diameter or self.job.stock.diameter)
        self.rapid(x=0.0)
        self.rapid(z=sz)
        if self.feed_mode != "g95":
            self.emit("g95")
            self.feed_mode = "g95"
        if op.peck:
            r = op.retract_z if op.retract_z is not None else sz
            l = op.peck_rapid or self.m.rapid_feed
            self.emit(f"g83z{self.n(op.z)}k{self.n(op.peck)}f{self.f(op.feed)}"
                      f"r{self.n(r)}c{self.n(op.peck_clearance)}l{fmt(l, 1)}")
        else:
            self.emit(f"g81z{self.n(op.z)}f{self.f(op.feed)}")
        self.feed = op.feed
        self.x, self.z, self.motion = 0.0, sz, None

    def op_axial(self, op: AxialPass) -> None:
        self.call_tool(op, op.x, max(op.x, 1e-3) if op.x else self.job.stock.diameter)
        self.rapid(x=op.x)
        self.rapid(z=op.z_start)
        self.cut(z=op.z_end, feed=op.feed, extra="p" if op.sync else "")
        self.dwell(op.dwell)
        if op.reverse_spindle_out:
            self.emit("m04" if op.spindle == "m03" else "m03")
        if op.retract == "feed":
            self.cut(z=op.z_start, feed=op.retract_feed or op.feed)
        else:
            self.rapid(z=op.z_start)
        if op.reverse_spindle_out:
            self.emit(op.spindle)
        if op.stop_after != "none":
            self.rapid(z=self.m.tool_call_z)
            if op.stop_spindle_for_stop:
                self.emit("m05")
                self.spindle = None   # next cut restarts it
            self.emit(op.stop_after)

    def op_tap(self, op: Tap) -> None:
        """Floating-holder tapping as shown in the manual (p2.58)."""
        self.call_tool(op, op.x, self.job.stock.diameter)
        self.rapid(x=op.x)
        self.rapid(z=op.z_start)
        feed = op.pitch * op.feed_ratio
        self.cut(z=op.z_end, feed=feed)
        self.emit("m04" if op.spindle == "m03" else "m03")
        self.dwell(op.reverse_dwell)
        self.cut(z=op.z_start, feed=feed)
        self.emit(op.spindle)

    def thread_plan(self, op: Thread) -> dict:
        """Numbers the G33 cycle will use, for the post, the GUI and warnings."""
        depth = abs(op.major - op.minor)                     # diameter terms
        if op.side == "od":
            final_x, start_x = op.minor, op.major - op.first_depth
        else:
            final_x, start_x = op.major, op.minor + op.first_depth
        if op.start_x is not None:
            start_x = op.start_x
        passes = (depth / op.first_depth) ** 2 if op.first_depth > 0 else float("inf")
        return {"depth": depth, "final_x": final_x, "start_x": start_x, "passes": passes,
                "k": op.lead * op.starts, "pitch": op.lead}

    def op_thread(self, op: Thread) -> None:
        inch = self.job.units == "inch"
        if not op.rpm or op.sfm:
            raise PostError(f"T{op.t} thread: threading needs a fixed RPM (G97), not surface speed")
        if op.first_depth <= 0 or op.lead <= 0 or op.starts < 1:
            raise PostError(f"T{op.t} thread: lead, starts and first-pass depth must be positive")
        if op.infeed_angle is not None and not 0 <= op.infeed_angle <= 30:
            raise PostError(f"T{op.t} thread: infeed angle must be 0-30 degrees (manual p2.24)")
        plan = self.thread_plan(op)
        k = plan["k"]
        feed_limit = self.m.rapid_feed
        if op.rpm * k > feed_limit + 1e-9:
            raise PostError(f"T{op.t} thread: {op.rpm:g} rpm x {k:g} lead = {op.rpm * k:g} "
                            f"{'ipm' if inch else 'mm/min'}, over the {feed_limit:g} limit. "
                            f"Slow the spindle to {int(feed_limit / k)} rpm or less (p2.23)")
        min_runup = 0.1 if inch else 2.5
        if op.runup < min_runup:
            self.warn(f"T{op.t} thread: run-up {op.runup:g} is short; the manual says start at least "
                      f"{min_runup:g} away so the slide reaches speed, more for coarse leads or high rpm")
        if op.taper_a is not None and op.start_x is None:
            raise PostError(f"T{op.t} thread: give the start X for a tapered thread "
                            "(the manual corrects the start for the taper over the run-up)")
        if op.side == "od" and plan["start_x"] <= plan["final_x"] or \
                op.side == "id" and plan["start_x"] >= plan["final_x"]:
            raise PostError(f"T{op.t} thread: start X {plan['start_x']:g} is already past the finished "
                            f"X {plan['final_x']:g}")

        sx = plan["start_x"]
        clear_x = self.stock_x if op.side == "od" else min(sx, op.minor) - 2 * op.first_depth
        z0 = op.z_start + op.runup
        self.call_tool(op, clear_x, self.job.stock.diameter)
        self.comment(f"THREAD ABOUT {max(1, round(plan['passes']))} PASSES")
        if op.lock_overrides:
            self.emit("m84")   # overrides locked at 100% so rpm can't drift between passes
        words = f"g33x{self.n(plan['final_x'])}z{self.n(op.z_end)}i{self.n(op.first_depth)}k{self.n(k)}"
        if op.taper_a is not None:
            words += f"a{self.n(op.taper_a)}"
        tail = ""
        if op.pullout:
            tail += "p"
        if op.infeed_angle is not None:
            tail += "c" if abs(op.infeed_angle - 29) < 1e-9 else f"c{fmt(op.infeed_angle, 2)}"
        for s_i in range(op.starts):
            zs = z0 + s_i * plan["pitch"]
            self.rapid(x=clear_x)
            self.rapid(z=zs)
            self.rapid(x=sx)
            self.emit(words + tail)
            # Spring passes repeat the same cycle with O from the same start point,
            # same rpm, same P/C (p2.26).
            for _ in range(op.spring_passes):
                self.emit(words + tail + "o")
            self.x, self.z, self.motion = sx, zs, None   # cycle returns to its start point
        self.rapid(x=clear_x)
        if op.lock_overrides:
            self.emit("m85")

    def _leave_known_state(self) -> None:
        """Before handing control to code we didn't write, park the slide at safe Z."""
        if self.tool is not None:
            self.rapid(z=self.m.tool_call_z)

    def _forget_state(self) -> None:
        # the subroutine / raw code may move, change tools or spindle: forget what we know
        self.x = self.z = self.motion = self.feed = None
        self.spindle = self.rpm = self.tool = None

    def op_call_sub(self, op: CallSub) -> None:
        self._leave_known_state()
        if op.comment:
            self.comment(op.comment)
        self.emit(f"m98p{op.sub}")
        self._forget_state()

    def op_raw(self, op: RawCode) -> None:
        self._leave_known_state()
        if op.comment:
            self.comment(op.comment)
        for line in op.code.splitlines():
            if line.strip():
                self.emit(line.strip())
        self._forget_state()

    def op_box_rough(self, op: BoxRough) -> None:
        """G74: the control removes the rectangle between the start point and X/Z (p2.39)."""
        sx = op.start_x if op.start_x is not None else self.stock_x
        sz = op.start_z if op.start_z is not None else self.m.z_clear
        self.call_tool(op, sx, self.job.stock.diameter)
        self.rapid(x=sx)
        self.rapid(z=sz)
        self.css_on(op)
        self.set_feed(op.feed)
        # U is ignored by G74 but the control still requires the word.
        self.emit(f"g74x{self.n(op.x)}z{self.n(op.z)}i{self.n(op.depth)}u{self.n(op.u)}f{self.f(op.feed)}")
        self.x, self.z, self.motion = sx, sz, None
        self.css_off(op)

    def op_radial(self, op: RadialPass) -> None:
        xs = op.x_start if op.x_start is not None else self.stock_x
        self.call_tool(op, xs, self.job.stock.diameter)
        self.rapid(x=xs)
        self.rapid(z=op.z)
        self.css_on(op)
        self.cut(x=op.x_end, feed=op.feed)
        self.dwell(op.dwell)
        if op.retract == "feed":
            self.cut(x=xs, feed=op.retract_feed or op.feed)
        else:
            self.rapid(x=xs)
        self.css_off(op)
        if isinstance(op, Cutoff) and op.stop_spindle_after:
            self.emit("m05")
            self.spindle = None

    HANDLERS = {CallSub: "op_call_sub", RawCode: "op_raw", Thread: "op_thread", Face: "op_face", BoxRough: "op_box_rough", Tap: "op_tap", Rough: "op_rough", Finish: "op_finish", Drill: "op_drill",
                AxialPass: "op_axial", Cutoff: "op_radial", RadialPass: "op_radial"}

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
            self.warn(f"bar diameter {j.stock.diameter:g} is over the {self.m.max_bar:g} collet "
                      f"capacity set for the {self.m.name}")
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
            handler = next(h for cls, h in self.HANDLERS.items() if type(op) is cls)
            getattr(self, handler)(op)
        self.footer()
        return "\n".join(self.lines) + "\n"


def post(job: Job, machine: MachineConfig | None = None, check: bool = True,
         warnings: list | None = None) -> str:
    """Generate the program. Pass a list as `warnings` to collect non-fatal notes."""
    try:
        job.validate()
    except (KeyError, ValueError) as e:
        raise PostError(str(e).strip('"')) from None
    if machine is not None and machine.units != job.units:
        machine = MachineConfig.for_units(job.units, {k: getattr(machine, k) for k in ("name", "max_rpm")})
    p = OmniTurnPost(job, machine)
    code = p.run()
    if warnings is not None:
        warnings.extend(p.warnings)
    if check:
        from ..lint import lint
        problems = [p for p in lint(code) if p.severity == "error"]
        if problems:
            raise PostError("generated code failed the OmniTurn rules check:\n" +
                            "\n".join(str(p) for p in problems))
    return code
