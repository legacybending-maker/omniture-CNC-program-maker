"""A job: units, stock, tool setup, named profiles and an ordered operation list.

Jobs are stored as TOML so they can be hand-edited, diffed in git, and written
by the fill-in-the-blank front end. See examples/ for the format.
"""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .geometry import Arc, Line, Point, Profile
from .ops import OPERATION_TYPES, Operation
from .tools import Setup, Station, ToolLibrary


@dataclass
class Stock:
    diameter: float
    length: float | None = None
    bore: float | None = None       # existing hole diameter, if any
    material: str = ""


@dataclass
class Job:
    name: str                       # also the program file name: max 8 characters
    units: Literal["inch", "metric"]
    stock: Stock
    setup: Setup
    profiles: dict[str, Profile] = field(default_factory=dict)
    operations: list[Operation] = field(default_factory=list)
    description: str = ""

    def __post_init__(self):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,8}", self.name):
            raise ValueError(
                f"job name {self.name!r} must be 1-8 letters/digits: it becomes the "
                "OmniTurn program file name (manual p2.8)"
            )
        for prof in self.profiles.values():
            prof.validate()
        for op in self.operations:
            self.setup[op.t]  # raises if the station isn't loaded
            prof = getattr(op, "profile", None)
            if prof is not None and prof not in self.profiles:
                raise KeyError(f"operation uses unknown profile {prof!r}")


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


def load_job(path: str | Path, library: ToolLibrary) -> Job:
    path = Path(path)
    data = tomllib.loads(path.read_text())
    j = data["job"]

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
    return Job(
        name=j["name"],
        units=j.get("units", "inch"),
        description=j.get("description", ""),
        stock=Stock(float(s["diameter"]), s.get("length"), s.get("bore"), s.get("material", "")),
        setup=setup,
        profiles=profiles,
        operations=ops,
    )
