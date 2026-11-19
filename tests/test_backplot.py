"""The backplot interpreter (backplot.py) on programs written by hand.

The interpreter isn't only for pictures: post() runs it on every generated
program for the travel check, and `omniturn-cam plot` runs it on any file an
operator hands it. These tests pin what it must do with input it can't draw
truthfully (issue #36): refuse with the line number, never guess a position,
never invent an arc, and never crash with a Python arithmetic error.

Programs here are short strings so each test shows exactly the lines it is
about. Arc directions use the OmniTurn mapping: print-CCW = G02 (p2.18-2.19).
"""

from __future__ import annotations

import math

import pytest

from omniturn_cam.backplot import CenteredArc, ProgramError, UnsupportedProgram, interpret, parse
from omniturn_cam.geometry import Line


def _feed(paths):
    return [p for p in paths if p.kind == "feed"]


def _contour(paths):
    return next(p for p in paths if p.kind == "contour")


def _on_circle(points, cz, cr, r, tol):
    """Every (z, x-diameter) point lies on the circle centre (cz, cr) radius r."""
    return all(abs(math.hypot(z - cz, x / 2 - cr) - r) <= tol for z, x in points)


# The manual's own I/K arcs (p2.18-2.19), as one program each, and the same
# arcs in R format. Start x.2 z0 -> x.5 z-.15, centre x.2 z-.15: print-CCW.
CCW_IK = "t1\ng00x.2z0\ng01x.2z0\ng02x.5z-.15i0k-.15\n"
CCW_R = "t1\ng00x.2z0\ng01x.2z0\ng02x.5z-.15r.15\n"
# X.2 Z-.3 -> X.4 Z-.4 with I.1: centre at radius .2, Z-.3; print-CW.
CW_IK = "t1\ng00x.2z-.3\ng01x.2z-.3\ng03x.4z-.4i.1k0\n"
CW_R = "t1\ng00x.2z-.3\ng01x.2z-.3\ng03x.4z-.4r.1\n"


# ------------------------------------------------------------------ arcs, R and I/K
@pytest.mark.parametrize(
    "program, direction, centre",
    [
        (CCW_IK, "ccw", (-0.15, 0.1)),
        (CCW_R, "ccw", (-0.15, 0.1)),
        (CW_IK, "cw", (-0.3, 0.2)),
        (CW_R, "cw", (-0.3, 0.2)),
    ],
    ids=["ccw-ik", "ccw-r", "cw-ik", "cw-r"],
)
def test_main_program_arcs_r_and_ik_draw_the_same_arc(program, direction, centre):
    """Defect A: an I/K arc used to raise KeyError 'r'. Both formats must give
    the manual's centre, so a machine set to arc_format = "ik" draws (and
    travel-checks) exactly what an "r" machine does."""
    arc = _feed(interpret(program))[0].segments[-1]
    assert isinstance(arc, CenteredArc) and arc.direction == direction
    assert arc.centre == pytest.approx(centre)


def _g75(contour_lines: str, sub: str = "}5") -> str:
    return "t1\ng00x1z.1\ng75i.05u.01f.005p5\nm30\n" + sub + "\n" + contour_lines + "m99\n"


@pytest.mark.parametrize("arc", ["g02x.5z-.15i0k-.15", "g02x.5z-.15r.15"], ids=["ik", "r"])
def test_contour_subroutine_arcs_r_and_ik(arc):
    """Defect A in a G75 contour subroutine: the R and I/K forms of the same
    arc draw the same contour."""
    c = _contour(interpret(_g75(f"x.2z0\n{arc}\nz-.5\n")))
    arc_seg = c.segments[0]
    assert isinstance(arc_seg, CenteredArc) and arc_seg.centre == pytest.approx((-0.15, 0.1))
    pts = c.points()
    assert _on_circle(pts[1:-1], -0.15, 0.1, 0.15, 1e-9)


