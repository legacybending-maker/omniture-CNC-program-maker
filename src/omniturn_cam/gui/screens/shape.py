"""F2 Part shape: the OD/ID profile table and its preview."""

from __future__ import annotations

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
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...backplot import draw_profile
from ...geometry import Arc, Line, Point, Profile
from ..widgets import UNIT_SUFFIX, NumberEdit, PlotCanvas, fmt_num
from .base import Screen, _buttons

COLS = ["Type", "X (dia)", "Z", "Corner", "Size", "Arc R", "Arc dir"]


class ShapeScreen(Screen):
    title = "2  PART SHAPE"

    def __init__(self, win):
        super().__init__(win)
        self.current: str | None = None
        self._loading = False
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
        self.sx = NumberEdit()
        self.sx.valueChanged.connect(lambda _v: self._rebuild())
        top.addWidget(self.sx)
        top.addWidget(QLabel("Start Z"))
        self.sz = NumberEdit()
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
        self.table = QTableWidget(0, len(COLS))
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
        it = self.table.item(r, c)
        txt = it.text().strip() if it else ""
        return float(txt) if txt else None

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
            self.msg.setText(f"<span style='color:#b00'>{e}</span>")
            return
        self.job.profiles[self.current] = prof
        self.msg.setText(f"<span style='color:#2e7d32'>Shape OK - {len(prof.segments)} elements</span>")
        self.win.touched()
        self._draw()

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
            self._load(name)

    def _side(self, _s):
        self._rebuild()

    def _import_step(self):
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
        except Exception as e:  # missing OCP, unreadable file
            QMessageBox.critical(self, "Import shape", str(e))
            return
        if dlg.exec() == QDialog.Accepted and dlg.result is not None:
            names = dlg.apply()
            self.win.touched()
            self.refresh(select=names[0])

    def _new(self):
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
        if not self.current:
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
        if not self.current:
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
        prof = self.job.profiles.get(self.current)
        r = self.table.currentRow()
        if prof is None or not (0 <= r < len(prof.segments)) or not (0 <= r + d < len(prof.segments)):
            return
        prof.segments[r], prof.segments[r + d] = prof.segments[r + d], prof.segments[r]
        self._load(self.current)
        self.table.setCurrentCell(r + d, 1)
        self._rebuild()

    def refresh(self, select=None):
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
