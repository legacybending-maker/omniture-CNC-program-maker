"""Tests anchored to the worked examples in the OmniTurn operator manual."""

import math
from pathlib import Path

import pytest

from omniturn_cam.backplot import interpret
from omniturn_cam.geometry import Arc, Point, Profile
from omniturn_cam.job import Job, Stock, load_job
from omniturn_cam.lint import lint
from omniturn_cam.ops import AxialPass, Drill, Face, Finish, Rough
from omniturn_cam.post import MachineConfig, OmniTurnPost, PostError, post
from omniturn_cam.post.omniturn import fmt, sanitize_comment
from omniturn_cam.tools import Setup, Station, Tool, ToolLibrary

EX = Path(__file__).parent.parent / "examples"


@pytest.fixture
def lib():
    return ToolLibrary.load(EX / "tools.toml")


def make_job(lib, profiles=None, ops=None, units="inch", stock=0.75, bore=None):
    setup = Setup()
    setup.add(Station(1, lib["lh-turn-35"], d=1))
    setup.add(Station(2, lib["drill-250"]))
    setup.add(Station(3, lib["die-head-1-2-20"]))
    return Job("TEST", units, Stock(stock, bore=bore), setup, profiles or {}, ops or [])


# --------------------------------------------------------------------- format
@pytest.mark.parametrize(
    "v,s",
    [(0, "0"), (0.5, ".5"), (-0.5, "-.5"), (1.0, "1"), (0.34300, ".343"), (-0.0000001, "0"), (12.34567, "12.3457")],
)
def test_number_format(v, s):
    assert fmt(v, 4) == s


def test_comment_sanitized():
    assert sanitize_comment("Drill 1/4, .250 dia") == "DRILL 1 4 250 DIA"


# --------------------------------------------------------------------- manual examples
def test_manual_g75_contour_subroutine_matches_manual(lib):
    """p2.44: the contour in subroutine 1 must match the manual line for line."""
    job = load_job(EX / "manual_g75.toml", lib)
    code = post(job)
    sub = code.split("}1\n", 1)[1].split("m99", 1)[0].split()
    assert sub == ["x0z0", "x.343c.125", "z-.3", "x.406", "g02x.406z-.487r.0935", "x.375", "z-.75", "x.75"]
    assert "g75i.02u.005f.003p1" in code


def test_arc_direction_matches_manual_ik_examples(lib):
    """p2.18-2.19: convex face-to-OD corner is G02 I0 K-.15; the step-up arc is G03 I.1 K0."""
    p = Profile(Point(0.2, 0.0)).arc(0.5, -0.15, 0.15, "ccw")
    p2 = Profile(Point(0.2, -0.3)).arc(0.4, -0.4, 0.1, "cw")
    post_ = OmniTurnPost(make_job(lib), MachineConfig(arc_format="ik"))
    assert post_.arc_words(p.start, p.segments[0]) == "g02x.5z-.15i0k-.15"
    assert post_.arc_words(p2.start, p2.segments[0]) == "g03x.4z-.4i.1k0"


def test_header_annotations_inch_and_metric(lib):
    inch = post(make_job(lib, ops=[Drill(t=2, rpm=2000, z=-0.5)]))
    assert "(UNITS INCH G70)" in inch and "\ng70\n" in inch and "f300" in inch
    metric = post(make_job(lib, ops=[Drill(t=2, rpm=2000, z=-12.0)], units="metric", stock=20))
    assert "(UNITS METRIC MM G71)" in metric and "\ng71\n" in metric and "f7620" in metric


def test_tool_call_followed_by_x_and_z(lib):
    code = post(make_job(lib, ops=[Drill(t=2, rpm=2000, z=-0.5, peck=0.1)]))
    lines = code.splitlines()
    i = next(n for n, l in enumerate(lines) if l.startswith("t2"))
    assert "x" in lines[i + 1] and "z" in lines[i + 1]
    assert "g83z-.5k.1f.003r.05c.02l300" in code


def test_finish_uses_comp_and_d_and_leads_off(lib):
    prof = Profile(Point(0, 0), name="od").line(x=0.5).line(z=-0.5).line(x=0.75)
    code = post(make_job(lib, {"od": prof}, [Finish(t=1, rpm=3000, profile="od")]))
    assert "\ng41\n" in code and "d1" in code and "\ng40\n" in code
    assert code.index("g41") < code.index("d1") < code.rindex("g40")
    assert "x.8436" in code  # lead-off > 2x nose radius before G40


def test_quadrant_rule_rejected(lib):
    # contour ends above the cycle start in X while starting below it -> invalid
    prof = Profile(Point(0, 0), name="od").line(x=0.5).line(z=-0.5).line(x=1.0)
    job = make_job(lib, {"od": prof}, [Rough(t=1, rpm=2000, profile="od", start_x=0.75, start_z=0.05)])
    with pytest.raises(PostError, match="quadrant"):
        post(job)


def test_die_head_synced_axial_pass(lib):
    op = AxialPass(t=3, rpm=300, x=0, z_start=0.1, z_end=-0.5, feed=0.05, sync=True)
    code = post(make_job(lib, ops=[op]))
    assert "g95f.05z-.5p" in code


def test_job_name_limited_to_8_chars(lib):
    with pytest.raises(ValueError, match="8"):
        Job("TOOLONGNAME", "inch", Stock(1), Setup()).validate()


# --------------------------------------------------------------------- lint
def test_lint_catches_rule_breaks():
    bad = "n10g90g72\ng2x1z1\nm03m08\nt1\nz.1\nx1,z2\nt40\nx0z0\ng75i.1u.01f.003p7\n"
    msgs = " | ".join(p.message for p in lint(bad))
    for frag in ["N sequence", "two digits", "one M code", "both X and Z", "commas", "out of range", "never defined"]:
        assert frag in msgs, frag