def test_ik_arc_over_180_degrees_is_drawn_around_the_written_centre():
    """I/K states the centre, so a major arc is drawn as written rather than
    recomputed as the minor arc of the same radius."""
    # From x.2 z0 print-CCW (G02) round a centre at radius .1, z-.1, to x0
    # z-.1: 270 degrees, the long way (print-CW, G03, would be the 90 degree one).
    paths = interpret("t1\ng00x.2z0\ng01x.2z0\ng02x0z-.1i0k-.1\n")
    pts = _feed(paths)[0].points()
    assert _on_circle(pts[1:], -0.1, 0.1, 0.1, 1e-9)
    # it goes round past the far side (z < -.1 at some point): the major arc
    assert min(z for z, _ in pts) < -0.15


@pytest.mark.parametrize(
    "line, words",
    [
        ("g02x.5z-.15i0k-.2", "no arc joins them"),  # I/K centre not equidistant from start and end
        ("g02x.5z-.15r.1", "can't reach"),  # chord .2121 > 2R .2
        ("g02x.5z-.15", "needs R, or both I and K"),
        ("g02x.5z-.15i0", "needs R, or both I and K"),
        ("g02x.5z-.15r.15i0k-.15", "both R and I/K"),
        ("g02x.5z-.15r0", "R0 is not an arc"),
        ("g02x.2z0r.1", "ends where it starts"),
        ("g02x.5z-.15i0k0", "on its start point"),
        ("g02g03x.5z-.15r.15", "two arc directions"),
    ],
)
def test_impossible_or_incomplete_arcs_are_refused_with_the_line(line, words):
    """Never silently approximate an arc or invent a radius (#36)."""
    with pytest.raises(ProgramError) as e:
        interpret(f"t1\ng00x.2z0\ng01x.2z0\n{line}\n")
    assert e.value.line == 4 and words in str(e.value)


def test_rounding_allowance_comes_from_the_decimals_written():
    """A half circle whose ends were rounded to 4 decimals can read a
    ten-thousandth longer than 2R: that's rounding, not a wrong program, so it
    is drawn. The same excess written to 6 decimals is real, and refused."""
    # True geometry: r .0050004 across .0100008 (issue #36's example). Written
    # to 4 decimals: r.005, ends x.5 z-.1 and x.5 z-.1100 -> chord .0100 ok;
    # push the end out a ten-thousandth to be sure the allowance is used.
    ok = "t1\ng00x.5z-.1\ng01x.5z-.1\ng03x.5z-.1101r.005\n"
    assert _feed(interpret(ok))[0].segments[-1].radius == 0.005
    # Six decimals: chord .010100 vs 2R .010000 is far beyond the half step
    # (.0000005) those decimals allow.
    with pytest.raises(ProgramError, match="can't reach"):
        interpret("t1\ng00x.5z-.1\ng01x.5z-.1\ng03x.5z-.110100r.005000\n")


def test_arc_codes_follow_the_machine_mapping():
    """interpret() reads arcs with the machine's G02/G03 mapping, as the post
    writes them (MachineConfig.arc_codes)."""
    swapped = {"ccw": "g03", "cw": "g02"}
    arc = _feed(interpret(CCW_R.replace("g02", "g03"), swapped))[0].segments[-1]
    assert arc.direction == "ccw"


# ------------------------------------------------------------------ positions never invented
def test_m10_before_any_coordinate_in_a_contour_is_passed_over():
    """Defect D: a subroutine starting with M10 used to become Point(None, None)
    and crash later with TypeError. Lines with no X or Z carry no position."""
    c = _contour(interpret(_g75("m10\nx.5z0\nx.5z-.2\n")))
    assert (c.start.x, c.start.z) == (0.5, 0.0)
    assert c.points() == [(0.0, 0.5), (-0.2, 0.5)]


def test_m10_before_any_move_in_the_main_program_draws_nothing_from_unknown():
    """The first X and Z only set the position; nothing is drawn from a
    position the program never gave."""
    paths = interpret("m10\nt1\nm03s1000\ng00x1\nz.1\ng01x.5z0\n")
    assert [(p.kind, p.start) for p in paths] == [("feed", paths[0].start)]
    assert (paths[0].start.x, paths[0].start.z) == (1.0, 0.1)


