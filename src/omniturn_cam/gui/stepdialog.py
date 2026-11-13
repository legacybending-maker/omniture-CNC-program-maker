"""F2 -> Import from STEP: pick the solid, pick which end is the face, preview."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from ..backplot import draw_profile
from .widgets import PlotCanvas, fmt_num


class StepImportDialog(QDialog):
    def __init__(self, path: str, job, parent=None):
        super().__init__(parent)
        from ..modelimport import is_mesh, list_bodies

        self.setWindowTitle(f"Import shape - {Path(path).name}")
        self.resize(1000, 640)
        self.path, self.job = path, job
        self.result = None
        self.solids = list_bodies(path)
        self.mesh = is_mesh(path)

        lay = QHBoxLayout(self)
        left = QVBoxLayout()
        form = QFormLayout()
        self.solid = QComboBox()
        for s in self.solids:
            dims = " x ".join(f"{v:.1f}" for v in s.size)
            self.solid.addItem(f"{s.name}   {dims} mm", s.index)
        self.solid.setEnabled(len(self.solids) > 1)
        form.addRow("Object" if self.mesh else "Solid", self.solid)
        flip_row = QHBoxLayout()
        self.flip = QCheckBox("Z0 at the other end")
        flip_btn = QPushButton("Flip ⇄")
        flip_btn.clicked.connect(lambda: self.flip.setChecked(not self.flip.isChecked()))
        flip_row.addWidget(self.flip)
        flip_row.addWidget(flip_btn)
        form.addRow("Face end", flip_row)
        self.info = QLabel("")
        form.addRow("Part", self.info)
        self.set_stock = QCheckBox()
        form.addRow("Bar diameter", self.set_stock)
        self.extend = QCheckBox("Run the OD shape out to the bar diameter at the back")
        self.extend.setChecked(True)
        self.extend.setToolTip("G75/G78 need the contour to end at the bar so the area to rough is closed")
        form.addRow("", self.extend)
        left.addLayout(form)
        left.addWidget(QLabel("<b>Notes</b>"))
        self.notes = QListWidget()
        self.notes.setWordWrap(True)
        left.addWidget(self.notes, 1)
        self.replace_note = QLabel("")
        self.replace_note.setWordWrap(True)
        self.replace_note.setStyleSheet("color:#a66b00")
        left.addWidget(self.replace_note)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Import shapes")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        left.addWidget(bb)
        self.ok = bb.button(QDialogButtonBox.Ok)
        lay.addLayout(left, 2)

        self.canvas = PlotCanvas()
        lay.addWidget(self.canvas, 3)

        self.solid.currentIndexChanged.connect(lambda _i: self._run())
        self.flip.toggled.connect(lambda _b: self._run())
        self._run()

    def _run(self):
        from ..modelimport import import_model
        from ..stepimport import StepImportError

        self.notes.clear()
        try:
            self.result = import_model(self.path, self.solid.currentData(), self.job.units, self.flip.isChecked())
        except StepImportError as e:
            self.result = None
            self.canvas.message(str(e))
            self.ok.setEnabled(False)
            return
        r = self.result
        self.ok.setEnabled(True)
        unit = "in" if self.job.units == "inch" else "mm"
        self.info.setText(
            f"{fmt_num(r.max_dia)} {unit} max dia  x  {fmt_num(r.length)} {unit} long"
            + ("  -  has a bore" if r.id else "")
        )
        bigger = r.max_dia > self.job.stock.diameter + 1e-9
        self.set_stock.setText(f"Set to {fmt_num(r.max_dia)} (now {fmt_num(self.job.stock.diameter)})")
        self.set_stock.setChecked(bigger)
        for w in r.warnings:
            QListWidgetItem("⚠ " + w, self.notes)
        if not r.warnings:
            QListWidgetItem("Clean turned part: every face is round on one axis or square to it.", self.notes)
        taken = [n for n in ("od", "id") if n in self.job.profiles and (n == "od" or r.id)]
        self.replace_note.setText(f"Replaces the existing shape(s): {', '.join(taken)}" if taken else "")
        ax = self.canvas.clear()
        draw_profile(ax, r.od, max(r.max_dia, self.job.stock.diameter))
        if r.id:
            pts = r.id.polyline()
            ax.plot([p[0] for p in pts], [p[1] / 2 for p in pts], lw=2, color="#b03a9a")
        ax.axvline(0, color="#2e9e5b", lw=1, ls="--")
        ax.set_title("OD blue   ID purple   Z0 face green", fontsize=9)
        self.canvas.draw_idle()

    def apply(self):
        """Put the imported shapes into the job. Returns the names added."""
        from ..stepimport import extend_to_stock

        r = self.result
        if self.set_stock.isChecked():
            self.job.stock.diameter = r.max_dia
        if self.extend.isChecked():
            extend_to_stock(r.od, self.job.stock.diameter)
        self.job.profiles["od"] = r.od
        names = ["od"]
        if r.id:
            self.job.profiles["id"] = r.id
            names.append("id")
        return names
