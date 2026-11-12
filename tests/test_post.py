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
        Job("TOOLONGNAME", "inch", Stock(1), Setup())


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
