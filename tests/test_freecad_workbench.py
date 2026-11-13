"""FreeCAD workbench, without the GUI. Runs only under FreeCAD's Python:

PYTHONPATH=/path/to/freecad/lib  /path/to/freecad/bin/python -m pytest tests/test_freecad_workbench.py
"""

import sys
import tomllib
from pathlib import Path

import pytest

App = pytest.importorskip("FreeCAD")
import Part  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "freecad_workbench"))

from omniturn_freecad.export import job_name, save_profiles  # noqa: E402
from omniturn_freecad.feature import is_profile, make_profile, profiles_of  # noqa: E402

from omniturn_cam.job import profile_to_dict  # noqa: E402
from omniturn_cam.stepimport import import_step  # noqa: E402

DATA = ROOT / "tests" / "data"


@pytest.fixture
def doc():
    d = App.newDocument("OmniTurnTest")
    name = d.Name
    yield d
    if name in App.listDocuments():  # a test may already have closed it
        App.closeDocument(name)


def add_step(doc, name):
    shape = Part.Shape()
    shape.read(str(DATA / f"{name}.step"))
    obj = doc.addObject("Part::Feature", name)
    obj.Shape = shape
    return obj


def test_profile_object_matches_standalone_import(doc):
    src = add_step(doc, "bush")
    prof = make_profile(src)
    prof.Flip = True
    prof.RunOutToStock = False
    doc.recompute()
    assert is_profile(prof) and not prof.Warnings
    got, data = profiles_of(prof)
    want = import_step(DATA / "bush.step", flip=True, kernel="freecad")
    assert profile_to_dict(got["od"]) == profile_to_dict(want.od)
    assert profile_to_dict(got["id"]) == profile_to_dict(want.id)
    assert prof.OD[0].startswith("start  X0.5600  Z0.0000")
    assert prof.MaxDiameter.Value == pytest.approx(25.4)
    assert prof.PartLength.Value == pytest.approx(1.25 * 25.4)


def test_profile_is_drawn_on_the_part_even_when_moved(doc):
    src = add_step(doc, "moved")
    prof = make_profile(src)
    prof.Flip = True
    doc.recompute()
    # every vertex of the drawn profile lies on the part's surface
    solid = src.Shape.Solids[0]
    for v in prof.Shape.Vertexes:
        assert solid.distToShape(Part.Vertex(v.Point))[0] < 1e-4
    assert len(prof.Shape.Edges) == len(prof.OD) - 1  # one edge per segment (first line is the start)
    assert any(isinstance(e.Curve, Part.Circle) for e in prof.Shape.Edges)  # the bead stays an arc


def _on_part(prof, src):
    """Every vertex of the drawn profile lies on the part, both in global coordinates."""

    def containers(obj):  # placement of the containers an object sits in
        pl, parent = App.Placement(), obj.getParentGeoFeatureGroup()
        while parent is not None:
            pl = parent.Placement.multiply(pl)
            parent = parent.getParentGeoFeatureGroup()
        return pl

    solid_g = Part.getShape(src).Solids[0].transformGeometry(containers(src).toMatrix())
    for v in prof.Shape.Vertexes:  # Shape vertices already include prof's own placement
        p = containers(prof).multVec(v.Point)
        assert solid_g.distToShape(Part.Vertex(p))[0] < 1e-4


def test_profile_follows_placement(doc):
    src = add_step(doc, "manual")
    src.Placement = App.Placement(App.Vector(40, -15, 7), App.Rotation(App.Vector(1, 2, 0), 35))
    prof = make_profile(src)
    prof.RunOutToStock = False  # the run-out goes out to the bar, off the part by design
    doc.recompute()
    assert prof.OD and not prof.Warnings
    _on_part(prof, src)


def test_profile_goes_in_the_parts_container(doc):
    asm = doc.addObject("App::Part", "Assembly")
    asm.Placement = App.Placement(App.Vector(100, 50, -20), App.Rotation(App.Vector(0, 1, 1), 70))
    src = add_step(doc, "bush")
    asm.addObject(src)
    src.Placement = App.Placement(App.Vector(5, 0, 0), App.Rotation(App.Vector(1, 0, 0), 90))
    prof = make_profile(src)
    prof.RunOutToStock = False
    doc.recompute()
    assert prof in asm.Group and prof.ID
    _on_part(prof, src)
    # moving the assembly moves the profile with it, no recompute needed
    asm.Placement.Base += App.Vector(0, 0, 30)
    _on_part(prof, src)


def test_feature_inside_a_body_means_the_body(doc):
    asm = doc.addObject("App::Part", "Assembly")
    asm.Placement.Base = App.Vector(0, 80, 0)
    body = doc.addObject("PartDesign::Body", "Body")
    asm.addObject(body)
    body.BaseFeature = add_step(doc, "manual")  # Body gets a BaseFeature object inside it
    doc.recompute()
    inner = [o for o in body.Group if o.isDerivedFrom("PartDesign::Feature")][0]
    prof = make_profile(inner)
    prof.RunOutToStock = False
    doc.recompute()
    assert prof.Source is body and prof in asm.Group and prof not in body.Group
    _on_part(prof, body)


def test_stock_runout_and_units(doc):
    src = add_step(doc, "manual")
    prof = make_profile(src)
    prof.Flip = True
    prof.StockDiameter = 1.0 * 25.4
    doc.recompute()
    assert prof.OD[-1] == "line   X1.0000  Z-1.0000"
    prof.Units = "metric"
    doc.recompute()
    assert prof.OD[-1] == "line   X25.400  Z-25.400"


def test_springs_and_bad_index_give_warnings_not_errors(doc):
    src = add_step(doc, "two")
    prof = make_profile(src, solid_index=5)
    doc.recompute()
    assert prof.Shape.isNull() and "out of range" in prof.Warnings[0]
    prof.SolidIndex = 1
    doc.recompute()
    assert not prof.Shape.isNull() and prof.ID


def test_save_to_new_and_existing_job(doc, tmp_path):
    pytest.importorskip("tomli_w")
    src = add_step(doc, "bush")
    prof = make_profile(src)
    prof.Flip = True
    doc.recompute()
    job = tmp_path / "bush.toml"
    assert "Started job BUSH" in save_profiles(prof, job)
    data = tomllib.loads(job.read_text())
    assert data["job"]["name"] == "BUSH" and data["stock"]["diameter"] == 1.0
    assert set(data["profile"]) == {"od", "id"}
    # an existing job keeps everything but the profiles
    job.write_text(job.read_text().replace('name = "BUSH"', 'name = "KEEPME"'))
    prof.Flip = False
    doc.recompute()
    assert "Added od, id" in save_profiles(prof, job)
    data2 = tomllib.loads(job.read_text())
    assert data2["job"]["name"] == "KEEPME" and data2["profile"] != data["profile"]


def test_document_round_trip(doc, tmp_path):
    src = add_step(doc, "manual")
    prof = make_profile(src)
    prof.Flip = True
    doc.recompute()
    od, name = list(prof.OD), prof.Name
    path = str(tmp_path / "part.FCStd")
    doc.saveAs(path)
    App.closeDocument(doc.Name)
    d2 = App.openDocument(path)
    try:
        p2 = d2.getObject(name)
        assert is_profile(p2) and list(p2.OD) == od
        p2.Flip = False
        d2.recompute()
        assert list(p2.OD) != od
    finally:
        App.closeDocument(d2.Name)


def test_job_name():
    assert job_name("Push to Draw-14 solid 3") == "PUSHTODR"
    assert job_name("###") == "PART"
