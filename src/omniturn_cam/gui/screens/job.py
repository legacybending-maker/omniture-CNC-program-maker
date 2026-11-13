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
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from ...job import NAME_RE
from ..widgets import UNIT_SUFFIX, NumberEdit
from .base import Screen


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
        from ...post.omniturn import OmniTurnPost

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
