"""Labels, units and help text for every fill-in-the-blank field.

The GUI builds its forms from this. Fields shared by several operations are in
FIELDS below; a field that belongs to one operation, or a different label for
a shared field, is in that operation's module (operations/<kind>.py, FIELDS).
The menu entries and tool kinds come from the operation modules too.
"""

from __future__ import annotations

import dataclasses

from .operations import OPERATION_TYPES, SPECS, Operation, kind_of
from .operations.base import FieldInfo

FIELDS: dict[str, FieldInfo] = {
    "t": FieldInfo("Tool station", "station", "Which T number on the plate does this cut", group="Tool"),
    "rpm": FieldInfo(
        "Spindle RPM",
        "rpm",
        "Fixed spindle speed (G97). Leave blank to use surface speed",
        group="Speed",
        optional=True,
    ),
    "sfm": FieldInfo(
        "Surface speed",
        "sfm",
        "Constant surface speed (G96), SFM or m/min. Used when RPM is blank",
        group="Speed",
        optional=True,
    ),
    "min_rpm": FieldInfo(
        "CSS min RPM",
        "rpm",
        "G76 floor for constant surface speed. Blank = no G76 line",
        group="Speed",
        optional=True,
        blank="none",
    ),
    "max_rpm": FieldInfo(
        "CSS max RPM",
        "rpm",
        "G77 ceiling for constant surface speed. Blank = the machine's max rpm (machine.toml)",
        group="Speed",
        optional=True,
        blank="machine max",
    ),
    "spindle": FieldInfo(
        "Spindle direction",
        "choice",
        "m03 forward (top coming) / m04 reverse (top going)",
        ("m03", "m04"),
        group="Speed",
    ),
    "coolant": FieldInfo("Coolant", "bool", "m08 on for this cut", group="Speed"),
    "comment": FieldInfo(
        "Operator note", "text", "Shown on the OmniTurn screen. Letters and digits only", group="Tool"
    ),
    # face
    "z": FieldInfo("Finished Z", "z", "Z position of the finished surface"),
    "x_start": FieldInfo("Start diameter", "dia", "Blank = stock diameter plus clearance", optional=True),
    "x_end": FieldInfo("End diameter", "dia", "Blank = just past centre", optional=True),
    "feed": FieldInfo("Feed", "feed", "Feed per revolution (G95)"),
    # rough / finish
    "profile": FieldInfo("Shape", "profile", "Which part shape to cut", group="Shape"),
    "depth": FieldInfo("Depth of cut (I)", "len", "Max depth per pass, per side (G75)"),
    "u": FieldInfo(
        "Finish stock (U)", "len", "G75: stock left per side after roughing. G78: stock left over the whole contour"
    ),
    "start_x": FieldInfo("Cycle start X", "dia", "Blank = automatic from stock / bore", optional=True, group="Shape"),
    "start_z": FieldInfo("Cycle start Z", "z", "Blank = clearance in front of the shape", optional=True, group="Shape"),
    # axial / radial
    "x": FieldInfo("Diameter", "dia", "Diameter the tool runs at (0 for on-centre tools)"),
    "z_start": FieldInfo("Start Z", "z", "Approach position in front of the work"),
    "z_end": FieldInfo("End Z", "z", "Depth the tool feeds to"),
    "dwell": FieldInfo("Dwell", "sec", "Seconds at the bottom of the cut (G04, min .1)"),
    "retract": FieldInfo("Retract", "choice", "rapid out, or feed out (box tools, burnishing)", ("rapid", "feed")),
    "retract_feed": FieldInfo("Retract feed", "feed", "Blank = same as the cut", optional=True),
}

# Built from the operation modules (kept under these names for the GUI and tests).
OVERRIDES: dict[str, dict[str, FieldInfo]] = {k: dict(s.fields) for k, s in SPECS.items() if s.fields}
OP_MENU: dict[str, tuple[str, str]] = {k: s.menu for k, s in SPECS.items()}
OP_TOOL_KINDS: dict[str, tuple[str, ...]] = {k: s.tool_kinds for k, s in SPECS.items() if s.tool_kinds}


def info(op_kind: str, name: str) -> FieldInfo:
    return OVERRIDES.get(op_kind, {}).get(name) or FIELDS.get(name) or FieldInfo(name, "text")


def op_kind(op: Operation) -> str:
    return kind_of(op)


TOOL_ONLY = {"t", "rpm", "sfm", "min_rpm", "max_rpm", "spindle", "coolant"}


def form_fields(op: Operation) -> list[tuple[str, FieldInfo]]:
    kind = op_kind(op)
    return [(f.name, info(kind, f.name)) for f in dataclasses.fields(op) if op.uses_tool or f.name not in TOOL_ONLY]


def is_required(op_cls, name: str) -> bool:
    f = op_cls.__dataclass_fields__[name]
    return f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING


def new_operation(kind: str, t: int, tool=None, **overrides) -> Operation:
    """Create an operation with sensible blanks filled in, pre-loaded from the tool."""
    spec = SPECS[kind]
    cls = spec.cls
    vals: dict = {"t": t, "rpm": spec.default_rpm}
    req = {
        "profile": "od",
        "z": 0.0,
        "x": 0.0,
        "z_start": 0.05,
        "z_end": -0.25,
        "x_start": 1.0,
        "x_end": 0.0,
        "feed": 0.003,
    }
    for name in cls.__dataclass_fields__:
        if is_required(cls, name) and name not in vals:
            vals[name] = req.get(name, 0.0)
    if tool is not None:
        # Tool-specific behaviour: params on the tool whose names match op fields win.
        for k, v in tool.params.items():
            if k in cls.__dataclass_fields__:
                vals[k] = v
        if spec.tool_defaults:
            spec.tool_defaults(tool, vals)
        # Tool kinds that behave the same whatever the operation:
        if tool.kind == "box_tool":
            vals.setdefault("retract", "feed")
        if tool.kind == "knurl_straddle" and "tpi" in tool.params:
            vals.setdefault("comment", f"Knurl {tool.params['tpi']} TPI")
    vals.update(overrides)
    vals = {k: v for k, v in vals.items() if k in cls.__dataclass_fields__}
    return cls(**vals)


def summary(op: Operation, units: str = "inch") -> str:
    spec = SPECS[op_kind(op)]
    bits = [f"T{op.t}", spec.menu[0]] if op.uses_tool else [spec.menu[0]]
    if hasattr(op, "profile"):
        bits.append(f"[{op.profile}]")
    if spec.summary:
        bits += spec.summary(op)
    if op.comment:
        bits.append(f"- {op.comment}")
    return "  ".join(bits)


__all__ = [
    "FIELDS",
    "OVERRIDES",
    "OP_MENU",
    "OP_TOOL_KINDS",
    "OPERATION_TYPES",
    "FieldInfo",
    "info",
    "op_kind",
    "form_fields",
    "is_required",
    "new_operation",
    "summary",
]
