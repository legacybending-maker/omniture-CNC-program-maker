"""Stage 5: machine-specific safeguards (issue #40).

Each test says what it protects at the machine. Two kinds live here:

* Rules the post now enforces (comp lead-in, ID centreline, coolant at
  operator stops, stock-length warnings, the operation exit contract).
* Pins on output that was investigated and deliberately *not* changed
  (the safe-start header, the CSS line order). A pin means a later change to
  those lines fails here and has to be argued for, against the manual and on
  the machine, instead of slipping through.

Passing these tests is software verification: the post writes what we meant
it to write. Whether the control does what we think with it is physical
verification, and only Jeff records that (docs/machine-verification.md).
"""

from pathlib import Path

import pytest

from omniturn_cam.geometry import Point, Profile
from omniturn_cam.job import Job, Stock, load_job, save_job
from omniturn_cam.operations import spec_for
from omniturn_cam.operations.box_rough import _start as box_start
from omniturn_cam.operations.finish import lead_in_length, lead_off_x
from omniturn_cam.operations.thread import thread_clear_x, thread_plan
from omniturn_cam.ops import (
    OPERATION_TYPES,
    AxialPass,
    BoxRough,
    CallSub,
    Cutoff,
    Drill,
    Face,
    Finish,
    RadialPass,
    RawCode,
    Rough,
    Tap,
    Thread,
)
from omniturn_cam.post import MachineConfig, OmniTurnPost, PostError, post
from omniturn_cam.post.state import UNKNOWN
from omniturn_cam.tools import Setup, Station, ToolLibrary

EX = Path(__file__).parent.parent / "examples"


@pytest.fixture(scope="module")
def lib():
    return ToolLibrary.load(EX / "tools.toml")


def od_profile() -> Profile:
    """Face corner to a .75 OD: a plain OD shape every OD operation can use."""
    return Profile(Point(0.0, 0.0), name="od").line(0.5, 0.0).line(0.5, -0.5).line(0.75, -0.5)


def job_with(lib, ops, *, stock=None, profiles=None, units="inch"):
    """A job whose plate holds every tool these tests use, one per station.

    The station numbers match the tests below: t1 OD turn (D1), t2 drill,
    t3 boring bar (D3), t4 die head, t5 cutoff, t6 tap, t7 thread, t8 knurl.
    """
    setup = Setup()
    setup.add(Station(1, lib["lh-turn-35"], d=1))
    setup.add(Station(2, lib["drill-250"]))
    setup.add(Station(3, lib["bore-bar-250"], d=3))
    setup.add(Station(4, lib["die-head-1-2-20"]))
    setup.add(Station(5, lib["cutoff-062"]))
    setup.add(Station(6, lib["tap-1-4-20"]))
    setup.add(Station(7, lib["thread-od-60"]))
    setup.add(Station(8, lib["knurl-strd-80"]))
    # A fresh Stock each call, not one built in the signature: a default
    # argument is made once, so every test would share (and could change)
    # the same object. That's ruff's B008.
    return Job("TEST", units, stock or Stock(0.75), setup, profiles or {"od": od_profile()}, list(ops))


def posted(job, machine=None) -> tuple[str, list[str]]:
    warns: list[str] = []
    return post(job, machine, warnings=warns), warns


# ===================================================================== comp lead-in
# Manual p2.32: when comp turns on or off, the tool must be off the part by
# "no less than the size of the radius".
# lh-turn-35 has a .0156 nose radius.


def test_lead_in_shorter_than_the_nose_radius_is_refused(lib):
    """The audit case: a .001 lead-in with a .0156 nose radius used to be
    accepted. Comp would turn on with the tool already touching the part."""
    op = Finish(t=1, rpm=2000, profile="od", start_z=0.001)
    with pytest.raises(PostError, match=r"lead-in of 0\.001.*nose radius 0\.0156.*p2\.32"):
        post(job_with(lib, [op]))


def test_lead_in_equal_to_the_nose_radius_is_allowed(lib):
    """'No less than' includes equal; the check mustn't trip on float noise."""
    op = Finish(t=1, rpm=2000, profile="od", start_z=0.0156)
    code, _ = posted(job_with(lib, [op]))
    assert "g95f.002z0d1" in code  # the D word rides the Z move onto the start (p2.31)


