"""FreeCAD's Part module as the geometry kernel (FreeCAD workbench).

Works on Part.Shape objects, so the workbench can hand it a solid that is
already in the document without going through a STEP file.
"""

from __future__ import annotations

from pathlib import Path

import FreeCAD  # noqa: F401  (raises ImportError outside FreeCAD)
import Part

from . import EdgeInfo, FaceInfo, SolidInfo


def _v(p):
    return (p.x, p.y, p.z)


def solids_of(shape) -> list[SolidInfo]:
    out = []
    for s in shape.Solids:
        bb = s.BoundBox
        out.append(SolidInfo(len(out), f"Solid {len(out) + 1}", abs(s.Volume), (bb.XLength, bb.YLength, bb.ZLength), s))
    return out


class FreeCADKernel:
    name = "freecad"

    def read_solids(self, path) -> list[SolidInfo]:
        from ..stepimport import StepImportError

        shape = Part.Shape()
        try:
            shape.read(str(path))
        except Exception as e:  # Part raises its own error types
            raise StepImportError(f"could not read {Path(path).name} as a STEP file ({e})") from e
        return solids_of(shape)

    def faces(self, solid) -> list[FaceInfo]:
        out = []
        for f in solid.Faces:
            s = f.Surface
            info = FaceInfo("other", f.Area)
            if isinstance(s, Part.Cylinder):
                info.kind, info.axis = "cylinder", (_v(s.Center), _v(s.Axis))
            elif isinstance(s, Part.Cone):
                info.kind, info.axis = "cone", (_v(s.Apex), _v(s.Axis))
            elif isinstance(s, Part.Toroid):
                info.kind, info.axis = "torus", (_v(s.Center), _v(s.Axis))
            elif isinstance(s, Part.SurfaceOfRevolution):
                info.kind, info.axis = "revolution", (_v(s.Location), _v(s.Direction))
            elif isinstance(s, Part.Plane):
                info.kind, info.normal = "plane", _v(s.Axis)
            elif isinstance(s, Part.Sphere):
                info.kind, info.center = "sphere", _v(s.Center)
            out.append(info)
        return out

    def section(self, solid, origin, normal) -> list[EdgeInfo]:
        from ..stepimport import StepImportError

        # A plane face comfortably bigger than the part, centred on the origin.
        n = FreeCAD.Vector(*normal)
        size = 4 * solid.BoundBox.DiagonalLength + 1.0
        plane = Part.Plane(FreeCAD.Vector(*origin), n)
        face = plane.toShape(-size, size, -size, size)
        try:
            cut = solid.section(face)
        except Exception as e:
            raise StepImportError(f"FreeCAD could not cut the part through its axis ({e})") from e
        out = []
        for e in cut.Edges:
            t0, t1 = e.ParameterRange
            info = EdgeInfo("other", t0, t1, lambda t, e=e: _v(e.valueAt(t)))
            c = e.Curve
            if isinstance(c, (Part.Line, Part.LineSegment)):
                info.kind = "line"
            elif isinstance(c, Part.Circle):
                ctr = c.Location if hasattr(c, "Location") else c.Center  # Center deprecated in 26.3
                info.kind, info.center, info.radius = "circle", _v(ctr), c.Radius
            out.append(info)
        return out
