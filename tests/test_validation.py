"""Input validation (src/omniturn_cam/validation.py, opcheck.py, Job.issues,
Profile.issues): bad values are refused before any G-code is written, every
problem names the operation, the setting and the value, and legitimate jobs
(negative Z, cutoffs past centre, ID grooves feeding outward) still post.

Most tests are parametrized over SPECS, the registered operations, so a new
operation is covered the day it's added: its numeric fields get the NaN and
infinity tests, its feeds the zero-feed tests, and so on.
"""

from __future__ import annotations

import dataclasses
import math
from pathlib import Path

import pytest

from omniturn_cam.fields import FIELDS, TOOL_ONLY, info, new_operation
from omniturn_cam.geometry import Point, Profile
from omniturn_cam.job import Job, Stock, load_job
from omniturn_cam.operations import SPECS
from omniturn_cam.post import MachineConfig, PostError, post
from omniturn_cam.tools import Setup, Station, ToolLibrary
from omniturn_cam.validation import ValidationError, _optional, hints, type_problem

ROOT = Path(__file__).parent.parent
EX = ROOT / "examples"

# Which library tool each operation is tried with.
TOOL_FOR = {
    "face": "lh-turn-35",
    "box_rough": "lh-turn-35",
    "rough": "lh-turn-35",
    "finish": "lh-turn-35",
    "drill": "drill-250",
    "thread": "thread-od-60",
    "tap": "tap-1-4-20",
    "axial": "box-bore",
    "radial": "knurl-strd-80",
    "cutoff": "cutoff-062",
}


@pytest.fixture(scope="module")
def lib():
    return ToolLibrary.load(EX / "tools.toml")


@pytest.fixture(scope="module")
def subs(tmp_path_factory):
    """An include file with subroutine 1, so a Call subroutine op has something to call."""
    path = tmp_path_factory.mktemp("subs") / "subs.txt"
    path.write_text("}1\nm10\nm99\n")
    return str(path)


def job_for(lib, subs, kind, units="inch", **changes):
    """A job holding one `kind` operation with its new-operation defaults for
    `units`, then `changes` applied. Bar 1 in or 25.4 mm, one OD shape."""
    k = 1 if units == "inch" else 25.4
    setup = Setup()
    setup.add(Station(1, lib[TOOL_FOR.get(kind, "lh-turn-35")], d=1))
    prof = Profile(Point(0, 0), name="od").line(x=0.5 * k).line(z=-0.5 * k).line(x=0.75 * k)
    extra = {"code": "m91"} if kind == "raw" else {}
    op = new_operation(kind, 1, setup.stations[1].tool, units=units, **extra)
    for name, value in changes.items():
        setattr(op, name, value)
    return Job("T", units, Stock(1.0 * k), setup, {"od": prof}, [op], include_subs=subs)


def refused(job, *words, machine=None):
    """Post `job`, expect a PostError, and check every word is in the message."""
    with pytest.raises(PostError) as e:
        post(job, machine)
    for w in words:
        assert w in str(e.value), f"{w!r} not in: {e.value}"
    return str(e.value)


def fields_of(kind):
    """The fields an operator (or a job file) can set for this kind."""
    cls = SPECS[kind].cls
    return [f.name for f in dataclasses.fields(cls) if cls.uses_tool or f.name not in TOOL_ONLY]


def numeric_fields():
    """(kind, field) for every int/float field of every operation."""
    out = []
    for kind, s in SPECS.items():
        h = hints(s.cls)
        for name in fields_of(kind):
            _, base = _optional(h[name])
            if base in (int, float):
                out.append((kind, name))
    return out


def fields_of_kind(*kinds):
    """(kind, field) for every field whose FieldInfo kind is one of `kinds`."""
    return [(k, n) for k in SPECS for n in fields_of(k) if info(k, n).kind in kinds]


# --------------------------------------------------------------------- legitimate jobs keep working
@pytest.mark.parametrize("units", ["inch", "metric"])
@pytest.mark.parametrize("kind", list(SPECS))
def test_every_new_operation_posts_without_problems(lib, subs, kind, units):
    """The starting values new_operation() gives must themselves be valid, in
    both unit systems. If they weren't, the operator's first press of F5 after
    adding a process would show an error they didn't cause."""
    warns: list[str] = []
    post(job_for(lib, subs, kind, units), warnings=warns)
    assert warns == []


