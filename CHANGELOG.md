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
- **Input validation before any code is written** (#32). Every operation's
  values, every shape, the job, the tool library and the machine profile are
  checked, and every problem is listed at once with the operation, the
  setting, the value and what to do, e.g. *operation 2 (drill t2): Peck depth
  (K) [peck] is 0: must be greater than zero. Leave it blank for a straight G81
  drill*. Refused now: NaN/infinity anywhere, zero or negative feeds and
  speeds, a feed per rev over the control's 300 ipm at that rpm (p2.11) or
  under its .00005 minimum (p2.51), a CSS min above the max, drills/taps/threads
  that don't feed toward the chuck, swapped thread diameters, zero-length
  shape segments, chamfers and radii that don't fit their lines, D numbers
  outside 1-32 (p5.16), and misspelt job `units`.
- New processes in a metric job start with millimetre values (feed .08,
  thread M10x1.5, cutoff to -.25...). Changing units still never converts
  numbers already entered.

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
- README "Inch and metric" (#34): what changing F1 units does to a job that
  already has values (nothing is converted, and most unconverted values still
  pass the check), what to do instead, and that tool library values are used
  as written. Docs only; no change to any program.

### Fixed
- **machine.toml is checked strictly.** A misspelt setting (`max_rmp`), a
  section other than `[inch]`/`[metric]`, a quoted number, NaN, or a zero or
  negative limit now stops with the file, section and setting, instead of
  being skipped or reaching the arithmetic. Both unit sections are checked.
- **An explicit `--machine` file that is missing or wrong stops the post**
  (exit code 2) instead of quietly using the built-in Hardinge profile. With
  no `--machine` and no personal profile, the built-in one is used and the
  command says so. The GUI no longer falls back to the built-in profile when
  your machine.toml can't be used: F5/F6 show what's wrong and make no program
  until it's fixed.
- tools.toml: unknown tool kinds, negative nose radius / diameter / width, and
  bad `pitch`/`tpi` are refused with the file and tool named. Extra keys still
  go to the tool's params and 0 still means "not given", so old libraries load.
- Job files: an unknown setting in an `[[op]]` table (with a "did you mean"),
  a missing required one, `t = 1.5` (used to become T1), and quoted numbers
  are clear errors instead of Python tracebacks or silent conversions.
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

- **Machines set to `arc_format = "ik"` can post again** (#36). Every
  program with an arc stopped with `KeyError: 'r'` because the travel check
  could only read R arcs. I/K arcs are now read back around the centre they
  give (I radial, p2.19), in the main program and in contour subroutines.
- **STEP/STL/3MF import no longer refuses valid arcs** (#36). End points and
  radii were rounded separately (6 decimals inch, 4 mm), which could make a
  half-round groove's chord longer than its diameter. Imports now keep
  10 decimals, just enough to clear float noise, and the comment in
  `stepimport.py` shows why that can't break an arc.
- **Subroutines in the include file are found whatever their header looks
  like** (#36). `}1 (LOADER)` was missed, so a contour could be given the same
  number. `}01` and `}1` are now the same subroutine everywhere, a duplicate
  in the include file is refused naming the file, and lint warns about
  leading zeros.
- **The backplot refuses what it can't read instead of crashing** (#36): a
  contour subroutine starting with `m10`, a contour with no X/Z start, an arc
  without R or I/K, a stray character, a number like `x1.2.3`. Each is an
  error with the line number. `omniturn-cam plot` prints it and exits 2; F5
  shows it on the plot. Legal raw code the backplot can't follow (a move in
  G91 or G73) gets a "travel not checked" warning instead of stopping the post.
- Lint now refuses non-ASCII, characters that aren't part of a word,
  unreadable numbers, a word missing its number, negative F or S, and a
  subroutine that never ends or starts inside another (#36). It runs before
  the travel check, so it reports these first.
- The F5 backplot draws arcs with the machine's own G02/G03 mapping.
- A C/R corner between lines that double back is an error with line numbers
  instead of a division by zero when a hand-written program is plotted (#36).

### G-code output
- Reading programs back (#36): no change to any program omniturn-cam writes
  (golden output unchanged). Programs that used to crash the post (any arc
  with `arc_format = "ik"`) now post. Raw code with characters or numbers the
  control can't read, or with an arc that can't exist, is now refused.
- Input validation (#32): no change to any valid program. Jobs that used to
  post with the values listed under Added now refuse instead.
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
