"""Labels, units and help text for every fill-in-the-blank field.

The GUI builds its forms from this table, so adding a field to an operation
dataclass only needs a line here to get a proper label and tooltip.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from .ops import OPERATION_TYPES, Operation


@dataclass(frozen=True)
class FieldInfo:
    label: str
    kind: str            # station profile dia z len feed rpm sfm sec bool text choice
    help: str = ""
    choices: tuple = ()
    group: str = "Cut"   # form section
    optional: bool = False  # blank allowed -> automatic
    blank: str = "auto"     # placeholder shown when left blank


# Field name -> info. Per-operation overrides live in OVERRIDES below.
FIELDS: dict[str, FieldInfo] = {
    "t": FieldInfo("Tool station", "station", "Which T number on the plate does this cut", group="Tool"),
    "rpm": FieldInfo("Spindle RPM", "rpm", "Fixed spindle speed (G97). Leave blank to use surface speed",
                     group="Speed", optional=True),
    "sfm": FieldInfo("Surface speed", "sfm", "Constant surface speed (G96), SFM or m/min. Used when RPM is blank",
                     group="Speed", optional=True),
    "min_rpm": FieldInfo("CSS min RPM", "rpm", "G76 floor for constant surface speed", group="Speed", optional=True),
    "max_rpm": FieldInfo("CSS max RPM", "rpm", "G77 ceiling for constant surface speed (default: machine max)",
                         group="Speed", optional=True),
    "spindle": FieldInfo("Spindle direction", "choice", "m03 forward (top coming) / m04 reverse (top going)",
                         ("m03", "m04"), group="Speed"),
    "coolant": FieldInfo("Coolant", "bool", "m08 on for this cut", group="Speed"),
    "comment": FieldInfo("Operator note", "text", "Shown on the OmniTurn screen. Letters and digits only",
                         group="Tool"),
    # face
    "z": FieldInfo("Finished Z", "z", "Z position of the finished surface"),
    "stock": FieldInfo("Face stock", "len", "Material left on the face to remove"),
    "depth_per_pass": FieldInfo("Depth per pass", "len", "Blank = one pass", optional=True),
    "x_start": FieldInfo("Start diameter", "dia", "Blank = stock diameter plus clearance", optional=True),
    "x_end": FieldInfo("End diameter", "dia", "Blank = just past centre", optional=True),
    "feed": FieldInfo("Feed", "feed", "Feed per revolution (G95)"),
    # rough / finish
    "profile": FieldInfo("Shape", "profile", "Which part shape to cut", group="Shape"),
    "cycle": FieldInfo("Roughing cycle", "choice",
                       "G75 box contour: passes parallel to Z.  G78: passes follow the contour",
                       ("g75", "g78"), group="Shape"),
    "depth": FieldInfo("Depth of cut (I)", "len", "Max depth per pass, per side (G75)"),
    "u": FieldInfo("Finish stock (U)", "len",
                   "G75: stock left per side after roughing. G78: stock left over the whole contour"),
    "start_x": FieldInfo("Cycle start X", "dia", "Blank = automatic from stock / bore", optional=True,
                         group="Shape"),
    "start_z": FieldInfo("Cycle start Z", "z", "Blank = clearance in front of the shape", optional=True,
                         group="Shape"),
    "comp": FieldInfo("Nose radius comp", "choice",
                      "auto picks G41/G42 from the cut direction; needs a D number on the station",
                      ("auto", "g41", "g42", "none"), group="Shape"),
    # drill
    "peck": FieldInfo("Peck depth (K)", "len", "Blank = straight G81 drill. Set for G83 peck", optional=True,
                      blank="no peck"),
    "retract_z": FieldInfo("Peck retract Z (R)", "z", "Where the drill rapids out to clear chips", optional=True),
    "peck_clearance": FieldInfo("Peck return gap (C)", "len", "Stops this far short of the last depth on re-entry"),
    "peck_rapid": FieldInfo("Peck retract feed (L)", "feed_ipm", "Blank = rapid", optional=True),
    # axial / radial
    "x": FieldInfo("Diameter", "dia", "Diameter the tool runs at (0 for on-centre tools)"),
    "z_start": FieldInfo("Start Z", "z", "Approach position in front of the work"),
    "z_end": FieldInfo("End Z", "z", "Depth the tool feeds to"),
    "sync": FieldInfo("Spindle sync (P)", "bool", "Start the feed in sync with the spindle: die heads, single-point"),
    "dwell": FieldInfo("Dwell", "sec", "Seconds at the bottom of the cut (G04, min .1)"),
    "retract": FieldInfo("Retract", "choice", "rapid out, or feed out (box tools, burnishing)",
                         ("rapid", "feed")),
    "retract_feed": FieldInfo("Retract feed", "feed", "Blank = same as the cut", optional=True),
    "reverse_spindle_out": FieldInfo("Reverse spindle to back out", "bool", "Non-opening dies and taps"),
    "stop_spindle_after": FieldInfo("Stop spindle after", "bool", "m05 once the part is off"),
    "stop_after": FieldInfo("Stop after", "choice",
                            "m01 optional stop (only stops if the operator has optional stop ON) or "
                            "m00 program stop (always stops), e.g. to reset a die head",
                            ("none", "m01", "m00")),
    "stop_spindle_for_stop": FieldInfo("Stop spindle for the stop", "bool",
                                       "m05 before the stop so the operator isn't reaching past a turning "
                                       "spindle; the next cut restarts it"),
    "side": FieldInfo("OD or ID", "choice", "External or internal thread", ("od", "id"), group="Shape"),
    "major": FieldInfo("Major diameter", "dia", "Thread major diameter", group="Shape"),
    "minor": FieldInfo("Minor diameter", "dia", "Thread minor diameter", group="Shape"),
    "lead": FieldInfo("Lead (K)", "len", "Advance per revolution. Single start: lead = pitch = 1/TPI",
                      group="Shape"),
    "starts": FieldInfo("Starts", "int", "Multi-start: each start is its own G33, offset in Z by the pitch",
                        group="Shape"),
    "first_depth": FieldInfo("First pass (I)", "len",
                             "Depth of the first pass, 2x depth of cut in diameter mode. The control keeps "
                             "the material per pass constant; passes = (total depth / I) squared"),
    "runup": FieldInfo("Run-up", "len", "Start this far in front of the thread so the slide is up to "
                                        "speed. At least .1, more for coarse leads or high rpm"),
    "infeed_angle": FieldInfo("Angle infeed (C)", "text_num",
                              "Blank = straight in. 29 = the control's default angle. 0-30 degrees",
                              optional=True, blank="straight in"),
    "pullout": FieldInfo("Pull out while moving (P)", "bool",
                         "Tool keeps travelling forward as it pulls out - no undercut needed"),
    "taper_a": FieldInfo("Taper (A)", "len", "X travel over the whole Z travel including run-up. "
                                            "Needs a start X", optional=True, blank="none"),
    "spring_passes": FieldInfo("Spring passes (O)", "int", "Extra single passes at finished depth"),
    "pitch": FieldInfo("Pitch / lead", "len", "Lead per revolution, 1/TPI (e.g. .05 for 20 TPI)"),
    "feed_ratio": FieldInfo("Feed ratio", "text_num",
                            "Programmed feed = pitch x ratio; the floating holder takes up the rest (manual: ~95%)"),
    "reverse_dwell": FieldInfo("Reverse dwell", "sec", "Time for the spindle to reverse at the bottom"),
}

OVERRIDES: dict[str, dict[str, FieldInfo]] = {
    "box_rough": {"x": FieldInfo("Box corner X", "dia", "Diameter to rough down to (leave finish stock here)"),
                  "z": FieldInfo("Box corner Z", "z", "Shoulder Z to rough to (leave finish stock here)"),
                  "depth": FieldInfo("Depth of cut (I)", "len", "Per side, per pass"),
                  "u": FieldInfo("U (ignored)", "len", "G74 ignores U but the control requires the word")},
    "thread": {"z_start": FieldInfo("Thread start Z", "z", "Where the thread begins on the part"),
               "z_end": FieldInfo("Thread end Z (Z)", "z", "Where the thread ends"),
               "start_x": FieldInfo("Start X", "dia", "Blank = major minus I (OD) / minor plus I (ID)",
                                    optional=True)},
    "tap": {"x": FieldInfo("Diameter", "dia", "0 for a tap on centre"),
            "z_start": FieldInfo("Start Z", "z", "Just in front of the work"),
            "z_end": FieldInfo("End Z", "z", "Stop a little short of full depth; the holder floats")},
    "drill": {"z": FieldInfo("Drill depth Z", "z", "Absolute Z of the drill point at full depth"),
              "start_z": FieldInfo("Start Z", "z", "Blank = clearance in front of the face", optional=True)},
    "radial": {"z": FieldInfo("Z position", "z", "Z of the tool's reference edge"),
               "x_start": FieldInfo("Start diameter", "dia", "Approach diameter"),
               "x_end": FieldInfo("End diameter", "dia", "Diameter the tool feeds to")},
    "cutoff": {"z": FieldInfo("Cutoff Z", "z", "Z of the blade's reference edge"),
               "x_end": FieldInfo("End diameter", "dia", "Just past centre, e.g. -.01")},
}

# Friendly names and one-line descriptions for the "add process" menu.
OP_MENU = {
    "face": ("Face", "Face the end of the part"),
    "box_rough": ("Box rough (G74)", "Remove a rectangle of material in passes"),
    "rough": ("Rough contour", "G75/G78 roughing to a shape - the control works out the passes"),
    "finish": ("Finish contour", "One pass along the shape with nose radius comp"),
    "drill": ("Drill / peck drill", "G81 or G83 on centre"),
    "thread": ("Thread (G33)", "Single-point thread - the control works out the passes"),
    "tap": ("Tap / solid die", "Tap, solid die holder or acorn die in a floating holder"),
    "axial": ("Axial pass", "Box tool, releasing die head, reamer, burnishing - straight feed along Z"),
    "radial": ("Radial pass", "Groove, form tool, straddle knurl - straight feed along X"),
    "cutoff": ("Cutoff", "Part off"),
}

# Which tool kinds suit each operation (first match is picked by default).
OP_TOOL_KINDS = {
    "face": ("turn", "face"),
    "box_rough": ("turn", "bore"),
    "rough": ("turn", "bore"),
    "finish": ("turn", "bore"),
    "drill": ("center_drill", "spot_drill", "drill"),
    "thread": ("thread",),
    "tap": ("tap", "die_solid"),
    "axial": ("box_tool", "die_head", "reamer", "drill"),
    "radial": ("knurl_straddle", "knurl_bump", "groove", "form"),
    "cutoff": ("cutoff",),
}


def info(op_kind: str, name: str) -> FieldInfo:
    return OVERRIDES.get(op_kind, {}).get(name) or FIELDS.get(name) or FieldInfo(name, "text")


def op_kind(op: Operation) -> str:
    return next(k for k, cls in OPERATION_TYPES.items() if type(op) is cls)


def form_fields(op: Operation) -> list[tuple[str, FieldInfo]]:
    kind = op_kind(op)
    return [(f.name, info(kind, f.name)) for f in dataclasses.fields(op)]


def is_required(op_cls, name: str) -> bool:
    f = op_cls.__dataclass_fields__[name]
    return f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING


def new_operation(kind: str, t: int, tool=None, **overrides) -> Operation:
    """Create an operation with sensible blanks filled in, pre-loaded from the tool."""
    cls = OPERATION_TYPES[kind]
    vals: dict = {"t": t, "rpm": 500 if kind == "thread" else 1500}
    req = {"profile": "od", "z": 0.0, "x": 0.0, "z_start": 0.05, "z_end": -0.25,
           "x_start": 1.0, "x_end": 0.0, "feed": 0.003}
    for name in cls.__dataclass_fields__:
        if is_required(cls, name) and name not in vals:
            vals[name] = req.get(name, 0.0)
    if tool is not None:
        # Tool-specific behaviour: params on the tool whose names match op fields win.
        for k, v in tool.params.items():
            if k in cls.__dataclass_fields__:
                vals[k] = v
        if tool.kind == "die_head" and kind == "axial":
            # releasing die head: feed to the trip point, dwell, rapid out, stop to reset
            vals.update(sync=True, feed=tool.params.get("pitch", vals.get("feed")), retract="rapid",
                        dwell=0.2, stop_after="m01", comment="Reset die head")
        if tool.kind in ("tap", "die_solid") and kind == "tap" and "pitch" in tool.params:
            vals["pitch"] = tool.params["pitch"]
        if tool.kind == "box_tool":
            vals.setdefault("retract", "feed")
        if tool.kind == "knurl_straddle" and "tpi" in tool.params:
            vals.setdefault("comment", f"Knurl {tool.params['tpi']} TPI")
    vals.update(overrides)
    vals = {k: v for k, v in vals.items() if k in cls.__dataclass_fields__}
    return cls(**vals)


def summary(op: Operation, units: str = "inch") -> str:
    kind = op_kind(op)
    name = OP_MENU[kind][0]
    bits = [f"T{op.t}", name]
    if hasattr(op, "profile"):
        bits.append(f"[{op.profile}]")
    if kind == "rough":
        bits.append(op.cycle.upper())
    if kind == "drill":
        bits.append(f"Z{op.z:g}" + (" peck" if op.peck else ""))
    if kind == "thread":
        bits.append(f"{op.side.upper()} {op.major:g} K{op.lead * op.starts:g}" + (f" x{op.starts}" if op.starts > 1 else ""))
    if kind in ("axial", "tap"):
        bits.append(f"X{op.x:g} Z{op.z_start:g}>{op.z_end:g}")
    if kind in ("radial", "cutoff"):
        bits.append(f"Z{op.z:g}")
    if op.comment:
        bits.append(f"- {op.comment}")
    return "  ".join(bits)