def test_example_and_golden_jobs_have_no_validation_errors(lib):
    """Every proven example and golden job passes all three layers."""
    for path in [*EX.glob("*.toml"), *(ROOT / "tests" / "golden" / "jobs").glob("*.toml")]:
        if "[job]" not in path.read_text():
            continue
        job = load_job(path, lib)
        found = job.issues(MachineConfig.for_units(job.units))
        assert not [i for i in found if i.severity == "error"], (path.name, [str(i) for i in found])


@pytest.mark.parametrize(
    "kind, changes",
    [
        ("drill", {"z": -0.75, "start_z": 0.1}),  # negative Z is the normal drill depth
        ("face", {"z": -0.02, "x_end": -0.03}),  # facing past centre
        ("cutoff", {"z": -0.662, "x_end": -0.01}),  # just past centre
        ("radial", {"x_start": 0.3, "x_end": 0.45}),  # ID groove: feeds *out*
        ("axial", {"z_start": -0.6, "z_end": -0.1}),  # back-boring: feeds toward the face
        ("axial", {"x": 0.0}),  # on centre
        ("box_rough", {"x": 0.96, "z": -0.59}),  # absolute diameter corner
        ("thread", {"side": "id", "major": 0.9375, "minor": 0.8783, "first_depth": 0.01, "z_end": -0.75}),
        ("tap", {"z_start": 0.1, "z_end": -0.3}),
    ],
)
def test_valid_negative_and_diameter_coordinates_post(lib, subs, kind, changes):
    """No blanket sign rules: these are all real cuts and must not be refused."""
    assert "m30" in post(job_for(lib, subs, kind, **changes))


# --------------------------------------------------------------------- layer 1: types and finiteness
@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf], ids=["nan", "inf", "-inf"])
@pytest.mark.parametrize("kind, name", numeric_fields())
def test_nan_and_infinity_refused_in_every_numeric_field(lib, subs, kind, name, bad):
    """NaN slips past every `<= 0` check (all comparisons with it are False) and
    would be written as 'fnan'; infinity as 'finf'. Every number field of every
    operation refuses both, and the message names the field."""
    refused(job_for(lib, subs, kind, **{name: bad}), "operation 1", f"[{name}]")


@pytest.mark.parametrize("kind, name", fields_of_kind("feed"))
@pytest.mark.parametrize("bad", [0.0, -0.002], ids=["zero", "negative"])
def test_zero_and_negative_feeds_refused(lib, subs, kind, name, bad):
    """A zero feed was written as 'f0' (Drill: g81...f0); negative feeds too."""
    refused(job_for(lib, subs, kind, **{name: bad}), f"[{name}]", "greater than zero")


@pytest.mark.parametrize("kind", [k for k, s in SPECS.items() if s.cls.uses_tool])
@pytest.mark.parametrize("field, bad", [("rpm", 0), ("rpm", -500), ("sfm", -300), ("sfm", 0)])
def test_zero_and_negative_spindle_speeds_refused(lib, subs, kind, field, bad):
    """Negative rpm used to reach the spindle line as m03s-500."""
    refused(job_for(lib, subs, kind, **{field: bad}), f"[{field}]", "greater than zero")


@pytest.mark.parametrize(
    "kind, name, bad, says",
    [
        ("drill", "rpm", "1500", "must be a number"),  # a quoted number in a job file
        ("drill", "feed", True, "must be a number"),  # bool is an int in Python; refused anyway
        ("face", "coolant", "yes", "true or false"),
        ("face", "coolant", 1, "true or false"),
        ("face", "spindle", "M03", "one of m03, m04"),
        ("rough", "cycle", "g76", "one of g75, g78"),
        ("thread", "starts", 2.0, "whole number"),
        ("thread", "spring_passes", 1.5, "whole number"),
        ("call_sub", "sub", "1", "whole number"),
        ("face", "comment", 5, "must be text"),
        ("drill", "z", None, "must be filled in"),  # a required field left blank
    ],
)
def test_wrong_types_refused(lib, subs, kind, name, bad, says):
    refused(job_for(lib, subs, kind, **{name: bad}), f"[{name}]", says)


def test_every_problem_is_reported_at_once(lib, subs):
    """Two bad operations: both reported in one go, not one per attempt."""
    job = job_for(lib, subs, "drill", feed=0.0)
    job.operations.append(new_operation("face", 1, rpm=-1))
    msg = refused(job, "2 problems", "operation 1 (drill t1)", "operation 2 (face t1)")
    assert "Feed [feed] is 0" in msg and "Spindle RPM [rpm] is -1" in msg


