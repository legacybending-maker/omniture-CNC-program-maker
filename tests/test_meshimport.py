"""Mesh (STL / 3MF) import compared with the exact STEP import of the same parts."""

import numpy as np
import pytest

pytest.importorskip("OCP")

import stepparts as sp  # noqa: E402

from omniturn_cam.geometry import Arc  # noqa: E402
from omniturn_cam.meshimport import import_mesh  # noqa: E402
from omniturn_cam.modelimport import import_model  # noqa: E402
from omniturn_cam.stepimport import StepImportError, import_step  # noqa: E402

TOL = 0.0025  # inch: what a mesh import holds against the exact CAD outline (arcs within ~.002")


def deviation(a, b):
    """Largest distance (inch) from any point on outline a to outline b, both ways."""
    pa = np.array(a.polyline(2.0)) * [1, 0.5]  # (z, diameter) -> (z, radius)
    pb = np.array(b.polyline(2.0)) * [1, 0.5]

    def dense(p):
        out = [p[0]]
        for q in p[1:]:
            n = max(1, int(np.linalg.norm(q - out[-1]) / 0.002))
            out.extend(out[-1] + (q - out[-1]) * k / n for k in range(1, n + 1))
        return np.array(out)

    pa, pb = dense(pa), dense(pb)
    d = np.linalg.norm(pa[:, None, :] - pb[None, :, :], axis=2)
    return max(d.min(1).max(), d.min(0).max())


def best_end(path, ref):
    """Mesh axis direction is arbitrary: compare whichever end-for-end import is closer."""
    results = [import_model(path, flip=f) for f in (True, False)]
    return min(results, key=lambda r: deviation(r.od, ref.od))


@pytest.fixture(scope="module")
def parts(tmp_path_factory):
    d = tmp_path_factory.mktemp("mesh")
    return {
        "manual_step": sp.write(d / "manual.step", sp.manual_part()),
        "bush_step": sp.write(d / "bush.step", sp.bushing()),
        "manual_stl": sp.write_stl(d / "manual.stl", sp.manual_part()),
        "manual_ascii": sp.write_stl(d / "manual_ascii.stl", sp.manual_part(), ascii=True),
        "moved_stl": sp.write_stl(d / "moved.stl", sp.move(sp.manual_part(), 10, -5, 30, (1, 1, 0), 63)),
        "bush_3mf": sp.write_3mf(d / "bush.3mf", sp.bushing()),
    }


@pytest.mark.parametrize("mesh", ["manual_stl", "manual_ascii", "moved_stl"])
def test_stl_outline_matches_step(parts, mesh):
    ref = import_step(parts["manual_step"], flip=True)
    r = best_end(parts[mesh], ref)
    assert deviation(r.od, ref.od) < TOL
    assert abs(r.max_dia - ref.max_dia) < TOL and abs(r.length - ref.length) < TOL
    bead = [s for s in r.od.segments if isinstance(s, Arc)]
    assert bead and all(abs(a.radius - 0.0935) < TOL and a.direction == "ccw" for a in bead)
    assert any("mesh" in w for w in r.warnings)
    assert not any("isn't turned" in w for w in r.warnings)


def test_3mf_with_item_transform_gives_od_and_id(parts):
    ref = import_step(parts["bush_step"], flip=True)
    r = best_end(parts["bush_3mf"], ref)
    assert deviation(r.od, ref.od) < TOL
    assert r.id is not None and deviation(r.id, ref.id) < TOL


def test_solidworks_part_files_get_a_clear_message(tmp_path):
    p = tmp_path / "part.SLDPRT"
    p.write_bytes(b"\x7f\xa2\xc4\xe9")
    with pytest.raises(StepImportError, match="STEP"):
        import_model(p)
