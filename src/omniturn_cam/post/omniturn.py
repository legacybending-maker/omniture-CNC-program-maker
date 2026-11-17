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
from ..operations.base import ENDS_AT
from ..tools import Station
from .external import analyse, subroutine_bodies
from .state import POSITION, UNKNOWN, MachineState

# Forgotten after every handoff to raw code or a subroutine, whatever the code
# says: see OmniTurnPost.resume_after for why each is on the list.
ALWAYS_FORGOTTEN = frozenset({"tool", "spindle", "rpm", "coolant", "feed", "motion"})


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
    """Writes one job as an OmniTurn program.

    ``self.state`` (a MachineState) is what the post knows about the control
    while it writes; see post/state.py. Two rules hold everywhere below:

    * A mode is written when the state doesn't already know it is set, and
      skipped only when the state *knows* it is (that's what keeps programs
      free of redundant lines without trusting stale knowledge).
    * The slide is never moved from a position the post doesn't know. The one
      move allowed without known X and Z is the approach after a tool call, and
      only when the slide is known to be clear at the tool-change Z.
    """

    def __init__(self, job: Job, machine: MachineConfig | None = None):
        self.job = job
        self.m = machine or MachineConfig.for_units(job.units)
        self.lines: list[str] = []
        self.state = MachineState()
        self.subs: dict[str, int] = {}  # profile name -> subroutine number
        self.warnings: list[str] = []
        self.external_text, self.external_nums = job.external_subs()
        # The include file's subroutines by number, so a raw M98 or a Call
        # subroutine operation can be read before the post decides what it changed.
        self.external_bodies = subroutine_bodies(self.external_text)
        # The operation being written (1-based) and the operation itself, for
        # error messages that say where the problem is (CONTRIBUTING: Usability).
        self.op_number = 0
        self.op: Operation | None = None
        # When the slide position is unknown: which operation lost it, and why.
        self.position_lost = ""

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

    @property
    def here(self) -> str:
        """The current operation in operator terms, e.g. 'operation 3 (finish t1)'."""
        op = self.op
        if op is None:
            return "the end of the program"
        tool = f" t{op.t}" if op.uses_tool else ""
        return f"operation {self.op_number} ({op.label}{tool})"

    def _axes(self, x, z, both=False) -> str:
        # `x`/`z` are where to go (None = this axis doesn't move); the state's
        # x/z are where the slide is (UNKNOWN = don't know). Two different
        # "nothing" values for two different meanings; see state.Unknown.
        s = self.state
        w = ""
        if x is not None and (both or s.x is UNKNOWN or abs(x - s.x) > 1e-9):
            w += "x" + self.n(x)
        if z is not None and (both or s.z is UNKNOWN or abs(z - s.z) > 1e-9):
            w += "z" + self.n(z)
        return w

    def _moved(self, x, z):
        s = self.state
        if x is not None:
            s.x = x
        if z is not None:
            s.z = z
        # "Clear" follows from the position whenever the post knows it: the
        # slide is clear exactly when it is at the tool-change Z.
        s.clear = s.z is not UNKNOWN and abs(s.z - self.m.tool_call_z) < 1e-9

    # ------------------------------------------------------------------ guards
    def _refuse_unknown_position(self, what: str) -> None:
        """Raise PostError: the post was asked to move from a position it doesn't know."""
        after = f" after {self.position_lost}" if self.position_lost else ""
        raise PostError(
            f"{self.here}: can't {what}: the post doesn't know where the slide is{after}. "
            "Moving from an unknown position could run a tool into the work or the collet. "
            "If that code always finishes with the slide back at the tool-change Z, clear of the "
            "work, set its 'Slide position afterwards' to 'tool_change_z' and prove it out in "
            "single block; otherwise change it so it doesn't move the slide"
        )

    def _check_modes(self) -> None:
        """Backstop before every move: the modes that give X and Z their meaning must
        be known. ``establish_modes`` (called by every tool call) sets them, so this
        only fires if an operation moves without calling a tool first, which is a
        bug in that operation. Refusing beats guessing what the numbers mean."""
        s = self.state
        units = "g71" if self.job.units == "metric" else "g70"
        if s.units != units or s.coords != "g90" or s.diameter != "g72" or s.comp is UNKNOWN:
            raise PostError(
                f"{self.here}: internal error, a move was written before the units, G90, "
                f"G72 and comp were established ({', '.join(s.unknown_fields()) or 'wrong mode'}). "
                "Please report this; the program was not written"
            )

    def establish_modes(self) -> None:
        """Write whichever of G40, G70/G71, G90 and G72 the state doesn't know are set.

        These four decide what every X and Z number means, and the header sets
        them. After raw code that may have changed one, the next tool call
        writes it again here, before the tool call line, because the line after
        a tool call must be the X and Z move (p2.9). Same lines and order as the
        header. When all four are known, this writes nothing.
        """
        s = self.state
        if s.comp != "g40":
            # Nothing the post writes runs comp across operations (finish turns
            # it off before it ends), so anything else here came from external
            # code. Cancel it: the next move is the approach in free air (p2.32).
            self.emit("g40")
            s.comp = "g40"
        units = "g71" if self.job.units == "metric" else "g70"
        if s.units != units:
            self.emit(units)
            s.units = units
        words = ("g90" if s.coords != "g90" else "") + ("g72" if s.diameter != "g72" else "")
        if words:
            self.emit(words)
            s.coords, s.diameter = "g90", "g72"

    # ------------------------------------------------------------------ motion
    def rapid(self, x=None, z=None, both=False) -> None:
        s = self.state
        words = self._axes(x, z, both)
        if not words:
            return
        # The approach after a tool call is the only move that may start
        # without a known position, and only from the tool-change Z.
        tool_call_approach = both and x is not None and z is not None and s.clear
        if not s.position_known and not tool_call_approach:
            self._refuse_unknown_position(f"rapid {words}")
        self._check_modes()
        prefix = "" if s.motion == "rapid" else "g00"
        self.emit(prefix + words)
        s.motion = "rapid"
        self._moved(x, z)

    def _feed_prefix(self, feed: float) -> str:
        s = self.state
        if s.feed_mode != "g95" or s.feed is UNKNOWN or abs(feed - s.feed) > 1e-12:
            s.feed_mode, s.feed = "g95", feed
            return "g95f" + self.f(feed)
        return "" if s.motion == "feed" else "g01"

    def set_feed(self, feed: float) -> None:
        """Establish IPR mode and feed on a line of its own (before cycles/arcs)."""
        s = self.state
        if s.feed_mode != "g95" or s.feed is UNKNOWN or abs(feed - s.feed) > 1e-12:
            self.emit("g95f" + self.f(feed))
            s.feed_mode, s.feed = "g95", feed

    def set_feed_mode(self, mode: str) -> None:
        """Write G94 or G95 alone unless the state knows it's already in effect.

        Used before a cycle that carries its own F word (G81/G83): the F means
        inches per rev only in G95, and per minute in G94 (p2.11), so the mode
        must be certain, not remembered from before some raw code.
        """
        if self.state.feed_mode != mode:
            self.emit(mode)
            self.state.feed_mode = mode

    def cut(self, x=None, z=None, feed: float = 0.0, extra: str = "") -> None:
        words = self._axes(x, z)
        if not words:
            return
        if not self.state.position_known:
            self._refuse_unknown_position(f"feed {words}")
        self._check_modes()
        self.emit(self._feed_prefix(feed) + words + extra)
        self.state.motion = "feed"
        self._moved(x, z)

    def cycle_ended_at(self, x: float, z: float) -> None:
        """Record where a canned cycle (G74/G75/G78/G81/G83/G33) left the slide.

        The manual says each of these returns the tool to its start point; the
        G00/G01 modal afterwards isn't documented, so it becomes UNKNOWN and the
        next move states it.
        """
        self._moved(x, z)
        self.state.motion = UNKNOWN

    def dwell(self, seconds: float) -> None:
        if seconds <= 0:
            return
        self.emit("g04f" + fmt(max(seconds, 0.1), 2))
        # G04 uses the F word; force the next cutting move to restate its feed.
        self.state.feed = UNKNOWN

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
                self._moved(seg.end.x, seg.end.z)
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
        s = self.state
        rpm = round(self.est_rpm(op, dia))
        # The S word below is an rpm only in G97; in G96 it would be a surface
        # speed (p2.52). Every operation starts its spindle in G97 (CSS ones
        # switch to G96 later, in css_on), so make G97 certain first.
        if s.spindle_mode != "g97":
            self.emit("g97")
            s.spindle_mode = "g97"
        if s.spindle != op.spindle or s.rpm != rpm:
            if s.spindle != op.spindle:
                self.emit(f"{op.spindle}s{rpm}")
            else:
                self.emit(f"s{rpm}")
            s.spindle, s.rpm = op.spindle, rpm
        # Coolant has three states, so both directions are written whenever the
        # state doesn't already know it's right: after raw code the post can't
        # tell whether coolant is on or off, and assuming either is a guess.
        want = "m08" if op.coolant else "m09"
        if s.coolant != want:
            self.emit(want)
            s.coolant = want

    def stop_spindle(self) -> None:
        """M05, recorded: the next spindle_on restarts it with M03/M04."""
        self.emit("m05")
        self.state.spindle = "m05"

    def set_comp(self, code: str) -> None:
        """Write G40/G41/G42 and record it, so the state always knows whether comp is on."""
        self.emit(code)
        self.state.comp = code

    def css_on(self, op: Operation) -> None:
        if op.sfm and not op.rpm:
            self.emit(f"g96s{fmt(op.sfm, 1)}")
            if op.min_rpm:
                self.emit(f"g76s{round(op.min_rpm)}")
            self.emit(f"g77s{round(op.max_rpm or self.m.max_rpm)}")
            self.state.spindle_mode = "g96"

    def css_off(self, op: Operation) -> None:
        if op.sfm and not op.rpm:
            if self.state.rpm is UNKNOWN:
                # spindle_on in the same operation always sets it; refuse rather than write "s".
                raise PostError(f"{self.here}: internal error, no rpm to return to after CSS")
            self.emit("g97")
            self.emit(f"s{self.state.rpm}")
            self.state.spindle_mode = "g97"

    def call_tool(self, op: Operation, approach_x: float, spindle_dia: float) -> None:
        st = self.station(op)
        s = self.state
        self.establish_modes()
        if s.tool is UNKNOWN or s.tool != op.t:
            if not s.clear:
                if not s.position_known:
                    self._refuse_unknown_position(f"call t{op.t}")
                # Retract in Z alone to the tool-change Z, from a known position.
                self.rapid(z=self.m.tool_call_z)
            if s.motion != "rapid":
                self.emit("g00")
                s.motion = "rapid"
            name = sanitize_comment(st.tool.comment, self.m.max_comment)
            self.emit(f"t{op.t}({name})" if name else f"t{op.t}")
            s.tool = op.t
            # The new tool's offsets change what X and Z mean, and the line
            # after a tool call must carry BOTH X and Z (manual p2.9). `clear`
            # stays True: the slide hasn't moved, so this approach is safe.
            s.x = s.z = UNKNOWN
            self.rapid(approach_x, self.m.tool_call_z, both=True)
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

    # ------------------------------------------------------------------ handing control to other code
    # Raw program lines and M98 calls run code the post didn't write. The
    # contract (docs/omniturn-post-rules.md, "Handing control to code the post
    # didn't write"): park only from a known position; afterwards forget
    # everything the code could have changed; never guess where the slide is;
    # let the next operation re-establish its modes; refuse when it can't.

    def park_before_handoff(self) -> None:
        """Before external code: rapid to the tool-change Z, in Z alone, if the post
        knows where the slide is and it isn't there already. From an unknown
        position it doesn't move at all."""
        s = self.state
        if s.position_known and not s.clear:
            self.rapid(z=self.m.tool_call_z)

    def resume_after(self, code: list[str], ends_at: str, what: str) -> None:
        """After external code: forget what it could have changed.

        ``code`` is the lines that were handed over (for a subroutine call, the
        ``m98pN`` line); ``external.analyse`` reads them, and any subroutine of
        the include file they call, and says what they could change. On top
        of that the tool, spindle, rpm, coolant, feed and G00/G01 are always
        forgotten: they're cheap to state again, and the spindle and
        coolant are physical outputs a loader routine or the operator (at an
        M00) commonly changes. Forgetting the tool also means the next operation
        calls its tool again, re-applying its offsets.

        ``ends_at`` is the operator's declaration on the operation:
        "check" (not declared: nothing assumed) or "tool_change_z" (the code
        always finishes with the slide back at the tool-change Z, clear of the
        work). The declaration is the only way the post accepts a position it
        can't see; it is warned about every time it's relied on.

        Raises PostError for an ``ends_at`` it doesn't recognise (a typo in a
        hand-edited job file must not count as either answer).
        """
        if ends_at not in ENDS_AT:
            raise PostError(
                f"{what}: 'Slide position afterwards' is {ends_at!r}; it must be one of {', '.join(ENDS_AT)}"
            )
        effects = analyse(code, self.external_bodies)
        s = self.state
        s.forget(ALWAYS_FORGOTTEN | effects.forget)
        if ends_at == "tool_change_z" and not s.clear:
            s.forget(POSITION)
            s.clear = True
            self.position_lost = ""
            self.warn(
                f"{what}: relying on your declaration that it always finishes with the slide at the "
                "tool-change Z, clear of the work. Prove it out in single block before running the job"
            )
        elif effects.moves_slide:
            self.position_lost = f"{what}: {effects.reason}"

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
        units = "g71" if j.units == "metric" else "g70"
        self.emit("g40")
        self.emit(units)
        self.emit(f"g90g72g94g97f{fmt(self.m.rapid_feed, 1)}")
        s = self.state
        s.comp, s.units, s.coords, s.diameter = "g40", units, "g90", "g72"
        s.feed_mode, s.spindle_mode, s.feed = "g94", "g97", self.m.rapid_feed
        # Everything else stays UNKNOWN: where the slide is, the spindle, the
        # coolant (the last program may have ended with M02, which leaves M
        # functions on; "Program end" in the post rules), the tool. The one thing the post takes on trust
        # is the operator's job at cycle start: the slide is clear of the work at
        # the tool-change position, so the first tool call's X/Z approach is
        # safe. Every program has always relied on that; it's written down here.
        s.clear = True

    def footer(self) -> None:
        self.op_number, self.op = 0, None
        s = self.state
        if not s.clear:
            if s.position_known:
                self.rapid(z=self.m.tool_call_z)
            else:
                self.warn(
                    f"the program ends without returning to the tool-change Z: the slide position is "
                    f"unknown after {self.position_lost}. Bring the slide clear before the next cycle start"
                )
        if s.coolant != "m09":
            self.emit("m09")
            s.coolant = "m09"
        self.emit("m05")
        self.emit("m30")
        # The subroutines are separate programs after M30: the position here only
        # drives _axes, so each starts from "unknown" and writes both X and Z.
        for name, num in self.subs.items():
            prof = self.job.profiles[name]
            self.emit(f"}}{num}")
            s.x = s.z = UNKNOWN
            self.emit(self._axes(prof.start.x, prof.start.z, both=True))
            self._moved(prof.start.x, prof.start.z)
            self.contour(prof, feed=None)
            self.emit("m99")
        if self.external_text.strip():
            for line in self.external_text.splitlines():
                if line.strip():
                    self.emit(line.strip())

    def run(self) -> str:
        self.header()
        # enumerate(..., 1) counts from 1, the way the process list numbers operations.
        for i, op in enumerate(self.job.operations, 1):
            self.op_number, self.op = i, op
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
