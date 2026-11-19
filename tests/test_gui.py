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
    # A test may leave an unsaved (or invalid) edit: closing would ask, and
    # a real question box would wait forever. Answer "discard".
    w.save_question = lambda: "discard"
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
    assert dlg.import_result is not None and dlg.import_result.id is not None
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


# =================================================================== issue #38: GUI integrity
# These drive the window the way an operator does: real key presses into
# fields (QTest), real F-key presses, the dialogs' own buttons. Message boxes
# go through win.notify / win.ask (app.py), which the `said` fixture records
# and answers, because a real modal box would wait forever for a click.
import copy  # noqa: E402

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QAbstractItemView, QDialog  # noqa: E402


@pytest.fixture
def said(win, monkeypatch):
    """What the window told the operator; ``said.answer`` is the reply to
    yes/no questions (default no)."""

    class Said(list):
        answer = False

    out = Said()
    monkeypatch.setattr(win, "notify", lambda title, text: out.append(text))
    monkeypatch.setattr(win, "ask", lambda title, text: (out.append(text), out.answer)[1])
    monkeypatch.setattr(win, "save_question", lambda: "discard")
    win.show()
    win.activateWindow()
    QTest.qWaitForWindowActive(win, 1000)
    return out


def _press(win, key):
    """Press an F-key the way the operator does: a real key press on the
    window, which fires the QShortcut."""
    QTest.keyClick(win, key)


def _type_into(line_edit, text):
    """Click into a field, select what's there and type over it, without
    pressing Return or leaving the field."""
    line_edit.setFocus()
    line_edit.selectAll()
    QTest.keyClicks(line_edit, text)


def _process_form(win, row=0):
    win.show_screen(3)
    win.screens[3].list.setCurrentRow(row)
    return win.screens[3].scroll.widget()


# ---- A. values typed and not "finished" are what gets used
def test_number_typed_then_f6_without_return_is_in_the_program(win, said):
    """The finding: type a feed, press F6. The field showed .0123 and the
    program used the old .003, because F-keys never take focus and so the
    field never saw 'editing finished'."""
    win.open_job(EX / "manual_g75.toml")
    feed = _process_form(win).widgets["feed"]
    _type_into(feed.edit, ".0123")
    _press(win, Qt.Key_F6)
    assert win.stack.currentIndex() == 5
    assert win.job.operations[0].feed == 0.0123
    assert "f.0123" in win.screens[5].code


def test_number_typed_then_f5_reaches_the_check(win, said):
    win.open_job(EX / "manual_g75.toml")
    rpm = _process_form(win).widgets["rpm"]
    _type_into(rpm.edit, "1234")
    _press(win, Qt.Key_F5)
    assert win.job.operations[0].rpm == 1234 and win.stack.currentIndex() == 4


def test_text_typed_then_f7_is_in_the_saved_file(win, said, tmp_path):
    """F1 description and program name, typed and saved with F7 straight
    away: the file has them (the name in capitals, as it will be on the stick)."""
    win.open_job(EX / "manual_g75.toml")
    win.job_path = tmp_path / "saved.toml"
    win.show_screen(0)
    _type_into(win.screens[0].desc, "typed then F7")
    _press(win, Qt.Key_F7)
    _type_into(win.screens[0].name, "newname")
    _press(win, Qt.Key_F7)
    text = win.job_path.read_text()
    assert 'description = "typed then F7"' in text and 'name = "NEWNAME"' in text
    assert not win.dirty and said == []


def test_table_cell_typed_then_screen_switch_reaches_the_shape(win, said):
    """A cell still open for typing in the F2 table is committed when the
    operator switches screen, so F5 checks the shape on screen."""
    win.open_job(EX / "manual_g75.toml")
    win.show_screen(1)
    table = win.screens[1].table
    item = table.item(1, 1)
    table.setCurrentItem(item)
    table.editItem(item)
    assert table.state() == QAbstractItemView.EditingState
    QTest.keyClicks(QApplication.focusWidget(), "0.35")
    _press(win, Qt.Key_F5)
    assert win.stack.currentIndex() == 4
    assert win.job.profiles["od"].segments[1].end.x == 0.35
    assert table.item(1, 1).text() == "0.35"


