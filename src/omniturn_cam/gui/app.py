"""Main window: IGF-style step screens with a function-key bar along the bottom.

F1 Job & blank   F2 Part shape   F3 Tool plate   F4 Processes
F5 Check         F6 Program      F7 Save job     F8 Tool library
"""

from __future__ import annotations

import html
import logging
import shutil
import sys
import traceback
from importlib import resources
from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QAction, QKeySequence, QShortcut
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
from shiboken6 import isValid

from ..geometry import Point, Profile
from ..job import Job, Stock, load_job, save_job
from ..post import MachineConfig, PostError, post
from ..post.omniturn import MachineConfigError
from ..tools import Setup, ToolLibrary
from .screens import CheckScreen, JobScreen, PlateScreen, ProcessScreen, ProgramScreen, ShapeScreen
from .toollib import ToolLibraryDialog
from .widgets import EditTable

CONFIG_DIR = Path.home() / ".config" / "omniturn-cam"
# Unexpected errors (bugs) are written here with their full traceback, so a
# developer can see what happened after the operator has closed the message.
log = logging.getLogger("omniturn_cam.gui")
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
        self.last_machine: MachineConfig | None = None  # set by generate()
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
            # Every F-key goes through show_screen / save / edit_library, and
            # each of those commits the open editors first (commit_edits). The
            # buttons keep Qt.NoFocus so the cursor stays in the field the
            # operator was typing in; committing is explicit instead.
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
        self._show(0)

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

    # Message boxes go through these three methods so the rest of the window
    # never calls QMessageBox directly. The tests replace them to answer for
    # the operator (a real modal dialog would wait forever with nobody there).
    def notify(self, title: str, text: str) -> None:
        """Tell the operator something went wrong and nothing was done."""
        QMessageBox.warning(self, title, text)

    def ask(self, title: str, text: str) -> bool:
        """A yes/no question; True for yes."""
        return QMessageBox.question(self, title, text) == QMessageBox.Yes

    def unexpected(self, action: str, exc: BaseException) -> None:
        """An error that is a bug in omniturn-cam, not the operator's input:
        log the full traceback, then say so plainly. Never swallowed: the
        operator always hears that ``action`` didn't happen."""
        log.error("%s failed", action, exc_info=(type(exc), exc, exc.__traceback__))
        self.notify(
            "omniturn-cam: internal error",
            f"{action} failed because of a problem in omniturn-cam itself, not in your job:\n\n"
            f"{type(exc).__name__}: {exc}\n\nNothing was changed. Details are in {log_path()}. "
            "Please report it with that file.",
        )

    def commit_edits(self, quiet: bool = False) -> bool:
        """Push every value typed on the current screen into the job.

        Finds every widget on the screen with a ``commit_edit`` method
        (widgets.py: NumberEdit, CommitLineEdit, EditTable) and calls it.
        Tables go first, so a typed cell reaches the shape before anything
        reads it. Returns True when every field was usable; otherwise tells
        the operator which fields aren't (unless ``quiet``) and returns
        False, and the caller doesn't go on: the job would be missing what
        the screen shows.
        """
        screen = self.stack.currentWidget()
        widgets = [screen, *screen.findChildren(QWidget)]
        widgets.sort(key=lambda w: not isinstance(w, EditTable))  # sort is stable: tables first, rest in order
        problems = []
        for w in widgets:
            # Committing one field can rebuild part of the screen (a new
            # station number re-sorts the F3 table), deleting widgets still in
            # this list. isValid() says whether the Qt object behind a Python
            # wrapper still exists; a deleted one has nothing to commit.
            if not isValid(w):
                continue
            commit = getattr(w, "commit_edit", None)
            if callable(commit) and (problem := commit()):
                problems.append(problem)
        if problems and not quiet:
            self.notify(
                "Fix these values first",
                "These fields don't hold a value omniturn-cam can use, so nothing was done:\n\n- "
                + "\n- ".join(problems)
                + "\n\nThey are outlined in red.",
            )
        return not problems

    def blocking_problems(self) -> list[str]:
        """Edits on screen that couldn't go into the job (a shape that isn't
        valid yet). While there are any, no program is made: it would come
        from the job, not from what the operator is looking at."""
        return [p for s in self.screens if (p := s.blocking_problem())]

    def show_screen(self, n: int) -> bool:
        """Switch to screen ``n`` (F1-F6) after committing the open editors.
        Stays put and returns False if a field holds something unusable:
        leaving would rebuild the next screen from the job, and the typed
        value would be lost without a word."""
        if not self.commit_edits():
            self.fkeys[self.stack.currentIndex()].setChecked(True)
            self.fkeys[n].setChecked(n == self.stack.currentIndex())
            return False
        self._show(n)
        return True

    def _show(self, n: int) -> None:
        """Switch screens without committing: after loading or replacing the
        job, when what's on screen belongs to the old one."""
        self.stack.setCurrentIndex(n)
        for i, b in enumerate(self.fkeys[:6]):
            b.setChecked(i == n)
        self.screens[n].refresh()
        self._banner()

    @property
    def machine(self) -> MachineConfig:
        """The machine profile, read fresh each time so an edit to the file
        counts at the next F5/F6. Raises MachineConfigError when it can't be
        used.

        It used to fall back to the built-in profile with only a warning, so
        a broken or mistyped machine.toml produced code for a *different*
        machine. Now nothing is generated until the file is fixed.
        """
        return MachineConfig.load(self.machine_path, self.job.units)

    def machine_problem(self, e: MachineConfigError) -> str:
        """What the operator sees when the machine profile can't be used."""
        return (
            f"The machine profile {self.machine_path} can't be used, so no program was made:\n{e}\n"
            "Fix the file and press F5 again. To go back to the built-in Hardinge HLV-H profile, "
            "delete the file and restart omniturn-cam."
        )

    def generate(self) -> str | None:
        """The program for the job as it stands, or None with the reason in
        ``last_error``. Callers show last_error; nothing here raises."""
        self.warnings = []
        blocking = self.blocking_problems()
        if blocking:
            self.last_error = "No program was made:\n" + "\n".join(blocking)
            return None
        try:
            # The job's own problems first: a job whose units are misspelt
            # can't pick a machine section, and that is the job's fault.
            self.job.validate()
            machine = self.machine
        except MachineConfigError as e:
            self.last_error = self.machine_problem(e)
            return None
        except (KeyError, ValueError) as e:
            self.last_error = str(e).strip('"')
            return None
        try:
            code = post(self.job, machine, warnings=self.warnings)
        except (PostError, KeyError, ValueError) as e:
            self.last_error = str(e).strip('"')
            return None
        except OSError as e:  # the include file vanished or can't be read
            self.last_error = f"Can't read {e.filename}: {e.strerror}. No program was made."
            return None
        except Exception as e:  # a bug: logged with its traceback, never hidden
            log.error("generating the program failed", exc_info=True)
            self.last_error = (
                f"omniturn-cam hit an internal error and made no program: {type(e).__name__}: {e}. "
                f"Details are in {log_path()}; please report it."
            )
            return None
        self.last_error = ""
        # Kept so the backplot (F5) reads arcs with this machine's G02/G03
        # mapping, the same profile the program was just made for.
        self.last_machine = machine
        return code

    # ------------------------------------------------------------------ files
    def _confirm_discard(self) -> bool:
        # An unusable field (or invalid shape) is an unsaved edit too.
        if not self.commit_edits(quiet=True) or self.blocking_problems():
            self.dirty = True
        if not self.dirty:
            return True
        r = self.save_question()
        if r == "save":
            return self.save()
        return r == "discard"

    def save_question(self) -> str:
        """Save / Discard / Cancel for unsaved changes: "save", "discard" or
        "cancel". A method of its own, like notify and ask, so tests answer it."""
        r = QMessageBox.question(
            self,
            "Unsaved changes",
            "Save changes to this job first?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
        )
        return {QMessageBox.Save: "save", QMessageBox.Discard: "discard"}.get(r, "cancel")

    def new(self):
        if self._confirm_discard():
            self.job, self.job_path, self.dirty = new_job(), None, False
            self._show(0)

    def open_dialog(self):
        if not self._confirm_discard():
            return
        start = self.settings.value("job_dir", str(Path.home()))
        path, _ = QFileDialog.getOpenFileName(self, "Open job", start, "omniturn-cam job (*.toml)")
        if path:
            self.open_job(Path(path))

    def open_job(self, path: Path) -> bool:
        """Load the job at ``path``. Returns False, keeping the current job,
        when it can't be loaded; the operator is told why.

        Three kinds of failure, told apart on purpose (issue #38):
        * ValueError / KeyError: the file is there but its contents are wrong
          (a misspelt setting, a tool not in the library). Fix the file.
        * OSError: the file can't be read at all (gone, a folder, no access).
        * anything else: a bug in omniturn-cam, logged with its traceback.
        Catching only the expected kinds by name, and reporting the rest as
        a bug, is what keeps a real bug from looking like a bad job file.
        """
        try:
            job = load_job(path, self.library, validate=False)
        except (KeyError, ValueError) as e:
            self.notify("Open job", f"Could not open {path.name}: the file has a problem.\n\n{str(e).strip(chr(34))}")
            return False
        except OSError as e:
            self.notify("Open job", f"Could not read {path}:\n{e.strerror or e}")
            return False
        except Exception as e:
            self.unexpected(f"Opening {path.name}", e)
            return False
        self.job, self.job_path, self.dirty = job, path, False
        self.settings.setValue("job_dir", str(path.parent))
        self._show(self.stack.currentIndex())
        return True

    def save(self) -> bool:
        """F7 / Ctrl+S. Returns True only when the file is written."""
        if not self.commit_edits():
            return False
        if self.job_path is None:
            return self.save_as()
        return self._save_to(self.job_path)

    def save_as(self) -> bool:
        if not self.commit_edits():
            return False
        start = self.settings.value("job_dir", str(Path.home()))
        path, _ = QFileDialog.getSaveFileName(
            self, "Save job", f"{start}/{self.job.name}.toml", "omniturn-cam job (*.toml)"
        )
        if not path:
            return False
        return self._save_to(Path(path if path.endswith(".toml") else path + ".toml"))

    def _save_to(self, path: Path) -> bool:
        """Write the job to ``path``. Only after the file is safely written
        does the window take it as the job's file and clear the unsaved
        mark; if the write fails, the job, its file name and the unsaved
        mark all stay as they were, and the old file is untouched
        (save_job writes atomically)."""
        blocking = self.blocking_problems()
        if blocking and not self.ask(
            "Save job",
            "\n".join(blocking) + "\n\nThe file will get the last valid shape, not the one on screen. Save anyway?",
        ):
            return False
        try:
            save_job(self.job, path)
        except OSError as e:
            self.notify(
                "Save job",
                f"Could not save to {path}:\n{e.strerror or e}\n\n"
                "Nothing was written there and the job is still open with your changes. "
                "Check the USB stick or folder, or use File > Save job as… to save somewhere else.",
            )
            return False
        except Exception as e:
            self.unexpected(f"Saving {path.name}", e)
            return False
        self.job_path = path
        self.settings.setValue("job_dir", str(path.parent))
        # An invalid shape on screen is still an unsaved change.
        self.dirty = bool(blocking)
        self._banner()
        return True

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
        except OSError as e:
            QMessageBox.critical(self, "Tool library", f"Could not read {path}:\n{e.strerror or e}")
            return
        for st in self.job.setup.stations.values():
            st.tool = lib[st.tool.id]
        self.library, self.library_path = lib, Path(path)
        self.settings.setValue("library_path", path)
        self.show_screen(self.stack.currentIndex())

    def edit_library(self):
        """F8. The dialog edits a copy of the library (a transaction): the
        job and the library here change only when the operator saves in the
        dialog, and then exactly to what was saved to the file."""
        if not self.commit_edits():
            return
        in_use = {st.tool.id for st in self.job.setup.stations.values()}
        dlg = ToolLibraryDialog(self.library, self.library_path, in_use, self)
        dlg.exec()
        if dlg.saved is not None:
            self.library = dlg.saved
            # Stations point at Tool objects. Point them at the saved ones, so
            # the job uses what's in the file. A tool in use can't be deleted
            # or have its ID changed in the dialog, so every ID is still there.
            for st in self.job.setup.stations.values():
                st.tool = self.library[st.tool.id]
        self._show(self.stack.currentIndex())

    def closeEvent(self, ev):
        if self._confirm_discard():
            ev.accept()
        else:
            ev.ignore()


