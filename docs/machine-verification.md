# Machine verification

omniturn-cam can prove that it writes the program it meant to write. Only the
machine can prove that the program does what we think. This file covers the
second kind: how a program gets proved out on the lathe, what gets recorded,
and the checks still open from Stage 5 (issue #40).

## Two kinds of verification

| | Software verification | Physical verification |
|---|---|---|
| Question | Does the post write the program we intended? | Does the HLV-H with the G4 control do what we think that program does? |
| Evidence | Tests, golden files (`tests/golden/expected/`), lint, the backplot | The program running on the machine: F4 Verify, single block, a real part |
| Who | Anyone, automatically on every pull request | Jeff, at the machine |
| Recorded in | Git history and CI | `tests/golden/verified.toml` |

A green test run never means a program is proved out. The golden files are a
record of what the software does, not proof that it's right (golden README).
A rule marked **VERIFY** in `docs/omniturn-post-rules.md` stays VERIFY until
someone has watched the control do it, however many tests cover the code that
writes it.

## The ledger: `tests/golden/verified.toml`

Only Jeff adds entries, after running the program on the machine. The test
`test_machine_verified_programs_unchanged` then fails if that program's output
ever changes, so proven G-code can't change silently. The test only needs
`sha256`; everything else is the record a person needs later to know what was
actually proved. Recommended entry:

```toml
[bushing]                         # golden job name (file name without .toml)
sha256 = "..."                    # SHA-256 of tests/golden/expected/bushing.cnc as it ran
program = "BUSH01"                # program name on the control
machine = "Hardinge HLV-H OmniTurn G4"
control = "OmniTurn G4"
firmware = "as shipped, about 2021, never updated"   # or what the control shows
tools = "t1 lh-turn-35 D1, t2 drill-250, t3 bore-bar-250 D3, ..."  # station, tool, D
material = "660 bronze"
stock = "1.0 dia, 1.0 out of a 5C collet, .25 bore"
date = 2026-11-20
operator = "Jeff"
status = "proved"                 # proved | proved-with-limits | failed
checks = ["F4 Verify", "single block", "full cycle"]
notes = "first part in tolerance; checked the die head reset stop"
limitations = "loader routine not run: bar fed by hand"
```

- **sha256**: `sha256sum tests/golden/expected/bushing.cnc` (Linux/macOS) or
  `python -c "import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" tests/golden/expected/bushing.cnc`.
  Hash the file in the repo, which is the one the tests compare.
- **status**: `proved` = ran as intended and made a good part.
  `proved-with-limits` = ran, with something not exercised (say what in
  `limitations`). `failed` = keep the entry as a record if you want, but say
  what went wrong; open an issue for it.
- **firmware**: the post rules record the control as shipped, never updated.
  If the control shows a version anywhere, write it here.

The comment block at the top of `verified.toml` shows a shorter example; this
fuller one is a recommendation, and extra keys don't affect the test.

## Before any test on the machine

These are the manual's own ways to prove out a program (p4.15-4.16, p5.4):

- **F4 Verify** first: the control draws the tool paths of the program in
  memory. Compare it with omniturn-cam's F5 backplot.
- **Feed rate override low.** The override works on rapids too (p2.11), so a
  wrong move is slow enough to stop.
- **Single block** (`S` in Automatic mode): one line per Cycle Start. The
  control shows the line it's on and the next one.
- **Optional stop** (`O`) with M01 after each tool change (the manual's own
  suggestion, p2.53) to check each tool arrives where expected.
- **Feed Hold** stops the slide; **Esc** ends the program and turns off M
  functions (p4.15).

Setup types used below:

- **Empty machine**: no bar in the collet, every tool clear. Programs run "in
  air"; watch positions on the display.
- **Setup stock**: a scrap bar of soft material, enough length out of the
  collet for the program, tools touched off as for a real job.
- **Actual material**: the job's own material, after the program has passed
  on setup stock.

Never set up a condition meant to make the machine misbehave. Each test below
watches what the control does with a program the post wrote, at low override,
ready to Feed Hold.

## Checklist

### 1. Safe start when the control is in an unknown state

- **What:** the header puts the control in known modes whatever the last
  program left.
- **Why:** G70/G71, G72/G73, G90/G91 decide what every number means. G71 and
  G73 survive until changed or power-off (p2.37).
- **G-code:** `g40`, `g70`, `g90g72g94g97f300` (header).
- **Setup:** empty machine. First run a short program with no moves and no
  spindle that changes modes: `g71`, `g73`, `g91`, `g95`, `m30`. Then run the
  job in single block, override low.
- **Observe:** after the header lines, the display is in inch, and the first
  approach moves to the programmed diameter and Z (X1 is .5 from centre).
- **Failure:** any move whose size or direction doesn't match the program read
  in G70, G90 and G72.

### 2. Spindle and coolant at program start

- **What:** what the spindle and coolant do between cycle start and the first
  operation's `m03`/`m08`/`m09`.
- **Why:** M02 ends a program and leaves M functions on (p2.58), which bar work
  uses on purpose. The header deliberately doesn't write M05/M09 (post rules,
  "Program start"); the first operation states both before it cuts.
