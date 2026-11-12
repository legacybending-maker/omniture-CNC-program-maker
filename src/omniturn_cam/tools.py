"""Tool library and tool-plate setup.

The library describes tools as physical objects (what they are, how they are
held, nose radius, optional STEP/DXF model). A *setup* says which tool sits at
which OmniTurn station number (T1-T32) and which secondary offset (D number)
holds its nose-radius value for this job.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

# What the tool is. Screw-machine tooling is first-class, not an afterthought.
ToolKind = Literal[
    "turn", "bore", "face", "groove", "cutoff", "thread",
    "drill", "center_drill", "spot_drill", "reamer", "tap",
    "box_tool",          # roller-back / balanced turning box tool
    "knurl_straddle",    # straddle (cut) knurl
    "knurl_bump",        # bump / form knurl
    "die_head",          # geometric / self-opening die head
    "form",
    "custom",
]

# How the tool is mounted relative to the machine axes.
#   radial        - cutting edge points along X toward the spindle centreline
#                   (normal stick tools, cutoff blades)
#   axial         - tool axis parallel to Z, cuts toward the spindle
#                   (drills, reamers, boring bars, box tools, die heads)
#   perpendicular - tool body perpendicular to the plate face (see README;
#                   geometry handled the same as radial for code generation)
Orientation = Literal["radial", "axial", "perpendicular"]

KIND_DEFAULT_ORIENTATION: dict[str, Orientation] = {
    "drill": "axial", "center_drill": "axial", "spot_drill": "axial", "reamer": "axial",
    "tap": "axial", "bore": "axial", "box_tool": "axial", "die_head": "axial",
}

AXIAL_KINDS = {"drill", "center_drill", "spot_drill", "reamer", "tap"}


@dataclass
class Tool:
    id: str
    name: str
    kind: ToolKind
    hand: Literal["left", "right", "neutral"] = "neutral"
    orientation: Orientation | None = None
    nose_radius: float = 0.0
    diameter: float | None = None      # drills, reamers, taps
    width: float | None = None         # grooving / cutoff blade width
    holder: str = ""                   # toolholding system, e.g. "QC 3/4 gang block"
    model: str | None = None           # path to STEP/DXF of the tool for backplot/collision
    units: Literal["inch", "metric"] = "inch"
    notes: str = ""
    params: dict[str, Any] = field(default_factory=dict)  # kind-specific: pitch, knurl tpi, etc.

    def __post_init__(self):
        if self.orientation is None:
            self.orientation = KIND_DEFAULT_ORIENTATION.get(self.kind, "radial")

    @property
    def comment(self) -> str:
        return self.name


class ToolLibrary:
    def __init__(self, tools: list[Tool] | None = None):
        self._tools: dict[str, Tool] = {}
        for t in tools or []:
            self.add(t)

    def add(self, tool: Tool) -> None:
        if tool.id in self._tools:
            raise ValueError(f"duplicate tool id {tool.id!r}")
        self._tools[tool.id] = tool

    def __getitem__(self, tool_id: str) -> Tool:
        try:
            return self._tools[tool_id]
        except KeyError:
            raise KeyError(f"tool {tool_id!r} is not in the tool library") from None

    def __iter__(self):
        return iter(self._tools.values())

    def __len__(self):
        return len(self._tools)

    @classmethod
    def load(cls, path: str | Path) -> "ToolLibrary":
        path = Path(path)
        data = tomllib.loads(path.read_text())
        known = set(Tool.__dataclass_fields__)
        tools = []
        for entry in data.get("tool", []):
            extra = {k: v for k, v in entry.items() if k not in known}
            entry = {k: v for k, v in entry.items() if k in known}
            entry.setdefault("params", {}).update(extra)
            if entry.get("model"):
                entry["model"] = str((path.parent / entry["model"]).resolve())
            tools.append(Tool(**entry))
        return cls(tools)


@dataclass
class Station:
    t: int                 # OmniTurn tool number 1-32
    tool: Tool
    d: int | None = None   # secondary offset holding the nose radius for TNR comp

    def __post_init__(self):
        if not 1 <= self.t <= 32:
            raise ValueError(f"tool number t{self.t} out of range (OmniTurn allows t1-t32)")


@dataclass
class Setup:
    stations: dict[int, Station] = field(default_factory=dict)

    def add(self, station: Station) -> None:
        if station.t in self.stations:
            raise ValueError(f"t{station.t} is assigned twice in the setup")
        self.stations[station.t] = station

    def __getitem__(self, t: int) -> Station:
        try:
            return self.stations[t]
        except KeyError:
            raise KeyError(f"no tool loaded at t{t} in this setup") from None
