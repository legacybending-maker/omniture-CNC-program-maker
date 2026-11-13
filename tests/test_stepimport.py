"""STEP import against the parts in tests/data (made by stepparts.py).

Every test runs once per geometry kernel that imports: OCP in the normal dev
environment, FreeCAD when pytest runs under FreeCAD's Python. Both must give
the same profiles.
"""

from functools import partial
from pathlib import Path

import pytest

from omniturn_cam import stepimport
from omniturn_cam.geometry import Arc, Line  # noqa: F401
from omniturn_cam.stepimport import StepImportError

DATA = Path(__file__).parent / "data"

MANUAL_OD = [
    ("L", 0.093, 0),
    ("L", 0.343, -0.125),
    ("L", 0.343, -0.3),
    ("L", 0.406, -0.3),
    ("A", 0.593, -0.3935, "ccw"),
    ("A", 0.406, -0.487, "ccw"),
    ("L", 0.375, -0.487),
    ("L", 0.375, -0.75),
    ("L", 0.75, -0.75),
    ("L", 0.75, -1.0),
]


class _Num(float):
    """Compares equal within .0001", so a value that sits exactly on a rounding
    boundary (e.g. -.3935 coming out as -.39349999) can't flip the test."""

    def __eq__(self, other):
        if not isinstance(other, (int, float)):
            return NotImplemented
        return abs(self - other) < 1e-4

    __hash__ = float.__hash__


def flat(profile):
    out = []
    for s in profile.segments:
        k = "A" if isinstance(s, Arc) else "L"
        t = (k, _Num(s.end.x), _Num(s.end.z))
        out.append(t + ((s.direction,) if k == "A" else ()))
    return out


@pytest.fixture(scope="module")
def parts():
    return {name: DATA / f"{name}.step" for name in ("manual", "moved", "bush", "features", "blind", "two")}


@pytest.fixture(params=["ocp", "freecad"], autouse=True)
def kernel(request, monkeypatch):
    """Run each test with one kernel; import_step / read_solids below use it."""
    pytest.importorskip({"ocp": "OCP", "freecad": "FreeCAD"}[request.param])
    monkeypatch.setitem(globals(), "import_step", partial(stepimport.import_step, kernel=request.param))
    monkeypatch.setitem(globals(), "read_solids", partial(stepimport.read_solids, kernel=request.param))
    return request.param


import_step = stepimport.import_step
read_solids = stepimport.read_solids


def test_manual_part_matches_drawing(parts):
    r = import_step(parts["manual"], flip=True)
    assert (r.od.start.x, r.od.start.z) == (0, 0)
    assert flat(r.od) == MANUAL_OD
    assert r.id is None and r.max_dia == 0.75 and r.length == 1.0 and not r.warnings
    assert all(round(s.radius, 4) == 0.0935 for s in r.od.segments if isinstance(s, Arc))


def test_axis_found_when_part_is_tilted_and_moved(parts):
    assert flat(import_step(parts["moved"], flip=True).od) == MANUAL_OD


def test_flip_puts_z0_at_the_other_end(parts):
    r = import_step(parts["manual"], flip=False)
    assert r.od.segments[0].end.x == 0.75 and r.length == 1.0


def test_bushing_gives_od_and_id(parts):
    r = import_step(parts["bush"], flip=True)
    assert (r.od.start.x, r.od.start.z) == (0.56, 0)
    assert flat(r.od) == [("L", 0.96, 0), ("L", 1.0, -0.02), ("L", 1.0, -1.25)]
    assert r.id.side == "id" and flat(r.id) == [("L", 0.5, -0.03), ("L", 0.5, -1.25)]


def test_blind_drilled_hole_and_dome(parts):
    r = import_step(parts["blind"], flip=True)
    assert flat(r.id)[-1][1:] == (0.0, pytest.approx(-0.5751, abs=1e-3))  # drill point on centre
    r2 = import_step(parts["blind"], flip=False)
    first = r2.od.segments[0]
    assert isinstance(first, Arc) and first.direction == "ccw" and round(first.radius, 4) == 0.5


def test_cross_hole_and_flat_reported_profile_kept(parts):
    r = import_step(parts["features"], flip=True)
    assert flat(r.od) == MANUAL_OD
    text = " ".join(r.warnings)
    assert "not turned" in text and "cross hole" in text and "flat" in text


def test_multiple_solids_must_be_chosen(parts):
    solids = read_solids(parts["two"])
    assert len(solids) == 2
    with pytest.raises(StepImportError, match="2 solids"):
        import_step(parts["two"])
    assert import_step(parts["two"], solid_index=1, flip=True).id is not None


def test_metric_units(parts):
    r = import_step(parts["manual"], flip=True, units="metric")
    assert r.max_dia == pytest.approx(19.05) and r.length == pytest.approx(25.4)


def test_imported_profile_posts_clean(parts):
    from omniturn_cam.job import Job, Stock
    from omniturn_cam.lint import lint
    from omniturn_cam.ops import Finish, Rough
    from omniturn_cam.post import post
    from omniturn_cam.tools import Setup, Station, Tool

    r = import_step(parts["manual"], flip=True)
    setup = Setup()
    setup.add(Station(1, Tool("t", "turn", "turn", nose_radius=0.0156), d=1))
    job = Job(
        "STEP1",
        "inch",
        Stock(0.75),
        setup,
        {"od": r.od},
        [Rough(t=1, rpm=2000, profile="od", start_x=0.75, start_z=0.05), Finish(t=1, rpm=2500, profile="od")],
    )
    code = post(job)
    assert "g02x.593z-.3935r.0935" in code and not [p for p in lint(code) if p.severity == "error"]