def test_generated_example_is_lint_clean(lib):
    code = post(load_job(EX / "manual_g75.toml", lib))
    assert [p for p in lint(code, "MANG75.cnc")] == []


# --------------------------------------------------------------------- backplot
def test_backplot_reads_back_arc_and_contour(lib):
    code = post(load_job(EX / "manual_g75.toml", lib))
    paths = interpret(code)
    kinds = {p.kind for p in paths}
    assert {"rapid", "feed", "contour", "drill"} <= kinds
    contour = next(p for p in paths if p.kind == "contour")
    assert any(isinstance(s, Arc) and s.direction == "ccw" for s in contour.segments)


# --------------------------------------------------------------------- phase 2b: taps, dies, G74, machine
from omniturn_cam.fields import new_operation  # noqa: E402
from omniturn_cam.ops import BoxRough, Tap  # noqa: E402


def _job_with(lib, *pairs, ops=()):
    setup = Setup()
    for t, tool_id in pairs:
        setup.add(Station(t, lib[tool_id]))
    return Job("TEST", "inch", Stock(0.75), setup, {}, list(ops))


def test_tap_follows_manual_floating_holder_method(lib):
    """p2.58: feed in at ~95% of pitch, reverse, dwell, feed out, restore direction."""
    op = new_operation("tap", 4, lib["tap-1-4-20"], rpm=1000, z_start=0.1, z_end=-0.4)
    code = post(_job_with(lib, (4, "tap-1-4-20"), ops=[op]))
    seq = code.split("x0z1\n", 1)[1].split("\n")
    assert seq[:7] == ["m03s1000", "m08", "z.1", "g95f.0475z-.4", "m04", "g04f.5", "g95f.0475z.1"]
    assert seq[7] == "m03"


def test_acorn_die_programmed_like_a_tap(lib):
    op = new_operation("tap", 6, lib["acorn-die-3-8-24"], rpm=300, x=0, z_start=0.1, z_end=-0.3)
    assert isinstance(op, Tap) and abs(op.pitch - 1 / 24) < 1e-5


def test_releasing_die_head_dwells_retracts_and_stops_for_reset(lib):
    op = new_operation("axial", 3, lib["die-head-1-2-20"], rpm=300, z_start=0.1, z_end=-0.5)
    assert op.stop_after == "m01" and op.sync and op.dwell == 0.2
    code = post(_job_with(lib, (3, "die-head-1-2-20"), ops=[op]))
    tail = code.split("g95f.05z-.5p\n", 1)[1].split("\n")
    # Stage 5: m09 after the m05, so coolant isn't running while the operator
    # resets the head (M01 leaves M functions on, p2.53).
    assert tail[:6] == ["g04f.2", "g00z.1", "z1", "m05", "m09", "m01"]


def test_g74_box_rough_always_carries_u(lib):
    op = BoxRough(t=1, rpm=2000, x=0.395, z=-0.74, depth=0.02, start_x=0.6, start_z=-0.497)
    code = post(_job_with(lib, (1, "lh-turn-35"), ops=[op]))
    assert "g74x.395z-.74i.02u.01f.003" in code


def test_machine_name_in_header_and_rpm_warning(lib, tmp_path):
    (tmp_path / "m.toml").write_text('name = "Hardinge HLV-H OmniTurn G4"\nmax_rpm = 4000\n[inch]\nmax_bar = 1.0625\n')
    m = MachineConfig.load(tmp_path / "m.toml", "inch")
    warns = []
    code = post(_job_with(lib, (2, "drill-250"), ops=[Drill(t=2, rpm=4500, z=-0.3)]), m, warnings=warns)
    assert "(HARDINGE HLV H OMNITURN G4)" in code
    assert any("4500" in w for w in warns)


def test_default_machine_is_hlvh_on_vfd():
    m = MachineConfig()
    assert m.max_rpm == 4000 and m.x_travel == 10 and m.z_travel == 9


def test_travel_warning(lib):
    warns = []
    post(_job_with(lib, (2, "drill-250"), ops=[Drill(t=2, rpm=2000, z=-9.5)]), warnings=warns)
    assert any("Z travel" in w for w in warns)


# --------------------------------------------------------------------- G33 threading (manual p2.23-2.29)
from omniturn_cam.ops import Thread  # noqa: E402


def _thread_job(lib, op):
    lib.add(Tool(id="thd", name="OD thread", kind="thread")) if "thd" not in [t.id for t in lib] else None
    return _job_with(lib, (4, "thd"), ops=[op])


def test_g33_matches_manual_half_13_example(lib):
    """p2.25: 1/2-13, major .495, minor .408, I.02 -> start X.475 at Z.2, G33X.408Z-.5I.02K.077P."""
    op = Thread(t=4, rpm=500, major=0.495, minor=0.408, z_end=-0.5, lead=0.077, first_depth=0.02, pullout=True)
    code = post(_thread_job(lib, op))
    lines = code.splitlines()
    i = lines.index("g33x.408z-.5i.02k.077p")
    assert lines[i - 1] == "x.475" and "z.2" in lines[i - 2]
    assert "(THREAD ABOUT 19 PASSES)" in code  # (.087/.02)^2 = 18.9