def test_lead_in_rule_only_applies_with_comp(lib):
    """Without comp there is nothing to turn on; a short lead-in is legal."""
    op = Finish(t=1, rpm=2000, profile="od", start_z=0.001, comp="none")
    code, _ = posted(job_with(lib, [op]))
    assert "g41" not in code and "g42" not in code


def test_blank_start_z_lead_in_comes_from_z_clear(lib):
    """A blank Start Z means front of shape + z_clear; a z_clear smaller than
    the nose radius is caught too, and the message says where it came from."""
    m = MachineConfig.for_units("inch", {"z_clear": 0.01, "tool_call_z": 1.0})
    op = Finish(t=1, rpm=2000, profile="od")
    with pytest.raises(PostError, match=r"automatic start \(z_clear 0\.01\)"):
        post(job_with(lib, [op]), m)
    # The default machine's .05 clears it, and the helper measures what emit writes.
    assert lead_in_length(op, od_profile(), 0.05) == pytest.approx(0.05)
    post(job_with(lib, [op]))


def test_lead_in_rule_applies_to_id_shapes_too(lib):
    """The manual states one rule for comp on, OD or ID. An ID pass with the
    same short lead-in is refused the same way (bore-bar-250, .008 radius)."""
    prof = Profile(Point(0.4, 0.0), name="id", side="id").line(0.4, -0.3).line(0.3, -0.3)
    op = Finish(t=3, rpm=2000, profile="id", start_x=0.25, start_z=0.005)
    with pytest.raises(PostError, match=r"lead-in of 0\.005"):
        post(job_with(lib, [op], stock=Stock(0.75, bore=0.25), profiles={"id": prof}))


# ===================================================================== ID centreline
# In a round hole the tool's clearance from the wall is greatest at the centre
# and shrinks again past it. bore-bar-250: .008 nose radius, so the lead-off is
# 2 x max(3 x .008, .01) = .048 on diameter, and p2.32 needs 2 x .008 = .016
# radial clearance before G40.


def _id_job(lib, end_x, comp="auto"):
    prof = Profile(Point(0.2, 0.0), name="id", side="id").line(0.2, -0.2).line(end_x, -0.2)
    op = Finish(t=3, rpm=2000, profile="id", start_x=0.0, comp=comp)
    return job_with(lib, [op], stock=Stock(0.75), profiles={"id": prof}), prof


def test_id_lead_off_stops_at_the_centreline_and_says_so(lib):
    """End at X.04: .04 - .048 would be X-.008, past the centre. It stops at X0
    (still .02 off the wall, more than the .016 the manual asks for)."""
    job, prof = _id_job(lib, 0.04)
    code, warns = posted(job)
    lines = code.splitlines()
    i = lines.index("x.04")
    assert lines[i + 1] == "x0"  # the lead-off: to the centre, not past it
    assert not any(line.startswith("x-") for line in lines)
    assert any("stops at the centreline" in w for w in warns)
    assert lead_off_x(prof, 0.008, "inch") == 0.0


def test_id_bore_too_small_to_lead_off_with_comp_is_refused(lib):
    """End at X.03: the most clearance the hole offers is .015, under the .016
    the manual needs before G40. No lead-off can comply, so refuse."""
    job, _ = _id_job(lib, 0.03)
    with pytest.raises(PostError, match=r"too small to lead off.*p2\.32"):
        post(job)


def test_id_bore_too_small_without_comp_just_stops_at_the_centre(lib):
    """Without comp the 2 x nose radius rule doesn't apply; X0 is still the
    most clearance there is."""
    job, _ = _id_job(lib, 0.03, comp="none")
    code, warns = posted(job)
    assert "x0" in code.splitlines() and not any(line.startswith("x-") for line in code.splitlines())
    assert any("stops at the centreline" in w for w in warns)


def test_id_lead_off_in_a_normal_bore_is_unchanged(lib):
    """End at X.25: .25 - .048 = .202, as before Stage 5."""
    job, _ = _id_job(lib, 0.25)
    code, warns = posted(job)
    assert "x.202" in code.splitlines()
    assert not any("centreline" in w for w in warns)


def test_od_lead_off_is_unchanged(lib):
    """OD leads off outward by .0936 (6 x .0156), as before Stage 5."""
    code, _ = posted(job_with(lib, [Finish(t=1, rpm=2000, profile="od")]))
    assert "x.8436" in code.splitlines()


