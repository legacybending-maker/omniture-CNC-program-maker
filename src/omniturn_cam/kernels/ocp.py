"""OpenCascade kernel through the cadquery-ocp package (standalone app)."""
from __future__ import annotations

from pathlib import Path

import OCP  # noqa: F401  (raises ImportError if the bindings aren't installed)

from . import EdgeInfo, FaceInfo, SolidInfo


def _cast(kind: str, shape):
    """TopoDS.Solid / Face / Edge downcast; OCP 7.x names them Solid_s etc."""
    from OCP.TopoDS import TopoDS
    fn = getattr(TopoDS, kind + "_s", None) or getattr(TopoDS, kind)
    return fn(shape)


def _v(p):
    return (p.X(), p.Y(), p.Z())


def _explore(shape, what, kind):
    from OCP.TopExp import TopExp_Explorer
    exp = TopExp_Explorer(shape, what)
    while exp.More():
        yield _cast(kind, exp.Current())
        exp.Next()


class OCPKernel:
    name = "ocp"

    def read_solids(self, path) -> list[SolidInfo]:
        from OCP.Bnd import Bnd_Box
        from OCP.BRepBndLib import BRepBndLib
        from OCP.BRepGProp import BRepGProp
        from OCP.GProp import GProp_GProps
        from OCP.IFSelect import IFSelect_RetDone
        from OCP.STEPControl import STEPControl_Reader
        from OCP.TopAbs import TopAbs_SOLID

        from ..stepimport import StepImportError
        reader = STEPControl_Reader()
        if reader.ReadFile(str(path)) != IFSelect_RetDone:
            raise StepImportError(f"could not read {Path(path).name} as a STEP file")
        reader.TransferRoots()
        out = []
        for solid in _explore(reader.OneShape(), TopAbs_SOLID, "Solid"):
            props = GProp_GProps()
            BRepGProp.VolumeProperties_s(solid, props)
            box = Bnd_Box()
            BRepBndLib.Add_s(solid, box)
            lo, hi = box.CornerMin(), box.CornerMax()
            out.append(SolidInfo(len(out), f"Solid {len(out) + 1}", abs(props.Mass()),
                                 (hi.X() - lo.X(), hi.Y() - lo.Y(), hi.Z() - lo.Z()), solid))
        return out

    def faces(self, solid) -> list[FaceInfo]:
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.BRepGProp import BRepGProp
        from OCP.GeomAbs import (GeomAbs_Cone, GeomAbs_Cylinder, GeomAbs_Plane, GeomAbs_Sphere,
                                 GeomAbs_SurfaceOfRevolution, GeomAbs_Torus)
        from OCP.GProp import GProp_GProps
        from OCP.TopAbs import TopAbs_FACE

        out = []
        for f in _explore(solid, TopAbs_FACE, "Face"):
            s = BRepAdaptor_Surface(f)
            props = GProp_GProps()
            BRepGProp.SurfaceProperties_s(f, props)
            t = s.GetType()
            info = FaceInfo("other", props.Mass())
            ax = None
            if t == GeomAbs_Cylinder:
                info.kind, ax = "cylinder", s.Cylinder().Axis()
            elif t == GeomAbs_Cone:
                info.kind, ax = "cone", s.Cone().Axis()
            elif t == GeomAbs_Torus:
                info.kind, ax = "torus", s.Torus().Axis()
            elif t == GeomAbs_SurfaceOfRevolution:
                info.kind, ax = "revolution", s.AxeOfRevolution()
            elif t == GeomAbs_Plane:
                info.kind, info.normal = "plane", _v(s.Plane().Axis().Direction())
            elif t == GeomAbs_Sphere:
                info.kind, info.center = "sphere", _v(s.Sphere().Location())
            if ax is not None:
                info.axis = (_v(ax.Location()), _v(ax.Direction()))
            out.append(info)
        return out

    def section(self, solid, origin, normal) -> list[EdgeInfo]:
        from OCP.BRepAdaptor import BRepAdaptor_Curve
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Section
        from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Line
        from OCP.gp import gp_Dir, gp_Pln, gp_Pnt
        from OCP.TopAbs import TopAbs_EDGE

        from ..stepimport import StepImportError
        sec = BRepAlgoAPI_Section(solid, gp_Pln(gp_Pnt(*origin), gp_Dir(*normal)))
        sec.Build()
        if not sec.IsDone():
            raise StepImportError("OpenCascade could not cut the part through its axis")
        out = []
        for e in _explore(sec.Shape(), TopAbs_EDGE, "Edge"):
            c = BRepAdaptor_Curve(e)
            typ = c.GetType()
            info = EdgeInfo("other", c.FirstParameter(), c.LastParameter(),
                            lambda t, c=c: _v(c.Value(t)))
            if typ == GeomAbs_Line:
                info.kind = "line"
            elif typ == GeomAbs_Circle:
                circ = c.Circle()
                info.kind, info.center, info.radius = "circle", _v(circ.Location()), circ.Radius()
            out.append(info)
        return out
