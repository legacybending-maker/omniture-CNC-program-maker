# Golden programs

`expected/` holds the complete program the post writes for every job in
`examples/` and `jobs/`, with the default machine profile and
`examples/tools.toml`. `tests/test_golden.py` compares them line by line.
`<name>.warn` holds the post's warnings for that job, if it has any.

**These are a record of what the software does, not proof that it's right.**
A program is only known to be right once it has run on the machine; those are
listed in `verified.toml`. What a ledger entry should record, and the
operator checklist, are in `docs/machine-verification.md`.

- `jobs/bushing.toml`: inch; every operation the manual examples don't cover
  (ID rough/finish, G74, G83, tap, die head, knurl, CSS, M98, raw lines,
  include file), the die head stop with coolant off and back on, and a stock
  length every tool clears (the collet check runs and stays quiet).
- `jobs/metric.toml`: metric; G71 output, two-start angle-infeed thread with
  a spring pass, R corner, G03 arc.
- `jobs/handoff.toml`, `jobs/handoff_mm.toml`: inch and metric; raw lines
  and M98 calls between automatic operations, and what the post writes to
  recover after each (readable and declared subroutines, raw M09, G94, G70,
  G96, a stop).
- `examples/manual_g75.toml`, `examples/thread_half13.toml`: the manual's own
  worked examples.

New operation types must be used in at least one job here (a test checks).
To update after an intended change, see the docstring in `tests/test_golden.py`
and CONTRIBUTING.md.
