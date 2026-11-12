# 3-phase dependency verification gate (standing rule)

Anything new entering this environment - pip packages, npm tools, vendored
code - passes three phases BEFORE it is used by a scan. The gate exists so
that a supply-chain compromise cannot reach either the operator's laptop or
the subjects of a scan. Phase evidence for each accepted dependency is
recorded below.

## Phase 1 - provenance
- Canonical PyPI project only: exact-name match against `pypi.org/pypi/<pkg>/json`.
  Typosquat check: project_urls must point to the upstream repo; maintainer
  identity cross-checked (real person/org with public history).
- License recorded; all transitive core dependencies enumerated and reviewed.
- No install_requires with remote fetch, no post-install hooks in the wheel
  (`py.typed`-only wheels preferred; `setup.py` scripts inspected via
  metadata).
- Version pin-range added to pyproject with bounded caps (no unbounded `*`).

## Phase 2 - sandboxed install
- Package installs ONLY into the project venv, never globally, never from
  arbitrary URLs (PyPI is the sole source; `--no-index` dirs for vendored).
- Pin range verified against an intentionally old-but-supported floor.

## Phase 3 - smoke + blast-radius
- Import smoke test, small render/exec, then the FULL project test suite
  (fixture transport, no live network).
- Runtime behavior spot-check: no unexpected network hosts, no subprocess
  spawning beyond documented needs.

## Verdicts recorded

### rich (PyPI) - ACCEPTED 2026-11-04
- Provenance: `pypi.org/pypi/rich/json` → version 15.0.0, author Will
  McGugan (willmcgugan@gmail.com), homepage github.com/Textualize/rich (the
  canonical repository), MIT license, core deps only `markdown-it-py` +
  `pygments` (both long-established). venv already had 13.9.4 installed and
  the pin `rich>=13,<16` accepts it.
- Install: venv-scoped only.
- Smoke: live cockpit render used by `thousand_eyes.live.RichSink`; full
  suite 43 passed / 1 skipped (numpy-optional banner). No new network hosts.

### numpy (PyPI, banner-only) - ACCEPTED 2026-11-04 (live)
- Provenance: `pypi.org/pypi/numpy/json` → canonical NumPy project (author
  Travis E. Oliphant et al. / NumFOCUS ecosystem), pypi.org/project/numpy.
- Install: venv-scoped, 2.2.6, pin `numpy>=1.26,<3` as the `banner` extra.
- Smoke: banner renders (truecolor cell art) and scans never import it -
  the module import is try/except and all engine paths are numpy-free.
- Optional extra documented in pyproject (`pipx install 'thousand-eyes[banner]'`).

### httpx / pyyaml / pytest / pytest-asyncio - ALREADY ACCEPTED (v0.3 baseline).

## Rejected classes (never installable through this gate)
- Any package whose metadata cannot prove canonical upstream identity.
- "Tool wrapper" packages that shell out to unknown binaries.
- Packages requesting network at import time or shipping post-install hooks.
