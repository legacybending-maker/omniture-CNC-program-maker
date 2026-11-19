"""F2 Part shape: the OD/ID profile table and its preview.

Invalid edits (issue #38)
-------------------------
The table is what the operator types; ``job.profiles`` is what the program is
made from. They are the same only while the table holds a valid shape. When
an edit makes it invalid (an arc too small for its chord, a letter where a
number goes), the table can't be turned into a Profile, so the job still has
the last valid one. That gap used to be invisible: F6 made a program from a
shape that was no longer on screen.

Now the gap is explicit state: ``self.problem`` says what's wrong while the
table and the job differ. While it is set:

* a red notice above the table says the program uses neither, and an
  "Undo changes" button puts the last valid shape back in the table;
* ``blocking_problem()`` returns it, so MainWindow makes no program (F5, F6)
  and asks before saving the last valid shape (F7);
* the table is kept as typed when the operator visits other screens, and
  actions that would reload it from the job (another shape, New, Rename,
  Delete, Import, Move) are refused rather than throwing the edit away;
* the preview shows the problem, not the old shape.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...backplot import draw_profile
from ...geometry import Arc, Line, Point, Profile
from ..widgets import UNIT_SUFFIX, EditTable, NumberEdit, PlotCanvas, fmt_num
from .base import Screen, _buttons

log = logging.getLogger("omniturn_cam.gui")

COLS = ["Type", "X (dia)", "Z", "Corner", "Size", "Arc R", "Arc dir"]


class ShapeScreen(Screen):
    title = "2  PART SHAPE"

    def __init__(self, win):
        super().__init__(win)
        self.current: str | None = None
        self._loading = False
        # What's wrong with the table while it differs from the job's shape
        # (see the module docstring), and which job it belongs to: a new or
        # opened job starts clean.
        self.problem: str | None = None
        self._problem_job = None
        lay = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(QLabel("Shape"))
        self.pick = QComboBox()
        self.pick.setMinimumWidth(140)
        self.pick.currentTextChanged.connect(self._pick)
        top.addWidget(self.pick)
        for text, slot in (("New", self._new), ("Rename", self._rename), ("Delete", self._delete)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            top.addWidget(b)
        top.addSpacing(20)
        top.addWidget(QLabel("Side"))
        self.side = QComboBox()
        self.side.addItems(["od", "id"])
        self.side.currentTextChanged.connect(self._side)
        top.addWidget(self.side)
        top.addSpacing(20)
        top.addWidget(QLabel("Start X"))
        self.sx = NumberEdit(label="Shape start X")
        self.sx.valueChanged.connect(lambda _v: self._rebuild())
        top.addWidget(self.sx)
        top.addWidget(QLabel("Start Z"))
        self.sz = NumberEdit(label="Shape start Z")
        self.sz.valueChanged.connect(lambda _v: self._rebuild())
        top.addWidget(self.sz)
        top.addStretch(1)
        b = QPushButton("Import from model…")
        b.setToolTip("Pick a STEP, STL or 3MF file of a turned part; its OD and bore become shapes")
        b.clicked.connect(self._import_step)
        top.addWidget(b)
        lay.addLayout(top)

        split = QSplitter(Qt.Horizontal)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        alert_row = QHBoxLayout()
        self.alert = QLabel("")
        self.alert.setWordWrap(True)
        self.alert.setStyleSheet("background:#b00020; color:white; padding:6px; font-weight:600;")
        alert_row.addWidget(self.alert, 1)
        self.undo = QPushButton("Undo changes")
        self.undo.setToolTip("Put the last valid shape back in the table")
        self.undo.clicked.connect(self._revert)
        alert_row.addWidget(self.undo)
        ll.addLayout(alert_row)
        self.alert.hide()
        self.undo.hide()
        # EditTable (widgets.py): a cell still being typed in is committed
        # before any F-key, like every other field.
        self.table = EditTable(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.itemChanged.connect(lambda _i: self._rebuild())
        self.table.currentCellChanged.connect(lambda *_: self._draw())
        ll.addWidget(self.table)
        row, _ = _buttons(
            ("+ Line", lambda: self._add("line")),
            ("+ Arc", lambda: self._add("arc")),
            ("Delete row", self._del_row),
            ("Move up", lambda: self._move(-1)),
            ("Move down", lambda: self._move(1)),
        )
        ll.addLayout(row)
        hint = QLabel(
            "X is diameter. Leave X or Z blank to keep the previous value. Corner C/R blends "
            "into the next line, like the OmniTurn's own C and R words. Arc direction is as "
            "seen on the print: face on the right, X+ up."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#667")
        ll.addWidget(hint)
        split.addWidget(left)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.canvas = PlotCanvas()
        rl.addWidget(self.canvas, 1)
        self.msg = QLabel("")
        self.msg.setWordWrap(True)
        rl.addWidget(self.msg)
        split.addWidget(right)
        split.setSizes([560, 520])
        lay.addWidget(split, 1)

    # ---------------------------------------------------------------- table <-> profile
    def _combo(self, items, value):
        cb = QComboBox()
        cb.addItems(items)
        cb.setCurrentText(value)
        cb.currentTextChanged.connect(lambda _t: self._rebuild())
        return cb

    def _row_of(self, widget):
        return next(r for r in range(self.table.rowCount()) if self.table.cellWidget(r, 0) is widget)

    def _row_enable(self, r):
        """Grey out the cells that don't apply to a line or an arc row."""
        is_arc = self.table.cellWidget(r, 0).currentText() == "arc"
        self.table.cellWidget(r, 3).setEnabled(not is_arc)
        self.table.cellWidget(r, 6).setEnabled(is_arc)
        for c, on in ((4, not is_arc), (5, is_arc)):
            it = self.table.item(r, c)
            if it is not None:
                flags = it.flags() | Qt.ItemIsEnabled if on else it.flags() & ~Qt.ItemIsEnabled
                it.setFlags(flags)

    def _set_row(self, r, kind, x=None, z=None, corner="", size=None, arc_r=None, arc_dir="ccw"):
        self._loading = True
        self.table.setCellWidget(r, 0, self._combo(["line", "arc"], kind))
        for c, v in ((1, x), (2, z), (4, size), (5, arc_r)):
            self.table.setItem(r, c, QTableWidgetItem(fmt_num(v)))
        self.table.setCellWidget(r, 3, self._combo(["", "C", "R"], corner))
        self.table.setCellWidget(r, 6, self._combo(["ccw", "cw"], arc_dir))
        type_cb = self.table.cellWidget(r, 0)
        type_cb.currentTextChanged.connect(lambda _t, cb=type_cb: self._row_enable(self._row_of(cb)))
        self._row_enable(r)
        self._loading = False

    def _cell(self, r, c):
        """The number in a cell; None if blank. Raises ValueError naming the
        row and column for anything else, so the notice says where to look."""
        it = self.table.item(r, c)
        txt = it.text().strip() if it else ""
        if not txt:
            return None
        try:
            v = float(txt)
        except ValueError:
            raise ValueError(f"row {r + 1} {COLS[c]}: {txt!r} isn't a number") from None
        if not math.isfinite(v):
            raise ValueError(f"row {r + 1} {COLS[c]}: {txt!r} isn't a finite number")
        return v

    def _rebuild(self):
        if self._loading or self.current is None:
            return
        try:
            sx, sz = self.sx.value(), self.sz.value()
            prof = Profile(Point(sx or 0.0, sz or 0.0), name=self.current, side=self.side.currentText())
            for r in range(self.table.rowCount()):
                last = prof.end
                kind = self.table.cellWidget(r, 0).currentText()
                x = self._cell(r, 1)
                z = self._cell(r, 2)
                end = Point(last.x if x is None else x, last.z if z is None else z)
                if kind == "arc":
                    rad = self._cell(r, 5)
                    if rad is None:
                        raise ValueError(f"row {r + 1}: arc needs a radius")
                    prof.segments.append(Arc(end, rad, self.table.cellWidget(r, 6).currentText()))
                else:
                    corner = self.table.cellWidget(r, 3).currentText()
                    size = self._cell(r, 4)
                    prof.segments.append(Line(end, size if corner == "C" else None, size if corner == "R" else None))
            prof.validate()
        except ValueError as e:
            self._set_problem(str(e))
            # The table differs from the saved job now, whatever the job holds.
            self.win.touched()
            return
        self.job.profiles[self.current] = prof
        self._set_problem(None)
        self.msg.setText(f"<span style='color:#2e7d32'>Shape OK - {len(prof.segments)} elements</span>")
        self.win.touched()
        self._draw()

    # ---------------------------------------------------------------- invalid-edit state
    def _set_problem(self, problem: str | None) -> None:
        self.problem = problem
        self._problem_job = self.job if problem else None
        if problem:
            self.alert.setText(
                f"This shape isn't valid: {problem}. Until it is, F5 and F6 make no program "
                f"(the job still holds the last valid shape, not this one)."
            )
            self.msg.setText(f"<span style='color:#b00'>{problem}</span>")
            self.canvas.message(f"{problem}\n\nNot drawn: fix the table, or Undo changes.")
        self.alert.setVisible(bool(problem))
        self.undo.setVisible(bool(problem))

    def _pending(self) -> bool:
        """True while the table holds an invalid edit for the current job."""
        return self.problem is not None and self._problem_job is self.job

    def blocking_problem(self) -> str | None:
        if not self._pending():
            return None
        return (
            f"Shape {self.current!r} on F2 isn't valid: {self.problem}. The table shows your edit, "
            "but the job still holds the last valid shape. Fix the table, or press Undo changes on F2."
        )

    def _refuse_while_invalid(self, action: str) -> bool:
        """True (and the operator told why) if ``action`` would reload the
        table from the job and so throw the invalid edit away."""
        if not self._pending():
            return False
        self.win.notify(
            "Part shape",
            f"Can't {action} while shape {self.current!r} has an edit that isn't valid:\n\n"
            f"{self.problem}\n\nFix it, or press Undo changes to go back to the last valid shape.",
        )
        return True

    def _revert(self):
        """Undo changes: the table goes back to the job's (last valid) shape."""
        self._set_problem(None)
        self._load(self.current)
        self.msg.setText("<span style='color:#2e7d32'>Back to the last valid shape</span>")

    def _load(self, name):
        self.current = name
        prof = self.job.profiles.get(name)
        self.table.setRowCount(0)
        if prof is None:
            self._draw()
            return
        self.side.blockSignals(True)
        self.side.setCurrentText(prof.side)
        self.side.blockSignals(False)
        self.sx.setValue(prof.start.x)
        self.sz.setValue(prof.start.z)
        self.table.setRowCount(len(prof.segments))
        for r, seg in enumerate(prof.segments):
            if isinstance(seg, Arc):
                self._set_row(r, "arc", seg.end.x, seg.end.z, arc_r=seg.radius, arc_dir=seg.direction)
            else:
                corner = "C" if seg.chamfer else "R" if seg.radius else ""
                self._set_row(r, "line", seg.end.x, seg.end.z, corner, seg.chamfer or seg.radius)
        self._draw()

    def _draw(self):
        if self._pending():
            return  # the problem is on the canvas; the old shape isn't what's on screen
        prof = self.job.profiles.get(self.current) if self.current else None
        if prof is None:
            self.canvas.message("No shape yet - press New", "#667")
            return
        ax = self.canvas.clear()
        try:
            draw_profile(ax, prof, self.job.stock.diameter, self.table.currentRow())
        except Exception as e:  # geometry still being typed
            self.canvas.message(str(e))
            return
        self.canvas.draw_idle()

    # ---------------------------------------------------------------- actions
    def _pick(self, name):
        if name and name != self.current:
            if self._refuse_while_invalid("switch to another shape"):
                self.pick.blockSignals(True)
                self.pick.setCurrentText(self.current)
                self.pick.blockSignals(False)
                return
            self._load(name)

    def _side(self, _s):
        self._rebuild()

    def _import_step(self):
        if self._refuse_while_invalid("import shapes"):
            return
        start = self.win.settings.value("step_dir", str(Path.home()))
        from ...modelimport import MODEL_FILTER

        path, _ = QFileDialog.getOpenFileName(
            self, "Import shape from a 3D model", start, MODEL_FILTER + ";;All files (*)"
        )
        if not path:
            return
        self.win.settings.setValue("step_dir", str(Path(path).parent))
        try:
            from ..stepdialog import StepImportDialog

            dlg = StepImportDialog(path, self.job, self)
        except Exception as e:  # missing kernel, unreadable or unsupported file
            log.warning("opening %s for import failed", path, exc_info=True)
            QMessageBox.critical(self, "Import shape", f"Couldn't open {Path(path).name} for import:\n{e}")
            return
        if dlg.exec() == QDialog.Accepted and dlg.import_result is not None:
            names = dlg.apply()
            self.win.touched()
            self.refresh(select=names[0])

    def _new(self):
        if self._refuse_while_invalid("start a new shape"):
            return
        name, ok = QInputDialog.getText(self, "New shape", "Shape name (e.g. od, bore):")
        if ok and name.strip():
            name = name.strip()
            if name in self.job.profiles:
                QMessageBox.warning(self, "New shape", f"A shape called {name} already exists.")
                return
            self.job.profiles[name] = Profile(Point(0, 0), name=name)
            self.win.touched()
            self.refresh(select=name)

    def _rename(self):
        if not self.current or self._refuse_while_invalid("rename the shape"):
            return
        name, ok = QInputDialog.getText(self, "Rename shape", "New name:", text=self.current)
        name = name.strip()
        if ok and name and name != self.current and name not in self.job.profiles:
            self.job.profiles[name] = self.job.profiles.pop(self.current)
            self.job.profiles[name].name = name
            for op in self.job.operations:
                if getattr(op, "profile", None) == self.current:
                    op.profile = name
            self.win.touched()
            self.refresh(select=name)

    def _delete(self):
        if not self.current or self._refuse_while_invalid("delete the shape"):
            return
        users = [i + 1 for i, op in enumerate(self.job.operations) if getattr(op, "profile", None) == self.current]
        if users:
            QMessageBox.warning(self, "Delete shape", f"Shape is used by process(es) {users}.")
            return
        del self.job.profiles[self.current]
        self.current = None
        self.win.touched()
        self.refresh()

    def _add(self, kind):
        if self.current is None:
            self._new()
            if self.current is None:
                return
        r = self.table.currentRow() + 1 if self.table.currentRow() >= 0 else self.table.rowCount()
        self.table.insertRow(r)
        self._set_row(r, kind)
        self.table.setCurrentCell(r, 1)
        self._rebuild()

    def _del_row(self):
        r = self.table.currentRow()
        if r >= 0:
            self.table.removeRow(r)
            self._rebuild()

    def _move(self, d):
        # Moving swaps segments in the job's shape and reloads the table.
        if self._refuse_while_invalid("move rows"):
            return
        prof = self.job.profiles.get(self.current)
        r = self.table.currentRow()
        if prof is None or not (0 <= r < len(prof.segments)) or not (0 <= r + d < len(prof.segments)):
            return
        prof.segments[r], prof.segments[r + d] = prof.segments[r + d], prof.segments[r]
        self._load(self.current)
        self.table.setCurrentCell(r + d, 1)
        self._rebuild()

    def refresh(self, select=None):
        if self._problem_job is not None and self._problem_job is not self.job:
            self._set_problem(None)  # a different job was opened: its shapes start clean
        if self._pending() and select in (None, self.current):
            # Coming back to F2 with an invalid edit: keep the table as typed.
            # Reloading it from the job would quietly throw the edit away.
            suf = UNIT_SUFFIX[self.job.units]
            self.sx.setSuffix(suf["dia"])
            self.sz.setSuffix(suf["z"])
            return
        names = sorted(self.job.profiles)
        self.pick.blockSignals(True)
        self.pick.clear()
        self.pick.addItems(names)
        self.pick.blockSignals(False)
        target = select or (self.current if self.current in names else (names[0] if names else None))
        if target:
            self.pick.setCurrentText(target)
        self._load(target)
        suf = UNIT_SUFFIX[self.job.units]
        self.sx.setSuffix(suf["dia"])
        self.sz.setSuffix(suf["z"])


# =========================================================================== 3
