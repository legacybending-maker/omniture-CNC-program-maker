"""F6 Program: the finished program and writing it to the USB stick."""

from __future__ import annotations

import getpass
import os
from pathlib import Path

from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFileDialog,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
)

from .base import Screen, _buttons


def usb_mounts() -> list[Path]:
    user = os.environ.get("USER") or getpass.getuser()
    roots = [Path("/media") / user, Path("/run/media") / user, Path("/media")]
    found = []
    for root in roots:
        if root.is_dir():
            for p in sorted(root.iterdir()):
                if p.is_dir() and os.path.ismount(p) and p not in found:
                    found.append(p)
    return found


class ProgramScreen(Screen):
    title = "6  PROGRAM"

    def __init__(self, win):
        super().__init__(win)
        lay = QVBoxLayout(self)
        self.status = QLabel("")
        lay.addWidget(self.status)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        f = QFont("monospace")
        f.setStyleHint(QFont.Monospace)
        f.setPointSize(11)
        self.text.setFont(f)
        lay.addWidget(self.text, 1)
        row, _ = _buttons(
            ("Save to USB stick", self._usb), ("Save program as…", self._save_as), ("Copy to clipboard", self._copy)
        )
        lay.addLayout(row)
        self.code = None

    def refresh(self):
        self.code = self.win.generate()
        if self.code is None:
            self.text.setPlainText("")
            self.status.setText(f"<span style='color:#b00020'>{self.win.last_error}</span>")
        else:
            self.text.setPlainText(self.code)
            self.status.setText(
                f"<b>{self.job.name}.cnc</b> &nbsp; {self.code.count(chr(10))} lines - "
                "prove out with F4 Verify and single block before running."
            )

    def _write(self, path: Path):
        path.write_text(self.code.replace("\n", "\r\n"))  # DOS line endings for the OmniTurn
        QMessageBox.information(self, "Saved", f"Wrote {path}")

    def _usb(self):
        if not self.code:
            return
        mounts = usb_mounts()
        if len(mounts) == 1:
            target = mounts[0] / f"{self.job.name}.cnc"
            if (
                target.exists()
                and QMessageBox.question(self, "Save to USB", f"{target.name} already exists on the stick. Replace it?")
                != QMessageBox.Yes
            ):
                return
            self._write(target)
        else:
            start = str(mounts[0] if mounts else Path.home())
            self._save_as(start)

    def _save_as(self, start=None):
        if not self.code:
            return
        start = start or str(Path.home())
        path, _ = QFileDialog.getSaveFileName(
            self, "Save program", f"{start}/{self.job.name}.cnc", "OmniTurn program (*.cnc *.txt)"
        )
        if path:
            self._write(Path(path))

    def _copy(self):
        if self.code:
            from PySide6.QtWidgets import QApplication

            QApplication.clipboard().setText(self.code)
