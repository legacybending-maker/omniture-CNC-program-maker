"""A job: units, stock, tool setup, named profiles and an ordered operation list.

Jobs are stored as TOML so they can be hand-edited, diffed in git, and written
by the fill-in-the-blank front end. See examples/ for the format.
"""

from __future__ import annotations

import dataclasses
import difflib
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from .geometry import Arc, Line, Point, Profile
from .opcheck import operation_issues
from .operations import OPERATION_TYPES, Operation, kind_of
from .tools import Setup, Station, ToolLibrary
from .validation import Issue, is_finite, is_number, is_whole, raise_errors, show

if TYPE_CHECKING:
    from .post.omniturn import MachineConfig

NAME_RE = re.compile(r"[A-Za-z0-9_-]{1,8}")

# Job file format version, written as [job] format = N. Files without it are
# format 1. Bump it only when an old omniturn-cam would misread a new file
# (renamed/removed keys, changed meaning or units of a value); adding an
# optional key with a safe default doesn't need a bump. When you bump it, keep
# reading every older format and add a test with an old file in tests/data/jobs/.
JOB_FORMAT = 1


@dataclass
class Stock:
    diameter: float
    length: float | None = None
    bore: float | None = None  # existing hole diameter, if any
    material: str = ""


@dataclass
class Job:
    name: str  # also the program file name: max 8 characters
    units: Literal["inch", "metric"]
    stock: Stock
    setup: Setup
    profiles: dict[str, Profile] = field(default_factory=dict)
    operations: list[Operation] = field(default_factory=list)
    description: str = ""
    include_subs: str | None = None  # file of subroutines appended after M30 (e.g. ZipLoader subs)

    def external_subs(self) -> tuple[str, list[int]]:
        """Text of the include file and the subroutine numbers it defines."""
        if not self.include_subs:
            return "", []
        text = Path(self.include_subs).read_text(errors="replace")
        nums = [int(n) for n in re.findall(r"^\s*\}(\d+)\s*$", text, re.M)]
        return text, nums

    def issues(self, machine: MachineConfig | None = None) -> list[Issue]:
        """Every problem with this job: errors and warnings, nothing raised.

        Without ``machine``: validation layers 1 and 2 (types, ranges and
        geometry that hold on any machine). With it, layer 3 too (feeds,
        speeds and clearances against that machine profile). See
        validation.py. The GUI can call this as often as it likes; it
        changes nothing.
        """
        out: list[Issue] = []
        if not isinstance(self.name, str) or not NAME_RE.fullmatch(self.name):
            out.append(
                Issue(
                    "job",
                    "must be 1-8 letters, digits, _ or -: it becomes the OmniTurn program file name (manual p2.8)",
                    setting="Job name [name]",
                    value=show(self.name),
                )
            )
        if self.units not in ("inch", "metric"):
            # Checked before anything else that depends on it. Everything
            # that tests `units == "metric"` would treat a typo like "Metric"
            # as inch, so a metric job would be posted as inches.
            out.append(Issue("job", "must be inch or metric", setting="Units [units]", value=show(self.units)))
            return out
        out += self._stock_issues()
        if self.include_subs and not Path(self.include_subs).is_file():
            out.append(Issue("job", f"subroutine file {self.include_subs} not found", setting="Include file"))
        out += self._station_issues()
        for prof in self.profiles.values():
            out += prof.issues()
        if machine is not None and machine.units != self.units:
            # The post used to quietly rebuild a default profile for the other
            # units here, dropping every setting in the file but two.
            out.append(
                Issue(
                    "job",
                    f"is {self.units} but the machine profile was loaded for {machine.units}",
                    "Load the machine profile with the job's units (MachineConfig.load(path, job.units))",
                    setting="Units [units]",
                )
            )
            return out
        out += operation_issues(self, machine)
        return out

    def _stock_issues(self) -> list[Issue]:
        out = []
        s = self.stock

        def bad(setting, value, problem):
            out.append(Issue("job", problem, setting=f"Bar stock {setting}", value=show(value)))

        if not is_finite(s.diameter) or s.diameter <= 0:
            bad("diameter", s.diameter, "must be a number greater than zero")
        if s.length is not None and (not is_finite(s.length) or s.length <= 0):
            bad("length", s.length, "must be a number greater than zero, or blank")
        if s.bore is not None:
            if not is_finite(s.bore) or s.bore < 0:
                bad("bore", s.bore, "must be a number, 0 or more, or blank")
            elif is_finite(s.diameter) and s.bore >= s.diameter:
                bad("bore", s.bore, f"must be smaller than the bar diameter {show(s.diameter)}")
        if not isinstance(s.material, str):
            bad("material", s.material, "must be text")
        return out

    def _station_issues(self) -> list[Issue]:
        """Stations are edited in place on F3, so they are checked here and
        not only when the job file is read."""
        out = []
        for key, st in self.setup.stations.items():
            where = f"station t{key}"
            if not is_whole(st.t) or st.t != key or not 1 <= st.t <= 32:
                out.append(Issue(where, "must be a whole number 1-32 (manual p2.9)", setting="T", value=show(st.t)))
            if st.d is not None and (not is_whole(st.d) or not 1 <= st.d <= 32):
                # Secondary offsets are numbered 1-32 (manual p5.16); D0 turns
                # the correction *off* (p2.62), so it can't hold a nose radius.
                out.append(
                    Issue(
                        where,
                        "must be a whole number 1-32 (secondary offsets, manual p5.16), or blank for none",
                        setting="D number",
                        value=show(st.d),
                    )
                )
        return out

    def validate(self, machine: MachineConfig | None = None) -> None:
        """Raise ValidationError (a ValueError) listing every error found;
        warnings don't raise. See issues()."""
        raise_errors(self.issues(machine))


