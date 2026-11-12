"""Tests anchored to the worked examples in the OmniTurn operator manual."""
from pathlib import Path

import pytest

from omniturn_cam.backplot import interpret
from omniturn_cam.geometry import Arc, Point, Profile
from omniturn_cam.job import Job, Stock, load_job
from omniturn_cam.lint import lint
from omniturn_cam.ops import AxialPass, Drill, Finish, Rough
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
@pytest.mark.parametrize("v,s", [(0, "0"), (0.5, ".5"), (-0.5, "-.5"), (1.0, "1"),
                                 (0.34300, ".343"), (-0.0000001, "0"), (12.34567, "12.3457")])
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
    assert sub == ["x0z0", "x.343c.125", "z-.3", "x.406", "g02x.406z-.487r.0935",
                   "x.375", "z-.75", "x.75"]
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
    for frag in ["N sequence", "two digits", "one M code", "both X and Z", "commas",
                 "out of range", "never defined"]:
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
    assert tail[:5] == ["g04f.2", "g00z.1", "z1", "m05", "m01"]


def test_g74_box_rough_always_carries_u(lib):
    op = BoxRough(t=1, rpm=2000, x=0.395, z=-0.74, depth=0.02, start_x=0.6, start_z=-0.497)
    code = post(_job_with(lib, (1, "lh-turn-35"), ops=[op]))
    assert "g74x.395z-.74i.02u.01f.003" in code


def test_machine_name_in_header_and_rpm_warning(lib, tmp_path):
    (tmp_path / "m.toml").write_text('name = "Hardinge HLV-H OmniTurn G4"\nmax_rpm = 3000\n[inch]\nmax_bar = 1.0625\n')
    m = MachineConfig.load(tmp_path / "m.toml", "inch")
    warns = []
    code = post(_job_with(lib, (2, "drill-250"), ops=[Drill(t=2, rpm=3500, z=-0.3)]), m, warnings=warns)
    assert "(HARDINGE HLV H OMNITURN G4)" in code
    assert any("3500" in w for w in warns)


# --------------------------------------------------------------------- G33 threading (manual p2.23-2.29)
from omniturn_cam.ops import Thread  # noqa: E402


def _thread_job(lib, op):
    lib.add(Tool(id="thd", name="OD thread", kind="thread")) if "thd" not in [t.id for t in lib] else None
    return _job_with(lib, (4, "thd"), ops=[op])


def test_g33_matches_manual_half_13_example(lib):
    """p2.25: 1/2-13, major .495, minor .408, I.02 -> start X.475 at Z.2, G33X.408Z-.5I.02K.077P."""
    op = Thread(t=4, rpm=500, major=0.495, minor=0.408, z_end=-0.5, lead=0.077,
                first_depth=0.02, pullout=True)
    code = post(_thread_job(lib, op))
    lines = code.splitlines()
    i = lines.index("g33x.408z-.5i.02k.077p")
    assert lines[i - 1] == "x.475" and "z.2" in lines[i - 2]
    assert "(THREAD ABOUT 19 PASSES)" in code   # (.087/.02)^2 = 18.9


def test_g33_multi_start_offsets_z_by_pitch(lib):
    """p2.28: three-start 20 pitch: K.15, starts at Z.2, .25, .3."""
    op = Thread(t=4, rpm=500, major=0.495, minor=0.424, z_end=-0.5, lead=0.05, starts=3,
                first_depth=0.01, pullout=True, start_x=0.494)
    code = post(_thread_job(lib, op))
    assert code.count("g33x.424z-.5i.01k.15p") == 3
    assert "z.25\n" in code and "z.3\n" in code


def test_g33_spring_pass_angle_infeed_and_lint(lib):
    op = Thread(t=4, rpm=500, major=0.5, minor=0.424, z_end=-0.5, lead=0.05, first_depth=0.008,
                pullout=True, infeed_angle=29, spring_passes=1)
    code = post(_thread_job(lib, op))
    assert "g33x.424z-.5i.008k.05pc\ng33x.424z-.5i.008k.05pco" in code
    assert not [p for p in lint(code) if p.severity == "error"]
    op.infeed_angle = 27
    assert "k.05pc27" in post(_thread_job(lib, op))


def test_g33_feed_limit_and_fixed_rpm(lib):
    op = Thread(t=4, rpm=3000, major=1.0, minor=0.8, z_end=-0.5, lead=0.125, first_depth=0.02)
    with pytest.raises(PostError, match="300"):
        post(_thread_job(lib, op))      # 3000 x .125 = 375 ipm
    op.rpm, op.sfm = None, 300
    with pytest.raises(PostError, match="fixed RPM"):
        post(_thread_job(lib, op))


def test_g33_internal_thread_starts_inside(lib):
    op = Thread(t=4, rpm=400, side="id", major=0.9375, minor=0.8783, z_end=-0.75, lead=0.05,
                first_depth=0.01, infeed_angle=29)
    code = post(_thread_job(lib, op))
    assert "g33x.9375z-.75i.01k.05c" in code and "\nx.8883\n" in code
