"""Base class and helpers shared by the step screens."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QHBoxLayout,
    QPushButton,
    QWidget,
)


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

    def blocking_problem(self) -> str | None:
        """An edit on this screen that couldn't go into the job (so the job
        isn't what the screen shows), or None. MainWindow refuses to make a
        program while any screen has one. Screens that can hold such an edit
        override this (ShapeScreen)."""
        return None


# =========================================================================== 1
