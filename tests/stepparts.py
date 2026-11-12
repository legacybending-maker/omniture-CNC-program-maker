"""Make test STEP files with OpenCascade: revolve a (z, r) outline, optionally move
it off the world axes, cut flats / cross holes, or put several solids in one file."""
from __future__ import annotations

import math

from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepBuilderAPI import (BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeWire,
                                BRepBuilderAPI_Transform)
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakeRevol
from OCP.BRep import BRep_Builder
from OCP.GC import GC_MakeArcOfCircle
from OCP.gp import gp_Ax1, gp_Ax2, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec
from OCP.IFSelect import IFSelect_RetDone
from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer
from OCP.TopoDS import TopoDS_Compound


def P(z, r):
    return gp_Pnt(r, 0, z)      # outline drawn in the XZ plane, revolved about Z


def revolve(outline):
    """outline: list of ("line", (z, r)) or ("arc", (z, r), (zmid, rmid)); first item is the start."""
    wire = BRepBuilderAPI_MakeWire()
    cur = outline[0][1]
    for item in outline[1:]:
        if item[0] == "line":
            e = BRepBuilderAPI_MakeEdge(P(*cur), P(*item[1])).Edge()
            cur = item[1]
        else:
            arc = GC_MakeArcOfCircle(P(*cur), P(*item[2]), P(*item[1])).Value()
            e = BRepBuilderAPI_MakeEdge(arc).Edge()
            cur = item[1]
        wire.Add(e)
    face = BRepBuilderAPI_MakeFace(wire.Wire()).Face()
    return BRepPrimAPI_MakeRevol(face, gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), 2 * math.pi).Shape()


def move(shape, tx=0, ty=0, tz=0, axis=(1, 1, 0), angle_deg=0):
    t = gp_Trsf()
    if angle_deg:
        t.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(*axis)), math.radians(angle_deg))
    t2 = gp_Trsf()
    t2.SetTranslation(gp_Vec(tx, ty, tz))
    return BRepBuilderAPI_Transform(BRepBuilderAPI_Transform(shape, t).Shape(), t2).Shape()


def cut(shape, tool):
    return BRepAlgoAPI_Cut(shape, tool).Shape()


def cross_hole(z, dia, length=100):
    return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(-length / 2, 0, z), gp_Dir(1, 0, 0)), dia / 2, length).Shape()


def flat(depth_r, z0, z1, size=100):
    # removes everything with y > depth_r between z0 and z1
    return BRepPrimAPI_MakeBox(gp_Pnt(-size / 2, depth_r, z0), size, size, z1 - z0).Shape()


def write(path, *shapes):
    w = STEPControl_Writer()
    if len(shapes) == 1:
        w.Transfer(shapes[0], STEPControl_AsIs)
    else:
        comp = TopoDS_Compound()
        b = BRep_Builder()
        b.MakeCompound(comp)
        for s in shapes:
            b.Add(comp, s)
        w.Transfer(comp, STEPControl_AsIs)
    assert w.Write(str(path)) == IFSelect_RetDone
    return path


IN = 25.4

# The manual's G75 example part (p2.43), in mm, face at z=0: .343 dia with .125 chamfer,
# .406 dia to Z-.3, a raised bead R.0935 to Z-.487, .375 dia to Z-.75, .75 dia body to Z-1.
def manual_part():
    r = lambda d: d / 2 * IN
    z = lambda v: v * IN
    bead_c = (z(-0.3935), r(0.406))
    top = (bead_c[0], bead_c[1] + 0.0935 * IN)
    return revolve([
        ("start", (z(0), 0)),
        ("line", (z(0), r(0.343) - 0.125 * IN)),
        ("line", (z(-0.125), r(0.343))),
        ("line", (z(-0.3), r(0.343))),
        ("line", (z(-0.3), r(0.406))),
        ("arc", (z(-0.487), r(0.406)), top),
        ("line", (z(-0.487), r(0.375))),
        ("line", (z(-0.75), r(0.375))),
        ("line", (z(-0.75), r(0.75))),
        ("line", (z(-1.0), r(0.75))),
        ("line", (z(-1.0), 0)),
        ("line", (z(0), 0)),
    ])


# A bushing: 1.0 OD, .5 bore with a .03 chamfer at the mouth, .02 chamfer on the OD at the face, 1.25 long.
def bushing():
    r = lambda d: d / 2 * IN
    z = lambda v: v * IN
    return revolve([
        ("start", (z(0), r(0.56))),
        ("line", (z(0), r(0.96))),
        ("line", (z(-0.02), r(1.0))),
        ("line", (z(-1.25), r(1.0))),
        ("line", (z(-1.25), r(0.5))),
        ("line", (z(-0.03), r(0.5))),
        ("line", (z(0), r(0.56))),
    ])


# 1.0 dia x 1.5 stud with a .25 drilled blind hole (118 deg point) .5 deep and a domed back end.
def blind_hole_dome():
    r = lambda d: d / 2 * IN
    z = lambda v: v * IN
    tip = 0.125 / math.tan(math.radians(59))
    dome_r = 0.5 * IN
    mid = (z(-1.5) - dome_r * math.sin(math.radians(45)) + dome_r * 0 , 0)
    return revolve([
        ("start", (z(0), r(0.25))),
        ("line", (z(0), r(1.0))),
        ("line", (z(-1.0), r(1.0))),
        ("arc", (z(-1.5), 0), (z(-1.0) - dome_r * math.sin(math.radians(45)), dome_r * math.cos(math.radians(45)))),
        ("line", (z(-0.5 - tip), 0)),
        ("line", (z(-0.5), r(0.25))),
        ("line", (z(0), r(0.25))),
    ])
