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
