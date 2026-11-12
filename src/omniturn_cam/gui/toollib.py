"""Tool library editor (F8)."""
from __future__ import annotations

import copy
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
                               QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from ..tools import KIND_DEFAULT_ORIENTATION, Tool, ToolKind
from .widgets import NumberEdit

KINDS = list(ToolKind.__args__)
KIND_HINTS = {
    "box_tool": "Roller-back / balanced box tool. Add params like rollers, cut_range",
    "knurl_straddle": "Straddle (cut) knurl. Params: tpi, knurl_width",
    "knurl_bump": "Bump / form knurl. Params: tpi",
    "die_head": "Geometric / self-opening die head. Param pitch sets the synced feed",
    "tap": "Tap: synced feed, reverses spindle to back out",
}


class ToolLibraryDialog(QDialog):
    def __init__(self, library, path, in_use: set[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Tool library - {path}")
        self.resize(980, 640)
        self.library, self.path, self.in_use = library, Path(path), in_use
        self.current: Tool | None = None
        self.changed = False

        lay = QHBoxLayout(self)
        left = QVBoxLayout()
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._select)
        left.addWidget(self.list, 1)
        row = QHBoxLayout()
        for text, slot in (("New", self._new), ("Duplicate", self._dup), ("Delete", self._delete)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        left.addLayout(row)
        lw = QWidget()
        lw.setLayout(left)
        lw.setMaximumWidth(330)
        lay.addWidget(lw)

        right = QVBoxLayout()
        g = QGroupBox("Tool")
        f = QFormLayout(g)
        self.id = QLineEdit()
        self.name = QLineEdit()
        self.kind = QComboBox()
        self.kind.addItems(KINDS)
        self.kind_hint = QLabel("")
        self.kind_hint.setStyleSheet("color:#667")
        self.kind_hint.setWordWrap(True)
        self.hand = QComboBox()
        self.hand.addItems(["left", "right", "neutral"])
        self.orient = QComboBox()
        self.orient.addItems(["radial", "axial", "perpendicular"])
        self.orient.setToolTip("radial: edge points along X at the centreline (stick tools, cutoff)\n"
                               "axial: parallel to Z (drills, bars, box tools, die heads)\n"
                               "perpendicular: body perpendicular to the tool plate")
        self.nose = NumberEdit(0.0, suffix="")
        self.dia = NumberEdit(None, optional=True, placeholder="-")
        self.width = NumberEdit(None, optional=True, placeholder="-")
        self.holder = QLineEdit()
        self.holder.setPlaceholderText("e.g. 3/4 gang block, ER16, screw machine 7/8 shank")
        model_row = QHBoxLayout()
        self.model = QLineEdit()
        self.model.setPlaceholderText("optional STEP or DXF of the tool")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        model_row.addWidget(self.model, 1)
        model_row.addWidget(browse)
        self.notes = QLineEdit()
        f.addRow("ID (unique)", self.id)
        f.addRow("Name", self.name)
        f.addRow("Kind", self.kind)
        f.addRow("", self.kind_hint)
        f.addRow("Hand", self.hand)
        f.addRow("Mounting", self.orient)
        f.addRow("Nose radius", self.nose)
        f.addRow("Diameter", self.dia)
        f.addRow("Blade width", self.width)
        f.addRow("Holder system", self.holder)
        f.addRow("Model file", model_row)
        f.addRow("Notes", self.notes)
        right.addWidget(g)

        g = QGroupBox("Extra parameters (how this tool is treated)")
        gl = QVBoxLayout(g)
        self.params = QTableWidget(0, 2)
        self.params.setHorizontalHeaderLabels(["Name", "Value"])
        self.params.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        gl.addWidget(self.params)
        prow = QHBoxLayout()
        b = QPushButton("+ Parameter")
        b.clicked.connect(lambda: self.params.insertRow(self.params.rowCount()))
        prow.addWidget(b)
        b = QPushButton("Remove")
        b.clicked.connect(lambda: self.params.removeRow(self.params.currentRow()))
        prow.addWidget(b)
        tip = QLabel("Names that match a process field (feed, rpm, dwell, retract, sync…) are filled in "
                     "automatically when this tool is used.")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#667")
        prow.addWidget(tip, 1)
        gl.addLayout(prow)
        right.addWidget(g, 1)

        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Close)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        right.addWidget(bb)
        rw = QWidget()
        rw.setLayout(right)
        lay.addWidget(rw, 1)

        self.kind.currentTextChanged.connect(self._kind_changed)
        self._fill_list()

    # ------------------------------------------------------------------ list
    def _fill_list(self, select_id=None):
        self.list.blockSignals(True)
        self.list.clear()
        for t in self.library:
            self.list.addItem(f"{t.name}  [{t.kind}]")
        self.list.blockSignals(False)
        ids = [t.id for t in self.library]
        if ids:
            self.list.setCurrentRow(ids.index(select_id) if select_id in ids else 0)

    def _select(self, row):
        if self.current is not None and not self._commit():
            return
        tools = list(self.library)
        if not 0 <= row < len(tools):
            self.current = None
            return
        t = self.current = tools[row]
        self.id.setText(t.id)
        self.name.setText(t.name)
        self.kind.blockSignals(True)
        self.kind.setCurrentText(t.kind)
        self.kind.blockSignals(False)
        self.kind_hint.setText(KIND_HINTS.get(t.kind, ""))
        self.hand.setCurrentText(t.hand)
        self.orient.setCurrentText(t.orientation)
        self.nose.setValue(t.nose_radius)
        self.dia.setValue(t.diameter)
        self.width.setValue(t.width)
        self.holder.setText(t.holder)
        self.model.setText(t.model or "")
        self.notes.setText(t.notes)
        self.params.setRowCount(0)
        for k, v in t.params.items():
            r = self.params.rowCount()
            self.params.insertRow(r)
            self.params.setItem(r, 0, QTableWidgetItem(k))
            self.params.setItem(r, 1, QTableWidgetItem(str(v)))

    def _kind_changed(self, kind):
        self.kind_hint.setText(KIND_HINTS.get(kind, ""))
        self.orient.setCurrentText(KIND_DEFAULT_ORIENTATION.get(kind, "radial"))

    @staticmethod
    def _parse(v: str):
        v = v.strip()
        if v.lower() in ("true", "false"):
            return v.lower() == "true"
        try:
            return int(v)
        except ValueError:
            pass
        try:
            return float(v)
        except ValueError:
            return v

    def _commit(self) -> bool:
        t = self.current
        if t is None:
            return True
        new_id = self.id.text().strip()
        if not new_id:
            QMessageBox.warning(self, "Tool", "Every tool needs an ID.")
            return False
        params = {}
        for r in range(self.params.rowCount()):
            k = self.params.item(r, 0)
            v = self.params.item(r, 1)
            if k and k.text().strip():
                params[k.text().strip()] = self._parse(v.text() if v else "")
        try:
            updated = Tool(id=new_id, name=self.name.text().strip() or new_id, kind=self.kind.currentText(),
                           hand=self.hand.currentText(), orientation=self.orient.currentText(),
                           nose_radius=self.nose.value() or 0.0, diameter=self.dia.value(),
                           width=self.width.value(), holder=self.holder.text().strip(),
                           model=self.model.text().strip() or None, units=t.units,
                           notes=self.notes.text().strip(), params=params)
            if updated != t:
                old_id = t.id
                if old_id != new_id:
                    if old_id in self.in_use:
                        raise ValueError(f"{old_id} is loaded in the current job; change its name, not its ID")
                    if new_id in {x.id for x in self.library}:
                        raise ValueError(f"duplicate tool id {new_id!r}")
                # update in place so stations already pointing at this tool see the change
                t.__dict__.update(updated.__dict__)
                if old_id != new_id:
                    self.library.rekey(old_id, t)
                self.changed = True
                idx = [x.id for x in self.library].index(t.id)
                if self.list.item(idx):
                    self.list.item(idx).setText(f"{t.name}  [{t.kind}]")
        except ValueError as e:
            QMessageBox.warning(self, "Tool", str(e))
            return False
        return True

    # ------------------------------------------------------------------ actions
    def _new(self):
        if not self._commit():
            return
        n = 1
        while f"tool-{n}" in {t.id for t in self.library}:
            n += 1
        self.library.add(Tool(id=f"tool-{n}", name=f"New tool {n}", kind="turn"))
        self.current = None
        self.changed = True
        self._fill_list(f"tool-{n}")

    def _dup(self):
        if not self._commit() or self.current is None:
            return
        t = copy.deepcopy(self.current)
        base, n = t.id, 2
        while f"{base}-{n}" in {x.id for x in self.library}:
            n += 1
        t.id, t.name = f"{base}-{n}", f"{t.name} copy"
        self.library.add(t)
        self.current = None
        self.changed = True
        self._fill_list(t.id)

    def _delete(self):
        t = self.current
        if t is None:
            return
        if t.id in self.in_use:
            QMessageBox.warning(self, "Delete tool", f"{t.name} is loaded on the plate in the current job.")
            return
        if QMessageBox.question(self, "Delete tool", f"Delete {t.name} from the library?") == QMessageBox.Yes:
            self.library.remove(t.id)
            self.current = None
            self.changed = True
            self._fill_list()

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "Tool model", str(self.path.parent),
                                              "Tool models (*.step *.stp *.dxf);;All files (*)")
        if path:
            self.model.setText(path)

    def _save(self):
        if not self._commit():
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.library.save(self.path)
        self.changed = True
        QMessageBox.information(self, "Tool library", f"Saved {len(self.library)} tools to {self.path}")