def log_path() -> Path:
    return CONFIG_DIR / "omniturn-cam.log"


def _setup_logging() -> None:
    """Send errors to ~/.config/omniturn-cam/omniturn-cam.log as well as the
    terminal. If the log can't be opened the program still runs."""
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        log_path().parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(log_path(), encoding="utf-8")
    except OSError:
        return
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger("omniturn_cam").addHandler(handler)


def _excepthook(kind, exc, tb) -> None:
    """The last line of defence: an exception no slot handled. Qt would only
    print it to a terminal nobody at the machine is looking at; this logs it
    and tells the operator. sys.excepthook is the function Python calls for
    an exception nothing caught."""
    log.error("unhandled exception", exc_info=(kind, exc, tb))
    if QApplication.instance() is not None:
        QMessageBox.critical(
            None,
            "omniturn-cam: internal error",
            f"Something went wrong inside omniturn-cam:\n\n{kind.__name__}: {exc}\n\n"
            f"The last action may not have finished. Details are in {log_path()}.",
        )
    else:  # pragma: no cover - no GUI to show it in
        traceback.print_exception(kind, exc, tb)


def main(argv=None):
    argv = sys.argv if argv is None else argv
    _setup_logging()
    sys.excepthook = _excepthook
    app = QApplication(argv)
    app.setApplicationName("omniturn-cam")
    app.setStyleSheet(STYLE)
    job = Path(argv[1]) if len(argv) > 1 and argv[1].endswith(".toml") else None
    try:
        w = MainWindow(job_path=job)
    except ValueError as e:
        # Almost always the tool library (tools.toml). Starting anyway with an
        # empty library would let the tool editor save it over the user's file.
        QMessageBox.critical(None, "omniturn-cam", f"omniturn-cam can't start:\n\n{e}\n\nFix the file and start again.")
        return 2
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