def test_message_names_operation_field_value_and_fix(lib, subs):
    msg = refused(job_for(lib, subs, "drill", peck=0.0))
    assert msg == (
        "operation 1 (drill t1): Peck depth (K) [peck] is 0: must be greater than zero. "
        "Leave it blank for a straight G81 drill"
    )


def test_every_field_annotation_can_be_checked():
    """type_problem raises TypeError for a type hint it doesn't understand. Any
    new field with such a hint fails here, instead of going unchecked."""
    for s in SPECS.values():
        for hint in hints(s.cls).values():
            type_problem(None, hint)  # raises TypeError for an unknown hint


@pytest.mark.parametrize("kind", list(SPECS))
def test_blank_allowed_exactly_where_the_form_says_optional(kind):
    """A field the form lets you leave blank must accept None, and a field that
    accepts None must say on the form what blank does (CONTRIBUTING: Usability).
    Otherwise the form and the validator disagree about the same box."""
    h = hints(SPECS[kind].cls)
    for name in fields_of(kind):
        allows_none, _ = _optional(h[name])
        assert allows_none == info(kind, name).optional, f"{kind}.{name}"


def test_validation_module_imports_nothing_from_the_project():
    """validation.py is imported by the operation modules while the operations
    package is starting up; a project import there would be an import cycle."""
    src = (ROOT / "src" / "omniturn_cam" / "validation.py").read_text()
    runtime = src.split("if TYPE_CHECKING:")[0] + src.split("if TYPE_CHECKING:")[1].split("\n\n", 1)[1]
    assert "from ." not in runtime and "import omniturn_cam" not in runtime


# --------------------------------------------------------------------- layer 2: each operation's own rules
@pytest.mark.parametrize(
    "kind, changes, words",
    [
        # optional values that 0 used to silently turn off
        ("face", {"depth_per_pass": 0.0}, ["[depth_per_pass]", "blank for one pass"]),
        ("drill", {"peck": 0.0}, ["[peck]", "straight G81"]),
        ("drill", {"peck": -0.1}, ["[peck]"]),
        ("axial", {"retract_feed": 0.0}, ["[retract_feed]"]),
        ("drill", {"peck": 0.1, "peck_rapid": -100.0}, ["[peck_rapid]"]),
        ("face", {"sfm": 400, "rpm": None, "min_rpm": 3000, "max_rpm": 2000}, ["[min_rpm]", "G76"]),
        ("axial", {"dwell": -0.5}, ["[dwell]", "negative"]),
        ("face", {"stock": -0.01}, ["[stock]"]),
        ("face", {"x_start": 0.5, "x_end": 0.5}, ["[x_end]", "nowhere"]),
        # drill direction and peck geometry (p2.48-2.50)
        ("drill", {"z": 0.2, "start_z": 0.1}, ["[z]", "below the start Z"]),
        ("drill", {"z": 0.1, "start_z": 0.1}, ["[z]"]),
        ("drill", {"z": -0.5, "peck": 0.1, "retract_z": -0.6}, ["[retract_z]", "above the drill depth"]),
        ("drill", {"peck": 0.1, "peck_clearance": -0.01}, ["[peck_clearance]", "uncut"]),
        # thread (consolidated from the emitter)
        ("thread", {"major": 0.4, "minor": 0.5}, ["[minor]", "smaller than the major"]),
        ("thread", {"z_start": -0.5, "z_end": 0.0}, ["[z_end]", "toward the chuck"]),
        ("thread", {"lead": 0.0}, ["[lead]"]),
        ("thread", {"lead": -0.05}, ["[lead]"]),
        ("thread", {"starts": 0}, ["[starts]"]),
        ("thread", {"first_depth": 0.0}, ["[first_depth]"]),
        ("thread", {"first_depth": 0.2}, ["already past the finished X", "make I smaller"]),
        ("thread", {"infeed_angle": 31.0}, ["[infeed_angle]", "0-30", "p2.24"]),
        ("thread", {"infeed_angle": -1.0}, ["[infeed_angle]"]),
        ("thread", {"runup": -0.1}, ["[runup]"]),
        ("thread", {"spring_passes": -1}, ["[spring_passes]"]),
        ("thread", {"taper_a": 0.05}, ["[start_x]", "tapered"]),
        ("thread", {"rpm": None, "sfm": 300}, ["fixed RPM"]),
        # tap, box rough, rough, radial, cutoff, raw, call sub
        ("tap", {"pitch": 0.0}, ["[pitch]"]),
        ("tap", {"feed_ratio": 0.0}, ["[feed_ratio]"]),
        ("tap", {"z_start": -0.3, "z_end": 0.1}, ["[z_end]", "toward the chuck"]),
        ("box_rough", {"depth": 0.0}, ["[depth]"]),
        ("box_rough", {"start_x": 0.9, "x": 0.9}, ["[x]", "no box"]),
        ("box_rough", {"z": 0.05}, ["[z]", "automatic start Z"]),  # start_z blank = z_clear .05
        ("rough", {"depth": 0.0}, ["[depth]"]),
        ("rough", {"u": -0.005}, ["[u]", "stock left"]),
        ("radial", {"x_end": 1.0}, ["[x_end]", "wouldn't move"]),  # x_start default is 1.0
        ("axial", {"z_end": 0.05}, ["[z_end]", "wouldn't move"]),  # z_start default .05
        ("cutoff", {"x_start": 0.5, "x_end": 0.8}, ["[x_end]", "toward the centre"]),
        ("cutoff", {"x_end": 2.0}, ["[x_end]", "automatic start diameter"]),
        ("raw", {"code": "  \n "}, ["[code]", "no program lines"]),
        ("call_sub", {"sub": 0}, ["[sub]", "1 or more"]),
    ],
)
def test_operation_rules(lib, subs, kind, changes, words):
    refused(job_for(lib, subs, kind, **changes), *words)


