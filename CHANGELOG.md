# Changelog

What changed in each release, for the people running it at the machine.
Every pull request that changes behaviour adds a line under **Unreleased**.
Changes to G-code output always get their own line under **G-code output**,
saying which programs are affected.

Format: [Keep a Changelog](https://keepachangelog.com/). Versions:
`MAJOR.MINOR.PATCH`; see [docs/RELEASING.md](docs/RELEASING.md).

## [Unreleased]

### Added
- Golden-output tests: the full program for every example and golden job is
  stored and compared line by line, so any change to G-code shows in review.
- `tests/golden/verified.toml`: record of programs proved out on the machine;
  their output can't change without the entry being removed on purpose.
- Job files carry `[job] format = 1`. Files from a newer version are refused
  with a message instead of being misread.
- Layering tests, ruff lint/format in CI, contribution and release docs,
  PR and issue templates, release workflow.
- Example tool library: a 1/4 min-bore boring bar.

### Changed
- Each operation type is one file in `operations/` (dataclass, labels, menu
  entry, tool defaults and OmniTurn code together), and each programming
  screen is one file in `gui/screens/`. Adding an operation is adding a file.
  No change to what the operator sees or to any program.
- New tests keep it that way: every field must have an operator label and
  help text, the add-process menu order and the F-key order are pinned, and
  every relative import (including ones inside functions) must resolve.

### Fixed
- numpy (needed for STL/3MF import) is now a declared dependency.
- Version numbers agree everywhere (0.2.0); `package.xml` already said 0.2.0.

### G-code output
- No change.

## Before the changelog (2026-11-11 to 2026-11-13)

Nothing was released. Work so far, in order:
- Profile model, OmniTurn G4 post, rules linter, backplot, tool library, TOML jobs.
- IGF-style fill-in-the-blank GUI and tool library editor.
- Confirmed cycle details (G75/G78 U, G74), taps and dies written long-hand,
  HLV-H machine profile (4000 rpm VFD, travel span check).
- G33 threading (multi-start, angle infeed, pull-out, spring passes),
  external subroutine files, M98 calls, raw code, M84/M85 around threads.
- STEP import (OCP), then kernel-neutral with FreeCAD; STL/3MF mesh import.
- FreeCAD workbench (turned-profile object, task panel, save to job).
- CI on pull requests (standalone + FreeCAD).