def test_table_cell_typed_then_f7_is_in_the_saved_file(win, said, tmp_path):
    """F7 saves without leaving the screen, so nothing makes the open cell
    editor let go of its text by itself: commit_edits (EditTable) must."""
    win.open_job(EX / "manual_g75.toml")
    win.job_path = tmp_path / "cell.toml"
    win.show_screen(1)
    table = win.screens[1].table
    item = table.item(2, 2)
    table.setCurrentItem(item)
    table.editItem(item)
    QTest.keyClicks(QApplication.focusWidget(), "-0.31")
    _press(win, Qt.Key_F7)
    assert win.stack.currentIndex() == 1 and table.state() != QAbstractItemView.EditingState
    assert win.job.profiles["od"].segments[2].end.z == -0.31
    assert "z = -0.31" in win.job_path.read_text()


@pytest.mark.parametrize("key", [Qt.Key_F5, Qt.Key_F6, Qt.Key_F7, Qt.Key_F1])
def test_invalid_number_refuses_the_f_key_and_keeps_the_job(win, said, key, tmp_path):
    """An entry that isn't a number stops F5/F6/F7 and screen changes: the
    operator is told which field, stays on it, and the job keeps its value.
    Moving on would rebuild the screen from the job and lose the entry."""
    win.open_job(EX / "manual_g75.toml")
    win.job_path = tmp_path / "keep.toml"
    feed = _process_form(win).widgets["feed"]
    _type_into(feed.edit, "abc")
    _press(win, key)
    assert win.stack.currentIndex() == 3 and win.fkeys[3].isChecked()
    assert win.job.operations[0].feed == 0.003
    assert said and "Feed" in said[-1] and "'abc' isn't a number" in said[-1]
    assert not win.job_path.exists()
    assert feed.edit.text() == "abc"  # the entry is still there to fix


def test_whole_number_fields_refuse_fractions_instead_of_rounding(win, said):
    """T1.5 used to become T2 without a word."""
    win.open_job(EX / "manual_g75.toml")
    before = sorted(win.job.setup.stations)
    win.show_screen(2)
    t_field = win.screens[2].table.cellWidget(0, 0)
    _type_into(t_field.edit, "1.5")
    _press(win, Qt.Key_F4)
    assert win.stack.currentIndex() == 2 and "must be a whole number" in said[-1]
    assert sorted(win.job.setup.stations) == before


def test_invalid_program_name_is_refused_not_left_red(win, said):
    win.open_job(EX / "manual_g75.toml")
    win.show_screen(0)
    _type_into(win.screens[0].name, "BAD NAME")
    _press(win, Qt.Key_F6)
    assert win.stack.currentIndex() == 0 and win.job.name == "MANG75"
    assert "Program name" in said[-1]


def test_switching_process_commits_the_form_first(win, said):
    win.open_job(EX / "manual_g75.toml")
    feed = _process_form(win, 0).widgets["feed"]
    _type_into(feed.edit, ".0042")
    win.screens[3].list.setCurrentRow(1)
    assert win.job.operations[0].feed == 0.0042


def test_switching_process_with_an_invalid_field_stays_put(win, said):
    win.open_job(EX / "manual_g75.toml")
    feed = _process_form(win, 0).widgets["feed"]
    _type_into(feed.edit, "x")
    win.screens[3].list.setCurrentRow(1)
    assert win.screens[3].list.currentRow() == 0
    assert win.screens[3].scroll.widget().widgets["feed"] is feed
    assert said