def test_finish_with_comp_needs_a_d_number(lib, subs):
    """Moved from the emitter into validation: a G41/G42 pass needs the
    secondary offset holding the nose radius (p2.35)."""
    job = job_for(lib, subs, "finish")
    job.setup.stations[1].d = None
    refused(job, "[comp]", "needs a D number")
    job.operations[0].comp = "none"
    assert "g41" not in post(job)


def test_shape_with_no_segments_refused_for_rough_and_finish(lib, subs):
    for kind in ("rough", "finish"):
        job = job_for(lib, subs, kind)
        job.profiles["od"] = Profile(Point(0, 0), name="od")
        refused(job, "[profile]", "no segments")


def test_short_thread_runup_is_a_warning_not_an_error(lib, subs):
    warns: list[str] = []
    post(job_for(lib, subs, "thread", runup=0.05), warnings=warns)
    assert any("[runup]" in w and "shorter" in w for w in warns)


def test_cutoff_above_centre_is_a_warning(lib, subs):
    warns: list[str] = []
    post(job_for(lib, subs, "cutoff", x_end=0.1), warnings=warns)
    assert any("won't come off" in w for w in warns)


# --------------------------------------------------------------------- layer 3: against the machine
def test_feed_over_the_control_limit_at_fixed_rpm_refused(lib, subs):
    """p2.11: at 4000 rpm the fastest G95 feed is .075. .3 at 1500 rpm is 450 ipm:
    almost always a decimal point in the wrong place."""
    refused(job_for(lib, subs, "face", rpm=1500, feed=0.3), "[feed]", "450 ipm", "p2.11")
    assert "m30" in post(job_for(lib, subs, "face", rpm=1500, feed=0.2))  # exactly 300 ipm is allowed


def test_feed_that_css_could_push_over_the_limit_is_a_warning(lib, subs):
    warns: list[str] = []
    post(job_for(lib, subs, "face", rpm=None, sfm=400, feed=0.1), warnings=warns)
    assert any("[feed]" in w and "CSS" in w for w in warns)


def test_feed_below_the_control_minimum_refused(lib, subs):
    refused(job_for(lib, subs, "face", feed=0.00001), "[feed]", "slowest feed", "p2.51")


def test_feed_that_rounds_to_f0_refused(lib, subs):
    m = MachineConfig.for_units("metric", {"feed_decimals": 1})
    refused(job_for(lib, subs, "face", "metric", feed=0.04), "[feed]", "F0", machine=m)


def test_peck_retract_feed_over_the_rapid_refused(lib, subs):
    refused(job_for(lib, subs, "drill", peck=0.1, peck_rapid=400.0), "[peck_rapid]", "fastest feed")


def test_css_floor_above_the_machine_top_speed_refused(lib, subs):
    job = job_for(lib, subs, "face", rpm=None, sfm=400, min_rpm=4500)
    refused(job, "[min_rpm]", "4000 rpm", "max_rpm in machine.toml")


