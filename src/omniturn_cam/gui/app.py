"""Main window: IGF-style step screens with a function-key bar along the bottom.

F1 Job & blank   F2 Part shape   F3 Tool plate   F4 Processes
F5 Check         F6 Program      F7 Save job     F8 Tool library
"""

from __future__ import annotations

import html
import shutil
import sys
from importlib import resources
from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QAction, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..geometry import Point, Profile
from ..job import Job, Stock, load_job, save_job
from ..post import MachineConfig, PostError, post
from ..tools import Setup, ToolLibrary
from .screens import CheckScreen, JobScreen, PlateScreen, ProcessScreen, ProgramScreen, ShapeScreen
from .toollib import ToolLibraryDialog

CONFIG_DIR = Path.home() / ".config" / "omniturn-cam"
STYLE = """
QWidget { font-size: 11pt; }
QPushButton { padding: 6px 12px; }
QLineEdit, QComboBox { min-height: 26px; }
QPushButton#fkey { min-height: 46px; font-weight: 600; border: 1px solid #9aa4b2; border-style: solid;
                   border-radius: 4px; background-color: #eef1f5; color: #1f2a38; }
QPushButton#fkey:checked { background-color: #2a6fdb; color: white; border: 1px solid #1d4fa0; }
QPushButton#fkey:hover:!checked { background-color: #dde6f3; }
QWidget#fkeybar { background-color: #dfe3e8; }
QLabel#banner { background: #1f2a38; color: white; padding: 8px 12px; font-size: 13pt; font-weight: 600; }
QGroupBox { font-weight: 600; margin-top: 10px; }
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }
"""


def default_library_path() -> Path:
    path = CONFIG_DIR / "tools.toml"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with resources.as_file(resources.files("omniturn_cam") / "data" / "default_tools.toml") as src:
            shutil.copy(src, path)
    return path


def default_machine_path() -> Path:
    path = CONFIG_DIR / "machine.toml"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with resources.as_file(resources.files("omniturn_cam") / "data" / "default_machine.toml") as src:
            shutil.copy(src, path)
    return path


def new_job() -> Job:
    return Job("PART1", "inch", Stock(1.0), Setup(), {"od": Profile(Point(0, 0), name="od")}, [])