# ---- B. an invalid shape never looks like the one being posted
def _make_shape_invalid(win):
    """Change the manual shape's arc radius (row 5, R .0935) to .001: too
    small for its chord. Returns the table."""
    win.show_screen(1)
    table = win.screens[1].table
    arc_row = next(r for r in range(table.rowCount()) if table.cellWidget(r, 0).currentText() == "arc")
    table.item(arc_row, 5).setText("0.001")
    return table, arc_row


def test_invalid_shape_blocks_f5_and_f6_and_fixing_it_restores_them(win, said):
    """The six steps from the brief: valid shape, an edit makes it invalid,
    the job still has the old one, F5 and F6 refuse with a clear reason, and
    correcting the edit makes them work again."""
    win.open_job(EX / "manual_g75.toml")
    valid = copy.deepcopy(win.job.profiles["od"])
    table, arc_row = _make_shape_invalid(win)
    assert win.job.profiles["od"] == valid  # the job still holds the last valid shape
    assert win.screens[1].alert.isVisible() and "isn't valid" in win.screens[1].alert.text()
    for key, screen in ((Qt.Key_F5, 4), (Qt.Key_F6, 5)):
        _press(win, key)
        assert win.stack.currentIndex() == screen
        assert win.generate() is None and "Shape 'od' on F2 isn't valid" in win.last_error
    status = win.screens[5].status.text()  # rich text: the apostrophe is escaped
    assert win.screens[5].code is None and "No program was made" in status and "Undo changes" in status
    _press(win, Qt.Key_F2)
    table.item(arc_row, 5).setText("0.0935")  # put it right
    assert not win.screens[1].alert.isVisible()
    _press(win, Qt.Key_F6)
    assert win.screens[5].code is not None and "g02x.406z-.487r.0935" in win.screens[5].code


def test_invalid_shape_edit_survives_visiting_other_screens(win, said):
    """Coming back to F2 must show the edit, not quietly reload the old shape."""
    win.open_job(EX / "manual_g75.toml")
    table, arc_row = _make_shape_invalid(win)
    _press(win, Qt.Key_F4)
    _press(win, Qt.Key_F2)
    assert table.item(arc_row, 5).text() == "0.001" and win.screens[1].alert.isVisible()


def test_invalid_shape_refuses_actions_that_would_throw_it_away_and_undo_restores(win, said):
    win.open_job(EX / "manual_g75.toml")
    table, arc_row = _make_shape_invalid(win)
    win.screens[1]._move(1)
    assert "Can't move rows" in said[-1] and table.item(arc_row, 5).text() == "0.001"
    win.screens[1].undo.click()
    assert table.item(arc_row, 5).text() == "0.0935" and not win.screens[1].alert.isVisible()
    assert win.generate() is not None


def test_saving_with_an_invalid_shape_asks_and_says_what_is_saved(win, said, tmp_path):
    win.open_job(EX / "manual_g75.toml")
    win.job_path = tmp_path / "x.toml"
    _make_shape_invalid(win)
    _press(win, Qt.Key_F7)
    assert "last valid shape" in said[-1] and not win.job_path.exists()  # answered no
    said.answer = True
    _press(win, Qt.Key_F7)
    assert "r = 0.0935" in win.job_path.read_text() and win.dirty  # the edit is still unsaved


def test_opening_another_job_clears_the_invalid_shape_state(win, said):
    win.open_job(EX / "manual_g75.toml")
    _make_shape_invalid(win)
    win.open_job(EX / "manual_g75.toml")
    win.show_screen(1)
    assert win.blocking_problems() == [] and not win.screens[1].alert.isVisible()


def test_unreadable_cell_names_its_row_and_column(win, said):
    win.open_job(EX / "manual_g75.toml")
    win.show_screen(1)
    win.screens[1].table.item(2, 2).setText("z?")
    assert "row 3 Z: 'z?' isn't a number" in win.screens[1].problem