- **G-code:** first tool call, then `m03s...` and `m08` or `m09`.
- **Setup:** setup stock. Run a short program that ends with `m03s500`, `m08`,
  `m02`; then the job, single block.
- **Observe:** the spindle and coolant stay as left until the first
  operation's lines change them, and those lines come before any feed move.
- **Failure:** a feed move before the spindle and coolant are what the
  operation asked for. Also record whether you'd *want* M05/M09 at program
  start for your workflow: that's a decision for you, not the post.

### 3. CSS activation and RPM limits

- **What:** G96 surface speed with its G76 minimum and G77 maximum.
- **Why:** the post writes `g96s..`, then `g76s..`, then `g77s..`, the order of
  the manual's example (p2.52). Whether the control briefly runs unclamped
  between the G96 line and the G77 line isn't stated (**VERIFY**).
- **G-code:** e.g. `g96s300` / `g76s500` / `g77s2500`, after the tool is at its
  start X.
- **Setup:** setup stock, a modest surface speed, a CSS face or finish
  operation. Single block: the control waits after each line, which is the
  longest any gap could last.
- **Observe:** the Speed box after each of the three lines.
- **Failure:** the rpm goes above the G77 value, or above the machine's 4000,
  at any point. If it does, report it: the post would then write G76/G77
  first, as a change of its own with a manual reference.

### 4. G41/G42 lead-in and lead-off

- **What:** nose radius comp turning on and off around a finish pass.
- **Why:** p2.31-2.32: G41/G42 alone on a line, the D word on the next linear
  move, the tool off the part by at least the nose radius when comp turns on
  and by at least twice it before G40. The post now refuses a shorter lead-in.
  These rules were read from the manual's text; check them against the
  printed p2.31-2.32.
- **G-code:** `g41` / `g95f..z0d1` ... `x(lead-off)` / `z(start)` / `g40`.
- **Setup:** empty machine first (F4 Verify, then single block), then setup
  stock. Secondary offset D set as p2.36 shows (X = -2R, Z = -R, R = nose
  radius).
- **Observe:** in single block the control shows the extra moves it creates
  for comp (p2.36). The first compensated move starts clear of the part.
- **Failure:** a control error, a path in F4 that cuts into the shape, or a
  generated move into the part.

### 5. Internal finishing clearance

- **What:** an ID finish leads off toward the centre and then retracts in Z.
  In a small bore it now stops at the centre instead of crossing it.
- **Why:** a boring bar's clearance from the wall is greatest at the centre.
- **G-code:** the lead-off `x..` after the last contour line, then `z(start)`.
- **Setup:** setup stock with a pre-drilled hole; one normal bore and the
  smallest bore you'd finish.
- **Observe:** the bar's back clears the far wall during the lead-off and the
  Z retract.
- **Failure:** contact with either wall, or a lead-off past the centre.

### 6. Subroutine exit positions

- **What:** where M98 subroutines leave the slide, and the post's handoff
  (park at the tool-change Z before, re-establish after).
