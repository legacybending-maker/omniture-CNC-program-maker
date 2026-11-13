# Contributing

omniturn-cam writes programs that move a real lathe, and it's meant to be
used and maintained for years. Changes are welcome; they go through the steps
below so that nothing reaches a machine without being tested and reviewed.

Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) first.

## The workflow

1. **An issue first.** Every piece of work starts as a GitHub issue that says
   what and why. One issue = one change to one area (post, GUI, import,
   FreeCAD workbench...). Big jobs get split into several issues.
2. **A branch per issue**, from the latest `main`:
   `<issue number>-<short-topic>`, e.g. `14-g33-taper`. Claude sessions use
   `claude/<issue number>-<short-topic>`. Never commit to `main`; branch
   protection refuses it anyway.
3. **Small pull requests.** One issue per PR, one area per PR. If you find
   another problem on the way, open an issue for it instead of fixing it in
   the same PR.
4. **Before opening the PR**, bring the branch up to date and run everything:

   ```bash
   git fetch origin && git rebase origin/main
   ruff format . && ruff check .
   QT_QPA_PLATFORM=offscreen python -m pytest
   ```

   Fill in the PR template. Write `Closes #N` in the description.
5. **CI must pass**: lint, the standalone suite on Python 3.11 and 3.12, and
   the FreeCAD suite. `main` requires all of them and requires the branch to be
   up to date with `main`, so two PRs that each pass alone can't combine into a
   broken `main`.
6. **Independent review before merge.** Someone (or a fresh Claude session)
   who did not write the change reviews it against the checklist below. The
   author doesn't review their own work.
7. **Merge with "Create a merge commit".** (Squash and rebase merges are
   turned off so commit history and `.git-blame-ignore-revs` stay valid.)
   The branch is deleted automatically.
8. **Releases** are cut from `main` now and then (see
   [docs/RELEASING.md](docs/RELEASING.md)). The shop installs releases, never
   an arbitrary `main`.

## Changes to G-code output

This is the part that matters most.

- The complete output of every example and golden job is stored in
  `tests/golden/expected/`. If your change alters any program, the golden test
  fails and shows the diff.
- If the change is intended: `UPDATE_GOLDEN=1 python -m pytest tests/test_golden.py`,
  then **read the diff of `tests/golden/expected/` line by line.** Explain in
  the PR which lines changed and why, with the manual page if it's a dialect
  change, and add the `gcode-output` label.
- Never regenerate the golden files just to get a green test.
- Programs that have been run on the machine are listed in
  `tests/golden/verified.toml`. Changing one fails a separate test until you
  remove its entry in the same PR and say so. It has to be proved out again
  before it goes back in. Only the machine owner adds entries.
- New dialect rules go in `docs/omniturn-post-rules.md` with a page
  reference. Anything you couldn't confirm in the manual is marked **VERIFY**.

## Tests

- Every bug fix comes with a test that fails without the fix.
- Every new operation, option or post feature: focused tests in
  `tests/test_post.py` (anchored to the manual's own examples where there is
  one) **and** use in a golden job.
- GUI changes: a test in `tests/test_gui.py` that drives the screen
  (`QT_QPA_PLATFORM=offscreen`).
- STEP import: fixtures live in `tests/data/`; rebuild with
  `python tests/stepparts.py`. Both kernels must give the same profile.
- Don't skip or loosen an existing test to get a change through. If a test is
  wrong, fix it in its own PR with an explanation.

## Compatibility

- **Job files** (`[job] format`): adding an optional key with a safe default
  is fine. Renaming or removing a key, or changing what a value means, needs
  a format bump in `job.py`, code that still reads the old format, and a frozen
  old file in `tests/data/jobs/`.
- **tools.toml and machine.toml** keys are user files on the shop laptop:
  don't rename keys without reading the old name too.
- **Operation defaults**: saved jobs write every value explicitly, so changing
  a default only affects new operations. Still say so in the PR.

## Usability

The operator is a machinist at the machine, not a programmer.

- Fill-in-the-blank, Okuma IGF style: every field has a plain-language label,
  units, and help text (`fields.py`, or the operation's own module). Blank means "automatic" and says what
  automatic will do.
- Error messages say what is wrong, where (operation number, tool), what to do
  about it, and the manual page when it's a control rule.
- Never change a value the operator typed without telling them.
- Keep the F-key screen order and shortcuts stable; people learn them by feel.
- Nothing writes to the USB stick until the rules check passes.

## Code style

- `ruff format` and `ruff check` (version pinned in `pyproject.toml`); CI
  enforces both.
- Python 3.11+. Type hints on public functions. Dataclasses for data.
- Comments explain *why*, especially anything that comes from the manual or
  the machine: cite the page.
- Keep the core free of GUI and CAD-kernel imports (`tests/test_layering.py`).

## Review checklist

The reviewer checks, and notes in the review:

- [ ] The change does what the issue asks, and nothing else.
- [ ] Tests cover the new behaviour and the edge cases; a bug fix has a
      regression test.
- [ ] **G-code:** if `tests/golden/expected/` changed, every changed line is
      explained and correct against `docs/omniturn-post-rules.md` and the
      manual. Modal state (feed mode, spindle, coolant, comp) is right after
      the change, including across tool changes and subroutine calls.
- [ ] Nothing that was a `PostError` became a warning or silent.
- [ ] Units: works in inch and metric; X is diameter everywhere.
- [ ] Job/tool/machine files: old files still load.
- [ ] Layering rules hold; no new dependency without a reason in the PR.
- [ ] Messages and labels are clear to an operator.
- [ ] Docs updated (`ARCHITECTURE.md`, post rules, README) if behaviour or
      structure changed.