def test_g33_multi_start_offsets_z_by_pitch(lib):
    """p2.28: three-start 20 pitch: K.15, starts at Z.2, .25, .3."""
    op = Thread(
        t=4,
        rpm=500,
        major=0.495,
        minor=0.424,
        z_end=-0.5,
        lead=0.05,
        starts=3,
        first_depth=0.01,
        pullout=True,
        start_x=0.494,
    )
    code = post(_thread_job(lib, op))
    assert code.count("g33x.424z-.5i.01k.15p") == 3
    assert "z.25\n" in code and "z.3\n" in code


def test_g33_spring_pass_angle_infeed_and_lint(lib):
    op = Thread(
        t=4,
        rpm=500,
        major=0.5,
        minor=0.424,
        z_end=-0.5,
        lead=0.05,
        first_depth=0.008,
        pullout=True,
        infeed_angle=29,
        spring_passes=1,
    )
    code = post(_thread_job(lib, op))
    assert "g33x.424z-.5i.008k.05pc29\ng33x.424z-.5i.008k.05pc29o" in code
    assert not [p for p in lint(code) if p.severity == "error"]
    op.infeed_angle = 27
    assert "k.05pc27" in post(_thread_job(lib, op))


def test_g33_feed_limit_and_fixed_rpm(lib):
    op = Thread(t=4, rpm=3000, major=1.0, minor=0.8, z_end=-0.5, lead=0.125, first_depth=0.02)
    with pytest.raises(PostError, match="300"):
        post(_thread_job(lib, op))  # 3000 x .125 = 375 ipm
    op.rpm, op.sfm = None, 300
    with pytest.raises(PostError, match="fixed RPM"):
        post(_thread_job(lib, op))


@pytest.mark.parametrize(
    "side, start_x, ok",
    [
        ("od", 0.475, True),  # between major .495 and minor .408: cuts down to the minor
        ("od", 0.408, False),  # at the finished X
        ("od", 0.30, False),  # inside it
        ("id", 0.420, True),  # between minor .408 and major .495: cuts out to the major
        ("id", 0.495, False),  # at the finished X
        ("id", 0.60, False),  # outside it
    ],
)
def test_g33_refuses_a_start_already_past_the_finished_x(lib, side, start_x, ok):
    """OD finishes at the minor, ID at the major; a start X on or past it leaves
    G33 nothing to cut (or cuts the wrong way), so the post refuses."""
    op = Thread(
        t=4, rpm=400, side=side, major=0.495, minor=0.408, z_end=-0.5, lead=0.077, first_depth=0.02, start_x=start_x
    )
    if ok:
        assert "g33" in post(_thread_job(lib, op))
    else:
        with pytest.raises(PostError, match="already past the finished X"):
            post(_thread_job(lib, op))


def test_g33_internal_thread_starts_inside(lib):
    op = Thread(
        t=4, rpm=400, side="id", major=0.9375, minor=0.8783, z_end=-0.75, lead=0.05, first_depth=0.01, infeed_angle=29
    )
    code = post(_thread_job(lib, op))
    assert "g33x.9375z-.75i.01k.05c29" in code and "\nx.8883\n" in code


# --------------------------------------------------------------------- external subroutines, M98, raw code
from omniturn_cam.ops import CallSub, RawCode  # noqa: E402


def test_include_file_appended_and_contour_renumbered(lib, tmp_path):
    ext = tmp_path / "extsub.cnc"
    ext.write_text("}1\nm10\nm99\n}2\nm11\nm99\n")
    job = load_job(EX / "manual_g75.toml", lib)
    job.include_subs = str(ext)
    job.operations.append(CallSub(sub=1, comment="Load bar"))
    code = post(job)
    assert "g75i.02u.005f.003p3" in code and "\n}3\n" in code  # moved past 1 and 2
    assert code.rstrip().endswith("}2\nm11\nm99")
    assert "m98p1" in code
    assert not [p for p in lint(code) if p.severity == "error"]


def test_lint_flags_duplicate_subs_and_m95_without_sub1():
    msgs = " | ".join(p.message for p in lint("m95\nm30\n}2\nm99\n}2\nm99\n"))
    assert "defined twice" in msgs and "subroutine 1 is called" in msgs


def test_raw_code_passes_through_and_parks_tool_first(lib):
    op = RawCode(code="m91\nm10\n")
    code = post(_raw_job(lib, op))
    i = code.index("m91")
    assert code[:i].rstrip().endswith("z1") and "m10" in code
    # the drill after the raw code is called again with both X and Z
    assert code.count("t2(") == 2


def _raw_job(lib, op):
    return _job_with(lib, (2, "drill-250"), ops=[Drill(t=2, rpm=2000, z=-0.1), op, Drill(t=2, rpm=2000, z=-0.2)])


def test_thread_locks_overrides(lib):
    op = Thread(t=4, rpm=500, major=0.495, minor=0.408, z_end=-0.5, lead=0.077, first_depth=0.02)
    code = post(_thread_job(lib, op))
    assert code.index("m84") < code.index("g33") < code.index("m85")


def test_every_operation_round_trips_through_a_saved_job(lib, tmp_path):
    from omniturn_cam.job import save_job
    from omniturn_cam.ops import OPERATION_TYPES

    job = load_job(EX / "manual_g75.toml", lib)
    job.operations = [new_operation(k, 1) for k in OPERATION_TYPES]
    save_job(job, tmp_path / "all.toml")
    again = load_job(tmp_path / "all.toml", lib, validate=False)
    assert [type(o) for o in again.operations] == [type(o) for o in job.operations]