@pytest.mark.parametrize(
    "contour, words",
    [
        ("z-.2\nx.5z-.3\n", "first move gives only Z"),
        ("x.5\nz-.3\n", "first move gives only X"),
        ("g02x.5z-.15r.15\n", "starts with an arc"),
        ("m10\nm11\n", "no X/Z moves"),
    ],
)
def test_malformed_contour_starts_are_refused(contour, words):
    with pytest.raises(ProgramError, match=words):
        interpret(_g75(contour))


@pytest.mark.parametrize(
    "line, words",
    [("g33x.4z-.5k.05", "G33 before"), ("g81z-.5", "drill cycle before"), ("g02x.5z-.15r.15", "arc before")],
)
def test_cycles_and_arcs_need_a_known_position(line, words):
    with pytest.raises(ProgramError, match=words):
        interpret(f"t1\n{line}\n")


def test_contour_call_to_a_missing_subroutine_is_refused():
    with pytest.raises(ProgramError, match="subroutine 7 isn't in the program"):
        interpret("t1\ng00x1z.1\ng75i.05u.01f.005p7\nm30\n")


# ------------------------------------------------------------------ subroutine declarations
def test_commented_subroutine_header_is_the_same_subroutine():
    """Defect C: `}5 (FINISH)` is subroutine 5 (comments may sit on any line, p2.6)."""
    c = _contour(interpret(_g75("x.5z0\nx.5z-.2\n", sub="}5 (FINISH)")))
    assert c.points()[-1] == (-0.2, 0.5)


def test_leading_zero_subroutine_number_matches_its_call():
    """Defect C: `}05` and `p5` are the same subroutine (they used to be
    compared as the text "05" and "5")."""
    c = _contour(interpret(_g75("x.5z0\nx.5z-.2\n", sub="}05")))
    assert len(c.segments) == 1


@pytest.mark.parametrize(
    "program, words",
    [
        ("m30\n}1\nm10\nm99\n}01 (AGAIN)\nm11\nm99\n", "subroutine 1 is defined twice"),
        ("m30\n}1\nm10\n}2\nm11\nm99\n", "subroutine 2 starts before subroutine 1 ends"),
        ("m30\n}1\nm10\n", "subroutine 1 never ends"),
        ("m30\n}1a\nm10\nm99\n", "isn't a subroutine start"),
    ],
)
def test_bad_subroutine_structure_is_refused(program, words):
    """A duplicate used to silently replace the first body, so a G75 could be
    drawn (and travel-checked) against the wrong contour."""
    with pytest.raises(ProgramError, match=words):
        parse(program)


# ------------------------------------------------------------------ unreadable input
@pytest.mark.parametrize(
    "line, words",
    [
        ("g01x1.2.3z0", "can't be read as a number"),
        ("g01x-z0", "can't be read as a number"),
        ("g01xz0", "X has no number"),
        ("g01x1;z0", "isn't part of any word"),
        ("g01x1z0°", "isn't part of any word"),  # a degree sign
        ("g1x1z0", "must be two digits"),
        ("t1.5", "tool numbers are whole"),
    ],
)
def test_unreadable_lines_are_refused_with_the_line_number(line, words):
    """These used to be skipped or half-read (a findall that drops what it
    can't match) or raise a bare ValueError."""
    with pytest.raises(ProgramError) as e:
        interpret(f"t1\ng00x1z.1\n{line}\n")
    assert e.value.line == 3 and words in str(e.value)


@pytest.mark.parametrize(
    "program",
    [
        "t1\ng00x1z.1\ng91\ng01x-.1\n",
        "t1\ng00x1z.1\ng73\ng01x.4z0\n",
        "t1\ng00x.2z0\ng01x.2z0\ng02x.5z-.15r-.15\n",
    ],
    ids=["g91-move", "g73-move", "negative-r"],
)
def test_valid_code_the_backplot_cannot_follow_is_unsupported_not_wrong(program):
    """UnsupportedProgram lets the travel check say "not checked" for legal
    raw code it can't follow, while still refusing broken programs."""
    with pytest.raises(UnsupportedProgram):
        interpret(program)


