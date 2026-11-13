"""Workbench commands."""
from __future__ import annotations

import os

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtGui

try:
    from PySide import QtWidgets
except ImportError:
    QtWidgets = QtGui

from . import icon
from .feature import is_profile, make_profile

PARAMS = "User parameter:BaseApp/Preferences/Mod/OmniTurnCAM"


def _message(text, title="OmniTurn CAM", warn=False):
    box = QtWidgets.QMessageBox.warning if warn else QtWidgets.QMessageBox.information
    box(Gui.getMainWindow(), title, text)


def _has_solid(obj) -> bool:
    shape = getattr(obj, "Shape", None)
    return shape is not None and not shape.isNull() and bool(shape.Solids)


def _picked_solid_index(sel, shape) -> int:
    """If the user clicked a face in the 3D view, the solid it belongs to; else the largest."""
    solids = shape.Solids
    for sub in getattr(sel, "SubObjects", []) or []:
        try:
            p = sub.CenterOfMass if hasattr(sub, "CenterOfMass") else sub.Point
        except Exception:
            continue
        for i, s in enumerate(solids):
            if s.isInside(p, 1e-3, True):
                return i
    return max(range(len(solids)), key=lambda i: solids[i].Volume)


class ExtractProfile:
    def GetResources(self):
        return {"Pixmap": icon("extract.svg"),
                "MenuText": "Extract turned profile",
                "ToolTip": "Find the turning axis of the selected solid and pull out its OD and ID "
                           "profiles, ready for omniturn-cam"}

    def IsActive(self):
        return App.ActiveDocument is not None

    def Activated(self):
        doc = App.ActiveDocument
        picks = [s for s in Gui.Selection.getSelectionEx() if _has_solid(s.Object) and not is_profile(s.Object)]
        if not picks:
            _message("Select the turned part (a solid) in the tree or the 3D view first.")
            return
        sel = picks[0]
        import Part
        try:
            shape = Part.getShape(sel.Object)
        except Exception:
            shape = sel.Object.Shape
        index = _picked_solid_index(sel, shape)
        units = App.ParamGet(PARAMS).GetString("Units", "inch")
        doc.openTransaction("Extract turned profile")
        obj = make_profile(sel.Object, index, doc, units)
        doc.recompute()
        obj.ViewObject.Proxy.new = True
        Gui.ActiveDocument.setEdit(obj, 0)


class SaveToJob:
    def GetResources(self):
        return {"Pixmap": icon("savejob.svg"),
                "MenuText": "Save profile to job file",
                "ToolTip": "Write the selected profile's OD and ID into an omniturn-cam job (.toml). "
                           "An existing job keeps its tools and operations; only the profiles are replaced"}

    def IsActive(self):
        return any(is_profile(o) for o in Gui.Selection.getSelection())

    def Activated(self):
        from .export import save_profiles
        profs = [o for o in Gui.Selection.getSelection() if is_profile(o)]
        obj = profs[0]
        params = App.ParamGet(PARAMS)
        start = params.GetString("LastJobDir", "") or os.path.dirname(obj.Document.FileName or "") or os.path.expanduser("~")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            Gui.getMainWindow(), "Save profile to omniturn-cam job", start,
            "omniturn-cam job (*.toml)", "", QtWidgets.QFileDialog.DontConfirmOverwrite)
        if not path:
            return
        if not path.lower().endswith(".toml"):
            path += ".toml"
        try:
            msg = save_profiles(obj, path)
        except Exception as e:
            _message(str(e), warn=True)
            return
        params.SetString("LastJobDir", os.path.dirname(path))
        if len(profs) > 1:
            msg += f"\n(Only {obj.Label} was saved; select one profile at a time.)"
        _message(msg)
        App.Console.PrintMessage(msg + "\n")


COMMANDS = {"OmniTurn_ExtractProfile": ExtractProfile, "OmniTurn_SaveToJob": SaveToJob}


def register() -> list[str]:
    for name, cls in COMMANDS.items():
        if name not in Gui.listCommands():
            Gui.addCommand(name, cls())
    return list(COMMANDS)
