# Architecture

omniturn-cam turns a description of a turned part into a program for an
OmniTurn G4 control. The code it writes moves a real machine, so the design
favours being predictable and checkable over being clever.

This file is the source of truth for how the code is laid out and the rules
that hold it together. If you change the structure, change this file in the
same pull request.

## Data flow

```
 STEP / STL / 3MF ──► modelimport ──► stepimport / meshimport ──► Profile (geometry.py)
                         (kernels/ for STEP)                         │
 hand-entered shape (GUI F2, job TOML) ──────────────────────────────┤
                                                                     ▼
 tools.toml ──► ToolLibrary ──► Setup (stations) ──►  Job (job.py) ◄── operations (ops.py)
                                                       │   saved/loaded as job TOML
                                                       ▼
                                   post/omniturn.py  (MachineConfig from machine.toml)
                                                       │
                                                       ▼
                                   lint.py  (every generated program; errors block output)
                                                       │
                                                       ▼
                                   program text ──► USB stick (CRLF, 8-char name)
                                                       │
                                   backplot.py  (reads the program back to draw it)
```

Three front ends drive the same core: the command line (`cli.py`), the
standalone PySide6 app (`gui/`), and the FreeCAD workbench
(`freecad_workbench/`). The job TOML file is the hand-off between them.

## Layers and the rules between them

| Layer | Where | May import |
|---|---|---|
| Core | `src/omniturn_cam/*.py`, `post/` | the standard library, `tomli_w`, `numpy`; `matplotlib` only inside functions |
| Geometry kernels | `src/omniturn_cam/kernels/` | core + one CAD kernel each (`ocp.py`: cadquery-ocp, `freecad.py`: FreeCAD `Part`) |
| Standalone GUI | `src/omniturn_cam/gui/` | core + PySide6 + matplotlib. CAD files only through `modelimport` |
| FreeCAD workbench | `freecad_workbench/` | core + FreeCAD + FreeCAD's `PySide` shim. Never PySide6 or `omniturn_cam.gui` |

`tests/test_layering.py` enforces these rules by reading the source. Why they
matter: the core has to run in the CLI with nothing optional installed, in the
PySide6 app, and inside FreeCAD's own Python, from one copy of the code. A Qt
or kernel import in the core breaks at least one of those.

The only kernel-specific calls are the three in `kernels/`:
`read_solids(path)`, `faces(solid)`, `section(solid, origin, normal)`.
`kernels.get()` picks OCP first, then FreeCAD.

## Modules

| Module | Job |
|---|---|
| `geometry.py` | `Profile`: a start point plus `Line` / `Arc` segments, X as diameter, arcs in print terms. Validation, bounds, undercut check, tessellation |
| `ops.py` | One dataclass per operation (what the operator fills in). `OPERATION_TYPES` maps the job-file `type` to the class |
| `fields.py` | Labels, units, help text, menu entries and tool kinds for every operation field. The GUI builds its forms from this |
| `tools.py` | Tool library (`tools.toml`) and the tool plate (`Setup` of `Station`s, with D offsets) |
| `job.py` | `Job`: units, stock, setup, named profiles, ordered operations. Reads/writes job TOML, `JOB_FORMAT` version |
| `post/omniturn.py` | `MachineConfig` (machine.toml) and `OmniTurnPost`: one `op_*` method per operation, modal-state tracking, subroutines below M30 |
| `lint.py` | Checks any program (generated or hand-written) against the manual's formatting rules |
| `backplot.py` | Interprets a program back into moves for drawing and for the travel check |
| `stepimport.py`, `meshimport.py`, `modelimport.py` | 3D model → OD/ID profiles |
| `cli.py` | `omniturn-cam post / lint / plot / gui / step` |
| `gui/` | F-key step screens (job, shape, plate, processes, check, program), tool library editor, STEP dialog |

## Invariants

These hold for every program the post writes. Each has a test.

1. **Output is deterministic.** The same job, tool library and machine profile
   always give byte-identical output. No timestamps, no dict-order surprises.
   (`tests/test_golden.py`)
2. **Output follows the manual.** Every generated program passes `lint` with no
   errors before it is returned; a rule break raises `PostError` instead of
   writing code. Rules with page references: `docs/omniturn-post-rules.md`.
3. **Errors refuse, they don't guess.** When the post can't do what was asked
   safely (wrong quadrant, thread feed over the limit, no D offset for comp),
   it raises `PostError` with what's wrong and the manual page. Questionable
   but legal things become warnings shown to the operator.
4. **Saving a job never changes its program.** (`tests/test_jobfile.py`)
5. **Old job files keep loading.** `[job] format` is versioned; a newer file is
   refused with a clear message rather than misread.
6. **Proven programs don't change silently.** Programs run on the machine are
   listed in `tests/golden/verified.toml`; changing their output fails a test
   until the entry is removed on purpose.

## Where machine facts live

- `docs/omniturn-post-rules.md`: the G4 dialect, with manual page references
  and CONFIRMED / VERIFY marks. Any rule the post follows must be in here.
- `src/omniturn_cam/data/default_machine.toml` and `MachineConfig`: limits of
  the reference machine (Hardinge HLV-H with OmniTurn G4, 4000 rpm VFD).
- The OmniTurn manuals themselves aren't in the repo (copyright). Cite pages.

## Adding an operation (current procedure)

1. Dataclass in `ops.py`; add it to `OPERATION_TYPES`.
2. Field labels/help in `fields.py` (`FIELDS` or `OVERRIDES`), a menu entry in
   `OP_MENU`, suitable tool kinds in `OP_TOOL_KINDS`, a line in `summary()`.
3. An `op_<kind>` method in `post/omniturn.py`, registered in `HANDLERS`.
4. The rule it relies on, with manual page, in `docs/omniturn-post-rules.md`.
5. Use it in a golden job (`tests/golden/jobs/`) and regenerate the expected
   output; add focused tests in `tests/test_post.py` for its edge cases.