def test_rough_warns_on_shape_that_turns_back_toward_face(lib):
    # cup: OD sleeve, then back up toward the face inside it (slot cut from the back)
    prof = (
        Profile(Point(0, 0), name="od")
        .line(x=0.65)
        .line(z=-0.84)
        .line(x=0.545)
        .line(z=-0.04)
        .line(x=0.415)
        .line(z=-0.915)
        .line(x=0.75)
    )
    assert prof.undercut_z() == -0.84
    warns = []
    post(make_job(lib, {"od": prof}, [Rough(t=1, rpm=2000, profile="od", start_x=0.75, start_z=0.05)]), warnings=warns)
    assert any("turns back" in w for w in warns)


# --------------------------------------------------------------------- machine state across raw code and M98 (#27)
# The post hands control to code it didn't write in two operations: raw program
# lines and M98 subroutine calls. These tests pin the contract in
# docs/omniturn-post-rules.md ("Handing control to code the post didn't write"):
# afterwards every mode an operation needs is either known or written again, and
# the slide is never moved from a position the post can't vouch for.
#
# Most of them check the program the way the control reads it, not the post's
# internal state: _modes() replays the modal G and M words of the program up to
# a line, so "the drill ran in G95" is checked against the actual output.
import re  # noqa: E402

from omniturn_cam.post.external import EVERYTHING, analyse, subroutine_bodies  # noqa: E402
from omniturn_cam.post.state import POSITION, UNKNOWN, MachineState  # noqa: E402

MODAL_GROUPS = {
    "coords": {"g90", "g91"},
    "diameter": {"g72", "g73"},
    "units": {"g70", "g71"},
    "feed_mode": {"g94", "g95"},
    "spindle_mode": {"g96", "g97"},
    "comp": {"g40", "g41", "g42"},
    "spindle": {"m03", "m04", "m05"},
    "coolant": {"m08", "m09"},
}


def _main(code: str) -> list[str]:
    """The main program's lines (everything before M30; subroutines follow it)."""
    lines = code.splitlines()
    return lines[: lines.index("m30")]


def _modes(lines: list[str]) -> dict[str, str | None]:
    """The last word from each modal group in ``lines``: the mode the control is
    in after running them. None = never set in these lines."""
    seen: dict[str, str | None] = dict.fromkeys(MODAL_GROUPS)
    for line in lines:
        for word in re.findall(r"[gm]\d\d", re.sub(r"\([^)]*\)", "", line)):
            for group, words in MODAL_GROUPS.items():
                if word in words:
                    seen[group] = word
    return seen


def _at(lines: list[str], start: str, after: int = 0) -> int:
    """Index of the first line starting with ``start`` at or after index ``after``."""
    return next(i for i, line in enumerate(lines) if i >= after and line.startswith(start))


def _hjob(lib, *ops, units="inch", include=None):
    """A job with a turning tool (t1, D1), a drill (t2) and a cutoff blade (t8)."""
    setup = Setup()
    setup.add(Station(1, lib["lh-turn-35"], d=1))
    setup.add(Station(2, lib["drill-250"]))
    setup.add(Station(8, lib["cutoff-062"]))
    job = Job("TEST", units, Stock(20.0 if units == "metric" else 0.75), setup, {}, list(ops))
    job.include_subs = include
    return job


def _drill(z, **kw):
    return Drill(t=2, rpm=2000, z=z, **kw)


def _subs(tmp_path, text):
    path = tmp_path / "subs.txt"
    path.write_text(text)
    return str(path)


# ---- feed mode (defect A: G94 survived raw code, so a G81 F.003 ran as .003 inches per MINUTE)
@pytest.mark.parametrize("raw", ["g94f100", "g95", "m91"])
def test_drill_after_raw_code_runs_in_g95(lib, raw):
    lines = _main(post(_hjob(lib, _drill(-0.1), RawCode(code=raw), _drill(-0.2))))
    i = _at(lines, "g81z-.2")
    assert _modes(lines[:i])["feed_mode"] == "g95"


def test_drill_after_raw_code_without_feed_mode_change_adds_nothing(lib):
    """M91 can't change the feed mode, so the post still knows G95: no redundant line."""
    lines = _main(post(_hjob(lib, _drill(-0.1), RawCode(code="m91"), _drill(-0.2))))
    assert "g95" not in lines[_at(lines, "m91") : _at(lines, "g81z-.2")]


# ---- coolant (defect B: after a raw M09 the post still believed coolant was on)
@pytest.mark.parametrize(
    "raw, coolant_after, want",
    [("m09", True, "m08"), ("m08", True, "m08"), ("m09", False, "m09")],
)
def test_coolant_is_what_the_next_operation_asks_for(lib, raw, coolant_after, want):
    lines = _main(post(_hjob(lib, _drill(-0.1), RawCode(code=raw), _drill(-0.2, coolant=coolant_after))))
    i = _at(lines, "g81z-.2")
    assert _modes(lines[:i])["coolant"] == want
    # and it's written after the raw code, not just left over from before it
    assert want in lines[_at(lines, raw) + 1 : i]


def test_coolant_unknown_at_program_start(lib):
    """The last program may have ended with M02 (coolant still on), so a dry first
    operation writes M09 rather than assuming coolant is off."""
    lines = _main(post(_hjob(lib, _drill(-0.1, coolant=False))))
    assert _modes(lines[: _at(lines, "g81")])["coolant"] == "m09"


# ---- spindle
@pytest.mark.parametrize("raw", ["m04", "m05", "g96s300"])
def test_spindle_direction_speed_and_mode_restored_after_raw_code(lib, raw):
    lines = _main(post(_hjob(lib, _drill(-0.1), RawCode(code=raw), _drill(-0.2))))
    i = _at(lines, "g81z-.2")
    modes = _modes(lines[:i])
    assert modes["spindle"] == "m03" and modes["spindle_mode"] == "g97"
    after_raw = lines[_at(lines, raw) + 1 : i]
    # The S word comes after the G97: in G96 "s2000" would be 2000 surface feet a minute.
    assert any(line.endswith("s2000") for line in after_raw)
    if raw.startswith("g96"):
        assert after_raw.index("g97") < next(n for n, line in enumerate(after_raw) if line.endswith("s2000"))


