"""The six step screens, in IGF order: job & blank, part shape, tool plate,
processes, check, program. Each screen edits MainWindow.job in place and calls
win.touched() after a change; refresh() reloads it from the job."""

from __future__ import annotations

import getpass
import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..backplot import draw, draw_profile
from ..fields import OP_MENU, OP_TOOL_KINDS, new_operation, summary
from ..geometry import Arc, Line, Point, Profile
from ..job import NAME_RE
from ..lint import lint
from ..post import PostError, post
from ..tools import Station
from .forms import OperationForm
from .widgets import UNIT_SUFFIX, NumberEdit, PlotCanvas, fmt_num


def _buttons(*specs):
    row = QHBoxLayout()
    out = []
    for text, slot in specs:
        b = QPushButton(text)
        b.clicked.connect(slot)
        row.addWidget(b)
        out.append(b)
    row.addStretch(1)
    return row, out


class Screen(QWidget):
    title = ""

    def __init__(self, win):
        super().__init__()
        self.win = win

    @property
    def job(self):
        return self.win.job

    def refresh(self):
        pass


# =========================================================================== 1
class JobScreen(Screen):
    title = "1  JOB & BLANK"

    def __init__(self, win):
        super().__init__(win)
        lay = QHBoxLayout(self)
        left = QVBoxLayout()
        lay.addLayout(left, 1)

        g = QGroupBox("Program")
        f = QFormLayout(g)
        self.name = QLineEdit()
        self.name.setMaxLength(8)
        self.name.setToolTip("Becomes the file name on the USB stick: 1-8 letters or digits (manual p2.8)")
        self.name.editingFinished.connect(self._name)
        self.desc = QLineEdit()
        self.desc.editingFinished.connect(lambda: self._set("description", self.desc.text()))
        self.units = QComboBox()
        self.units.addItems(["inch", "metric"])
        self.units.currentTextChanged.connect(self._units)
        f.addRow("Program name", self.name)
        f.addRow("Description", self.desc)
        f.addRow("Units", self.units)
        subrow = QHBoxLayout()
        self.subs = QLineEdit()
        self.subs.setPlaceholderText("none")
        self.subs.setToolTip(
            "A file of saved subroutines (e.g. ZipLoader / bar-load subs) copied in below M30. "
            "Generated contour subroutines are numbered around it."
        )
        self.subs.editingFinished.connect(lambda: self._set("include_subs", self.subs.text().strip() or None))
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_subs)
        subrow.addWidget(self.subs, 1)
        subrow.addWidget(browse)
        f.addRow("Subroutine file", subrow)
        self.units_note = QLabel("")
        self.units_note.setStyleSheet("color:#667")
        self.units_note.setWordWrap(True)
        f.addRow("", self.units_note)
        left.addWidget(g)

        g = QGroupBox("Blank (bar stock)")
        f = QFormLayout(g)
        self.material = QLineEdit()
        self.material.editingFinished.connect(lambda: self._stock("material", self.material.text()))
        self.dia = NumberEdit(optional=False)
        self.dia.valueChanged.connect(lambda v: self._stock("diameter", v))
        self.length = NumberEdit(optional=True, placeholder="optional")
        self.length.valueChanged.connect(lambda v: self._stock("length", v))
        self.bore = NumberEdit(optional=True, placeholder="none")
        self.bore.valueChanged.connect(lambda v: self._stock("bore", v))
        f.addRow("Material", self.material)
        f.addRow("Bar diameter", self.dia)
        f.addRow("Length out of collet", self.length)
        f.addRow("Existing bore", self.bore)
        left.addWidget(g)
        left.addStretch(1)

        right = QGroupBox("Program header preview")
        rl = QVBoxLayout(right)
        self.header = QPlainTextEdit()
        self.header.setReadOnly(True)
        self.header.setFont(QFont("monospace"))
        rl.addWidget(self.header)
        lay.addWidget(right, 1)

    def _set(self, attr, v):
        setattr(self.job, attr, v)
        self.win.touched()
        self._preview()

    def _browse_subs(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Subroutine file", str(Path.home()), "OmniTurn programs (*.cnc *.txt *);;All files (*)"
        )
        if path:
            self.subs.setText(path)
            self._set("include_subs", path)

    def _stock(self, attr, v):
        setattr(self.job.stock, attr, v)
        self.win.touched()

    def _name(self):
        txt = self.name.text().strip().upper()
        self.name.setText(txt)
        ok = bool(NAME_RE.fullmatch(txt))
        self.name.setStyleSheet("" if ok else "QLineEdit { border: 2px solid #d33; }")
        if ok and txt != self.job.name:
            self._set("name", txt)

    def _units(self, u):
        if u != self.job.units:
            self.job.units = u
            self.win.touched()
            self.refresh()

    def _preview(self):
        from ..post.omniturn import OmniTurnPost

        p = OmniTurnPost(self.job, self.win.machine)
        p.header()
        self.header.setPlainText("\n".join(p.lines))

    def refresh(self):
        j = self.job
        self.name.setText(j.name)
        self.desc.setText(j.description)
        self.subs.setText(j.include_subs or "")
        self.units.blockSignals(True)
        self.units.setCurrentText(j.units)
        self.units.blockSignals(False)
        suf = UNIT_SUFFIX[j.units]
        for w in (self.dia, self.bore):
            w.setSuffix(suf["dia"])
        self.length.setSuffix(suf["len"])
        self.dia.setValue(j.stock.diameter)
        self.length.setValue(j.stock.length)
        self.bore.setValue(j.stock.bore)
        self.material.setText(j.stock.material)
        self.units_note.setText("Changing units relabels fields; it does not convert numbers already entered.")
        self._preview()


