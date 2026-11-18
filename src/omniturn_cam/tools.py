"""Tool library and tool-plate setup.

The library describes tools as physical objects (what they are, how they are
held, nose radius, optional STEP/DXF model). A *setup* says which tool sits at
which OmniTurn station number (T1-T32) and which secondary offset (D number)
holds its nose-radius value for this job.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from .validation import Issue, ValidationError, is_finite, show, type_issues

# What the tool is. Screw-machine tooling is first-class, not an afterthought.
ToolKind = Literal[
    "turn",
    "bore",
    "face",
    "groove",
    "cutoff",
    "thread",
    "drill",
    "center_drill",
    "spot_drill",
    "reamer",
    "tap",
    "box_tool",  # roller-back / balanced turning box tool
    "knurl_straddle",  # straddle (cut) knurl
    "knurl_bump",  # bump / form knurl
    "die_head",  # releasing die head (geometric / self-opening): no reversal
    "die_solid",  # solid die holder / acorn die: run like a tap
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
    "drill": "axial",
    "center_drill": "axial",
    "spot_drill": "axial",
    "reamer": "axial",
    "tap": "axial",
    "bore": "axial",
    "box_tool": "axial",
    "die_head": "axial",
    "die_solid": "axial",
}

AXIAL_KINDS = {"drill", "center_drill", "spot_drill", "reamer", "tap"}

# Tool params that operations read (fields.new_operation copies a param whose
# name matches an operation field; Tap and Axial pass read `pitch`; the knurl
# comment reads `tpi`). They must be positive numbers when they're given.
# Other params are notes for the operator and can be anything TOML allows.
NUMERIC_PARAMS = ("pitch", "tpi")


@dataclass
class Tool:
    id: str
    name: str
    kind: ToolKind
    hand: Literal["left", "right", "neutral"] = "neutral"
    orientation: Orientation | None = None
    nose_radius: float = 0.0
    diameter: float | None = None  # drills, reamers, taps
    width: float | None = None  # grooving / cutoff blade width
    holder: str = ""  # toolholding system, e.g. "QC 3/4 gang block"
    model: str | None = None  # path to STEP/DXF of the tool for backplot/collision
    units: Literal["inch", "metric"] = "inch"
    notes: str = ""
    params: dict[str, Any] = field(default_factory=dict)  # kind-specific: pitch, knurl tpi, etc.

    def __post_init__(self):
        """Fill in the orientation from the kind, then refuse an impossible tool.

        Checking here, in the one place every Tool is made (tools.toml, the
        tool library editor, tests), means no path can build a tool with a
        negative nose radius. Raises ValidationError (a ValueError) listing
        every problem."""
        if self.orientation is None and isinstance(self.kind, str):
            self.orientation = KIND_DEFAULT_ORIENTATION.get(self.kind, "radial")
        problems = self.issues()
        if problems:
            raise ValidationError(problems)

    def issues(self, where: str | None = None) -> list[Issue]:
        """Every problem with this tool (validation.py, Issue)."""
        where = where or f"tool {self.id!r}"
        out = type_issues(self, where, skip={"params"})
        if out:
            return out
        if not self.id.strip():
            out.append(Issue(where, "can't be empty: jobs refer to the tool by it", setting="id"))
        # Zero means "none" for these, as it always has: the drill emitter
        # reads `tool.diameter or stock` and the tool editor saves 0 for a
        # blank radius. Negative is never a real tool.
        if self.nose_radius < 0:
            out.append(Issue(where, "can't be negative", "0 for a sharp tool", "nose_radius", show(self.nose_radius)))
        for name in ("diameter", "width"):
            v = getattr(self, name)
            if v is not None and v < 0:
                out.append(Issue(where, "can't be negative", "Leave it out if it doesn't apply", name, show(v)))
        if not isinstance(self.params, dict):
            out.append(Issue(where, "must be a table of extra settings", setting="params"))
            return out
        for name in NUMERIC_PARAMS:
            if name in self.params:
                v = self.params[name]
                if not is_finite(v) or v <= 0:
                    out.append(Issue(where, "must be a number greater than zero", setting=name, value=show(v)))
        return out

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
    def load(cls, path: str | Path) -> ToolLibrary:
        """Read a tools.toml. Keys that aren't Tool fields are kept in the
        tool's params, as they always have been (old libraries keep loading).

        Raises ValidationError (a ValueError) naming the file and each tool
        that is wrong, with every problem found, not only the first.
        """
        path = Path(path)
        try:
            data = tomllib.loads(path.read_text())
        except OSError as e:
            raise ValidationError([Issue(str(path), f"can't be read: {e.strerror or e}")]) from None
        except tomllib.TOMLDecodeError as e:
            raise ValidationError([Issue(str(path), f"is not a valid tool library: {e}")]) from None
        known = set(Tool.__dataclass_fields__)
        tools, problems = [], []
        entries = data.get("tool", [])
        if not isinstance(entries, list):
            raise ValidationError([Issue(str(path), "tools must be [[tool]] tables", setting="tool")])
        for i, entry in enumerate(entries, 1):
            where = f"{path}, tool {i}" + (f" ({entry['id']!r})" if isinstance(entry.get("id"), str) else "")
            missing = [k for k in ("id", "name", "kind") if k not in entry]
            if missing:
                problems.append(Issue(where, "is missing " + ", ".join(missing)))
                continue
            extra = {k: v for k, v in entry.items() if k not in known}
            entry = {k: v for k, v in entry.items() if k in known}
            entry.setdefault("params", {})
            if isinstance(entry["params"], dict):
                entry["params"].update(extra)
            if isinstance(entry.get("model"), str) and entry["model"]:
                entry["model"] = str((path.parent / entry["model"]).resolve())
            try:
                tools.append(Tool(**entry))
            except ValidationError as e:
                # Same problems, but saying which file and which tool.
                problems += [replace(issue, where=where) for issue in e.issues]
        if problems:
            raise ValidationError(problems)
        try:
            return cls(tools)
        except ValueError as e:  # duplicate ids
            raise ValidationError([Issue(str(path), str(e))]) from None

    def remove(self, tool_id: str) -> None:
        del self._tools[tool_id]

    def rekey(self, old_id: str, tool: Tool) -> None:
        """Replace the entry stored under old_id with tool (whose id may differ)."""
        if tool.id != old_id and tool.id in self._tools:
            raise ValueError(f"duplicate tool id {tool.id!r}")
        items = [(tool.id, tool) if k == old_id else (k, v) for k, v in self._tools.items()]
        self._tools = dict(items)

    def save(self, path: str | Path) -> None:
        import tomli_w

        path = Path(path)
        out = []
        for t in self:
            d = {
                "id": t.id,
                "name": t.name,
                "kind": t.kind,
                "hand": t.hand,
                "orientation": t.orientation,
                "nose_radius": t.nose_radius,
                "diameter": t.diameter,
                "width": t.width,
                "holder": t.holder or None,
                "model": t.model,
                "units": t.units,
                "notes": t.notes or None,
            }
            if t.model:
                try:
                    d["model"] = str(Path(t.model).resolve().relative_to(path.parent.resolve()))
                except ValueError:
                    pass
            d = {k: v for k, v in d.items() if v is not None}
            for k, v in t.params.items():
                if k not in d and v is not None:
                    d[k] = v
            out.append(d)
        path.write_text("# omniturn-cam tool library\n" + tomli_w.dumps({"tool": out}))


@dataclass
class Station:
    t: int  # OmniTurn tool number 1-32
    tool: Tool
    d: int | None = None  # secondary offset holding the nose radius for TNR comp

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
