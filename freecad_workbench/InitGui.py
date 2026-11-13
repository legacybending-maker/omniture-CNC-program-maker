# OmniTurn CAM workbench for FreeCAD - GUI start-up.
# FreeCAD runs this file at start-up with this folder on sys.path.
import FreeCADGui as Gui


class OmniTurnCAMWorkbench(Gui.Workbench):
    MenuText = "OmniTurn CAM"
    ToolTip = "Conversational 2D lathe CAM for OmniTurn gang-tool lathes"

    def __init__(self):
        import omniturn_freecad

        self.__class__.Icon = omniturn_freecad.icon("workbench.svg")

    def Initialize(self):
        from omniturn_freecad import commands

        names = commands.register()
        self.appendToolbar("OmniTurn CAM", names)
        self.appendMenu("&OmniTurn", names)

    def Activated(self):
        pass

    def Deactivated(self):
        pass

    def GetClassName(self):
        return "Gui::PythonWorkbench"


Gui.addWorkbench(OmniTurnCAMWorkbench())
