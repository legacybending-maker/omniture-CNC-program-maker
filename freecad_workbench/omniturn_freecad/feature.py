"""The "Turned profile" document object.

A parametric FeaturePython object: it links to a solid in the document and
holds the OD and ID profiles extracted from it, drawn on the part in the plane
of the cut. Change the model and recompute, and the profile follows.

No FreeCADGui imports at module level, so documents with profiles open in
FreeCADCmd too.
"""

from __future__ import annotations

import json
import math

import FreeCAD as App
import Part

from . import icon  # noqa: F401  (also puts omniturn_cam on sys.path)
from omniturn_cam.geometry import Arc, Profile
from omniturn_cam.job import profile_from_dict, profile_to_dict
from omniturn_cam.stepimport import ImportResult, StepImportError, extend_to_stock, import_solid

MM_PER_INCH = 25.4
OD_COLOR = (1.0, 0.45, 0.0)  # orange
ID_COLOR = (0.1, 0.45, 1.0)  # blue


def _source_shape(obj):
    """Shape of the linked object with its own placement, in the coordinates of
    the container both objects live in (see make_profile)."""
    src = obj.Source
    if src is None:
        raise StepImportError("no source solid linked")
    try:
        return Part.getShape(src)
    except Exception:
        return src.Shape


def _solids(obj):
    shape = _source_shape(obj)
    solids = shape.Solids
    if not solids:
        raise StepImportError(f"{obj.Source.Label} has no solids")
    return solids


def _profile_edges(prof: Profile, result: ImportResult) -> list:
    """3D edges for a profile, in the model, on the plane where it was cut."""
    to3d = lambda x, z: App.Vector(*result.to_model(x, z))
    edges = []
    prev = prof.start
    for seg in prof.segments:
        p0, p1 = to3d(prev.x, prev.z), to3d(seg.end.x, seg.end.z)
        if (p1 - p0).Length < 1e-7:
            prev = seg.end
            continue
        if isinstance(seg, Arc):
            cz, cr = seg.center(prev)
            a0 = math.atan2(prev.r - cr, prev.z - cz)
            a1 = math.atan2(seg.end.r - cr, seg.end.z - cz)
            if seg.direction == "ccw":
                while a1 <= a0:
                    a1 += 2 * math.pi
            else:
                while a1 >= a0:
                    a1 -= 2 * math.pi
            am = (a0 + a1) / 2
            pm = to3d(2 * (cr + seg.radius * math.sin(am)), cz + seg.radius * math.cos(am))
            edges.append(Part.Arc(p0, pm, p1).toShape())
        else:
            # C/R corner words only come from hand-edited profiles; the line to the
            # sharp corner is what the control is given, so that's what is drawn.
            edges.append(Part.LineSegment(p0, p1).toShape())
        prev = seg.end
    return edges


def _segment_text(prof: Profile, units: str) -> list[str]:
    fmt = (lambda v: f"{v:.4f}") if units == "inch" else (lambda v: f"{v:.3f}")
    lines = [f"start  X{fmt(prof.start.x)}  Z{fmt(prof.start.z)}"]
    for seg in prof.segments:
        if isinstance(seg, Arc):
            lines.append(f"arc    X{fmt(seg.end.x)}  Z{fmt(seg.end.z)}  R{fmt(seg.radius)}  {seg.direction}")
        else:
            lines.append(f"line   X{fmt(seg.end.x)}  Z{fmt(seg.end.z)}")
    return lines