# ---- C. the tool library dialog is a transaction
def _library_session(monkeypatch, script):
    """Run F8 with `script(dlg)` standing in for the operator while the
    dialog is open (exec() would wait for a real click)."""
    from omniturn_cam.gui.toollib import ToolLibraryDialog

    monkeypatch.setattr(ToolLibraryDialog, "exec", lambda dlg: script(dlg))
    monkeypatch.setattr(ToolLibraryDialog, "confirm_discard", lambda dlg: True)


def _pick_tool(dlg, tool_id):
    dlg.list.setCurrentRow([t.id for t in dlg.library].index(tool_id))


@pytest.fixture
def lib_copy(win, tmp_path):
    """Work on a copy of the example library so saves don't touch examples/."""
    import shutil

    path = tmp_path / "tools.toml"
    shutil.copy(EX / "tools.toml", path)
    win.library_path = path
    win.open_job(EX / "manual_g75.toml")
    return path


@pytest.mark.parametrize("how", ["close", "escape"])
def test_tool_edits_closed_without_saving_change_nothing(win, said, monkeypatch, lib_copy, how):
    """The finding: tools were edited in place, so the job's T1 kept a new
    nose radius after Close. Now the dialog edits a copy."""
    st = win.job.setup.stations[1]
    before, file_before = st.tool.nose_radius, lib_copy.read_text()

    def operator(dlg):
        _pick_tool(dlg, st.tool.id)
        _type_into(dlg.nose.edit, "0.5")
        dlg.list.setCurrentRow(0 if dlg.list.currentRow() else 1)  # commit the form to the copy
        if how == "close":
            dlg.reject()  # the Close button's slot
        else:
            QTest.keyClick(dlg, Qt.Key_Escape)
        return QDialog.Rejected

    _library_session(monkeypatch, operator)
    win.edit_library()
    assert win.job.setup.stations[1].tool.nose_radius == before
    assert win.library[st.tool.id].nose_radius == before and lib_copy.read_text() == file_before


def test_reopening_after_cancel_shows_the_original_tool(win, said, monkeypatch, lib_copy):
    tid = win.job.setup.stations[1].tool.id
    seen = []

    def edit_and_cancel(dlg):
        _pick_tool(dlg, tid)
        dlg.nose.setValue(0.5)
        dlg._commit()
        dlg.reject()

    def look(dlg):
        _pick_tool(dlg, tid)
        seen.append(dlg.nose.value())
        dlg.reject()

    _library_session(monkeypatch, edit_and_cancel)
    win.edit_library()
    _library_session(monkeypatch, look)
    win.edit_library()
    assert seen == [0.0156]


def test_saving_tool_edits_updates_the_file_and_the_job(win, said, monkeypatch, lib_copy):
    """An in-use tool's nose radius changed and saved: the job's station
    uses the saved tool, and it's in tools.toml."""
    tid = win.job.setup.stations[1].tool.id
    monkeypatch.setattr("omniturn_cam.gui.toollib.QMessageBox.information", lambda *a: None)

    def operator(dlg):
        _pick_tool(dlg, tid)
        _type_into(dlg.nose.edit, "0.031")
        assert dlg._save()
        dlg.reject()

    _library_session(monkeypatch, operator)
    win.edit_library()
    assert win.job.setup.stations[1].tool.nose_radius == 0.031
    assert win.job.setup.stations[1].tool is win.library[tid]
    from omniturn_cam.tools import ToolLibrary

    assert ToolLibrary.load(lib_copy)[tid].nose_radius == 0.031


def test_edits_after_a_save_and_then_cancelled_are_not_kept(win, said, monkeypatch, lib_copy):
    tid = win.job.setup.stations[1].tool.id
    monkeypatch.setattr("omniturn_cam.gui.toollib.QMessageBox.information", lambda *a: None)

    def operator(dlg):
        _pick_tool(dlg, tid)
        dlg.nose.setValue(0.031)
        dlg._save()
        dlg.nose.setValue(0.9)
        dlg._commit()
        dlg.reject()

    _library_session(monkeypatch, operator)
    win.edit_library()
    assert win.job.setup.stations[1].tool.nose_radius == 0.031


