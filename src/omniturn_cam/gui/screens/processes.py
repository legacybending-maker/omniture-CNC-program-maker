"""F4 Processes: the ordered operation list and the fill-in-the-blank form."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...fields import OP_MENU, OP_TOOL_KINDS, new_operation, summary
from ..forms import OperationForm
from .base import Screen, _buttons


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
        self._form_row: int | None = None  # the process whose form is showing

    def refresh(self, select=None):
        row = self.list.currentRow() if select is None else select
        self.list.blockSignals(True)
        self.list.clear()
        for i, op in enumerate(self.job.operations, 1):
            QListWidgetItem(f"{i:>2}.  {summary(op, self.job.units)}", self.list)
        self.list.blockSignals(False)
        self._form_row = None  # the job (or its list) was rebuilt: no form to keep
        if self.job.operations:
            self.list.setCurrentRow(min(max(row, 0), len(self.job.operations) - 1))
            self._select(self.list.currentRow())
        else:
            empty = QLabel("No processes yet. Press  + Add process.")
            empty.setAlignment(Qt.AlignCenter)
            self.scroll.setWidget(empty)

    def _select(self, row):
        # The form for the process being left is about to be thrown away:
        # what's typed in it goes into the job first. If a field can't be
        # used, stay on that process (the list goes back to it) so the
        # operator can fix it rather than lose it.
        if self._form_row is not None and self._form_row != row and not self.win.commit_edits():
            self.list.blockSignals(True)
            self.list.setCurrentRow(self._form_row)
            self.list.blockSignals(False)
            return
        if not 0 <= row < len(self.job.operations):
            return
        form = OperationForm(self.job.operations[row], self.job)
        form.changed.connect(lambda: self._edited(row))
        self.scroll.setWidget(form)
        self._form_row = row

    def _committed(self) -> bool:
        """Commit the open form before a list change that rebuilds it."""
        return self.win.commit_edits()

    def _edited(self, row):
        self.win.touched()
        item = self.list.item(row)
        if item:
            item.setText(f"{row + 1:>2}.  {summary(self.job.operations[row], self.job.units)}")

    def _add(self, kind):
        from ...operations import OPERATION_TYPES

        if not self._committed():
            return
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
        op = new_operation(kind, st.t, st.tool, units=self.job.units, **overrides)
        at = self.list.currentRow() + 1 if self.list.currentRow() >= 0 else len(self.job.operations)
        self.job.operations.insert(at, op)
        self.win.touched()
        self.refresh(select=at)

    def _dup(self):
        import copy

        if not self._committed():
            return
        r = self.list.currentRow()
        if r >= 0:
            self.job.operations.insert(r + 1, copy.deepcopy(self.job.operations[r]))
            self.win.touched()
            self.refresh(select=r + 1)

    def _delete(self):
        r = self.list.currentRow()
        if r >= 0:
            # Nothing to commit: the form being deleted goes with its process.
            del self.job.operations[r]
            self.win.touched()
            self.refresh(select=r - 1)

    def _move(self, d):
        if not self._committed():
            return
        r = self.list.currentRow()
        ops = self.job.operations
        if 0 <= r < len(ops) and 0 <= r + d < len(ops):
            ops[r], ops[r + d] = ops[r + d], ops[r]
            self.win.touched()
            self.refresh(select=r + d)


# =========================================================================== 5
