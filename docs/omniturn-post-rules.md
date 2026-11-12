# OmniTurn G4 — post-processor rules

Summarised from the OmniTurn Operator and Maintenance Manual (G4 control, 2023 and Sep 2026 editions, available from omniturn.com). Page refs are manual pages. Items marked **VERIFY** couldn't be read cleanly from the PDF text and need a check against the printed pages or on the machine. Items marked **CONFIRMED** were checked by a machinist against the printed manual.

## Target machine
The reference machine is a **Hardinge HLV-H toolroom lathe retrofitted with the OmniTurn G4 control and CNC package**, not a factory OmniTurn GT-75. The control and G-code dialect are the same, but the iron is different:
- Spindle on the OmniTurn VFD: 4000 rpm max. 5C collets, 1-1/16" round capacity.
- Travel: about 10" X and 9" Z (to be confirmed on the machine). The post warns when a program's moves span more than that. It can't do a true limit check, because where the travel sits relative to the part depends on the tool offsets.
- Control firmware is as shipped, about 5 years old and never updated. The June 2023 manual that came with the control is the reference. The Sep 2026 manual has the same "Codes Honored" list apart from M86 (vision system). Its G33 pages read cleanly and were used to fill in text that is garbled in the 2023 PDF. Where only the 2026 text documents a detail (e.g. `C` alone = 29°), the post avoids relying on it. Machine-specific limits live in `machine.toml` (see `examples/machine.toml`). Every program header names the machine it was posted for.

## File format
- Plain text, filename max 8 characters, extension .txt, .cnc, or none (2.8)
- Header block: `g90g72g94f300` (absolute, diameter mode, IPM, rapid feed) (2.6). X defaults to RADIUS mode (G73) unless G72 is given (2.10, 2.37)
- G70 inch is the default; G71 metric is modal and survives until G70 or power-off, so put G70/G71 at the start of every program (2.37). In G71 the F words are mm/min or mm/rev, so the "rapid" feed is F7620, not F300.
- Lowercase letters recommended to avoid O-vs-0 mistakes (2.6)
- NO N sequence numbers (2.6)
- NO commas anywhere in the program (2.6)
- Comments in parentheses, text only — no periods, commas or symbols; keep short; never inside a loop; `()` clears the displayed comment. Good place: on the tool call line, e.g. `t1(LH turn tool)` (2.6)
- G and M codes must be two digits: `g02` not `g2` (2.6)
- One M code per line; one one-shot G code per line; S and F can go on any line (2.6)
- Decimal-point programming; leading/trailing zeros optional. Diameter mode X resolution .0001 (2.6). Metric display is four-place (2.37)
- Block skip: `/` at the start of a line (2.6)

## Modal vs one-shot (2.6)
- Modal: all M codes, G10 G70 G71 G72 G73 G76 G77 G90 G91 G94 G95 G96 G97
- One-shot: G02 G03 G04 G33 G34 G81 G83 G92

## Axes and arc direction (2.5, 2.16–2.19)
- X: up (or away on attachments) is minus, down/toward operator is plus
- Z: toward spindle is minus
- **Arc direction (CONFIRMED):** the manual's I/K examples fix it, and the p2.43 drawing shows a raised bead that matches. A convex face-to-OD corner from X.2 Z0 to X.5 Z-.15 with centre at X.2 Z-.15 is `G02X.5Z-.15I0K-.15`; on a print (face right, X+ up) that arc runs counter-clockwise. So **print-CCW = G02, print-CW = G03**, consistent with the OmniTurn's X+ pointing down. omniturn-cam stores arcs in print terms and maps them in `MachineConfig.arc_codes`. Still worth one look on the F4 Verify screen.
- I and K are incremental from the arc START to the centre; I is a radial distance even in G72 diameter mode (2.19 G03 example: X.2→X.4 with I.1).

## Tool calls (2.9)
- Two blocks: `tN` (1–32), then a line with BOTH X and Z. Call to a safe Z first, then approach.

## Feeds and speeds (2.11–2.12, 2.51–2.52)
- G94 = IPM (F300 = rapid, also max feed). G95 = IPR (max feed limited to 300 IPM equivalent)
- G00 switches to rapid; G01 returns to the last-programmed feed; `G95Fn` on a move also switches from rapid to feed
- M03Sn / M04Sn spindle, M05 stop. G97 RPM (default), G96Sn CSS with G76Sn min / G77Sn max. Turn CSS on after positioning for the cut and back to G97 after (2.52)
- Adding P to the end of a move starts it in sync with the spindle (2.11, 2.30), e.g. `g95f.05z-.5p`. Used for die heads
- G04Fn dwell in seconds, minimum .1 (2.20). The manual's tapping example feeds out after a dwell without restating F; omniturn-cam restates it anyway. Never use a dwell for loading or unloading (manual: unsafe).

## Geometry helpers
- Auto chamfer/radius on line-to-line corners: `C` or `R` with the absolute size on the move to the theoretical sharp corner. Lines only, not arcs (2.14)
- Tool nose radius comp: G41/G42/G40, radius value stored in secondary offset `Dn`; turn on with a move carrying Dn; lead off the part by more than 2× TNR before G40 (2.31–2.36). Manual's OD example cutting toward the chuck uses G41.
- Secondary offsets `Dn` = operator-adjustable corrections added to moves (2.59)

