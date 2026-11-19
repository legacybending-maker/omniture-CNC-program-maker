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


# --------------------------------------------------------------------- import precision (issue #36, defect B)
# These build model chains by hand (model units are mm, radius coordinates)
# and convert them with _to_profile, so they run without a geometry kernel.
import math  # noqa: E402

from omniturn_cam.geometry import LENGTH_TOL, Point, Profile  # noqa: E402
from omniturn_cam.stepimport import ARC_ROUNDING_FACTOR, IMPORT_DECIMALS, Seg, _to_profile  # noqa: E402

MM = 25.4


def _half_circle_chain(r_mm: float, z0: float = -2.54, x_r: float = 12.7, excess_mm: float = 0.0):
    """A face line, then a half circle of radius r_mm along the Z axis at
    radius x_r, then a line on: the shape of a full-radius groove bottom."""
    za, zb = z0, z0 - 2 * r_mm - excess_mm
    return [
        Seg((0.0, x_r), (z0, x_r)),
        Seg((za, x_r), (zb, x_r), "arc", ((za + zb) / 2, x_r), r_mm, "cw"),
        Seg((zb, x_r), (zb - 2.54, x_r)),
    ]


def test_import_rounding_error_fits_inside_the_geometry_tolerance():
    """The derivation in stepimport.py: rounding every number to
    IMPORT_DECIMALS can shift (chord - 2R) by at most ARC_ROUNDING_FACTOR
    half-steps, and that must be smaller than what Profile.issues() allows,
    so rounding alone can never make a valid arc invalid."""
    assert ARC_ROUNDING_FACTOR * 0.5 * 10**-IMPORT_DECIMALS < LENGTH_TOL


def test_rounding_factor_is_a_true_worst_case():
    """Check the derivation's arithmetic by trying it: round thousands of
    random arcs to 3 decimals (big enough steps to see) the way _to_profile
    does (X doubled before rounding) and check (chord - 2R) never moves by
    more than ARC_ROUNDING_FACTOR half-steps. `random.Random(36)` is seeded,
    so the test does the same thing every run."""
    import random

    rng = random.Random(36)
    d, e = 3, 0.5 * 10**-3
    worst = 0.0
    for _ in range(20000):
        z0, r0, z1, r1 = (rng.uniform(-5, 5) for _ in range(4))
        rad = rng.uniform(0.001, 5)
        before = math.hypot(z1 - z0, r1 - r0) - 2 * rad
        x0, x1 = round(2 * r0, d) / 2, round(2 * r1, d) / 2
        after = math.hypot(round(z1, d) - round(z0, d), x1 - x0) - 2 * round(rad, d)
        worst = max(worst, abs(after - before) / e)
    assert worst <= ARC_ROUNDING_FACTOR
    assert worst > 0.5 * ARC_ROUNDING_FACTOR  # and the bound isn't wildly loose


@pytest.mark.parametrize("units, scale", [("inch", 1 / MM), ("metric", 1.0)])
@pytest.mark.parametrize("r_in", [0.0050004, 0.5, 0.00012345])  # the #36 example, ordinary, very small
def test_half_circle_survives_import_in_inch_and_metric(units, scale, r_in):
    """Defect B: r .0050004 across a .0100008 chord became r .005 across
    .010001 after the old 6-decimal rounding, and the shape was refused."""
    prof = _to_profile(_half_circle_chain(r_in * MM), "od", "od", scale)
    assert prof.issues() == [], (units, r_in)
    arc = prof.segments[1]
    assert arc.radius == pytest.approx(r_in * MM * scale, abs=1e-9)


def test_old_rounding_really_did_break_the_example():
    """Documents the bug: rounding end points and radius separately to 6
    decimals makes the #36 example's chord longer than 2R."""
    prof = _to_profile(_half_circle_chain(0.0050004 * MM), "od", "od", 1 / MM, decimals=6)
    assert any("too small for chord" in str(i) for i in prof.issues())


@pytest.mark.parametrize("units, scale", [("inch", 1 / MM), ("metric", 1.0)])
def test_near_half_circle_just_under_the_limit_is_kept(units, scale):
    """An arc of a hair under 180 degrees (chord 2R minus a micron) imports
    as itself."""
    chain = _half_circle_chain(1.0, excess_mm=-1e-6)
    assert _to_profile(chain, "od", "od", scale).issues() == []


@pytest.mark.parametrize("units, scale", [("inch", 1 / MM), ("metric", 1.0)])
def test_arc_genuinely_too_small_for_its_chord_is_still_refused(units, scale):
    """The tolerance is for rounding, not a licence: a radius that's short of
    its chord by a micron (far more than LENGTH_TOL) is refused, in both units."""
    chain = _half_circle_chain(1.0, excess_mm=1e-3)
    probs = _to_profile(chain, "od", "od", scale).issues()
    assert any("too small for chord" in str(i) for i in probs)


def test_imported_values_are_clean_numbers():
    """The rounding that remains clears float noise: 19.05 mm is .75 in, not
    .7500000000000001, so saved jobs and the F2 table show what the print says."""
    prof = _to_profile([Seg((0.0, 0.0), (0.0, 9.525)), Seg((0.0, 9.525), (-25.4, 9.525))], "od", "od", 1 / MM)
    assert prof.segments[0].end == Point(0.75, 0.0) and prof.segments[1].end.z == -1.0
    assert isinstance(prof, Profile)
