"""A job: units, stock, tool setup, named profiles and an ordered operation list.

Jobs are stored as TOML so they can be hand-edited, diffed in git, and written
by the fill-in-the-blank front end. See examples/ for the format.
"""

from __future__ import annotations

import dataclasses
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .geometry import Arc, Line, Point, Profile
from .operations import OPERATION_TYPES, Operation, kind_of
from .tools import Setup, Station, ToolLibrary

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

    def validate(self) -> None:
        """Raise ValueError/KeyError describing the first problem found."""
        if not NAME_RE.fullmatch(self.name):
            raise ValueError(
                f"job name {self.name!r} must be 1-8 letters/digits: it becomes the "
                "OmniTurn program file name (manual p2.8)"
            )
        if self.stock.diameter <= 0:
            raise ValueError("stock diameter must be greater than zero")
        for prof in self.profiles.values():
            prof.validate()
        if self.include_subs and not Path(self.include_subs).is_file():
            raise ValueError(f"subroutine file {self.include_subs} not found")
        for i, op in enumerate(self.operations, 1):
            if not op.uses_tool:
                continue
            if op.t not in self.setup.stations:
                raise KeyError(f"operation {i} ({op.label}) uses t{op.t}, which has no tool loaded")
            prof = getattr(op, "profile", None)
            if prof is not None and prof not in self.profiles:
                raise KeyError(f"operation {i} ({op.label}) uses unknown profile {prof!r}")


# --------------------------------------------------------------------- reading
def _point(v) -> Point:
    if isinstance(v, dict):
        return Point(float(v["x"]), float(v["z"]))
    x, z = v
    return Point(float(x), float(z))


def profile_from_dict(name: str, d: dict) -> Profile:
    prof = Profile(start=_point(d["start"]), name=name, side=d.get("side", "od"))
    for seg in d.get("seg", []):
        last = prof.end
        x = float(seg.get("x", last.x))
        z = float(seg.get("z", last.z))
        if "arc" in seg:
            prof.segments.append(Arc(Point(x, z), float(seg["r"]), seg["arc"]))
        else:
            prof.segments.append(Line(Point(x, z), seg.get("c"), seg.get("r")))
    return prof


def job_from_dict(data: dict, library: ToolLibrary, base_dir: Path | None = None) -> Job:
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
    for st in data.get("station", []):
        setup.add(Station(int(st["t"]), library[st["tool"]], st.get("d")))

    profiles = {name: profile_from_dict(name, d) for name, d in data.get("profile", {}).items()}

    ops = []
    for o in data.get("op", []):
        o = dict(o)
        kind = o.pop("type")
        try:
            cls = OPERATION_TYPES[kind]
        except KeyError:
            raise ValueError(f"unknown operation type {kind!r}; known: {', '.join(OPERATION_TYPES)}") from None
        ops.append(cls(**o))

    s = data["stock"]
    inc = j.get("include_subs")
    if inc and base_dir is not None and not Path(inc).is_absolute():
        inc = str((base_dir / inc).resolve())
    return Job(
        name=j["name"],
        units=j.get("units", "inch"),
        description=j.get("description", ""),
        include_subs=inc,
        stock=Stock(float(s["diameter"]), s.get("length"), s.get("bore"), s.get("material", "")),
        setup=setup,
        profiles=profiles,
        operations=ops,
    )


def load_job(path: str | Path, library: ToolLibrary, validate: bool = True) -> Job:
    job = job_from_dict(tomllib.loads(Path(path).read_text()), library, Path(path).parent)
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
