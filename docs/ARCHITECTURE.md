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
 tools.toml ──► ToolLibrary ──► Setup (stations) ──►  Job (job.py) ◄── operations/ (one file each)
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
| `operations/` | One module per operation type: its dataclass (what the operator fills in), its field labels, menu entry, tool kinds, process-list summary, tool-based defaults and its OmniTurn emitter, registered as one `SPEC = OpSpec(...)`. The package finds the modules itself. `OPERATION_TYPES` maps the job-file `type` to the class |
| `ops.py` | The operation classes under their old import path (re-exports `operations/`) |
| `fields.py` | Labels, units and help text for the fields several operations share; builds `OP_MENU`, `OP_TOOL_KINDS` and `OVERRIDES` from the specs. The GUI builds its forms from this |
| `gcode.py` | Number format, comment rules, `PostError`, and how a program line is read: `code_part()` and `subroutine_start()`, the one definition of a `}n` declaration that lint, the backplot, `Job.external_subs` and `post/external.py` all use |
| `validation.py` | The validation vocabulary: `Issue`, `ValidationError`, type checks from type hints, the per-quantity rules (feeds > 0...), `CheckContext`. Imports nothing from the project (the operation modules import it while `operations/` is loading) |
| `opcheck.py` | Runs the three validation layers over a job's operations; called by `Job.issues()` |
| `tools.py` | Tool library (`tools.toml`) and the tool plate (`Setup` of `Station`s, with D offsets) |
| `fileio.py` | `write_text_atomic` / `write_bytes_atomic`: write to a temporary file beside the target, fsync, then `os.replace`, so a failed save never leaves a half-written job, library or program. Used by `save_job`, `ToolLibrary.save`, the GUI's program writer and `post -o` |
| `job.py` | `Job`: units, stock, setup, named profiles, ordered operations. Reads/writes job TOML, `JOB_FORMAT` version. `include_subs` is kept as written; `base_dir` (the job file's folder, not saved) resolves a relative one (`Job.include_path`) |
| `post/omniturn.py` | `MachineConfig` (machine.toml) and `OmniTurnPost`: header/footer, motion and tool-call helpers the operations use, the mode-establishing helpers, the handoff to raw code and M98 (`park_before_handoff`, `resume_after`), subroutines below M30. Dispatches each operation to its spec's `emit` |
| `post/state.py` | `MachineState`: every mode the post relies on, the slide position and the tool, each known or `UNKNOWN` (a value that refuses to be used as true or false) |
| `post/external.py` | Reads raw lines and include-file subroutines and says which `MachineState` fields they could change. Only ever forgets; anything it can't account for forgets everything |
| `lint.py` | Checks any program (generated or hand-written) against the manual's formatting rules |
| `backplot.py` | Interprets a program back into moves for drawing and for the travel check. Never guesses: anything it can't read truthfully is a `ProgramError` with the line number (`UnsupportedProgram` for legal code it can't follow); arcs (R or I/K) are drawn round the centre the words give, with a rounding allowance worked out from the program's decimals |
| `stepimport.py`, `meshimport.py`, `modelimport.py` | 3D model → OD/ID profiles |
| `cli.py` | `omniturn-cam post / lint / plot / gui / step` |
| `gui/` | `app.py` main window and F-key bar; `screens/` one module per F-key step screen (job, shape, plate, processes, check, program); `forms.py` builds an operation's form from its fields; `widgets.py` the committing editors; tool library editor, STEP dialog. See "The GUI and the job" |

## Invariants

These hold for every program the post writes. Each has a test.

1. **Output is deterministic.** The same job, tool library and machine profile
   always give byte-identical output. No timestamps, no dict-order surprises.
   (`tests/test_golden.py`)
