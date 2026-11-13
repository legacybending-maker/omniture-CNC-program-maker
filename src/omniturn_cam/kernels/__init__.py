"""Geometry kernels for STEP import.

The profile extraction in ``stepimport`` needs only three things from a CAD
kernel, so it never imports one directly:

* ``read_solids(path)``   - the solids in a STEP file
* ``faces(solid)``        - each face's type, area and axis / normal / centre
* ``section(solid, origin, normal)`` - the edges where a plane cuts the solid

Two kernels implement that:

* ``ocp``     - OpenCascade through the ``cadquery-ocp`` package (standalone app)
* ``freecad`` - FreeCAD's own Part module (the FreeCAD workbench, or running
                the CLI with FreeCAD's Python)

Both wrap the same OpenCascade underneath, so they give the same profiles; the
test suite runs the same STEP files through each one it can import.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol


@dataclass
class SolidInfo:
    index: int
    name: str
    volume: float  # mm^3
    size: tuple  # bounding box dx, dy, dz in mm
    shape: object = field(repr=False, default=None)


@dataclass
class FaceInfo:
    """kind: cylinder | cone | torus | revolution | plane | sphere | other.
    axis = (point, unit dir) for the round kinds; normal for planes; center for spheres."""

    kind: str
    area: float
    axis: tuple | None = None
    normal: tuple | None = None
    center: tuple | None = None


@dataclass
class EdgeInfo:
    """One edge of a planar cut. value(t) gives the 3D point (mm) at parameter t.
    kind: line | circle | other; circles also give centre (3D) and radius, and
    their parameter is the angle in radians."""

    kind: str
    t0: float
    t1: float
    value: Callable[[float], tuple]
    center: tuple | None = None
    radius: float = 0.0


class Kernel(Protocol):
    name: str

    def read_solids(self, path) -> list[SolidInfo]: ...
    def faces(self, solid) -> list[FaceInfo]: ...
    def section(self, solid, origin: tuple, normal: tuple) -> list[EdgeInfo]: ...


class KernelUnavailable(ImportError):
    pass


def get(name: str | None = None) -> Kernel:
    """The named kernel, or the first one that imports (OCP, then FreeCAD)."""
    names = [name] if name else ["ocp", "freecad"]
    errors = []
    for n in names:
        try:
            if n == "ocp":
                from .ocp import OCPKernel

                return OCPKernel()
            if n == "freecad":
                from .freecad import FreeCADKernel

                return FreeCADKernel()
            raise KernelUnavailable(f"unknown geometry kernel {n!r}")
        except ImportError as e:
            errors.append(f"{n}: {e}")
    raise KernelUnavailable(
        'STEP import needs OpenCascade: pip install cadquery-ocp (or pip install -e ".[step]"), '
        "or run inside FreeCAD. (" + "; ".join(errors) + ")"
    )