@pytest.mark.parametrize(
    "program",
    ["t1\ng00x1z.1\nls3\ng01x.5z0\nlf\n", "t1\ng00x1z.1\ng75i.05u.01f.005\nx.5z0\nz-.2\nrf\n"],
    ids=["loop", "inline-contour"],
)
def test_marker_lines_with_bare_letters_are_not_misread(program):
    """LS/LF (loops, p2.20-2.21) and RF (end of an inline contour, p2.41) have
    letters with no number; they must not be read as an F or R word."""
    assert interpret(program)


def test_fractional_contour_subroutine_number_is_refused():
    with pytest.raises(ProgramError, match="whole number"):
        interpret("t1\ng00x1z.1\ng75i.05u.01f.005p5.5\nm30\n}5\nx.5z0\nm99\n")


def test_mode_switch_and_back_without_a_move_draws_normally():
    """Raw code that switches to G91 and back before moving is fine."""
    paths = interpret("t1\ng00x1z.1\ng91\ng90\ng01x.5z0\n")
    assert _feed(paths)[0].points() == [(0.1, 1.0), (0.0, 0.5)]


# ------------------------------------------------------------------ corners
def test_corner_on_lines_that_double_back_is_refused_not_divided_by_zero():
    """Defect F: corner_blend used to compute R / tan(0). From a hand-written
    program (never checked as a Profile) that's now a ProgramError."""
    with pytest.raises(ProgramError, match="doubles back"):
        interpret("t1\ng00x1z.1\ng01x1z0\nz-.5r.05\nz0\n")


def test_corner_with_both_c_and_r_is_refused():
    with pytest.raises(ProgramError, match="C or R, not both"):
        interpret("t1\ng00x1z.1\ng01x1z0\nz-.5c.05r.05\nx1.2\n")


def test_corner_on_a_straight_run_is_drawn_sharp():
    """Two lines in a straight line have no corner to blend: drawn as written
    (a very shallow corner in a shape can read back exactly straight once
    rounded, so this mustn't be refused or divide by zero)."""
    paths = interpret("t1\ng00x1z.1\ng01x1z0\nz-.5r.05\nz-1\n")
    assert _feed(paths)[0].points() == [(0.1, 1.0), (0.0, 1.0), (-0.5, 1.0), (-1.0, 1.0)]


def test_lines_with_a_valid_corner_still_blend():
    seg = _feed(interpret("t1\ng00x1z.1\ng01x1z0\nz-.5r.05\nx1.4\n"))[0].segments
    assert isinstance(seg[1], Line) and seg[1].radius == 0.05


# ------------------------------------------------------------------ the plot command
def test_plot_command_refuses_a_malformed_program_cleanly(tmp_path, capsys):
    """`omniturn-cam plot` on a broken hand-written file: one error line with
    the file and line number and exit code 2, not a Python traceback, and no
    image written."""
    from omniturn_cam.cli import main

    prog = tmp_path / "BAD.cnc"
    prog.write_text("t1\ng00x1z.1\ng75i.05u.01f.005p5\nm30\n}5\nz-.2\nm99\n")
    png = tmp_path / "out.png"
    assert main(["plot", str(prog), str(png)]) == 2
    err = capsys.readouterr().err
    assert "BAD.cnc" in err and "line 6" in err and "only Z" in err
    assert not png.exists()


def test_plot_command_refuses_bytes_that_are_not_text(tmp_path, capsys):
    from omniturn_cam.cli import main

    prog = tmp_path / "BIN.cnc"
    prog.write_bytes(b"t1\ng00x1z.1\ng01x\xff.5z0\n")
    assert main(["plot", str(prog), str(tmp_path / "o.png")]) == 2
    assert "line 3" in capsys.readouterr().err


def test_plot_command_still_plots_a_good_program(tmp_path):
    from omniturn_cam.cli import main

    prog = tmp_path / "GOOD.cnc"
    prog.write_text("t1\ng00x1z.1\ng01x.5z0\ng02x.8z-.15r.15\nz-.5\nm30\n")
    png = tmp_path / "out.png"
    assert main(["plot", str(prog), str(png)]) == 0 and png.stat().st_size > 0
