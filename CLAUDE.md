# omniturn-cam: instructions for Claude sessions

Conversational 2D lathe CAM for the OmniTurn G4 control. Its output runs on a
real lathe (Hardinge HLV-H with an OmniTurn G4 retrofit). Wrong code can crash
the machine or hurt someone, so correctness beats speed every time.

@CONTRIBUTING.md
@docs/ARCHITECTURE.md

## How a session works

- **One session = one issue = one area of the code.** Several sessions run in
  parallel on different issues; stay inside yours. If you find something else
  that needs doing, open a new issue for it (or tell the user) instead of
  fixing it here. The one exception is the docs exception in
  CONTRIBUTING.md (step 3): docs-only changes may go into an open, unreviewed
  docs-only PR. Code and tests never do.
- Branch from the latest `origin/main` as `claude/<issue#>-<topic>`. Never
  commit or push to `main`.
- Before opening the PR: `git fetch origin && git rebase origin/main`, then
  `ruff format . && ruff check . && QT_QPA_PLATFORM=offscreen python -m pytest`.
  All green, or the PR isn't ready.
- Open the PR with the template filled in and `Closes #N`. Wait for CI. Don't
  merge your own PR: the user merges after an independent review.
- If asked to **review** a PR, use the review checklist in CONTRIBUTING.md,
  read the full diff (including `tests/golden/expected/`), run the tests
  yourself, and report problems with file and line. A review that only says
  "looks good" is not a review.

## Hard rules

- Don't update `tests/golden/expected/` unless the output change is the point
  of the issue. If you do, list every changed line in the PR and why.
- Don't edit `tests/golden/verified.toml` except to remove an entry whose
  output your PR intentionally changes (and say so). Only the user adds entries.
- Don't weaken, skip or delete tests to get a green run.
- Don't turn a `PostError` into a warning, or a warning into nothing.
- Every dialect rule cites a page of the OmniTurn manual in
  `docs/omniturn-post-rules.md`. If you can't confirm a rule in the manual,
  mark it **VERIFY** and tell the user; don't guess.
- Keep the core free of Qt and CAD-kernel imports (`tests/test_layering.py`).
- Job file changes follow the compatibility rules (`JOB_FORMAT` in `job.py`).

## Teaching comments

Jeff and William maintain this code and are learning programming through it.
Follow "Teaching comments" in CONTRIBUTING.md for every line you write or
change: explain *why* it's written this way, name the best practice it
follows and what goes wrong without it, and explain Python features a
beginner wouldn't know. Don't narrate the obvious. End every PR description
with a **What to learn from this** section. When the user asks why you did
something, answer as a teacher: the reasoning, the alternatives, the trade-off.

## Useful facts

- Install for development: `pip install -e ".[all,dev]"` (the FreeCAD suite
  runs in CI; locally it needs a FreeCAD with Python 3.11+).
- Regenerate golden output (only when intended):
  `UPDATE_GOLDEN=1 python -m pytest tests/test_golden.py`.
- Rebuild STEP fixtures: `python tests/stepparts.py`.
- The OmniTurn manuals are not in the repo. In the claude.ai project they are
  attached as `OmniTurn_Manual_g4.pdf`, `OmniTurn-G4-Diagnostics.pdf` and
  `using_external_subroutines.pdf`.