@pytest.mark.parametrize(
    "new_id, words",
    [("", "needs an ID"), ("drill-250", "duplicate tool id"), ("renamed", "loaded in the current job")],
    ids=["blank", "duplicate", "in-use"],
)
def test_invalid_tool_id_is_refused_and_the_list_stays_on_that_tool(win, said, monkeypatch, lib_copy, new_id, words):
    """A refused edit used to leave the list highlighting the tool clicked
    next while the form still showed the one being edited. The in-use case
    edits the tool on T1; the others a tool that isn't on the plate."""
    on_plate = {st.tool.id for st in win.job.setup.stations.values()}
    tid = (
        win.job.setup.stations[1].tool.id
        if new_id == "renamed"
        else next(t.id for t in win.library if t.id not in on_plate and t.id != "drill-250")
    )
    warned = []
    monkeypatch.setattr("omniturn_cam.gui.toollib.QMessageBox.warning", lambda _p, _t, text: warned.append(text))
    state = {}

    def operator(dlg):
        _pick_tool(dlg, tid)
        row = dlg.list.currentRow()
        dlg.id.setText(new_id)
        dlg.list.setCurrentRow(row + 1)  # try to move on
        state["row"], state["expected"], state["form"] = dlg.list.currentRow(), row, dlg.id.text()
        dlg.reject()

    _library_session(monkeypatch, operator)
    win.edit_library()
    assert words in warned[-1]
    assert state["row"] == state["expected"] and state["form"] == new_id
    assert win.library[tid].id == tid


def test_tool_library_save_failure_keeps_the_edits_and_the_file(win, said, monkeypatch, lib_copy):
    tid = win.job.setup.stations[1].tool.id
    warned = []
    monkeypatch.setattr("omniturn_cam.gui.toollib.QMessageBox.warning", lambda _p, _t, text: warned.append(text))
    before = lib_copy.read_text()

    def operator(dlg):
        _pick_tool(dlg, tid)
        dlg.nose.setValue(0.031)
        # A "folder" that is really a file: the save can't create it, even
        # for root (a read-only folder wouldn't stop root, who runs CI here).
        blocker = lib_copy.parent / "not-a-folder"
        blocker.write_text("")
        dlg.path = blocker / "tools.toml"
        assert not dlg._save()
        assert dlg.dirty and dlg.saved is None
        dlg.reject()

    _library_session(monkeypatch, operator)
    win.edit_library()
    assert "Could not save" in warned[-1] and lib_copy.read_text() == before
    assert win.job.setup.stations[1].tool.nose_radius == 0.0156


# ---- D. malformed job files are messages, not tracebacks
HEAD = '[job]\nname = "BAD"\n[stock]\ndiameter = 1.0\n'


@pytest.mark.parametrize(
    "text, words",
    [
        (
            HEAD + '[[op]]\ntype = "drill"\nt = 2\nrpm = 1000\nz = -0.5\nfeeed = 0.003\n',
            ["operation 1 (drill)", "'feeed'", "did you mean 'feed'"],
        ),
        (HEAD + "[profile.od]\nstart = { x = 0, z = 0 }\nseg = [1]\n", ["shape 'od' segment 1 must be a table"]),
        ('station = "T1"\n' + HEAD, ["station must be a list"]),  # top-level keys come before [tables] in TOML
        ("op = [1]\n" + HEAD, ["operation 1 must be a table"]),
    ],
    ids=["unknown-op-field", "segment-not-a-table", "station-not-a-list", "op-not-a-table"],
)
def test_malformed_job_file_opens_with_a_message(win, said, tmp_path, text, words):
    """Each used to raise TypeError out of the GUI (or would have, for the
    unknown field, before #33). Now: which operation, which setting, what to do."""
    path = tmp_path / "bad.toml"
    path.write_text(text)
    win.open_job(EX / "manual_g75.toml")
    assert win.open_job(path) is False
    assert win.job.name == "MANG75"  # the open job is kept
    assert "Could not open bad.toml" in said[-1]
    for w in words:
        assert w in said[-1], said[-1]


