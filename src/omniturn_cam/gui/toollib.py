"""Tool library editor (F8).

Editing is a transaction (issue #38). The dialog works on a *copy* of the
library (``copy.deepcopy``: every Tool copied too, not just the list). The
job's stations point at the real Tool objects, so before this, editing a
tool changed the job at once, and closing without saving left the change
in place. Now:

* Save writes the copy to tools.toml, and only when that worked hands a copy
  of it back in ``saved``; MainWindow then swaps it in and points the
  stations at the saved tools.
* Close (or Esc, or the window's X) with unsaved edits asks first, then
  throws the copy away. The job and the library it came from never saw it.
"""

from __future__ import annotations

import copy
from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..tools import KIND_DEFAULT_ORIENTATION, Tool, ToolKind, ToolLibrary
from .widgets import EditTable, NumberEdit

KINDS = list(ToolKind.__args__)
KIND_HINTS = {
    "box_tool": "Roller-back / balanced box tool. Add params like rollers, cut_range",
    "knurl_straddle": "Straddle (cut) knurl. Params: tpi, knurl_width",
    "knurl_bump": "Bump / form knurl. Params: tpi",
    "die_head": "Geometric / self-opening die head. Param pitch sets the synced feed",
    "tap": "Tap: synced feed, reverses spindle to back out",
}


class ToolLibraryDialog(QDialog):
    """Edit a copy of ``library``; see the module docstring.

    ``saved`` is None until Save succeeds, then the library as saved (a copy
    the dialog no longer touches). ``dirty`` is True while there are edits
    that haven't been saved.
    """

    def __init__(self, library: ToolLibrary, path, in_use: set[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Tool library - {path}")
        self.resize(980, 640)
        self.library = copy.deepcopy(library)  # the working copy
        self.path, self.in_use = Path(path), in_use
        self.current: Tool | None = None
        self.saved: ToolLibrary | None = None
        self.dirty = False

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
        self.orient.setToolTip(
            "radial: edge points along X at the centreline (stick tools, cutoff)\n"
            "axial: parallel to Z (drills, bars, box tools, die heads)\n"
            "perpendicular: body perpendicular to the tool plate"
        )
        self.nose = NumberEdit(0.0, suffix="", label="Nose radius")
        self.dia = NumberEdit(None, optional=True, placeholder="-", label="Diameter")
        self.width = NumberEdit(None, optional=True, placeholder="-", label="Blade width")
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
        self.params = EditTable(0, 2)
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
        tip = QLabel(
            "Names that match a process field (feed, rpm, dwell, retract, sync…) are filled in "
            "automatically when this tool is used."
        )
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#667")
        prow.addWidget(tip, 1)
        gl.addLayout(prow)
        right.addWidget(g, 1)

        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Close)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)  # Close; QDialog sends Esc and the X here too
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

    def _row_of_current(self) -> int:
        ids = [t.id for t in self.library]
        return ids.index(self.current.id) if self.current is not None and self.current.id in ids else -1

    def _select(self, row):
        if self.current is not None and not self._commit():
            # The list already moved to the clicked tool, but the form still
            # shows the one that couldn't be saved. Put the highlight back on
            # that one, so the list and the form agree about which tool is
            # being edited. blockSignals stops this from calling _select again.
            self.list.blockSignals(True)
            self.list.setCurrentRow(self._row_of_current())
            self.list.blockSignals(False)
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
        """Copy the form into the working copy's current tool. False (and the
        operator told why) if the form can't be a valid tool."""
        t = self.current
        if t is None:
            return True
        new_id = self.id.text().strip()
        if not new_id:
            QMessageBox.warning(self, "Tool", "Every tool needs an ID.")
            return False
        params = self._form_params()
        try:
            updated = Tool(
                id=new_id,
                name=self.name.text().strip() or new_id,
                kind=self.kind.currentText(),
                hand=self.hand.currentText(),
                orientation=self.orient.currentText(),
                nose_radius=self.nose.value() or 0.0,
                diameter=self.dia.value(),
                width=self.width.value(),
                holder=self.holder.text().strip(),
                model=self.model.text().strip() or None,
                units=t.units,
                notes=self.notes.text().strip(),
                params=params,
            )
            if updated != t:
                old_id = t.id
                if old_id != new_id:
                    if old_id in self.in_use:
                        raise ValueError(f"{old_id} is loaded in the current job; change its name, not its ID")
                    if new_id in {x.id for x in self.library}:
                        raise ValueError(f"duplicate tool id {new_id!r}")
                # Update the working copy's Tool in place, so the list keeps
                # its order. Nothing outside the dialog points at it.
                t.__dict__.update(updated.__dict__)
                if old_id != new_id:
                    self.library.rekey(old_id, t)
                self.dirty = True
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
        self.dirty = True
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
        self.dirty = True
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
            self.dirty = True
            self._fill_list()

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Tool model", str(self.path.parent), "Tool models (*.step *.stp *.dxf);;All files (*)"
        )
        if path:
            self.model.setText(path)

    def _save(self) -> bool:
        """Write the working copy to the file; only if that worked, hand it
        back (``saved``). A failed write leaves the file as it was (it's
        written atomically) and the edits still open here to try again."""
        if not self._commit():
            return False
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.library.save(self.path)
        except OSError as e:
            QMessageBox.warning(
                self,
                "Tool library",
                f"Could not save to {self.path}:\n{e.strerror or e}\n\nNothing was changed; your edits are still here.",
            )
            return False
        # A copy, so edits made after this save don't leak into the job.
        self.saved = copy.deepcopy(self.library)
        self.dirty = False
        QMessageBox.information(self, "Tool library", f"Saved {len(self.library)} tools to {self.path}")
        return True

    def reject(self):
        """Close / Esc / the window's X. Unsaved edits are thrown away, after
        asking: they're only in the working copy."""
        # A form edit not yet copied counts too. If the form can't even be a
        # valid tool, that's an unsaved edit as well.
        try:
            unsaved = self.dirty or (self.current is not None and self._form_differs())
        except ValueError:
            unsaved = True
        if unsaved and not self.confirm_discard():
            return
        super().reject()

    def confirm_discard(self) -> bool:
        """Ask before throwing unsaved edits away. Its own method so tests can
        answer it."""
        return (
            QMessageBox.question(
                self, "Tool library", "Close without saving? Your changes to the tool library will be lost."
            )
            == QMessageBox.Yes
        )

    def _form_params(self) -> dict:
        """The extra-parameters table as a dict, a cell still being typed in
        included."""
        self.params.commit_edit()
        params = {}
        for r in range(self.params.rowCount()):
            k = self.params.item(r, 0)
            v = self.params.item(r, 1)
            if k and k.text().strip():
                params[k.text().strip()] = self._parse(v.text() if v else "")
        return params

    def _form_differs(self) -> bool:
        """Whether the form shows something other than the current tool.
        Raises ValueError if a number in it can't be read."""
        t = self.current
        return (
            self.id.text().strip() != t.id
            or (self.name.text().strip() or t.id) != t.name
            or self.kind.currentText() != t.kind
            or self.hand.currentText() != t.hand
            or self.orient.currentText() != t.orientation
            or (self.nose.value() or 0.0) != t.nose_radius
            or self.dia.value() != t.diameter
            or self.width.value() != t.width
            or self.holder.text().strip() != t.holder
            or (self.model.text().strip() or None) != t.model
            or self.notes.text().strip() != t.notes
            or self._form_params() != t.params
        )
