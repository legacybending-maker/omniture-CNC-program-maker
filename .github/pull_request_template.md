## What and why

<!-- One or two sentences. What changes for the operator? -->

Closes #

## Area

<!-- One area per PR. Tick one. -->
- [ ] Post / G-code (`post/`, `operations/`, `lint.py`)
- [ ] GUI (`gui/`, `fields.py`, operation labels in `operations/`)
- [ ] Model import (`stepimport`, `meshimport`, `kernels/`)
- [ ] FreeCAD workbench
- [ ] Job / tool / machine files
- [ ] Tests, CI, docs only

## G-code output

- [ ] **No change** to `tests/golden/expected/`
- [ ] **Changed** (label `gcode-output`). Every changed line and why, with manual page:

<!--
  bushing.cnc: line 42 g95f.003 -> g95f.0025 because ...
-->

- [ ] A program in `tests/golden/verified.toml` is affected; I removed its entry
      and it needs proving out again on the machine.

## Checks

- [ ] Rebased on the latest `main`
- [ ] `ruff format . && ruff check .` clean
- [ ] `QT_QPA_PLATFORM=offscreen python -m pytest` passes locally
- [ ] New behaviour has tests; a bug fix has a test that failed before the fix
- [ ] Inch and metric both handled
- [ ] New and changed code has teaching comments (CONTRIBUTING.md)
- [ ] Old job / tool / machine files still load
- [ ] `CHANGELOG.md` updated under **Unreleased**
- [ ] Docs updated if behaviour or structure changed (`docs/ARCHITECTURE.md`, `docs/omniturn-post-rules.md`, README)

## What to learn from this

<!-- Two or three ideas in this change worth understanding, in plain language. -->

## Review

<!-- Filled in by the reviewer, who did not write this change (CONTRIBUTING.md checklist). -->
- Reviewed by:
- Notes:
