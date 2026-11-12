# Contributing to thousand-eyes

Thanks for looking under the hood. The rules here are short on purpose.

## The fence is not negotiable

Anything you add must respect the hard rules enforced in code:

1. Pivots must be confirmed by the response body itself.
2. No password-reset flows, no credential probing, no login-wall brute.
3. No broker APIs, no breach-dump pulls: existence checks only.
4. No spam engines, no fake-account generation.
5. Politeness caps stay capped.

## Workflow

1. Fork, then create a branch from `main`.
2. Keep modules small and typed; every finding is an evidence row, never a
   bare string.
3. Run the tests: `py -3 -m pytest -q`. New modules need at least one test
   that proves they refuse to fire without confirmation.
4. New dependencies pass the 3-phase gate first (see
   `research/dependency-gate-3phase.md`). Default answer is "no new deps".
5. Pull requests: one feature per PR, describe the evidence model of any
   new module (what it fetches, what counts as confirmed).

## Style

- No em dashes or en dashes in source, docs or strings: plain hyphens only
  (`python scripts/strip_dashes.py` sweeps and enforces it).
- Windows-safe output: assume a console that may not be UTF-8; never print
  raw bytes that could crash a run.
- Copy tone is lowercase, operator-to-operator, no marketing.
