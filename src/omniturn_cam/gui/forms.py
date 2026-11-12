"""Builds the fill-in-the-blank form for one operation from fields.py metadata."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QGroupBox, QLabel, QLineEdit,
                               QVBoxLayout, QWidget)

from ..fields import OP_MENU, form_fields, op_kind
from .widgets import UNIT_SUFFIX, NumberEdit

GROUP_ORDER = ["Tool", "Shape", "Cut", "Speed"]
NUMERIC = {"dia", "z", "len", "feed", "feed_ipm", "rpm", "sfm", "sec", "text_num", "int"}


class OperationForm(QWidget):
    """Edits an Operation in place; emits changed() after every committed edit."""

    changed = Signal()

    def __init__(self, op, job, parent=None):
        super().__init__(parent)
        self.op, self.job = op, job
        units = UNIT_SUFFIX[job.units]
        outer = QVBoxLayout(self)
        kind = op_kind(op)
        title = QLabel(f"<b>{OP_MENU[kind][0]}</b> &nbsp; <span style='color:#667'>{OP_MENU[kind][1]}</span>")
        title.setWordWrap(True)
        outer.addWidget(title)

        groups: dict[str, QFormLayout] = {}
        boxes = {}
        for g in GROUP_ORDER:
            box = QGroupBox(g)
            groups[g] = QFormLayout(box)
            boxes[g] = box
        self.widgets = {}
        for name, info in form_fields(op):
            w = self._widget(name, info, units)
            w.setToolTip(info.help)
            lab = QLabel(info.label)
            lab.setToolTip(info.help)
            groups.get(info.group, groups["Cut"]).addRow(lab, w)
            self.widgets[name] = w
        for g in GROUP_ORDER:
            if groups[g].rowCount():
                outer.addWidget(boxes[g])
        outer.addStretch(1)

    def _set(self, name, value):
        setattr(self.op, name, value)
        self.changed.emit()

    def _widget(self, name, info, units):
        v = getattr(self.op, name)
        if info.kind == "station":
            cb = QComboBox()
            for t, st in sorted(self.job.setup.stations.items()):
                cb.addItem(f"T{t}  {st.tool.name}", t)
            if v not in self.job.setup.stations:
                cb.addItem(f"T{v}  (empty station)", v)
            cb.setCurrentIndex(cb.findData(v))
            cb.currentIndexChanged.connect(lambda _i, cb=cb: self._set(name, cb.currentData()))
            return cb
        if info.kind == "profile":
            cb = QComboBox()
            cb.addItems(sorted(self.job.profiles) or ["od"])
            if cb.findText(v) < 0:
                cb.addItem(v)
            cb.setCurrentText(v)
            cb.currentTextChanged.connect(lambda txt: self._set(name, txt))
            return cb
        if info.kind == "choice":
            cb = QComboBox()
            cb.addItems(list(info.choices))
            cb.setCurrentText(str(v))
            cb.currentTextChanged.connect(lambda txt: self._set(name, txt))
            return cb
        if info.kind == "bool":
            ch = QCheckBox()
            ch.setChecked(bool(v))
            ch.toggled.connect(lambda on: self._set(name, on))
            return ch
        if info.kind in NUMERIC:
            ne = NumberEdit(v, optional=info.optional, integer=info.kind in ("rpm", "int"),
                            suffix=units.get(info.kind, ""), placeholder=info.blank if info.optional else "")
            ne.valueChanged.connect(lambda val: self._set(name, val))
            return ne
        le = QLineEdit(str(v or ""))
        le.editingFinished.connect(lambda le=le: self._set(name, le.text()))
        return le
