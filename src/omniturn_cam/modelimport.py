"""One entry point for every 3D model the shape import accepts: STEP (exact CAD
solids) and STL / 3MF (triangle meshes, e.g. 3D-print files)."""

from __future__ import annotations

from pathlib import Path

MODEL_FILTER = "3D models (*.step *.stp *.STEP *.STP *.stl *.STL *.3mf *.3MF)"
NOT_SUPPORTED = {
    ".sldprt": "SolidWorks part files can't be read outside SolidWorks. Use File > Save As > STEP (AP214).",
    ".sldasm": "SolidWorks assemblies can't be read outside SolidWorks. Save the part as STEP (AP214).",
    ".fcstd": "Export the body from FreeCAD with File > Export > STEP.",
    ".ipt": "Inventor part files can't be read directly. Export as STEP.",
    ".f3d": "Fusion files can't be read directly. Export as STEP.",
}


def is_mesh(path) -> bool:
    from .meshimport import MESH_SUFFIXES

    return Path(path).suffix.lower() in MESH_SUFFIXES


def _check(path):
    from .stepimport import StepImportError

    hint = NOT_SUPPORTED.get(Path(path).suffix.lower())
    if hint:
        raise StepImportError(hint)


def list_bodies(path):
    """Solids (STEP) or mesh objects (STL/3MF), each with .index .name .size .volume."""
    _check(path)
    if is_mesh(path):
        from .meshimport import read_mesh

        return read_mesh(path)
    from .stepimport import read_solids

    return read_solids(path)


def import_model(path, index=None, units="inch", flip=False):
    _check(path)
    if is_mesh(path):
        from .meshimport import import_mesh

        return import_mesh(path, index, units, flip)
    from .stepimport import import_step

    return import_step(path, index, units, flip)