def test_id_thread_clearance_stops_at_the_centreline(lib):
    """Minor .09, first pass .05: min(.14, .09) - .1 = -.01 used to put the
    tool past the centre while it moved in Z. Now X0, with a warning."""
    op = Thread(t=7, rpm=300, side="id", major=0.2, minor=0.09, first_depth=0.05, z_end=-0.3, lead=0.025)
    code, warns = posted(job_with(lib, [op], stock=Stock(0.75, bore=0.09)))
    assert thread_clear_x(op, thread_plan(op)["start_x"], 0.85) == 0.0
    assert not any(line.startswith("x-") for line in code.splitlines())
    assert any("clearance position stops at the centreline" in w for w in warns)


def test_id_thread_clearance_in_a_normal_bore_is_unchanged(lib):
    op = Thread(t=7, rpm=300, side="id", major=0.5, minor=0.42, first_depth=0.02, z_end=-0.3, lead=0.05)
    assert thread_clear_x(op, thread_plan(op)["start_x"], 0.85) == pytest.approx(0.42 - 0.04)


def test_id_thread_cycle_retract_past_centre_is_a_verify_warning(lib):
    """The control's own between-pass retract (start - 2I) can't be changed by
    the post; when it would pass the centre, the operator is told, marked
    VERIFY because the manual doesn't show the ID direction."""
    op = Thread(t=7, rpm=300, side="id", major=0.3, minor=0.09, first_depth=0.1, z_end=-0.3, lead=0.025)
    _, warns = posted(job_with(lib, [op], stock=Stock(0.75, bore=0.09)))
    assert any("retracts 2 x I" in w and "VERIFY" in w for w in warns)


# ===================================================================== coolant at operator stops
# M00 "does not cancel active M functions" (p2.53). The die head reset stop
# used to leave coolant running while the operator reached in.


def _axial(stop="m01", **kw):
    return AxialPass(t=4, rpm=300, x=0, z_start=0.1, z_end=-0.35, feed=0.05, sync=True, stop_after=stop, **kw)


def _after(code: str, marker: str) -> list[str]:
    """The program lines after the first line that is exactly ``marker``.
    (Whole lines, so "z1" doesn't match inside the approach "x0z1".)"""
    lines = code.splitlines()
    return lines[lines.index(marker) + 1 :]


@pytest.mark.parametrize("stop", ["m00", "m01"])
def test_coolant_off_before_an_operator_stop(lib, stop):
    code, _ = posted(job_with(lib, [_axial(stop)]))
    tail = _after(code, "z1")
    assert tail[:3] == ["m05", "m09", stop]


def test_next_operation_turns_coolant_back_on(lib):
    """The post recorded the M09, so the next operation that wants coolant
    writes M08 again instead of believing it's still on and cutting dry."""
    code, _ = posted(job_with(lib, [_axial("m00"), Drill(t=2, rpm=1800, z=-0.5)]))
    tail = _after(code, "m00")
    assert tail[0].startswith("t2") and tail[1:4] == ["x0z1", "m03s1800", "m08"]


def test_coolant_already_off_is_not_written_again(lib):
    """An axial pass that runs dry already has M09 in effect; no second M09."""
    code, _ = posted(job_with(lib, [_axial("m01", coolant=False)]))
    tail = _after(code, "z1")
    assert tail[:2] == ["m05", "m01"]


def test_coolant_off_even_when_the_spindle_keeps_turning(lib):
    """The two settings are independent: no M05 asked for, M09 still written."""
    code, _ = posted(job_with(lib, [_axial("m00", stop_spindle_for_stop=False)]))
    tail = _after(code, "z1")
    assert tail[:2] == ["m09", "m00"]


def test_coolant_left_on_when_the_operator_says_so(lib):
    """With the new setting off, the old output: no M09, and the post still
    knows coolant is on, so the next operation doesn't restate M08."""
    code, _ = posted(job_with(lib, [_axial("m00", stop_coolant_for_stop=False), Drill(t=2, rpm=1800, z=-0.5)]))
    assert _after(code, "z1")[:2] == ["m05", "m00"]
    nxt = _after(code, "m00")
    assert nxt[0].startswith("t2") and nxt[1:3] == ["x0z1", "m03s1800"]
    assert _after(code, "m03s1800")[0] != "m08"


