"""Small reusable widgets: numeric entry with blanks, embedded matplotlib canvas."""
from __future__ import annotations

import matplotlib
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QWidget

matplotlib.rcParams.update({"font.size": 8, "legend.fontsize": 8})

UNIT_SUFFIX = {
    "inch": {"dia": "in dia", "z": "in", "len": "in", "feed": "in/rev", "feed_ipm": "in/min",
             "rpm": "rpm", "sfm": "sfm", "sec": "sec"},
    "metric": {"dia": "mm dia", "z": "mm", "len": "mm", "feed": "mm/rev", "feed_ipm": "mm/min",
               "rpm": "rpm", "sfm": "m/min", "sec": "sec"},
}


def fmt_num(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer() and abs(v) >= 1:
        return str(int(v))
    return f"{v:.6g}"


class NumberEdit(QWidget):
    """A line edit for a number. Blank means None (automatic) when optional."""

    valueChanged = Signal(object)

    def __init__(self, value=None, optional=False, integer=False, suffix="", placeholder="", parent=None):
        super().__init__(parent)
        self.optional, self.integer = optional, integer
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.edit = QLineEdit(fmt_num(value))
        self.edit.setPlaceholderText(placeholder or ("auto" if optional else ""))
        self.edit.setMinimumWidth(90)
        lay.addWidget(self.edit, 1)
        self.suffix = QLabel(suffix)
        self.suffix.setMinimumWidth(56)
        self.suffix.setStyleSheet("color: #667;")
        lay.addWidget(self.suffix)
        self.edit.editingFinished.connect(self._commit)
        self._last = value

    def value(self):
        txt = self.edit.text().strip()
        if not txt:
            return None
        v = float(txt)
        return int(round(v)) if self.integer else v

    def setValue(self, v):
        self._last = v
        self.edit.setText(fmt_num(v))
        self._mark(True)

    def setSuffix(self, s):
        self.suffix.setText(s)

    def _mark(self, ok: bool):
        self.edit.setStyleSheet("" if ok else "QLineEdit { border: 2px solid #d33; }")

    def _commit(self):
        try:
            v = self.value()
            if v is None and not self.optional:
                raise ValueError("required")
        except ValueError:
            self._mark(False)
            return
        self._mark(True)
        if v != self._last:
            self._last = v
            self.valueChanged.emit(v)


class PlotCanvas(FigureCanvasQTAgg):
    def __init__(self, parent=None):
        self.fig = Figure(figsize=(6, 3.5), tight_layout=True)
        super().__init__(self.fig)
        self.setParent(parent)
        self.ax = self.fig.add_subplot(111)

    def clear(self):
        self.fig.clear()
        self.ax = self.fig.add_subplot(111)
        return self.ax

    def message(self, text: str, color="#b00"):
        ax = self.clear()
        ax.axis("off")
        ax.text(0.5, 0.5, text, ha="center", va="center", color=color, wrap=True, transform=ax.transAxes)
        self.draw_idle()