# --------------------------------------------------------------------- reading
def _num(value, where: str) -> float:
    """A number from the job file, as float. Refuses text and true/false
    instead of converting them: ``float("1.0")`` would quietly accept a
    quoted number, and ``float(True)`` is 1.0. NaN gets through here; the
    shape check (Profile.issues) reports it with the segment it's in."""
    if not is_number(value):
        raise ValueError(f"{where} is {show(value)}: must be a number")
    return float(value)


def _point(v, where: str) -> Point:
    if isinstance(v, dict):
        if "x" not in v or "z" not in v:
            raise ValueError(f"{where} needs both x and z")
        return Point(_num(v["x"], f"{where} x"), _num(v["z"], f"{where} z"))
    try:
        x, z = v  # tuple unpacking: fails unless v has exactly two items
    except (TypeError, ValueError):
        raise ValueError(f"{where} must be {{ x = ..., z = ... }}") from None
    return Point(_num(x, f"{where} x"), _num(z, f"{where} z"))


def profile_from_dict(name: str, d: dict) -> Profile:
    where = f"shape {name!r}"
    if "start" not in d:
        raise ValueError(f"{where} has no start point")
    prof = Profile(start=_point(d["start"], f"{where} start"), name=name, side=d.get("side", "od"))
    for i, seg in enumerate(d.get("seg", []), 1):
        here = f"{where} segment {i}"
        last = prof.end
        x = _num(seg["x"], f"{here} x") if "x" in seg else last.x
        z = _num(seg["z"], f"{here} z") if "z" in seg else last.z
        if "arc" in seg:
            if "r" not in seg:
                raise ValueError(f"{here} is an arc with no radius r")
            prof.segments.append(Arc(Point(x, z), _num(seg["r"], f"{here} r"), seg["arc"]))
        else:
            # C and R are checked (type, size, fit) by Profile.issues().
            try:
                prof.segments.append(Line(Point(x, z), seg.get("c"), seg.get("r")))
            except ValueError as e:
                raise ValueError(f"{here}: {e}") from None
    return prof


def _op_from_dict(number: int, o: dict) -> Operation:
    """Build one operation from its [[op]] table, refusing keys the operation
    doesn't have and required keys that are missing.

    Without this, ``cls(**o)`` raises TypeError ("unexpected keyword
    argument 'feeed'"), which nothing catches and which doesn't say which
    operation it was. ``**o`` unpacks the dict into keyword arguments.
    """
    o = dict(o)
    kind = o.pop("type", None)
    if kind is None:
        raise ValueError(f"operation {number} in the job file has no type")
    try:
        cls = OPERATION_TYPES[kind]
    except (KeyError, TypeError):
        raise ValueError(f"unknown operation type {kind!r}; known: {', '.join(OPERATION_TYPES)}") from None
    names = {f.name for f in dataclasses.fields(cls)}
    where = f"operation {number} ({kind})"
    unknown = [k for k in o if k not in names]
    if unknown:
        # difflib.get_close_matches finds the nearest spellings, so a typo
        # gets "did you mean" instead of a list of every key.
        hints = [
            f"{k!r} (did you mean {m[0]!r}?)" if (m := difflib.get_close_matches(k, names, 1)) else repr(k)
            for k in unknown
        ]
        raise ValueError(f"{where} has settings it doesn't use: {', '.join(hints)}")
    missing = [
        f.name
        for f in dataclasses.fields(cls)
        if f.name not in o and f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING
    ]
    if missing:
        raise ValueError(f"{where} is missing {', '.join(missing)}")
    return cls(**o)


