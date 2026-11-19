"""Drive the GUI headless the way an operator would and check the job it builds."""

import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from omniturn_cam.gui.app import MainWindow  # noqa: E402
from omniturn_cam.job import load_job  # noqa: E402
from omniturn_cam.lint import lint  # noqa: E402

EX = Path(__file__).parent.parent / "examples"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(app, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    # CONFIG_DIR is worked out from the home folder once, when gui.app is
    # imported, so setting HOME above doesn't move it. Point it at this test's
    # temporary folder too, or the tests read and write the real
    # ~/.config/omniturn-cam/machine.toml on whoever's computer runs them.
    import omniturn_cam.gui.app as app_module

    monkeypatch.setattr(app_module, "CONFIG_DIR", tmp_path / ".config" / "omniturn-cam")
    w = MainWindow(library_path=EX / "tools.toml")
    w.dirty = False
    yield w
    w.dirty = False
    w.close()


def test_loads_example_and_generates(win):
    win.open_job(EX / "manual_g75.toml")
    for i in range(6):
        win.show_screen(i)
    assert win.last_error == ""
    code = win.screens[5].text.toPlainText()
    assert "g75i.02u.005f.003p1" in code and not [p for p in lint(code) if p.severity == "error"]


def test_build_job_from_scratch_and_save(win, tmp_path):
    win.show_screen(2)  # tool plate
    plate = win.screens[2]
    plate._add()  # T1 = first library tool (LH turn)
    st = win.job.setup.stations[1]
    st.d = 1
    win.show_screen(1)  # part shape
    shape = win.screens[1]
    shape._add("line")
    shape.table.item(0, 1).setText("0.5")  # X.5 at Z0
    shape._add("line")
    shape.table.item(1, 2).setText("-0.5")  # Z-.5 (X blank = keep .5)
    shape._add("line")
    shape.table.item(2, 1).setText("1.0")  # up to the bar
    prof = win.job.profiles["od"]
    assert [(s.end.x, s.end.z) for s in prof.segments] == [(0.5, 0), (0.5, -0.5), (1.0, -0.5)]

    win.show_screen(3)  # processes
    proc = win.screens[3]
    proc._add("rough")
    proc._add("finish")
    assert [op.label for op in win.job.operations] == ["rough", "finish"]
    assert win.job.operations[0].t == 1 and win.job.operations[0].profile == "od"

    win.show_screen(4)
    assert win.last_error == "", win.last_error
    code = win.generate()
    assert "g75" in code and "g41" in code and "d1" in code

    win.job_path = tmp_path / "part1.toml"
    assert win.save()
    again = load_job(win.job_path, win.library)
    assert len(again.operations) == 2 and again.setup.stations[1].d == 1


def test_errors_are_reported_not_raised(win):
    win.show_screen(3)
    win.job.operations.clear()
    from omniturn_cam.fields import new_operation

    win.job.operations.append(new_operation("drill", 7))  # no tool at T7
    win.show_screen(4)
    assert "t7" in win.last_error


def test_die_head_process_prefills_from_tool(win):
    from omniturn_cam.fields import new_operation

    op = new_operation("axial", 3, win.library["die-head-1-2-20"])
    assert op.sync is True and op.feed == 0.05


def test_step_import_dialog_fills_shapes(win):
    from omniturn_cam import kernels

    try:
        kernels.get()  # OCP or FreeCAD, whichever is installed
    except kernels.KernelUnavailable:
        pytest.skip("no geometry kernel")
    from omniturn_cam.gui.stepdialog import StepImportDialog

    path = Path(__file__).parent / "data" / "bush.step"
    win.job.stock.diameter = 0.75
    dlg = StepImportDialog(str(path), win.job, win)
    dlg.flip.setChecked(True)
    assert dlg.result is not None and dlg.result.id is not None
    assert dlg.set_stock.isChecked()  # part is bigger than the bar
    dlg.apply()
    assert win.job.stock.diameter == 1.0
    assert set(win.job.profiles) >= {"od", "id"} and win.job.profiles["id"].side == "id"


# The F-key bar, in order. Operators press these by feel, so the order and the
# keys are part of the interface: change them here, on purpose, or not at all.
F_KEYS = [
    "F1 Job & Blank",
    "F2 Part Shape",
    "F3 Tool Plate",
    "F4 Processes",
    "F5 Check",
    "F6 Program",
    "F7 Save Job",
    "F8 Tool Library",
]


def test_f_keys_are_stable(win):
    got = [b.text().replace("\n", " ").replace("&&", "&") for b in win.fkeys]
    assert got == F_KEYS


def test_every_operation_form_builds(win):
    """Each operation's fill-in-the-blank form builds, and every field gets a
    proper label rather than its Python name."""
    from PySide6.QtWidgets import QFormLayout

    from omniturn_cam.fields import form_fields, new_operation
    from omniturn_cam.gui.forms import OperationForm
    from omniturn_cam.operations import SPECS

    win.open_job(EX / "manual_g75.toml")
    for kind in SPECS:
        op = new_operation(kind, 1)
        form = OperationForm(op, win.job)
        expected = dict(form_fields(op))
        assert set(form.widgets) == set(expected), f"{kind}: form fields differ from the operation's fields"
        for name, widget in form.widgets.items():
            layout = widget.parentWidget().layout()
            assert isinstance(layout, QFormLayout), f"{kind}.{name}: not in a form section"
            label = layout.labelForField(widget)
            assert label is not None, f"{kind}.{name}: no label on the form"
            text = label.text()
            # fields.info() falls back to the Python name when nothing labels a field.
            assert text != name, f"{kind}.{name}: the form shows the Python name"
            assert text == expected[name].label
            assert widget.toolTip() == expected[name].help and expected[name].help.strip()
        form.deleteLater()


# ---------------------------------------------------------------- machine profile (Stage 2, issue #32)
def test_default_machine_profile_is_used_when_nothing_else_is_set(win, tmp_path):
    """With a fresh HOME the GUI copies the built-in profile to
    ~/.config/omniturn-cam/machine.toml and posts with it. Also guards the
    fixture: the tests below break that file on purpose, so it must be the
    test's own copy and never the real one."""
    win.open_job(EX / "manual_g75.toml")
    assert win.machine_path.exists()
    assert win.machine_path.is_relative_to(tmp_path)
    assert win.generate() is not None and win.last_error == ""


@pytest.mark.parametrize(
    "text, words",
    [
        ("max_rmp = 2500\n", ["max_rmp", "Did you mean max_rpm"]),
        ('max_rpm = "4000"\n', ["max_rpm is '4000'", "must be a number"]),
        ("[inch]\nz_clear = nan\n", ["z_clear is nan"]),
        ("max_rpm = \n", ["not valid TOML"]),
    ],
)
def test_invalid_machine_file_refuses_to_generate(win, text, words):
    """It used to warn and post with the built-in machine instead: code for a
    machine other than the one the operator set up. Now nothing is made, F5
    and F6 say which file and setting, and how to get going again."""
    win.open_job(EX / "manual_g75.toml")
    win.machine_path.write_text(text)
    assert win.generate() is None
    for w in [str(win.machine_path), "no program was made", "Fix the file", *words]:
        assert w in win.last_error, win.last_error
    win.show_screen(5)  # F6 Program: no code to save, the problem shown
    assert win.screens[5].text.toPlainText() == "" and win.screens[5].code is None
    assert "no program was made" in win.screens[5].status.text()
    win.show_screen(0)  # F1 header preview shows the problem instead of crashing
    assert "can't be used" in win.screens[0].header.toPlainText()


def test_unreadable_machine_file_refuses_to_generate(win):
    win.open_job(EX / "manual_g75.toml")
    win.machine_path.unlink()
    assert win.generate() is None and "can't be read" in win.last_error


def test_fixing_the_machine_file_takes_effect_at_the_next_check(win):
    win.open_job(EX / "manual_g75.toml")
    good = win.machine_path.read_text()
    win.machine_path.write_text("max_rmp = 1\n")
    assert win.generate() is None
    win.machine_path.write_text(good)
    assert win.generate() is not None


# ---------------------------------------------------------------- units and new processes
def test_changing_units_keeps_values_and_new_processes_use_the_new_units(win):
    """Changing F1 units relabels fields and never converts numbers already
    in the job; processes added afterwards start with values for the new units."""
    win.open_job(EX / "manual_g75.toml")
    before = [dict(vars(op)) for op in win.job.operations]
    win.screens[0]._units("metric")
    assert win.job.units == "metric"
    assert [dict(vars(op)) for op in win.job.operations] == before
    win.show_screen(3)
    win.screens[3].list.setCurrentRow(-1)
    win.screens[3]._add("face")
    added = win.job.operations[-1]
    assert added.feed == 0.08  # mm/rev, not .003


# ---------------------------------------------------------------- F5 backplot (issue #36)
def _canvas_text(screen) -> str:
    """The text a PlotCanvas.message() put on the plot, if any."""
    return " ".join(t.get_text() for ax in screen.canvas.figure.axes for t in ax.texts)


def test_check_screen_backplots_with_the_machines_arc_format(win):
    """F5 draws with the machine the program was made for. An I/K machine
    used to crash F5 (and the post) with KeyError 'r'."""
    win.open_job(EX / "manual_g75.toml")
    win.machine_path.write_text('arc_format = "ik"\n')
    win.show_screen(4)
    assert win.last_error == "" and win.last_machine.arc_format == "ik"
    assert "can't draw" not in _canvas_text(win.screens[4])


def test_check_screen_shows_a_backplot_problem_instead_of_crashing(win, monkeypatch):
    """If the interpreter ever can't read a program, F5 says so on the plot
    rather than raising into Qt."""
    import omniturn_cam.gui.screens.check as check
    from omniturn_cam.backplot import ProgramError

    def broken(*a, **k):
        raise ProgramError(7, "made-up problem")

    monkeypatch.setattr(check, "draw", broken)
    win.open_job(EX / "manual_g75.toml")
    win.show_screen(4)
    assert "can't draw this program" in _canvas_text(win.screens[4])
    assert "line 7: made-up problem" in _canvas_text(win.screens[4])