def test_unreadable_job_path_is_a_message(win, said, tmp_path):
    assert win.open_job(tmp_path) is False and "Could not read" in said[-1]


def test_a_bug_while_opening_is_reported_as_one(win, said, tmp_path, monkeypatch, caplog):
    """An exception that isn't about the file's contents is a programming
    error: logged with its traceback and called an internal error, not
    passed off as a bad job file."""
    import omniturn_cam.gui.app as app_module

    def broken(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(app_module, "load_job", broken)
    assert win.open_job(EX / "manual_g75.toml") is False
    assert "problem in omniturn-cam itself" in said[-1] and "RuntimeError: boom" in said[-1]
    assert any(r.exc_info and r.exc_info[0] is RuntimeError for r in caplog.records)


def test_a_bug_while_generating_is_reported_not_raised(win, said, monkeypatch, caplog):
    import omniturn_cam.gui.app as app_module

    win.open_job(EX / "manual_g75.toml")
    monkeypatch.setattr(app_module, "post", lambda *a, **k: 1 / 0)
    assert win.generate() is None and "internal error" in win.last_error
    assert any(r.exc_info and r.exc_info[0] is ZeroDivisionError for r in caplog.records)


# ---- E. saving fails cleanly
def test_save_failure_keeps_job_dirty_flag_and_old_file(win, said, tmp_path, monkeypatch):
    """A disk that fails mid-write (fsync raising, as a pulled stick does):
    the old file is untouched, no temporary file is left, the job stays
    unsaved and the operator is told."""
    win.open_job(EX / "manual_g75.toml")
    folder = tmp_path / "stick"
    folder.mkdir()
    win.job_path = folder / "job.toml"
    win.job_path.write_text("old contents")
    win.job.description = "changed"
    win.touched()

    def pulled(_fd):
        raise OSError(5, "Input/output error")

    monkeypatch.setattr("omniturn_cam.fileio.os.fsync", pulled)
    assert win.save() is False
    assert win.job_path.read_text() == "old contents"
    assert sorted(p.name for p in folder.iterdir()) == ["job.toml"]
    assert win.dirty and win.job.description == "changed" and "Could not save" in said[-1]


def test_save_as_failure_leaves_the_job_on_its_old_file(win, said, tmp_path, monkeypatch):
    win.open_job(EX / "manual_g75.toml")
    old = win.job_path
    target = tmp_path / "no-such-folder" / "new.toml"
    monkeypatch.setattr(
        "omniturn_cam.gui.app.QFileDialog.getSaveFileName", staticmethod(lambda *a, **k: (str(target), ""))
    )
    assert win.save_as() is False
    assert win.job_path == old and not target.exists() and "Could not save" in said[-1]


def test_save_as_success_moves_the_job_to_the_new_file(win, said, tmp_path, monkeypatch):
    win.open_job(EX / "manual_g75.toml")
    target = tmp_path / "new"
    monkeypatch.setattr(
        "omniturn_cam.gui.app.QFileDialog.getSaveFileName", staticmethod(lambda *a, **k: (str(target), ""))
    )
    assert win.save_as() is True
    assert win.job_path == tmp_path / "new.toml" and win.job_path.exists() and not win.dirty


def test_program_write_failure_on_f6_is_a_message(win, said, tmp_path, monkeypatch):
    warned = []
    monkeypatch.setattr(
        "omniturn_cam.gui.screens.program.QMessageBox.warning", lambda _p, _t, text: warned.append(text)
    )
    win.open_job(EX / "manual_g75.toml")
    win.show_screen(5)
    assert win.screens[5]._write(tmp_path / "missing" / "MANG75.cnc") is False
    assert "Could not write" in warned[-1]


def test_program_written_with_dos_line_endings(win, said, tmp_path, monkeypatch):
    monkeypatch.setattr("omniturn_cam.gui.screens.program.QMessageBox.information", lambda *a: None)
    win.open_job(EX / "manual_g75.toml")
    win.show_screen(5)
    out = tmp_path / "MANG75.cnc"
    assert win.screens[5]._write(out)
    assert out.read_bytes().count(b"\r\n") == win.screens[5].code.count("\n")


# ---- F. other crash paths
def test_full_plate_add_station_warns(win, said, monkeypatch):
    from omniturn_cam.tools import Station

    warned = []
    monkeypatch.setattr("omniturn_cam.gui.screens.plate.QMessageBox.warning", lambda _p, _t, text: warned.append(text))
    tool = next(iter(win.library))
    for t in range(1, 33):
        win.job.setup.add(Station(t, tool))
    win.show_screen(2)
    win.screens[2]._add()
    assert "All 32 stations" in warned[-1] and len(win.job.setup.stations) == 32


def _fake_model(monkeypatch, import_model):
    import omniturn_cam.modelimport as mi

    body = type("Body", (), {"name": "Solid 1", "size": (10.0, 10.0, 20.0), "index": 0})()
    monkeypatch.setattr(mi, "list_bodies", lambda path: [body])
    monkeypatch.setattr(mi, "is_mesh", lambda path: False)
    monkeypatch.setattr(mi, "import_model", import_model)


def test_step_dialog_reports_an_unexpected_import_error(win, said, monkeypatch, caplog):
    """Only StepImportError was caught; a kernel failure took the dialog down."""
    from omniturn_cam.gui.stepdialog import StepImportDialog

    def kernel_fails(*a, **k):
        raise RuntimeError("Standard_Failure: BRep_API")

    _fake_model(monkeypatch, kernel_fails)
    dlg = StepImportDialog("part.step", win.job, win)
    assert dlg.import_result is None and not dlg.ok.isEnabled()
    assert any("Standard_Failure" in t.get_text() for ax in dlg.canvas.figure.axes for t in ax.texts)
    assert any(r.exc_info and r.exc_info[0] is RuntimeError for r in caplog.records)


def test_step_dialog_result_is_qdialogs_again(win, said, monkeypatch):
    """`dlg.result` was the import result, hiding QDialog.result() (the
    accepted/rejected code). Now import_result holds the import."""
    from omniturn_cam.gui.stepdialog import StepImportDialog
    from omniturn_cam.stepimport import StepImportError

    def not_turned(*a, **k):
        raise StepImportError("no round faces")

    _fake_model(monkeypatch, not_turned)
    dlg = StepImportDialog("part.step", win.job, win)
    dlg.reject()
    assert dlg.result() == QDialog.Rejected and dlg.import_result is None


# ---- G. the subroutine file path is saved as written
def test_relative_subroutine_path_round_trips_through_the_gui(win, said, tmp_path, monkeypatch):
    folder = tmp_path / "jobs"
    folder.mkdir()
    (folder / "subs.txt").write_text("}1\nm10\nm99\n")
    win.open_job(EX / "manual_g75.toml")
    win.job.include_subs = "subs.txt"
    win.job.base_dir = folder
    win._save_to(folder / "part.toml")
    assert 'include_subs = "subs.txt"' in (folder / "part.toml").read_text()
    win.open_job(folder / "part.toml")
    win.show_screen(0)
    assert win.screens[0].subs.text() == "subs.txt"
    assert win.generate() is not None and "\n}1\nm10\nm99" in win.generate()


# ---- metric (Stage 2): defaults and labels in the form
def test_metric_job_form_shows_metric_defaults_and_units(win, said):
    win.job.units = "metric"
    win.show_screen(2)
    win.screens[2]._add()
    win.show_screen(3)
    win.screens[3]._add("face")
    feed = win.screens[3].scroll.widget().widgets["feed"]
    assert feed.edit.text() == "0.08" and feed.suffix.text() == "mm/rev"