def test_css_ceiling_over_the_machine_is_a_warning(lib, subs):
    warns: list[str] = []
    post(job_for(lib, subs, "face", rpm=None, sfm=400, max_rpm=6000), warnings=warns)
    assert any("[max_rpm]" in w for w in warns)


def test_tap_feed_per_minute_limit(lib, subs):
    refused(job_for(lib, subs, "tap", rpm=4000, pitch=0.1), "[rpm]", "limit")


def test_drill_depth_must_be_below_the_automatic_start(lib, subs):
    """Start Z blank = z_clear from machine.toml (.05 in)."""
    refused(job_for(lib, subs, "drill", z=0.05), "[z]", "z_clear in machine.toml")


def test_machine_layer_uses_the_machine_given(lib, subs):
    """Same job, two machines: the machine-dependent rule follows machine.toml.
    A drill to Z.03 with the start blank is fine when the start is Z.05, and
    refused when machine.toml puts the start at Z.02."""
    job = job_for(lib, subs, "drill", z=0.03)
    assert "m30" in post(job)
    refused(job, "[z]", "z_clear", machine=MachineConfig(z_clear=0.02))


def test_machine_profile_for_the_other_units_refused(lib, subs):
    """post() used to rebuild a default profile silently, keeping only the
    name and max rpm."""
    refused(job_for(lib, subs, "face", "metric"), "loaded for inch", machine=MachineConfig())


# --------------------------------------------------------------------- geometry (Profile.issues)
def _shape_problems(prof):
    return [str(i) for i in prof.issues()]


@pytest.mark.parametrize(
    "build, words",
    [
        (lambda p: p.line(x=0.5).line(x=0.5), ["segment 2", "goes nowhere"]),
        (lambda p: p.line(x=0.5, c=0.3).line(z=-0.5), ["segment 1", "too short for its corners"]),
        # Two corners sharing a .1-long line: .06 + .06 doesn't fit.
        # Two corners sharing segment 3, X.5 to X.7: X is a diameter, so it
        # is .1 long, and .06 + .06 doesn't fit.
        (
            lambda p: p.line(x=0.5).line(z=-0.1, c=0.06).line(x=0.7, c=0.06).line(z=-0.5),
            ["segment 3", "too short", "X is a diameter"],
        ),
        (lambda p: p.line(x=0.5).line(z=-0.1, r=0.5).line(x=0.7), ["segment 2", "too short"]),
        (lambda p: p.line(x=0.5).line(x=0.7, c=0.02).line(x=0.9), ["corner C", "straight line"]),
        (lambda p: p.line(x=0.5, r=0.02).line(x=0.2), ["corner R", "doubles back"]),
        (lambda p: p.line(x=0.5, c=-0.02).line(z=-0.5), ["corner C", "greater than zero"]),
        (lambda p: p.line(x=math.nan).line(z=-0.5), ["segment 1 x", "finite"]),
        (lambda p: p.line(x=0.5).arc(0.7, -0.1, 0.0, "cw"), ["arc radius", "greater than zero"]),
        (lambda p: p.line(x=0.5).arc(0.7, -0.1, 0.1, "cc"), ["arc direction", "cw or ccw"]),
        (lambda p: p.line(x=0.5).arc(0.9, -0.5, 0.05, "cw"), ["segment 2", "too small for chord"]),
        (lambda p: p.line(x=0.5, c=0.02).arc(0.7, -0.1, 0.1, "cw"), ["only blend two straight lines", "p2.14"]),
    ],
)
def test_impossible_geometry_refused(build, words):
    """Each of these used to post, or crash with ZeroDivisionError in the
    corner maths instead of saying what was wrong."""
    msgs = " | ".join(_shape_problems(build(Profile(Point(0, 0), name="od"))))
    for w in words:
        assert w in msgs, msgs


def test_corners_that_just_fit_are_accepted():
    """A chamfer exactly as long as its line, and the manual's p2.44 shape."""
    # Segment 3 runs X.5 to X.7: .1 long (X is a diameter), exactly two .05 chamfers.
    p = Profile(Point(0, 0), name="od").line(x=0.5).line(z=-0.1, c=0.05).line(x=0.7, c=0.05).line(z=-0.5)
    assert _shape_problems(p) == []
    zero_means_none = Profile(Point(0, 0), name="od").line(x=0.5, c=0).line(z=-0.5)
    assert _shape_problems(zero_means_none) == []