class MainWindow(QMainWindow):
    def __init__(self, library_path: Path | None = None, job_path: Path | None = None):
        super().__init__()
        self.settings = QSettings("omniturn-cam", "omniturn-cam")
        self.library_path = Path(library_path or self.settings.value("library_path", "") or default_library_path())
        self.library = ToolLibrary.load(self.library_path)
        self.job = new_job()
        self.job_path: Path | None = None
        self.dirty = False
        self.last_error = ""
        self.warnings: list[str] = []
        self.machine_path = default_machine_path()

        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.banner = QLabel()
        self.banner.setObjectName("banner")
        outer.addWidget(self.banner)
        self.stack = QStackedWidget()
        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(10, 8, 10, 8)
        bl.addWidget(self.stack)
        outer.addWidget(body, 1)

        self.screens = [
            JobScreen(self),
            ShapeScreen(self),
            PlateScreen(self),
            ProcessScreen(self),
            CheckScreen(self),
            ProgramScreen(self),
        ]
        for s in self.screens:
            self.stack.addWidget(s)

        keys = QHBoxLayout()
        keys.setContentsMargins(6, 6, 6, 6)
        self.fkeys = []
        labels = [
            "Job &&\nBlank",
            "Part\nShape",
            "Tool\nPlate",
            "Processes",
            "Check",
            "Program",
            "Save\nJob",
            "Tool\nLibrary",
        ]
        for i, text in enumerate(labels):
            b = QPushButton(f"F{i + 1}\n{text.replace(chr(10), ' ')}")
            b.setObjectName("fkey")
            b.setFocusPolicy(Qt.NoFocus)
            if i < 6:
                b.setCheckable(True)
                b.clicked.connect(lambda _c=False, n=i: self.show_screen(n))
            elif i == 6:
                b.clicked.connect(self.save)
            else:
                b.clicked.connect(self.edit_library)
            keys.addWidget(b)
            QShortcut(QKeySequence(f"F{i + 1}"), self, activated=b.click)
            self.fkeys.append(b)
        kw = QWidget()
        kw.setLayout(keys)
        kw.setObjectName("fkeybar")
        kw.setAttribute(Qt.WA_StyledBackground, True)
        outer.addWidget(kw)
        self.setCentralWidget(central)
        self._menus()
        self.resize(1280, 820)

        if job_path:
            self.open_job(Path(job_path))
        self.show_screen(0)

    # ------------------------------------------------------------------ menus
    def _menus(self):
        m = self.menuBar().addMenu("&File")
        for text, key, slot in (
            ("&New job", "Ctrl+N", self.new),
            ("&Open job…", "Ctrl+O", self.open_dialog),
            ("&Save job", "Ctrl+S", self.save),
            ("Save job &as…", "Ctrl+Shift+S", self.save_as),
            (None, None, None),
            ("Open tool &library…", None, self.open_library),
            ("&Edit tool library…", None, self.edit_library),
            (None, None, None),
            ("&Quit", "Ctrl+Q", self.close),
        ):
            if text is None:
                m.addSeparator()
                continue
            a = QAction(text, self)
            if key:
                a.setShortcut(key)
            a.triggered.connect(slot)
            m.addAction(a)
        h = self.menuBar().addMenu("&Help")
        a = QAction("&About", self)
        a.triggered.connect(
            lambda: QMessageBox.about(
                self,
                "omniturn-cam",
                "<b>omniturn-cam</b><br>Conversational lathe CAM for OmniTurn gang-tool lathes.<br><br>"
                "GPL-3.0-or-later. No warranty: prove out every program with F4 Verify and single block.",
            )
        )
        h.addAction(a)

    # ------------------------------------------------------------------ state
    def touched(self):
        self.dirty = True
        self._banner()

    def _banner(self):
        i = self.stack.currentIndex()
        name = self.job_path.name if self.job_path else "unsaved job"
        star = " *" if self.dirty else ""
        sep = "&nbsp;&nbsp;|&nbsp;&nbsp;"
        title = html.escape(self.screens[i].title).replace("  ", "&nbsp;&nbsp;")
        self.banner.setText(
            f"{title}&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;"
            f"<span style='font-weight:400; color:#c9d3df'>{self.job.name}{sep}"
            f"{self.job.units.upper()}{sep}bar {self.job.stock.diameter:g}{sep}{name}{star}</span>"
        )
        self.setWindowTitle(f"omniturn-cam - {self.job.name}{star}")

    def show_screen(self, n: int):
        self.stack.setCurrentIndex(n)
        for i, b in enumerate(self.fkeys[:6]):
            b.setChecked(i == n)
        self.screens[n].refresh()
        self._banner()

    @property
    def machine(self) -> MachineConfig:
        try:
            return MachineConfig.load(self.machine_path, self.job.units)
        except (OSError, ValueError) as e:
            self.warnings = [f"could not read {self.machine_path}: {e}"]
            return MachineConfig.for_units(self.job.units)

    def generate(self) -> str | None:
        self.warnings = []
        try:
            code = post(self.job, self.machine, warnings=self.warnings)
        except (PostError, KeyError, ValueError) as e:
            self.last_error = str(e).strip('"')
            return None
        self.last_error = ""
        return code

    # ------------------------------------------------------------------ files
    def _confirm_discard(self) -> bool:
        if not self.dirty:
            return True
        r = QMessageBox.question(
            self,
            "Unsaved changes",
            "Save changes to this job first?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
        )
        if r == QMessageBox.Save:
            return self.save()
        return r == QMessageBox.Discard

    def new(self):
        if self._confirm_discard():
            self.job, self.job_path, self.dirty = new_job(), None, False
            self.show_screen(0)

    def open_dialog(self):
        if not self._confirm_discard():
            return
        start = self.settings.value("job_dir", str(Path.home()))
        path, _ = QFileDialog.getOpenFileName(self, "Open job", start, "omniturn-cam job (*.toml)")
        if path:
            self.open_job(Path(path))

    def open_job(self, path: Path):
        try:
            self.job = load_job(path, self.library, validate=False)
        except (KeyError, ValueError) as e:
            QMessageBox.critical(self, "Open job", f"Could not open {path.name}:\n{e}")
            return
        self.job_path, self.dirty = path, False
        self.settings.setValue("job_dir", str(path.parent))
        self.show_screen(self.stack.currentIndex())

    def save(self) -> bool:
        if self.job_path is None:
            return self.save_as()
        save_job(self.job, self.job_path)
        self.dirty = False
        self._banner()
        return True

    def save_as(self) -> bool:
        start = self.settings.value("job_dir", str(Path.home()))
        path, _ = QFileDialog.getSaveFileName(
            self, "Save job", f"{start}/{self.job.name}.toml", "omniturn-cam job (*.toml)"
        )
        if not path:
            return False
        self.job_path = Path(path if path.endswith(".toml") else path + ".toml")
        self.settings.setValue("job_dir", str(self.job_path.parent))
        return self.save()

    def open_library(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open tool library", str(self.library_path.parent), "Tool library (*.toml)"
        )
        if not path:
            return
        try:
            lib = ToolLibrary.load(path)
            missing = [st.tool.id for st in self.job.setup.stations.values() if st.tool.id not in {t.id for t in lib}]
            if missing:
                raise KeyError(f"current job uses tools not in that library: {', '.join(missing)}")
        except (KeyError, ValueError) as e:
            QMessageBox.critical(self, "Tool library", str(e))
            return
        for st in self.job.setup.stations.values():
            st.tool = lib[st.tool.id]
        self.library, self.library_path = lib, Path(path)
        self.settings.setValue("library_path", path)
        self.show_screen(self.stack.currentIndex())

    def edit_library(self):
        in_use = {st.tool.id for st in self.job.setup.stations.values()}
        dlg = ToolLibraryDialog(self.library, self.library_path, in_use, self)
        dlg.exec()
        self.show_screen(self.stack.currentIndex())

    def closeEvent(self, ev):
        if self._confirm_discard():
            ev.accept()
        else:
            ev.ignore()


def main(argv=None):
    argv = sys.argv if argv is None else argv
    app = QApplication(argv)
    app.setApplicationName("omniturn-cam")
    app.setStyleSheet(STYLE)
    job = Path(argv[1]) if len(argv) > 1 and argv[1].endswith(".toml") else None
    w = MainWindow(job_path=job)
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