2. **Output follows the manual.** Every generated program passes `lint` with no
   errors before it is returned; a rule break raises `PostError` instead of
   writing code. Lint runs before the travel check reads the program back
   (`post()`), so nothing malformed reaches the interpreter. Rules with page
   references: `docs/omniturn-post-rules.md`.
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
7. **The post only relies on what it knows.** Every mode and the slide position
   can be unknown; a mode is skipped only when the post knows it's in effect,
   and the slide is never moved from an unknown position. After raw code or a
   subroutine the post forgets what that code could change and the next
   operation re-establishes it, or the post refuses
   (`docs/omniturn-post-rules.md`, "Handing control to code the post didn't
   write"; `tests/test_post.py`).
8. **Every operation ends where the next tool change is safe** (Stage 5,
   issue #40). Tool changes retract in Z alone to the tool-change Z from
   wherever the last operation left the slide (`OmniTurnPost.call_tool`), so
   each operation must end where that straight Z retract is in free air,
   with comp off (G40) and rpm mode (G97). This used to be true only because
   each operation happened to be written that way; it is now a contract,
   checked for every registered operation by `tests/test_machine_safety.py`
   (a new operation fails there until it's added to the table):

   | Operation | Ends at | Why a Z-alone retract is clear |
   |---|---|---|
   | face | its start X, z_clear in front of the last face pass | in front of the face just cut |
   | rough (G75/G78), box rough (G74) | the cycle start point | the cycle returns there (2.39, 2.41); the start is outside the shape |
   | finish | the lead-off X, at its approach Z | approach Z is in front of the shape |
   | drill (G81/G83) | X0 at its start Z | the cycle returns there (2.48-2.50) |
   | tap, axial | its X at its start Z (axial with a stop: tool-change Z) | back up the line it fed down |
   | thread (G33) | its clearance X at its last start Z | the line it approached on; ID clearance never crosses X0 |
   | radial, cutoff | its start X at its Z | back out along the line it fed in on |
   | raw, call sub | unknown unless declared `tool_change_z` | parked at the tool-change Z before; never moved from an unknown position after (invariant 7) |

   Known limit: radial and thread trust their start X. A Start diameter
   typed *below* a larger shoulder nearer the face would make the Z retract
   cross it; the post doesn't model the part's material, so it can't see that.

## Validation

Nothing reaches G-code generation until it has been checked. `Job.issues(machine)`
returns every problem as an `Issue` (where, setting, value, problem, fix,
error or warning); `Job.validate()` raises `ValidationError` (a `ValueError`)
listing all the errors. The post runs it in `post()` and again in
`OmniTurnPost.run()` with the machine, and refuses with `PostError` before
writing a line. Three layers, kept apart:

1. **Scalar and type** (`validation.py`, generic). Each field's type hint
   decides its type (float must be finite, int must be whole and not a bool,
   bool must be a bool, `Literal` must be one of its choices, `X | None` may be
   blank). The field's `FieldInfo.kind` adds the rule for its quantity: rpm,
   sfm and feeds > 0, seconds ≥ 0, stations 1-32. Coordinates get no sign
   rule. A new operation is covered without writing anything.
2. **Operation geometry** (`OpSpec.validate`, one per module). Rules about the
   operation itself on any machine: a drill depth below its start, a thread
   minor below its major, a cutoff feeding inward. Direction rules belong to
   the operation that has them, never to all of them.
3. **Machine** (`OpSpec.validate_machine`, plus checks every operation shares
   in `opcheck.py`). Feeds against the control's range at this rpm, CSS limits
   against `max_rpm`, defaults that come from machine.toml clearances. Runs
   only when layers 1-2 found no errors in that operation.

Shapes (`Profile.issues`), the job itself (name, units, stock, stations),
tools (`Tool.issues`, run when a `Tool` is made) and machine profiles
(`MachineConfig.issues`, run when one is made; `MachineConfig.load` adds the
file and section) use the same `Issue`. A machine profile the operator asked
for (`--machine`, or their ~/.config file) that can't be used is always fatal.

Precision: shapes are kept at full precision and checked with
`geometry.LENGTH_TOL` (floating-point noise only); numbers are rounded only
when the program is written (`MachineConfig.decimals`). Model import rounds to
`stepimport.IMPORT_DECIMALS` just to clear float noise, and the comment there
shows that rounding can't move an arc across `LENGTH_TOL`. Reading a written
program back (`backplot.py`) allows for the rounding the program's decimals
imply, and no more.

## Units

A `Job` has one `units` (`"inch"` or `"metric"`), and every number in it
(stock, profiles, operation fields) is in those units. The core never
converts a stored value. `job.units` decides:

- which `MachineConfig` section applies (`MachineConfig.load(path, units)`
  picks `[inch]` or `[metric]`; `Job.issues` refuses a profile loaded for the
  other units),
- G70/G71 in the program and the scale of every unit-dependent constant (the
  sfm→rpm factor, `CheckContext.per_unit` thresholds, the control's feed
  limits),
- the starting values of new operations (`new_operation(..., units=)`,
  `OpSpec.metric_defaults`) and the GUI's unit labels (`gui/widgets.py`
  `UNIT_SUFFIX`).

Changing F1 units sets `job.units` and nothing else
(`tests/test_gui.py`, `test_changing_units_keeps_values_and_new_processes_use_the_new_units`).
Converting silently would change numbers the operator typed without telling
them (CONTRIBUTING.md, "Usability"). The only conversion is at the edge:
model import (`stepimport.finish_loop`) turns model millimetres into the
job's units once, when the shape is made. `Tool.units` is stored but not
used, so tool library values (nose radius, diameter, `pitch`) are taken in
the job's units as written. What this means for the operator is in the
README, "Inch and metric".

## The GUI and the job

The standalone GUI edits `MainWindow.job` in place; the core never sees a
widget. Three rules keep what the operator sees and what the job holds the
same (issue #38, `tests/test_gui.py`):

1. **Edits are committed before anything reads the job.** Every editor that
   can hold typed-but-unfinished text has `commit_edit()` (`widgets.py`:
   `NumberEdit`, `CommitLineEdit`, `EditTable`). `MainWindow.commit_edits()`
   calls it on every widget of the current screen before an F-key, a save,
   F8, or a list change that rebuilds a form. An unusable entry stops the
   action, names the field and stays on screen; it's never rounded or
   dropped.
2. **An edit that can't go into the job is explicit state, and blocks.** A
   screen whose content can't become valid job data (F2 with an invalid
   shape) returns it from `blocking_problem()`. While any screen does,
   `generate()` makes no program and saving asks first. F2 keeps the table
   as typed across screens and offers Undo changes.
3. **Dialogs edit copies.** The tool library dialog works on a deep copy and
   hands back `saved` only after the file is written; `MainWindow` then
   points the stations at the saved tools. Closing without saving changes
   nothing.

At the UI boundary, expected failures (bad file contents: `ValueError`,
`KeyError`; files that can't be read or written: `OSError`) become messages
that say what to do. Anything else is a bug: logged with its traceback to
`~/.config/omniturn-cam/omniturn-cam.log` and reported as an internal error,
never shown as a problem with the operator's file. Message boxes go through
`notify`, `ask` and `save_question` so tests can answer them.

## Where machine facts live

- `docs/omniturn-post-rules.md`: the G4 dialect, with manual page references
  and CONFIRMED / VERIFY marks. Any rule the post follows must be in here.
- `docs/machine-verification.md`: software vs physical verification, what a
  `verified.toml` entry records, and the operator checklist for the VERIFY
  items.
- The travel check (`OmniTurnPost.check_travel`) also compares each tool's
  deepest programmed Z with the collet face worked out from F1's length out
  of the collet (`check_stock_length`). Warnings only; not a collision model.
- `src/omniturn_cam/data/default_machine.toml` and `MachineConfig`: limits of
  the reference machine (Hardinge HLV-H with OmniTurn G4, 4000 rpm VFD).
- The OmniTurn manuals themselves aren't in the repo (copyright). Cite pages.

## Adding an operation

1. Copy the closest module in `src/omniturn_cam/operations/` to
   `<kind>.py`. Give it a new dataclass, `FIELDS` for any field the shared
   table in `fields.py` doesn't label (or labels wrongly for this operation),
   an `emit(p, op)` that writes the code with the `OmniTurnPost` helpers, and
   `SPEC = OpSpec(...)` with a unique `kind` and the next free `order`
   (higher than every existing one, so the new entry lands at the end of the
   menu). Give it `validate` (and `validate_machine` if a rule needs the
   machine profile) for its own rules, and `metric_defaults` for every inch
   length or feed default. Nothing else in `src/` needs editing: the package
   finds the file.
2. Write the rule it relies on, with the manual page, in
   `docs/omniturn-post-rules.md`.
3. Use it in a golden job (`tests/golden/jobs/`), regenerate the expected
   output, and read every line of it. Add focused tests in
   `tests/test_post.py` for its edge cases and errors.
4. Append its `kind` to `MENU_ORDER` in `tests/test_operations.py`. The test
   pins the whole menu, so it fails until you do; that's deliberate, because
   operators find menu entries by position.

`tests/test_operations.py` fails if the new module is incomplete: a field with
no operator label or help, an inch length/feed default with no metric one, no menu text, no tool kinds, no docstring, a menu
position (`order`) shared with another operation, or a menu that doesn't match
`MENU_ORDER`. If another PR added an operation first, your rebase conflicts on
`MENU_ORDER` and your `order` may clash: take the next free number again and
put your entry after theirs.
`tests/test_golden.py` fails until a golden job uses it.

Never rename a `kind` or remove a module: saved job files refer to them by
name (`tests/test_operations.py` checks the list).
