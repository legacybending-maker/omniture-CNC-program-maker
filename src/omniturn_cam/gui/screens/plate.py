"""F3 Tool plate: which tool sits at which T number."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHeaderView,
    QLabel,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ...tools import Station
from ..widgets import NumberEdit, fmt_num
from .base import Screen, _buttons


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
            t = NumberEdit(st.t, integer=True, label=f"T{st.t} station number")
            t.valueChanged.connect(lambda v, st=st: self._renumber(st, v))
            self.table.setCellWidget(r, 0, t)
            cb = QComboBox()
            for tool in tools:
                cb.addItem(f"{tool.name}   ({tool.id})", tool.id)
            cb.setCurrentIndex(max(0, cb.findData(st.tool.id)))
            cb.currentIndexChanged.connect(lambda _i, cb=cb, st=st: self._tool(st, cb.currentData()))
            self.table.setCellWidget(r, 1, cb)
            d = NumberEdit(st.d, optional=True, integer=True, placeholder="none", label=f"T{st.t} D offset")
            d.valueChanged.connect(lambda v, st=st: self._d(st, v))
            self.table.setCellWidget(r, 2, d)
            size = st.tool.diameter if st.tool.diameter else st.tool.nose_radius
            for c, txt in ((3, st.tool.kind), (4, st.tool.orientation), (5, fmt_num(size))):
                it = QTableWidgetItem(txt)
                it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(r, c, it)
        self.table.resizeRowsToContents()

    def _add(self):
        if not self.win.commit_edits():  # refresh() below rebuilds the table
            return
        if not len(self.win.library):
            QMessageBox.warning(self, "Tool plate", "The tool library is empty - add tools first (F8).")
            return
        used = set(self.job.setup.stations)
        # next(..., None): the default makes next() return None when there's
        # no free number, instead of raising StopIteration (which used to
        # escape this button with all 32 stations in use).
        t = next((n for n in range(1, 33) if n not in used), None)
        if t is None:
            QMessageBox.warning(
                self,
                "Tool plate",
                "All 32 stations (T1-T32, manual p2.9) already have a tool. Remove one to add another.",
            )
            return
        self.job.setup.add(Station(t, next(iter(self.win.library))))
        self.win.touched()
        self.refresh()

    def _remove(self):
        if not self.win.commit_edits():
            return
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
            # Say why the number went back instead of quietly undoing it.
            why = "is already used" if v in self.job.setup.stations else "must be 1-32 (manual p2.9)"
            QMessageBox.warning(self, "Tool plate", f"T{st.t} can't become T{fmt_num(v)}: that number {why}.")
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
