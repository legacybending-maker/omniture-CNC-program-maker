"""Small reusable widgets: numeric entry with blanks, committing line edits and
tables, embedded matplotlib canvas.

Committing edits (issue #38)
----------------------------
A Qt line edit only tells the program its text changed when editing
*finishes*: Return, or focus moving to another widget. The F-key buttons
never take focus (so the operator's cursor stays where it was), so pressing
F6 straight after typing a feed used to make the program from the old feed
while the field showed the new one.

So every editor that can hold typed-but-not-yet-used text has a
``commit_edit()`` method: push what's on screen into the job now, or say why
it can't (``None`` means fine, a string is the problem in operator words).
``MainWindow.commit_edits()`` finds every widget on the current screen that
has one and calls it before anything that reads the job (F5, F6, F7, F8,
changing screen, rebuilding a form). It's *duck typing*: the window doesn't
care what class a widget is, only whether it has a ``commit_edit`` method,
so a new kind of editor joins in by defining one.
"""

from __future__ import annotations

import math
from collections.abc import Callable

import matplotlib
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QAbstractItemDelegate,
    QAbstractItemView,
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QTableWidget,
    QWidget,
)

BAD_FIELD = "QLineEdit { border: 2px solid #d33; }"

matplotlib.rcParams.update({"font.size": 8, "legend.fontsize": 8})

UNIT_SUFFIX = {
    "inch": {
        "dia": "in dia",
        "z": "in",
        "len": "in",
        "feed": "in/rev",
        "feed_ipm": "in/min",
        "rpm": "rpm",
        "sfm": "sfm",
        "sec": "sec",
    },
    "metric": {
        "dia": "mm dia",
        "z": "mm",
        "len": "mm",
        "feed": "mm/rev",
        "feed_ipm": "mm/min",
        "rpm": "rpm",
        "sfm": "m/min",
        "sec": "sec",
    },
}


def fmt_num(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer() and abs(v) >= 1:
        return str(int(v))
    return f"{v:.6g}"


class NumberEdit(QWidget):
    """A line edit for a number. Blank means None (automatic) when optional.

    ``valueChanged`` is emitted with the new value when a valid, different
    number is committed: on Return, when focus leaves, or when
    ``commit_edit()`` is called. An invalid entry is marked red and never
    reaches the job; ``commit_edit()`` returns what's wrong with it.

    ``label`` names the field in messages ("Feed"); set it when the field
    sits somewhere its label doesn't make obvious.
    """

    valueChanged = Signal(object)

    def __init__(
        self, value=None, optional=False, integer=False, suffix="", placeholder="", parent=None, label: str = ""
    ):
        super().__init__(parent)
        self.optional, self.integer, self.label = optional, integer, label
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
        self.edit.editingFinished.connect(self.commit_edit)
        self._last = value

    def problem(self) -> str | None:
        """What's wrong with the text as typed, or None if it's a usable value."""
        txt = self.edit.text().strip()
        if not txt:
            return None if self.optional else "can't be blank"
        try:
            v = float(txt)
        except ValueError:
            return f"{txt!r} isn't a number"
        if not math.isfinite(v):
            return f"{txt!r} isn't a finite number"
        if self.integer and not v.is_integer():
            # It used to be rounded: T1.5 quietly became T2. An operator's
            # number is never changed without telling them.
            return f"{txt} must be a whole number"
        return None

    def value(self):
        """The typed value: None for blank, an int for whole-number fields.

        Raises ValueError (with problem()'s text) if the text isn't usable."""
        problem = self.problem()
        if problem:
            raise ValueError(problem)
        txt = self.edit.text().strip()
        if not txt:
            return None
        v = float(txt)
        return int(v) if self.integer else v

    def setValue(self, v):
        self._last = v
        self.edit.setText(fmt_num(v))
        self._mark(True)

    def setSuffix(self, s):
        self.suffix.setText(s)

    def _mark(self, ok: bool):
        self.edit.setStyleSheet("" if ok else BAD_FIELD)

    def commit_edit(self) -> str | None:
        """Push the typed value into the job (via valueChanged) if it's valid
        and different. Returns None when the field is fine, or the problem."""
        problem = self.problem()
        if problem:
            self._mark(False)
            return f"{self.label}: {problem}" if self.label else problem
        self._mark(True)
        v = self.value()
        if v != self._last:
            self._last = v
            self.valueChanged.emit(v)
        return None


class CommitLineEdit(QLineEdit):
    """A text field that hands its text on through ``committed`` when editing
    finishes or ``commit_edit()`` is called, if the text changed and passes
    ``check`` (a function returning a problem string, or None if fine).

    Set the text from the job with ``setCommitted()`` so the field knows that
    text is already in the job and doesn't report it as a change.
    """

    committed = Signal(str)

    def __init__(self, text: str = "", check: Callable[[str], str | None] | None = None, label: str = "", parent=None):
        super().__init__(text, parent)
        self.check, self.label = check, label
        self._last = text
        self.editingFinished.connect(self.commit_edit)

    def setCommitted(self, text: str) -> None:
        self._last = text
        self.setText(text)
        self.setStyleSheet("")

    def commit_edit(self) -> str | None:
        text = self.text()
        problem = self.check(text) if self.check else None
        if problem:
            self.setStyleSheet(BAD_FIELD)
            return f"{self.label}: {problem}" if self.label else problem
        self.setStyleSheet("")
        if text != self._last:
            self._last = text
            self.committed.emit(text)
        return None


class EditTable(QTableWidget):
    """A table whose open cell editor can be committed on demand.

    While a cell is being typed in, the text lives in a temporary editor
    widget, not in the cell; the table copies it in when the editor closes.
    ``commit_edit()`` closes it now, through the same ``commitData`` /
    ``closeEditor`` calls the table makes itself when focus leaves, so the
    cell (and whatever listens to ``itemChanged``) sees the typed value.
    Those two are *protected* in Qt (only the table itself may call them),
    which is why this is a subclass rather than a helper function.
    """

    def commit_edit(self) -> str | None:
        if self.state() != QAbstractItemView.EditingState:
            return None
        editor = QApplication.focusWidget()
        if editor is not None and self.viewport().isAncestorOf(editor):
            self.commitData(editor)
            self.closeEditor(editor, QAbstractItemDelegate.NoHint)
        return None


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
