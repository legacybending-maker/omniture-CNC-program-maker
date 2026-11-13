"""The architecture rules, as tests (see docs/ARCHITECTURE.md).

The core (everything in src/omniturn_cam outside gui/ and kernels/) must run
with no GUI toolkit and no CAD kernel, so the same code serves the CLI, the
standalone GUI and the FreeCAD workbench. These tests read the source, so they
catch imports hidden inside functions too.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

PKG = Path(__file__).parent.parent / "src" / "omniturn_cam"
WORKBENCH = Path(__file__).parent.parent / "freecad_workbench"

GUI_TOOLKITS = {"PySide6", "PySide2", "PySide", "PyQt5", "PyQt6", "shiboken6", "tkinter"}
CAD_KERNELS = {"OCP", "cadquery", "FreeCAD", "FreeCADGui", "Part", "Mesh", "OCC"}
# Optional extras: allowed in core only inside a function, so importing the
# module never needs them.
LAZY_ONLY = {"matplotlib"}
# Entry points that may start the GUI, lazily, for a sub-command.
GUI_LAUNCHERS = {"cli.py"}


def _imports(path: Path):
    """(top-level module name, imported at module level?, relative-import target, line) per import."""
    tree = ast.parse(path.read_text(), str(path))
    top_level = {id(n) for n in tree.body}
    # imports inside `if TYPE_CHECKING:` / try at module level count as module level
    for node in tree.body:
        if isinstance(node, (ast.If, ast.Try)):
            top_level |= {id(n) for n in ast.walk(node)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield a.name.split(".")[0], id(node) in top_level, None, node.lineno
        elif isinstance(node, ast.ImportFrom):
            if node.level:   # relative: resolve against the package
                base = path.parent
                for _ in range(node.level - 1):
                    base = base.parent
                target = base / (node.module or "").replace(".", "/") if node.module else base
                yield None, id(node) in top_level, target.resolve(), node.lineno
            else:
                yield node.module.split(".")[0], id(node) in top_level, None, node.lineno


def _core_files():
    for p in sorted(PKG.rglob("*.py")):
        rel = p.relative_to(PKG).parts
        if rel[0] not in ("gui", "kernels"):
            yield p


def _rel(p: Path) -> str:
    return str(p.relative_to(PKG.parent.parent))


@pytest.mark.parametrize("path", list(_core_files()), ids=_rel)
def test_core_imports_no_gui_or_cad_kernel(path):
    bad = []
    for mod, at_top, target, line in _imports(path):
        if mod in GUI_TOOLKITS | CAD_KERNELS:
            bad.append(f"line {line}: imports {mod}")
        elif mod in LAZY_ONLY and at_top:
            bad.append(f"line {line}: imports optional {mod} at module level (import it inside the function)")
        elif target is not None and (PKG / "gui") in [target, *target.parents]:
            if path.name not in GUI_LAUNCHERS or at_top:
                bad.append(f"line {line}: imports from gui/")
    assert not bad, f"{_rel(path)} breaks the core layering rule:\n  " + "\n  ".join(bad)


@pytest.mark.parametrize("path", sorted((PKG / "kernels").glob("*.py")), ids=_rel)
def test_kernels_import_no_gui(path):
    bad = [f"line {line}: {mod}" for mod, _, target, line in _imports(path) if mod in GUI_TOOLKITS]
    assert not bad, f"{_rel(path)}: kernel adapters must not import a GUI toolkit: {bad}"


@pytest.mark.parametrize("path", sorted((PKG / "gui").rglob("*.py")), ids=_rel)
def test_gui_imports_no_cad_kernel_directly(path):
    """The GUI reaches CAD files through modelimport/kernels, never a kernel itself."""
    bad = [f"line {line}: {mod}" for mod, _, _, line in _imports(path) if mod in CAD_KERNELS]
    assert not bad, f"{_rel(path)}: go through omniturn_cam.kernels instead: {bad}"


@pytest.mark.parametrize("path", sorted(WORKBENCH.rglob("*.py")), ids=lambda p: str(p.relative_to(WORKBENCH.parent)))
def test_workbench_does_not_use_standalone_gui(path):
    """The workbench runs in FreeCAD's Qt; it must not pull in the PySide6 standalone app."""
    bad = []
    for mod, _, _, line in _imports(path):
        if mod in {"PySide6", "PyQt5", "PyQt6"}:
            bad.append(f"line {line}: imports {mod} (use FreeCAD's PySide shim)")
    src = path.read_text()
    if "omniturn_cam.gui" in src:
        bad.append("imports omniturn_cam.gui (the standalone PySide6 app)")
    assert not bad, f"{path.name}:\n  " + "\n  ".join(bad)
