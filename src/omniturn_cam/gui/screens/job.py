"""F1 Job & blank: program name, units, bar stock, include file."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from ...job import NAME_RE
from ..widgets import UNIT_SUFFIX, CommitLineEdit, NumberEdit
from .base import Screen


def _name_problem(text: str) -> str | None:
    if NAME_RE.fullmatch(text.strip().upper()):
        return None
    return f"{text.strip()!r} must be 1-8 letters, digits, _ or - (it becomes the file name, manual p2.8)"


class JobScreen(Screen):
    title = "1  JOB & BLANK"

    def __init__(self, win):
        super().__init__(win)
        lay = QHBoxLayout(self)
        left = QVBoxLayout()
        lay.addLayout(left, 1)

        g = QGroupBox("Program")
        f = QFormLayout(g)
        # CommitLineEdit (widgets.py): committed before any F-key, so a name
        # typed and not yet "finished" still reaches the job. A name that
        # isn't valid is refused there, with this message, instead of being
        # left red on screen while the job keeps the old one.
        self.name = CommitLineEdit(check=_name_problem, label="Program name")
        self.name.setMaxLength(8)
        self.name.setToolTip("Becomes the file name on the USB stick: 1-8 letters or digits (manual p2.8)")
        self.name.committed.connect(self._name)
        self.desc = CommitLineEdit(label="Description")
        self.desc.committed.connect(lambda text: self._set("description", text))
        self.units = QComboBox()
        self.units.addItems(["inch", "metric"])
        self.units.currentTextChanged.connect(self._units)
        f.addRow("Program name", self.name)
        f.addRow("Description", self.desc)
        f.addRow("Units", self.units)
        subrow = QHBoxLayout()
        self.subs = CommitLineEdit(label="Subroutine file")
        self.subs.setPlaceholderText("none")
        self.subs.setToolTip(
            "A file of saved subroutines (e.g. ZipLoader / bar-load subs) copied in below M30. "
            "Generated contour subroutines are numbered around it."
        )
        self.subs.committed.connect(lambda text: self._set("include_subs", text.strip() or None))
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
        self.material = CommitLineEdit(label="Material")
        self.material.committed.connect(lambda text: self._stock("material", text))
        self.dia = NumberEdit(optional=False, label="Bar diameter")
        self.dia.valueChanged.connect(lambda v: self._stock("diameter", v))
        self.length = NumberEdit(optional=True, placeholder="optional", label="Length out of collet")
        self.length.valueChanged.connect(lambda v: self._stock("length", v))
        self.bore = NumberEdit(optional=True, placeholder="none", label="Existing bore")
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
            self.subs.setCommitted(path)
            self._set("include_subs", path)

    def _stock(self, attr, v):
        setattr(self.job.stock, attr, v)
        self.win.touched()

    def _name(self, text):
        txt = text.strip().upper()
        self.name.setCommitted(txt)  # shown in capitals, as the file will be named
        if txt != self.job.name:
            self._set("name", txt)

    def _units(self, u):
        if u != self.job.units:
            # refresh() below reloads every field from the job, so anything
            # typed and not yet committed goes in first (or the change waits).
            if not self.win.commit_edits():
                self.units.blockSignals(True)
                self.units.setCurrentText(self.job.units)
                self.units.blockSignals(False)
                return
            self.job.units = u
            self.win.touched()
            self.refresh()

    def _preview(self):
        from ...post.omniturn import MachineConfigError, OmniTurnPost

        try:
            p = OmniTurnPost(self.job, self.win.machine)
        except MachineConfigError as e:
            self.header.setPlainText(self.win.machine_problem(e))
            return
        p.header()
        self.header.setPlainText("\n".join(p.lines))

    def refresh(self):
        j = self.job
        self.name.setCommitted(j.name)
        self.desc.setCommitted(j.description)
        self.subs.setCommitted(j.include_subs or "")
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
        self.material.setCommitted(j.stock.material)
        self.units_note.setText(
            "Changing units relabels fields; it does not convert numbers already entered. "
            "Processes added after the change start with values for the new units."
        )
        self._preview()


# =========================================================================== 2