class ProfileFeature:
    TYPE = "OmniTurnCAM::Profile"

    def __init__(self, obj):
        obj.Proxy = self
        self.ensure_properties(obj)

    @staticmethod
    def ensure_properties(obj):
        def add(kind, name, group, doc, value=None, mode=0):
            if name not in obj.PropertiesList:
                obj.addProperty(kind, name, group, doc)
                if value is not None:
                    setattr(obj, name, value)
                obj.setEditorMode(name, mode)

        add("App::PropertyLink", "Source", "Profile", "Turned solid the profile comes from")
        add(
            "App::PropertyInteger",
            "SolidIndex",
            "Profile",
            "Which solid of the source, when it has more than one (0 = first)",
            0,
        )
        add("App::PropertyEnumeration", "Units", "Profile", "Units the profile is written in")
        if not obj.getEnumerationsOfProperty("Units"):
            obj.Units = ["inch", "metric"]
            obj.Units = "inch"
        add("App::PropertyBool", "Flip", "Profile", "Put Z0 (the face) at the other end of the part", False)
        add(
            "App::PropertyLength",
            "StockDiameter",
            "Profile",
            "Bar diameter. The OD is run out to it at the back so G75/G78 have a closed area "
            "to rough. 0 = the part's largest diameter",
            0,
        )
        add("App::PropertyBool", "RunOutToStock", "Profile", "Finish the OD at the stock diameter", True)
        ro = 1  # read-only in the property editor
        add("App::PropertyLength", "MaxDiameter", "Result", "Largest diameter of the part", None, ro)
        add("App::PropertyLength", "PartLength", "Result", "Length of the part along the axis", None, ro)
        add("App::PropertyStringList", "OD", "Result", "OD profile, X diameter / Z", None, ro)
        add("App::PropertyStringList", "ID", "Result", "ID profile (bore / drilled hole), if any", None, ro)
        add("App::PropertyStringList", "Warnings", "Result", "Things to check", None, ro)
        add("App::PropertyString", "ProfileData", "Result", "OD/ID profiles as JSON (for job export)", None, 2)

    # FreeCAD calls these to save/restore the proxy; there's no state beyond the properties.
    def dumps(self):
        return None

    def loads(self, state):
        return None

    __getstate__ = dumps  # FreeCAD < 1.0

    def __setstate__(self, state):
        return None

    def onDocumentRestored(self, obj):
        self.ensure_properties(obj)

    def execute(self, obj):
        try:
            result = self.extract(obj)
        except StepImportError as e:
            obj.Warnings = [str(e)]
            obj.OD, obj.ID, obj.ProfileData = [], [], ""
            obj.Shape = Part.Shape()
            App.Console.PrintWarning(f"{obj.Label}: {e}\n")
            return
        k = MM_PER_INCH if result.units == "inch" else 1.0
        obj.MaxDiameter = result.max_dia * k
        obj.PartLength = result.length * k
        obj.OD = _segment_text(result.od, result.units)
        obj.ID = _segment_text(result.id, result.units) if result.id else []
        obj.Warnings = result.warnings
        obj.ProfileData = json.dumps(
            {
                "units": result.units,
                "max_dia": result.max_dia,
                "length": result.length,
                "solid": result.solid,
                "profile": {"od": profile_to_dict(result.od)}
                | ({"id": profile_to_dict(result.id)} if result.id else {}),
            }
        )
        od_edges = _profile_edges(result.od, result)
        id_edges = _profile_edges(result.id, result) if result.id else []
        shape = Part.Compound(od_edges + id_edges)
        obj.Shape = shape
        vo = getattr(obj, "ViewObject", None)
        if vo is not None and hasattr(vo, "LineColorArray"):
            try:
                vo.LineColorArray = [OD_COLOR] * len(od_edges) + [ID_COLOR] * len(id_edges)
            except Exception:
                pass

    @staticmethod
    def extract(obj) -> ImportResult:
        solids = _solids(obj)
        idx = obj.SolidIndex
        if not 0 <= idx < len(solids):
            raise StepImportError(f"{obj.Source.Label} has {len(solids)} solid(s); SolidIndex {idx} is out of range")
        result = import_solid(solids[idx], obj.Units, obj.Flip, "freecad", f"{obj.Source.Label} solid {idx + 1}")
        if obj.RunOutToStock:
            k = MM_PER_INCH if obj.Units == "inch" else 1.0
            stock = obj.StockDiameter.Value / k or result.max_dia
            extend_to_stock(result.od, round(stock, 6 if obj.Units == "inch" else 4))
        return result


def profiles_of(obj) -> tuple[dict[str, Profile], dict]:
    """The profiles stored on a profile object, and the rest of its data."""
    if not obj.ProfileData:
        raise StepImportError(f"{obj.Label} has no profile - recompute it and check its Warnings")
    data = json.loads(obj.ProfileData)
    return {name: profile_from_dict(name, d) for name, d in data["profile"].items()}, data


def is_profile(obj) -> bool:
    return getattr(getattr(obj, "Proxy", None), "TYPE", None) == ProfileFeature.TYPE


def source_and_container(source):
    """The object to take the profile from, and the container (App::Part etc.)
    the profile should go in so it sits on the part and moves with it.
    A feature inside a PartDesign Body means the whole Body (its tip)."""
    parent = source.getParentGeoFeatureGroup()
    if parent is not None and parent.isDerivedFrom("PartDesign::Body"):
        source, parent = parent, parent.getParentGeoFeatureGroup()
    return source, parent


def make_profile(source, solid_index: int = 0, doc=None, units: str = "inch"):
    """Create a profile object for `source` (a document object with a solid)."""
    source, container = source_and_container(source)
    doc = doc or source.Document
    obj = doc.addObject("Part::FeaturePython", "TurnedProfile")
    if container is not None:
        container.addObject(obj)
    ProfileFeature(obj)
    obj.Label = f"{source.Label} profile"
    obj.Source = source
    obj.SolidIndex = solid_index
    obj.Units = units
    if App.GuiUp:
        from .viewprovider import ViewProviderProfile

        ViewProviderProfile(obj.ViewObject)
    return obj
