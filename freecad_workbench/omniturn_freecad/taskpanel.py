"""Task panel for creating / editing a turned profile.

Edits go straight into the object's properties and recompute it, so the 3D
view is the preview. OK keeps the change, Cancel rolls it back (and removes a
new profile) through the document's undo transaction.
"""

from __future__ import annotations

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtGui

try:
    from PySide import QtWidgets
except ImportError:  # older FreeCAD shims put the widgets in QtGui
    QtWidgets = QtGui

from .feature import MM_PER_INCH, _solids

PARAMS = "User parameter:BaseApp/Preferences/Mod/OmniTurnCAM"


class ProfileTaskPanel:
    def __init__(self, obj, new: bool = False):
        self.obj = obj
        self.new = new
        self.doc = obj.Document
        if not new:
            self.doc.openTransaction("Edit turned profile")
        self.form = self._build()
        self.form.setWindowTitle("Turned profile")
        self._load()
        self._refresh()

    # ------------------------------------------------------------------ UI
    def _build(self):
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        form = QtWidgets.QFormLayout()
        lay.addLayout(form)

        self.source = QtWidgets.QLabel()
        form.addRow("Part:", self.source)
        self.solid = QtWidgets.QComboBox()
        form.addRow("Solid:", self.solid)
        self.units = QtWidgets.QComboBox()
        self.units.addItems(["inch", "metric"])
        form.addRow("Units:", self.units)
        self.flip = QtWidgets.QCheckBox("Face (Z0) at the other end")
        form.addRow("", self.flip)
        self.stock = QtWidgets.QDoubleSpinBox()
        self.stock.setDecimals(4)
        self.stock.setRange(0, 100000)
        self.stock.setSpecialValueText("part max dia")
        form.addRow("Bar dia:", self.stock)
        self.runout = QtWidgets.QCheckBox("Run the OD out to the bar at the back")
        form.addRow("", self.runout)

        self.summary = QtWidgets.QLabel()
        lay.addWidget(self.summary)
        self.warnings = QtWidgets.QLabel()
        self.warnings.setWordWrap(True)
        self.warnings.setStyleSheet("color: #b35900")
        lay.addWidget(self.warnings)
        self.text = QtWidgets.QPlainTextEdit()
        self.text.setReadOnly(True)
        font = QtGui.QFont("monospace")
        font.setStyleHint(QtGui.QFont.TypeWriter)
        self.text.setFont(font)
        self.text.setMinimumHeight(220)
        lay.addWidget(self.text)
        hint = QtWidgets.QLabel(
            "Orange = OD, blue = ID, drawn on the part where it was cut. "
            "Use Save to job file to send it to omniturn-cam."
        )
        hint.setWordWrap(True)
        lay.addWidget(hint)
        return w

    def _load(self):
        o = self.obj
        self.source.setText(o.Source.Label if o.Source else "-")
        self.solid.blockSignals(True)
        try:
            solids = _solids(o)
        except Exception:
            solids = []
        for i, s in enumerate(solids):
            bb = s.BoundBox
            self.solid.addItem(f"{i + 1}: {bb.XLength:.1f} x {bb.YLength:.1f} x {bb.ZLength:.1f} mm", i)
        self.solid.setCurrentIndex(max(0, min(o.SolidIndex, len(solids) - 1)))
        self.solid.setEnabled(len(solids) > 1)
        self.solid.blockSignals(False)
        self.units.setCurrentText(o.Units)
        self.flip.setChecked(o.Flip)
        self.runout.setChecked(o.RunOutToStock)
        self._set_stock_units()
        self.solid.currentIndexChanged.connect(self._changed)
        self.units.currentIndexChanged.connect(self._units_changed)
        self.flip.toggled.connect(self._changed)
        self.runout.toggled.connect(self._changed)
        self.stock.editingFinished.connect(self._changed)

    def _k(self):
        return MM_PER_INCH if self.units.currentText() == "inch" else 1.0

    def _set_stock_units(self):
        self.stock.blockSignals(True)
        self.stock.setSuffix(" in" if self.units.currentText() == "inch" else " mm")
        self.stock.setValue(self.obj.StockDiameter.Value / self._k())
        self.stock.blockSignals(False)

    # ------------------------------------------------------------------ changes
    def _units_changed(self):
        self._set_stock_units()
        self._changed()

    def _changed(self, *_):
        o = self.obj
        o.SolidIndex = self.solid.currentData() or 0
        o.Units = self.units.currentText()
        o.Flip = self.flip.isChecked()
        o.RunOutToStock = self.runout.isChecked()
        o.StockDiameter = self.stock.value() * self._k()
        o.recompute()
        self._refresh()

    def _refresh(self):
        o = self.obj
        u = o.Units
        k = MM_PER_INCH if u == "inch" else 1.0
        unit = '"' if u == "inch" else " mm"
        if o.OD:
            self.summary.setText(
                f"{o.MaxDiameter.Value / k:.4f}{unit} max dia x {o.PartLength.Value / k:.4f}{unit} long"
            )
        else:
            self.summary.setText("No profile")
        self.warnings.setText("\n".join("• " + w for w in o.Warnings))
        self.warnings.setVisible(bool(o.Warnings))
        text = ["OD"] + ["  " + s for s in o.OD]
        if o.ID:
            text += ["", "ID"] + ["  " + s for s in o.ID]
        self.text.setPlainText("\n".join(text) if o.OD else "")

    # ------------------------------------------------------------------ dialog
    def getStandardButtons(self):
        return QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel

    def accept(self):
        App.ParamGet(PARAMS).SetString("Units", self.obj.Units)
        self.doc.commitTransaction()
        Gui.ActiveDocument.resetEdit()
        self.doc.recompute()
        return True

    def reject(self):
        self.doc.abortTransaction()
        Gui.ActiveDocument.resetEdit()
        self.doc.recompute()
        return True