# =========================================================================== 2
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
        from ..modelimport import MODEL_FILTER

        path, _ = QFileDialog.getOpenFileName(
            self, "Import shape from a 3D model", start, MODEL_FILTER + ";;All files (*)"
        )
        if not path:
            return
        self.win.settings.setValue("step_dir", str(Path(path).parent))
        try:
            from .stepdialog import StepImportDialog

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
class PlateScreen(Screen):
    title = "3  TOOL PLATE"

    def __init__(self, win):
        super().__init__(win)
        lay = QVBoxLayout(self)
        info = QLabel(
            "Which tool is in which station for this job. D is the secondary offset that "
            "holds the tool's nose radius for G41/G42 comp."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color:#667")
        lay.addWidget(info)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["T", "Tool", "D", "Kind", "Mounting", "Nose R / dia"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        lay.addWidget(self.table, 1)
        row, _ = _buttons(
            ("+ Station", self._add),
            ("Remove station", self._remove),
            ("Edit tool library… (F8)", self.win.edit_library),
        )
        lay.addLayout(row)

    def refresh(self):
        stations = sorted(self.job.setup.stations.values(), key=lambda s: s.t)
        self.table.setRowCount(len(stations))
        tools = list(self.win.library)
        for r, st in enumerate(stations):
            t = NumberEdit(st.t, integer=True)
            t.valueChanged.connect(lambda v, st=st: self._renumber(st, v))
            self.table.setCellWidget(r, 0, t)
            cb = QComboBox()
            for tool in tools:
                cb.addItem(f"{tool.name}   ({tool.id})", tool.id)
            cb.setCurrentIndex(max(0, cb.findData(st.tool.id)))
            cb.currentIndexChanged.connect(lambda _i, cb=cb, st=st: self._tool(st, cb.currentData()))
            self.table.setCellWidget(r, 1, cb)
            d = NumberEdit(st.d, optional=True, integer=True, placeholder="none")
            d.valueChanged.connect(lambda v, st=st: self._d(st, v))
            self.table.setCellWidget(r, 2, d)
            size = st.tool.diameter if st.tool.diameter else st.tool.nose_radius
            for c, txt in ((3, st.tool.kind), (4, st.tool.orientation), (5, fmt_num(size))):
                it = QTableWidgetItem(txt)
                it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(r, c, it)
        self.table.resizeRowsToContents()

    def _add(self):
        if not len(self.win.library):
            QMessageBox.warning(self, "Tool plate", "The tool library is empty - add tools first (F8).")
            return
        used = set(self.job.setup.stations)
        t = next(n for n in range(1, 33) if n not in used)
        self.job.setup.add(Station(t, next(iter(self.win.library))))
        self.win.touched()
        self.refresh()

    def _remove(self):
        r = self.table.currentRow()
        if r < 0:
            return
        t = sorted(self.job.setup.stations)[r]
        users = [i + 1 for i, op in enumerate(self.job.operations) if op.t == t]
        if (
            users
            and QMessageBox.question(self, "Remove station", f"T{t} is used by process(es) {users}. Remove anyway?")
            != QMessageBox.Yes
        ):
            return
        del self.job.setup.stations[t]
        self.win.touched()
        self.refresh()

    def _renumber(self, st, v):
        if v is None or not 1 <= v <= 32 or v in self.job.setup.stations:
            self.refresh()
            return
        old = st.t
        del self.job.setup.stations[old]
        st.t = v
        self.job.setup.stations[v] = st
        for op in self.job.operations:
            if op.t == old:
                op.t = v
        self.win.touched()
        self.refresh()

    def _tool(self, st, tool_id):
        st.tool = self.win.library[tool_id]
        self.win.touched()
        self.refresh()

    def _d(self, st, v):
        st.d = v
        self.win.touched()


# =========================================================================== 4
class ProcessScreen(Screen):
    title = "4  PROCESSES"

    def __init__(self, win):
        super().__init__(win)
        lay = QHBoxLayout(self)
        left = QVBoxLayout()
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._select)
        left.addWidget(self.list, 1)
        add = QPushButton("+ Add process")
        menu = QMenu(add)
        for kind, (name, desc) in OP_MENU.items():
            act = menu.addAction(f"{name}  -  {desc}")
            act.triggered.connect(lambda _c=False, k=kind: self._add(k))
        add.setMenu(menu)
        left.addWidget(add)
        row, _ = _buttons(
            ("Duplicate", self._dup),
            ("Delete", self._delete),
            ("Up", lambda: self._move(-1)),
            ("Down", lambda: self._move(1)),
        )
        left.addLayout(row)
        lw = QWidget()
        lw.setLayout(left)
        lw.setMaximumWidth(460)
        lay.addWidget(lw, 2)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        lay.addWidget(self.scroll, 3)

    def refresh(self, select=None):
        row = self.list.currentRow() if select is None else select
        self.list.blockSignals(True)
        self.list.clear()
        for i, op in enumerate(self.job.operations, 1):
            QListWidgetItem(f"{i:>2}.  {summary(op, self.job.units)}", self.list)
        self.list.blockSignals(False)
        if self.job.operations:
            self.list.setCurrentRow(min(max(row, 0), len(self.job.operations) - 1))
            self._select(self.list.currentRow())
        else:
            empty = QLabel("No processes yet. Press  + Add process.")
            empty.setAlignment(Qt.AlignCenter)
            self.scroll.setWidget(empty)

    def _select(self, row):
        if not 0 <= row < len(self.job.operations):
            return
        form = OperationForm(self.job.operations[row], self.job)
        form.changed.connect(lambda: self._edited(row))
        self.scroll.setWidget(form)

    def _edited(self, row):
        self.win.touched()
        item = self.list.item(row)
        if item:
            item.setText(f"{row + 1:>2}.  {summary(self.job.operations[row], self.job.units)}")

    def _add(self, kind):
        from ..ops import OPERATION_TYPES

        stations = sorted(self.job.setup.stations.values(), key=lambda s: s.t)
        if not OPERATION_TYPES[kind].uses_tool:
            op = OPERATION_TYPES[kind]()
            at = self.list.currentRow() + 1 if self.list.currentRow() >= 0 else len(self.job.operations)
            self.job.operations.insert(at, op)
            self.win.touched()
            self.refresh(select=at)
            return
        if not stations:
            QMessageBox.warning(self, "Add process", "Load some tools on the plate first (F3).")
            return
        wanted = OP_TOOL_KINDS.get(kind, ())
        st = next((s for k in wanted for s in stations if s.tool.kind == k), stations[0])
        overrides = {}
        if kind in ("rough", "finish") and self.job.profiles:
            overrides["profile"] = sorted(self.job.profiles)[0]
        if kind == "cutoff":
            overrides["x_start"] = None
        op = new_operation(kind, st.t, st.tool, **overrides)
        at = self.list.currentRow() + 1 if self.list.currentRow() >= 0 else len(self.job.operations)
        self.job.operations.insert(at, op)
        self.win.touched()
        self.refresh(select=at)

    def _dup(self):
        import copy

        r = self.list.currentRow()
        if r >= 0:
            self.job.operations.insert(r + 1, copy.deepcopy(self.job.operations[r]))
            self.win.touched()
            self.refresh(select=r + 1)

    def _delete(self):
        r = self.list.currentRow()
        if r >= 0:
            del self.job.operations[r]
            self.win.touched()
            self.refresh(select=r - 1)

    def _move(self, d):
        r = self.list.currentRow()
        ops = self.job.operations
        if 0 <= r < len(ops) and 0 <= r + d < len(ops):
            ops[r], ops[r + d] = ops[r + d], ops[r]
            self.win.touched()
            self.refresh(select=r + d)


# =========================================================================== 5
class CheckScreen(Screen):
    title = "5  CHECK"

    def __init__(self, win):
        super().__init__(win)
        lay = QHBoxLayout(self)
        self.canvas = PlotCanvas()
        lay.addWidget(self.canvas, 3)
        right = QVBoxLayout()
        right.addWidget(QLabel("<b>Rules check</b>"))
        self.msgs = QListWidget()
        right.addWidget(self.msgs, 1)
        legend = QLabel(
            "Backplot: feeds solid (colour per tool), rapids dotted, grey band = "
            "shape the G75/G78 cycle roughs to, shaded = bar stock.\n\n"
            "This is a preview of the code, not a simulation of the machine. "
            "Always run F4 Verify on the OmniTurn and single-block new programs."
        )
        legend.setWordWrap(True)
        legend.setStyleSheet("color:#667")
        right.addWidget(legend)
        rw = QWidget()
        rw.setLayout(right)
        lay.addWidget(rw, 2)

    def _msg(self, text, color):
        it = QListWidgetItem(text, self.msgs)
        it.setForeground(QColor(color))

    def refresh(self):
        self.msgs.clear()
        code = self.win.generate()
        if code is None:
            self._msg(self.win.last_error, "#b00020")
            self.canvas.message(self.win.last_error)
            return
        probs = lint(code, self.job.name + ".cnc")
        for w in self.win.warnings:
            self._msg(f"warning: {w}", "#a66b00")
        for p in probs:
            self._msg(str(p), "#b00020" if p.severity == "error" else "#a66b00")
        if not probs:
            self._msg("All OmniTurn formatting rules pass", "#2e7d32")
        n = code.count("\n")
        self._msg(f"{n} lines, {len(self.job.operations)} processes, {len(self.job.setup.stations)} tools", "#334")
        ax = self.canvas.clear()
        draw(ax, code, self.job.stock.diameter)
        self.canvas.draw_idle()


# =========================================================================== 6
def usb_mounts() -> list[Path]:
    user = os.environ.get("USER") or getpass.getuser()
    roots = [Path("/media") / user, Path("/run/media") / user, Path("/media")]
    found = []
    for root in roots:
        if root.is_dir():
            for p in sorted(root.iterdir()):
                if p.is_dir() and os.path.ismount(p) and p not in found:
                    found.append(p)
    return found


class ProgramScreen(Screen):
    title = "6  PROGRAM"

    def __init__(self, win):
        super().__init__(win)
        lay = QVBoxLayout(self)
        self.status = QLabel("")
        lay.addWidget(self.status)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        f = QFont("monospace")
        f.setStyleHint(QFont.Monospace)
        f.setPointSize(11)
        self.text.setFont(f)
        lay.addWidget(self.text, 1)
        row, _ = _buttons(
            ("Save to USB stick", self._usb), ("Save program as…", self._save_as), ("Copy to clipboard", self._copy)
        )
        lay.addLayout(row)
        self.code = None

    def refresh(self):
        self.code = self.win.generate()
        if self.code is None:
            self.text.setPlainText("")
            self.status.setText(f"<span style='color:#b00020'>{self.win.last_error}</span>")
        else:
            self.text.setPlainText(self.code)
            self.status.setText(
                f"<b>{self.job.name}.cnc</b> &nbsp; {self.code.count(chr(10))} lines - "
                "prove out with F4 Verify and single block before running."
            )

    def _write(self, path: Path):
        path.write_text(self.code.replace("\n", "\r\n"))  # DOS line endings for the OmniTurn
        QMessageBox.information(self, "Saved", f"Wrote {path}")

    def _usb(self):
        if not self.code:
            return
        mounts = usb_mounts()
        if len(mounts) == 1:
            target = mounts[0] / f"{self.job.name}.cnc"
            if (
                target.exists()
                and QMessageBox.question(self, "Save to USB", f"{target.name} already exists on the stick. Replace it?")
                != QMessageBox.Yes
            ):
                return
            self._write(target)
        else:
            start = str(mounts[0] if mounts else Path.home())
            self._save_as(start)

    def _save_as(self, start=None):
        if not self.code:
            return
        start = start or str(Path.home())
        path, _ = QFileDialog.getSaveFileName(
            self, "Save program", f"{start}/{self.job.name}.cnc", "OmniTurn program (*.cnc *.txt)"
        )
        if path:
            self._write(Path(path))

    def _copy(self):
        if self.code:
            from PySide6.QtWidgets import QApplication

            QApplication.clipboard().setText(self.code)