def test_css_operation_starts_from_a_known_g97_after_raw_g96(lib):
    """A CSS face after raw G96: G97, then the approach rpm, then G96 with its own speed."""
    face = Face(t=1, sfm=400, max_rpm=3500, z=0.0)
    lines = _main(post(_hjob(lib, _drill(-0.1), RawCode(code="g96s300"), face)))
    after = lines[_at(lines, "g96s300") + 1 :]
    assert (
        after.index("g97") < next(n for n, line in enumerate(after) if line.startswith("m03s")) < after.index("g96s400")
    )


# ---- modal conditions that give X and Z their meaning
@pytest.mark.parametrize(
    "units, raw, group, want",
    [
        ("inch", "g91", "coords", "g90"),
        ("inch", "g73", "diameter", "g72"),
        ("inch", "g71", "units", "g70"),
        ("metric", "g70", "units", "g71"),
        ("inch", "g41", "comp", "g40"),
        ("inch", "g42", "comp", "g40"),
    ],
)
def test_modes_restored_before_the_next_tool_call(lib, units, raw, group, want):
    """The line after a tool call must be the X/Z move (p2.9), so G90/G72/units/G40
    are written before the tool call, never between it and its move."""
    drill = _drill(-2.5 if units == "metric" else -0.1)
    lines = _main(post(_hjob(lib, drill, RawCode(code=raw), Face(t=1, rpm=1500, z=0.0), units=units)))
    t = _at(lines, "t1(", _at(lines, raw))
    assert _modes(lines[:t])[group] == want
    assert "x" in lines[t + 1] and "z" in lines[t + 1]


def test_every_mode_known_at_the_first_cut_after_unreadable_code(lib):
    """After a subroutine the post can't read, every mode is written again before cutting."""
    sub = CallSub(sub=7, ends_at="tool_change_z")
    lines = _main(post(_hjob(lib, _drill(-0.1), sub, Face(t=1, rpm=1500, z=0.0)), check=False))  # P7: not here
    cut = _at(lines, "g95f", _at(lines, "m98p7"))
    after = _modes(lines[_at(lines, "m98p7") + 1 : cut + 1])
    assert after == {
        "coords": "g90",
        "diameter": "g72",
        "units": "g70",
        "feed_mode": "g95",
        "spindle_mode": "g97",
        "comp": "g40",
        "spindle": "m03",
        "coolant": "m08",
    }


def test_nothing_restated_after_code_that_changes_nothing_tracked(lib):
    """Loader waits and PLC outputs leave G modes alone: no restore lines."""
    lines = _main(post(_hjob(lib, _drill(-0.1), RawCode(code="m91\nm32"), Face(t=1, rpm=1500, z=0.0))))
    between = lines[_at(lines, "m32") + 1 : _at(lines, "t1(")]
    assert between == ["g00"]


# ---- position (defect C: a two-axis rapid from wherever a subroutine left the slide)
def test_unreadable_subroutine_refuses_the_next_move(lib):
    """The post can't see subroutine 7, so it can't know where the slide is."""
    job = _hjob(lib, _drill(-0.1), CallSub(sub=7), Face(t=1, rpm=1500, z=0.0))
    with pytest.raises(
        PostError,
        match=r"operation 3 \(face t1\): can't call t1: .* after operation 2 \(call sub P7\): subroutine 7 isn't in",
    ):
        post(job, check=False)  # check=False: lint would also stop it (P7 undefined); the post must stop first


def test_subroutine_declared_to_end_at_tool_change_z(lib):
    """Declared: the next tool call goes from there, with no move before it, and the
    operator is warned that the post relies on the declaration."""
    job = _hjob(lib, _drill(-0.1), CallSub(sub=7, ends_at="tool_change_z"), Face(t=1, rpm=1500, z=0.0))
    warns = []
    lines = _main(post(job, check=False, warnings=warns))
    between = lines[_at(lines, "m98p7") + 1 : _at(lines, "t1(")]
    assert not [line for line in between if "x" in line or "z" in line]
    assert any("call sub P7" in w and "declaration" in w for w in warns)


def test_readable_subroutine_that_doesnt_move_needs_no_declaration(lib, tmp_path):
    inc = _subs(tmp_path, "}1\nm10\ng04f.5\nm11\nm99\n")
    warns = []
    code = post(_hjob(lib, _drill(-0.1), CallSub(sub=1), Face(t=1, rpm=1500, z=0.0), include=inc), warnings=warns)
    assert "m98p1" in code and not warns


def test_readable_subroutine_that_moves_is_refused(lib, tmp_path):
    inc = _subs(tmp_path, "}1\nm13\ng00z-.5\nm12\nz1\nm99\n")
    job = _hjob(lib, _drill(-0.1), CallSub(sub=1), Face(t=1, rpm=1500, z=0.0), include=inc)
    with pytest.raises(PostError, match=r"line 'g00z-.5' moves the slide \(in subroutine 1\)"):
        post(job)
    job.operations[1].ends_at = "tool_change_z"
    assert "m98p1" in post(job)


def test_subroutine_calling_another_is_read_through(lib, tmp_path):
    inc = _subs(tmp_path, "}1\nm98p2\nm99\n}2\nx0\nm99\n")
    job = _hjob(lib, _drill(-0.1), CallSub(sub=1), Face(t=1, rpm=1500, z=0.0), include=inc)
    with pytest.raises(PostError, match=r"line 'x0' moves the slide \(in subroutine 2\)"):
        post(job)