def test_no_m09_without_a_stop(lib):
    code, _ = posted(job_with(lib, [_axial("none"), Drill(t=2, rpm=1800, z=-0.5)]))
    head = code.split("t2(", 1)[0]
    assert head.count("m09") == 0


def test_job_saved_before_stage_5_gets_coolant_off(lib, tmp_path):
    """Compatibility: an old job file has no stop_coolant_for_stop key; it
    loads with the safe default (coolant off), as CONTRIBUTING.md allows for a
    new optional key."""
    save_job(job_with(lib, [_axial("m01")]), tmp_path / "old.toml")
    text = (tmp_path / "old.toml").read_text()
    assert "stop_coolant_for_stop = true" in text
    (tmp_path / "old.toml").write_text(text.replace("stop_coolant_for_stop = true\n", ""))
    again = load_job(tmp_path / "old.toml", lib)
    assert again.operations[0].stop_coolant_for_stop is True


# ===================================================================== stock length
# F1 "Length out of collet". Warnings only: this compares programmed Z with
# where the collet face probably is, nothing more.


def test_drill_past_the_collet_face_warns(lib):
    code, warns = posted(job_with(lib, [Drill(t=2, rpm=1800, z=-1.2)], stock=Stock(0.75, length=1.0)))
    hit = [w for w in warns if w.startswith("t2 ")]
    assert hit and "Z-1.2" in hit[0] and "about Z-1 " in hit[0] and "holders and offsets aren't modelled" in hit[0]


def test_drill_inside_the_stock_length_is_quiet(lib):
    _, warns = posted(job_with(lib, [Drill(t=2, rpm=1800, z=-0.8)], stock=Stock(0.75, length=1.0)))
    assert not any("collet" in w for w in warns)


def test_no_length_no_check(lib):
    """Length left blank on F1: the post can't compare, and says nothing."""
    _, warns = posted(job_with(lib, [Drill(t=2, rpm=1800, z=-5.0)]))
    assert not any("collet" in w for w in warns)


def test_face_stock_moves_the_bar_end(lib):
    """With .05 face stock the bar end is at Z.05, so 1.0 out of the collet
    puts the face of the collet at Z-.95: a drill to -.97 now warns."""
    ops = [Face(t=1, rpm=2000, stock=0.05), Drill(t=2, rpm=1800, z=-0.97)]
    _, warns = posted(job_with(lib, ops, stock=Stock(0.75, length=1.0)))
    assert any(w.startswith("t2 ") and "bar end at Z.05" in w for w in warns)


def test_cutoff_blade_width_is_a_conditional_warning(lib):
    """Cutoff at Z-.95 with a .062 blade: the reference edge is clear of a
    collet at Z-1, but if it's the face-side edge the other edge reaches
    Z-1.012. The post doesn't know which edge it is, and says so."""
    op = Cutoff(t=5, rpm=1000, z=-0.95, feed=0.001)
    _, warns = posted(job_with(lib, [op], stock=Stock(0.75, length=1.0)))
    hit = [w for w in warns if w.startswith("t5 ")]
    assert hit and "if Z is the face-side edge" in hit[0] and "Z-1.012" in hit[0]


def test_cutoff_well_clear_of_the_collet_is_quiet(lib):
    op = Cutoff(t=5, rpm=1000, z=-0.6, feed=0.001)
    _, warns = posted(job_with(lib, [op], stock=Stock(0.75, length=1.0)))
    assert not any("collet" in w for w in warns)


# ===================================================================== safe start (pinned, VERIFY)
# Investigated, not changed: see docs/omniturn-post-rules.md, "Program start".


def test_header_sets_every_mode_and_no_m_codes(lib):
    """The header establishes comp off, units, absolute, diameter, IPM and RPM
    mode, in this order, and writes no M05/M09: a program that follows an M02
    bar-work cycle may be meant to find the spindle running (p2.58). If this
    changes, the PR has to justify it against the manual and the machine."""
    code, _ = posted(job_with(lib, [Drill(t=2, rpm=1800, z=-0.5)]))
    lines = code.splitlines()
    first_tool = next(i for i, line in enumerate(lines) if line.startswith("t"))
    codes = [line for line in lines[:first_tool] if not line.startswith("(")]
    assert codes[:3] == ["g40", "g70", "g90g72g94g97f300"]
    # Anything after the header line and before the tool is the tool call's
    # own G00 (call_tool); never an M code.
    assert codes[3:] in ([], ["g00"])