- **Why:** the post never moves from a position it doesn't know; a *Slide
  position afterwards: tool_change_z* declaration is a promise only the
  machine can confirm (post rules, "Handing control to code the post didn't
  write").
- **G-code:** `z(tool-change)` / `m98pN` / ... / next tool call.
- **Setup:** empty machine, single block.
- **Observe:** where the slide is when the subroutine's `m99` returns.
- **Failure:** it isn't at the tool-change Z when the operation declares it.

### 7. Loader routines

- **What:** loader subroutines (M95/subroutine 1, M91-M94 waits, M10/M11,
  M12/M13) inside a posted program.
- **Why:** they're shop code the post reads but didn't write; the post forgets
  the spindle, coolant and tool after them and calls the tool again.
- **G-code:** the include file's subroutines after M30; the post's park
  before and tool call after.
- **Setup:** as the loader's own instructions say, empty machine first.
- **Observe:** the post's next move after the loader code is a tool call from
  the tool-change Z.
- **Failure:** any slide move between the loader code and the next tool call
  line.

### 8. Rapid movements after tool changes

- **What:** every tool change: Z alone back to the tool-change Z, `tN`, then
  the X/Z approach.
- **Why:** this is the operation exit contract (ARCHITECTURE.md, invariant 8):
  each operation ends where a Z-only retract is in free air.
- **G-code:** `z1` (or `g00z1`), `tN(...)`, `x..z1`.
- **Setup:** empty machine, then setup stock; optional stop on with M01s.
- **Observe:** at each tool change, the Z retract finishes before any X move,
  and the approach to the first cutting position passes over air.
- **Failure:** any X motion before the slide reaches the tool-change Z, or an
  approach line that crosses the bar.

### 9. Axial operator stops

- **What:** the die head reset stop: `z(tool-change)`, `m05`, `m09`, `m01`
  (or `m00`).
- **Why:** M00 doesn't cancel M functions (p2.53), so the post now turns
  coolant off as well as the spindle before the operator reaches in.
- **Setup:** setup stock, die head job, optional stop ON (`O`) for M01.
- **Observe:** the slide at the tool-change Z, spindle stopped, coolant off,
  program waiting.
- **Failure:** spindle or coolant still running at the stop; with `O` on, the
  M01 doesn't stop.

### 10. Coolant restoration after operator stops

- **What:** after the stop, the next operation turns coolant back on.
- **Why:** the post recorded the M09, so it writes M08 for the next operation
  that uses coolant.
- **G-code:** `t..`, `x..z..`, `m03s..`, `m08`.
- **Setup:** continue test 9. Repeat once with optional stop OFF: the M01 is
  skipped but the M05/M09 still run, so the spindle and coolant stop briefly
  and come back for the next tool.
- **Observe:** coolant on before the next tool's first feed move.
- **Failure:** the next operation cuts dry.

### 11. Minimum and maximum supported geometry

- **What:** the extremes: largest bar (1-1/16 round, 5C), smallest bore
  finished or threaded, longest Z and X spans, the deepest Z near the collet.
- **Why:** the travel limits (10 in X, 9 in Z) are marked VERIFY in
  `default_machine.toml`, and the stock-length warning assumes where the bar
  end and collet face are.
- **Setup:** F4 Verify for all; empty machine for travel; setup stock for the
  collet check, with the length out of the collet measured with a scale.
- **Observe:** whether the post's travel and collet warnings match what the
  machine shows. Never jog a tool to the collet to find it; measure the bar.
- **Failure:** a move the post didn't warn about reaches a travel limit or the
  collet, or a warning that is wrong for this machine.

### 12. Single block and F4 Verify on every new program

- **What:** the manual's prove-out for any new program, before it goes in the
  ledger.
- **Why:** it's the only check of the whole program on the real control.
- **Setup:** F4 Verify; empty machine single block at low override with
  optional stop; setup stock single block; then a full cycle on actual material.
- **Observe:** F4 matches omniturn-cam's F5 backplot; each line does what it
  says.
- **Failure:** any difference. Don't add the program to the ledger; open an
  issue with the program, the line and what happened.

## Open VERIFY items from Stage 5

| Item | Post behaviour now | Checklist test |
|---|---|---|
| Comp lead-in ≥ nose radius, lead-off ≥ 2× (p2.31-2.32, read from the manual text) | Enforced | 4 |
| G96 before G76/G77 (p2.52 example order); unclamped rpm in between? | Unchanged | 3 |
| Safe start: no M05/M09 in the header | Unchanged | 1, 2 |
| G33 ID retract direction between passes (p2.24) | Warning when it would pass the centre | 5, 11 |
| Coolant still on at M00/M01 (p2.53 says M functions stay on) | M09 before the stop | 9, 10 |
| Collet face position for the stock-length warning | Warning only | 11 |
| Slide travel 10 in X, 9 in Z | Warning only | 11 |
