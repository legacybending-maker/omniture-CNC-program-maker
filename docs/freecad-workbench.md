# FreeCAD workbench: design decisions and findings

> General architecture, layering rules and the workflow live in
> [ARCHITECTURE.md](ARCHITECTURE.md), [CONTRIBUTING.md](../CONTRIBUTING.md) and
> [CLAUDE.md](../CLAUDE.md); they are the source of truth. This doc keeps the
> FreeCAD-specific decisions and test findings. Open work is tracked as GitHub
> issues, not here.

Goal: ship omniturn-cam as a FreeCAD add-on as well as the standalone app, from the same repo and the same core code. That's the quickest way to get it in front of people, since FreeCAD's only lathe CAM (dubstar-04 Turning Addon) is a proof of concept stuck on 0.21.

**Status (2026-11-13):** merged to `main` in PR #1. CI runs the full suite inside conda-forge FreeCAD on every PR (the `FreeCAD workbench` job).

## Layout
- `src/omniturn_cam/kernels/`: the only kernel-specific code, three calls each: `read_solids(path)`, `faces(solid)`, `section(solid, origin, normal)`. `ocp.py` = cadquery-ocp (standalone app); `freecad.py` = FreeCAD Part. `kernels.get()` picks OCP, then FreeCAD, so the CLI also runs under FreeCAD's Python.
- `stepimport.finish_loop()` is shared by STEP and mesh import; STEP passes `axis`/`radial` so results know where the cut lies (the workbench draws the profile from that).
- `freecad_workbench/`: FreeCAD front end only (Init/InitGui, `omniturn_freecad`: feature, view provider, task panel, commands, export, icons). Adds `../src` to sys.path so workbench and core always come from the same checkout. Uses FreeCAD's `PySide` shim, never PySide6 (enforced by `tests/test_layering.py`).
- `package.xml` at the repo root, so the repo is the add-on (classic "DirMod" layout; FreeCAD ≥ 1.0 because the core needs Python 3.11 `tomllib`). Python dependency: `tomli_w`, installed by the Addon Manager.

## Decisions
- **Own workbench, not inside FreeCAD's CAM (Path) workbench.** CAM's Job/ToolController/toolbit model is mill-centred and its API changes between releases; box tools and die heads don't fit it. A bridge can come later if CAM gets real lathe support.
- **Keep the FreeCAD layer thin**, so FreeCAD API changes (e.g. Qt5→Qt6, `Circle.Center` deprecated in 26.3) only touch a few files.
- **The profile is a parametric FeaturePython object** ("Turned profile"): links to the source solid, recomputes when the model changes, saves with the .FCStd. Properties: Source, SolidIndex, Units, Flip, StockDiameter, RunOutToStock; read-only results OD/ID/Warnings/MaxDiameter/PartLength.
- **The profile goes in the same container as the part** (App::Part), so it's drawn on the part and moves with it. A feature inside a PartDesign Body means the whole Body.
- **The task panel edits the object live**, with the document's undo transaction as Cancel; the 3D view is the preview.
- **Hand-off is the job TOML file**: a new file starts a job (8-char name, bar size, profiles); an existing job keeps its tools/ops and only gets new profiles (`job.merge_profiles`, shared with the CLI `--into`).

## Found while testing on "Push to Draw-14.STEP" (12 solids)

(The STEP file itself isn't in the repo; ask Jeff for it.)

- 3 solids are coil springs (82–92 % free-form surface). The OCP section **hung** on one of them. The core now refuses any solid that is more than 50 % free-form, with a clear message, before trying to cut it.
- A fully turned part gets 1 cut instead of 12 (the 12-angle search only runs when flats or cross holes are found). The whole assembly imports in ~10 s.
- Both kernels give identical profiles on all 12 solids.
- PTD Nozzle: the ID opens out behind the .1875 orifice at the default Z0 end, so it would be bored from the other end. The undercut warning catches Z turning back toward the face, but not a bore that gets bigger behind a smaller hole (issue #4).

## Tests
- STEP fixtures in `tests/data/` (rebuild: `python tests/stepparts.py`); `test_stepimport.py` runs once per available kernel.
- `test_freecad_workbench.py`: the object against the standalone import, placements, App::Part containers, PartDesign Body, stock run-out and units, bad solid index, new/existing job export, save + reopen.
- Real-GUI check (FreeCAD 26.3 under Xvfb): workbench activates, Extract Profile → task panel → Flip → OK → Save to job all work. Screenshot in `docs/screenshots/freecad-workbench.png`.

## Open work (GitHub issues)
- #6 Try the workbench on the shop laptop's FreeCAD (needs machine check)
- #7 Submit to the FreeCAD-addons index
- #8 Programming screens inside FreeCAD
- #12 Extract profiles from Mesh objects (STL/3MF)
- `package.xml` maintainer email is a GitHub noreply placeholder; change if wanted.
