"""The six step screens, in IGF order: job & blank, part shape, tool plate,
processes, check, program. One module per screen. Each screen edits
MainWindow.job in place and calls win.touched() after a change; refresh()
reloads it from the job."""

from .base import Screen
from .check import CheckScreen
from .job import JobScreen
from .plate import PlateScreen
from .processes import ProcessScreen
from .program import ProgramScreen, usb_mounts
from .shape import ShapeScreen

__all__ = [
    "Screen",
    "JobScreen",
    "ShapeScreen",
    "PlateScreen",
    "ProcessScreen",
    "CheckScreen",
    "ProgramScreen",
    "usb_mounts",
]