def test_raw_code_that_moves_x_and_z_is_refused_unless_declared(lib):
    raw = RawCode(code="g00x.5z.2\nz1")
    job = _hjob(lib, _drill(-0.1), raw, Face(t=1, rpm=1500, z=0.0))
    with pytest.raises(PostError, match=r"line 'g00x.5z.2' moves the slide"):
        post(job)
    raw.ends_at = "tool_change_z"
    lines = _main(post(job))
    t = _at(lines, "t1(", _at(lines, "z1", _at(lines, "g00x.5z.2")))
    assert "x" in lines[t + 1] and "z" in lines[t + 1]


def test_raw_code_that_changes_coordinate_mode_and_moves(lib):
    """G91 and an incremental move: both the mode and the position are lost."""
    job = _hjob(lib, _drill(-0.1), RawCode(code="g91\nz-.1"), _drill(-0.2))
    with pytest.raises(PostError, match="doesn't know where the slide is"):
        post(job)


def test_no_automatic_retract_at_the_end_from_an_unknown_position(lib):
    """Raw code that moves, last in the job: the footer doesn't move the slide, and says so."""
    warns = []
    lines = post(_hjob(lib, _drill(-0.1), RawCode(code="x0z.5")), warnings=warns).splitlines()
    assert lines[_at(lines, "x0z.5") + 1 :][:3] == ["m09", "m05", "m30"]
    assert any("ends without returning" in w for w in warns)


def test_a_later_declaration_recovers_a_lost_position(lib):
    """Raw code that moves (undeclared), then a subroutine declared to end at the
    tool-change Z: the declaration is the last word about where the slide is."""
    ops = [_drill(-0.1), RawCode(code="x0z.5"), CallSub(sub=7, ends_at="tool_change_z"), _drill(-0.2)]
    assert "g81z-.2" in post(_hjob(lib, *ops), check=False)


def test_unknown_ends_at_value_is_refused(lib):
    with pytest.raises(PostError, match="tool_change_z"):
        post(_hjob(lib, RawCode(code="m91", ends_at="safe"), _drill(-0.1)))


def test_park_before_handoff_only_from_a_known_position(lib):
    """Two raw blocks in a row: the second doesn't try to park, because after the
    first (which moves) the post doesn't know where the slide is."""
    ops = [_drill(-0.1), RawCode(code="x0z.5"), RawCode(code="m91", ends_at="tool_change_z"), _drill(-0.2)]
    lines = _main(post(_hjob(lib, *ops)))
    assert lines[_at(lines, "x0z.5") + 1] == "m91"


def test_ends_at_round_trips_and_old_files_default_to_check(lib, tmp_path):
    from omniturn_cam.job import save_job

    job = load_job(Path(__file__).parent / "golden" / "jobs" / "bushing.toml", lib)
    assert {op.ends_at for op in job.operations if isinstance(op, CallSub | RawCode)} == {"check"}
    job.operations[0].ends_at = "tool_change_z"
    save_job(job, tmp_path / "j.toml")
    assert load_job(tmp_path / "j.toml", lib).operations[0].ends_at == "tool_change_z"


# ---- the state model and the code reader on their own
def test_unknown_cannot_be_used_as_true_or_false():
    """`if state.coolant:` was the root of defect B; with UNKNOWN it can't be written by mistake."""
    with pytest.raises(TypeError):
        bool(UNKNOWN)
    with pytest.raises(TypeError):
        if MachineState().coolant:
            pass


def test_new_state_knows_nothing_and_forget_rejects_typos():
    s = MachineState()
    assert len(s.unknown_fields()) == 14 and s.clear is False and not s.position_known
    s.x, s.z, s.clear = 1.0, 1.0, True
    s.forget(POSITION)
    assert s.x is UNKNOWN and s.clear is False
    with pytest.raises(KeyError):
        s.forget({"colant"})


@pytest.mark.parametrize(
    "code, forgets, moves",
    [
        ("m91\nm32\nm10\nm84", set(), False),  # loader waits / outputs: nothing tracked
        ("(LOAD)\n/m11", set(), False),  # comments and block skip
        ("g04f.5", {"feed"}, False),
        ("g94f100", {"feed_mode", "feed"}, False),
        ("g96s300", {"spindle_mode", "rpm"}, False),
        ("m09", {"coolant"}, False),
        ("m04", {"spindle", "rpm"}, False),
        ("g91", {"coords"}, False),
        ("ls3\nm91\nlf", set(), False),  # a loop repeats nothing new
        ("x1", POSITION, True),
        ("t3", {"tool"} | POSITION, True),
        ("g10", EVERYTHING, True),  # not one the post can account for
        ("m00", EVERYTHING, True),  # operator may jog during a stop
        ("m98p9", EVERYTHING, True),  # not readable
        ("}4", EVERYTHING, True),
        ("x1;", EVERYTHING, True),  # not plain words
    ],
)
def test_external_code_reader(code, forgets, moves):
    eff = analyse(code.splitlines())
    assert eff.forget >= forgets and eff.moves_slide == moves
    if forgets is not EVERYTHING and not moves:
        assert eff.forget == forgets


def test_external_reader_follows_m98_and_stops_on_recursion():
    subs = subroutine_bodies("}1\nm10\nm98p2\nm99\n}2\ng04f1\nm99\n}3\nm98p3\nm99\n")
    assert subs == {1: ["m10", "m98p2"], 2: ["g04f1"], 3: ["m98p3"]}
    assert analyse(["m98p1"], subs).forget == {"feed"}
    assert analyse(["m98p3"], subs).forget == EVERYTHING