def test_bad_shape_stops_the_post(lib, subs):
    job = job_for(lib, subs, "rough")
    job.profiles["od"].segments.append(job.profiles["od"].segments[-1])  # a zero-length repeat
    refused(job, "shape 'od'", "goes nowhere")


# --------------------------------------------------------------------- job-level checks and job files
def test_misspelt_units_refused_not_posted_as_inch(lib, subs):
    """`units == "metric"` checks everywhere made "Metric" mean inch."""
    job = job_for(lib, subs, "face", "metric")
    job.units = "Metric"
    refused(job, "Units [units]", "inch or metric")


@pytest.mark.parametrize(
    "stock, words",
    [
        (Stock(0.0), ["diameter"]),
        (Stock(math.nan), ["diameter"]),
        (Stock(1.0, bore=1.0), ["bore", "smaller than the bar"]),
        (Stock(1.0, bore=-0.1), ["bore"]),
        (Stock(1.0, length=0.0), ["length"]),
    ],
)
def test_impossible_stock_refused(lib, subs, stock, words):
    job = job_for(lib, subs, "face")
    job.stock = stock
    refused(job, *words)


@pytest.mark.parametrize("d", [0, 33, -1])
def test_station_d_number_out_of_range(lib, subs, d):
    """Secondary offsets are D1-D32 (p5.16); D0 switches correction off (p2.62)."""
    job = job_for(lib, subs, "face")
    job.setup.stations[1].d = d
    refused(job, "station t1", "D number", "1-32")


def _job_file(tmp_path, op_lines, station='t = 1\ntool = "lh-turn-35"', extra=""):
    path = tmp_path / "j.toml"
    path.write_text(
        f'[job]\nname = "J"\nunits = "inch"\n[stock]\ndiameter = 1.0\n{extra}\n'
        f"[[station]]\n{station}\n[[op]]\n{op_lines}\n"
    )
    return path


@pytest.mark.parametrize(
    "op_lines, station, words",
    [
        ('type = "face"\nt = 1\nrpm = 1500\nfeeed = 0.003', None, ["feeed", "did you mean 'feed'"]),
        ('type = "drill"\nt = 1\nrpm = 1500', None, ["operation 1 (drill)", "missing z"]),
        ("t = 1", None, ["no type"]),
        ('type = "face"\nt = 1\nrpm = 1500', 't = 1.5\ntool = "lh-turn-35"', ["t is 1.5", "whole number"]),
        ('type = "face"\nt = 1\nrpm = 1500', 't = 1\ntool = "lh-turn-35"\nd = "1"', ["d is '1'"]),
    ],
)
def test_job_file_problems_are_clear_value_errors(lib, tmp_path, op_lines, station, words):
    """These used to escape as TypeError ('unexpected keyword argument') or
    were quietly converted (int(1.5) is 1)."""
    path = _job_file(tmp_path, op_lines, station or 't = 1\ntool = "lh-turn-35"')
    with pytest.raises(ValueError) as e:
        load_job(path, lib)
    for w in words:
        assert w in str(e.value)


def test_quoted_stock_diameter_in_a_job_file_refused(lib, tmp_path):
    path = _job_file(tmp_path, 'type = "face"\nt = 1\nrpm = 1500')
    path.write_text(path.read_text().replace("diameter = 1.0", 'diameter = "1.0"'))
    with pytest.raises(ValidationError, match="Bar stock diameter"):
        load_job(path, lib)


def test_job_file_that_is_not_toml_names_the_file(lib, tmp_path):
    path = tmp_path / "broken.toml"
    path.write_text("[job\nname = 'x'\n")
    with pytest.raises(ValueError, match="broken.toml"):
        load_job(path, lib)


def test_gui_loads_bad_job_without_validating_then_refuses_to_post(lib, tmp_path):
    """The GUI opens jobs with validate=False so the operator can fix them on
    screen; posting is where they're stopped."""
    path = _job_file(tmp_path, 'type = "face"\nt = 1\nrpm = -1500')
    job = load_job(path, lib, validate=False)
    refused(job, "[rpm]")


def test_shared_fields_have_metadata_for_validation():
    """quantity_issues looks up every field's kind; a shared field that lost
    its FieldInfo would be checked as plain text."""
    for name in ("rpm", "sfm", "min_rpm", "max_rpm", "feed", "retract_feed", "dwell", "t"):
        assert FIELDS[name].kind in ("rpm", "sfm", "feed", "sec", "station")