def test_spindle_and_coolant_are_explicit_before_the_first_cut(lib):
    """What the header leaves unknown, the first operation states before it
    cuts: M03/M04 with S, and M08 or M09, whatever the last program left on."""
    for coolant, word in ((True, "m08"), (False, "m09")):
        code, _ = posted(job_with(lib, [Drill(t=2, rpm=1800, z=-0.5, coolant=coolant)]))
        lines = code.splitlines()
        cut = next(i for i, line in enumerate(lines) if line.startswith(("g81", "g83", "g95")))
        assert "m03s1800" in lines[:cut] and word in lines[:cut]


# ===================================================================== CSS order (pinned, matches p2.52)


def test_css_lines_in_the_manuals_order(lib):
    """p2.52's own example: G96S250, then G76S500, then G77S2500, after the
    tool is positioned. The post writes the same order. Whether the control
    briefly runs unclamped between the G96 and G77 lines is VERIFY; the order
    stays as the manual shows it until that's checked on the machine."""
    op = Face(t=1, sfm=300, min_rpm=500, max_rpm=2500)
    code, _ = posted(job_with(lib, [op]))
    lines = code.splitlines()
    i = lines.index("g96s300")
    assert lines[i : i + 3] == ["g96s300", "g76s500", "g77s2500"]
    # ...and after the move to the cutting position, never before it.
    assert lines[i - 1].startswith(("x", "z", "g00"))


# ===================================================================== exit contract
# docs/ARCHITECTURE.md, invariant 8. Every operation the post can write must
# leave the slide where this table says, with comp off and RPM mode on, so the
# next tool call can retract in Z alone. A new operation fails
# test_every_operation_has_an_exit_contract until it's added here, on purpose:
# its author has to say where it ends and why that is safe.


def _exit_face(p, op):
    return p.stock_x, op.z + p.m.z_clear


def _exit_rough(p, op):
    return p.start_point(op, p.job.profiles[op.profile])


def _exit_box(p, op):
    return box_start(op, p.stock_x, p.m.z_clear)


def _exit_finish(p, op):
    prof = p.job.profiles[op.profile]
    return lead_off_x(prof, p.station(op).tool.nose_radius, p.job.units), p.start_point(op, prof)[1]


def _exit_drill(p, op):
    return 0.0, p.m.z_clear


def _exit_tap(p, op):
    return op.x, op.z_start


def _exit_thread(p, op):
    plan = thread_plan(op)
    return thread_clear_x(op, plan["start_x"], p.stock_x), op.z_start + op.runup + (op.starts - 1) * op.lead


def _exit_axial(p, op):
    return op.x, (p.m.tool_call_z if op.stop_after != "none" else op.z_start)


def _exit_radial(p, op):
    return (op.x_start if op.x_start is not None else p.stock_x), op.z


# kind: (a sample operation, where it must leave the slide, why that's safe)
EXITS = {
    "face": (Face(t=1, rpm=2000), _exit_face, "on its approach X, z_clear in front of the last face pass"),
    "rough": (Rough(t=1, rpm=2000, profile="od"), _exit_rough, "the G75/G78 start point; the cycle returns there"),
    "box_rough": (
        BoxRough(t=1, rpm=2000, x=0.6, z=-0.3),
        _exit_box,
        "the G74 start point; the cycle returns there",
    ),
    "finish": (Finish(t=1, rpm=2000, profile="od"), _exit_finish, "led off the shape, back at its approach Z"),
    "drill": (Drill(t=2, rpm=1800, z=-0.5), _exit_drill, "on centre at its start Z; G81/G83 return there"),
    "tap": (Tap(t=6, rpm=400, x=0, z_start=0.1, z_end=-0.3), _exit_tap, "on its approach X at its start Z"),
    "thread": (Thread(t=7, rpm=300), _exit_thread, "at the clearance X it approached on, in front of the thread"),
    "axial": (_axial("none"), _exit_axial, "on its approach X at its start Z"),
    # The same operation with an operator stop ends at the tool-change Z.
    "axial_stop": (_axial("m01"), _exit_axial, "at the tool-change Z, where it stopped for the operator"),
    "radial": (
        RadialPass(t=8, rpm=600, z=-0.3, x_start=0.85, x_end=0.7, feed=0.002),
        _exit_radial,
        "back at its approach X, at its Z",
    ),
    "cutoff": (Cutoff(t=5, rpm=1000, z=-0.6, feed=0.001), _exit_radial, "back at its approach X, at its Z"),
    # External code: the post parks first and never assumes where it ends.
    "raw": (RawCode(t=0, code="x.5z-.1", ends_at="check"), None, "unknown unless declared (handoff contract)"),
    "call_sub": (CallSub(t=0, sub=7, ends_at="check"), None, "unknown unless declared (handoff contract)"),
}


