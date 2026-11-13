"""FreeCAD workbench for omniturn-cam.

This package is only the FreeCAD front end. All the CAM logic lives in the
``omniturn_cam`` package (``src/`` in the same repository), which the
standalone app uses too. The workbench adds that directory to sys.path so the
two always come from the same checkout.
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
SRC = os.path.join(REPO, "src")
ICONS = os.path.join(HERE, "icons")

if os.path.isdir(os.path.join(SRC, "omniturn_cam")) and SRC not in sys.path:
    sys.path.insert(0, SRC)


def icon(name: str) -> str:
    return os.path.join(ICONS, name)
