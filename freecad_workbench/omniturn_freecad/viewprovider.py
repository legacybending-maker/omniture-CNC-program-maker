"""View provider for the turned profile: colours, icon, double-click to edit."""
from __future__ import annotations

import FreeCADGui as Gui

from . import icon
from .feature import OD_COLOR


class ViewProviderProfile:
    def __init__(self, vobj):
        vobj.Proxy = self
        try:
            vobj.LineWidth = 3.0
            vobj.PointSize = 5.0
            vobj.LineColor = OD_COLOR
            vobj.PointColor = OD_COLOR
        except Exception:
            pass

    def attach(self, vobj):
        self.Object = vobj.Object

    def getIcon(self):
        return icon("profile.svg")

    def setEdit(self, vobj, mode=0):
        if mode != 0:
            return None
        from .taskpanel import ProfileTaskPanel
        new = getattr(self, "new", False)
        self.new = False
        Gui.Control.showDialog(ProfileTaskPanel(vobj.Object, new=new))
        return True

    def unsetEdit(self, vobj, mode=0):
        if mode != 0:
            return None
        Gui.Control.closeDialog()
        return True

    def doubleClicked(self, vobj):
        Gui.ActiveDocument.setEdit(vobj.Object, 0)
        return True

    def dumps(self):
        return None

    def loads(self, state):
        return None

    __getstate__ = dumps

    def __setstate__(self, state):
        return None