# --------------------------------------------------------------------- issue #36: reading programs back
# The post reads every program it writes back through the backplot
# interpreter (check_travel), so a reader that crashes or misreads stops or
# corrupts real output. These pin the post-level half of issue #36; the
# interpreter on its own is tested in tests/test_backplot.py.
from omniturn_cam.backplot import CenteredArc  # noqa: E402
from omniturn_cam.geometry import corner_blend  # noqa: E402


def _ik(units="inch"):
    return MachineConfig.for_units(units, {"arc_format": "ik"})


def _arc_lines(code):
    """(main-program arc lines, subroutine arc lines)."""
    main, _, subs = code.partition("\nm30\n")
    pick = lambda part: [ln for ln in part.splitlines() if ln.startswith(("g02", "g03"))]  # noqa: E731
    return pick(main), pick(subs)


def test_ik_machine_posts_arcs_in_main_program_and_contour(lib):
    """Defect A: with arc_format = "ik" every post with an arc died with
    KeyError 'r' in the travel check. The manual's G75 job has arcs in both the
    finish pass (main program) and the contour subroutine."""
    job = load_job(EX / "manual_g75.toml", lib)
    code = post(job, _ik())
    main_arcs, sub_arcs = _arc_lines(code)
    assert main_arcs and sub_arcs
    assert all("i" in a and "k" in a and "r" not in a for a in main_arcs + sub_arcs)
    assert not [p for p in lint(code) if p.severity == "error"]


def test_ik_and_r_programs_read_back_to_the_same_path(lib):
    """The interpreter's I/K reading (centre = start + I/K, I radial, p2.19)
    must agree with the post's I/K writing: same points as the R program, to
    within one output step."""
    job = load_job(EX / "manual_g75.toml", lib)
    r_m, ik_m = MachineConfig.for_units("inch"), _ik()
    r_pts = [q for p in interpret(post(job, r_m), r_m.arc_codes, r_m.decimals) for q in p.points()]
    ik_pts = [q for p in interpret(post(job, ik_m), ik_m.arc_codes, ik_m.decimals) for q in p.points()]
    assert len(r_pts) == len(ik_pts)
    assert all(abs(a[0] - b[0]) <= 2e-4 and abs(a[1] - b[1]) <= 2e-4 for a, b in zip(r_pts, ik_pts, strict=True))


def test_ik_machine_posts_metric_arcs(lib):
    """The metric job with arcs posts under I/K too (3 decimals, mm)."""
    job = load_job(Path(__file__).parent / "golden" / "jobs" / "metric.toml", lib)
    code = post(job, _ik("metric"))
    assert any("i" in a and "k" in a for a in sum(_arc_lines(code), []))


def test_lint_runs_before_the_travel_check(lib, monkeypatch):
    """Defect E: lint (is the program well formed?) must refuse before the
    travel check reads the program. Before #36 the interpreter ran first, so a
    malformed program crashed it instead of getting lint's message."""
    import omniturn_cam.lint as lint_module

    calls = []
    monkeypatch.setattr(
        lint_module, "lint", lambda code, *a: calls.append("lint") or [lint_module.Problem(1, "error", "made up")]
    )
    monkeypatch.setattr(OmniTurnPost, "check_travel", lambda self, code: calls.append("travel"))
    with pytest.raises(PostError, match="made up"):
        post(load_job(EX / "manual_g75.toml", lib))
    assert calls == ["lint"]


def test_travel_check_still_runs_after_lint_passes(lib, monkeypatch):
    calls = []
    real = OmniTurnPost.check_travel
    monkeypatch.setattr(OmniTurnPost, "check_travel", lambda self, code: calls.append("travel") or real(self, code))
    post(load_job(EX / "manual_g75.toml", lib))
    assert calls == ["travel"]


def test_raw_code_the_backplot_cannot_follow_warns_travel_not_checked(lib):
    """Legal raw code that moves in G91 can't be drawn. The travel check is
    advisory, so the program is still made, and the operator is told the
    check didn't run instead of it silently passing."""
    raw = RawCode(code="g91\nz-.01\ng90", ends_at="tool_change_z")
    warns = []
    code = post(_hjob(lib, _drill(-0.1), raw, _drill(-0.2)), warnings=warns)
    assert "g91" in code
    assert any("travel not checked" in w and "G91" in w for w in warns), warns


def test_raw_code_with_an_impossible_arc_is_refused(lib):
    """An arc that can't exist isn't "can't follow", it's wrong: refused."""
    raw = RawCode(code="g01x.5z-.1\ng02x.9z-.5r.05", ends_at="tool_change_z")
    with pytest.raises(PostError, match="can't be read back.*can't reach"):
        post(_hjob(lib, _drill(-0.1), raw, _drill(-0.2)))


# ---- subroutine declarations (defect C)
@pytest.mark.parametrize("header", ["}1 (LOADER)", "}01", "/}1"])
def test_include_file_subroutine_header_forms_are_seen(lib, tmp_path, header):
    """`}1 (LOADER)` used to be missed by job.external_subs(), so the post
    could number its own contour 1 and the program defined subroutine 1
    twice. Every reader now uses gcode.subroutine_start."""
    job = load_job(EX / "manual_g75.toml", lib)
    job.include_subs = _subs(tmp_path, f"{header}\nm10\nm99\n")
    assert job.external_subs()[1] == [1]
    code = post(job)
    assert "g75i.02u.005f.003p2" in code and "\n}2\n" in code  # numbered past 1
    assert not [p for p in lint(code) if p.severity == "error"]