## Canned cycles (the controller does the roughing)
- G75 `G75InUnFnPn` box contour roughing (2.41–2.45): straight passes parallel to Z, depth ≤ I per side. **U = material left per side** after roughing (CONFIRMED). Area bounded by lines through the start point and the contour A→B. Tool returns to start.
- G78 `G78UnFnPn` rough contour (2.46–2.47): passes follow the contour. **U = stock left over the entire contour** for finishing (CONFIRMED).
- Contour follows the cycle line ending in `RF` on its own line, or lives in a subroutine called with Pn
- TNR comp cannot be active during roughing cycles; the comp'd finish pass must be written inline, not in the subroutine
- Contour start A and end B must be in the same quadrant relative to the cycle start S, or the control errors (2.42)
- G74 `G74XnZnInUnFn` box roughing, rectangular (2.38–2.40). X/Z = corner of the box, I = depth per side. **U is ignored but must be present** (CONFIRMED). Leave finish stock by adjusting X and Z themselves.
- G81 `G81ZnFn` drill: feed to Z, rapid back to start (2.48)
- G83 `G83ZnKnFnRnCnLn` peck: K peck depth, R rapid-out plane, C clearance on return, L retract feed (2.49–2.50). Example `G83Z-.5K.1F.003R.5C.05L300`
- G33 threading `G33XnZnInKnAnCnPO` (2.23–2.29, CONFIRMED from the printed pages):
  - X = finished diameter (OD: minor; ID: major), Z = end of thread
  - I = depth of the FIRST pass, 2× depth of cut in diameter mode. The control keeps the material removed per pass constant, so passes ≈ (total depth / I)². Start X = major − I (OD), so the first pass is a full I
  - K = lead. Multi-start: K = lead × starts, one G33 per start, each start's Z offset by the pitch (2.28)
  - A = taper, X travel over the whole Z travel including the run-up; the start X must be corrected for the taper (2.29)
  - C = angle infeed; `c` alone = 29° (2026 manual), `c27` = 27°, range 0–30°. The post always writes the angle explicitly
  - P = keep moving forward in Z while pulling out (no undercut); overshoots Z by as much as the X retract
  - O = single pass at finished depth; pulls out to 3×I. To re-pick a thread: part not moved in the collet, same start X and Z, same spindle speed, same P/C
  - Retracts between passes to start + 2×I; returns to the start point at the end
  - Start at least .1" in front of the thread so the slide is up to speed (the ball screw needs about ½ rev); rpm × K must stay under 300 ipm
- "Do-it-yourself" threading / thread milling: `p` alone at the end of any move starts it in sync with the spindle, e.g. `z-.25p` (2.30, CONFIRMED)

## Taps and dies
- G84, G86 and G89 are **not** in the G4 "Codes Honored" list (p2.1–2.2), CONFIRMED on the machine: tapping and die cycles are written out long-hand. The manual's tapping method (p2.58) uses ordinary moves and needs a floating or self-releasing holder: `g95f(pitch × .95)z(depth)` → `m04` → `g04f(dwell)` → feed out → `m03`. omniturn-cam uses this for taps, solid die holders and acorn dies.
- Releasing die heads (geometric): feed at the pitch, spindle-synced (P), to the trip point → short dwell → rapid out → `m05` → `m01` so the operator can reset the head. M01 only stops if optional stop is switched ON at the control ("O" in Automatic mode, p2.53); use M00 if the stop must always happen.

## Subroutines (2.55; "Using External Sub-routines", Dec 2024)
- `}n` begins subroutine n and `m99` ends it, each alone on its line. `m98Pn` calls it. Subroutines go after M30, and each number may be defined only once.
- **Subroutine 1 is special:** `M95` jumps to subroutine 1 when loader input HDR112 is shorted. M97InCnPn (PLC option) jumps conditionally to any subroutine.
- Shops keep reusable subroutine files (ZipLoader / bar-load routines), usually numbered from 1, and paste them below M30 with the control's editor (Alt-E, "Paste From"). omniturn-cam can copy such a file in itself (job setting `include_subs`). Its own contour subroutines are numbered past any number the file uses, starting at `first_sub` in machine.toml.
- If a subroutine file will be pasted on the control instead, set `first_sub` above its numbers so they don't clash. The linter flags duplicate subroutine numbers, and M95 with no subroutine 1.
- Pattern used by omniturn-cam: each finish contour in its own subroutine, called from G75/G78.

## Loader, input and override codes (2.54–2.56)
- M91/M92 wait for HDR111 open/shorted, M93/M94 wait for HDR110 open/shorted. M10/M11 and M40–M59 are loader functions. M32/M33/M38, M60–M65 are PLC outputs.
- M84 locks the feed and spindle overrides at 100%; M85 unlocks them, and so do M30, M02 and any tool call. omniturn-cam brackets G33 with M84/M85 by default, so the spindle override can't change the rpm between passes.
- LSn / LF loop start/finish (2.20–2.21). No comments inside loops.

## Files on the control (External Sub-routines sheet)
- The control runs DOS. The USB stick is `D:`, programs live in `C:\PROGRAMS`, and saved subroutine files can go in `C:\RUNFILES`. File names are 8 characters. omniturn-cam writes CRLF line endings.

## Resolution (G4 Diagnostics sheet)
- Axis encoders: 4000 counts per .2" of travel = .00005" per count, so .0001" on diameter. That matches the 4-place diameter output.
- Spindle encoder: 4000 counts per rev plus a marker. Everything per-rev (G95, G33, G83) depends on it. Ctrl-S in Jog mode checks it.

## Program end
- M30 ends and cancels active M functions; M02 ends without cancelling; M00 program stop
- Collet: M12 clamp, M13 unclamp. Coolant M08/M09
