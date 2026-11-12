# OmniTurn G4 — post-processor rules

Summarised from the OmniTurn Operator and Maintenance Manual (G4 control, 2023–2024 edition, available from omniturn.com). Page refs are manual pages. Items marked **VERIFY** couldn't be read cleanly from the PDF text and need a check against the printed pages or on the machine. Items marked **CONFIRMED** were checked by a machinist against the printed manual.

## Target machine
The reference machine is a **Hardinge HLV-H toolroom lathe retrofitted with the OmniTurn G4 control and CNC package**, not a factory OmniTurn GT-75. The control and G-code dialect are the same, but the iron is different. The stock HLV-H spindle runs 125–3000 rpm and takes 5C collets up to 1-1/16" round, and its travels and tool plate layout are its own. Machine-specific limits live in `machine.toml` (see `examples/machine.toml`). Every program header names the machine it was posted for.

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
- Adding P to a move starts it in sync with the spindle (2.11) — used for die heads. VERIFY exact syntax (`...p` vs `...p0`)
- G04Fn dwell in seconds, minimum .1 (2.20). The dwell uses the F word, so the generator restates the feed on the next cut.

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
- G33 threading `G33XnZnInKnAnCnPO` (2.23–2.30). Readable from the examples: X = final thread diameter, Z = end of thread, K = lead (times starts for multi-start), A = taper (X change over the whole Z travel including run-up), C = angle infeed, P = pull-out at the end, O = single pass, tool returns to start. Retracts between passes to start + 2×I. **VERIFY** before generating: exact meaning of I and how passes are calculated, P pull-out distance, C infeed angle, and the conditions for re-picking a thread with O.

## Taps and dies
- G84, G86 and G89 are **not** in the G4 "Codes Honored" list (p2.1–2.2). The manual's tapping method (p2.58) uses ordinary moves and needs a floating or self-releasing holder: `g95f(pitch × .95)z(depth)` → `m04` → `g04f(dwell)` → feed out → `m03`. omniturn-cam uses this for taps, solid die holders and acorn dies.
- Releasing die heads (geometric): feed at the pitch, spindle-synced (P), to the trip point → short dwell → rapid out → `m05` → `m01` so the operator can reset the head. M01 only stops if optional stop is switched ON at the control ("O" in Automatic mode, p2.53); use M00 if the stop must always happen.

## Subroutines (2.55)
- `}n` begins subroutine n, `m99` ends it (nothing else on that line), `m98Pn` calls it. Subroutines go after M30.
- Pattern used by omniturn-cam: finish contour in `}1`, called from G75/G78.

## Program end
- M30 ends and cancels active M functions; M02 ends without cancelling; M00 program stop
- Collet: M12 clamp, M13 unclamp. Coolant M08/M09