def test_every_operation_has_an_exit_contract():
    """The table covers exactly the registered operations (plus variants of
    them, whose sample operation is still one of the registered types)."""
    assert {type(op) for op, _, _ in EXITS.values()} == set(OPERATION_TYPES.values())
    assert set(OPERATION_TYPES) <= set(EXITS)


def _run_until_after(job, n_ops: int) -> OmniTurnPost:
    """Write the header and the first ``n_ops`` operations, then stop, so the
    test can look at the post's state between two operations."""
    p = OmniTurnPost(job)
    p.header()
    for i, op in enumerate(job.operations[:n_ops], 1):
        p.op_number, p.op = i, op
        spec_for(op).emit(p, op)
    return p


@pytest.mark.parametrize("kind", sorted(k for k, v in EXITS.items() if v[1] is not None))
def test_built_in_operation_exit(lib, kind):
    op, exit_at, why = EXITS[kind]
    job = job_with(lib, [op])
    job.validate()
    p = _run_until_after(job, 1)
    s = p.state
    x, z = exit_at(p, op)
    assert s.position_known, f"{kind} must end at a known position ({why})"
    assert (s.x, s.z) == (pytest.approx(x), pytest.approx(z)), f"{kind}: {why}"
    # Modal state it leaves: comp off and RPM mode, whatever it used inside.
    assert s.comp == "g40" and s.spindle_mode == "g97", f"{kind} left comp or CSS on"
    assert s.units == "g70" and s.coords == "g90" and s.diameter == "g72"


@pytest.mark.parametrize("kind", sorted(k for k, v in EXITS.items() if v[1] is not None))
def test_tool_change_after_each_operation_retracts_in_z_first(lib, kind):
    """The next tool call's first move is Z alone to the tool-change Z: no X
    move happens until the slide is back where tools are changed. (Tool 2,
    or tool 1 when the operation already uses tool 2.)"""
    op = EXITS[kind][0]
    # A different tool, so there is a tool change to watch.
    nxt = Drill(t=2, rpm=1800, z=-0.5) if op.t != 2 else Tap(t=6, rpm=400, x=0, z_start=0.1, z_end=-0.3)
    job = job_with(lib, [op, nxt])
    p = _run_until_after(job, 1)
    before = len(p.lines)
    already_clear = p.state.clear
    p.op_number, p.op = 2, nxt
    spec_for(nxt).emit(p, nxt)
    new = p.lines[before:]
    tool_line = next(i for i, line in enumerate(new) if line.startswith("t"))
    moves = [line for line in new[:tool_line] if "x" in line or "z" in line]
    if already_clear:
        assert moves == []
    else:
        assert moves in (["z1"], ["g00z1"]), f"after {kind}: {new[:tool_line]}"


@pytest.mark.parametrize("kind", ["raw", "call_sub"])
def test_external_code_parks_first_and_forgets_where_it_ends(lib, kind):
    """Raw code and M98 can leave the slide anywhere: the post parks at the
    tool-change Z before them and never guesses the position after (issue
    #27). This test pins that both are part of the exit contract."""
    op = EXITS[kind][0]
    job = job_with(lib, [Finish(t=1, rpm=2000, profile="od"), op])
    p = _run_until_after(job, 2)
    assert "z1" in p.lines or "g00z1" in p.lines
    assert p.state.tool is UNKNOWN and p.state.spindle is UNKNOWN and p.state.coolant is UNKNOWN
    assert not p.state.position_known
