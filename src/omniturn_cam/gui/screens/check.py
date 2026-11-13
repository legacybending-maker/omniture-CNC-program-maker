"""F5 Check: post the program, run the rules check, backplot."""

from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...backplot import draw
from ...lint import lint
from ..widgets import PlotCanvas
from .base import Screen


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