def test_duplicate_subroutine_in_the_include_file_is_refused_naming_the_file(lib, tmp_path):
    job = load_job(EX / "manual_g75.toml", lib)
    job.include_subs = _subs(tmp_path, "}1\nm10\nm99\n}01 (AGAIN)\nm11\nm99\n")
    with pytest.raises(PostError, match=r"subroutine 1 is defined twice in .*subs\.txt"):
        post(job)


def test_shared_reader_normalises_numbers():
    from omniturn_cam.gcode import code_part, subroutine_numbers, subroutine_start

    assert [subroutine_start(code_part(s)) for s in ("}1", "}01 (X)", " } 7 ", "/}3", "}1a", "}", "m99")] == [
        1,
        1,
        7,
        3,
        None,
        None,
        None,
    ]
    assert list(subroutine_numbers("}2\nm99\n(}9 in a comment)\n}02\nm99\n")) == [2, 2]


# ---- lint (defects C and section 8 of #36)
def _lint_msgs(program):
    return [(p.severity, p.message) for p in lint(program)]


def test_lint_matches_leading_zero_subroutines_to_their_calls_and_warns():
    msgs = _lint_msgs("m98p1\nm30\n}01\nm10\nm99\n")
    assert not [m for s, m in msgs if s == "error"], msgs
    assert any(s == "warning" and "leading zero" in m for s, m in msgs)
    msgs = _lint_msgs("m98p01\nm30\n}1\nm10\nm99\n")
    assert not [m for s, m in msgs if s == "error"] and any("p1, not p01" in m for s, m in msgs)


def test_lint_reports_duplicate_written_two_ways():
    msgs = _lint_msgs("m30\n}1 (LOADER)\nm10\nm99\n}01\nm11\nm99\n")
    assert ("error", "subroutine 1 is defined twice - check pasted / included subroutine files") in msgs


@pytest.mark.parametrize(
    "program, severity, words",
    [
        ("m30\n}1\nm10\n", "error", "never ends"),
        ("m30\n}1\nm10\n}2\nm11\nm99\n", "error", "starts before the subroutine on line 2 ends"),
        ("g00x1z0\nm99\nm30\n", "warning", "no subroutine open"),
        ("g01x1z0°\n", "error", "isn't ASCII"),
        ("g01x1;z0\n", "error", "isn't part of any OmniTurn word"),
        ("g01x1.2.3z0\n", "error", "can't read '1.2.3'"),
        ("g01x-z0\n", "error", "can't read '-'"),
        ("g01xz0\n", "error", "X has no number"),
        ("g95f-.003\n", "error", "can't be negative"),
        ("m03s-1000\n", "error", "can't be negative"),
    ],
)
def test_lint_refuses_what_the_readers_would_skip(program, severity, words):
    """The interpreter and the handoff reader skip anything that isn't a
    letter-number word; lint must refuse it so it never reaches the machine."""
    msgs = _lint_msgs(program)
    assert any(s == severity and words in m for s, m in msgs), msgs


@pytest.mark.parametrize("line", ["g95f.05z-.5p", "g33x.408z-.5i.02k.077pc29o", "rf", "lf", "ls3", "ls", "}1 (LOADER)"])
def test_lint_accepts_bare_words_the_manual_uses(line):
    """P (spindle sync, p2.30), O and C in a G33 (p2.23-2.29), RF, LF and LS
    carry no number in the manual's own examples."""
    program = f"{line}\nm99\n" if line.startswith("}") else f"{line}\n"
    assert not [m for s, m in _lint_msgs(program) if s == "error" and "number" in m]


# ---- corners (defect F): corner_blend itself, for programs that never were a Profile
def test_corner_blend_refuses_degenerate_corners_with_value_error():
    """corner_blend divided by tan(0) for a line doubling back and by a zero
    length for a zero-length line. Now a ValueError the interpreter turns
    into a ProgramError with line numbers."""
    with pytest.raises(ValueError, match="doubles back"):
        corner_blend((0, 0.5), (-0.5, 0.5), (0, 0.5), radius=0.05)
    with pytest.raises(ValueError, match="no length"):
        corner_blend((-0.5, 0.5), (-0.5, 0.5), (-0.5, 0.7), chamfer=0.05)


def test_near_tangent_corner_is_legitimate():
    """A shallow corner (a 0.1 degree taper change) is real geometry: the
    shape is accepted and the corner blends without blowing up."""
    angle = math.radians(0.1)
    end = Point(2 * (0.5 + 0.5 * math.tan(angle)), -1.0)
    p = Profile(Point(1.0, 0), name="od").line(z=-0.5, r=0.02).line(x=end.x, z=end.z)
    assert p.issues() == []
    assert len(p.polyline()) > 3


@pytest.mark.parametrize("excess, ok", [(0.0, True), (5e-10, True), (1e-6, False)])
def test_half_circle_exactly_at_the_validity_boundary(excess, ok):
    """chord = 2R is a half circle and valid; a chord longer than 2R by more
    than floating-point noise (geometry.LENGTH_TOL) is not."""
    r = 0.25
    p = Profile(Point(1.0, 0.0), name="od").line(z=-0.1).arc(1.0, -0.1 - 2 * r - excess, r, "cw")
    assert (p.issues() == []) is ok


def test_backplot_contour_arcs_are_drawn_around_the_written_centre(lib):
    """The interpreter keeps the centre it computed from the written words
    (CenteredArc) rather than recomputing it later."""
    code = post(load_job(EX / "manual_g75.toml", lib), _ik())
    contour = next(p for p in interpret(code, decimals=4) if p.kind == "contour")
    assert any(isinstance(s, CenteredArc) for s in contour.segments)