def job_from_dict(data: dict, library: ToolLibrary, base_dir: Path | None = None) -> Job:
    if not isinstance(data.get("job"), dict):
        raise ValueError("the job file has no [job] table")
    j = data["job"]
    fmt = j.get("format", 1)
    if not isinstance(fmt, int) or fmt < 1:
        raise ValueError(f"job file format {fmt!r} is not valid")
    if fmt > JOB_FORMAT:
        raise ValueError(
            f"this job file is format {fmt}, written by a newer omniturn-cam; this version "
            f"reads up to format {JOB_FORMAT}. Update omniturn-cam to open it."
        )
    setup = Setup()
    for i, st in enumerate(data.get("station", []), 1):
        t, d = st.get("t"), st.get("d")
        # int() used to be applied here, so t = 1.5 quietly became T1.
        if not is_whole(t):
            raise ValueError(f"station {i} in the job file: t is {show(t)}, must be a whole number 1-32")
        if d is not None and not is_whole(d):
            raise ValueError(f"station t{t} in the job file: d is {show(d)}, must be a whole number 1-32")
        if "tool" not in st:
            raise ValueError(f"station t{t} in the job file has no tool")
        setup.add(Station(t, library[st["tool"]], d))

    profiles = {name: profile_from_dict(name, d) for name, d in data.get("profile", {}).items()}

    ops = [_op_from_dict(i, o) for i, o in enumerate(data.get("op", []), 1)]

    if "stock" not in data or "diameter" not in data["stock"]:
        raise ValueError("the job file has no [stock] diameter")
    if "name" not in j:
        raise ValueError("the job file has no [job] name")
    s = data["stock"]
    inc = j.get("include_subs")
    if inc and base_dir is not None and not Path(inc).is_absolute():
        inc = str((base_dir / inc).resolve())
    return Job(
        name=j["name"],
        units=j.get("units", "inch"),
        description=j.get("description", ""),
        include_subs=inc,
        # The stock values are kept as written and checked by Job.issues():
        # float() here would quietly accept diameter = "1.0".
        stock=Stock(s["diameter"], s.get("length"), s.get("bore"), s.get("material", "")),
        setup=setup,
        profiles=profiles,
        operations=ops,
    )


def load_job(path: str | Path, library: ToolLibrary, validate: bool = True) -> Job:
    """Read a job file. With ``validate`` (the default) also run Job.validate().

    Raises ValueError (ValidationError for problems in the values) naming the
    file, and KeyError for a tool that isn't in the library.
    """
    try:
        data = tomllib.loads(Path(path).read_text())
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"{path} is not a valid job file: {e}") from None
    job = job_from_dict(data, library, Path(path).parent)
    if validate:
        job.validate()
    return job


# --------------------------------------------------------------------- writing
def _clean(d: dict) -> dict:
    """TOML has no null: drop None values."""
    return {k: v for k, v in d.items() if v is not None}


def profile_to_dict(prof: Profile) -> dict:
    segs = []
    for seg in prof.segments:
        s = {"x": seg.end.x, "z": seg.end.z}
        if isinstance(seg, Arc):
            s.update(arc=seg.direction, r=seg.radius)
        else:
            if seg.chamfer:
                s["c"] = seg.chamfer
            if seg.radius:
                s["r"] = seg.radius
        segs.append(s)
    return {"side": prof.side, "start": {"x": prof.start.x, "z": prof.start.z}, "seg": segs}


def op_to_dict(op: Operation) -> dict:
    out = {"type": kind_of(op)}
    # Write every value explicitly so a saved job never changes if defaults do.
    for f in dataclasses.fields(op):
        v = getattr(op, f.name)
        if v is None or (f.name == "comment" and not v):
            continue
        out[f.name] = v
    return out


def job_to_dict(job: Job) -> dict:
    return {
        "job": _clean(
            {
                "name": job.name,
                "format": JOB_FORMAT,
                "units": job.units,
                "description": job.description or None,
                "include_subs": job.include_subs,
            }
        ),
        "stock": _clean(dataclasses.asdict(job.stock) | {"material": job.stock.material or None}),
        "station": [
            _clean({"t": st.t, "tool": st.tool.id, "d": st.d})
            for st in sorted(job.setup.stations.values(), key=lambda s: s.t)
        ],
        "profile": {name: profile_to_dict(p) for name, p in job.profiles.items()},
        "op": [op_to_dict(op) for op in job.operations],
    }


def _tomli_w():
    try:
        import tomli_w
    except ImportError as e:  # FreeCAD's bundled Python may not have it
        raise ImportError(
            "saving job files needs the tomli-w package: pip install tomli-w "
            "(in FreeCAD, the Addon Manager installs it with the workbench)"
        ) from e
    return tomli_w


def save_job(job: Job, path: str | Path) -> None:
    Path(path).write_text("# omniturn-cam job file\n" + _tomli_w().dumps(job_to_dict(job), multiline_strings=False))


def profiles_toml(profiles: dict[str, Profile]) -> str:
    """Just the [profile.*] tables, e.g. to print or paste into a job."""
    return _tomli_w().dumps({"profile": {k: profile_to_dict(v) for k, v in profiles.items()}})


def merge_profiles(path: str | Path, profiles: dict[str, Profile]) -> None:
    """Add or replace profiles in an existing job file, keeping everything else."""
    data = tomllib.loads(Path(path).read_text())
    data.setdefault("profile", {}).update({k: profile_to_dict(v) for k, v in profiles.items()})
    Path(path).write_text("# omniturn-cam job file\n" + _tomli_w().dumps(data))
