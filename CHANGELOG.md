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
- Raw program lines and Call subroutine have **Slide position afterwards**:
  `check` (default) or `tool_change_z`, the operator's declaration that the
  code always finishes with the slide back at the tool-change Z. The post
  warns every time it relies on the declaration (#27).

### Changed
- Each operation type is one file in `operations/` (dataclass, labels, menu
  entry, tool defaults and OmniTurn code together), and each programming
  screen is one file in `gui/screens/`. Adding an operation is adding a file.
  No change to what the operator sees or to any program.
- New tests keep it that way: every field must have an operator label and
  help text, the add-process menu order and the F-key order are pinned, and
  every relative import (including ones inside functions) must resolve.
- Help text for fields you can leave blank now says what blank does: CSS min
  RPM (no G76 line), CSS max RPM (the machine's max), peck retract Z (the start
  Z), thread taper (straight thread). Placeholders match. No change to any
  program.

### Fixed
- After raw lines or a subroutine call the post no longer trusts what it knew
  before (#27). It reads the code (and the subroutine, if it's in the include
  file), forgets every mode and the position it could have changed, and the
  next operation writes them again: G95 before a drill cycle (a raw G94 used
  to leave G81 F.003 running as .003 inches per minute), M08 *or* M09 (a raw
  M09 used to leave the next cut dry), G97 before an rpm, G40/G70/G71/G90/G72
  before the tool call.
- **The post now refuses** to move the slide after raw lines or a subroutine
  that can move it, instead of making a simultaneous X/Z rapid from wherever
  that code left the slide. Jobs whose raw code or subroutine moves the slide
  (or calls a subroutine that isn't in the include file) need *Slide position
  afterwards* set to `tool_change_z` before they post. Raw lines and
  subroutines that only use loader, wait and PLC codes post as before.
- numpy (needed for STL/3MF import) is now a declared dependency.
- Version numbers agree everywhere (0.2.0); `package.xml` already said 0.2.0.

### G-code output
- Programs with raw lines or a subroutine call: modes the code could have
  changed are written again after it (see Fixed). In the golden jobs only
  `bushing.cnc` changes: `m08` after the cutoff's spindle start, because the
  raw lines before it hand control away and coolant is no longer assumed on.
- A program whose first operation has coolant off now writes `m09` (coolant
  isn't assumed off at program start). No golden job has one.
- New golden jobs `handoff` (inch) and `handoff_mm` (metric).

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
